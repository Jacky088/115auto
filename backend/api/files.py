"""115 文件浏览器 API：查阅/按需统计目录大小/排序筛选/新建目录/重命名/删除/移动/复制。"""
import html
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from p115pickcode import to_id as _pickcode_to_id

from ..pan115 import client as pan
from .deps import require_user

router = APIRouter(prefix="/api/files")

# 允许的服务端排序字段（严格排除「按目录总大小排序」，115 列表接口本身不支持目录大小）
_SORT_FIELD_MAP = {
    "name": "file_name",
    "mtime": "user_utime",
    "ptime": "user_ptime",
}

# 按需目录统计缓存（fs_category_get）：单目录按需查 + 5 分钟缓存 + 轻量间隔节流，防止连点触发风控
_STAT_TTL = 300.0
_STAT_MAX_ENTRIES = 500
_STAT_MIN_GAP = 0.5
_stat_cache_lock = threading.Lock()
_stat_cache: dict[int, tuple[float, dict]] = {}
_name_cache: dict[int, tuple[float, str]] = {}
_stat_call_lock = threading.Lock()
_last_stat_at = 0.0


def _clear_stat_cache():
    with _stat_cache_lock:
        _stat_cache.clear()
        _name_cache.clear()


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _opt_int(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _numeric_id(f: dict, pick_code: str) -> int:
    """从 pick_code 离线解码数值 id（pick_code 本身编码了文件/目录 id）。

    某些接口形态（如 open 列表）的返回项不带 fid/file_id 数值字段，
    导航（fs_files 的 cid 参数）必须用数值 id，因此在这里解码还原。
    """
    if not pick_code:
        return 0
    try:
        return _pickcode_to_id(pick_code)
    except Exception:
        return 0


def _item(f: dict) -> dict:
    """统一列表项结构（兼容 115 网页接口、open 接口与搜索接口的字段名）。"""
    is_dir = (f.get("fc") in (0, "0") or f.get("fid") in (0, "0")
              or f.get("file_category") in (0, "0"))
    pick_code = f.get("pick_code") or f.get("pc") or ""
    # 网页接口的目录项通常只有 cid（没有 fid/file_id），缺了它前端无法进入子目录
    raw_id = f.get("fid") or f.get("file_id") or (f.get("cid") if is_dir else None)
    return {
        # 数值 id 优先（fid/file_id/cid），缺失时从 pick_code 解码还原，最后才兜底 pick_code 字符串
        "id": str(raw_id or _numeric_id(f, pick_code) or pick_code),
        "name": f.get("file_name") or f.get("fn") or f.get("n") or "",
        "is_dir": is_dir,
        "size": _int(f.get("file_size") or f.get("fs") or f.get("s")),
        "mtime": _int(f.get("update_time") or f.get("user_utime")
                      or f.get("user_ptime") or f.get("t")),
        "ctime": _int(f.get("user_ptime") or f.get("tp") or f.get("create_time")),
        "parent_id": str(f.get("pid") if f.get("pid") is not None
                         else f.get("parent_id") if f.get("parent_id") is not None
                         else f.get("cid") if f.get("cid") is not None else ""),
        "pick_code": f.get("pick_code") or f.get("pc") or "",
        "sha1": f.get("sha1") or f.get("sha") or "",
    }


def _name(p: dict) -> str:
    return p.get("name") or p.get("n") or p.get("file_name") or p.get("fn") or ""


def _parse_items(raw) -> list[dict]:
    """保留 115 服务端排序结果（fc_mix=0 已保证目录置顶），仅做字段归一。"""
    return [_item(f) for f in (raw or []) if isinstance(f, dict)]


def _breadcrumb(resp: dict, cid: int) -> list[dict]:
    """把 115 返回的 path 统一成「根目录 → 当前目录」的面包屑。

    兼容两种返回形态：
    - webapi fs_files: [["目录名", "cid"], ...]（根目录在前，末位通常是当前目录）
    - dict 形态: {"cid"/"file_id"/"fid"/"id", "pid"/"parent_id", "name"/"n"/...}
    id 解析不出来（或错位）的节点一律丢弃，避免前端出现「同名目录重复显示」
    和「点面包屑跳错目录」的问题。
    """
    if cid == 0:
        return [{"id": 0, "name": "根目录"}]

    nodes: list[dict] = []
    for p in (resp.get("path") or []):
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            a, b = str(p[0] or ""), str(p[1] or "")
            # 两列里全数字的是 id、另一个是名称（不同接口顺序可能相反）
            if a.isdigit() and not b.isdigit():
                nodes.append({"id": _int(a), "pid": -1, "name": b})
            elif b.isdigit() and not a.isdigit():
                nodes.append({"id": _int(b), "pid": -1, "name": a})
            elif a.isdigit() and b.isdigit():
                nodes.append({"id": _int(a), "pid": -1, "name": b})
            else:
                continue
        elif isinstance(p, dict):
            if not any(k in p for k in ("cid", "file_id", "fid", "id")):
                continue  # 完全没有 id 字段，解析不出来，直接丢弃
            nodes.append({
                "id": _int(p.get("cid") or p.get("file_id") or p.get("fid")
                           or p.get("id") or 0),
                "pid": _int(p.get("pid") if p.get("pid") is not None else p.get("parent_id"), -1),
                "name": _name(p),
            })
    if not nodes:
        # 接口没给路径信息，交给前端沿用已知的目录名
        return []
    by_id = {str(n["id"]): n for n in nodes}

    # 1) 优先根据 pid 链回溯（仅 dict 形态带 pid），避免依赖接口返回顺序
    chain: list[dict] = []
    cur = str(cid)
    seen: set[str] = set()
    while cur not in seen:
        seen.add(cur)
        n = by_id.get(cur)
        if not n:
            break
        chain.append({"id": n["id"], "name": n["name"] or ("根目录" if n["id"] == 0 else str(n["id"]))})
        pid = n["pid"]
        if n["id"] == 0 or pid is None or pid < 0 or str(pid) == cur:
            break
        cur = str(pid)

    if chain and chain[-1]["id"] == 0 and len(chain) > 1:
        return list(reversed(chain))

    # 2) 按 115 返回顺序兜底（本来就是根目录 → 当前目录）
    ordered = list(nodes)
    if len(ordered) > 1 and ordered[-1]["id"] == 0 and ordered[0]["id"] != 0:
        ordered.reverse()  # 个别形态根目录在末位
    cleaned: list[dict] = []
    for n in ordered:
        if n["id"] == 0 and cleaned:
            continue  # 根目录（id=0）只允许出现在首位
        if cleaned and cleaned[-1]["id"] == n["id"]:
            continue  # 相邻重复节点去重
        cleaned.append({"id": n["id"], "name": n["name"] or ("根目录" if n["id"] == 0 else "")})
    if not cleaned:
        return []
    if cleaned[0]["id"] != 0:
        cleaned.insert(0, {"id": 0, "name": "根目录"})
    # 截断到当前目录；若当前目录不在列表里（或 id 解析失败被丢弃），
    # 原样返回祖先链，由前端用已知的目录名补上当前目录
    index = next((i for i, n in enumerate(cleaned) if str(n["id"]) == str(cid)), None)
    if index is not None:
        cleaned = cleaned[:index + 1]
    return cleaned


def _client():
    # pan.check_login 内部有 60s 缓存，连续翻目录不会重复请求 115
    st = pan.check_login()
    if not st.get("ok"):
        raise HTTPException(400, "115 未登录或 Cookie 失效，请到「账号管理」重新扫码登录或导入 Cookie")
    try:
        return pan._get_client()
    except Exception as e:
        # 截断异常文本：p115client 异常可能内嵌请求参数，完整堆栈只进服务端日志
        raise HTTPException(400, f"115 客户端不可用: {str(e)[:150]}")


def _check(resp, action: str) -> dict:
    if not isinstance(resp, dict):
        raise HTTPException(400, f"{action}失败: 115 返回数据异常")
    if not resp.get("state"):
        msg = resp.get("error") or resp.get("message") or str(resp)[:200]
        raise HTTPException(400, f"{action}失败: {msg}")
    return resp


def _check_category_resp(resp, action: str) -> dict:
    """校验 fs_category_get 响应：115 /category/get 成功时默认不带 state 字段，失败才带 state=false 或 errno。"""
    if not isinstance(resp, dict) or not resp:
        raise HTTPException(400, f"{action}失败: 115 返回数据异常")
    if resp.get("state") is False or resp.get("state") == 0 or _int(resp.get("errno"), 0) != 0:
        msg = resp.get("error") or resp.get("message") or str(resp)[:200]
        raise HTTPException(400, f"{action}失败: {msg}")
    return resp


def _call_impl(action: str, func, serialize: bool, *args, **kwargs) -> dict:
    """调用 115 接口并统一将网络/业务异常转成可读的 400 错误。"""
    try:
        resp = pan.pan_call(func, *args, **kwargs) if serialize else func(*args, **kwargs)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"{action}失败: {str(e)[:150]}")
    return _check(resp, action)


