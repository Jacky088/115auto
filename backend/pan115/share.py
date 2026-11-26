"""115 分享链接遍历、文件快照解析与批量转存模块。"""
from __future__ import annotations

import time

from ..core import logstream


def _pan():
    """延迟获取 client 模块引用，避免循环依赖且确保单测 patch 生效。"""
    from . import client
    return client


def share_snap_all(share_code: str, receive_code: str, max_files: int = 5000) -> dict:
    """遍历分享的全部文件。返回 {title, files: [{fid, fn, fs}], total_size, top_names}。

    top_names 记录分享顶层（cid=0）的条目名：115 转存后若把分享整体以文件夹形式
    落到目标目录，顶层文件夹名只存在于这里，反查确认需要它。
    """
    from p115client import P115Client
    pan = _pan()
    title = ""
    files: list[dict] = []
    total_size = 0
    top_names: list[str] = []
    top_ids: list[str] = []  # 分享顶层条目 id（目录为 cid）：整包转存用，保留目录结构

    def snap(cid: int, offset: int):
        # 优先使用已登录客户端实例（带 Cookie 且经过 _get_client 30s 超时封装）；
        # 若尚未配置 Cookie（如匿名分享预检或部分无 Cookie 单元测试），回落静态方法并显式传 timeout=30 防挂死
        try:
            cli = pan._get_client()
            fn = cli.share_snap
        except Exception:
            fn = P115Client.share_snap
        resp = pan.pan_call(
            fn,
            {
                "share_code": share_code, "receive_code": receive_code,
                "cid": cid, "limit": 1150, "offset": offset,
            },
            timeout=30,
        )
        if not resp.get("state"):
            raise pan.Pan115Error(f"读取分享失败: {str(resp)[:300]}")
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
                fid = pan._file_id(f)
                if not fid:  # 防御：缺 id 的条目无法转存，跳过而不是带着 None 提交
                    continue
                name = pan._file_name(f)
                if cid == 0 and name and name not in top_names:
                    top_names.append(name)  # 分享顶层条目名（文件或文件夹）
                if cid == 0 and str(fid) not in top_ids:
                    top_ids.append(str(fid))  # 顶层条目 id（文件夹为 cid）
                if pan._is_dir_item(f):  # 子目录，继续遍历
                    child = pan._to_int(fid, -1)
                    if child >= 0:
                        stack.append(child)
                    continue
                if str(fid) in seen_files:
                    continue
                seen_files.add(str(fid))
                size = pan._to_int(f.get("fs") or f.get("file_size") or f.get("s"), 0)
                files.append({"fid": str(fid), "fn": name, "fs": size})
                total_size += size
            offset += len(items)  # 按实际返回条数翻页：固定 1150 在服务端少返回时会漏项
            if not items or len(files) >= max_files:
                # total 用 _to_int 容错：接口偶发返回字符串/异常值时不能中断整个遍历
                if len(files) >= max_files and (pan._to_int(total, 0) > max_files or stack):
                    truncated = True  # 超大分享达到遍历上限，超出部分不会转存
                break
            if total and offset >= pan._to_int(total, 0):
                # count 偶发缺失（total=0）时以空页作为唯一退出条件，避免大分享漏扫
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
    单次请求按 500 个 file_id 分批串行提交，避免超长分享整单被 115 拒收。
    """
    pan = _pan()
    client = pan._get_client()
    ids = [str(i) for i in (file_ids or []) if i]
    if not ids:
        ids = ["0"]  # 整包转存语义：115 约定 file_id=0 表示全部
    resp: dict = {}
    batch_size = 500
    total_batches = (len(ids) + batch_size - 1) // batch_size
    try:
        user_id = client.user_id  # 与 115 网页请求一致；取不到时省略
    except Exception:
        user_id = None
    for bi in range(0, len(ids), batch_size):
        chunk = ids[bi:bi + batch_size]
        payload = {
            "share_code": share_code,
            "receive_code": receive_code,
            "file_id": ",".join(chunk),
            "cid": cid,
        }
        if user_id:
            payload["user_id"] = user_id
        if total_batches > 1:
            logstream.info("115", f"分享 {share_code} 文件较多，分批转存 {bi // batch_size + 1}/{total_batches} 批")
        resp = pan.pan_call(client.share_receive, payload)
        if not resp.get("state"):
            code = str(resp.get("error_code") or "").strip()
            msg = resp.get("error", "") or str(resp)[:200]
            if "目录" in msg or "cid" in msg.lower():
                pan.clear_dir_cache()  # 目标目录可能已被删除/重命名：缓存失效，下次重新逐级解析
            raise pan.Pan115Error(f"{msg}（error_code={code}）" if code else msg)
        if bi + batch_size < len(ids):
            time.sleep(0.5 * pan.pace_factor())  # 分批之间留间隔，防风控
    return resp
