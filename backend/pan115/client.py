"""p115client 封装：扫码登录（Web 流程）、目录逐级创建、分享文件遍历、转存、容量查询。

注意：所有网络调用是同步阻塞的，调用方（转存 worker）需放到线程池执行。
"""
import asyncio
import functools
import io
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import qrcode
import qrcode.image.svg

from .. import config, database as db
from ..core import logstream
from ..core.t2s import t2s
from ..core.episode import extract_episode_keys, parse as parse_episode

_lock = threading.Lock()
_client = None  # P115Client 单例（懒加载）

# ---- 115 风控自适应限速 ----
# 连续出现风控征兆错误时放大转存间隔（1→2→3→5→8 倍），成功若干次后逐级恢复。
# 转存节奏由 queue_worker 读取 pace_factor() 应用。
_risk_lock = threading.Lock()
_consec_risk = 0
_RISK_KEYWORDS = ("频繁", "风控", "验证码", "IP登录异常", "too many", "limit", "405")
_PACE_STEPS = (1, 2, 3, 5, 8)


def note_115_error(err: str):
    """转存相关接口报错后调用：命中风控征兆词则升级退避档位。"""
    global _consec_risk
    s = (err or "").lower()
    with _risk_lock:
        if any(k in s for k in _RISK_KEYWORDS):
            _consec_risk = min(_consec_risk + 1, len(_PACE_STEPS) - 1)


def note_115_ok():
    """一次完整成功后调用：逐级恢复到正常节奏。"""
    global _consec_risk
    with _risk_lock:
        if _consec_risk > 0:
            _consec_risk -= 1


def pace_factor() -> int:
    """当前应使用的间隔倍数（1 = 正常）。"""
    with _risk_lock:
        return _PACE_STEPS[_consec_risk]


# ---- 进程级 115 后台/写接口串行队列与最小冷却间隔 ----
# 所有后台重操作与写操作（转存、云下载提交/核对、目录遍历/新建/移动、自动整理、
# 网页文件写操作）统一经 pan_call 串行化，避免并发撞车触发 115 风控。
# 轻量交互只读与带锁缓存接口（check_login、space_info、validate_cookie、扫码登录、
# 网页网盘只读浏览/搜索）放行不进此锁，避免被后台长任务堵死超时。
_api_lock = threading.RLock()
_last_api_at = -1000.0
_MIN_API_GAP = 0.25  # 任意两次串行 115 请求间的最小间隔（秒）
_MAX_API_GAP = 1.5   # 含风控退避倍数后的单步上限（兼顾交互响应速度）

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


class Pan115Error(Exception):
    pass


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
        return _client


def reset_client():
    global _client
    with _lock:
        _client = None
    _invalidate_caches()


# ---------- 扫码登录 ----------

