"""数据库备份导出、校验、恢复与快照引擎。"""
import json
import logging
import re
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger("database.backup")


def _db():
    from .. import database as db
    return db

from .. import config

BACKUP_EXCLUDE_SETTINGS = {
    "web_password_hash",   # 登录密码哈希（可离线爆破）
    "web_username",        # 与密码同属登录凭据，一并保留当前值
    "notify_token",        # TG Bot Token（等于 Bot 控制权）
    "tg_api_hash",         # TG API 凭据
    "tg_phone",            # 手机号（PII）
    "proxy",               # 代理地址可能内嵌账号密码
    "bark_url",            # URL 内嵌推送 key
    "pushplus_token",
    "ntfy_token",
    "api_token",           # 对外 API Token
    "tmdb_api_key",
    "tg_admin_id",         # TG Bot 授权用户绑定（auth 类配置，随登录凭据一并保留）
    "notify_chat_id",      # TG 通知目标会话：与 notify_token 同属推送目的地，恢复外来值
                           # 会把自动备份/通知导向未知会话，恢复后保留本机当前值
    "webdav_url",          # WebDAV 备份服务地址（可能内嵌内网拓扑/账号信息）
    "webdav_username",     # WebDAV 用户名，与密码同属云端写入凭据
    "webdav_password",     # WebDAV 应用密码（等同云端备份写入权限）
    "webdav_tested_url",   # 连接测试缓存：绑定具体地址/账号，不跨机恢复
    "webdav_tested_user",
    "webdav_last_test_ok", # 本机连通性状态，随地址/账号变化
}

# 绑定本机文件或引导完成的本地状态键：保留目标实例当前值，避免跨机恢复后把空机器误判为已登录/已配置
BACKUP_LOCAL_STATE_SETTINGS = frozenset({
    "cookie_ok",
    "userbot_ok",
    "setup_completed",
    "token_version",  # token 吊销版本号属本机安全状态：恢复外来值可能意外吊销/复活会话
})
BACKUP_KEEP_SETTINGS = frozenset(BACKUP_EXCLUDE_SETTINGS) | BACKUP_LOCAL_STATE_SETTINGS

# 内部运行态键：不随备份导出，恢复时一律重置或删除，避免恢复出旧运行标记或让 TG 复核误报
BACKUP_RUNTIME_SETTINGS = frozenset({
    "running",
    "paused",
    "auto_stop_reason",
    "backfill_pending",
    "webdav_last_backup_at",
    "webdav_last_backup_date",
    "webdav_last_backup_status",
})
BACKUP_RUNTIME_PREFIXES = ("poll_fail_",)

