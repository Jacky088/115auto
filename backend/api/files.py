"""115 文件浏览器 API：查阅/新建目录/重命名/删除/移动/复制（基于 p115client 官方自用接口）。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from p115pickcode import to_id as _pickcode_to_id

from ..pan115 import client as pan
from .deps import require_user

router = APIRouter(prefix="/api/files")


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


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
        "parent_id": str(f.get("pid") if f.get("pid") is not None
                         else f.get("parent_id") if f.get("parent_id") is not None
                         else f.get("cid") if f.get("cid") is not None else ""),
        "pick_code": f.get("pick_code") or f.get("pc") or "",
        "sha1": f.get("sha1") or f.get("sha") or "",
    }


def _name(p: dict) -> str:
    return p.get("name") or p.get("n") or p.get("file_name") or p.get("fn") or ""


def _sorted_items(raw) -> list[dict]:
    """目录优先、同类按名称排序，保证前端展示稳定。"""
    items = [_item(f) for f in (raw or []) if isinstance(f, dict)]
    items.sort(key=lambda x: (not x["is_dir"], (x["name"] or "").lower()))
    return items


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
        raise HTTPException(400, "115 未登录或 Cookie 失效，请先到「账号管理」扫码登录")
    try:
        return pan._get_client()
    except Exception as e:
        raise HTTPException(400, f"115 客户端不可用: {e}")


def _check(resp, action: str) -> dict:
    if not isinstance(resp, dict):
        raise HTTPException(400, f"{action}失败: 115 返回数据异常")
    if not resp.get("state"):
        msg = resp.get("error") or resp.get("message") or str(resp)[:200]
        raise HTTPException(400, f"{action}失败: {msg}")
    return resp


def _call(action: str, func, *args, **kwargs) -> dict:
    """调用 115 接口，把网络异常/未登录异常统一转成可读的 400 错误。"""
    try:
        resp = func(*args, **kwargs)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"{action}失败: {e}")
    return _check(resp, action)


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
        raise HTTPException(400, f"单次最多操作 {max_items} 项，请分批处理")
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
def list_files(cid: int = Query(0, ge=0), search: str = Query("", max_length=200),
               page: int = Query(1, ge=1), size: int = Query(100, ge=1, le=500),
               _=Depends(require_user)):
    """列出目录内容（或全局搜索）。"""
    client = _client()
    if search:
        resp = _call("搜索", client.fs_search,
                     {"search_value": search, "limit": size, "offset": (page - 1) * size})
        items = _sorted_items(resp.get("data"))
        return {"items": items, "total": _int(resp.get("count"), len(items)), "cid": -1, "path": []}

    resp = _call("读取目录", client.fs_files,
                 {"cid": cid, "limit": size, "offset": (page - 1) * size,
                  "o": "file_name", "asc": 1, "show_dir": 1,
                  "fc_mix": 0, "custom_order": 1})
    items = _sorted_items(resp.get("data"))
    return {"items": items, "total": _int(resp.get("count"), len(items)),
            "cid": cid, "path": _breadcrumb(resp, cid)}


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
    return {"ok": True}


@router.post("/delete")
def delete(body: dict, _=Depends(require_user)):
    """删除（进回收站）。body: {ids: [...]}"""
    client = _client()
    ids = _ids(body)
    _call("删除", client.fs_delete, ids)
    return {"ok": True, "count": len(ids)}


@router.post("/move")
def move(body: dict, _=Depends(require_user)):
    """移动到目录。body: {ids: [...], target_cid}"""
    client = _client()
    ids, target = _ids(body), _target(body)
    if str(target) in ids:
        raise HTTPException(400, "不能移动目录到自身内部")
    _call("移动", client.fs_move, ids, pid=target)
    return {"ok": True, "count": len(ids)}


@router.post("/copy")
def copy_files(body: dict, _=Depends(require_user)):
    """复制到目录。body: {ids: [...], target_cid}"""
    client = _client()
    ids, target = _ids(body), _target(body)
    if str(target) in ids:
        raise HTTPException(400, "不能复制目录到自身内部")
    _call("复制", client.fs_copy, ids, pid=target)
    return {"ok": True, "count": len(ids)}