def _call(action: str, func, *args, **kwargs) -> dict:
    """写操作入口：经进程级串行队列门闸执行，避免与后台转存/整理并发撞车。"""
    return _call_impl(action, func, True, *args, **kwargs)


def _call_read(action: str, func, *args, **kwargs) -> dict:
    """只读浏览/搜索入口：直接放行不进后台串行锁，避免网盘文件读取被后台长任务堵死。"""
    return _call_impl(action, func, False, *args, **kwargs)


def _ids(body: dict, max_items: int = 200) -> list[str]:
    raw = body.get("ids")
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "未选择文件")
    ids = []
    for x in raw:
        s = str(x).strip()
        if s and s not in ids:
            ids.append(s)
    if not ids:
        raise HTTPException(400, "未选择文件")
    if len(ids) > max_items:
        raise HTTPException(400, f"单次最多操作 {max_items} 项，请分批操作")
    return ids


def _target(body: dict) -> int:
    raw = body.get("target_cid")
    if raw is None or str(raw).strip() == "":
        raise HTTPException(400, "未指定目标目录")
    target = _int(raw, -1)
    if target < 0:
        raise HTTPException(400, "目标目录不合法")
    return target


@router.get("/list")
def list_files(
    cid: int = Query(0, ge=0),
    search: str = Query("", max_length=200),
    search_scope: str = Query("all"),
    sort: str = Query("name"),
    order: str = Query("asc"),
    file_type: int = Query(0, ge=0, le=7),
    page: int = Query(1, ge=1),
    size: int = Query(100, ge=1, le=500),
    _=Depends(require_user),
):
    """列出目录内容（支持服务端排序、类型筛选与当前目录/全盘搜索）。"""
    client = _client()
    order_col = _SORT_FIELD_MAP.get((sort or "").strip().lower(), "file_name")
    asc_flag = 0 if (order or "").strip().lower() == "desc" else 1
    scope = "dir" if (search_scope or "").strip().lower() == "dir" else "all"

    if search:
        payload = {
            "search_value": search,
            "limit": size,
            "offset": (page - 1) * size,
            "o": order_col,
            "asc": asc_flag,
            "fc_mix": 0,
            "count_folders": 1,
        }
        if scope == "dir" and cid > 0:
            payload["cid"] = cid
        if file_type > 0:
            payload["type"] = file_type
        resp = _call_read("搜索", client.fs_search, payload)
        items = _parse_items(resp.get("data"))
        return {
            "items": items,
            "total": _int(resp.get("count"), len(items)),
            "folder_count": _opt_int(resp.get("folder_count")),
            "file_count": _opt_int(resp.get("file_count")),
            "cid": cid if scope == "dir" else -1,
            "path": [],
        }

    payload = {
        "cid": cid,
        "limit": size,
        "offset": (page - 1) * size,
        "o": order_col,
        "asc": asc_flag,
        "show_dir": 1,
        "fc_mix": 0,
        "custom_order": 1,
        "count_folders": 1,
    }
    if file_type > 0:
        # 必须显式 cur=1：否则 115 在指定 type>0 时默认递归遍历整个目录树
        payload["type"] = file_type
        payload["cur"] = 1
    resp = _call_read("读取目录", client.fs_files, payload)
    items = _parse_items(resp.get("data"))
    return {
        "items": items,
        "total": _int(resp.get("count"), len(items)),
        "folder_count": _opt_int(resp.get("folder_count")),
        "file_count": _opt_int(resp.get("file_count")),
        "cid": cid,
        "path": _breadcrumb(resp, cid),
    }


