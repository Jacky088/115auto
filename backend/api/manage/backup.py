"""数据快照备份恢复与整机迁移 ZIP 包流式导出/导入路由。"""
import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ... import auth, database as db
from ...core import logstream
from ..deps import require_user
from ._common import _client_ip, _set_auth_cookie, _set_poster_cookie, _web_username

router = APIRouter()


# ---------- 备份 ----------
def _format_backup_stats_text(stats: dict) -> str:
    return (
        f"设置 {stats.get('settings', 0)} 项、频道 {stats.get('channels', 0)} 个、"
        f"规则 {stats.get('rules', 0)} 条、路由 {stats.get('routes', 0)} 条、"
        f"黑名单 {stats.get('blacklist', 0)} 条、订阅 {stats.get('subscriptions', 0)} 条、"
        f"转存记录 {stats.get('transfers', 0)} 条、自动整理 {stats.get('auto_move_rules', 0)} 条、"
        f"整理刮削 {stats.get('media_rules', 0)} 条、操作账本 {stats.get('organized_records', 0)} 条、"
        f"待刮削任务 {stats.get('media_tasks', 0)} 条"
    )


async def _read_backup_request(request: Request) -> dict:
    """从请求体解析备份（同时兼容 JSON 与 .json.gz / .zip 原始字节上传）。"""
    from urllib.parse import unquote

    raw = await request.body()
    fname = unquote(request.headers.get("x-backup-filename", "") or "")
    try:
        return db.parse_backup_bytes(raw, filename=fname)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/backup/preview")
@router.post("/settings/backup/preview")
async def backup_preview(request: Request, _=Depends(require_user)):
    """只读校验并预览备份文件（支持 .json / .json.gz / .zip），用于网页恢复确认弹窗。"""
    data = await _read_backup_request(request)
    return {"ok": True, "summary": db.backup_counts(data)}


@router.post("/backup/restore")
@router.post("/settings/backup/restore")
async def backup_restore(request: Request, _=Depends(require_user)):
    """从备份数据恢复业务数据（整体覆盖）。

    同时支持直接 POST 备份 JSON 以及原始 .json.gz / .zip 字节流；恢复在事务内完成，
    失败自动回滚；恢复后系统回到停止状态，需手动点「开始运行」。
    """
    data = await _read_backup_request(request)
    try:
        stats = db.restore_payload(data)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    logstream.info("system", f"已从备份恢复数据：{_format_backup_stats_text(stats)}")
    return {"ok": True, "stats": stats}


