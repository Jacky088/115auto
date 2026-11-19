"""SQLite 数据层：所有表、设置读写、种子数据。同步 sqlite3 + WAL，小数据量足够。"""
import json
import logging
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from . import config
from .db import (
    BACKUP_EXCLUDE_SETTINGS,
    BACKUP_KEEP_SETTINGS,
    BACKUP_LOCAL_STATE_SETTINGS,
    BACKUP_MAX_DECOMPRESSED_BYTES,
    BACKUP_RUNTIME_PREFIXES,
    BACKUP_RUNTIME_SETTINGS,
    BACKUP_TABLES,
    DEFAULT_BLACKLIST_KEYWORDS,
    DEFAULT_ROUTES,
    DEFAULT_RULES,
    DEFAULT_SETTINGS,
    SCHEMA,
    _BACKUP_TABLE_SQL,
    _collect_conn_counts,
    _emit_migrate_warnings,
    _migrate,
    _normalize_restored_media_task,
    _read_stream_limited,
    _restore_idle_checks_locked,
    _seed,
    _validate_restored_meta_dir,
    backup_counts,
    backup_payload,
    ensure_restore_idle_guard,
    export_db_snapshot,
    is_runtime_setting_key,
    parse_backup_bytes,
    restore_db_snapshot,
    restore_payload,
)

logger = logging.getLogger("database")

# RLock（而非 Lock）：transaction() 上下文与 _migrate 等持锁路径内严禁调用
# execute/query/add_log 的约定改为结构性保证——同线程重入不再死锁
_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _init_db():
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=60.0)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")  # WAL 下的安全档位：高频小事务（写日志）收益明显
        # 遇到锁竞争时自旋等待长达 60s 而不是提前抛 database is locked
        _conn.execute("PRAGMA busy_timeout=60000")
        # 快速结构完整性校验，拦截损坏页或异常头
        res = _conn.execute("PRAGMA quick_check").fetchone()
        if res and res[0] != "ok":
            raise sqlite3.DatabaseError(f"quick_check failed: {res[0]}")
        _conn.executescript(SCHEMA)
        _migrate_warnings = _migrate(_conn)
        _seed(_conn)
        _conn.commit()
        _emit_migrate_warnings(_migrate_warnings)


def _handle_corrupt_db(err: Exception):
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None

    db_path = Path(config.DB_PATH).resolve()
    db_dir = db_path.parent
    db_dir.mkdir(parents=True, exist_ok=True)
    db_name = db_path.name
    ts = time.strftime("%Y%m%d_%H%M%S")

    corrupted_files = []
    # 查找 app.db, app.db-wal, app.db-shm 及相关临时文件
    for target in [db_path, db_dir / f"{db_name}-wal", db_dir / f"{db_name}-shm"]:
        if target.exists():
            backup_file = db_dir / f"{target.name}.corrupted_{ts}.bak"
            try:
                shutil.copy2(target, backup_file)
                corrupted_files.append(str(backup_file.name))
                target.unlink(missing_ok=True)
            except Exception as ex:
                logger.warning(f"处理损坏数据库文件 {target} 失败: {ex}")

    # 写入修复通知文件方便用户排查
    notice_file = db_dir / "DATABASE_RECOVERY_NOTICE.txt"
    try:
        with open(notice_file, "a", encoding="utf-8") as f:
            f.write(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 数据库损坏自动容灾自愈触发\n"
                f"错误原因: {err}\n"
                f"原损坏文件已安全备份为: {', '.join(corrupted_files)}\n"
                f"系统已自动重建空白健康数据库，服务恢复正常启动运行。\n\n"
            )
    except Exception:
        pass

    logger.warning("数据库已自动重建，损坏备份已归档至: %s", corrupted_files)
    _init_db()


def init():
    try:
        _init_db()
    except sqlite3.DatabaseError as e:
        logger.error(f"数据库初始化检测到严重损坏 (DatabaseError: {e})，正在启动自动容灾自愈机制...")
        _handle_corrupt_db(e)


# ---- 基础 ----
def execute(sql, params=()) -> int:
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur.rowcount


def insert(sql, params=()) -> int:
    """执行 INSERT 并返回自增 id（与 execute 同一把锁，避免再查 last row 时被其他线程插入抢走）。"""
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return int(cur.lastrowid or 0)


def query(sql, params=(), one=False):
    with _lock:
        rows = _conn.execute(sql, params).fetchall()
    if one:
        return rows[0] if rows else None
    return rows


@contextmanager
def transaction():
    """批量写事务：多条语句一次提交（注意：内部不要调用 execute/query，会死锁，
    直接用返回的连接游标操作）。异常自动回滚。"""
    with _lock:
        try:
            yield _conn
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise


# ---- settings ----
def get_setting(key, default=None):
    row = query("SELECT value FROM settings WHERE key=?", (key,), one=True)
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]


def set_setting(key, value):
    execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)))


# ---- 转存 ----
def quota_today() -> int:
    row = query("SELECT count FROM daily_quota WHERE day=?", (date.today().isoformat(),), one=True)
    return row["count"] if row else 0


def quota_incr():
    execute("INSERT INTO daily_quota VALUES(?,1) ON CONFLICT(day) DO UPDATE SET count=count+1",
            (date.today().isoformat(),))


def add_log(level: str, module: str, message: str):
    execute("INSERT INTO logs(level,module,message,created_at) VALUES(?,?,?,?)",
            (level, module, message[:2000], time.time()))


__all__ = [
    "SCHEMA",
    "DEFAULT_SETTINGS",
    "DEFAULT_RULES",
    "DEFAULT_ROUTES",
    "DEFAULT_BLACKLIST_KEYWORDS",
    "_seed",
    "_migrate",
    "_emit_migrate_warnings",
    "_lock",
    "_conn",
    "_init_db",
    "_handle_corrupt_db",
    "init",
    "execute",
    "insert",
    "query",
    "transaction",
    "get_setting",
    "set_setting",
    "quota_today",
    "quota_incr",
    "add_log",
    "BACKUP_EXCLUDE_SETTINGS",
    "BACKUP_LOCAL_STATE_SETTINGS",
    "BACKUP_KEEP_SETTINGS",
    "BACKUP_RUNTIME_SETTINGS",
    "BACKUP_RUNTIME_PREFIXES",
    "BACKUP_TABLES",
    "_BACKUP_TABLE_SQL",
    "BACKUP_MAX_DECOMPRESSED_BYTES",
    "is_runtime_setting_key",
    "_read_stream_limited",
    "parse_backup_bytes",
    "backup_counts",
    "_normalize_restored_media_task",
    "_restore_idle_checks_locked",
    "ensure_restore_idle_guard",
    "_validate_restored_meta_dir",
    "restore_payload",
    "backup_payload",
    "_collect_conn_counts",
    "export_db_snapshot",
    "restore_db_snapshot",
]
