"""SQLite 默认模式定义与初始种子数据。"""
import json
import time

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
    min_quality TEXT DEFAULT '',       -- 该订阅最低画质（空=跟随全局设置）
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
    sub_id INTEGER NOT NULL DEFAULT 0,     -- 命中的订阅 id（质量策略按订阅+同一集比较）
    episode_key TEXT NOT NULL DEFAULT '',  -- 集标识（S01E05 / 第5集），空=无法识别
    quality_score INTEGER NOT NULL DEFAULT 0, -- 画质分数（0=未知，不参与比较）
    quality_tag TEXT NOT NULL DEFAULT '',  -- 画质标签（1080p WEB H265）
    wash INTEGER NOT NULL DEFAULT 0,       -- 1=洗版升级任务
    wash_from INTEGER NOT NULL DEFAULT 0,  -- 被洗版的旧成功任务 id
    dest_cid INTEGER NOT NULL DEFAULT 0,   -- 成功后记录落盘目录/顶层条目，供后续洗版定位
    dest_ids TEXT NOT NULL DEFAULT '',     -- 落盘顶层条目 id（JSON 数组）
    created_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_transfers_status ON transfers(status);
CREATE INDEX IF NOT EXISTS idx_transfers_code ON transfers(share_code);
CREATE INDEX IF NOT EXISTS idx_transfers_created ON transfers(created_at);
-- 消息列表页按 (channel, message_id) 关联转存状态（相关子查询），无索引会随记录量线性变慢
CREATE INDEX IF NOT EXISTS idx_transfers_channel_msg ON transfers(channel, message_id);
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
    media_type TEXT NOT NULL DEFAULT 'auto',   -- auto | movie | tv：movie 规则跳过带集数标识文件
    dir_match INTEGER NOT NULL DEFAULT 0,      -- 1=按父目录名匹配（云下载目录专用，仅任务子目录参与）
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
    undo_note TEXT DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_records_file ON organized_records(file_id, undone);
