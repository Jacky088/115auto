"""日志流：写 SQLite + 内存缓冲 + asyncio 广播给 WebSocket 客户端。"""
import asyncio
import itertools
import time
from collections import deque

from .. import database as db

_buf: deque = deque(maxlen=500)
_seq = itertools.count(1)  # 全局递增序号：前端据此对「重连补发」去重，避免日志重复显示
_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None  # WebSocket 所在事件循环（日志可能来自工作线程）


def _push(q: asyncio.Queue, entry: dict):
    try:
        q.put_nowait(entry)
    except asyncio.QueueFull:
        pass  # 客户端消费太慢：丢弃这一条实时日志，保留订阅（不能因此永久退订）
    except Exception:
        _subscribers.discard(q)


def log(level: str, module: str, message: str):
    entry = {"seq": next(_seq), "level": level, "module": module,
             "message": message, "ts": time.time()}
    _buf.append(entry)
    try:
        db.add_log(level, module, message)
    except Exception:
        pass
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    target = _loop
    for q in list(_subscribers):
        if target is not None and current is not target:
            # 来自线程池（如转存工作器）：切换到事件循环线程，asyncio.Queue 不是线程安全的
            try:
                target.call_soon_threadsafe(_push, q, entry)
            except RuntimeError:
                _subscribers.discard(q)  # 事件循环已关闭
        else:
            _push(q, entry)


def info(module, message): log("info", module, message)
def warn(module, message): log("warn", module, message)
def error(module, message): log("error", module, message)


def exc(e: BaseException, tail: int = 2) -> str:
    """异常 → 带具体原因的日志文本：异常类型: 消息 + 调用栈末尾（定位问题用）。

    在 except 块里用：logstream.error("transfer", f"工作器异常: {logstream.exc(e)}")
    """
    import traceback
    body = f"{type(e).__name__}: {e}"
    try:
        frames = traceback.format_exception(type(e), e, e.__traceback__)
        if tail > 0 and len(frames) > 1:
            body += "\n" + "".join(frames[-(tail + 1):-1]).strip()
    except Exception:
        pass
    return body


def recent(n: int = 200) -> list[dict]:
    return list(_buf)[-n:]


def subscribe() -> asyncio.Queue:
    global _loop
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        _loop = None
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue):
    global _loop
    _subscribers.discard(q)
    if not _subscribers:
        _loop = None