@router.get("/backup/export")
def backup_export(_=Depends(require_user)):
    """导出全部业务数据为 JSON 备份文件，与 TG 定时自动备份共用 db.backup_payload。"""
    import json
    from fastapi.responses import Response
    payload = db.backup_payload()
    content = json.dumps(payload, ensure_ascii=False, indent=1)
    fname = f"autosave_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
    logstream.info("system", "已导出数据备份（JSON）")
    return Response(
        content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/backup/import")
async def backup_import(_=Depends(require_user)):
    """备份导入为预留接口：网页与 TG Bot 均支持 JSON / GZ / ZIP 恢复。"""
    raise HTTPException(400, "网页「系统设置」与 TG Bot 均支持 .json / .json.gz / .zip 恢复，"
                             "请在网页上传备份文件，或直接把备份文件发送给 Bot")


# ---------- 本地备份管理 (data/backups/) ----------
def _local_backup_dir():
    from pathlib import Path
    from ... import config
    d = Path(config.DATA_DIR) / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


def _resolve_safe_local_backup(filename: str):
    from pathlib import Path
    base_name = Path(filename).name
    if not base_name or base_name != filename or base_name.startswith("."):
        raise HTTPException(400, "非法备份文件名")
    target = (_local_backup_dir() / base_name).resolve()
    if target.parent != _local_backup_dir() or not target.is_file():
        raise HTTPException(404, "备份文件不存在")
    return target


@router.get("/backup/local/list")
async def backup_local_list(_=Depends(require_user)):
    """获取本地 data/backups/ 目录下的历史备份列表。"""
    def _scan():
        d = _local_backup_dir()
        items = []
        for p in d.iterdir():
            if not p.is_file() or p.name.startswith("."):
                continue
            name_lower = p.name.lower()
            if not (name_lower.endswith(".json") or name_lower.endswith(".gz") or name_lower.endswith(".zip")):
                continue
            stat = p.stat()
            items.append({
                "name": p.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "mtime_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            })
        items.sort(key=lambda x: x["mtime"], reverse=True)
        return items

    return await asyncio.to_thread(_scan)


@router.post("/backup/local/preview")
async def backup_local_preview(
    filename: str = Query(..., description="本地备份文件名"),
    _=Depends(require_user),
):
    """只读校验并预览指定的本地备份文件。"""
    p = _resolve_safe_local_backup(filename)
    raw = await asyncio.to_thread(p.read_bytes)
    try:
        data = db.parse_backup_bytes(raw, filename=p.name)
        return {"ok": True, "filename": p.name, "summary": db.backup_counts(data)}
    except ValueError as e:
        if p.name.lower().endswith(".zip"):
            try:
                from ...core import migration_bundle
                info = await asyncio.to_thread(migration_bundle.inspect_migration_bundle, p)
                return {"ok": True, "filename": p.name, "summary": info.get("counts", {})}
            except Exception:
                pass
        raise HTTPException(400, f"解析备份文件失败：{e}") from e


@router.post("/backup/local/restore")
async def backup_local_restore(
    filename: str = Query(..., description="本地备份文件名"),
    _=Depends(require_user),
):
    """从本地备份文件恢复业务数据（整体覆盖）。"""
    try:
        db.ensure_restore_idle_guard()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    p = _resolve_safe_local_backup(filename)
    raw = await asyncio.to_thread(p.read_bytes)
    try:
        data = db.parse_backup_bytes(raw, filename=p.name)
        stats = db.restore_payload(data)
    except ValueError as e:
        if p.name.lower().endswith(".zip"):
            from ...core import migration_bundle
            res = await migration_bundle.apply_migration_bundle(p, restore_credentials=False, restore_meta=True)
            stats = res.get("counts", {})
        else:
            raise HTTPException(400, str(e)) from e

    logstream.info("system", f"已从本地备份恢复数据（{p.name}）：{_format_backup_stats_text(stats)}")
    return {"ok": True, "stats": stats}


@router.delete("/backup/local")
async def backup_local_delete(
    filename: str = Query(..., description="本地备份文件名"),
    _=Depends(require_user),
):
    """删除指定的本地历史备份文件。"""
    p = _resolve_safe_local_backup(filename)
    try:
        p.unlink()
        logstream.info("system", f"已删除本地备份文件：{p.name}")
        return {"ok": True, "filename": p.name}
    except Exception as e:
        raise HTTPException(500, f"删除备份文件失败：{e}") from e


@router.get("/backup/local/download")
async def backup_local_download(
    filename: str = Query(..., description="本地备份文件名"),
    _=Depends(require_user),
):
    """下载指定的本地备份文件。"""
    from fastapi.responses import FileResponse
    p = _resolve_safe_local_backup(filename)
    return FileResponse(
        path=p,
        filename=p.name,
        media_type="application/octet-stream",
    )


# ---------- 全量迁移包（网页端流式打包导出 / 恢复） ----------
async def _stream_bundle_to_temp(request: Request):
    """将大体积 ZIP 迁移包按块直接落盘到 DATA_DIR/.tmp_backup，避免整包读入内存。"""
    import shutil
    import tempfile
    from pathlib import Path
    from ...core import migration_bundle

    work_dir = Path(tempfile.mkdtemp(dir=migration_bundle.tmp_backup_root(), prefix="bundle_imp_"))
    zip_path = work_dir / "upload.zip"
    total = 0
    try:
        with open(zip_path, "wb") as f:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > migration_bundle.MAX_BUNDLE_UPLOAD_BYTES:
                    raise HTTPException(413, "迁移包体积超过 2 GB 上限")
                f.write(chunk)
        if total == 0:
            raise HTTPException(400, "上传的迁移包文件为空")
        return work_dir, zip_path
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


@router.get("/backup/bundle/estimate")
@router.get("/settings/backup/bundle/estimate")
async def backup_bundle_estimate(_=Depends(require_user)):
    """统计数据库、凭据文件与本地元数据体积，用于导出迁移包前参考。"""
    from ...core import migration_bundle
    return await asyncio.to_thread(migration_bundle.estimate_bundle)


@router.post("/backup/bundle/export")
@router.post("/settings/backup/bundle/export")
async def backup_bundle_export(body: dict, request: Request, _=Depends(require_user)):
    """流式导出整机迁移 ZIP 包（可选是否含凭据、本地元数据范围及是否精简日志/缓存）。"""
    import shutil
    from fastapi.responses import StreamingResponse
    from starlette.background import BackgroundTask
    from ...core import migration_bundle

    include_creds = bool(body.get("include_credentials", False))
    meta_mode = str(body.get("meta_mode") or "nfo").strip().lower()
    slim_db = bool(body.get("slim_db", True))

    if include_creds:
        ip = _client_ip(request)
        if auth.login_throttled(ip):
            raise HTTPException(429, "密码错误次数过多，请 1 分钟后再试", headers={"Retry-After": "60"})
        pwd = str(body.get("password") or "")
        stored_hash = str(db.get_setting("web_password_hash", "") or "")
        if not pwd or not auth.verify_password(pwd, stored_hash):
            auth.record_login_fail(ip)
            raise HTTPException(400, "导出含凭据与会话的迁移包前，必须输入正确的当前登录密码")

    try:
        work_dir, zip_path, fname, manifest = await asyncio.to_thread(
            migration_bundle.build_migration_bundle,
            include_creds,
            meta_mode,
            slim_db,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    def _iter_file():
        with open(zip_path, "rb") as fh:
            while True:
                chunk = fh.read(64 * 1024)
                if not chunk:
                    break
                yield chunk

    logstream.info(
        "system",
        f"已导出全量迁移包（凭据={'是' if include_creds else '否'}，元数据={meta_mode}，瘦身={'是' if slim_db else '否'}，"
        f".nfo={manifest['meta_nfo_count']}，图片/杂项={manifest['meta_asset_count']}）",
    )
    return StreamingResponse(
        _iter_file(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Content-Length": str(zip_path.stat().st_size),
        },
        background=BackgroundTask(shutil.rmtree, work_dir, ignore_errors=True),
    )


@router.post("/backup/bundle/preview")
@router.post("/settings/backup/bundle/preview")
async def backup_bundle_preview(request: Request, _=Depends(require_user)):
    """流式接收并预览 ZIP 迁移包内容（只读，不修改系统数据）。"""
    import shutil
    from ...core import migration_bundle

    async with _BUNDLE_UPLOAD_SEM:
        work_dir, zip_path = await _stream_bundle_to_temp(request)
    try:
        info = await asyncio.to_thread(migration_bundle.inspect_migration_bundle, zip_path, work_dir / "stage")
        info.pop("staged_db", None)
        info.pop("manifest", None)
        return {"ok": True, "preview": info}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# 迁移包上传/落盘并发上限：单包最大 2GB，小盘 NAS 并发接收多包可写满磁盘影响 SQLite 落盘
_BUNDLE_UPLOAD_SEM = asyncio.Semaphore(2)


@router.post("/backup/bundle/restore")
@router.post("/settings/backup/bundle/restore")
async def backup_bundle_restore(
    request: Request,
    response: Response,
    restore_credentials: bool = False,
    restore_meta: bool = True,
    _=Depends(require_user),
):
    """流式接收并从 ZIP 迁移包恢复整机状态（系统需处于停止状态）。

    restore_credentials 默认 False：与 JSON 备份恢复的口径一致（备份不应携带可利用凭据）。
    恶意迁移包若默认采纳包内 web_password_hash/secret.key/115 Cookie，导入即被静默接管；
    确属整机迁移的场景由前端显式传 true（网页恢复向导默认不传）。"""
    import shutil
    from ...core import migration_bundle

    try:
        db.ensure_restore_idle_guard()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    async with _BUNDLE_UPLOAD_SEM:
        work_dir, zip_path = await _stream_bundle_to_temp(request)
    try:
        res = await migration_bundle.apply_migration_bundle(
            zip_path,
            restore_credentials=restore_credentials,
            restore_meta=restore_meta,
        )
        out = {"ok": True, **res}
        if res.get("restored_credentials"):
            new_token = auth.make_token(_web_username())
            _set_poster_cookie(response, request, new_token)
            _set_auth_cookie(response, request, new_token)
            out["token"] = new_token
        logstream.info(
            "system",
            f"已从全量迁移包恢复：{_format_backup_stats_text(res['counts'])}；"
            f"恢复凭据={'是' if res['restored_credentials'] else '否'}，"
            f"恢复本地元数据={res['meta_files_restored']} 个文件",
        )
        return out
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