def qrcode_create() -> dict:
    """生成登录二维码。返回 {uid, qr_svg, token}。"""
    from p115client import P115Client
    app = str(db.get_setting("115_app", "alipaymini"))
    resp = P115Client.login_qrcode_token(app=app)
    token = resp["data"]
    qr_content = token.pop("qrcode", "") or f"https://115.com/scan/dg-{token['uid']}"
    img = qrcode.make(qr_content, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return {
        "uid": token["uid"],
        "qr_svg": buf.getvalue().decode(),
        "token": token,  # 含 uid/time/sign，用于轮询
        "app": app,
    }


def qrcode_poll(token: dict) -> dict:
    """长轮询扫码状态（115 接口挂起最长 30s）。

    返回 status: 0 等待 / 1 已扫描 / 2 已确认 / 负数 过期或取消。
    """
    from p115client import P115Client
    try:
        resp = P115Client.login_qrcode_scan_status(token, timeout=32)
    except Exception as e:
        logstream.warn("115", f"扫码状态查询异常（视为继续等待）: {e}")
        return {"status": 0}  # 网络抖动视为继续等待
    data = resp.get("data") or {}
    status = data.get("status", 0)
    return {"status": status}


_finish_lock = threading.Lock()  # 串行化取 cookie：并发调用会触发 115 风控

# 115 登录风控的已知错误 → 给用户的可操作指引（海外/机房 IP 最常触发）
_LOGIN_RISK_HINT = {
    "40101017": "115 风控拦截了本次登录（常见于海外/机房 IP）。可稍后重试或更换网络出口，"
                "或使用「导入 Cookie」：在本地浏览器登录 115.com 后复制 Cookie 粘贴。",
}


def _login_err_hint(resp: dict) -> str:
    """把取 cookie 失败的原始返回翻译成带指引的错误信息（已知风控错误不展示原始 JSON）。"""
    errno = str(resp.get("errno") or resp.get("code") or "")
    if errno in _LOGIN_RISK_HINT:
        return _LOGIN_RISK_HINT[errno]
    raw = json.dumps(resp, ensure_ascii=False)[:300]
    if "IP登录异常" in raw:
        return ("115 登录风控（IP 登录异常），一般当日 0 点自动解禁；可更换网络出口重试，"
                "或点「手动导入 Cookie」。")
    return f"获取 cookie 失败: {raw}"


def qrcode_finish(uid: str, app: str) -> dict:
    """扫码确认成功后取 cookie 并保存。

    取 cookie 接口对并发/重复调用敏感（易报"IP登录异常"），这里：
    - 进程内加锁串行化，避免多端同时轮询时并发取 cookie
    - 失败自动重试 3 次（间隔 1.5s），规避瞬时风控；确定性风控错误（如 40101017）不重试
    """
    from p115client import P115Client
    last_err: Exception | None = None
    with _finish_lock:
        for _ in range(3):
            try:
                resp = P115Client.login_qrcode_scan_result(uid, app)
                cookie = (resp.get("data") or {}).get("cookie", "") or resp.get("cookie", "")
                if cookie:
                    config.COOKIE_FILE.write_text(cookie)
                    config.secure_file(config.COOKIE_FILE)
                    reset_client()
                    db.set_setting("cookie_ok", True)
                    logstream.info("115", "扫码登录成功")
                    return {"ok": True}
                # 确定性风控错误（老乡验证等）：重试无意义且会加重风控，直接给出指引
                errno = str(resp.get("errno") or resp.get("code") or "")
                if errno in _LOGIN_RISK_HINT:
                    raise Pan115Error(_login_err_hint(resp))
                last_err = Pan115Error(_login_err_hint(resp))
            except Pan115Error:
                raise
            except Exception as e:
                last_err = e
            time.sleep(1.5)
    raise last_err or Pan115Error("获取 cookie 失败")


def validate_cookie(cookie: str) -> dict:
    """Cookie 有效性校验统一入口：项目里所有「新增 Cookie」的地方（手动导入及
    未来其他方式）都必须经此校验通过后才能落盘。
    返回 {ok, name, cookie(归一化后), err}。"""
    from p115client import P115Client
    cookie = cookie.strip().strip('"').strip("'")
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    if not cookie:
        return {"ok": False, "name": "", "cookie": "", "err": "Cookie 为空"}
    if not all(k in cookie for k in ("UID=", "CID=", "SEID=")):
        return {"ok": False, "name": "", "cookie": "",
                "err": "Cookie 里缺少 UID/CID/SEID 字段：请复制完整的 115 登录 Cookie"
                       "（形如 UID=xxx;CID=xxx;SEID=xxx）"}
    try:
        client = P115Client(cookie)
        resp = client.user_my_info()
    except Exception as e:
        return {"ok": False, "name": "", "cookie": cookie, "err": f"Cookie 无法使用: {e}"}
    if not resp.get("state"):
        return {"ok": False, "name": "", "cookie": cookie, "err": f"Cookie 校验失败: {str(resp)[:200]}"}
    data = resp.get("data", {}) or {}
    name = data.get("user_name", "") or data.get("uname", "")
    return {"ok": True, "name": name, "cookie": cookie, "err": ""}


def import_cookie(cookie: str) -> dict:
    """手动导入 cookie（海外 VPS 扫码被风控时的替代方案）：经统一校验入口通过后保存。"""
    v = validate_cookie(cookie)
    if not v["ok"]:
        raise Pan115Error(v["err"])
    config.COOKIE_FILE.write_text(v["cookie"])  # 落盘用校验时归一化后的同一份
    config.secure_file(config.COOKIE_FILE)
    reset_client()
    db.set_setting("cookie_ok", True)
    logstream.info("115", f"手动导入 Cookie 成功（账号：{v['name']}）")
    return {"ok": True, "name": v["name"]}


# ---------- 账号 ----------

def _space_num(v) -> int:
    """容量字段兼容：可能是数值，也可能是 {"size": N, "size_format": "..."} 对象。"""
    if isinstance(v, dict):
        v = v.get("size", 0)
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def space_info(force: bool = False) -> dict:
    """容量信息 {total, use, free, ratio}（字节）。默认复用 5 分钟缓存。

    缓存未命中时用锁合并并发：worker / 仪表盘 / 账号页同时来取也只打一次 115。
    字段兼容两种响应形态：proapi 返回 all_total/all_use/all_remain（值可能是
    {"size": N, "size_format": "..."} 对象），旧接口返回 total_size/use_size。
    """
    global _space_cache
    if not force:
        with _cache_lock:
            cached = _space_cache
        if cached and time.time() < cached[0]:
            return cached[1]
    with _space_call_lock:
        if not force:
            with _cache_lock:
                cached = _space_cache
            if cached and time.time() < cached[0]:
                return cached[1]
        client = _get_client()
        resp = client.user_space_info()
        if not resp.get("state"):
            raise Pan115Error(str(resp)[:200])
        data = resp.get("data", {}) or {}
        total = _space_num(data.get("total_size") or data.get("all_total"))
        use = _space_num(data.get("use_size") or data.get("all_use"))
        free = _space_num(data.get("free_size") or data.get("all_remain")) or max(total - use, 0)
        ratio = round(use / total * 100, 1) if total else 0
        result = {"total": total, "use": use, "free": free, "ratio": ratio}
        now = time.time()
        with _cache_lock:
            _space_cache = (now + _SPACE_TTL, result)
        return result


def check_login(force: bool = False) -> dict:
    """校验 cookie，返回 {ok, name, err}。成功缓存 60s、失败缓存 30s（force 跳过缓存）。

    缓存未命中时用锁合并并发：转存 worker / 文件浏览 / 仪表盘同时来校验
    也只放一个真实请求到 115，其余复用结果（防止并发堆积触发风控）。
    """
    global _login_cache
    if not force:
        with _cache_lock:
            cached = _login_cache
        if cached and time.time() < cached[0]:
            return cached[1]
    with _login_call_lock:
        now = time.time()
        if not force:
            with _cache_lock:
                cached = _login_cache
            if cached and now < cached[0]:
                return cached[1]
        try:
            client = _get_client()
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
            db.set_setting("cookie_ok", False)
            result = {"ok": False, "err": str(e)[:200]}
            ttl = _LOGIN_BAD_TTL
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


def _list_dir(pid: int = 0) -> dict[str, int]:
    """列出某目录下的子目录 {名字: cid}。"""
    client = _get_client()
    dirs: dict[str, int] = {}
    offset = 0
    while True:
        resp = pan_call(client.fs_files, {"cid": pid, "fc_mix": 0, "limit": 1150, "offset": offset,
                                          "o": "user_ptime", "asc": 0, "show_dir": 1})
        if not resp.get("state"):
            raise Pan115Error(f"fs_files 失败: {str(resp)[:200]}")
        data = resp.get("data") or []
        for f in data:
            if not isinstance(f, dict):
                continue
            fid, name = _file_id(f), _file_name(f)
            if not _is_dir_item(f) or not fid or not name:
                continue
            cid = _to_int(fid, -1)
            if cid >= 0:
                dirs[name] = cid
        total = _to_int(resp.get("count"), 0)
        offset += len(data)
        if not data or offset >= total:
            break
        time.sleep(0.4)  # 大目录多页翻页间隔，降低请求频率
    return dirs


# 目录路径 → cid 缓存：同一目标目录每个任务都会 mkdirs（去重检查 + 正式转存各一次），
# 逐层 _list_dir 还要每层 sleep 0.5s，缓存后命中直接返回，省 115 请求与等待。
# 带 TTL：用户在网页里改了目录结构时最多滞后 cache_ttl 过期一次，出错自动清空。
_dir_cache: dict[str, tuple[float, int]] = {}
_dir_cache_lock = threading.Lock()
_DIR_CACHE_TTL = 600.0  # 10 分钟


def _dir_cache_get(path: str) -> int:
    with _dir_cache_lock:
        hit = _dir_cache.get(path)
        if hit and time.time() < hit[0]:
            return hit[1]
        if hit:
            _dir_cache.pop(path, None)
    return 0


def _dir_cache_put(path: str, cid: int) -> None:
    with _dir_cache_lock:
        if len(_dir_cache) > 200:
            _dir_cache.clear()  # 简单兜底防无限增长
        _dir_cache[path] = (time.time() + _DIR_CACHE_TTL, cid)


def clear_dir_cache():
    """目录失效（转存/云下载报目录不存在等）时调用：下次重新逐级解析。"""
    with _dir_cache_lock:
        _dir_cache.clear()


def resolve_dir_cid(path: str) -> int:
    """只读解析 115 目录路径返回 cid；任一级目录不存在时返回 0（不自动创建）。"""
    if not path or not path.strip("/"):
        return 0
    norm = "/" + path.strip("/")
    if (hit := _dir_cache_get(norm)):
        return hit
    pid = 0
    walked = ""
    for name in [p for p in norm.strip("/").split("/") if p]:
        walked += "/" + name
        if (hit := _dir_cache_get(walked)):
            pid = hit
            continue
        dirs = _list_dir(pid)
        if name not in dirs:
            return 0
        pid = dirs[name]
        _dir_cache_put(walked, pid)
    return pid


def mkdirs(path: str) -> int:
    """逐级确保目录存在，返回最深层 cid。path 形如 /媒体库/电影。"""
    if not path or not path.strip("/"):
        return 0
    path = path.rstrip("/") or "/"
    cached = _dir_cache_get(path)
    if cached:
        return cached
    client = _get_client()
    parts = [p for p in path.strip("/").split("/") if p]
    pid = 0
    walked = ""  # 已解析到的前缀，逐级写入缓存
    for name in parts:
        walked += "/" + name
        if (hit := _dir_cache_get(walked)):
            pid = hit
            continue
        dirs = _list_dir(pid)
        if name in dirs:
            pid = dirs[name]
        else:
            resp = pan_call(client.fs_mkdir, {"cname": name, "pid": pid})
            if not resp.get("state"):
                raise Pan115Error(f"创建目录 {name} 失败: {str(resp)[:200]}")
            data = resp.get("data", {}) or {}
            new_cid = _to_int(data.get("file_id") or data.get("cid") or resp.get("cid"), 0)
            if not new_cid:
                new_cid = _list_dir(pid).get(name, 0)  # 响应缺 id 时回查上级目录
            pid = new_cid
            logstream.info("115", f"已创建目录 {name} (cid={pid})")
        _dir_cache_put(walked, pid)
        time.sleep(0.5)
    return pid


# ---------- 分享 ----------

def share_snap_all(share_code: str, receive_code: str, max_files: int = 5000) -> dict:
    """遍历分享的全部文件。返回 {title, files: [{fid, fn, fs}], total_size, top_names}。

    top_names 记录分享顶层（cid=0）的条目名：115 转存后若把分享整体以文件夹形式
    落到目标目录，顶层文件夹名只存在于这里，反查确认需要它。
    """
    from p115client import P115Client
    title = ""
    files: list[dict] = []
    total_size = 0
    top_names: list[str] = []
    top_ids: list[str] = []  # 分享顶层条目 id（目录为 cid）：整包转存用，保留目录结构

    def snap(cid: int, offset: int):
        resp = pan_call(P115Client.share_snap, {
            "share_code": share_code, "receive_code": receive_code,
            "cid": cid, "limit": 1150, "offset": offset,
        })
        if not resp.get("state"):
            raise Pan115Error(f"读取分享失败: {str(resp)[:300]}")
        return resp

    def _unwrap(resp: dict):
        """兼容两种响应形态：
        - 新（实测 web 接口）：列表/标题/计数都包在 data 里 → data.list / data.shareinfo / data.count
        - 旧（平铺）：resp.data 是 list → resp.shareinfo / resp.count
        """
        data = resp.get("data")
        if isinstance(data, dict):
            items = data.get("list") or []
            shareinfo = data.get("shareinfo") or resp.get("shareinfo") or {}
            count = data.get("count", resp.get("count", 0))
        elif isinstance(data, list):
            items = data
            shareinfo = resp.get("shareinfo") or {}
            count = resp.get("count", 0)
        else:
            items, shareinfo, count = [], {}, 0
        return items, (shareinfo if isinstance(shareinfo, dict) else {}), count

    stack = [0]
    seen_dirs: set[int] = set()
    seen_files: set[str] = set()  # 文件 fid 去重：服务端异常返回重复页时不重复计数
    truncated = False
    while stack and len(files) < max_files:
        cid = stack.pop(0)
        if cid in seen_dirs:
            continue
        seen_dirs.add(cid)
        offset = 0
        while True:
            resp = snap(cid, offset)
            items, shareinfo, total = _unwrap(resp)
            if not title:
                title = shareinfo.get("share_title", "") or resp.get("title", "") or ""
            for f in items:
                if not isinstance(f, dict):  # 防御：任何非 dict 元素跳过而不是崩溃
                    continue
                fid = _file_id(f)
                if not fid:  # 防御：缺 id 的条目无法转存，跳过而不是带着 None 提交
                    continue
                name = _file_name(f)
                if cid == 0 and name and name not in top_names:
                    top_names.append(name)  # 分享顶层条目名（文件或文件夹）
                if cid == 0 and str(fid) not in top_ids:
                    top_ids.append(str(fid))  # 顶层条目 id（文件夹为 cid）
                if _is_dir_item(f):  # 子目录，继续遍历
                    child = _to_int(fid, -1)
                    if child >= 0:
                        stack.append(child)
                    continue
                if str(fid) in seen_files:
                    continue
                seen_files.add(str(fid))
                size = _to_int(f.get("fs") or f.get("file_size") or f.get("s"), 0)
                files.append({"fid": str(fid), "fn": name, "fs": size})
                total_size += size
            offset += len(items)  # 按实际返回条数翻页：固定 1150 在服务端少返回时会漏项
            if offset >= int(total or 0) or not items or len(files) >= max_files:
                if len(files) >= max_files and (int(total or 0) > max_files or stack):
                    truncated = True  # 超大分享达到遍历上限，超出部分不会转存
                break
            time.sleep(0.5)
        time.sleep(0.5)  # 目录之间也留间隔：多目录分享逐页轰炸易触发风控
    if truncated:
        logstream.warn("115", f"分享 {share_code} 文件数达到 {max_files} 上限，"
                              f"仅遍历前 {max_files} 个文件，超出部分不会转存")
    return {"title": title, "files": files, "total_size": total_size, "truncated": truncated,
            "top_names": top_names, "top_ids": top_ids}


def receive(share_code: str, receive_code: str, cid: int, file_ids: list[str]) -> dict:
    """转存指定文件到目录 cid。

    注意：不能传 ``is_check=1``——115 把它当作「只校验能否转存」的预检请求，
    接口照样返回 ``state: true``，但实际不会落文件到网盘（线上表现为反查目录
    永远没有文件、任务无限重试）。官方网页真正执行转存时不带该参数。
    """
    client = _get_client()
    payload = {
        "share_code": share_code,
        "receive_code": receive_code,
        "file_id": ",".join(file_ids),
        "cid": cid,
    }
    try:
        payload["user_id"] = client.user_id  # 与 115 网页请求一致；取不到时省略
    except Exception:
        pass
    resp = pan_call(client.share_receive, payload)
    if not resp.get("state"):
        code = str(resp.get("error_code") or "").strip()
        msg = resp.get("error", "") or str(resp)[:200]
        if "目录" in msg or "cid" in msg.lower():
            clear_dir_cache()  # 目标目录可能已被删除/重命名：缓存失效，下次重新逐级解析
        raise Pan115Error(f"{msg}（error_code={code}）" if code else msg)
    return resp


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


def list_entries(cid: int, limit: int = 2000) -> list[dict]:
    """列出目录内全部条目 [{id, name, is_dir}]（整理/去重用，需要 id 而不只是名字）。"""
    client = _get_client()
    out: list[dict] = []
    offset = 0
    while offset < limit:
        resp = pan_call(client.fs_files, {"cid": cid, "limit": min(500, limit - offset),
                                          "offset": offset, "o": "user_ptime", "asc": 0,
                                          "show_dir": 1})
        if not resp.get("state"):
            raise Pan115Error(f"列目录失败: {str(resp)[:200]}")
        data = resp.get("data") or []
        for f in data:
            if not isinstance(f, dict):
                continue
            name = _file_name(f)
            if not name:
                continue
            f_size = _to_int(f.get("s") or f.get("fs") or f.get("file_size"), 0)
            out.append({
                "id": _file_id(f),
                "name": name,
                "is_dir": _is_dir_item(f),
                "size": f_size,
                "sha1": str(f.get("sha") or f.get("sha1") or ""),
            })
        if not data:
            break
        offset += len(data)
        if offset < limit:
            time.sleep(0.4)
    return out


_EP_SEASON_RE = re.compile(r"[Ss](\d{1,2})[\s._-]*[Ee]\d{1,3}")
_EP_CN_RE = re.compile(r"第\s?(\d{1,3})\s?[集话話]")


def organize_seasons(cid: int, min_files: int = 2) -> int:
    """把目录下的剧集文件按季归入「Season N」子目录，返回移动的文件数。

    - 只处理带集数标识（S01E01 / 第1集）的文件；第N集默认按第 1 季
    - 单季少于 min_files 个文件时不建目录（避免为两三集碎片建一堆空壳）
    - 目录已存在时复用
    """
    client = _get_client()
    entries = list_entries(cid)
    groups: dict[int, list[dict]] = {}
    for e in entries:
        if e["is_dir"]:
            continue
        m = _EP_SEASON_RE.search(e["name"]) or _EP_CN_RE.search(e["name"])
        if not m:
            continue
        season = 1 if _EP_CN_RE.fullmatch(m.group(0).strip()) else max(int(m.group(1)), 1)
        groups.setdefault(season, []).append(e)
    moved = 0
    for season, items in sorted(groups.items()):
        if len(items) < min_files:
            continue
        sname = f"Season {season}"
        target = next((e for e in entries if e["is_dir"] and e["name"] == sname), None)
        if target is None:
            resp = pan_call(client.fs_mkdir, {"cname": sname, "pid": cid})
            if not resp.get("state"):
                raise Pan115Error(f"创建 {sname} 失败: {str(resp)[:200]}")
            data = resp.get("data", {}) or {}
            new_id = _to_int(data.get("file_id") or data.get("cid") or resp.get("cid"), 0)
            if not new_id:
                new_id = _list_dir(cid).get(sname, 0)
            target = {"id": str(new_id), "name": sname, "is_dir": True}
            entries.append(target)
            time.sleep(0.5)
        pan_call(client.fs_move, [str(i["id"]) for i in items], pid=int(str(target["id"])))
        moved += len(items)
        time.sleep(0.5)
    return moved


def search_file(name: str, limit: int = 20) -> list[dict]:
    """按文件名全盘搜索，返回 [{id, name, parent}]（找回误落其他目录的文件用）。"""
    client = _get_client()
    resp = pan_call(client.fs_search, {"search_value": name, "limit": limit})
    if not resp.get("state"):
        raise Pan115Error(f"搜索失败: {str(resp)[:200]}")
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
            time.sleep(0.4)  # 多页翻页间隔，降低请求频率
    return names

_CHINESE_CHAR_RE = re.compile(r"[一-龥]")

_MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".ts", ".mov", ".avi", ".flv", ".wmv", ".m2ts", ".rmvb", ".iso",
    ".srt", ".ass", ".vtt", ".sub"
}
_EXTRA_KEYWORDS = (
    "预告", "预告片", "花絮", "特辑", "彩蛋", "片段", "宣传片", "制作特辑",
    "sample", "trailer", "preview", "teaser", "extra", "behindthescenes", "featurette"
)
_ROMAN_NUMS = {
    "Ⅰ": "1", "Ⅱ": "2", "Ⅲ": "3", "Ⅳ": "4", "Ⅴ": "5",
    "Ⅵ": "6", "Ⅶ": "7", "Ⅷ": "8", "Ⅸ": "9", "Ⅹ": "10",
    "ⅰ": "1", "ⅱ": "2", "ⅲ": "3", "ⅳ": "4", "ⅴ": "5",
}
_CN_NUM_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15, "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20
}
_GENERIC_CN_TERMS = {
    "第一季", "第二季", "第三季", "第四季", "第五季", "第六季", "第七季", "第八季",
    "特别篇", "剧场版", "全集", "完结", "合集", "更新", "重制版", "电影版"
}


