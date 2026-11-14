"""日志流：写 SQLite + 内存缓冲 + asyncio 广播给 WebSocket 客户端。"""
import asyncio
import itertools
import re
import threading
import time
from collections import deque
from queue import Full, Queue

from .. import database as db

_buf: deque = deque(maxlen=500)
_seq = itertools.count(1)  # 全局递增序号：前端据此对「重连补发」去重，避免日志重复显示
_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None  # WebSocket 所在事件循环（日志可能来自工作线程）
# 单工作线程串行写日志 DB：事件循环线程调用 log() 时不再同步等 SQLite 写锁
# （线程池长事务持锁时会把事件循环卡住 → WS 心跳超时「未连接」+ 页面请求排队）。
# 用有界队列 + 专用线程（而非 ThreadPoolExecutor）：DB 卡顿时满了就丢新日志，
# 不让待写队列无限堆积吃内存。
_log_q: Queue = Queue(maxsize=2000)


def _log_worker():
    while True:
        fn = _log_q.get()
        try:
            fn()
        except Exception:
            pass
        finally:
            _log_q.task_done()


def _submit_log(fn) -> None:
    if _log_thread is None or not _log_thread.is_alive():
        _start_log_worker()
    try:
        _log_q.put_nowait(fn)
    except Full:
        pass  # 队列满（DB 长时间卡死）：丢弃这条日志，保护调用方


_log_thread: threading.Thread | None = None


def _start_log_worker():
    global _log_thread
    _log_thread = threading.Thread(target=_log_worker, name="logwrite", daemon=True)
    _log_thread.start()


def _push(q: asyncio.Queue, entry: dict):
    try:
        q.put_nowait(entry)
    except asyncio.QueueFull:
        pass  # 客户端消费太慢：丢弃这一条实时日志，保留订阅（不能因此永久退订）
    except Exception:
        _subscribers.discard(q)


# 纵深防御：日志统一出口处的凭据特征脱敏。当前代码没有直接打印 Cookie/Token 的调用点，
# 但异常串可能内嵌带凭据的请求上下文（p115client/httpx 的错误 repr）——在唯一出口
# 统一抹掉，任何未来调用点泄露凭据都会被这里兜住（WS 日志流 + SQLite logs 表同源）。
_SECRET_RE = re.compile(
    r"((?:UID|CID|SEID|KID|api_hash|access_token|refresh_token)\s*[=:]\s*)[^;\"'\s&,]+",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+")


def _redact(message: str) -> str:
    s = str(message or "")
    s = _SECRET_RE.sub(r"\1***", s)
    return _BEARER_RE.sub(r"\1***", s)


def log(level: str, module: str, message: str):
    message = _redact(message)
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
            _submit_log(lambda: db.add_log(level, module, message))
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
