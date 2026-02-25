"""
小智消息更新接口 Handler

为 OpenClaw 提供长轮询拉取接口。
"""

from aiohttp import web
from core.integration.xiaozhi_updates import get_updates
from config.logger import setup_logging

TAG = __name__


class XiaozhiUpdatesHandler:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logging()

    async def handle_get(self, request: web.Request) -> web.Response:
        """
        GET /xiaozhi/updates?device_id=xxx&offset=0&timeout=30
        
        OpenClaw 长轮询拉取小智文本消息的接口。
        """
        try:
            device_id = request.query.get("device_id")
            if not device_id:
                return web.json_response(
                    {"ok": False, "error": "device_id 参数必填"}, status=400
                )

            offset = int(request.query.get("offset", "0"))
            timeout = int(request.query.get("timeout", "30"))

            # 限制超时时间，避免连接占用过久
            if timeout > 60:
                timeout = 60
            if timeout < 1:
                timeout = 1

            updates = await get_updates(device_id, offset, timeout)
            
            return web.json_response({"ok": True, "result": updates})

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"xiaozhi updates 接口出错: {e}")
            return web.json_response(
                {"ok": False, "error": str(e)}, status=500
            )

    async def handle_options(self, request: web.Request) -> web.Response:
        """处理 CORS 预检请求"""
        return web.Response(
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            }
        )

