"""115 目录路径解析、创建（mkdirs）与缓存机制。"""
from __future__ import annotations

import threading
import time

from ..core import logstream


def _pan():
    """延迟获取 client 模块引用，避免循环导入且保证测试 patch 透明生效。"""
    from . import client
    return client


def _to_int(v, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _list_dir(pid: int = 0) -> dict[str, int]:
    """列出某目录下的子目录 {名字: cid}。"""
    pan = _pan()
    client = pan._get_client()
    dirs: dict[str, int] = {}
    offset = 0
    while True:
        resp = pan.pan_call(client.fs_files, {"cid": pid, "fc_mix": 0, "limit": 1150, "offset": offset,
                                              "o": "user_ptime", "asc": 0, "show_dir": 1})
        if not resp.get("state"):
            raise pan.Pan115Error(f"fs_files 失败: {str(resp)[:200]}")
        data = resp.get("data") or []
        for f in data:
            if not isinstance(f, dict):
                continue
            fid, name = pan._file_id(f), pan._file_name(f)
            if not pan._is_dir_item(f) or not fid or not name:
                continue
            cid = _to_int(fid, -1)
            if cid >= 0:
                dirs[name] = cid
        total = _to_int(resp.get("count"), 0)
        offset += len(data)
        if not data:
            break
        if total and offset >= total:
            break
        if offset > 500_000:  # count 偶发缺失时防止失控翻页的兜底上限
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
    pan = _pan()
    client = pan._get_client()
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
            resp = pan.pan_call(client.fs_mkdir, {"cname": name, "pid": pid})
            if not resp.get("state"):
                raise pan.Pan115Error(f"创建目录 {name} 失败: {str(resp)[:200]}")
            data = resp.get("data", {}) or {}
            new_cid = _to_int(data.get("file_id") or data.get("cid") or resp.get("cid"), 0)
            if not new_cid:
                new_cid = _list_dir(pid).get(name, 0)  # 响应缺 id 时回查上级目录
            pid = new_cid
            logstream.info("115", f"已创建目录 {name} (cid={pid})")
        _dir_cache_put(walked, pid)
        time.sleep(0.5)
    return pid


def ensure_subdir(base_cid: int, rel_dir: str) -> int:
    """从已知目录 base_cid 出发逐级查找或创建相对子目录，返回末级 cid。"""
    cur = int(base_cid)
    segs = [s.strip() for s in (rel_dir or "").strip("/").split("/") if s.strip()]
    if not segs:
        return cur
    pan = _pan()
    client = pan._get_client()
    for name in segs:
        children = _list_dir(cur)
        if name in children:
            cur = children[name]
            continue
        resp = pan.pan_call(client.fs_mkdir, {"cname": name, "pid": cur})
        if not resp.get("state"):
            # 并发或同名重试再查一次目录列表
            children = _list_dir(cur)
            if name in children:
                cur = children[name]
                continue
            msg = resp.get("error") or resp.get("msg") or str(resp)[:200]
            pan.note_115_error(msg)
            raise pan.Pan115Error(f"创建子目录「{name}」失败: {msg}")
        data = resp.get("data") or {}
        new_cid = data.get("cid") or data.get("file_id") or data.get("id") or resp.get("cid")
        if new_cid:
            cur = int(str(new_cid))
        else:
            cur = _list_dir(cur).get(name, 0)
            if not cur:
                raise pan.Pan115Error(f"创建子目录「{name}」后未能定位 cid")
        clear_dir_cache()
    return cur
