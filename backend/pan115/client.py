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
_LOGIN_OK_TTL = 60      # 登录有效：60 秒内复用
_LOGIN_BAD_TTL = 30     # 登录失效：30 秒内复用（便于恢复后尽快识别）
_SPACE_TTL = 300        # 容量信息：5 分钟


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
    except Exception:
        return {"status": 0}  # 网络抖动视为继续等待
    data = resp.get("data") or {}
    status = data.get("status", 0)
    return {"status": status}


def qrcode_finish(uid: str, app: str) -> dict:
    """扫码确认成功后取 cookie 并保存。"""
    from p115client import P115Client
    resp = P115Client.login_qrcode_scan_result(uid, app)
    cookie = resp.get("data", {}).get("cookie", "")
    if not cookie:
        raise Pan115Error(f"获取 cookie 失败: {json.dumps(resp, ensure_ascii=False)[:300]}")
    config.COOKIE_FILE.write_text(cookie)
    reset_client()
    db.set_setting("cookie_ok", True)
    logstream.info("115", "扫码登录成功")
    return {"ok": True}


# ---------- 账号 ----------

def space_info(force: bool = False) -> dict:
    """容量信息 {total, use, free, ratio}（字节）。默认复用 5 分钟缓存。"""
    global _space_cache
    now = time.time()
    if not force:
        with _cache_lock:
            cached = _space_cache
        if cached and now < cached[0]:
            return cached[1]
    client = _get_client()
    resp = client.user_space_info()
    if not resp.get("state"):
        raise Pan115Error(str(resp)[:200])
    data = resp.get("data", {}) or {}
    total = int(data.get("total_size", 0))
    use = int(data.get("use_size", 0))
    free = int(data.get("free_size", max(total - use, 0)))
    ratio = round(use / total * 100, 1) if total else 0
    result = {"total": total, "use": use, "free": free, "ratio": ratio}
    with _cache_lock:
        _space_cache = (now + _SPACE_TTL, result)
    return result


def check_login(force: bool = False) -> dict:
    """校验 cookie，返回 {ok, name, err}。成功缓存 60s、失败缓存 30s（force 跳过缓存）。"""
    global _login_cache
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
        _login_cache = (now + ttl, result)
    return result


# ---------- 文件系统 ----------

def _list_dir(pid: int = 0) -> dict[str, int]:
    """列出某目录下的子目录 {名字: cid}。"""
    client = _get_client()
    dirs: dict[str, int] = {}
    offset = 0
    while True:
        resp = client.fs_files({"cid": pid, "fc_mix": 0, "limit": 1150, "offset": offset, "o": "user_ptime", "asc": 0, "show_dir": 1})
        if not resp.get("state"):
            raise Pan115Error(f"fs_files 失败: {str(resp)[:200]}")
        for f in resp.get("data", []):
            if str(f.get("fc")) == "0":  # 目录
                dirs[f["file_name"]] = int(f["fid"])
        total = int(resp.get("count", 0))
        offset += 1150
        if offset >= total or not resp.get("data"):
            break
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
            new_cid = int(data.get("file_id") or data.get("cid") or resp.get("cid") or 0)
            if not new_cid:
                new_cid = _list_dir(pid).get(name, 0)  # 响应缺 id 时回查上级目录
            pid = new_cid
            logstream.info("115", f"已创建目录 {name} (cid={pid})")
        time.sleep(0.5)
    return pid


# ---------- 分享 ----------

def share_snap_all(share_code: str, receive_code: str, max_files: int = 5000) -> dict:
    """遍历分享的全部文件。返回 {title, files: [{fid, fn, fs}], total_size}。"""
    from p115client import P115Client
    title = ""
    files: list[dict] = []
    total_size = 0

    def snap(cid: int, offset: int):
        resp = P115Client.share_snap({
            "share_code": share_code, "receive_code": receive_code,
            "cid": cid, "limit": 1150, "offset": offset,
        })
        if not resp.get("state"):
            raise Pan115Error(f"读取分享失败: {str(resp)[:300]}")
        return resp

    stack = [0]
    seen_dirs: set[int] = set()
    while stack and len(files) < max_files:
        cid = stack.pop(0)
        if cid in seen_dirs:
            continue
        seen_dirs.add(cid)
        offset = 0
        while True:
            resp = snap(cid, offset)
            data = resp.get("data", [])
            if not title:
                title = resp.get("shareinfo", {}).get("share_title", "") or resp.get("title", "") or ""
            for f in data:
                fid = f.get("fid") or f.get("file_id")
                if str(f.get("fc")) == "0":  # 子目录，继续遍历
                    stack.append(int(fid))
                else:
                    size = int(f.get("fs", 0) or 0)
                    files.append({"fid": str(fid), "fn": f.get("fn") or f.get("file_name") or "", "fs": size})
                    total_size += size
            total = int(resp.get("count", 0) or 0)
            offset += 1150
            if offset >= total or not data or len(files) >= max_files:
                break
            time.sleep(0.5)
    return {"title": title, "files": files, "total_size": total_size}


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


def list_file_names(cid: int, limit: int = 200) -> set[str]:
    """列出目录内的文件名集合（用于转存后反查确认）。"""
    client = _get_client()
    names: set[str] = set()
    offset = 0
    while offset < limit:
        resp = client.fs_files({"cid": cid, "limit": min(200, limit - offset),
                                "offset": offset, "o": "user_ptime", "asc": 0})
        if not resp.get("state"):
            break
        data = resp.get("data") or []
        for f in data:
            names.add(f.get("file_name") or f.get("fn") or "")
        if not data:
            break
        offset += len(data)
    return {n for n in names if n}