@router.get("/stat")
def stat_dir(
    cid: int = Query(..., gt=0),
    refresh: bool = Query(False),
    _=Depends(require_user),
):
    """按需统计单个目录的总大小、文件数与子目录数（带 5 分钟缓存与轻量间隔节流）。"""
    global _last_stat_at
    now = time.monotonic()
    if not refresh:
        with _stat_cache_lock:
            hit = _stat_cache.get(cid)
            if hit and hit[0] > now:
                return {**hit[1], "cached": True}

    client = _client()
    with _stat_call_lock:
        # 复查缓存：防止同目录并发点击重复打 115
        now = time.monotonic()
        if not refresh:
            with _stat_cache_lock:
                hit = _stat_cache.get(cid)
                if hit and hit[0] > now:
                    return {**hit[1], "cached": True}
        wait = (_last_stat_at + _STAT_MIN_GAP) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            resp = client.fs_category_get({"cid": cid})
        except Exception as e:
            raise HTTPException(400, f"统计目录失败: {str(e)[:150]}")
        finally:
            _last_stat_at = time.monotonic()

    checked = _check_category_resp(resp, "统计目录")
    info = checked.get("data") if isinstance(checked.get("data"), dict) else checked
    size_bytes = _int(info.get("size_byte") if info.get("size_byte") is not None else info.get("size"), 0)
    raw_size_text = str(info.get("size") or "").strip()

    # 从 paths 提取上级目录名与完整路径（搜索模式下可零额外请求补全上级名称与路径）
    parent_name = ""
    full_path = ""
    raw_paths = info.get("paths")
    if isinstance(raw_paths, list) and raw_paths:
        segs: list[str] = []
        last_id = 0
        last_name = ""
        for p in raw_paths:
            if not isinstance(p, dict):
                continue
            pid = _int(p.get("file_id") if p.get("file_id") is not None else p.get("cid"), 0)
            pname = html.unescape(str(p.get("file_name") or p.get("name") or "")).strip()
            last_id, last_name = pid, pname
            if pid != 0 and pname:
                segs.append(pname)
        parent_name = "根目录" if last_id == 0 else last_name
        item_name = html.unescape(str(info.get("file_name") or "")).strip()
        if item_name:
            full_path = "/" + "/".join([*segs, item_name])

    result = {
        "cid": str(cid),
        "size": size_bytes,
        "size_text": raw_size_text if not raw_size_text.isdigit() else "",
        "file_count": _int(info.get("count"), 0),
        "folder_count": _int(info.get("folder_count"), 0),
        "parent_name": parent_name,
        "full_path": full_path,
    }
    with _stat_cache_lock:
        if len(_stat_cache) >= _STAT_MAX_ENTRIES:
            oldest_cid = min(_stat_cache, key=lambda k: _stat_cache[k][0])
            _stat_cache.pop(oldest_cid, None)
        _stat_cache[cid] = (time.monotonic() + _STAT_TTL, result)
    return {**result, "cached": False}


