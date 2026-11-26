"""p115client 封装：登录态/容量缓存、风控自适应限速、目录遍历与读写、分享转存与云下载。

注意：所有网络调用是同步阻塞的，调用方（转存 worker）需放到线程池执行。
扫码登录与 Cookie 导入位于 `auth.py`，定时文件整理匹配引擎位于 `auto_move.py`，
本模块末尾保留全部既有符号重导出以维持 100% 向后兼容。
"""
import asyncio
import functools
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx  # noqa: F401  check_login 的网络异常分类依赖（缺失时 isinstance 抛 NameError）
try:
    from urllib3_future.exceptions import HTTPError as _Urllib3HTTPError
except Exception:  # pragma: no cover
    _Urllib3HTTPError = None

_NET_EXC_TYPES = (
    (httpx.HTTPError, ConnectionError, TimeoutError, OSError, _Urllib3HTTPError)
    if _Urllib3HTTPError is not None
    else (httpx.HTTPError, ConnectionError, TimeoutError, OSError)
)

from .. import config, database as db
_lock = threading.Lock()
_client = None  # P115Client 单例（懒加载）

# ---- 115 风控自适应限速 ----
# 连续出现风控征兆错误时放大转存间隔（1→2→3→5→8 倍），成功若干次后逐级恢复。
# 转存节奏由 queue_worker 读取 pace_factor() 应用。
_risk_lock = threading.Lock()
_consec_risk = 0
_risk_cooldown_until = 0.0
_last_risk_err = ""
_last_risk_at = 0.0
_RISK_KEYWORDS = ("频繁", "风控", "验证码", "ip登录异常", "too many")
# limit / 405 用词边界匹配：errno=405、HTTP 405、rate limit 命中；
# "abc405xx"（分享码）、"file size limitation" 等无关错误串不命中（子串匹配误升退避档）
_RISK_WORD_RE = re.compile(r"(?<![0-9a-z])(?:limit|405)(?![0-9a-z])")
_PACE_STEPS = (1, 2, 3, 5, 8)
_COOLDOWN_STEPS = (0, 60, 180, 600, 1800)  # 各风控档位对应的自适应冷却秒数


def _hits_risk_keyword(s: str) -> bool:
    return any(k in s for k in _RISK_KEYWORDS) or bool(_RISK_WORD_RE.search(s))


def note_115_error(err: str):
    """转存相关接口报错后调用：命中风控征兆词则升级退避档位并启动指数冷却。"""
    global _consec_risk, _risk_cooldown_until, _last_risk_err, _last_risk_at
    s = (err or "").lower()
    with _risk_lock:
        if _hits_risk_keyword(s):
            _consec_risk = min(_consec_risk + 1, len(_PACE_STEPS) - 1)
            now = time.time()
            _last_risk_at = now
            _last_risk_err = (err or "").strip()[:160]
            cd = _COOLDOWN_STEPS[_consec_risk]
            if cd > 0:
                _risk_cooldown_until = max(_risk_cooldown_until, now + cd)


def note_115_ok():
    """一次完整成功后调用：逐级恢复到正常节奏并解除冷却。"""
    global _consec_risk, _risk_cooldown_until
    with _risk_lock:
        if _consec_risk > 0:
            _consec_risk -= 1
        if _consec_risk == 0:
            _risk_cooldown_until = 0.0


def pace_factor() -> int:
    """当前应使用的间隔倍数（1 = 正常）。"""
    with _risk_lock:
        return _PACE_STEPS[_consec_risk]


def risk_cooldown_remaining() -> int:
    """当前仍需等待的风控冷却剩余秒数（0 表示无冷却）。"""
    with _risk_lock:
        rem = _risk_cooldown_until - time.time()
        return max(0, int(rem))