def extract_chinese_subject(kw: str) -> str:
    """从包含中英文的关键词中提取最长连续纯中文主体（排除通用词缀，长度>=2）。"""
    if not kw:
        return ""
    cn_chunks = re.findall(r"[\u4e00-\u9fa5]+", kw)
    if not cn_chunks:
        return ""
    valid_chunks = [c for c in cn_chunks if len(c) >= 2 and c not in _GENERIC_CN_TERMS]
    if not valid_chunks:
        return ""
    return max(valid_chunks, key=len)


_GENERIC_LIBRARY_SKIP_FOLDERS = {
    "其他", "未整理", "待整理", "待分类", "临时", "新建文件夹", "temp", "trash", "recycle", "lost+found"
}

_DUP_SUFFIX_RE = re.compile(r"[\s\._-]*[\(\[（【]\s*([1-9]\d{0,2})\s*[\)\]）】]$")


def strip_duplicate_suffix(filename: str) -> tuple[str, int]:
    """剥离 115 重复转存时自动追加的 (1)、(2)、（1）、（2）、 (1) 等冲突后缀。
    返回 (clean_name, dup_index)：
    - 若无后缀：返回 (原文件名, 0)
    - 若有后缀：返回 (去除后缀后的规范文件名, 后缀编号数字)
    同时兼容半角括号 ()、全角括号（）、中括号 []、全角中括号【】及有无前置空格。
    排除 4 位年份（如 2019、2024），避免误伤正片标题年份。
    """
    root, ext = os.path.splitext(filename or "")
    m = _DUP_SUFFIX_RE.search(root)
    if m:
        clean_root = root[:m.start()].rstrip()
        try:
            dup_idx = int(m.group(1))
        except ValueError:
            dup_idx = 1
        return f"{clean_root}{ext}", dup_idx
    return filename or "", 0