BACKUP_TABLES = (
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
_BACKUP_TABLE_SQL = {
    "transfers": "SELECT * FROM transfers ORDER BY id",
    "auto_move_rules": "SELECT * FROM auto_move_rules ORDER BY id",
    "media_rules": "SELECT * FROM media_rules ORDER BY id",
    "organized_records": "SELECT * FROM organized_records ORDER BY id",
    "media_tasks": (
        "SELECT * FROM media_tasks "
        "WHERE status IN ('need_confirm','staged','queued','skipped','running') ORDER BY id"
    ),
}
BACKUP_MAX_DECOMPRESSED_BYTES = 128 * 1024 * 1024  # 解压后上限 128 MB，防止 gzip/zip 炸弹打爆内存


def is_runtime_setting_key(key: str) -> bool:
    """判断是否为运行期瞬态配置键（导出排除、恢复重置/清理、TG 复核跳过）。"""
    return key in BACKUP_RUNTIME_SETTINGS or key.startswith(BACKUP_RUNTIME_PREFIXES)


def _read_stream_limited(stream, limit: int) -> bytes:
    """带解压体积上限的流式读取，防 gzip/zip 炸弹导致 OOM。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ValueError(f"备份解压后体积超过 {limit // (1024 * 1024)} MB 上限")
        chunks.append(chunk)
    return b"".join(chunks)


def parse_backup_bytes(
    raw_bytes: bytes,
    filename: str = "",
    max_decompressed_bytes: int = BACKUP_MAX_DECOMPRESSED_BYTES,
) -> dict:
    """统一解析 .json / .json.gz / .zip 备份字节流并做基础结构校验。"""
    import gzip
    import io
    import zipfile
    
    if not raw_bytes:
        raise ValueError("备份文件内容为空")
    name = str(filename or "").strip().lower()
    payload_bytes = raw_bytes
    if name.endswith((".gz", ".json.gz")) or raw_bytes[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw_bytes)) as gz:
                payload_bytes = _read_stream_limited(gz, max_decompressed_bytes)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Gzip 解压失败：{e}") from e
    elif name.endswith(".zip") or raw_bytes[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                jnames = [
                    info.filename
                    for info in zf.infolist()
                    if not info.is_dir()
                    and info.filename.lower().endswith(".json")
                    and not info.filename.startswith("__MACOSX/")
                ]
                if not jnames:
                    raise ValueError("ZIP 压缩包内未找到 .json 备份文件")
                with zf.open(jnames[0]) as zmember:
                    payload_bytes = _read_stream_limited(zmember, max_decompressed_bytes)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"ZIP 解压失败：{e}") from e
    elif len(raw_bytes) > max_decompressed_bytes:
        raise ValueError(f"备份文件体积超过 {max_decompressed_bytes // (1024 * 1024)} MB 上限")

    try:
        data = json.loads(payload_bytes)
    except Exception as e:
        raise ValueError(f"备份不是合法 JSON：{e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("_meta"), dict) or "settings" not in data:
        raise ValueError("不是本程序的备份文件（缺少 _meta 或 settings 标记）")
    return data


def backup_counts(data: dict) -> dict:
    """汇总备份字典中各业务表与 settings 数量，供网页预览/日志/TG 摘要共用。"""
    meta = data.get("_meta") or {}
    counts = {t: len(data.get(t) or []) for t in BACKUP_TABLES}
    return {
        "exported_at": str(meta.get("exported_at") or "未知时间"),
        "settings": len(data.get("settings") or {}),
        **counts,
    }


def _normalize_restored_media_task(row: dict) -> dict:
    """将备份时处于 running 的刮削/整理任务安全归位，避免恢复后出现卡死假象。"""
    r = dict(row)
    if str(r.get("status") or "") == "running":
        if str(r.get("task_kind") or "scrape") == "scrape" and r.get("selected_tmdb_id"):
            r["status"] = "queued"
        else:
            cands = str(r.get("candidates_json") or "").strip()
            r["status"] = "need_confirm" if cands not in ("", "[]") else "skipped"
    return r


def _restore_idle_checks_locked():
    """恢复空闲守卫的锁内复查。必须在 _lock 已持有的前提下调用（防 TOCTOU：
    外层检查与真正恢复之间可能有任务被 worker 领走）。注意不能调 get_setting（会重入 _lock）。"""
    row = _db()._conn.execute("SELECT value FROM settings WHERE key='running'").fetchone()
    if row is not None and row["value"] == "true":
        raise ValueError("系统正在运行，请先点击顶栏「停止」再恢复备份")
    running_rows = _db()._conn.execute(
        "SELECT count(*) c FROM transfers WHERE status='running'").fetchone()["c"]
    if running_rows:
        raise ValueError("有转存中的任务，请等待其完成（或重启程序）后再恢复备份")
    media_running = _db()._conn.execute(
        "SELECT count(*) c FROM media_tasks WHERE status='running'").fetchone()["c"]
    if media_running:
        raise ValueError("有正在执行的整理或刮削任务，请等待其结束后再恢复备份")


def ensure_restore_idle_guard():
    """恢复前状态检查：要求系统停止且无活动中的转存/整理刮削任务。"""
    if _db()._conn is None:
        if _db().get_setting("running", False):
            raise ValueError("系统正在运行，请先点击顶栏「停止」再恢复备份")
        return
    with _db()._lock:
        _restore_idle_checks_locked()


def _validate_restored_meta_dir(raw) -> str:
    """恢复链路对 media_local_meta_dir 复用与网页设置一致的边界校验，
    防止备份 JSON 直接携带任意目录绕过 api/media.py 的设置校验。"""
    raw = str(raw or "").strip()
    if "\x00" in raw:
        raise ValueError("备份中的本地元数据目录包含非法空字符")
    if not raw:
        return ""
    resolved = Path(raw).expanduser().resolve()
    if (len(resolved.parts) <= 1
            or resolved.is_relative_to(config.BACKEND_DIR)
            or config.BACKEND_DIR.is_relative_to(resolved)
            or resolved.is_relative_to(config.DATA_DIR)
            or config.DATA_DIR.is_relative_to(resolved)):
        raise ValueError("备份中的本地元数据目录不合法（不允许系统根目录、程序目录或系统数据目录）")
    return str(resolved)


def restore_payload(data: dict) -> dict:
    """从备份字典恢复全部业务数据（整体覆盖，事务内完成）。

    校验通过后按备份内容重建 BACKUP_TABLES 全部业务表；settings 按「整体覆盖但保留
    BACKUP_KEEP_SETTINGS（凭据与本机登录/向导状态）当前值」处理；
    恢复完成后强制 running=false（停止状态）并清除旧运行态；旧备份不含 media_tasks 时
    同步清空残留队列，含 media_tasks 时按非运行态还原。返回各表恢复条数。
    """
    if not isinstance(data, dict) or not isinstance(data.get("_meta"), dict):
        raise ValueError("不是有效的备份文件（缺少 _meta 段）")
    for t in BACKUP_TABLES:
        data.setdefault(t, [])
    if "settings" not in data:
        raise ValueError("备份缺少 settings 段")
    if not isinstance(data["settings"], dict):
        raise ValueError("备份 settings 段格式不正确")
    for t in BACKUP_TABLES:
        if not isinstance(data[t], list):
            raise ValueError(f"备份 {t} 段格式不正确")

    ensure_restore_idle_guard()

    with _db()._lock:
        try:
            _restore_idle_checks_locked()  # 锁内复查，缩小 TOCTOU 窗口
            # 列名白名单：在连接互斥锁内读取真实表结构（防恶意备份注入与并发连接冲突）
            allowed_cols = {t: {r["name"] for r in _db()._conn.execute(f"PRAGMA table_info({t})")}
                            for t in BACKUP_TABLES}
            for t in BACKUP_TABLES:
                for r in (data[t] or []):
                    if not isinstance(r, dict):
                        raise ValueError(f"{t} 中存在非对象行")
                    unknown = set(r.keys()) - allowed_cols[t]
                    if unknown:
                        raise ValueError(f"备份 {t} 段包含未知列: {', '.join(sorted(unknown))}")
            cur = _db()._conn.cursor()
            cur.execute("BEGIN")
            # 先记下需要保留的当前设置（凭据及本机登录/向导状态，恢复后原样带回来）
            keep = {}
            for k in BACKUP_KEEP_SETTINGS:
                row = cur.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
                if row is not None:
                    keep[k] = row["value"]
            seen_active: set[str] = set()
            for t in BACKUP_TABLES:
                rows = data[t] or []
                cur.execute(f"DELETE FROM {t}")
                for r in rows:
                    if t == "transfers":
                        r = dict(r)
                        # 同分享码的多条活动行（旧版竞态遗留）只保留第一条，
                        # 其余降级为 duplicate，避免触发活动任务唯一索引
                        if str(r.get("status")) in ("queued", "running", "submitted"):
                            sc = str(r.get("share_code") or "")
                            if sc in seen_active:
                                r["status"] = "duplicate"
                            else:
                                seen_active.add(sc)
                    elif t == "media_tasks":
                        r = _normalize_restored_media_task(r)
                    cols = ",".join(r.keys())
                    qs = ",".join("?" * len(r))
                    cur.execute(f"INSERT INTO {t}({cols}) VALUES({qs})", tuple(r.values()))
                # 自增序列对齐到恢复后的最大 id，防止后续新记录与恢复记录撞 id
                try:
                    if rows:
                        cur.execute("UPDATE sqlite_sequence SET seq=(SELECT MAX(id) FROM "
                                    f"{t}) WHERE name='{t}'")
                        if cur.execute("SELECT changes()").fetchone()[0] == 0:
                            cur.execute(f"INSERT INTO sqlite_sequence(name,seq) VALUES('{t}',"
                                        f"(SELECT MAX(id) FROM {t}))")
                    else:
                        cur.execute(f"DELETE FROM sqlite_sequence WHERE name='{t}'")
                except sqlite3.OperationalError:
                    pass  # 表无 AUTOINCREMENT 时没有 sqlite_sequence 行，忽略
            cur.execute("DELETE FROM settings")
            for k, v in (data["settings"] or {}).items():
                # 键名限定为应用自身的设置键格式（小写字母/数字/下划线）：
                # 恶意备份无法借恢复通道注入任意键名/符号键
                if (not isinstance(k, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", k)):
                    raise ValueError("备份 settings 中存在非法键名")
                # 凭据/本机状态与内部运行态一律不采纳备份值，防止替换敏感凭据或误恢复运行态
                if k in BACKUP_KEEP_SETTINGS or is_runtime_setting_key(k):
                    continue
                if k == "media_local_meta_dir":
                    v = _validate_restored_meta_dir(v)
                cur.execute("INSERT INTO settings VALUES(?,?)", (k, str(v)))
            for k, v in keep.items():
                cur.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, v))
            cur.execute("INSERT OR REPLACE INTO settings VALUES('running','false')")
            # 显式清除运行期瞬态键（兼容旧备份结构也无多余残留）
            for k in ("paused", "auto_stop_reason", "backfill_pending"):
                cur.execute("DELETE FROM settings WHERE key=?", (k,))
            cur.execute("DELETE FROM settings WHERE key LIKE 'poll_fail_%'")
            _db()._conn.commit()
            stats = {"settings": len(data["settings"] or {})}
            stats.update({t: len(data[t] or []) for t in BACKUP_TABLES})
            return stats
        except Exception:
            _db()._conn.rollback()
            raise


def backup_payload() -> dict:
    """全部业务数据 → JSON 备份字典（网页导出与 TG 定时备份共用）。"""
    def dump(sql):
        return [dict(r) for r in _db().query(sql)]

    out = {
        "_meta": {"version": 1, "exported_at": time.strftime("%Y-%m-%d %H:%M:%S")},
        "settings": {
            r["key"]: r["value"]
            for r in dump("SELECT * FROM settings")
            if r["key"] not in BACKUP_KEEP_SETTINGS and not is_runtime_setting_key(r["key"])
        },
    }
    for t in BACKUP_TABLES:
        out[t] = dump(_BACKUP_TABLE_SQL.get(t, f"SELECT * FROM {t}"))
    return out


def _collect_conn_counts(conn: sqlite3.Connection) -> dict:
    counts = {"settings": int(conn.execute("SELECT count(*) FROM settings").fetchone()[0])}
    for t in BACKUP_TABLES:
        counts[t] = int(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
    for extra in ("messages", "blacklist_hits", "daily_quota", "logs", "tmdb_cache"):
        counts[extra] = int(conn.execute(f"SELECT count(*) FROM {extra}").fetchone()[0])
    return counts


def export_db_snapshot(dest_path: Path, exclude_credentials: bool = True, slim_db: bool = True) -> dict:
    """用 SQLite Online Backup API 导出一致性库快照（可选脱敏凭据与清理缓存日志）。"""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        dest_path.unlink()
    dst = sqlite3.connect(dest_path)
    src = None
    try:
        with _db()._lock:
            _db()._conn.commit()
        # 用独立连接做 Online Backup：WAL 模式下备份读取不影响主连接写入，
        # 快照一致性由 SQLite 保证；备份期间转存/日志照常写库（此前全程持有全局锁）。
        src = sqlite3.connect(config.DB_PATH, timeout=30)
        src.execute("PRAGMA busy_timeout=30000")
        src.backup(dst)
        src.close()
        src = None

        # 以下仅操作临时快照文件，不持有主库锁
        dst.execute("INSERT OR REPLACE INTO settings VALUES('running','false')")
        for k in ("paused", "auto_stop_reason", "backfill_pending"):
            dst.execute("DELETE FROM settings WHERE key=?", (k,))
        dst.execute("DELETE FROM settings WHERE key LIKE 'poll_fail_%'")
        # 运行中的任务快照中统一归位，防止跨机打开即呈现卡死状态
        dst.row_factory = sqlite3.Row
        for mt in dst.execute("SELECT * FROM media_tasks WHERE status='running'").fetchall():
            norm = _normalize_restored_media_task(dict(mt))
            dst.execute("UPDATE media_tasks SET status=? WHERE id=?", (norm["status"], mt["id"]))
        dst.execute("UPDATE transfers SET status='queued', running_at=NULL WHERE status='running'")

        if exclude_credentials:
            qs = ",".join("?" * len(BACKUP_KEEP_SETTINGS))
            dst.execute(f"DELETE FROM settings WHERE key IN ({qs})", tuple(BACKUP_KEEP_SETTINGS))
        if slim_db:
            for tbl in ("logs", "tmdb_cache", "media_tg_batches"):
                dst.execute(f"DELETE FROM {tbl}")
            dst.commit()
            dst.execute("VACUUM")
        counts = _collect_conn_counts(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
        dst.commit()
        return counts
    finally:
        if src is not None:
            try:
                src.close()
            except Exception:
                logger.debug("关闭备份源连接失败（不影响已完成快照）", exc_info=True)
        dst.close()


def restore_db_snapshot(
    src_db_path: Path,
    restore_credentials: bool = False,
    source_meta_root: str = "",
    target_meta_root: Path | None = None,
    remap_meta_fn=None,
) -> dict:
    """用校验通过的快照库原子替换主库内容，并视策略保留本机凭据与映射本地元数据路径。"""
    ensure_restore_idle_guard()
    src = sqlite3.connect(f"file:{src_db_path}?mode=ro", uri=True)
    try:
        migrate_warnings = []
        with _db()._lock:
            _restore_idle_checks_locked()  # 锁内复查，缩小 TOCTOU 窗口
            keep = {}
            for k in BACKUP_KEEP_SETTINGS:
                row = _db()._conn.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
                if row is not None:
                    keep[k] = row["value"]
            cur_meta_row = _db()._conn.execute(
                "SELECT value FROM settings WHERE key='media_local_meta_dir'"
            ).fetchone()
            cur_meta_val = cur_meta_row["value"] if cur_meta_row is not None else None

            src.backup(_db()._conn)
            migrate_warnings = _db()._migrate()
            _db()._seed()

            # 强制复位运行态
            _db()._conn.execute("INSERT OR REPLACE INTO settings VALUES('running','false')")
            for k in ("paused", "auto_stop_reason", "backfill_pending"):
                _db()._conn.execute("DELETE FROM settings WHERE key=?", (k,))
            _db()._conn.execute("DELETE FROM settings WHERE key LIKE 'poll_fail_%'")

            # 本机自定义元数据目录不盲从来源机器路径，以免跨 OS/容器失效
            if cur_meta_val is not None:
                _db()._conn.execute("INSERT OR REPLACE INTO settings VALUES('media_local_meta_dir',?)", (cur_meta_val,))
            else:
                _db()._conn.execute("DELETE FROM settings WHERE key='media_local_meta_dir'")

            if not restore_credentials:
                qs = ",".join("?" * len(BACKUP_KEEP_SETTINGS))
                _db()._conn.execute(f"DELETE FROM settings WHERE key IN ({qs})", tuple(BACKUP_KEEP_SETTINGS))
                for k, v in keep.items():
                    _db()._conn.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, v))
            elif not _db()._conn.execute("SELECT 1 FROM settings WHERE key='web_password_hash' AND value != ''").fetchone():
                # 兜底防锁死：若导入包意外缺少登录密码哈希，保留当前实例登录凭据
                for k in ("web_username", "web_password_hash"):
                    if k in keep:
                        _db()._conn.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, keep[k]))

            # 运行中任务安全归位
            _db()._conn.execute("UPDATE transfers SET status='queued', running_at=NULL WHERE status='running'")
            for mt in _db()._conn.execute("SELECT * FROM media_tasks WHERE status='running'").fetchall():
                norm = _normalize_restored_media_task(dict(mt))
                _db()._conn.execute("UPDATE media_tasks SET status=? WHERE id=?", (norm["status"], mt["id"]))

            # 跨机重映射本地元数据路径
            if target_meta_root is not None and remap_meta_fn is not None:
                for mt in _db()._conn.execute(
                    "SELECT id, local_meta_dir FROM media_tasks WHERE local_meta_dir != ''"
                ).fetchall():
                    new_dir = remap_meta_fn(mt["local_meta_dir"], source_meta_root, target_meta_root)
                    if new_dir != mt["local_meta_dir"]:
                        _db()._conn.execute("UPDATE media_tasks SET local_meta_dir=? WHERE id=?", (new_dir, mt["id"]))

            _db()._conn.commit()
            counts = _collect_conn_counts(_db()._conn)
        # 锁外补发迁移告警日志（锁内写日志经 db.execute 会重入 _lock 导致死锁）
        _db()._emit_migrate_warnings(migrate_warnings)
        return counts
    finally:
        src.close()
