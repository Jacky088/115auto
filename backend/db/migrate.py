"""数据库轻量迁移与告警日志。"""
import json
import sqlite3

def _migrate(conn=None):
    if conn is None:
        from .. import database as db
        conn = db._conn
    """轻量迁移：旧库补新列。返回迁移告警消息列表——调用方必须在释放 _lock 之后再写日志
    （logstream→db.add_log→execute 会再次获取 _lock，锁内直写会同线程重入死锁）。"""
    _warnings = []
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(transfers)")}
    if "bypass_filters" not in cols:
        conn.execute("ALTER TABLE transfers ADD COLUMN bypass_filters INTEGER NOT NULL DEFAULT 0")
    if "increment" not in cols:
        conn.execute("ALTER TABLE transfers ADD COLUMN increment INTEGER NOT NULL DEFAULT 0")
    if "priority" not in cols:
        conn.execute("ALTER TABLE transfers ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
    if "running_at" not in cols:
        conn.execute("ALTER TABLE transfers ADD COLUMN running_at REAL")
    if "kind" not in cols:
        conn.execute("ALTER TABLE transfers ADD COLUMN kind TEXT NOT NULL DEFAULT 'share'")
    # 订阅质量策略/洗版所需列（旧库补列，默认值保持旧行为：0/空=不参与比较）
    for col, ddl in (("sub_id", "INTEGER NOT NULL DEFAULT 0"),
                     ("episode_key", "TEXT NOT NULL DEFAULT ''"),
                     ("quality_score", "INTEGER NOT NULL DEFAULT 0"),
                     ("quality_tag", "TEXT NOT NULL DEFAULT ''"),
                     ("wash", "INTEGER NOT NULL DEFAULT 0"),
                     ("wash_from", "INTEGER NOT NULL DEFAULT 0"),
                     ("dest_cid", "INTEGER NOT NULL DEFAULT 0"),
                     ("dest_ids", "TEXT NOT NULL DEFAULT ''")):
        if col not in cols:
            conn.execute(f"ALTER TABLE transfers ADD COLUMN {col} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_transfers_sub_ep ON transfers(sub_id, episode_key)")
    rcols = {r["name"] for r in conn.execute("PRAGMA table_info(routes)")}
    if "offline_dir" not in rcols:
        conn.execute("ALTER TABLE routes ADD COLUMN offline_dir TEXT NOT NULL DEFAULT ''")
    rule_cols = {r["name"] for r in conn.execute("PRAGMA table_info(rules)")}
    if "note" not in rule_cols:
        conn.execute("ALTER TABLE rules ADD COLUMN note TEXT DEFAULT ''")
    # 频道级订阅模式开关（旧库无此列，默认全盘转存）
    ccols = {r["name"] for r in conn.execute("PRAGMA table_info(channels)")}
    if "sub_only" not in ccols:
        conn.execute("ALTER TABLE channels ADD COLUMN sub_only INTEGER NOT NULL DEFAULT 0")
    # 整理账本撤销备注（定时文件整理的账本复用本表；撤销遇位置漂移等原因记在此处）
    ocols = {r["name"] for r in conn.execute("PRAGMA table_info(organized_records)")}
    if "undo_note" not in ocols:
        conn.execute("ALTER TABLE organized_records ADD COLUMN undo_note TEXT DEFAULT ''")
    # 整理规则的媒体类型（movie 规则跳过带集数标识文件，解两库同名错位归档）
    # 与云下载目录模式（按父目录名匹配，仅云下载目录的规则可开启）
    acols = {r["name"] for r in conn.execute("PRAGMA table_info(auto_move_rules)")}
    if "media_type" not in acols:
        conn.execute("ALTER TABLE auto_move_rules ADD COLUMN media_type TEXT NOT NULL DEFAULT 'auto'")
    if "dir_match" not in acols:
        conn.execute("ALTER TABLE auto_move_rules ADD COLUMN dir_match INTEGER NOT NULL DEFAULT 0")
    # 订阅别名与结构化追更进度（旧库无这些列，默认空值即可兼容）
    # 注意：col/ddl 来自上方硬编码元组（非用户输入），f-string 拼接安全；
    # 若未来改为动态来源，必须先过白名单
    scols = {r["name"] for r in conn.execute("PRAGMA table_info(subscriptions)")}
    for col, ddl in (("aliases", "TEXT DEFAULT ''"),
                     ("exclude_keywords", "TEXT DEFAULT ''"),
                     ("tmdb_id", "INTEGER"),
                     ("kind", "TEXT DEFAULT ''"),
                     ("last_season", "INTEGER"),
                     ("last_episode", "INTEGER"),
                     ("min_quality", "TEXT DEFAULT ''"),
                     ("auto_disable_movie", "INTEGER NOT NULL DEFAULT 1")):
        if col not in scols:
            conn.execute(f"ALTER TABLE subscriptions ADD COLUMN {col} {ddl}")
    # 老库迁移：向导已完成的部署视为已启动，保持原有运行行为
    if not conn.execute("SELECT value FROM settings WHERE key='running'").fetchone():
        done = conn.execute("SELECT value FROM settings WHERE key='setup_completed'").fetchone()
        running = 'true' if (done and done[0] == 'true') else 'false'
        conn.execute("INSERT INTO settings VALUES('running',?)", (running,))

    # 定时整理规则表
    conn.execute("""
    CREATE TABLE IF NOT EXISTS auto_move_rules(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_dir TEXT NOT NULL,
        target_dir TEXT NOT NULL,
        keyword TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'default',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL,
        last_run_at REAL,
        last_moved_count INTEGER NOT NULL DEFAULT 0
    )""")
    am_cols = {r["name"] for r in conn.execute("PRAGMA table_info(auto_move_rules)")}
    if "mode" not in am_cols:
        conn.execute("ALTER TABLE auto_move_rules ADD COLUMN mode TEXT NOT NULL DEFAULT 'default'")
    if "allow_english" not in am_cols:
        conn.execute("ALTER TABLE auto_move_rules ADD COLUMN allow_english INTEGER NOT NULL DEFAULT 0")

    # 活动任务唯一性：同一分享码同时最多一个 排队/转存中/已提交 任务。
    # 先清理历史竞态产生的重复活动行（保留优先级最高、最早入队的一条），再建部分唯一索引；
    # 运行期的并发入队冲突由 pipeline 捕获 IntegrityError 转判重兜底
    dup = conn.execute(
        "SELECT share_code, count(*) c FROM transfers"
        " WHERE status IN ('queued','running','submitted') GROUP BY share_code HAVING c > 1"
    ).fetchall()
    for r in dup:
        keep = conn.execute(
            "SELECT id FROM transfers WHERE share_code=? AND status IN ('queued','running','submitted')"
            " ORDER BY priority DESC, id ASC LIMIT 1", (r["share_code"],)).fetchone()
        conn.execute(
            "DELETE FROM transfers WHERE share_code=? AND status IN ('queued','running','submitted')"
            " AND id != ?", (r["share_code"], keep["id"]))
        _warnings.append(f"清理同分享码的重复活动任务（share_code={r['share_code']}，"
                         f"保留 #{keep['id']}）")
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_transfers_active_code ON transfers(share_code)"
            " WHERE status IN ('queued','running','submitted')")
    except sqlite3.Error:
        pass  # 极端情况下仍有冲突数据：跳过索引，竞态兜底由 pipeline 捕获冲突

    # 迁移：脱钩定时自动备份的默认 TG Bot 开通状态；
    # 旧版本升级时 auto_backup_channels 尚不存在，若此前已开启定时备份，回填 TG Bot 通道，
    # 避免升级后调度条件因通道为空而静默跳过（用户后续手动清空则不再回填）。
    try:
        row_ch = conn.execute("SELECT value FROM settings WHERE key='auto_backup_channels'").fetchone()
        row_en = conn.execute("SELECT value FROM settings WHERE key='auto_backup_enabled'").fetchone()
        try:
            enabled = bool(json.loads(row_en[0])) if row_en else False
        except Exception:
            enabled = False
        if row_ch is None:
            if enabled:
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES('auto_backup_channels','[\"tgbot\"]')"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        elif row_ch[0] in ('["tgbot"]', "['tgbot']") and not enabled:
            conn.execute("UPDATE settings SET value='[]' WHERE key='auto_backup_channels'")
    except Exception:
        pass

    return _warnings


def _emit_migrate_warnings(messages):
    """锁外补发 _migrate 的告警日志（延迟导入避免循环依赖；失败不影响主流程）。"""
    if not messages:
        return
    try:
        from ..core import logstream
        for w in messages:
            logstream.warn("database", w)
    except Exception:
        pass


