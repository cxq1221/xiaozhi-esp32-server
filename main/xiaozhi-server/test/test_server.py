"""
基于 aiohttp 的轻量级测试服务器：

- 托管本目录下的测试页面（index.html、test_page.html 等）
- 提供配置接口：/xiaozhi/tester/config
  从 ~/.openclaw/openclaw.json 读取 xiaozhi.deviceId，返回给前端用作默认设备 MAC。
- 反向代理 OTA、视觉分析和 WebSocket：
  - /xiaozhi/ota/...           -> http://127.0.0.1:8003/xiaozhi/ota/...
  - /mcp/vision/explain        -> http://127.0.0.1:8003/mcp/vision/explain
  - /xiaozhi/v1/ (WebSocket)   -> ws://127.0.0.1:8000/xiaozhi/v1/

这样对外只需要暴露一个端口（例如 30055），浏览器无需关心内部端口映射。

使用方式：

    cd main/xiaozhi-server/test
    python test_server.py  # 默认端口 8006

    # 或指定对外映射端口
    python test_server.py --port 30055

启动后：
- 测试页访问：  http://<公网IP>:<port>/
- 配置接口：    http://<公网IP>:<port>/xiaozhi/tester/config
- OTA 接口：    http://<公网IP>:<port>/xiaozhi/ota/
- WebSocket：   ws://<公网IP>:<port>/xiaozhi/v1/
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Tuple

import aiohttp
from aiohttp import web
import asyncio
from asyncio.subprocess import PIPE


BASE_DIR = Path(__file__).parent.resolve()

# 内部 xiaozhi-server 的 HTTP/WS 端点（容器内或本机回环）
UPSTREAM_HTTP_BASE = os.getenv("XIAOZHI_HTTP_BASE", "http://127.0.0.1:8003")
UPSTREAM_WS_BASE = os.getenv("XIAOZHI_WS_BASE", "ws://127.0.0.1:8000")


async def handle_tester_config(request: web.Request) -> web.Response:
    """
    返回 ~/.openclaw/openclaw.json 中的 channels.xiaozhi.deviceId。
    """
    status, payload = _load_openclaw_device_id()
    return web.json_response(payload, status=status)


def _load_openclaw_device_id() -> Tuple[int, dict]:
    try:
        home = Path(os.path.expanduser("~"))
        cfg_path = home / ".openclaw" / "openclaw.json"
        if not cfg_path.exists():
            return 404, {
                "ok": False,
                "error": f"config file not found: {cfg_path}",
            }

        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        device_id = (
            data.get("channels", {})
            .get("xiaozhi", {})
            .get("deviceId")
        )
        if not device_id:
            return 500, {
                "ok": False,
                "error": "channels.xiaozhi.deviceId not found in openclaw.json",
            }

        return 200, {"ok": True, "deviceId": device_id}
    except Exception as e:  # noqa: BLE001
        return 500, {"ok": False, "error": str(e)}


async def handle_llm_config(request: web.Request) -> web.Response:
    """
    接收前端提交的 LLM API Key，并写入 data/.config.yaml 中的 LLM.DeepSeekLLM.api_key。
    """
    try:
        data = await request.json()
        api_key = str(data.get("api_key", "")).strip()
        if not api_key:
            return web.json_response({"ok": False, "error": "api_key is required"}, status=400)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "error": f"invalid json: {e}"}, status=400)

    status, payload = _update_deepseek_api_key(api_key)

    # 写入成功后尝试重启 xiaozhi 服务，使新配置生效
    if status == 200:
        ok, msg = await _restart_xiaozhi_service()
        payload["restart"] = "ok" if ok else "failed"
        if not ok:
            payload["restart_error"] = msg

    return web.json_response(payload, status=status)


async def handle_llm_config_get(request: web.Request) -> web.Response:
    """
    返回当前 data/.config.yaml 中的 LLM.DeepSeekLLM.api_key（若存在）。
    """
    status, payload = _load_deepseek_api_key()
    return web.json_response(payload, status=status)


def _load_deepseek_api_key() -> Tuple[int, dict]:
    cfg_path = BASE_DIR.parent / "data" / ".config.yaml"
    if not cfg_path.exists():
        return 404, {"ok": False, "error": f"config file not found: {cfg_path}"}

    try:
        lines = cfg_path.read_text(encoding="utf-8").splitlines()
    except Exception as e:  # noqa: BLE001
        return 500, {"ok": False, "error": f"read config failed: {e}"}

    inside_llm = False
    inside_deepseek = False
    current_key = ""

    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        elif not line.startswith(" "):  # 顶层键
            inside_llm = stripped.startswith("LLM:")
            inside_deepseek = False
            continue

        if inside_llm and stripped.startswith("DeepSeekLLM:"):
            inside_deepseek = True
            continue

        if inside_llm and inside_deepseek and stripped.startswith("api_key:"):
            # 提取冒号后的内容
            parts = stripped.split(":", 1)
            if len(parts) == 2:
                current_key = parts[1].strip()
            break

    return 200, {"ok": True, "api_key": current_key}


def _update_deepseek_api_key(api_key: str) -> Tuple[int, dict]:
    """
    在 data/.config.yaml 中找到 LLM.DeepSeekLLM.api_key 并更新为给定值。
    这是一个尽量保守的行级编辑，不依赖额外 YAML 库。
    """
    cfg_path = BASE_DIR.parent / "data" / ".config.yaml"
    if not cfg_path.exists():
        return 404, {"ok": False, "error": f"config file not found: {cfg_path}"}

    try:
        lines = cfg_path.read_text(encoding="utf-8").splitlines()
    except Exception as e:  # noqa: BLE001
        return 500, {"ok": False, "error": f"read config failed: {e}"}

    new_lines = []
    inside_llm = False
    inside_deepseek = False
    replaced = False

    for line in lines:
        stripped = line.lstrip()

        # 进入/离开 LLM 顶层块
        if not stripped or stripped.startswith("#"):
            # 空行或注释不影响状态
            pass
        elif not line.startswith(" "):  # 顶层键
            # 遇到新的顶层键时，如果是 LLM 开始块，否则退出 LLM
            inside_llm = stripped.startswith("LLM:")
            inside_deepseek = False

        # 检测 DeepSeekLLM 块开始
        if inside_llm and stripped.startswith("DeepSeekLLM:"):
            inside_deepseek = True

        # 若进入 DeepSeekLLM 块之外的其它子块，则退出 inside_deepseek
        if inside_llm and inside_deepseek and stripped and not stripped.startswith(("#", "DeepSeekLLM:", "type:", "model_name:", "url:", "api_key:")):
            # 粗略判断：出现新的字段行时，如果不是我们关心的字段，且缩进与 api_key 同级，则认为离开 DeepSeekLLM
            # 这里保持保守策略，只在首次匹配 api_key 时替换
            pass

        if inside_llm and inside_deepseek and stripped.startswith("api_key:"):
            indent = line[: len(line) - len(stripped)]
            new_lines.append(f"{indent}api_key: {api_key}")
            replaced = True
            continue

        new_lines.append(line)

    if not replaced:
        return 500, {"ok": False, "error": "DeepSeekLLM.api_key line not found in data/.config.yaml"}

    try:
        cfg_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return 500, {"ok": False, "error": f"write config failed: {e}"}

    return 200, {"ok": True}


async def _restart_xiaozhi_service() -> Tuple[bool, str]:
    """
    调用 systemctl restart xiaozhi 以使配置生效。
    如果当前环境没有 systemd 或权限不足，会返回 failed 和错误信息，但不会影响写入结果。
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            "restart",
            "xiaozhi",
            stdout=PIPE,
            stderr=PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return True, (stdout.decode("utf-8", "ignore") or "").strip()
        msg = stderr.decode("utf-8", "ignore") or f"systemctl exited with {proc.returncode}"
        return False, msg.strip()
    except FileNotFoundError:
        return False, "systemctl not found; please restart xiaozhi service manually."
    except Exception as e:  # noqa: BLE001
        return False, str(e)


