"""
小智消息队列模块：用于 OpenClaw 长轮询拉取。

原始实现仅使用内存队列，xiaozhi-server 重启后会丢失尚未被 OpenClaw 拉取的消息。
现在增加简单的本地文件持久化：

- 所有入队消息会以一行一条 JSON 的形式追加写入 data/xiaozhi_updates.log
- 模块加载时从该文件恢复 _message_id 和内存队列
"""

import asyncio
import json
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, Deque, List

# 全局自增 ID + 每个 device_id 一条队列
_message_id: int = 0
_queues: Dict[str, Deque[dict]] = defaultdict(deque)
_lock = asyncio.Lock()


def _get_log_path() -> Path:
    """
    日志文件路径：<项目根>/data/xiaozhi_updates.log
    这里通过 __file__ 向上两级定位到 main/xiaozhi-server 目录。
    """
    base_dir = Path(__file__).resolve().parents[2]
    data_dir = base_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "xiaozhi_updates.log"


_LOG_PATH = _get_log_path()


def _load_persisted_messages() -> None:
    """
    启动时从本地日志恢复队列和自增 ID。

    文件格式：一行一个 JSON：
      {"id": 1, "device_id": "...", "text": "...", "ts": 1739350000.123}
    """
    global _message_id
    if not _LOG_PATH.exists():
        return
    try:
        with _LOG_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    mid = int(rec.get("id"))
                    device_id = str(rec.get("device_id") or "")
                    text = str(rec.get("text") or "")
                    ts = float(rec.get("ts") or time.time())
                except Exception:
                    # 单条解析失败直接跳过
                    continue
                if not device_id or not text:
                    continue
                _queues[device_id].append(
                    {
                        "id": mid,
                        "device_id": device_id,
                        "text": text,
                        "ts": ts,
                    }
                )
                if mid > _message_id:
                    _message_id = mid
    except Exception:
        # 启动阶段加载失败不应阻塞主流程，直接忽略错误，后续只使用内存队列。
        return


# 模块加载时尝试恢复历史消息
_load_persisted_messages()


async def push_message(device_id: str, text: str) -> None:
    """
    将一条来自某设备的文本消息推入内存队列，并追加写入本地日志。

    Args:
        device_id: 设备 ID
        text: 用户说的文本内容
    """
    global _message_id
    async with _lock:
        _message_id += 1
        record = {
            "id": _message_id,
            "device_id": device_id,
            "text": text,
            "ts": time.time(),
        }
        _queues[device_id].append(record)

        # 追加写入本地文件，保证重启后仍可恢复
        try:
            with _LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            # 写日志失败不影响主流程，只是无法持久化
            pass


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
                # 计算本次返回的最大消息 ID，并将 <= max_id 的记录视为已「消费」
                max_id = max(m["id"] for m in items)

                # 1) 内存中移除已返回的消息，避免重复返回
                _queues[device_id] = deque(m for m in q if m["id"] > max_id)

                # 2) 磁盘文件中移除该 device_id 的已返回消息，防止重放
                try:
                    if _LOG_PATH.exists():
                        lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()
                        with _LOG_PATH.open("w", encoding="utf-8") as f:
                            for line in lines:
                                raw = line.strip()
                                if not raw:
                                    continue
                                try:
                                    rec = json.loads(raw)
                                except Exception:
                                    # 解析失败的行原样写回，避免意外丢失
                                    f.write(line + "\n")
                                    continue
                                rec_dev = str(rec.get("device_id") or "")
                                rec_id = int(rec.get("id") or 0)
                                if rec_dev == device_id and rec_id <= max_id:
                                    # 已经返回给 OpenClaw 的消息：从文件中删除
                                    continue
                                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                except Exception:
                    # 文件清理失败不影响拉取流程，只是可能在少数情况下重复返回历史消息
                    pass

                return items

        if time.time() >= deadline:
            return []

        # 没有数据，睡一小会儿继续查
        await asyncio.sleep(1)