@router.get("/name")
def get_item_name(
    cid: int = Query(..., ge=0),
    _=Depends(require_user),
):
    """按 ID 轻量查询单个目录/文件名称（走 fs_file_skim，不统计目录大小，带 5 分钟缓存）。"""
    if cid == 0:
        return {"cid": "0", "name": "根目录", "cached": True}
    now = time.monotonic()
    with _stat_cache_lock:
        hit = _name_cache.get(cid)
        if hit and hit[0] > now:
            return {"cid": str(cid), "name": hit[1], "cached": True}

    client = _client()
    resp = _call_read("查询名称", client.fs_file_skim, cid)
    rows = resp.get("data")
    name = ""
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        name = html.unescape(str(rows[0].get("file_name") or rows[0].get("fn") or rows[0].get("n") or "")).strip()
    elif isinstance(rows, dict):
        name = html.unescape(str(rows.get("file_name") or rows.get("fn") or rows.get("n") or "")).strip()
    with _stat_cache_lock:
        if len(_name_cache) >= _STAT_MAX_ENTRIES:
            oldest_cid = min(_name_cache, key=lambda k: _name_cache[k][0])
            _name_cache.pop(oldest_cid, None)
        if name:
            _name_cache[cid] = (time.monotonic() + _STAT_TTL, name)
    return {"cid": str(cid), "name": name, "cached": False}