def risk_status() -> dict:
    """返回当前 115 风控退避与冷却状态，供仪表盘看板展示。"""
    with _risk_lock:
        now = time.time()
        rem = max(0, int(_risk_cooldown_until - now))
        return {
            "level": _consec_risk,
            "max_level": len(_PACE_STEPS) - 1,
            "pace_factor": _PACE_STEPS[_consec_risk],
            "cooling": rem > 0,
            "cooldown_remaining_sec": rem,
            "cooldown_until": _risk_cooldown_until if rem > 0 else 0.0,
            "last_error": _last_risk_err,
            "last_risk_at": _last_risk_at,
        }


def probe_and_recover_risk() -> dict:
    """手动探测 115 接口健康度：若恢复正常则清零冷却并重置风控档位。"""
    global _consec_risk, _risk_cooldown_until
    from ..core import logstream
    st = check_login(force=True)
    if st.get("ok"):
        with _risk_lock:
            _consec_risk = 0
            _risk_cooldown_until = 0.0
        logstream.info("115", "手动探测 115 接口正常，已解除风控冷却并恢复标准转存节奏")
        return {"ok": True, "risk": risk_status(), "msg": "115 接口探测正常，已退出风控冷却"}
    err = st.get("err") or "115 接口暂未恢复"
    if _hits_risk_keyword(err.lower()):
        note_115_error(err)
    return {"ok": False, "risk": risk_status(), "msg": f"探测未通过：{err[:120]}"}


# ---- 进程级 115 后台/写接口串行队列与最小冷却间隔 ----
# 所有后台重操作与写操作（转存、云下载提交/核对、目录遍历/新建/移动、自动整理、
# 网页文件写操作）统一经 pan_call 串行化，避免并发撞车触发 115 风控。
# 轻量交互只读与带锁缓存接口（check_login、space_info、validate_cookie、扫码登录、
# 网页网盘只读浏览/搜索）放行不进此锁，避免被后台长任务堵死超时。
_api_lock = threading.RLock()
_last_api_at = -1000.0
_MIN_API_GAP = 0.25  # 任意两次串行 115 请求间的最小间隔（秒）
_MAX_API_GAP = 2.0   # 含风控退避倍数后的单步上限（须 ≥ _MIN_API_GAP × 8 档，否则最高退避档被截断失效）

# 专用线程池：隔离阻塞型 115/网络后台任务，防止占满默认 asyncio 线程池
# （小核容器上默认仅 5~6 个线程）而饿死 TG 频道轮询、Userbot 与本地 SQLite 读写。
_pan_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="pan115")


async def run_pan_thread(fn, *args, **kwargs):
    """在专用线程池中执行阻塞型 115 / 外部网络任务，避免挤占默认 asyncio.to_thread 池。"""
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(_pan_executor, functools.partial(fn, *args, **kwargs))
    return await loop.run_in_executor(_pan_executor, fn, *args)


def pan_call(fn, *args, **kwargs):
    """进程级 115 后台/写请求串行入口（可重入互斥 + 最小间隔节流）。"""
    global _last_api_at
    with _api_lock:
        gap = min(_MAX_API_GAP, _MIN_API_GAP * pace_factor())
        wait = (_last_api_at + gap) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            return fn(*args, **kwargs)
        finally:
            _last_api_at = time.monotonic()

# 登录态/容量查询缓存：后台仪表盘与文件浏览会频繁调用，避免每次都打 115 接口
_cache_lock = threading.Lock()
_login_cache: tuple[float, dict] | None = None  # (过期时间, 结果)
_space_cache: tuple[float, dict] | None = None
_login_call_lock = threading.Lock()  # 缓存未命中时合并并发：同一时刻只放一个真实校验
_space_call_lock = threading.Lock()
_LOGIN_OK_TTL = 60      # 登录有效：60 秒内复用
_LOGIN_BAD_TTL = 30     # 登录失效：30 秒内复用（便于恢复后尽快识别）
_SPACE_TTL = 300        # 容量信息: 5 分钟
# 容量查询的等锁上限：锁内是真实网络请求，若持锁线程卡住，
# 无上限等待会让调用线程（含事件循环线程）被永久挂起，因此统一限时并降级。
_SPACE_LOCK_TIMEOUT = 10.0


