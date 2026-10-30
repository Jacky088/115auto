"""日志流：写 SQLite + 内存缓冲 + asyncio 广播给 WebSocket 客户端。"""
import asyncio
import time
from collections import deque

from .. import database as db

_buf: deque = deque(maxlen=500)
_subscribers: set[asyncio.Queue] = set()


def log(level: str, module: str, message: str):
    entry = {"level": level, "module": module, "message": message, "ts": time.time()}
    _buf.append(entry)
    try:
        db.add_log(level, module, message)
    except Exception:
        pass
    dead = []
    for q in _subscribers:
        try:
            q.put_nowait(entry)
        except Exception:
            dead.append(q)
    for q in dead:
        _subscribers.discard(q)


def info(module, message): log("info", module, message)
def warn(module, message): log("warn", module, message)
def error(module, message): log("error", module, message)


def recent(n: int = 200) -> list[dict]:
    return list(_buf)[-n:]


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue):
    _subscribers.discard(q)