def parse_season_number(folder_name: str) -> int | None:
    """从文件夹名称中解析季度数字（1, 2, ...），特别篇为 0，解析不到返回 None。
    完全兼容：Season01, Season 01, Season.01, Season1, S01, S1, season01, s01, s1,
             第一季, 第1季, 第01季, 第 1 季, 特别篇, SP 等（大小写不敏感）。
    """
    name = (folder_name or "").strip()
    if not name:
        return None
    cleaned = re.sub(r"^[\(\[\{（【《「『]|[\)\]\}）】》」』]$", "", name).strip()
    if cleaned != name:
        return parse_season_number(cleaned)
    m = re.search(r"^(?:season[\s\.\-_]*|[sS])0*(\d{1,2})$", name.strip(), re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"^第\s*([一二三四五六七八九十\d]+)\s*季$", name.strip())
    if m:
        val = m.group(1).strip()
        if val.isdigit():
            return int(val)
        return _CN_NUM_MAP.get(val)
    if re.search(r"^(?:特别篇|specials?|sp(?:\s*\d{1,3})?)$", name.strip(), re.IGNORECASE):
        return 0
    return None


def normalize_title(s: str, fuzzy_t2s: bool = True) -> str:
    """标题软化归一化：小写 + 简繁自适应 + 罗马数字转阿拉伯数字 + 去除所有标点符号与空白字符。"""
    if not s:
        return ""
    if fuzzy_t2s:
        s = t2s(s)
    s = s.lower()
    for rom, arab in _ROMAN_NUMS.items():
        s = s.replace(rom.lower(), arab)
    return re.sub(r"[^\w\u4e00-\u9fa5]+|_", "", s)


