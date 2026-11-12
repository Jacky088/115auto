"""网页全量迁移包：SQLite 一致性快照 + 可选凭据/本地元数据流式 ZIP 打包与安全恢复。"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

from .. import auth, config, database as db

BUNDLE_FORMAT = "autosave115-migration-bundle"
BUNDLE_VERSION = 1

MAX_BUNDLE_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024       # 单包上传上限 2 GB
MAX_BUNDLE_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024  # 解压后总体积上限 4 GB（防 zip 炸弹）
MAX_BUNDLE_DB_BYTES = 1024 * 1024 * 1024                # 快照库上限 1 GB
MAX_BUNDLE_FILES = 100_000

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".avif"})
_REQUIRED_DB_TABLES = (
    "settings",
    "channels",
    "rules",
    "routes",
    "blacklist",
    "subscriptions",
    "transfers",
    "auto_move_rules",
    "media_rules",
    "organized_records",
    "media_tasks",
)
_COPY_BUF_SIZE = 64 * 1024


def tmp_backup_root() -> Path:
    """临时迁移工作目录置于 DATA_DIR 内，避免占满容器小内存 /tmp tmpfs。"""
    p = config.DATA_DIR / ".tmp_backup"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cleanup_stale_tmp_dirs() -> None:
    """进程启动时清理上次异常退出遗留的临时备份/恢复目录。"""
    root = config.DATA_DIR / ".tmp_backup"
    if not root.exists():
        return
    for child in list(root.iterdir()):
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except OSError:
            pass


def resolve_active_meta_root() -> Path:
    """解析当前实例正在使用的本地 `.nfo`/海报根目录。"""
    custom_root = str(db.get_setting("media_local_meta_dir", "") or "").strip()
    base = Path(custom_root).expanduser() if custom_root else config.MEDIA_META_DIR
    return base.resolve()


def _credential_file_map() -> dict[str, Path]:
    return {
        "115_cookie.txt": config.COOKIE_FILE,
        "secret.key": config.SECRET_FILE,
        "initial_password.txt": auth.INITIAL_PASSWORD_FILE,
        "tg_session.session": config.TG_SESSION_FILE.with_name(config.TG_SESSION_FILE.name + ".session"),
        "tg_session.session-journal": config.TG_SESSION_FILE.with_name(config.TG_SESSION_FILE.name + ".session-journal"),
    }


def remap_meta_path(old_path: str, src_root: str, dst_root: Path) -> str:
    """跨平台/跨容器迁移时把来源机的绝对 local_meta_dir 安全重映射到目标元数据目录。"""
    raw = str(old_path or "").strip()
    if not raw or not src_root:
        return ""
    norm_raw = raw.replace("\\", "/")
    norm_src = src_root.strip().replace("\\", "/").rstrip("/")
    if not norm_src:
        return ""
    if norm_raw.lower().startswith(norm_src.lower() + "/"):
        rel = norm_raw[len(norm_src) + 1 :]
        parts = [seg for seg in rel.split("/") if seg and seg not in (".", "..") and ":" not in seg]
        if parts:
            dst_resolved = dst_root.resolve()
            cand = (dst_resolved / Path(*parts)).resolve()
            if cand != dst_resolved and cand.is_relative_to(dst_resolved):
                return str(cand)
    return ""


def _iter_safe_meta_files(meta_root: Path, meta_mode: str):
    """安全枚举 meta_root 下的文件（跳过符号链接与越界路径）。"""
    if meta_mode not in ("nfo", "all") or not meta_root.exists() or not meta_root.is_dir():
        return
    resolved_root = meta_root.resolve()
    for dirpath, dirnames, filenames in os.walk(resolved_root, followlinks=False):
        # 过滤掉符号链接目录，禁止跟随目录链接出逃
        dirnames[:] = sorted(
            d for d in dirnames
            if not (Path(dirpath) / d).is_symlink()
        )
        for fn in sorted(filenames):
            fpath = Path(dirpath) / fn
            if fpath.is_symlink():
                continue
            try:
                fres = fpath.resolve()
                if not fres.is_file() or not fres.is_relative_to(resolved_root):
                    continue
                rel = fres.relative_to(resolved_root)
            except OSError:
                continue
            if meta_mode == "nfo" and fres.suffix.lower() != ".nfo":
                continue
            yield fres, rel.as_posix()


def estimate_bundle() -> dict:
    """统计当前数据库、凭据文件与本地刮削元数据体量，供导出前估算参考。"""
    db_bytes = 0
    db_base = Path(config.DB_PATH)
    for cand in (
        db_base,
        db_base.with_name(db_base.name + "-wal"),
        db_base.with_name(db_base.name + "-shm"),
    ):
        try:
            if cand.exists() and cand.is_file():
                db_bytes += cand.stat().st_size
        except OSError:
            pass

    cred_bytes = 0
    cred_count = 0
    for p in _credential_file_map().values():
        try:
            if p.exists() and p.is_file():
                cred_bytes += p.stat().st_size
                cred_count += 1
        except OSError:
            pass

    meta_root = resolve_active_meta_root()
    nfo_count = 0
    nfo_bytes = 0
    asset_count = 0
    asset_bytes = 0
    for fres, _ in _iter_safe_meta_files(meta_root, "all"):
        try:
            sz = fres.stat().st_size
        except OSError:
            continue
        if fres.suffix.lower() == ".nfo":
            nfo_count += 1
            nfo_bytes += sz
        else:
            asset_count += 1
            asset_bytes += sz

    pending_media = db.query(
        "SELECT count(*) c FROM media_tasks WHERE status IN ('need_confirm','staged','queued','skipped','running')",
        one=True,
    )["c"]
    total_media = db.query("SELECT count(*) c FROM media_tasks", one=True)["c"]
    return {
        "db_bytes": db_bytes,
        "credentials_count": cred_count,
        "credentials_bytes": cred_bytes,
        "meta_root": str(meta_root),
        "meta_nfo_count": nfo_count,
        "meta_nfo_bytes": nfo_bytes,
        "meta_asset_count": asset_count,
        "meta_asset_bytes": asset_bytes,
        "pending_media_tasks": int(pending_media or 0),
        "total_media_tasks": int(total_media or 0),
    }


def build_migration_bundle(
    include_credentials: bool = False,
    meta_mode: str = "nfo",
    slim_db: bool = True,
) -> tuple[Path, Path, str, dict]:
    """生成全量迁移 ZIP 包到临时工作目录，返回 (work_dir, zip_path, filename, manifest)。"""
    if meta_mode not in ("none", "nfo", "all"):
        raise ValueError("无效的元数据导出选项，仅支持 none / nfo / all")

    work_dir = Path(tempfile.mkdtemp(dir=tmp_backup_root(), prefix="bundle_exp_"))
    try:
        snap_db = work_dir / "app.db"
        counts = db.export_db_snapshot(
            snap_db,
            exclude_credentials=not include_credentials,
            slim_db=slim_db,
        )
        db_size = snap_db.stat().st_size

        fname = f"autosave_migration_{time.strftime('%Y%m%d_%H%M%S')}.zip"
        zip_path = work_dir / fname
        cred_files: list[str] = []
        meta_nfo_count = 0
        meta_asset_count = 0
        meta_bytes = 0
        meta_root = resolve_active_meta_root()

        with zipfile.ZipFile(zip_path, "w", allowZip64=True) as zf:
            zf.write(snap_db, "app.db", compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
            try:
                snap_db.unlink(missing_ok=True)
            except OSError:
                pass

            if include_credentials:
                for cname, cpath in _credential_file_map().items():
                    try:
                        if cpath.exists() and cpath.is_file() and not cpath.is_symlink():
                            zf.write(
                                cpath,
                                f"credentials/{cname}",
                                compress_type=zipfile.ZIP_DEFLATED,
                                compresslevel=6,
                            )
                            cred_files.append(cname)
                    except OSError:
                        pass

            if meta_mode in ("nfo", "all"):
                for fres, rel_posix in _iter_safe_meta_files(meta_root, meta_mode):
                    ext = fres.suffix.lower()
                    ctype = zipfile.ZIP_STORED if ext in _IMAGE_EXTS else zipfile.ZIP_DEFLATED
                    kw = {} if ctype == zipfile.ZIP_STORED else {"compresslevel": 6}
                    zf.write(fres, f"media_meta/{rel_posix}", compress_type=ctype, **kw)
                    try:
                        meta_bytes += fres.stat().st_size
                    except OSError:
                        pass
                    if ext == ".nfo":
                        meta_nfo_count += 1
                    else:
                        meta_asset_count += 1

            manifest = {
                "format": BUNDLE_FORMAT,
                "version": BUNDLE_VERSION,
                "app_version": config.APP_VERSION,
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "include_credentials": bool(include_credentials),
                "meta_mode": meta_mode,
                "slim_db": bool(slim_db),
                "source_meta_root": str(meta_root),
                "db_bytes": db_size,
                "counts": counts,
                "credential_files": cred_files,
                "meta_nfo_count": meta_nfo_count,
                "meta_asset_count": meta_asset_count,
                "meta_bytes": meta_bytes,
            }
            zf.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=1),
                compress_type=zipfile.ZIP_DEFLATED,
            )

        return work_dir, zip_path, fname, manifest
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


def _validate_zip_member_path(name: str) -> PurePosixPath:
    """严格校验 ZIP 成员路径，防 zip-slip 路径穿越、绝对路径或驱动器号注入。"""
    if not name or "\x00" in name or "\\" in name or ":" in name or name.startswith("/"):
        raise ValueError(f"迁移包包含非法路径：{name!r}")
    p = PurePosixPath(name)
    if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        raise ValueError(f"迁移包包含越界路径：{name!r}")
    return p


def _copy_zip_entry_limited(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path, limit: int) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with zf.open(info, "r") as src, open(dest, "wb") as dst:
        while True:
            chunk = src.read(_COPY_BUF_SIZE)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                raise ValueError(f"文件 {info.filename} 解压体积超出安全上限")
            dst.write(chunk)
    return written


def _validate_sqlite_snapshot(db_path: Path) -> tuple[dict, bool]:
    conn = sqlite3.connect(db_path)
    try:
        chk = conn.execute("PRAGMA quick_check").fetchone()
        if not chk or chk[0] != "ok":
            raise ValueError("迁移包内的数据库完整性校验失败")
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        missing = [t for t in _REQUIRED_DB_TABLES if t not in tables]
        if missing:
            raise ValueError(f"迁移包数据库缺少必要表：{', '.join(missing)}")
        counts = {"settings": int(conn.execute("SELECT count(*) FROM settings").fetchone()[0])}
        for t in db.BACKUP_TABLES:
            counts[t] = int(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
        for extra in ("messages", "blacklist_hits", "daily_quota"):
            if extra in tables:
                counts[extra] = int(conn.execute(f"SELECT count(*) FROM {extra}").fetchone()[0])
        # 检查库内是否携带敏感设置
        qs = ",".join("?" * len(db.BACKUP_EXCLUDE_SETTINGS))
        has_secret_settings = bool(
            conn.execute(
                f"SELECT 1 FROM settings WHERE key IN ({qs}) AND value != '' AND value != '\"\"' LIMIT 1",
                tuple(db.BACKUP_EXCLUDE_SETTINGS),
            ).fetchone()
        )
        return counts, has_secret_settings
    finally:
        conn.close()


def inspect_migration_bundle(zip_path: Path, stage_dir: Path) -> dict:
    """流式校验并拆解迁移包摘要（防 zip-slip / 符号链接 / 解压炸弹 / 坏库）。"""
    if not zip_path.exists() or zip_path.stat().st_size == 0:
        raise ValueError("迁移包文件为空")
    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except Exception as e:
        raise ValueError(f"不是合法的 ZIP 迁移包：{e}") from e

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_BUNDLE_FILES:
            raise ValueError(f"迁移包文件数量超过 {MAX_BUNDLE_FILES} 上限")

        allowed_creds = set(_credential_file_map().keys())
        seen_names: set[str] = set()
        total_uncompressed = 0
        manifest_info: zipfile.ZipInfo | None = None
        db_info: zipfile.ZipInfo | None = None
        cred_names: list[str] = []
        meta_infos: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        meta_nfo_count = 0
        meta_asset_count = 0
        meta_bytes = 0

        for info in infos:
            if info.is_dir():
                continue
            # 拒绝符号链接（Unix mode 0120000）
            unix_mode = (info.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000:
                raise ValueError(f"迁移包包含不安全的符号链接：{info.filename}")
            total_uncompressed += max(0, int(info.file_size or 0))
            if total_uncompressed > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                raise ValueError("迁移包声明的解压总体积超出 4 GB 安全上限")

            p = _validate_zip_member_path(info.filename)
            if info.filename in seen_names:
                raise ValueError(f"迁移包存在重复路径：{info.filename}")
            seen_names.add(info.filename)

            if p.as_posix() == "manifest.json":
                manifest_info = info
            elif p.as_posix() == "app.db":
                db_info = info
            elif len(p.parts) == 2 and p.parts[0] == "credentials":
                if p.parts[1] not in allowed_creds:
                    raise ValueError(f"迁移包包含未知凭据文件：{info.filename}")
                cred_names.append(p.parts[1])
            elif len(p.parts) >= 2 and p.parts[0] == "media_meta":
                meta_infos.append((info, PurePosixPath(*p.parts[1:])))
                meta_bytes += max(0, int(info.file_size or 0))
                if p.suffix.lower() == ".nfo":
                    meta_nfo_count += 1
                else:
                    meta_asset_count += 1
            else:
                raise ValueError(f"迁移包包含非预期文件：{info.filename}")

        if manifest_info is None:
            raise ValueError("不是本程序的迁移包（缺少 manifest.json）")
        if db_info is None:
            raise ValueError("迁移包缺少核心数据库 app.db")

        manifest_bytes = zf.read(manifest_info)[: 2 * 1024 * 1024]
        try:
            manifest = json.loads(manifest_bytes)
        except Exception as e:
            raise ValueError(f"manifest.json 解析失败：{e}") from e
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != BUNDLE_FORMAT
            or int(manifest.get("version") or 0) != BUNDLE_VERSION
        ):
            raise ValueError("不支持的迁移包格式或版本")

        stage_dir.mkdir(parents=True, exist_ok=True)
        staged_db = stage_dir / "app.db"
        db_size = _copy_zip_entry_limited(zf, db_info, staged_db, MAX_BUNDLE_DB_BYTES)
        counts, db_has_secrets = _validate_sqlite_snapshot(staged_db)

    return {
        "manifest": manifest,
        "staged_db": staged_db,
        "exported_at": str(manifest.get("exported_at") or "未知时间"),
        "app_version": str(manifest.get("app_version") or ""),
        "source_meta_root": str(manifest.get("source_meta_root") or ""),
        "meta_mode": str(manifest.get("meta_mode") or "none"),
        "include_credentials": bool(manifest.get("include_credentials", False)),
        "has_credentials": bool(cred_names or db_has_secrets),
        "credential_files": sorted(cred_names),
        "has_meta": bool(meta_infos),
        "meta_nfo_count": meta_nfo_count,
        "meta_asset_count": meta_asset_count,
        "meta_bytes": meta_bytes,
        "db_bytes": db_size,
        "bundle_bytes": zip_path.stat().st_size,
        "counts": counts,
    }


async def apply_migration_bundle(
    zip_path: Path,
    restore_credentials: bool = True,
    restore_meta: bool = True,
) -> dict:
    """校验证件与包结构 → 可选断开旧会话 → 提取本地元数据 → 原子替换库并刷新内存状态。"""
    db.ensure_restore_idle_guard()
    stage_dir = zip_path.parent / "stage"
    info = await asyncio.to_thread(inspect_migration_bundle, zip_path, stage_dir)

    will_restore_creds = bool(restore_credentials and info["has_credentials"])
    will_restore_meta = bool(restore_meta and info["has_meta"])

    from ..core import classifier
    from ..pan115 import client as pan
    from ..tg import userbot

    if will_restore_creds:
        await userbot.disconnect_for_restore()

    target_meta_root = resolve_active_meta_root()
    meta_files_restored = 0
    if will_restore_meta:
        def _extract_meta() -> int:
            target_meta_root.mkdir(parents=True, exist_ok=True)
            resolved_root = target_meta_root.resolve()
            cnt = 0
            total_meta_written = 0
            with zipfile.ZipFile(zip_path, "r") as zf:
                for zinfo in zf.infolist():
                    if zinfo.is_dir():
                        continue
                    p = _validate_zip_member_path(zinfo.filename)
                    if len(p.parts) < 2 or p.parts[0] != "media_meta":
                        continue
                    rel_parts = p.parts[1:]
                    dest = (resolved_root / Path(*rel_parts)).resolve()
                    if dest == resolved_root or not dest.is_relative_to(resolved_root):
                        raise ValueError(f"元数据目标路径越界：{zinfo.filename}")
                    tmp_dest = dest.with_name(f".restore_tmp_{dest.name}")
                    written = _copy_zip_entry_limited(zf, zinfo, tmp_dest, 128 * 1024 * 1024)
                    total_meta_written += written
                    if total_meta_written > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                        tmp_dest.unlink(missing_ok=True)
                        raise ValueError("解压本地元数据总体积超出安全上限")
                    tmp_dest.replace(dest)
                    cnt += 1
            return cnt

        meta_files_restored = await asyncio.to_thread(_extract_meta)

    counts = await asyncio.to_thread(
        db.restore_db_snapshot,
        info["staged_db"],
        will_restore_creds,
        info["source_meta_root"],
        target_meta_root if will_restore_meta else None,
        remap_meta_path,
    )

    restored_cred_files: list[str] = []
    if will_restore_creds:
        def _extract_creds() -> list[str]:
            cmap = _credential_file_map()
            written_names: list[str] = []
            with zipfile.ZipFile(zip_path, "r") as zf:
                in_zip = {
                    PurePosixPath(zinfo.filename).parts[1]: zinfo
                    for zinfo in zf.infolist()
                    if not zinfo.is_dir()
                    and len(_validate_zip_member_path(zinfo.filename).parts) == 2
                    and PurePosixPath(zinfo.filename).parts[0] == "credentials"
                }
                for cname, dest in cmap.items():
                    if cname in in_zip:
                        tmp_dest = dest.with_name(f".restore_tmp_{dest.name}")
                        _copy_zip_entry_limited(zf, in_zip[cname], tmp_dest, 64 * 1024 * 1024)
                        config.secure_file(tmp_dest)
                        tmp_dest.replace(dest)
                        config.secure_file(dest)
                        written_names.append(cname)
                    elif cname in ("115_cookie.txt", "tg_session.session", "tg_session.session-journal", "initial_password.txt"):
                        # 同步清理旧实例残余会话/日志/初始密码文件，防止新旧状态混用
                        try:
                            dest.unlink(missing_ok=True)
                        except OSError:
                            pass
            return written_names

        restored_cred_files = await asyncio.to_thread(_extract_creds)
        auth.clear_secret_cache()

    classifier._cache = ([], 0.0)
    pan.reset_client()

    if will_restore_creds:
        mode = str(db.get_setting("tg_mode", "polling") or "polling")
        if mode in ("userbot", "both"):
            await userbot.start_if_configured()

    return {
        "counts": counts,
        "restored_credentials": will_restore_creds,
        "credential_files": restored_cred_files,
        "restored_meta": will_restore_meta,
        "meta_files_restored": meta_files_restored,
    }
