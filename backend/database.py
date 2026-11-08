"""SQLite 数据层：所有表、设置读写、种子数据。同步 sqlite3 + WAL，小数据量足够。"""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date

from . import config

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
    last_ep TEXT DEFAULT '',           -- 最近一次命中的集数标识（S01E48 / 第495集）
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
    "subscription_only": False,       # 开启后仅转存命中订阅关键词的内容
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
    "auto_backup_days": 7,
    "digest_last_sent": "",           # 内部状态：上次摘要日期
    "backup_last_sent": 0,            # 内部状态：上次备份时间戳
    "api_token": "",                  # 对外 API token（留空 = 对外 API 关闭）
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


def init():
    global _conn
    _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")  # WAL 下的安全档位：高频小事务（写日志）收益明显
    _conn.executescript(SCHEMA)
    _migrate()
    _seed()
    _conn.commit()


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
    # 老库迁移：向导已完成的部署视为已启动，保持原有运行行为
    if not _conn.execute("SELECT value FROM settings WHERE key='running'").fetchone():
        done = _conn.execute("SELECT value FROM settings WHERE key='setup_completed'").fetchone()
        running = 'true' if (done and done[0] == 'true') else 'false'
        _conn.execute("INSERT INTO settings VALUES('running',?)", (running,))

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


def restore_payload(data: dict) -> dict:
    """从备份字典恢复全部业务数据（整体覆盖，事务内完成）。

    校验通过后按备份内容重建 6 张业务表；settings 按「整体覆盖但保留
    BACKUP_EXCLUDE_SETTINGS 中的当前值」处理（备份里本来就没有这些项，
    保留是为了恢复后登录态与通知/代理等密钥配置不丢，避免恢复后被锁在门外）；
    恢复完成后强制 running=false（停止状态），避免带着旧快照里的运行标记直接启动。
    返回各表恢复条数。

    与运行中 worker 的竞态防护：恢复前要求系统处于停止状态且无转存中任务；
    transfers 中同分享码的多条活动行（旧版竞态遗留）自动降级为 duplicate，
    避免触发活动任务唯一索引导致恢复被整单拒绝。
    """
    if not isinstance(data, dict) or not isinstance(data.get("_meta"), dict):
        raise ValueError("不是有效的备份文件（缺少 _meta 段）")
    tables = ("channels", "rules", "routes", "blacklist", "subscriptions", "transfers")
    if "settings" not in data:
        raise ValueError("备份缺少 settings 段")
    if not isinstance(data["settings"], dict):
        raise ValueError("备份 settings 段格式不正确")
    for t in tables:
        if t not in data:
            raise ValueError(f"备份缺少 {t} 段")
        if not isinstance(data[t], list):
            raise ValueError(f"备份 {t} 段格式不正确")

    # 与运行中 worker 的竞态防护：恢复前要求系统停止且无转存中任务
    if get_setting("running", False):
        raise ValueError("系统正在运行，请先点击顶栏「停止」再恢复备份")
    if _conn is not None:
        running_rows = _conn.execute(
            "SELECT count(*) c FROM transfers WHERE status='running'").fetchone()["c"]
        if running_rows:
            raise ValueError("有转存中的任务，请等待其完成（或重启程序）后再恢复备份")

    # 列名白名单：备份行的 key 直接拼进 SQL，必须先按真实表结构校验（防恶意备份注入）
    allowed_cols = {t: {r["name"] for r in _conn.execute(f"PRAGMA table_info({t})")}
                    for t in tables}
    for t in tables:
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
            # 先记下需要保留的当前设置（备份不含这些项，恢复后原样带回来）
            keep = {}
            for k in BACKUP_EXCLUDE_SETTINGS:
                row = cur.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
                if row is not None:
                    keep[k] = row["value"]
            seen_active: set[str] = set()
            for t in tables:
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
                    cols = ",".join(r.keys())
                    qs = ",".join("?" * len(r))
                    cur.execute(f"INSERT INTO {t}({cols}) VALUES({qs})", tuple(r.values()))
                # 自增序列对齐到恢复后的最大 id，防止后续新记录与恢复记录撞 id
                try:
                    cur.execute("UPDATE sqlite_sequence SET seq=(SELECT MAX(id) FROM "
                                f"{t}) WHERE name='{t}'")
                    if cur.execute("SELECT changes()").fetchone()[0] == 0 and rows:
                        cur.execute(f"INSERT INTO sqlite_sequence(name,seq) VALUES('{t}',"
                                    f"(SELECT MAX(id) FROM {t}))")
                except sqlite3.OperationalError:
                    pass  # 表无 AUTOINCREMENT 时没有 sqlite_sequence 行，忽略
            cur.execute("DELETE FROM settings")
            for k, v in (data["settings"] or {}).items():
                cur.execute("INSERT INTO settings VALUES(?,?)", (k, str(v)))
            for k, v in keep.items():
                cur.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, v))
            cur.execute("INSERT OR REPLACE INTO settings VALUES('running','false')")
            # 内部运行态不随备份恢复：避免恢复出「暂停中/带自动停止原因/待补全请求」的旧状态
            for k in ("paused", "auto_stop_reason", "backfill_pending"):
                cur.execute("DELETE FROM settings WHERE key=?", (k,))
            cur.execute("DELETE FROM settings WHERE key LIKE 'poll_fail_%'")
            _conn.commit()
            stats = {"settings": len(data["settings"] or {})}
            stats.update({t: len(data[t] or []) for t in tables})
            return stats
        except Exception:
            _conn.rollback()
            raise


def backup_payload() -> dict:
    """全部业务数据 → JSON 备份字典（网页导出与 TG 定时备份共用）。"""
    def dump(sql):
        return [dict(r) for r in query(sql)]

    return {
        "_meta": {"version": 1, "exported_at": time.strftime("%Y-%m-%d %H:%M:%S")},
        "settings": {r["key"]: r["value"] for r in dump("SELECT * FROM settings")
                     if r["key"] not in BACKUP_EXCLUDE_SETTINGS},
        "channels": dump("SELECT * FROM channels"),
        "rules": dump("SELECT * FROM rules"),
        "routes": dump("SELECT * FROM routes"),
        "blacklist": dump("SELECT * FROM blacklist"),
        "subscriptions": dump("SELECT * FROM subscriptions"),
        "transfers": dump("SELECT * FROM transfers ORDER BY id"),
    }
