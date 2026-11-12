"""SQLite 数据层：所有表、设置读写、种子数据。同步 sqlite3 + WAL，小数据量足够。"""
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from . import config

logger = logging.getLogger("database")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS channels(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,               -- polling | userbot
    name TEXT NOT NULL,               -- 频道名（不含 @）
    enabled INTEGER NOT NULL DEFAULT 1,
    sub_only INTEGER NOT NULL DEFAULT 0,  -- 1=仅转存命中订阅关键词的内容（按频道独立设置）
    last_message_id INTEGER NOT NULL DEFAULT 0,
    last_fetch_at REAL,
    msg_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rules(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    category TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS routes(
    category TEXT PRIMARY KEY,
    target_dir TEXT NOT NULL,          -- 分享转存目标目录；'skip' 表示不转存
    offline_dir TEXT NOT NULL DEFAULT ''  -- 云下载目标目录；留空=与分享转存相同；'skip' 表示不云下载
);
CREATE TABLE IF NOT EXISTS blacklist(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,                -- channel | keyword | file_pattern
    value TEXT NOT NULL,
    note TEXT DEFAULT '',
    created_at REAL NOT NULL,
    UNIQUE(type, value)
);
CREATE TABLE IF NOT EXISTS blacklist_hits(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT, message_id INTEGER,
    text TEXT, share_code TEXT,
    rule_type TEXT, rule_value TEXT,   -- 命中的规则
    stage TEXT,                        -- message / share_info
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL UNIQUE,      -- 匹配关键词（对消息标题/正文做不区分大小写的子串匹配）
    note TEXT DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    aliases TEXT DEFAULT '',           -- 别名（JSON 数组）：TMDB 原始名等，任一命中即算命中
    tmdb_id INTEGER,                   -- TMDB 条目 ID（仅用于展示/追溯）
    kind TEXT DEFAULT '',              -- tv | movie | ''
    last_ep TEXT DEFAULT '',           -- 最近一次命中的集数标识（S01E48 / 第495集）
    last_season INTEGER,               -- 最近命中的季号（结构化进度，用于数值比较）
    last_episode INTEGER,              -- 最近命中的集号
    last_title TEXT DEFAULT '',        -- 最近一次命中的标题
    last_match_at REAL,                -- 最近命中时间
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages(
    channel TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    text TEXT,
    processed_at REAL NOT NULL,
    PRIMARY KEY(channel, message_id)
);
CREATE TABLE IF NOT EXISTS transfers(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'share',   -- share 分享转存 | offline 云下载(ed2k/磁力)
    share_code TEXT NOT NULL,
    receive_code TEXT DEFAULT '',
    share_url TEXT DEFAULT '',
    title TEXT DEFAULT '',
    channel TEXT DEFAULT '',
    message_id INTEGER DEFAULT 0,
    category TEXT DEFAULT '',
    target_dir TEXT DEFAULT '',
    size INTEGER DEFAULT 0,
    file_count INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
        -- queued | running | success | failed | blocked | duplicate | skipped
    error TEXT DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at REAL,
    bypass_filters INTEGER NOT NULL DEFAULT 0,
    increment INTEGER NOT NULL DEFAULT 0,  -- 1=追更任务（同一分享新增集数，只转存新文件）
    priority INTEGER NOT NULL DEFAULT 0,   -- 队列优先级：1=向前补全（额度有限时优先消耗）
    running_at REAL,                       -- 领取时间：看门狗据此回收卡死的转存任务
    created_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_transfers_status ON transfers(status);
CREATE INDEX IF NOT EXISTS idx_transfers_code ON transfers(share_code);
CREATE INDEX IF NOT EXISTS idx_transfers_created ON transfers(created_at);
CREATE TABLE IF NOT EXISTS daily_quota(
    day TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT, module TEXT, message TEXT, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_created ON logs(created_at);
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
);
CREATE TABLE IF NOT EXISTS media_rules(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT DEFAULT '',
    source_dir TEXT NOT NULL,
    target_dir TEXT NOT NULL DEFAULT '',
    media_type TEXT NOT NULL DEFAULT 'auto',
    organize_enabled INTEGER NOT NULL DEFAULT 1,
    scrape_enabled INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    last_run_at REAL,
    last_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS media_tasks(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_kind TEXT NOT NULL DEFAULT 'scrape',
    rule_id INTEGER NOT NULL DEFAULT 0,
    media_type TEXT DEFAULT '',
    source_dir TEXT DEFAULT '',
    source_cid TEXT DEFAULT '',
    file_id TEXT DEFAULT '',
    orig_name TEXT DEFAULT '',
    orig_path TEXT DEFAULT '',
    parsed_json TEXT DEFAULT '{}',
    target_path TEXT DEFAULT '',
    target_name TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    confidence REAL NOT NULL DEFAULT 0,
    candidates_json TEXT DEFAULT '[]',
    selected_tmdb_id INTEGER,
    selected_kind TEXT DEFAULT '',
    selected_title TEXT DEFAULT '',
    selected_year TEXT DEFAULT '',
    selected_poster TEXT DEFAULT '',
    select_source TEXT DEFAULT '',
    tg_chat_id TEXT DEFAULT '',
    tg_message_id INTEGER DEFAULT 0,
    tg_batch_id TEXT DEFAULT '',
    local_meta_dir TEXT DEFAULT '',
    error TEXT DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    batch_id TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_media_tasks_status ON media_tasks(status, task_kind);
CREATE INDEX IF NOT EXISTS idx_media_tasks_batch ON media_tasks(tg_batch_id);
CREATE TABLE IF NOT EXISTS organized_records(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER DEFAULT 0,
    file_id TEXT DEFAULT '',
    parent_cid TEXT DEFAULT '',
    sha1 TEXT DEFAULT '',
    size INTEGER DEFAULT 0,
    orig_path TEXT DEFAULT '',
    orig_name TEXT DEFAULT '',
    dest_cid TEXT DEFAULT '',
    dest_path TEXT DEFAULT '',
    dest_name TEXT DEFAULT '',
    action TEXT DEFAULT '',
    tmdb_id INTEGER,
    batch_id TEXT DEFAULT '',
    undone INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_records_file ON organized_records(file_id, undone);
CREATE INDEX IF NOT EXISTS idx_org_records_orig ON organized_records(orig_name, undone);
CREATE INDEX IF NOT EXISTS idx_org_records_batch ON organized_records(batch_id);
CREATE TABLE IF NOT EXISTS tmdb_cache(
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    expires_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS media_tg_batches(
    batch_id TEXT PRIMARY KEY,
    chat_id TEXT DEFAULT '',
    console_message_id INTEGER DEFAULT 0,
    total_confirm INTEGER DEFAULT 0,
    pushed_confirm INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);
"""

LOG_RETENTION_DAYS = 7  # 日志保留天数，到期自动永久删除

DEFAULT_SETTINGS = {
    "setup_completed": False,
    "running": False,                 # 总开关：向导完成后由用户点击「启动运行」
    "web_password_hash": "",          # 首次登录用环境变量初始化
    "tg_mode": "polling",             # polling | userbot | both
    "enable_share_transfer": True,    # 115 分享转存开关
    "enable_offline_download": True,  # ED2K/磁力云下载开关
    "poll_interval_min": 30,
    "poll_interval_max": 120,
    "transfer_interval_min": 30,
    "transfer_interval_max": 90,
    "running_timeout_min": 60,        # 看门狗阈值：running 任务超过该分钟数无心跳才重新排队
    "daily_limit": 50,
    "retry_backoff": [60, 300, 1800],
    "min_size_gb": 0.2,
    "unclassified_action": "skip",    # skip | 目标目录路径
    "notify_token": "",
    "notify_chat_id": "",
    "notify_transfer_success": True,  # 转存成功自动 TG 通知
    "schedule_enabled": False,        # 定时计划开关：开启后仅允许时段内转存
    "schedule_start": "22:00",        # 允许转存开始时间
    "schedule_end": "08:00",          # 允许转存结束时间（支持跨零点）
    "proxy": "",                      # 仅 TG 流量
    "tg_api_id": "",
    "tg_api_hash": "",
    "tg_phone": "",
    "cookie_ok": None,                # 115 cookie 状态
    "userbot_ok": None,               # userbot 登录状态
    # ---- 订阅追剧 ----
    "subscription_only": False,       # 全局兜底：仅频道不存在时使用，正常以频道级 sub_only 为准
    # ---- 转存增强 ----
    "dedup_by_name": True,            # 转存前检查目标目录已有同名内容，命中判重跳过
    "share_update_enabled": True,     # 分享追更：同一分享链接新增集数时自动增量转存（只存新文件）
    "organize_enabled": False,        # 转存后把剧集文件归入 Season N 子目录
    # ---- TMDB 刮削 ----
    "tmdb_api_key": "",               # TMDB API Key（订阅页搜索/分类浏览用，刮削功能已移除）
    # ---- 通知渠道 ----
    "bark_url": "",                   # Bark 推送（https://api.day.app/你的key）
    "pushplus_token": "",             # PushPlus token
    "pushplus_topic": "",             # PushPlus 群组编码（可选，一对多群发用，留空发本人）
    "ntfy_url": "",                   # ntfy 主题完整 URL（https://ntfy.sh/你的主题）
    "ntfy_token": "",                 # ntfy 访问令牌（可选，自建开启访问控制时填）
    # ---- 定时任务 ----
    "digest_enabled": True,           # 每日摘要通知
    "digest_time": "21:00",
    "auto_backup_enabled": False,     # 定时把数据备份 JSON 推送到 TG
    "auto_backup_days": 7,            # 备份间隔天数（1~365）
    "auto_backup_time": "04:00",       # 备份推送时间（HH:MM，默认凌晨 04:00）
    "digest_last_sent": "",           # 内部状态：上次摘要日期
    "backup_last_sent": 0,            # 内部状态：上次备份时间戳
    "backup_last_sent_date": "",      # 内部状态：上次备份日期 YYYY-MM-DD
    "api_token": "",                  # 对外 API token（留空 = 对外 API 关闭）
    # ---- 115 防风控与拟人化 ----
    "schedule_jitter_min": 5,          # 开工随机扰动（分钟，0~30）
    "batch_rest_enabled": True,        # 拟人化连存小憩开关
    "batch_rest_count": 3,             # 连续完成 N 笔后小憩
    "batch_rest_min_min": 2,           # 小憩最短时长（分钟）
    "batch_rest_max_min": 5,           # 小憩最长时长（分钟）
    "health_check_interval_min": 10,   # 后台健康检查周期（分钟，默认10）
    "dedup_cache_ttl_sec": 90,         # 同名查重目录缓存（秒，默认90）
    # ---- 定时文件整理（按目录映射移动中文文件）----
    "auto_move_enabled": False,       # 定时整理总开关
    "auto_move_schedule_type": "interval", # 整理调度方式：interval 固定间隔 | fixed_times 每天固定时间点
    "auto_move_interval_hours": 6,    # 整理轮询间隔（小时）
    "auto_move_fixed_times": '["06:00", "12:00", "18:00"]', # 每天固定时间点（JSON数组）
    "auto_move_scan_subdirs": True,   # 穿透源目录下的子文件夹
    "auto_move_notify": True,         # 整理完成后推送通知
    "auto_move_last_run": 0,          # 上次定时整理时间戳
    "auto_move_mode": "library",      # 整理模式：library 总库智能分流 | exact 一对一精确匹配
    "auto_move_season_folder": True,  # 季目录分流：若剧集目录下存在对应季文件夹（如 Season 1/第一季），自动归入
    "auto_move_fuzzy_t2s": True,      # 简繁互通：标题匹配时自动兼容繁体与简体
    "auto_move_dedup_by_episode": True,  # 按季集防重：同剧目标已有同季同集视频时跳过，防止不同命名重复归档
    # ---- 整理刮削（独立双模块）----
    "media_append_tmdb_tag": True,    # 规范目录名追加 [tmdbid=xxx]
    "media_organize_template_movie": "{title} ({year}){tmdb_tag}/{title} ({year}){part_tag}{res_tag}{ext}",
    "media_organize_template_tv": "{title} ({year}){tmdb_tag}/Season {season_00}/{title} - S{season_00}E{episode_00}{part_tag}{ext}",
    "media_conflict_policy": "skip",  # skip | replace_if_better | keep_both
    "media_move_subtitles": True,     # 配套字幕随主视频同步重命名并移动
    "media_skip_extras": True,        # 自动识别并跳过预告/样片/广告等非正片杂项
    "media_extra_min_mb": 50,         # 花絮/样片体积阈值（MB）
    "media_clean_empty_dirs": True,   # 移动整理后清理遗留空目录
    "media_scrape_confidence_threshold": 85,  # 自动刮削高置信分数线（低于则进待确认）
    "media_scrape_margin_threshold": 12,      # 领先第二候选的最小分差
    "media_scrape_write_local_nfo": True,     # 仅在本地持久化生成标准 .nfo（零 115 上传）
    "media_scrape_download_poster": True,     # 仅在本地保存海报与背景图（零 115 上传）
    "media_scrape_wait_seconds": 0,           # 待确认等待秒数（默认 0：直接跳过进列表）
    "media_scrape_tg_batch_limit": 10,        # 单轮刮削结束后自动推送的 TG 待确认卡片上限
    "media_local_meta_dir": "",               # 留空默认使用 DATA_DIR/media_meta
    "media_op_delay_min": 0.8,                # 115 写操作之间最小随机间隔（秒）
    "media_op_delay_max": 2.0,                # 115 写操作之间最大随机间隔（秒）
    "media_organize_schedule_enabled": False, # 定时自动目录整理开关
    "media_organize_schedule_hours": 12,      # 定时整理周期（小时）
    "media_organize_last_run": 0,             # 上次目录整理时间戳
    "media_scrape_last_run": 0,               # 上次媒体刮削时间戳
}

DEFAULT_RULES = [
    # ── 少儿（特征词明确，置于最前，避免被"动画"关键词抢归动漫）──
    (r"(少儿|儿童|幼儿|亲子|启蒙)", "少儿", 8),
    # ── 动漫（字幕组标签最可靠，优先级最高）──
    (r"\[(ANi|LoliHouse|SweetSub|北宇治|桜都|NC-Raws|Kamigami|DMG|Jsum|VARYG|Kirara|幻樱|"
     r"HS-workshop|天月动字幕组|SubPIG|喵萌|Nekomoe|MingY|B-Global|Baha|baha|AI-Raws|KISSSUB|千夏|MCABC)\]", "动漫", 10),
    (r"(新番|番剧|季度合集|国创|动画)", "动漫", 11),
    (r"(OVA|OAD|剧场版)", "动漫", 12),
    # ── 纪录片（特征词明确，放在电视剧"全集/完结"之前，避免"全6集"抢先命中）──
    (r"(纪录片|documentary|bbc\.|nhk\.|discovery|国家地理|行星地球|蓝色星球)", "纪录片", 15),
    # ── 电视剧 ──
    (r"[Ss]\d{1,2}\s?[Ee]\d{1,3}", "电视剧", 20),
    (r"第\s?\d+\s?[集话話]", "电视剧", 21),
    (r"(全\s?\d+\s?集|全集|完结|更新至(第)?\s?\d+\s?集)", "电视剧", 22),
    (r"(season\s?\d{1,2}|(?<![a-za-z])EP\d{1,4}|(?<![a-za-z0-9])[Ss]\d{1,2}(?![a-za-z0-9]))", "电视剧", 23),
    # ── 综艺（期数 / 年份季格式 S2024E15）──
    (r"第\s?\d+\s?期", "综艺", 30),
    (r"[Ss]\d{4}\s?[Ee]\d{1,3}", "综艺", 31),
    (r"(综艺|真人秀|演唱会|晚会)", "综艺", 32),
    # ── 电影（括号/点分隔的年份更准确，画质标识兜底）──
    (r"[（(\.](19|20)\d{2}[）)\.]", "电影", 40),
    (r"(1080p|2160p|720p|4k|bluray|blu-ray|remux|web-?dl|hdtv|hdr)", "电影", 41),
]

DEFAULT_ROUTES = [
    ("电影", "/媒体库/电影"),
    ("电视剧", "/媒体库/电视剧"),
    ("动漫", "/媒体库/动漫"),
    ("综艺", "/媒体库/综艺"),
    ("纪录片", "/媒体库/纪录片"),
    ("少儿", "/媒体库/少儿"),
]

# 默认关键词黑名单：阻挡预告/花絮/枪版/广告等非正片与垃圾内容
# 注意均为子串匹配，措辞避免过短（如不用裸 TS/TC，以免误伤含这些字母的英文片名）
DEFAULT_BLACKLIST_KEYWORDS = [
    # 预告 / 花絮 / 幕后（非正片内容）
    "预告", "花絮", "幕后花絮", "制作特辑", "先导片",
    # 枪版 / 低画质抢先版
    "枪版", "TS版", "TC版", "CAM版", "屏摄", "抢版",
    # 广告 / 推广 / 垃圾
    "广告", "推广", "招商", "招募", "兼职", "刷单", "返利", "优惠券",
    # 试看 / 样片
    "试看", "样片", "样本",
]


def _init_db():
    global _conn
    _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")  # WAL 下的安全档位：高频小事务（写日志）收益明显
    # 快速结构完整性校验，拦截损坏页或异常头
    res = _conn.execute("PRAGMA quick_check").fetchone()
    if res and res[0] != "ok":
        raise sqlite3.DatabaseError(f"quick_check failed: {res[0]}")
    _conn.executescript(SCHEMA)
    _migrate()
    _seed()
    _conn.commit()


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


def _migrate():
    """轻量迁移：旧库补新列。"""
    cols = {r["name"] for r in _conn.execute("PRAGMA table_info(transfers)")}
    if "bypass_filters" not in cols:
        _conn.execute("ALTER TABLE transfers ADD COLUMN bypass_filters INTEGER NOT NULL DEFAULT 0")
    if "increment" not in cols:
        _conn.execute("ALTER TABLE transfers ADD COLUMN increment INTEGER NOT NULL DEFAULT 0")
    if "priority" not in cols:
        _conn.execute("ALTER TABLE transfers ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
    if "running_at" not in cols:
        _conn.execute("ALTER TABLE transfers ADD COLUMN running_at REAL")
    if "kind" not in cols:
        _conn.execute("ALTER TABLE transfers ADD COLUMN kind TEXT NOT NULL DEFAULT 'share'")
    rcols = {r["name"] for r in _conn.execute("PRAGMA table_info(routes)")}
    if "offline_dir" not in rcols:
        _conn.execute("ALTER TABLE routes ADD COLUMN offline_dir TEXT NOT NULL DEFAULT ''")
    # 频道级订阅模式开关（旧库无此列，默认全盘转存）
    ccols = {r["name"] for r in _conn.execute("PRAGMA table_info(channels)")}
    if "sub_only" not in ccols:
        _conn.execute("ALTER TABLE channels ADD COLUMN sub_only INTEGER NOT NULL DEFAULT 0")
    # 订阅别名与结构化追更进度（旧库无这些列，默认空值即可兼容）
    scols = {r["name"] for r in _conn.execute("PRAGMA table_info(subscriptions)")}
    for col, ddl in (("aliases", "TEXT DEFAULT ''"),
                     ("tmdb_id", "INTEGER"),
                     ("kind", "TEXT DEFAULT ''"),
                     ("last_season", "INTEGER"),
                     ("last_episode", "INTEGER")):
        if col not in scols:
            _conn.execute(f"ALTER TABLE subscriptions ADD COLUMN {col} {ddl}")
    # 老库迁移：向导已完成的部署视为已启动，保持原有运行行为
    if not _conn.execute("SELECT value FROM settings WHERE key='running'").fetchone():
        done = _conn.execute("SELECT value FROM settings WHERE key='setup_completed'").fetchone()
        running = 'true' if (done and done[0] == 'true') else 'false'
        _conn.execute("INSERT INTO settings VALUES('running',?)", (running,))

    # 定时整理规则表
    _conn.execute("""
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
    am_cols = {r["name"] for r in _conn.execute("PRAGMA table_info(auto_move_rules)")}
    if "mode" not in am_cols:
        _conn.execute("ALTER TABLE auto_move_rules ADD COLUMN mode TEXT NOT NULL DEFAULT 'default'")

    # 活动任务唯一性：同一分享码同时最多一个 排队/转存中/已提交 任务。
    # 先清理历史竞态产生的重复活动行（保留优先级最高、最早入队的一条），再建部分唯一索引；
    # 运行期的并发入队冲突由 pipeline 捕获 IntegrityError 转判重兜底
    dup = _conn.execute(
        "SELECT share_code, count(*) c FROM transfers"
        " WHERE status IN ('queued','running','submitted') GROUP BY share_code HAVING c > 1"
    ).fetchall()
    if dup:
        from .core import logstream  # 延迟导入：core.logstream 反向依赖本模块
    for r in dup:
        keep = _conn.execute(
            "SELECT id FROM transfers WHERE share_code=? AND status IN ('queued','running','submitted')"
            " ORDER BY priority DESC, id ASC LIMIT 1", (r["share_code"],)).fetchone()
        _conn.execute(
            "DELETE FROM transfers WHERE share_code=? AND status IN ('queued','running','submitted')"
            " AND id != ?", (r["share_code"], keep["id"]))
        logstream.warn("database", f"清理同分享码的重复活动任务（share_code={r['share_code']}，"
                                   f"保留 #{keep['id']}）")
    try:
        _conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_transfers_active_code ON transfers(share_code)"
            " WHERE status IN ('queued','running','submitted')")
    except sqlite3.Error:
        pass  # 极端情况下仍有冲突数据：跳过索引，竞态兜底由 pipeline 捕获冲突


def _seed():
    now = time.time()
    for k, v in DEFAULT_SETTINGS.items():
        if not _conn.execute("SELECT 1 FROM settings WHERE key=?", (k,)).fetchone():
            _conn.execute("INSERT INTO settings VALUES(?,?)", (k, json.dumps(v, ensure_ascii=False)))
    if not _conn.execute("SELECT 1 FROM rules").fetchone():
        for pat, cat, pri in DEFAULT_RULES:
            _conn.execute("INSERT INTO rules(pattern,category,priority) VALUES(?,?,?)", (pat, cat, pri))
    if not _conn.execute("SELECT 1 FROM routes").fetchone():
        for cat, d in DEFAULT_ROUTES:
            _conn.execute("INSERT INTO routes(category,target_dir,offline_dir) VALUES(?,?,'')", (cat, d))
    if not _conn.execute("SELECT 1 FROM blacklist").fetchone():
        for kw in DEFAULT_BLACKLIST_KEYWORDS:
            _conn.execute("INSERT INTO blacklist(type,value,note,created_at) VALUES('keyword',?,'',?)", (kw, now))


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


# 备份不含任何可被直接利用的凭据：备份文件可能被转发/共享（或作为文件躺在 TG 对话里），
# 不应携带登录密码哈希、Bot Token、代理凭据等敏感值。恢复时这些项保留当前值不变。
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
}

# 绑定本机文件或引导完成的本地状态键：保留目标实例当前值，避免跨机恢复后把空机器误判为已登录/已配置
BACKUP_LOCAL_STATE_SETTINGS = frozenset({
    "cookie_ok",
    "userbot_ok",
    "setup_completed",
})
BACKUP_KEEP_SETTINGS = frozenset(BACKUP_EXCLUDE_SETTINGS) | BACKUP_LOCAL_STATE_SETTINGS

# 内部运行态键：不随备份导出，恢复时一律重置或删除，避免恢复出旧运行标记或让 TG 复核误报
BACKUP_RUNTIME_SETTINGS = frozenset({
    "running",
    "paused",
    "auto_stop_reason",
    "backfill_pending",
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


def ensure_restore_idle_guard():
    """恢复前状态检查：要求系统停止且无活动中的转存/整理刮削任务。"""
    if get_setting("running", False):
        raise ValueError("系统正在运行，请先点击顶栏「停止」再恢复备份")
    if _conn is not None:
        running_rows = _conn.execute(
            "SELECT count(*) c FROM transfers WHERE status='running'").fetchone()["c"]
        if running_rows:
            raise ValueError("有转存中的任务，请等待其完成（或重启程序）后再恢复备份")
        media_running = _conn.execute(
            "SELECT count(*) c FROM media_tasks WHERE status='running'").fetchone()["c"]
        if media_running:
            raise ValueError("有正在执行的整理或刮削任务，请等待其结束后再恢复备份")


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

    # 列名白名单：备份行的 key 直接拼进 SQL，必须先按真实表结构校验（防恶意备份注入）
    allowed_cols = {t: {r["name"] for r in _conn.execute(f"PRAGMA table_info({t})")}
                    for t in BACKUP_TABLES}
    for t in BACKUP_TABLES:
        for r in (data[t] or []):
            if not isinstance(r, dict):
                raise ValueError(f"{t} 中存在非对象行")
            unknown = set(r.keys()) - allowed_cols[t]
            if unknown:
                raise ValueError(f"备份 {t} 段包含未知列: {', '.join(sorted(unknown))}")

    with _lock:
        try:
            cur = _conn.cursor()
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
                if not isinstance(k, str) or not k or len(k) > 64:
                    raise ValueError("备份 settings 中存在非法键名")
                # 凭据/本机状态与内部运行态一律不采纳备份值，防止替换敏感凭据或误恢复运行态
                if k in BACKUP_KEEP_SETTINGS or is_runtime_setting_key(k):
                    continue
                cur.execute("INSERT INTO settings VALUES(?,?)", (k, str(v)))
            for k, v in keep.items():
                cur.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, v))
            cur.execute("INSERT OR REPLACE INTO settings VALUES('running','false')")
            # 显式清除运行期瞬态键（兼容旧备份结构也无多余残留）
            for k in ("paused", "auto_stop_reason", "backfill_pending"):
                cur.execute("DELETE FROM settings WHERE key=?", (k,))
            cur.execute("DELETE FROM settings WHERE key LIKE 'poll_fail_%'")
            _conn.commit()
            stats = {"settings": len(data["settings"] or {})}
            stats.update({t: len(data[t] or []) for t in BACKUP_TABLES})
            return stats
        except Exception:
            _conn.rollback()
            raise


def backup_payload() -> dict:
    """全部业务数据 → JSON 备份字典（网页导出与 TG 定时备份共用）。"""
    def dump(sql):
        return [dict(r) for r in query(sql)]

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
    try:
        with _lock:
            _conn.commit()
            _conn.backup(dst)

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
        with _lock:
            keep = {}
            for k in BACKUP_KEEP_SETTINGS:
                row = _conn.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
                if row is not None:
                    keep[k] = row["value"]
            cur_meta_row = _conn.execute(
                "SELECT value FROM settings WHERE key='media_local_meta_dir'"
            ).fetchone()
            cur_meta_val = cur_meta_row["value"] if cur_meta_row is not None else None

            src.backup(_conn)
            _migrate()
            _seed()

            # 强制复位运行态
            _conn.execute("INSERT OR REPLACE INTO settings VALUES('running','false')")
            for k in ("paused", "auto_stop_reason", "backfill_pending"):
                _conn.execute("DELETE FROM settings WHERE key=?", (k,))
            _conn.execute("DELETE FROM settings WHERE key LIKE 'poll_fail_%'")

            # 本机自定义元数据目录不盲从来源机器路径，以免跨 OS/容器失效
            if cur_meta_val is not None:
                _conn.execute("INSERT OR REPLACE INTO settings VALUES('media_local_meta_dir',?)", (cur_meta_val,))
            else:
                _conn.execute("DELETE FROM settings WHERE key='media_local_meta_dir'")

            if not restore_credentials:
                qs = ",".join("?" * len(BACKUP_KEEP_SETTINGS))
                _conn.execute(f"DELETE FROM settings WHERE key IN ({qs})", tuple(BACKUP_KEEP_SETTINGS))
                for k, v in keep.items():
                    _conn.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, v))
            elif not _conn.execute("SELECT 1 FROM settings WHERE key='web_password_hash' AND value != ''").fetchone():
                # 兜底防锁死：若导入包意外缺少登录密码哈希，保留当前实例登录凭据
                for k in ("web_username", "web_password_hash"):
                    if k in keep:
                        _conn.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, keep[k]))

            # 运行中任务安全归位
            _conn.execute("UPDATE transfers SET status='queued', running_at=NULL WHERE status='running'")
            for mt in _conn.execute("SELECT * FROM media_tasks WHERE status='running'").fetchall():
                norm = _normalize_restored_media_task(dict(mt))
                _conn.execute("UPDATE media_tasks SET status=? WHERE id=?", (norm["status"], mt["id"]))

            # 跨机重映射本地元数据路径
            if target_meta_root is not None and remap_meta_fn is not None:
                for mt in _conn.execute(
                    "SELECT id, local_meta_dir FROM media_tasks WHERE local_meta_dir != ''"
                ).fetchall():
                    new_dir = remap_meta_fn(mt["local_meta_dir"], source_meta_root, target_meta_root)
                    if new_dir != mt["local_meta_dir"]:
                        _conn.execute("UPDATE media_tasks SET local_meta_dir=? WHERE id=?", (new_dir, mt["id"]))

            _conn.commit()
            return _collect_conn_counts(_conn)
    finally:
        src.close()