def extract_target_folder_name(target_dir: str) -> str:
    """从目标目录路径中提取匹配关键词：
    1. 优先取末级目录，若末级为季目录（如 Season 1、第1季、特别篇等），自动回溯上级剧名目录；
    2. 若回溯后仍无中文（如 /电视剧/Drama/Season 1），尝试继续向上找包含中文的目录；
    3. 自动剥离常见的年份后缀（如 (2023)、[2024]、.2023 等），提取出纯净剧名。
    """
    parts = [p.strip() for p in (target_dir or "").strip().strip("/").split("/") if p.strip()]
    if not parts:
        return ""
    idx = len(parts) - 1
    if idx > 0 and parse_season_number(parts[idx]) is not None:
        idx -= 1
    candidate = parts[idx]
    if not _CHINESE_CHAR_RE.search(candidate) and idx > 0 and _CHINESE_CHAR_RE.search(parts[idx - 1]):
        candidate = parts[idx - 1]
    cleaned = re.sub(r"(?:[\s\.\-_]*[\(\[\{（【]?\s*(?:19\d{2}|20\d{2})\s*[\)\]\}）】]?)+$", "", candidate).strip()
    return cleaned or candidate


_AUTO_MOVE_SKIP_SCAN_DIRS = frozenset({
    "@eadir", ".ds_store", "__macosx", "_待清理旧版", "_待清理重复",
    "sample", "samples", "subs", "subtitles", "extras", "extra",
    "bdmv", "certificate", "video_ts",
})
_AUTO_MOVE_SCAN_MAX_DIRS = 50


def _should_skip_auto_move_dir(dir_name: str) -> bool:
    low = (dir_name or "").strip().lower()
    if not low or low in _AUTO_MOVE_SKIP_SCAN_DIRS:
        return True
    return any(extra in low for extra in _EXTRA_KEYWORDS)


