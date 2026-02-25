"""
小智回复接口 Handler

为 OpenClaw 提供将文本回复推送到指定设备的能力。
"""

from aiohttp import web

from config.logger import setup_logging
from core.connection import get_active_connection_by_device_id
from core.handle.intentHandler import speak_txt

TAG = __name__


class XiaozhiReplyHandler:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logging()

    async def handle_post(self, request: web.Request) -> web.Response:
        """
        POST /xiaozhi/reply

        请求体:
        {
            "device_id": "esp32_default",
            "text": "要播放到设备上的文本"
        }
        """
        try:
            data = await request.json()
            device_id = data.get("device_id") or data.get("deviceId")
            text = data.get("text")

            if not device_id or not text:
                return web.json_response(
                    {"ok": False, "error": "device_id 和 text 必填"}, status=400
                )

            # 查找对应设备的在线连接
            conn = await get_active_connection_by_device_id(device_id)
            if not conn:
                return web.json_response(
                    {
                        "ok": False,
                        "error": f"device_id={device_id} 未找到在线连接",
                    },
                    status=404,
                )

            # 复用现有的 TTS 播报逻辑
            speak_txt(conn, text)
            self.logger.bind(tag=TAG).info(
                f"已将 OpenClaw 回复推送到设备 {device_id}: {text[:50]}..."
            )

            return web.json_response({"ok": True})

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"xiaozhi reply 接口出错: {e}")
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def handle_options(self, request: web.Request) -> web.Response:
        """处理 CORS 预检请求"""
        return web.Response(
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            }
        )