class Pan115Error(Exception):
    # Cookie 特征字段（p115client 部分异常会把带凭据的请求上下文带进消息）：
    # 异常统一在构造时脱敏，避免 UID/CID/SEID 值经日志 / WS 日志流 / 接口响应外泄
    _COOKIE_RE = re.compile(r"((?:UID|CID|SEID|KID)\s*=\s*)[^;\"'\s&]+", re.IGNORECASE)

    @staticmethod
    def _scrub(text: str) -> str:
        return Pan115Error._COOKIE_RE.sub(r"\1***", str(text or ""))

    def __init__(self, *args):
        super().__init__(*(self._scrub(a) if isinstance(a, str) else a for a in args))


def _invalidate_caches():
    global _login_cache, _space_cache
    with _cache_lock:
        _login_cache = None
        _space_cache = None


def _get_client():
    global _client
    with _lock:
        if _client is not None:
            return _client
        if not config.COOKIE_FILE.exists():
            raise Pan115Error("115 未登录，请到「账号管理」重新扫码登录或导入 Cookie")
        cookie = config.COOKIE_FILE.read_text().strip()
        if not cookie:
            raise Pan115Error("115 Cookie 为空，请到「账号管理」重新扫码登录或导入 Cookie")
        from p115client import P115Client
        _client = P115Client(cookie)
        _orig_req = _client.request
        def _req_with_timeout(*args, **kwargs):
            kwargs.setdefault("timeout", 30)
            return _orig_req(*args, **kwargs)
        _client.request = _req_with_timeout
        return _client


def reset_client():
    global _client
    with _lock:
        _client = None
    _invalidate_caches()


# ---------- 账号 ----------

def _space_num(v) -> int:
    """容量字段兼容：可能是数值，也可能是 {"size": N, "size_format": "..."} 对象。"""
    if isinstance(v, dict):
        v = v.get("size", 0)
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def space_info(force: bool = False, timeout: int = 15) -> dict:
    """容量信息 {total, use, free, ratio}（字节）。默认复用 5 分钟缓存。

    `timeout` 为真实请求的秒级上限：调度热路径（如工作器门禁轮询）传入更短的上限，
    保证内层网络请求先于外层 `wait_for` 超时，从而走「沿用上次状态」的降级分支，
    而不是被外层计时器打断导致整轮调度被丢弃。

    缓存未命中时非阻塞合并并发：同一时刻只放一个真实请求打 115，
    若已有线程正在打接口，其余调用直接复用现有旧缓存（0 毫秒响应），绝不排队等锁。
    """
    global _space_cache
    now = time.time()
    if not force:
        with _cache_lock:
            cached = _space_cache
        if cached and now < cached[0]:
            return cached[1]
    if force:
        acquired = _space_call_lock.acquire(timeout=_SPACE_LOCK_TIMEOUT)
    else:
        acquired = _space_call_lock.acquire(blocking=False)
    if not acquired:
        with _cache_lock:
            if _space_cache is not None:
                return _space_cache[1]
        # 缓存为空（首次探测或上次失败）才真正等锁；限时等待，
        # 避免 115 侧挂起时把调用线程（可能正是事件循环线程）无限期堵死。
        if not _space_call_lock.acquire(timeout=_SPACE_LOCK_TIMEOUT):
            with _cache_lock:
                if _space_cache is not None:
                    return _space_cache[1]
            raise Pan115Error("115 容量查询排队超时，请稍后重试")
    try:
        if not force:
            with _cache_lock:
                cached = _space_cache
            if cached and now < cached[0]:
                return cached[1]
        client = _get_client()
        try:
            resp = client.user_space_info(timeout=timeout)
        except TypeError:
            resp = client.user_space_info()
        if not resp.get("state"):
            raise Pan115Error(str(resp)[:200])
        data = resp.get("data", {}) or {}
        total = _space_num(data.get("total_size") or data.get("all_total"))
        use = _space_num(data.get("use_size") or data.get("all_use"))
        free = _space_num(data.get("free_size") or data.get("all_remain")) or max(total - use, 0)
        ratio = round(use / total * 100, 1) if total else 0
        result = {"total": total, "use": use, "free": free, "ratio": ratio}
        now_ts = time.time()
        with _cache_lock:
            _space_cache = (now_ts + _SPACE_TTL, result)
        return result
    finally:
        _space_call_lock.release()


