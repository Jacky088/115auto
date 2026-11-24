"""WebDAV 云端备份 API：配置管理/连通性测试/远端备份列表/即时整机备份上传/远端恢复拉取。"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from ... import database as db
from ...core import migration_bundle, webdav
from ...core.executors import run_ext_thread
from ..deps import require_user

router = APIRouter(prefix="/backup/webdav")


def _safe_backup_filename(filename: str) -> str:
    """校验远端备份文件名：仅接受本系统生成的 autosave_*.zip，阻断路径穿越。"""
    fn = str(filename or "").strip()
    if (not fn.startswith("autosave_") or not fn.lower().endswith(".zip")
            or "/" in fn or "\\" in fn or ".." in fn):
        raise HTTPException(400, "非法的备份文件名")
    return fn


@router.get("/config")
def get_config(_=Depends(require_user)):
    """获取当前 WebDAV 备份配置（密码脱敏）。"""
    return webdav.get_webdav_config()


@router.post("/config")
def save_config(body: dict, _=Depends(require_user)):
    """保存 WebDAV 备份配置（添加或更换必须先测试成功）。"""
    url = str(body.get("url") or "").strip()
    username = str(body.get("username") or "").strip()
    password = body.get("password")
    root_dir = str(body.get("root_dir") or "/115AutoSave_Backup").strip()
    keep_count = int(body.get("keep_count") or 10)
    auto_backup = bool(body.get("auto_backup_enabled", False))

    if not url or not username:
        raise HTTPException(400, "WebDAV 服务地址与用户名不能为空")

    last_ok = bool(db.get_setting("webdav_last_test_ok", False))
    tested_url = str(db.get_setting("webdav_tested_url", "") or "")
    tested_user = str(db.get_setting("webdav_tested_user", "") or "")

    clean_url = url.rstrip("/")
    if not last_ok or clean_url != tested_url.rstrip("/") or username != tested_user:
        raise HTTPException(400, "WebDAV 配置必须先点击「测试连接」验证通过后方可保存（更换配置同理）")

    return webdav.save_webdav_config(
        url=url,
        username=username,
        password=password,
        root_dir=root_dir,
        keep_count=keep_count,
        auto_backup_enabled=auto_backup,
    )


@router.delete("/config")
def delete_config(_=Depends(require_user)):
    """清除已保存的 WebDAV 配置与凭据。"""
    return webdav.clear_webdav_config()


@router.post("/test")
async def test_connection(body: dict, _=Depends(require_user)):
    """测试 WebDAV 连通性与读写删权限（带耗时统计）。"""
    url = str(body.get("url") or "").strip()
    username = str(body.get("username") or "").strip()
    raw_pwd = str(body.get("password") or "")
    root_dir = str(body.get("root_dir") or "/115AutoSave_Backup").strip()

    if not raw_pwd or raw_pwd == "******":
        # 尝试使用已保存的原密码
        raw_pwd = str(await asyncio.to_thread(db.get_setting, "webdav_password", "") or "")

    if not url or not username or not raw_pwd:
        raise HTTPException(400, "请完整填写 WebDAV 地址、用户名与应用密码后再进行测试")

    client = webdav.WebDAVClient(
        base_url=url,
        username=username,
        password=raw_pwd,
        root_dir=root_dir,
    )
    res = await run_ext_thread(client.test_connection)
    if res.get("ok"):
        await asyncio.to_thread(db.set_setting, "webdav_last_test_ok", True)
        await asyncio.to_thread(db.set_setting, "webdav_tested_url", url.rstrip("/"))
        await asyncio.to_thread(db.set_setting, "webdav_tested_user", username)
    else:
        await asyncio.to_thread(db.set_setting, "webdav_last_test_ok", False)
        await asyncio.to_thread(db.set_setting, "webdav_tested_url", "")
        await asyncio.to_thread(db.set_setting, "webdav_tested_user", "")
    return res


@router.post("/export_now")
async def export_to_webdav(body: dict | None = None, _=Depends(require_user)):
    """立即生成一份整机迁移包并直接流式上传至已配置的 WebDAV。"""
    client = webdav.create_configured_client()
    if not client:
        raise HTTPException(400, "尚未配置有效的 WebDAV 服务器或未保存应用密码")

    last_test_ok = bool(await asyncio.to_thread(db.get_setting, "webdav_last_test_ok", False))
    if not last_test_ok:
        raise HTTPException(400, "请先在上方点击「测试连接」并成功通过后，再执行 WebDAV 上传")

    payload = body or {}
    meta_mode = str(payload.get("meta_mode") or "nfo")
    slim_db = bool(payload.get("slim_db", True))
    include_credentials = bool(payload.get("include_credentials", False))

    def _do_build_and_upload():
        work_dir = None
        try:
            work_dir, zip_path, fname, _ = migration_bundle.build_migration_bundle(
                include_credentials=include_credentials,
                meta_mode=meta_mode,
                slim_db=slim_db,
            )
            up_res = client.upload_file(zip_path, fname)
            keep_count = int(db.get_setting("webdav_keep_count", 10) or 10)
            pruned = client.prune_backups(keep_count)
            return {
                "ok": True,
                "filename": fname,
                "bytes": up_res["bytes"],
                "elapsed_sec": up_res["elapsed_sec"],
                "pruned": pruned,
            }
        finally:
            if work_dir and Path(work_dir).exists():
                shutil.rmtree(work_dir, ignore_errors=True)

    try:
        res = await run_ext_thread(_do_build_and_upload)
        await asyncio.to_thread(db.set_setting, "webdav_last_backup_at", time.time())
        await asyncio.to_thread(db.set_setting, "webdav_last_backup_status", f"成功 ({res['filename']})")
        return res
    except Exception as e:
        await asyncio.to_thread(db.set_setting, "webdav_last_backup_status", f"失败: {str(e)[:100]}")
        raise HTTPException(500, f"整机备份上传 WebDAV 失败: {str(e)[:200]}")


@router.get("/list")
async def list_webdav_backups(_=Depends(require_user)):
    """获取远端 WebDAV 备份目录下的迁移包列表（失败原因原样透出，便于用户排查）。"""
    client = webdav.create_configured_client()
    if not client:
        raise HTTPException(400, "尚未配置有效的 WebDAV 服务器：缺少服务地址/用户名/应用密码，请重新填写并测试连接")
    try:
        return await run_ext_thread(client.list_backups)
    except Exception as e:
        raise HTTPException(502, f"读取 WebDAV 备份列表失败：{str(e)[:200]}") from e


@router.post("/restore_preview")
async def restore_preview_from_webdav(
    filename: str = Query(..., description="远端备份文件名"),
    _=Depends(require_user),
):
    """从 WebDAV 下载指定的备份文件并解析结构供确认。"""
    fn = _safe_backup_filename(filename)
    client = webdav.create_configured_client()
    if not client:
        raise HTTPException(400, "尚未配置有效的 WebDAV 服务器")

    work_dir = Path(tempfile.mkdtemp(dir=migration_bundle.tmp_backup_root(), prefix="dav_prev_"))
    local_zip = work_dir / fn

    def _download_and_inspect():
        client.download_file(fn, local_zip)
        return migration_bundle.inspect_migration_bundle(local_zip, work_dir / "stage")

    try:
        info = await run_ext_thread(_download_and_inspect)
        return {"ok": True, "filename": fn, "preview": info}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@router.post("/restore_apply")
async def restore_apply_from_webdav(
    filename: str = Query(..., description="远端备份文件名"),
    restore_credentials: bool = Query(False),
    restore_meta: bool = Query(True),
    _=Depends(require_user),
):
    """从 WebDAV 下载指定的备份文件并原子恢复到本地系统。"""
    fn = _safe_backup_filename(filename)
    client = webdav.create_configured_client()
    if not client:
        raise HTTPException(400, "尚未配置有效的 WebDAV 服务器")

    work_dir = Path(tempfile.mkdtemp(dir=migration_bundle.tmp_backup_root(), prefix="dav_rest_"))
    local_zip = work_dir / fn

    try:
        await run_ext_thread(client.download_file, fn, local_zip)
        res = await migration_bundle.apply_migration_bundle(
            local_zip,
            restore_credentials=restore_credentials,
            restore_meta=restore_meta,
        )
        return res
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@router.delete("/file")
async def delete_backup_file(
    filename: str = Query(..., description="要删除的远端备份文件名"),
    _=Depends(require_user),
):
    """删除 WebDAV 远端指定的备份文件（带文件名合法性校验）。"""
    fn = _safe_backup_filename(filename)

    client = webdav.create_configured_client()
    if not client:
        raise HTTPException(400, "尚未配置有效的 WebDAV 服务器或未保存应用密码")

    try:
        await run_ext_thread(client.delete_file, fn)
        return {"ok": True, "message": f"备份文件 {fn} 已成功删除"}
    except Exception as e:
        raise HTTPException(500, f"删除 WebDAV 备份文件失败: {str(e)[:100]}")