def move_matched_files_for_rule(source_dir: str, target_dir: str,
                                keyword: str | None = None,
                                scan_subdirs: bool = True,
                                mode: str = "library",
                                season_folder: bool = True,
                                fuzzy_t2s: bool = True,
                                dedup_by_episode: bool | None = None,
                                max_depth: int = 2,
                                scan_cache: dict | None = None) -> tuple[int, list[str]]:
    """按映射规则排队移动满足条件的文件（做好 115 严格风控）：

    模式支持：
    - library（总库智能分流模式）：目标目录作为媒体总库（如 /电视剧），自动识别库内所有一级剧集子文件夹
      （如 折月亮 (2026)、繁花 (2023)），根据文件名智能归入对应剧目文件夹；若剧目下有季子目录（如 Season 1/第一季），
      且开启了 season_folder，自动分流入季目录。
    - exact（一对一精确匹配模式）：目标目录作为具体剧目目录（如 /电视剧/折月亮 (2026)），仅移动匹配此剧名的文件。

    防风控与安全原则：
    1. 只移动文件，文件夹绝不移动（is_dir == False），忽略 BDMV/花絮/字幕独立文件夹
    2. 文件名必须包含中文（_CHINESE_CHAR_RE.search(name)）
    3. 媒体扩展名白名单（_MEDIA_EXTENSIONS），过滤广告与非媒体文件
    4. 排除预告/花絮/sample 等非正片文件
    5. 源目录穿透默认最多 2 层（兼容 剧名/Season 目录），带总目录配额上限与周期缓存复用
    6. 分批 fs_move（每批最多 30 个），批次间休眠 0.5s * pace_factor()
    7. 支持按季集防重（同剧目标已存在同季同集视频时跳过）及同名/副本防撞
    8. 捕获 115 异常并检测风控关键词，一旦命中记录风控状态并抛出
    """
    if mode == "exact":
        kw = (keyword or extract_target_folder_name(target_dir)).strip()
        if not kw:
            raise ValueError(f"目标目录「{target_dir}」未包含有效文件夹名称，无法提取匹配关键词，不予执行")

    if dedup_by_episode is None:
        try:
            dedup_ep = bool(db.get_setting("auto_move_dedup_by_episode", True))
        except Exception:
            dedup_ep = True
    else:
        dedup_ep = bool(dedup_by_episode)

    target_cid = mkdirs(target_dir)
    if target_cid is None:
        raise RuntimeError(f"无法获取或创建目标目录: {target_dir}")

    source_cid = mkdirs(source_dir)
    if source_cid is None:
        raise RuntimeError(f"无法获取源目录: {source_dir}")

    if mode == "exact" and source_cid == target_cid:
        logstream.info("auto_move", f"精确模式下源目录与目标目录相同「{target_dir}」，无需移动")
        return 0, []

    pace = pace_factor()
    client = _get_client()
    entries_cache: dict[int, list[dict]] = scan_cache.setdefault("dir_entries", {}) if scan_cache is not None else {}

    def cached_list_entries(cid: int, limit: int = 2000, sleep_sec: float = 0.0) -> list[dict]:
        if cid in entries_cache:
            return entries_cache[cid]
        if sleep_sec > 0 and pace > 0:
            time.sleep(sleep_sec * pace)
        res = list_entries(cid, limit=limit)
        if scan_cache is not None:
            entries_cache[cid] = res
        return res

    drama_targets: list[dict] = []

    if mode == "library":
        # 总库智能分流模式：列出目标总库下的一级子文件夹构建剧目映射
        root_targets = cached_list_entries(target_cid, limit=2000, sleep_sec=0.2)
        sub_dramas = [e for e in root_targets if e.get("is_dir")]
        for sd in sub_dramas:
            sd_name = sd.get("name", "").strip()
            if not sd_name or sd_name.lower() in _GENERIC_LIBRARY_SKIP_FOLDERS:
                continue
            if parse_season_number(sd_name) is not None:
                continue  # 排除裸露的季目录
            if not _CHINESE_CHAR_RE.search(sd_name):
                continue  # 必须包含中文汉字，排除纯英文/纯数字目录
            sd_kw = extract_target_folder_name(sd_name)
            sd_norm_kw = normalize_title(sd_kw, fuzzy_t2s=fuzzy_t2s)
            if len(sd_norm_kw) < 2:
                continue  # 剧名关键词过短跳过，避免单字严重误匹配
            sd_cn = extract_chinese_subject(sd_kw)
            sd_norm_cn = normalize_title(sd_cn, fuzzy_t2s=fuzzy_t2s) if sd_cn else ""
            drama_targets.append({
                "cid": int(str(sd["id"])),
                "name": sd_name,
                "kw": sd_kw,
                "norm_kw": sd_norm_kw,
                "norm_cn_kw": sd_norm_cn,
            })
        # 按关键词长度降序排序，长剧名优先匹配（如「流浪地球2」优先于「流浪地球」）
        drama_targets.sort(key=lambda x: len(x["norm_kw"]), reverse=True)
        if not drama_targets:
            logstream.info("auto_move", f"总库「{target_dir}」下暂未发现包含中文的剧集子文件夹，跳过本次分流")
            return 0, []
    else:
        # 一对一精确匹配模式
        norm_kw = normalize_title(kw, fuzzy_t2s=fuzzy_t2s)
        cn_subj = extract_chinese_subject(kw)
        norm_cn_subj = normalize_title(cn_subj, fuzzy_t2s=fuzzy_t2s) if cn_subj else ""
        drama_targets = [{
            "cid": target_cid,
            "name": os.path.basename(target_dir.rstrip("/")),
            "kw": kw,
            "norm_kw": norm_kw,
            "norm_cn_kw": norm_cn_subj,
        }]

    # 1. 受控 BFS 扫描源目录（默认最多穿透 2 层：源目录 -> 剧名目录 -> Season X，并设总目录请求配额防风控）
    effective_depth = 0 if not scan_subdirs else max(1, min(int(max_depth or 2), 3))
    candidate_files: list[dict] = []
    scan_queue: list[tuple[int, int, str]] = [(source_cid, 0, "")]
    visited_scan_cids: set[int] = set()
    scanned_subdirs = 0

    while scan_queue:
        cur_cid, cur_depth, cur_folder_name = scan_queue.pop(0)
        if cur_cid in visited_scan_cids:
            continue
        visited_scan_cids.add(cur_cid)
        try:
            cur_entries = cached_list_entries(
                cur_cid,
                limit=2000 if cur_depth == 0 else 1000,
                sleep_sec=0.2 if cur_depth == 0 else 0.25,
            )
        except Exception:
            continue

        # BDMV / VIDEO_TS 原盘目录保护：不拆散枚举内部流文件
        if cur_depth > 0:
            dir_names_low = {
                str(x.get("name") or "").strip().lower()
                for x in cur_entries if x.get("is_dir")
            }
            if dir_names_low & {"bdmv", "certificate", "video_ts"}:
                continue

        for e in cur_entries:
            if not e.get("is_dir"):
                candidate_files.append({
                    **e,
                    "_parent_cid": cur_cid,
                    "_parent_folder_name": cur_folder_name,
                })
            elif cur_depth < effective_depth and scanned_subdirs < _AUTO_MOVE_SCAN_MAX_DIRS:
                dname = str(e.get("name") or "").strip()
                if _should_skip_auto_move_dir(dname):
                    continue
                try:
                    sub_cid = int(str(e["id"]))
                except (TypeError, ValueError):
                    continue
                if sub_cid in visited_scan_cids:
                    continue
                scanned_subdirs += 1
                scan_queue.append((sub_cid, cur_depth + 1, dname))

    if not candidate_files:
        return 0, []

    # 季子目录与目标文件查重缓存（原始文件名 + 剥离副本后缀规范名 + 按季集防重集合）
    drama_season_cache: dict[int, dict[int, int]] = {}
    drama_season_names: dict[int, dict[int, str]] = {}
    dest_file_cache: dict[int, set[str]] = {}
    dest_canon_cache: dict[int, set[str]] = {}
    dir_ep_cache: dict[tuple[int, str], set[tuple[int, int, str, str]]] = {}

    def get_dest_file_names(cid: int) -> tuple[set[str], set[str]]:
        if cid not in dest_file_cache:
            try:
                raw_names = list_file_names(cid, limit=2000)
                canon_names = {strip_duplicate_suffix(n)[0] for n in raw_names}
                dest_file_cache[cid] = raw_names
                dest_canon_cache[cid] = canon_names
            except Exception:
                dest_file_cache[cid] = set()
                dest_canon_cache[cid] = set()
        return dest_file_cache[cid], dest_canon_cache[cid]

    def get_dir_episode_keys(cid: int, folder_hint: str = "") -> set[tuple[int, int, str, str]]:
        ckey = (cid, folder_hint)
        if ckey not in dir_ep_cache:
            raw_names, _ = get_dest_file_names(cid)
            eps: set[tuple[int, int, str, str]] = set()
            for rname in raw_names:
                if any(extra in rname.lower() for extra in _EXTRA_KEYWORDS):
                    continue
                for k in extract_episode_keys(rname, folder_hint=folder_hint):
                    eps.add(k)
            dir_ep_cache[ckey] = eps
        return dir_ep_cache[ckey]

    def get_season_subdirs(drama_cid: int) -> dict[int, int]:
        if drama_cid not in drama_season_cache:
            mapping: dict[int, int] = {}
            names_map: dict[int, str] = {}
            try:
                entries = cached_list_entries(drama_cid, limit=100, sleep_sec=0.2)
                for item in entries:
                    if item.get("is_dir"):
                        sname = item.get("name", "")
                        s_idx = parse_season_number(sname)
                        if s_idx is not None:
                            mapping[s_idx] = int(str(item["id"]))
                            names_map[s_idx] = sname
            except Exception:
                pass
            drama_season_cache[drama_cid] = mapping
            drama_season_names[drama_cid] = names_map
        return drama_season_cache[drama_cid]

    seen_ids: set[str] = set()
    # 聚类去重：开启按季集防重且为带集号的视频时按 (drama_cid, ep_keys) 聚类择优，否则按 (final_dest_cid, clean_name)
    candidates_by_group: dict[tuple, list[dict]] = {}
    ep_skipped_cnt = 0
    ep_skipped_samples: list[str] = []

    for f in candidate_files:
        fid = str(f.get("id"))
        fname = f.get("name", "")
        if not fid or not fname or fid in seen_ids:
            continue
        # 扩展名白名单校验
        ext = os.path.splitext(fname)[1].lower()
        if ext not in _MEDIA_EXTENSIONS:
            continue
        # 中文硬指标：必须含有汉字（纯数字、纯英文坚决不移动）
        if not _CHINESE_CHAR_RE.search(fname):
            continue
        # 预告/花絮非正片过滤
        fname_lower = fname.lower()
        if any(extra in fname_lower for extra in _EXTRA_KEYWORDS):
            continue

        # 软化标题比对（支持中英文混合双层匹配）
        norm_name = normalize_title(fname, fuzzy_t2s=fuzzy_t2s)
        matched_drama = None
        # 第一轮：优先精确匹配完整体关键词（如 doctorx白色黑手党时代 命中）
        for dt in drama_targets:
            if dt["norm_kw"] in norm_name:
                matched_drama = dt
                break
        # 第二轮：若完整体未命中，回落比对纯中文主体（如发布组仅写 白色黑手党时代 时命中）
        if not matched_drama:
            for dt in drama_targets:
                if dt.get("norm_cn_kw") and len(dt["norm_cn_kw"]) >= 2 and dt["norm_cn_kw"] in norm_name:
                    matched_drama = dt
                    break
        if not matched_drama:
            continue

        # 目标 cid 确定：若开启按季归档且剧目下有季子目录，按季落入季目录
        drama_cid = matched_drama["cid"]
        final_dest_cid = drama_cid
        seasons_map: dict[int, int] = {}
        if season_folder:
            s_num, ep_num, _ = parse_episode(fname)
            if s_num is None and ep_num is not None:
                s_num = parse_season_number(f.get("_parent_folder_name", ""))
                if s_num is None:
                    s_num = 1  # 电视剧单集默认第 1 季
            if s_num is not None:
                seasons_map = get_season_subdirs(drama_cid)
                if s_num in seasons_map:
                    final_dest_cid = seasons_map[s_num]

        # 若文件已经在目标目录，无需移动
        if f.get("_parent_cid") == final_dest_cid:
            continue

        # 剥离 (1)、(2)、（1）、（2）等重复转存后缀，提取基准规范名与副本编号
        clean_name, dup_idx = strip_duplicate_suffix(fname)

        # 目标目录文件名查重：目标库已有原始文件或任一历史副本时，源目录全部副本均跳过
        dest_raw_names, dest_canon_names = get_dest_file_names(final_dest_cid)
        if fname in dest_raw_names or clean_name in dest_raw_names or clean_name in dest_canon_names:
            continue

        # 按季集防重：同剧目标（剧集根目录 + 对应季目录）已有相同季集视频时跳过不同命名版本
        ep_keys = extract_episode_keys(fname, folder_hint=f.get("_parent_folder_name", "")) if dedup_ep else []
        if dedup_ep and ep_keys:
            existing_eps = set(get_dir_episode_keys(drama_cid, ""))
            if final_dest_cid != drama_cid:
                s_hint = drama_season_names.get(drama_cid, {}).get(ep_keys[0][0], f"Season {ep_keys[0][0]}")
                existing_eps |= get_dir_episode_keys(final_dest_cid, s_hint)
            elif season_folder:
                for ek_s in {k[0] for k in ep_keys}:
                    if ek_s in seasons_map:
                        s_hint = drama_season_names.get(drama_cid, {}).get(ek_s, f"Season {ek_s}")
                        existing_eps |= get_dir_episode_keys(seasons_map[ek_s], s_hint)
            if all(k in existing_eps for k in ep_keys):
                ep_skipped_cnt += 1
                if len(ep_skipped_samples) < 5:
                    ep_skipped_samples.append(fname)
                continue

        seen_ids.add(fid)
        if dedup_ep and ep_keys:
            group_key = ("ep", drama_cid, tuple(ep_keys))
        else:
            group_key = ("name", final_dest_cid, clean_name)

        candidates_by_group.setdefault(group_key, []).append({
            "id": fid,
            "name": fname,
            "clean_name": clean_name,
            "dup_idx": dup_idx,
            "size": int(f.get("size", f.get("s", 0)) or 0),
            "parent_cid": f.get("_parent_cid"),
            "dest_cid": final_dest_cid,
            "drama_cid": drama_cid,
            "drama_name": matched_drama["name"],
            "ep_keys": ep_keys,
        })

    if not candidates_by_group:
        if ep_skipped_cnt > 0:
            logstream.info(
                "auto_move",
                f"按季集防重跳过 {ep_skipped_cnt} 个同季同集视频（目标已存在）：{'、'.join(ep_skipped_samples[:3])}",
            )
        return 0, []

    # 择优提取：每组仅移动一份文件！
    # 优先级：dup_idx 升序（0 原版最优先，其次 (1)、(2)）；同编号时大文件优先（如 2160p 优于 1080p）
    files_to_move: list[dict] = []
    batch_eps_added: dict[int, set[tuple[int, int, str, str]]] = {}
    for gkey, items in candidates_by_group.items():
        items.sort(key=lambda x: (x["dup_idx"], -x["size"]))
        best = items[0]
        if len(items) > 1 and gkey[0] == "ep":
            ep_skipped_cnt += len(items) - 1
            for extra_it in items[1:]:
                if len(ep_skipped_samples) < 5:
                    ep_skipped_samples.append(extra_it["name"])
        if gkey[0] == "ep" and best["ep_keys"]:
            d_added = batch_eps_added.setdefault(best["drama_cid"], set())
            if all(k in d_added for k in best["ep_keys"]):
                ep_skipped_cnt += 1
                if len(ep_skipped_samples) < 5:
                    ep_skipped_samples.append(best["name"])
                continue
            d_added.update(best["ep_keys"])
        files_to_move.append(best)
        dest_raw_names, dest_canon_names = get_dest_file_names(best["dest_cid"])
        dest_raw_names.add(best["name"])
        dest_canon_names.add(best["clean_name"])

    if ep_skipped_cnt > 0:
        logstream.info(
            "auto_move",
            f"按季集防重跳过 {ep_skipped_cnt} 个同季同集视频（目标或本批已存在）：{'、'.join(ep_skipped_samples[:3])}",
        )

    # 按目标目录分组移动
    by_dest: dict[int, list[dict]] = {}
    for item in files_to_move:
        by_dest.setdefault(item["dest_cid"], []).append(item)

    moved_count = 0
    sample_names: list[str] = []

    for dest_cid, items in by_dest.items():
        for i in range(0, len(items), 30):
            batch = items[i:i + 30]
            ids = [x["id"] for x in batch if x.get("id")]
            if not ids:
                continue
            time.sleep(0.5 * pace_factor())
            try:
                resp = pan_call(client.fs_move, ids, pid=dest_cid)
                if not resp.get("state"):
                    msg = resp.get("error", "") or resp.get("msg", "") or str(resp)[:200]
                    note_115_error(msg)
                    raise Pan115Error(f"移动文件被 115 拒绝: {msg}")
                moved_count += len(ids)
                sample_names.extend([x["name"] for x in batch][:2])
                note_115_ok()
                if scan_cache is not None:
                    entries_cache.pop(dest_cid, None)
                    for x in batch:
                        if x.get("parent_cid") is not None:
                            entries_cache.pop(int(x["parent_cid"]), None)
            except Exception as e:
                note_115_error(str(e))
                raise

    return moved_count, sample_names[:5]