def _get_cached_login(now: float, force: bool) -> dict | None:
    if force:
        return None
    with _cache_lock:
        cached = _login_cache
    if cached and now < cached[0]:
        return cached[1]
    return None


def check_login(force: bool = False) -> dict:
    """校验 cookie，返回 {ok, name, err, latency_ms, checked_at}。
    常规调用成功缓存 60s、失败缓存 30s；force=True 跳过缓存实时检测。
    """
    global _login_cache
    now = time.time()
    hit = _get_cached_login(now, force)
    if hit:
        return hit

    with _login_call_lock:
        now = time.time()
        hit = _get_cached_login(now, force)
        if hit:
            return hit

        t0 = time.monotonic()
        try:
            client = _get_client()
            try:
                resp = client.user_my_info(timeout=12)
            except TypeError:
                resp = client.user_my_info()
            if resp.get("state"):
                data = resp.get("data", {})
                db.set_setting("cookie_ok", True)
                result = {"ok": True, "name": data.get("user_name", "") or data.get("uname", "")}
                ttl = _LOGIN_OK_TTL
            else:
                db.set_setting("cookie_ok", False)
                result = {"ok": False, "err": str(resp)[:200]}
                ttl = _LOGIN_BAD_TTL
        except Exception as e:
            if isinstance(e, _NET_EXC_TYPES):
                result = {"ok": None, "err": f"网络异常，无法确认 Cookie 状态: {e}", "network": True}
            else:
                db.set_setting("cookie_ok", False)
                result = {"ok": False, "err": str(e)[:200]}
            ttl = _LOGIN_BAD_TTL

        latency = round((time.monotonic() - t0) * 1000)
        result["latency_ms"] = latency
        result["checked_at"] = time.time()

        with _cache_lock:
            _login_cache = (time.time() + ttl, result)
        return result


# ---------- 文件系统 ----------

def _file_id(f: dict) -> str:
    """115 各接口的 id 字段兼容：网页接口 file_id / open 接口 fid / 分享或目录 cid。"""
    for k in ("fid", "file_id", "cid", "id"):
        v = f.get(k)
        if v is not None and str(v) not in ("", "0"):
            return str(v)
    return str(f.get("pick_code") or f.get("pc") or "")


def _file_name(f: dict) -> str:
    return str(f.get("file_name") or f.get("fn") or f.get("n") or f.get("name") or "")


def _is_dir_item(f: dict) -> bool:
    """目录判定：fc/file_category == 0（网页接口），或 open 接口的 is_dir。

    注意不能写成 ``f.get("fc") or f.get("file_category")``：fc 为整数 0 时是 falsy，
    会被错误地取到 None，从而把目录当成文件。
    """
    for k in ("fc", "file_category"):
        if f.get(k) is not None:
            return str(f.get(k)) == "0"
    raw_is_dir = f.get("is_dir")
    if raw_is_dir is not None:
        if isinstance(raw_is_dir, str):
            return raw_is_dir.strip().lower() in ("1", "true", "yes", "y")
        return bool(raw_is_dir)
    return False


def _to_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


from .dirs import (  # noqa: F401
    _dir_cache,
    _dir_cache_get,
    _dir_cache_lock,
    _dir_cache_put,
    _list_dir,
    clear_dir_cache,
    ensure_subdir,
    mkdirs,
    resolve_dir_cid,
)


