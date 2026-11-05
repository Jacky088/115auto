"""p115client 封装：扫码登录（Web 流程）、目录逐级创建、分享文件遍历、转存、容量查询。

注意：所有网络调用是同步阻塞的，调用方（转存 worker）需放到线程池执行。
"""
import io
import json
import threading
import time

import qrcode
import qrcode.image.svg

from .. import config, database as db
from ..core import logstream

_lock = threading.Lock()
_client = None  # P115Client 单例（懒加载）

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
            raise Pan115Error("尚未登录 115，请先扫码")
        cookie = config.COOKIE_FILE.read_text().strip()
        if not cookie:
            raise Pan115Error("115 cookie 为空，请重新扫码")
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
                "或点「手动导入 Cookie」——在本地电脑浏览器登录 115.com 后复制 Cookie 粘贴进来。",
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


def import_cookie(cookie: str) -> dict:
    """手动导入 cookie（海外 VPS 扫码被风控时的替代方案）：校验有效后保存。"""
    from p115client import P115Client
    cookie = cookie.strip().strip('"').strip("'")
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    if not cookie:
        raise Pan115Error("Cookie 为空")
    if not all(k in cookie for k in ("UID=", "CID=", "SEID=")):
        raise Pan115Error("Cookie 里缺少 UID/CID/SEID 字段：请复制完整的 115 登录 Cookie"
                          "（形如 UID=xxx;CID=xxx;SEID=xxx）")
    try:
        client = P115Client(cookie)
        resp = client.user_my_info()
    except Exception as e:
        raise Pan115Error(f"Cookie 无法使用: {e}")
    if not resp.get("state"):
        raise Pan115Error(f"Cookie 校验失败: {str(resp)[:200]}")
    data = resp.get("data", {}) or {}
    name = data.get("user_name", "") or data.get("uname", "")
    config.COOKIE_FILE.write_text(cookie)
    reset_client()
    db.set_setting("cookie_ok", True)
    logstream.info("115", f"手动导入 cookie 成功（账号: {name}）")
    return {"ok": True, "name": name}


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
        resp = client.fs_files({"cid": pid, "fc_mix": 0, "limit": 1150, "offset": offset,
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


def mkdirs(path: str) -> int:
    """逐级确保目录存在，返回最深层 cid。path 形如 /媒体库/电影。"""
    client = _get_client()
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts:
        return 0
    pid = 0
    for name in parts:
        dirs = _list_dir(pid)
        if name in dirs:
            pid = dirs[name]
        else:
            resp = client.fs_mkdir({"cname": name, "pid": pid})
            if not resp.get("state"):
                raise Pan115Error(f"创建目录 {name} 失败: {str(resp)[:200]}")
            data = resp.get("data", {}) or {}
            new_cid = _to_int(data.get("file_id") or data.get("cid") or resp.get("cid"), 0)
            if not new_cid:
                new_cid = _list_dir(pid).get(name, 0)  # 响应缺 id 时回查上级目录
            pid = new_cid
            logstream.info("115", f"已创建目录 {name} (cid={pid})")
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
        resp = P115Client.share_snap({
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
                break
            time.sleep(0.5)
        time.sleep(0.5)  # 目录之间也留间隔：多目录分享逐页轰炸易触发风控
    return {"title": title, "files": files, "total_size": total_size,
            "top_names": top_names, "top_ids": top_ids}


def receive(share_code: str, receive_code: str, cid: int, file_ids: list[str]) -> dict:
    """转存指定文件到目录 cid。"""
    client = _get_client()
    resp = client.share_receive({
        "share_code": share_code,
        "receive_code": receive_code,
        "file_id": ",".join(file_ids),
        "cid": cid,
        "is_check": 1,
    })
    if not resp.get("state"):
        code = resp.get("error_code", "")
        msg = resp.get("error", "") or str(resp)[:200]
        raise Pan115Error(f"{msg} (error_code={code})")
    return resp


def offline_download(url: str, cid: int) -> dict:
    """提交云下载任务（ED2K/磁力/HTTP），保存到目录 cid。"""
    client = _get_client()
    resp = client.clouddownload_task_add_url({"url": url, "wp_path_id": cid})
    if not resp.get("state"):
        msg = resp.get("error", "") or str(resp)[:200]
        raise Pan115Error(f"云下载任务提交失败: {msg}")
    return resp


def task_list_all(max_pages: int = 5) -> list[dict]:
    """拉取 115 云下载任务列表（含 url/name/percent/status 字段）。

    任一页接口失败都会抛出 Pan115Error（而不是返回不完整/空列表），
    调用方据此跳过本轮判定，避免把「接口异常」误判成「任务不存在 → 下载失败」。
    """
    client = _get_client()
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            resp = client.clouddownload_task_list({"page": page, "page_size": 100})
        except Exception as e:
            raise Pan115Error(f"拉取云下载任务列表失败: {e}")
        if not resp.get("state"):
            msg = resp.get("error", "") or str(resp)[:200]
            raise Pan115Error(f"拉取云下载任务列表失败: {msg}")
        tasks = resp.get("tasks") or []
        out.extend(tasks)
        if len(tasks) < 100:
            break
        time.sleep(0.5)
    return out


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
        resp = client.fs_files({"cid": cid, "limit": min(500, limit - offset),
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
        if not data:
            break
        offset += len(data)
        if offset < limit:
            time.sleep(0.4)  # 多页翻页间隔，降低请求频率
    return names