# ---------- 整理刮削专用 115 底层读写与树扫描封装 ----------

def _unwrap_write_resp(resp: dict, action_name: str) -> dict:
    """统一校验 115 写接口响应并在风控出错时记入退避。"""
    if not isinstance(resp, dict) or not resp.get("state"):
        msg = (resp.get("error") or resp.get("msg") or str(resp)[:200]) if isinstance(resp, dict) else str(resp)[:200]
        note_115_error(msg)
        raise Pan115Error(f"{action_name}被 115 拒绝: {msg}")
    note_115_ok()
    return resp


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
    except Pan115Error:
        raise
    except Exception as e:
        note_115_error(str(e))
        raise


def ensure_subdir(base_cid: int, rel_dir: str) -> int:
    """从已知目录 base_cid 出发逐级查找或创建相对子目录，返回末级 cid。"""
    cur = int(base_cid)
    segs = [s.strip() for s in (rel_dir or "").strip("/").split("/") if s.strip()]
    if not segs:
        return cur
    client = _get_client()
    for name in segs:
        children = _list_dir(cur)
        if name in children:
            cur = children[name]
            continue
        resp = pan_call(client.fs_mkdir, {"cname": name, "pid": cur})
        if not resp.get("state"):
            # 并发或同名重试再查一次目录列表
            children = _list_dir(cur)
            if name in children:
                cur = children[name]
                continue
            msg = resp.get("error") or resp.get("msg") or str(resp)[:200]
            note_115_error(msg)
            raise Pan115Error(f"创建子目录「{name}」失败: {msg}")
        data = resp.get("data") or {}
        new_cid = data.get("cid") or data.get("file_id") or data.get("id") or resp.get("cid")
        if new_cid:
            cur = int(str(new_cid))
        else:
            cur = _list_dir(cur).get(name, 0)
            if not cur:
                raise Pan115Error(f"创建子目录「{name}」后未能定位 cid")
        clear_dir_cache()
    return cur