# ---------- 分享 ----------
from .share import (  # noqa: F401
    receive,
    share_snap_all,
)


def offline_download(url: str, cid: int) -> dict:
    """提交云下载任务（ED2K/磁力/HTTP），保存到目录 cid。"""
    client = _get_client()
    resp = pan_call(client.clouddownload_task_add_url, {"url": url, "wp_path_id": cid})
    if not resp.get("state"):
        msg = resp.get("error", "") or str(resp)[:200]
        if "目录" in msg or "cid" in msg.lower() or "wp_path" in msg.lower():
            clear_dir_cache()
        raise Pan115Error(f"云下载任务提交失败: {msg}")
    return resp


def task_list_all(max_pages: int = 20) -> list[dict]:
    """拉取 115 云下载任务列表（含 url/name/percentDone/status/info_hash/file_id 字段）。

    全量分页（每页 50，最多 20 页 = 1000 条），显式传递 stat=0 以拉取全部任务（包含下载中与已完成）。
    任一页接口失败都会抛出 Pan115Error（而不是返回不完整/空列表），
    调用方据此跳过本轮判定，避免把「接口异常」误判成「任务不存在 → 下载失败」。
    """
    client = _get_client()
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            resp = pan_call(client.clouddownload_task_list,
                            {"page": page, "page_size": 50, "stat": 0})
        except Exception as e:
            raise Pan115Error(f"拉取云下载任务列表失败: {e}")
        if not resp.get("state"):
            msg = resp.get("error", "") or str(resp)[:200]
            raise Pan115Error(f"拉取云下载任务列表失败: {msg}")
        tasks = resp.get("tasks") or []
        out.extend(tasks)
        total = resp.get("total") or resp.get("count")
        if not tasks or len(tasks) < 50:
            break
        if total and len(out) >= int(total):
            break
        time.sleep(0.3)
    return out


def list_entries(cid: int, limit: int = 2000, known_ids: set[str] | None = None,
                 state: dict | None = None, before_request=None) -> list[dict]:
    """列出目录内全部条目 [{id, name, is_dir, size, sha1, uptime}]。

    known_ids / state / before_request 供扫描索引做增量与预算控制（可选，缺省行为与旧版一致）：
    - known_ids：索引里已存在的 file_id 集合；整页 file_id 全部已知即提前停止
      （目录按 user_ptime 新→旧返回，更旧的页视为未变，省下大量请求）；
    - state：回填 {"complete": bool, "requests": n}，complete=False 表示因预算/上限未扫完；
    - before_request：每页请求前的回调（扫描节流/预算/取消），返回 False 时停止翻页。
    """
    client = _get_client()
    out: list[dict] = []
    offset = 0
    requests = 0
    complete = True
    early_stopped = False  # 整页已知提前停止：更旧的页未被观察，不能据此判定条目消失
    page_size = 1150  # 115 单页上限
    while offset < limit:
        if before_request is not None and not before_request():
            complete = False
            break
        req_limit = min(page_size, limit - offset)
        resp = pan_call(client.fs_files, {"cid": cid, "limit": req_limit,
                                          "offset": offset, "o": "user_ptime", "asc": 0,
                                          "show_dir": 1})
        requests += 1
        if not resp.get("state"):
            msg = str(resp.get("error") or resp.get("errno") or str(resp)[:200])
            note_115_error(msg)
            raise Pan115Error(f"列目录失败: {msg}")
        data = resp.get("data") or []
        page_ids: list[str] = []
        for f in data:
            if not isinstance(f, dict):
                continue
            name = _file_name(f)
            if not name:
                continue
            fid = _file_id(f)
            page_ids.append(str(fid))
            f_size = _to_int(f.get("s") or f.get("fs") or f.get("file_size"), 0)
            out.append({
                "id": fid,
                "name": name,
                "is_dir": _is_dir_item(f),
                "size": f_size,
                "sha1": str(f.get("sha") or f.get("sha1") or ""),
                "uptime": float(_to_int(f.get("user_ptime") or f.get("upt"), 0)),
            })
        if not data:
            break
        offset += len(data)
        if known_ids and page_ids and len(page_ids) == len(data) \
                and all(pid in known_ids for pid in page_ids):
            # 本页全部已知：按更新时间倒序，更旧的页无需再翻。
            # 若本页已装满，说明还有更旧的页未观察，此时不能做“消失”标记。
            if len(data) >= req_limit:
                early_stopped = True
            break
        if offset >= limit:
            complete = False  # 达到单目录上限，目录可能还有更旧条目
            break
        if offset < limit:
            time.sleep(0.4 * pace_factor())
    if state is not None:
        state["complete"] = complete
        state["early_stopped"] = early_stopped
        state["requests"] = requests
    if requests:
        note_115_ok()  # 完整成功后逐级恢复限速档位
    return out



