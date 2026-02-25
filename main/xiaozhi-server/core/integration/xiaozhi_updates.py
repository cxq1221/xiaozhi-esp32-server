"""
小智消息队列模块：用于 OpenClaw 长轮询拉取。

提供内存队列，存储待拉取的用户文本消息。
"""

import time
import asyncio
from collections import defaultdict, deque
from typing import Dict, Deque, List

# 全局自增 ID + 每个 device_id 一条队列
_message_id: int = 0
_queues: Dict[str, Deque[dict]] = defaultdict(deque)
_lock = asyncio.Lock()


async def push_message(device_id: str, text: str) -> None:
    """
    将一条来自某设备的文本消息推入内存队列。
    
    Args:
        device_id: 设备 ID
        text: 用户说的文本内容
    """
    global _message_id
    async with _lock:
        _message_id += 1
        _queues[device_id].append(
            {
                "id": _message_id,
                "device_id": device_id,
                "text": text,
                "ts": time.time(),
            }
        )


async def get_updates(device_id: str, offset: int, timeout: int = 30) -> List[dict]:
    """
    长轮询接口：返回所有 id > offset 的消息。
    如果暂时没有新消息，则最多等待 timeout 秒。
    
    Args:
        device_id: 设备 ID
        offset: 上次处理到的最后一条消息 ID
        timeout: 最长挂起时间（秒）
        
    Returns:
        消息列表，每条消息包含 id, device_id, text, ts
    """
    deadline = time.time() + timeout

    while True:
        async with _lock:
            q = _queues[device_id]
            items = [m for m in q if m["id"] > offset]
            if items:
                return items

        if time.time() >= deadline:
            return []

        # 没有数据，睡一小会儿继续查
        await asyncio.sleep(1)

