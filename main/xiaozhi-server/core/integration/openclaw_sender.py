from __future__ import annotations

import httpx
from typing import Any


async def send_to_openclaw(conn: Any, text: str) -> None:
    """
    将小智识别到的用户文本转发到 OpenClaw。

    最小实现：只做单向上报，不等待或使用返回结果。
    不会影响原有对话链路，失败时仅记录日志。
    """

    # 从连接对象上获取配置和日志器
    cfg = getattr(conn, "config", {}) or {}
    logger = getattr(conn, "logger", None)

    openclaw_cfg = cfg.get("openclaw") or {}
    if not openclaw_cfg.get("enabled"):
        return

    base_url = openclaw_cfg.get("base_url")
    if not base_url:
        if logger:
            logger.bind(tag="openclaw").warning(
                "OpenClaw 已启用但未配置 base_url，跳过转发"
            )
        return

    channel = openclaw_cfg.get("channel", "xiaozhi")
    timeout = float(openclaw_cfg.get("timeout", 10))

    device_id = getattr(conn, "device_id", None) or "esp32_default"

    payload = {
        "channel": channel,
        "user_id": device_id,
        "message": text,
        "context": {
            "source": "xiaozhi-esp32",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            await client.post(f"{base_url}/api/v1/chat", json=payload)
        if logger:
            logger.bind(tag="openclaw").debug(
                f"已将消息转发到 OpenClaw，device_id={device_id}, text={text[:50]}..."
            )
    except Exception as e:
        if logger:
            logger.bind(tag="openclaw").error(f"转发到 OpenClaw 失败: {e}")