def walk_media_tree(root_cid: int, root_path: str, max_depth: int = 4) -> dict:
    """礼貌遍历 115 指定目录下的文件树（自动将 BDMV 原盘视为整体目录不拆散内部流文件）。

    返回 {"files": [...], "bdmv_dirs": [...], "dirs": [...]}
    """
    norm_root = "/" + root_path.strip("/")
    queue_dirs: list[tuple[int, str, list[str], int]] = [(int(root_cid), norm_root, [], 0)]
    files_out: list[dict] = []
    bdmv_out: list[dict] = []
    dirs_out: list[dict] = []

    while queue_dirs:
        cur_cid, cur_path, rel_parents, depth = queue_dirs.pop(0)
        if depth > max_depth:
            continue
        entries = list_entries(cur_cid, limit=2000)
        dir_names_lower = {e["name"].lower() for e in entries if e.get("is_dir")}
        # BDMV / VIDEO_TS 原盘保护：父目录视作原子整体，不再深入枚举 Stream/*.m2ts
        if depth > 0 and ("bdmv" in dir_names_lower or "certificate" in dir_names_lower or "video_ts" in dir_names_lower):
            bdmv_out.append({
                "id": str(cur_cid),
                "name": rel_parents[-1],
                "path": cur_path,
                "parent_dirs": rel_parents[:-1],
                "parent_cid": "",  # 由外层记录填充
                "is_bdmv": True,
            })
            continue

        sibling_names = [e["name"] for e in entries if not e.get("is_dir")]
        for e in entries:
            if e.get("is_dir"):
                if e["name"].lower() in ("@eadir", ".ds_store", "__macosx", "_待清理旧版"):
                    continue
                sub_path = f"{cur_path.rstrip('/')}/{e['name']}"
                dirs_out.append({
                    "id": str(e["id"]),
                    "name": e["name"],
                    "path": sub_path,
                    "parent_cid": str(cur_cid),
                    "depth": depth + 1,
                })
                if depth + 1 <= max_depth:
                    queue_dirs.append((int(str(e["id"])), sub_path, rel_parents + [e["name"]], depth + 1))
            else:
                files_out.append({
                    "id": str(e["id"]),
                    "name": e["name"],
                    "size": int(e.get("size") or 0),
                    "sha1": e.get("sha1") or "",
                    "parent_cid": str(cur_cid),
                    "dir_path": cur_path,
                    "parent_dirs": list(rel_parents),
                    "sibling_names": sibling_names,
                    "sibling_entries": [x for x in entries if not x.get("is_dir")],
                })
        if queue_dirs:
            time.sleep(0.25 * pace_factor())

    # 补全 bdmv_dirs 的 parent_cid
    dir_parent_map = {d["path"]: d["parent_cid"] for d in dirs_out}
    for b in bdmv_out:
        b["parent_cid"] = dir_parent_map.get(b["path"], "")

    return {"files": files_out, "bdmv_dirs": bdmv_out, "dirs": dirs_out}