@router.post("/mkdir")
def mkdir(body: dict, _=Depends(require_user)):
    """新建目录。body: {pid, name}"""
    client = _client()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "目录名不能为空")
    if len(name) > 255:
        raise HTTPException(400, "目录名过长")
    resp = _call("新建目录", client.fs_mkdir,
                 {"cname": name, "pid": _int(body.get("pid"), 0)})
    pan.clear_dir_cache()
    _clear_stat_cache()
    data = resp.get("data", {}) or {}
    cid = data.get("file_id") or data.get("cid") or data.get("fid") or resp.get("cid")
    return {"ok": True, "cid": cid, "name": name}


@router.post("/rename")
def rename(body: dict, _=Depends(require_user)):
    """重命名。body: {id, name}"""
    client = _client()
    fid = str(body.get("id") or "").strip()
    name = (body.get("name") or "").strip()
    if not fid or not name:
        raise HTTPException(400, "参数不完整")
    if len(name) > 255:
        raise HTTPException(400, "文件名过长")
    _call("重命名", client.fs_rename, (fid, name))
    pan.clear_dir_cache()
    _clear_stat_cache()
    return {"ok": True}


@router.post("/delete")
def delete(body: dict, _=Depends(require_user)):
    """删除（进回收站）。body: {ids: [...]}"""
    client = _client()
    ids = _ids(body)
    _call("删除", client.fs_delete, ids)
    pan.clear_dir_cache()
    _clear_stat_cache()
    return {"ok": True, "count": len(ids)}


@router.post("/move")
def move(body: dict, _=Depends(require_user)):
    """移动到目录。body: {ids: [...], target_cid}"""
    client = _client()
    ids, target = _ids(body), _target(body)
    if str(target) in ids:
        raise HTTPException(400, "不能将目录移动到其自身内部")
    _call("移动", client.fs_move, ids, pid=target)
    pan.clear_dir_cache()
    _clear_stat_cache()
    return {"ok": True, "count": len(ids)}


@router.post("/copy")
def copy_files(body: dict, _=Depends(require_user)):
    """复制到目录。body: {ids: [...], target_cid}"""
    client = _client()
    ids, target = _ids(body), _target(body)
    if str(target) in ids:
        raise HTTPException(400, "不能将目录复制到其自身内部")
    _call("复制", client.fs_copy, ids, pid=target)
    _clear_stat_cache()
    return {"ok": True, "count": len(ids)}


def _rb_item(it: dict) -> dict:
    """统一回收站条目结构。"""
    is_dir = (str(it.get("type") or "") == "2"
              or str(it.get("ico") or "").lower() == "folder"
              or it.get("fc") in (0, "0"))
    return {
        "id": str(it.get("id") or it.get("rid") or ""),
        "name": html.unescape(str(it.get("file_name") or it.get("fn") or it.get("n") or "")).strip(),
        "is_dir": is_dir,
        "size": _int(it.get("file_size") or it.get("fs") or it.get("s"), 0),
        "dtime": _int(it.get("dtime") or it.get("delete_time") or it.get("t"), 0),
        "parent_name": html.unescape(str(it.get("parent_name") or "")).strip(),
        "parent_id": str(it.get("cid") if it.get("cid") is not None else ""),
    }


@router.get("/recyclebin")
def list_recyclebin(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    _=Depends(require_user),
):
    """分页查看 115 回收站（按删除时间倒序，只读不触发清空）。"""
    client = _client()
    resp = _call_read(
        "读取回收站",
        client.recyclebin_list,
        {"limit": size, "offset": (page - 1) * size, "o": "dtime", "asc": 0},
    )
    raw = resp.get("data")
    if isinstance(raw, dict):
        rows = list(raw.values())
    elif isinstance(raw, list):
        rows = raw
    else:
        rows = []
    items = [_rb_item(x) for x in rows if isinstance(x, dict)]
    items = [x for x in items if x["id"]]
    return {"items": items, "total": _int(resp.get("count"), len(items))}


@router.post("/recyclebin/revert")
def revert_recyclebin(body: dict, _=Depends(require_user)):
    """从 115 回收站还原选中的条目回原目录。body: {ids: [...]}"""
    client = _client()
    ids = _ids(body, max_items=100)
    _call("还原文件", client.recyclebin_revert, ids)
    pan.clear_dir_cache()
    _clear_stat_cache()
    return {"ok": True, "count": len(ids)}