async def proxy_ota(request: web.Request) -> web.Response:
    """
    反向代理 OTA 请求到内部 xiaozhi-server，并重写 websocket.url 为外部可访问的 WS 地址。
    """
    upstream_url = f"{UPSTREAM_HTTP_BASE}{request.rel_url}"
    async with aiohttp.ClientSession() as session:
        async with session.request(
            method=request.method,
            url=upstream_url,
            headers=_filtered_headers(request.headers),
            data=await request.read(),
        ) as resp:
            text = await resp.text()

    # 尝试解析 JSON 并重写 websocket.url
    try:
        body = json.loads(text)
        ws_info = body.get("websocket")
        if isinstance(ws_info, dict) and "url" in ws_info:
            # 使用当前请求的 host + 端口 作为外部 WebSocket 入口
            external_origin = request.url.origin()
            external_ws = external_origin.with_scheme("ws").with_path("/xiaozhi/v1/")
            body["websocket"]["url"] = str(external_ws)
        return web.json_response(body, status=resp.status)
    except Exception:
        # 非 JSON 或解析失败时，直接透传原始响应
        return web.Response(
            status=resp.status,
            text=text,
            headers={"Content-Type": resp.headers.get("Content-Type", "text/plain")},
        )


async def proxy_vision(request: web.Request) -> web.Response:
    """
    反向代理视觉分析接口到内部 xiaozhi-server。
    """
    upstream_url = f"{UPSTREAM_HTTP_BASE}{request.rel_url}"
    async with aiohttp.ClientSession() as session:
        async with session.request(
            method=request.method,
            url=upstream_url,
            headers=_filtered_headers(request.headers),
            data=await request.read(),
        ) as resp:
            body = await resp.read()

    headers = {
        "Content-Type": resp.headers.get("Content-Type", "application/json"),
    }
    return web.Response(status=resp.status, body=body, headers=headers)


