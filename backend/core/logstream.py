"""日志流：写 SQLite + 内存缓冲 + asyncio 广播给 WebSocket 客户端。"""
import asyncio
import itertools
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from .. import database as db

_buf: deque = deque(maxlen=500)
_seq = itertools.count(1)  # 全局递增序号：前端据此对「重连补发」去重，避免日志重复显示
_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None  # WebSocket 所在事件循环（日志可能来自工作线程）
# 单工作线程串行写日志 DB：事件循环线程调用 log() 时不再同步等 SQLite 写锁
# （线程池长事务持锁时会把事件循环卡住 → WS 心跳超时「未连接」+ 页面请求排队）
_log_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="logwrite")


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
        on_loop = False
        try:
            on_loop = asyncio.get_running_loop() is not None
        except RuntimeError:
            on_loop = False
        if on_loop:
            # 事件循环线程：DB 写入交给后台线程（fire-and-forget），绝不阻塞循环本身
            _log_writer.submit(db.add_log, level, module, message)
        else:
            db.add_log(level, module, message)
    except Exception:
        pass
    target = _loop
    for q in list(_subscribers):
        if target is not None and on_loop and current_is_loop(target):
            # 来自事件循环且目标即本循环：直接投递（asyncio.Queue 非线程安全）
            _push(q, entry)
        elif target is not None:
            # 来自线程池（如转存工作器）：切换到事件循环线程
            try:
                target.call_soon_threadsafe(_push, q, entry)
            except RuntimeError:
                _subscribers.discard(q)  # 事件循环已关闭
        else:
            _push(q, entry)


def current_is_loop(target) -> bool:
    try:
        return asyncio.get_running_loop() is target
    except RuntimeError:
        return False


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