CREATE INDEX IF NOT EXISTS idx_org_records_orig ON organized_records(orig_name, undone);
CREATE INDEX IF NOT EXISTS idx_org_records_batch ON organized_records(batch_id);
CREATE TABLE IF NOT EXISTS dedupe_rules(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT DEFAULT '',
    source_dir TEXT NOT NULL,
    include_subdirs INTEGER NOT NULL DEFAULT 1,
    min_size_mb INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    last_run_at REAL,
    last_deleted_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS dedupe_records(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT DEFAULT '',
    rule_id INTEGER NOT NULL DEFAULT 0,
    file_id TEXT DEFAULT '',
    file_name TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    sha1 TEXT DEFAULT '',
    size INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'deleted',
    note TEXT DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dedupe_records_batch ON dedupe_records(batch_id);
CREATE INDEX IF NOT EXISTS idx_dedupe_records_file ON dedupe_records(file_id);
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
-- ---- 扫描索引（三套预览共用：分流整理 / 目录整理刮削 / 重复清理）----
-- 目录级索引：TTL 内直接读索引零请求；本程序写操作后置 dirty 触发增量重扫
CREATE TABLE IF NOT EXISTS scan_dirs(
    cid INTEGER PRIMARY KEY,
    path TEXT DEFAULT '',
    entry_count INTEGER NOT NULL DEFAULT 0,
    last_scan_at REAL,
    last_full_at REAL,
    dirty INTEGER NOT NULL DEFAULT 0
);
-- 条目级索引：cid+file_id 唯一，gone_at 标记已消失条目（软删，便于判重与续扫）
CREATE TABLE IF NOT EXISTS scan_entries(
    cid INTEGER NOT NULL,
    file_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    is_dir INTEGER NOT NULL DEFAULT 0,
    size INTEGER NOT NULL DEFAULT 0,
    sha1 TEXT NOT NULL DEFAULT '',
    parent_cid TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT '',
    uptime REAL NOT NULL DEFAULT 0,
    seen_at REAL NOT NULL DEFAULT 0,
    gone_at REAL,
    PRIMARY KEY(cid, file_id)
);
CREATE INDEX IF NOT EXISTS idx_scan_entries_file ON scan_entries(file_id);
CREATE INDEX IF NOT EXISTS idx_scan_entries_uptime ON scan_entries(cid, uptime);
CREATE INDEX IF NOT EXISTS idx_scan_entries_path ON scan_entries(path);
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
    # ---- 订阅质量策略与洗版（默认全关，空策略时行为与旧版完全一致）----
    "quality_policy_enabled": False,  # 质量策略总开关：按画质择优/过滤低画质
    "quality_min_resolution": "",     # 最低画质：'' 不限制 | 720p | 1080p | 2160p
    "wash_enabled": False,            # 洗版升级：同集出现更高画质时自动升级
    "wash_archive_dir": "",           # 洗版旧版归档目录（115 路径，留空则洗版不可用）
    # ---- 空间门闸（空间告急时整队列挂起，任务保持排队、不判失败、不计重试次数）----
    "space_guard_enabled": True,      # 空间预检/自动暂停总开关（关闭后完全沿用旧行为）
    "space_blocked": False,           # 内部状态：队列是否因空间不足挂起中
    "space_blocked_reason": "",       # 内部状态：挂起原因（展示用）
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
    "auto_backup_channels": [],       # 定时自动备份途径：tgbot, webdav, local（默认不自动勾选，须用户主动勾选）
    "bundle_meta_mode": "nfo",
    "bundle_slim_db": True,
    "bundle_include_credentials": False,
    "bundle_export_dest": "local",
    "bundle_restore_source": "local",
    "bundle_restore_credentials": False,
    "bundle_restore_meta": True,
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
    "media_scrape_schedule_enabled": False,   # 定时自动媒体刮削开关
    "media_organize_schedule_type": "interval", # 定时整理调度方式：interval(固定间隔) 或 fixed_times(每天固定时间点)
    "media_organize_schedule_hours": 12,      # 定时整理周期（小时）
    "media_organize_fixed_times": '["06:00", "12:00", "18:00"]', # 每天固定整理时间点
    "media_organize_last_run": 0,             # 上次目录整理时间戳
    "media_scrape_last_run": 0,               # 上次媒体刮削时间戳
}

DEFAULT_RULES = [
    # 优先级 = 判定顺序（升序，首条命中生效）。原则：
    #   1) 越明确的特征越靠前（少儿最明确 → 字幕组标签 → 集数 → 年份/画质兜底）；
    #   2) 兜底规则（画质、裸年份、「电影」二字）放最后，避免抢走剧集/动漫；
    #   3) 分类名本身（如「电视剧」）也能命中，方便频道正文或直发消息直接写分类。
    # ── 少儿（特征词明确，置于最前，避免被"动画"关键词抢归动漫）──
    (r"(少儿|儿童|幼儿|亲子|启蒙|幼教|益智|儿歌|童话|动画片|低龄)", "少儿", 8),
    # ── 动漫（字幕组/压制组标签最可靠，优先级最高）──
    (r"[\[【](ANi|LoliHouse|SweetSub|北宇治|桜都|NC-Raws|Kamigami|DMG|Jsum|VARYG|Kirara|幻樱|"
     r"HS-workshop|天月动字幕组|SubPIG|喵萌奶茶屋|喵萌|Nekomoe|MingY|B-Global|Baha|baha|AI-Raws|KISSSUB|千夏|MCABC|"
     r"Lilith-Raws|Sakurato|Airota|Ohys-Raws|ReinForce|Moozzi2|Snow-Raws|UHA-WINGS|银光|诸神|华盟|澄空|"
     r"雪飘|轻之国度|极影|动漫国|风之圣殿|悠哈璃羽|幻月|天空树|DHR|HYSUB|JYFanSub|XKsub|Nekomoe kissaten)"
     r"[\]】]", "动漫", 10),
    (r"(新番|番剧|国创|国漫|日漫|美漫|连载动画|TV动画|WEB动画|季度合集|动画|动漫)", "动漫", 11),
    (r"(OVA|OAD|剧场版|动画电影|番外篇)", "动漫", 12),
    # ── 纪录片（特征词明确，放在电视剧"全集/完结"之前，避免"全6集"抢先命中）──
    (r"(纪录片|纪实|documentary|bbc|nhk|pbs|discovery|探索频道|国家地理|历史频道|行星地球|蓝色星球)",
     "纪录片", 15),
    # ── 电视剧 ──
    (r"[Ss]\d{1,2}\s?[Ee]\d{1,3}", "电视剧", 20),
    (r"第\s?\d{1,4}\s?[集话話回]", "电视剧", 21),
    (r"(全\s?\d+\s?集|全集|完结|更新至\s?第?\s?\d+\s?集|连载至\s?第?\s?\d+\s?集|全\s?\d+\s?季)",
     "电视剧", 22),
    (r"(season\s?\d{1,2}|(?<![a-z])ep\d{1,4}|(?<![a-z0-9])[Ss]\d{1,2}(?![a-z0-9])|第\s?\d{1,2}\s?季)",
     "电视剧", 23),
    (r"(电视剧|连续剧|国剧|美剧|日剧|韩剧|泰剧|港剧|台剧|网剧|短剧|情景喜剧|单元剧)", "电视剧", 24),
    # 通用的带方括号字幕组标记（不少日剧/韩剧也用，故排在剧集特征之后兜底）
    (r"[\[【][^\]】]{0,24}(字幕组|字幕社|压制组|[Rr]aws)[^\]】]{0,12}[\]】]", "动漫", 25),
    # ── 综艺（期数 / 年份季格式 S2024E15）──
    (r"第\s?\d+\s?期", "综艺", 30),
    (r"[Ss]\d{4}\s?[Ee]\d{1,3}", "综艺", 31),
    (r"(综艺|真人秀|脱口秀|演唱会|晚会|选秀|音综|访谈|喜剧大赛|舞台剧)", "综艺", 32),
    # ── 电影（括号/点分隔的年份更准确，画质标识与分类名兜底）──
    (r"[（(\.](19|20)\d{2}[）)\.]", "电影", 40),
    (r"(1080p|2160p|720p|480p|4k|8k|uhd|bluray|blu-ray|bdrip|remux|web-?dl|webrip|hdtv|hdr|"
     r"dolby|杜比视界|hevc|h\.?265|x265|x264|10bit)", "电影", 41),
    (r"(电影|院线|影版|电影版|蓝光原盘)", "电影", 42),
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
    "预告", "先导片", "花絮", "幕后花絮", "制作特辑", "片花", "探班",
    # 枪版 / 抢先版（盗摄或低画质）
    "枪版", "TS版", "TC版", "CAM版", "屏摄", "抢版", "抢先版", "盗摄",
    # 解说 / 速看 / 混剪（短视频二创，不是完整资源）
    "影视解说", "速看", "混剪",
    # 广告 / 推广 / 引流
    "广告", "推广", "招商", "招募", "兼职", "刷单", "返利", "优惠券", "引流",
    "加群", "群号", "客服", "私信", "代下", "有偿", "付费进群", "收费资源",
    "淘宝", "拼多多",
    # 试看 / 样片
    "试看", "样片", "样本",
]




def _seed(conn=None):
    if conn is None:
        from .. import database as db
        conn = db._conn
    now = time.time()
    for k, v in DEFAULT_SETTINGS.items():
        if not conn.execute("SELECT 1 FROM settings WHERE key=?", (k,)).fetchone():
            conn.execute("INSERT INTO settings VALUES(?,?)", (k, json.dumps(v, ensure_ascii=False)))
    if not conn.execute("SELECT 1 FROM rules").fetchone():
        for pat, cat, pri in DEFAULT_RULES:
            conn.execute("INSERT INTO rules(pattern,category,priority) VALUES(?,?,?)", (pat, cat, pri))
    if not conn.execute("SELECT 1 FROM routes").fetchone():
        for cat, d in DEFAULT_ROUTES:
            conn.execute("INSERT INTO routes(category,target_dir,offline_dir) VALUES(?,?,'')", (cat, d))
    if not conn.execute("SELECT 1 FROM blacklist").fetchone():
        for kw in DEFAULT_BLACKLIST_KEYWORDS:
            conn.execute("INSERT INTO blacklist(type,value,note,created_at) VALUES('keyword',?,'',?)", (kw, now))