async def proxy_websocket(request: web.Request) -> web.StreamResponse:
    """
    WebSocket 反向代理：前端 <-> test_server.py <-> 内部 xiaozhi-server。
    """
    # 与前端建立 WebSocket 连接
    ws_client = web.WebSocketResponse()
    await ws_client.prepare(request)

    # 构造内部 xiaozhi-server 的 WS URL，保留 query（device-id / client-id / authorization 等）
    upstream_ws_url = f"{UPSTREAM_WS_BASE}{request.rel_url}"

    async with aiohttp.ClientSession() as session:
        try:
            ws_upstream = await session.ws_connect(upstream_ws_url)
        except Exception as e:  # noqa: BLE001
            await ws_client.send_str(f"Failed to connect upstream WebSocket: {e}")
            await ws_client.close()
            return ws_client

        async def client_to_upstream() -> None:
            async for msg in ws_client:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await ws_upstream.send_str(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await ws_upstream.send_bytes(msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    await ws_upstream.close()
                    break

        async def upstream_to_client() -> None:
            async for msg in ws_upstream:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await ws_client.send_str(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await ws_client.send_bytes(msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    await ws_client.close()
                    break

        # 并发桥接两个方向
        await asyncio.gather(client_to_upstream(), upstream_to_client())

    return ws_client


def _filtered_headers(headers: aiohttp.typedefs.LooseHeaders) -> dict:
    """
    过滤掉 hop-by-hop 头部，防止转发时出问题。
    """
    hop_by_hop = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}
    return {k: v for k, v in headers.items() if k.lower() not in hop_by_hop}


async def index(request: web.Request) -> web.StreamResponse:
    """
    根路径：返回 index.html。
    """
    return web.FileResponse(BASE_DIR / "index.html")


def create_app() -> web.Application:
    app = web.Application()

    # API 路由
    app.router.add_get("/xiaozhi/tester/config", handle_tester_config)
    app.router.add_get("/xiaozhi/tester/llm-config", handle_llm_config_get)
    app.router.add_post("/xiaozhi/tester/llm-config", handle_llm_config)
    app.router.add_route("*", "/xiaozhi/ota/{tail:.*}", proxy_ota)
    app.router.add_route("*", "/mcp/vision/explain", proxy_vision)
    app.router.add_route("*", "/xiaozhi/v1/", proxy_websocket)

    # 测试页根路径
    app.router.add_get("/", index)

    # 静态资源（js、css、images 等）
    app.router.add_static("/", BASE_DIR, show_index=False)

    # 为所有响应添加基础 CORS 头
    @web.middleware
    async def cors_middleware(request: web.Request, handler):
        resp = await handler(request)
        if isinstance(resp, web.StreamResponse):
            resp.headers.setdefault("Access-Control-Allow-Origin", "*")
            resp.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            resp.headers.setdefault("Access-Control-Allow-Headers", "Content-Type")
        return resp

    app.middlewares.append(cors_middleware)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Xiaozhi test page server (aiohttp)")
    parser.add_argument(
        "--port",
        type=int,
        default=8006,
        help="Port to listen on (default: 8006)",
    )
    args = parser.parse_args()

    app = create_app()
    print(
        f"[xiaozhi-test] Serving test page at http://0.0.0.0:{args.port}/ "
        f"(root={BASE_DIR})"
    )
    web.run_app(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    import asyncio

    main()