from .tree import (  # noqa: F401
    _EP_CN_RE,
    _EP_SEASON_RE,
    organize_seasons,
    walk_media_tree,
)


def search_file(name: str, limit: int = 20) -> list[dict]:
    """按文件名全盘搜索，返回 [{id, name, parent}]（找回误落其他目录的文件用）。"""
    client = _get_client()
    resp = pan_call(client.fs_search, {"search_value": name, "limit": limit})
    if not resp.get("state"):
        msg = str(resp.get("error") or resp.get("errno") or str(resp)[:200])
        note_115_error(msg)
        raise Pan115Error(f"搜索失败: {msg}")
    note_115_ok()
    out: list[dict] = []
    for f in (resp.get("data") or []):
        if not isinstance(f, dict):
            continue
        fname = _file_name(f)
        fid = _file_id(f)
        if not fname or not fid or _is_dir_item(f):
            continue
        parent = f.get("pid") or f.get("parent_id") or ""
        out.append({"id": str(fid), "name": fname, "parent": str(parent)})
    return out


def copy_files_to(ids: list[str], cid: int) -> dict:
    """把文件复制到目标目录（115 服务端秒复制，不动原位置）。"""
    client = _get_client()
    resp = pan_call(client.fs_copy, ids, pid=cid)
    if not resp.get("state"):
        msg = resp.get("error", "") or str(resp)[:200]
        raise Pan115Error(f"复制失败: {msg}")
    return resp


def list_file_names(cid: int, limit: int = 500) -> set[str]:
    """列出目录内的条目名集合（文件与目录，用于转存后反查确认）。

    115 网页接口 fs_files 的条目字段是 n/s/fc/pc（而不是 file_name/file_size），
    因此必须走 _file_name 兼容取值；旧实现只认 file_name，导致反查结果恒为空，
    转存其实成功却一直被误判为失败并反复重试。
    接口失败时抛出 Pan115Error，由调用方决定是否按「无法验证」放行，
    而不是静默返回空集合造成假阴性。
    """
    client = _get_client()
    names: set[str] = set()
    offset = 0
    while offset < limit:
        page_size = min(500, limit - offset)
        resp = pan_call(client.fs_files, {"cid": cid, "limit": page_size,
                                          "offset": offset, "o": "user_ptime", "asc": 0,
                                          "show_dir": 1})
        if not resp.get("state"):
            raise Pan115Error(f"列目录失败: {str(resp)[:200]}")
        data = resp.get("data") or []
        for f in data:
            if not isinstance(f, dict):
                continue
            name = _file_name(f)
            if name:
                names.add(name)
        if not data or len(data) < page_size:
            break
        count = int(resp.get("count") or 0)
        offset += len(data)
        if count and offset >= count:
            break
        if offset < limit:
            time.sleep(0.4 * pace_factor())  # 多页翻页间隔，降低请求频率
    return names


# ---------- 整理刮削专用 115 底层读写与树扫描封装 ----------

def _unwrap_write_resp(resp: dict, action_name: str) -> dict:
    """统一校验 115 写接口响应并在风控出错时记入退避。"""
    if not isinstance(resp, dict) or not resp.get("state"):
        msg = (resp.get("error") or resp.get("msg") or str(resp)[:200]) if isinstance(resp, dict) else str(resp)[:200]
        note_115_error(msg)
        raise Pan115Error(f"{action_name}被 115 拒绝: {msg}")
    note_115_ok()
    return resp


def _touch_scan_index(file_ids) -> None:
    """写操作后把受影响条目所在目录标记为脏，下次预览走增量重扫（失败静默）。"""
    try:
        from ..worker import scan_index
        scan_index.mark_dirty_for_file_ids([str(x) for x in file_ids if x])
    except Exception:
        pass


def rename_entry(fid: str | int, new_name: str) -> None:
    """重命名单个文件或文件夹（经 pan_call 串行并清目录缓存）。"""
    client = _get_client()
    name = (new_name or "").strip()
    if not name or len(name.encode("utf-8")) > 255:
        raise Pan115Error(f"目标文件名不合法或超长: {name[:60]}")
    try:
        resp = pan_call(client.fs_rename, (str(fid), name))
        _unwrap_write_resp(resp, "重命名")
        clear_dir_cache()
        _touch_scan_index([fid])
    except Pan115Error:
        raise
    except Exception as e:
        note_115_error(str(e))
        raise


def move_entries(fids: list[str], target_cid: int) -> None:
    """批量移动文件/文件夹到目标目录 cid（每批最多 30 个，串行节流）。"""
    ids = [str(i) for i in fids if i]
    if not ids:
        return
    client = _get_client()
    for i in range(0, len(ids), 30):
        chunk = ids[i:i + 30]
        if i > 0:
            time.sleep(0.4 * pace_factor())
        try:
            resp = pan_call(client.fs_move, chunk, pid=int(target_cid))
            _unwrap_write_resp(resp, "移动")
        except Pan115Error:
            raise
        except Exception as e:
            note_115_error(str(e))
            raise
    clear_dir_cache()
    _touch_scan_index(ids)


def delete_entries(fids: list[str]) -> None:
    """将指定文件/空目录移入 115 回收站（用于洗版清理旧版或清理空目录）。"""
    ids = [str(i) for i in fids if i]
    if not ids:
        return
    client = _get_client()
    try:
        resp = pan_call(client.fs_delete, ids)
        _unwrap_write_resp(resp, "移入回收站")
        clear_dir_cache()
        _touch_scan_index(ids)
    except Pan115Error:
        raise
    except Exception as e:
        note_115_error(str(e))
        raise


# ---------- 向后兼容门面重导出（auth / auto_move） ----------
from .auth import (  # noqa: E402, F401
    _LOGIN_RISK_HINT,
    _QR_LOGIN_APPS,
    _finish_lock,
    _login_err_hint,
    _norm_qr_app,
    import_cookie,
    qrcode_create,
    qrcode_finish,
    qrcode_poll,
    validate_cookie,
)
from .auto_move import (  # noqa: E402, F401
    _AD_BRACKET_RE,
    _AUTO_MOVE_SCAN_MAX_DIRS,
    _AUTO_MOVE_SKIP_SCAN_DIRS,
    _CHINESE_CHAR_RE,
    _CN_NUM_MAP,
    _DIR_MATCH_MIN_SIZE,
    _DUP_SUFFIX_RE,
    _EXTRA_KEYWORDS,
    _GENERIC_CN_TERMS,
    _GENERIC_LIBRARY_SKIP_FOLDERS,
    _MEDIA_EXTENSIONS,
    _ROMAN_NUMS,
    _should_skip_auto_move_dir,
    _strip_ad_brackets,
    collect_library_keyword_conflicts,
    extract_chinese_subject,
    extract_target_folder_name,
    move_matched_files_for_rule,
    normalize_title,
    parse_season_number,
    strip_duplicate_suffix,
)
