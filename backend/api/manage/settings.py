"""系统设置、代理/通知测试与历史日志查询导出路由。"""
import re
import time

from fastapi import APIRouter, Depends, HTTPException

from ... import database as db
from ...core import logstream
from ...worker import jobs
from ..deps import require_user
from ._common import SECRET_MASK, _clamp_page, _clean_dir_action, _like_escape

router = APIRouter()


# ---------- settings ----------
_INT_SETTINGS = {
    # 轮询/转存间隔下限按风控保守值设定：115 转存类操作过快会触发风控，
    # TG 网页轮询过快也易被限流，因此分别限制在 15/30 秒以上
    "poll_interval_min": (15, 3600),
    "poll_interval_max": (15, 3600),
    "transfer_interval_min": (30, 1800),
    "transfer_interval_max": (30, 1800),
    "running_timeout_min": (15, 720),
    # 115 单账号每日转存安全上限：1~50（0 不再表示「不限制」）
    "daily_limit": (1, 50),
    "tg_api_id": (0, 2 ** 31 - 1),
    "auto_backup_days": (1, 365),
}
_BOOL_SETTINGS = {"enable_share_transfer", "enable_offline_download",
                  "notify_transfer_success", "schedule_enabled",
                  "subscription_only", "dedup_by_name", "organize_enabled",
                  "share_update_enabled", "quality_policy_enabled", "wash_enabled",
                  "space_guard_enabled",
                  "digest_enabled", "auto_backup_enabled",
                  "bundle_slim_db", "bundle_include_credentials",
                  "bundle_restore_credentials", "bundle_restore_meta"}





def _norm_setting(k: str, v):
    """归一化设置值；非法输入抛 400，避免脏配置让后台任务崩溃。"""
    if k == "tg_api_id":
        # Userbot 未配置时该值为空串：Settings 页会原样回传，必须允许空值，
        # 否则「保存设置」会被误判为非法整数而整体失败；非空值仍走下方整数范围校验。
        if v is None or (isinstance(v, str) and not v.strip()):
            return ""
    if k in _BOOL_SETTINGS:
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)
    if k in _INT_SETTINGS:
        lo, hi = _INT_SETTINGS[k]
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{k} 必须是整数")
        if not (lo <= n <= hi):
            if k == "daily_limit":
                raise HTTPException(400, f"每日转存上限必须在 {lo} ~ {hi} 之间（整数）")
            elif k == "auto_backup_days":
                raise HTTPException(400, f"自动备份间隔必须在 {lo} ~ {hi} 天之间（整数）")
            else:
                raise HTTPException(400, f"{k} 必须在 {lo} ~ {hi} 之间（整数）")
        return n
    if k == "min_size_gb":
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise HTTPException(400, "分享体积下限必须是数字")
        if f < 0 or f > 1000:
            raise HTTPException(400, "分享体积下限必须在 0 ~ 1000 GB 之间")
        return round(f, 2)
    if k == "retry_backoff":
        if not isinstance(v, (list, tuple)) or not v:
            raise HTTPException(400, "重试退避至少需要填写一个大于 0 的秒数")
        vals = []
        for item in v:
            try:
                n = int(float(item))
            except (TypeError, ValueError):
                raise HTTPException(400, "重试退避只能包含数字（秒）")
            # 重试间隔下限 30 秒（前端只提供预设档位，此处兜底防过密重试触发风控）
            if n < 30:
                raise HTTPException(400, "重试退避的秒数不能小于 30（避免过密重试触发风控）")
            vals.append(min(n, 86400))
        return vals[:10]
    if k in ("schedule_start", "schedule_end", "digest_time", "auto_backup_time"):
        s = str(v).strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s):
            raise HTTPException(400, f"{k} 需为 HH:MM 格式（如 08:30）")
        return s
    if k == "auto_backup_channels":
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x).strip() in ("tgbot", "webdav", "local")]
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip() in ("tgbot", "webdav", "local")]
        return []
    if k == "bundle_meta_mode":
        s = str(v or "nfo").strip()
        return s if s in ("nfo", "all", "none") else "nfo"
    if k in ("bundle_export_dest", "bundle_restore_source"):
        s = str(v or "local").strip()
        return s if s in ("local", "webdav") else "local"
    if k == "tg_mode":
        s = str(v).strip()
        if s not in ("polling", "userbot", "both"):
            raise HTTPException(400, "tg_mode 仅支持 polling / userbot / both")
        return s
    if k == "unclassified_action":
        return _clean_dir_action(v)
    if k == "proxy":  # 仅接受 http(s)/socks 代理地址：拦截换行/空格等脏值导致后台请求库解析崩溃。
        # 不拦截内网地址——局域网代理（如 http://192.168.x.x:7890）是合法部署场景，
        # 且本设置仅管理员登录后可改，不构成未认证攻击面
        s = str(v or "").strip()
        if s and not re.match(r"^(https?|socks5h?|socks4)://[^\s]+$", s, re.IGNORECASE):
            raise HTTPException(400, "代理地址需以 http(s):// 或 socks5:// 开头")
        return s
    if k in ("bark_url", "ntfy_url", "webhook_url"):  # 推送地址必须是 http(s)：拦截手滑填错的协议（如 file:）
        s = str(v or "").strip()
        if s and not re.match(r"^https?://[^\s]+$", s, re.IGNORECASE):
            raise HTTPException(400, f"{k} 需为 http(s):// 开头的完整 URL")
        return s.rstrip("/")
    if k == "schedule_jitter_min":
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise HTTPException(400, "开工随机扰动必须是整数（分钟）")
        if not (0 <= n <= 30):
            raise HTTPException(400, "开工随机扰动必须在 0 ~ 30 分钟之间")
        return n
    if k == "batch_rest_enabled":
        return bool(v)
    if k == "batch_rest_count":
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise HTTPException(400, "连存小憩笔数必须是整数")
        if not (2 <= n <= 20):
            raise HTTPException(400, "连存小憩笔数必须在 2 ~ 20 笔之间")
        return n
    if k in ("batch_rest_min_min", "batch_rest_max_min"):
        lbl = "最短" if k == "batch_rest_min_min" else "最长"
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{lbl}小憩时间必须是整数（分钟）")
        if not (1 <= n <= 60):
            raise HTTPException(400, f"{lbl}小憩时间必须在 1 ~ 60 分钟之间")
        return n
    if k == "health_check_interval_min":
        try:
            return max(1, min(60, int(float(v))))
        except (TypeError, ValueError):
            return 10
    if k == "auto_move_mode":
        return "exact" if str(v).strip().lower() == "exact" else "library"
    if k in ("auto_move_season_folder", "auto_move_fuzzy_t2s", "auto_move_enabled",
             "auto_move_scan_subdirs", "auto_move_notify", "auto_move_dedup_by_episode"):
        return bool(v)
    if k == "auto_move_interval_hours":
        try:
            return max(1, min(168, int(float(v))))
        except Exception:
            return 6
    if k == "dedup_cache_ttl_sec":
        try:
            return max(0, min(600, int(float(v))))
        except (TypeError, ValueError):
            return 90
    if k == "quality_min_resolution":
        s = str(v or "").strip().lower()
        if s not in ("", "720p", "1080p", "2160p"):
            raise HTTPException(400, "最低画质仅支持 不限 / 720p / 1080p / 2160p")
        return s
    if k == "wash_archive_dir":
        # 115 网盘虚拟路径：必须以 / 开头；拒绝 .. 段，防止拼出意外目录
        s = str(v or "").strip()
        if s and (not s.startswith("/") or any(p == ".." for p in s.split("/"))):
            raise HTTPException(400, "洗版归档目录需为以 / 开头的 115 网盘路径（如 /媒体库/云下载）")
        return s.rstrip("/") if s.strip("/") else ""
    if v is None:
        return ""
    return v if isinstance(v, str) else str(v)


@router.get("/settings")
def get_settings(_=Depends(require_user)):
    keys = ["tg_mode", "enable_share_transfer", "enable_offline_download",
            "poll_interval_min", "poll_interval_max", "transfer_interval_min",
            "transfer_interval_max", "running_timeout_min", "daily_limit",
            "retry_backoff", "min_size_gb",
            "unclassified_action", "notify_token", "notify_chat_id",
            "notify_transfer_success", "proxy",
            "schedule_enabled", "schedule_start", "schedule_end",
            "schedule_jitter_min", "batch_rest_enabled", "batch_rest_count",
            "batch_rest_min_min", "batch_rest_max_min", "health_check_interval_min",
            "dedup_cache_ttl_sec",
            "tg_api_id", "tg_api_hash", "tg_phone",
            "subscription_only", "dedup_by_name", "organize_enabled",
            "share_update_enabled",
            "quality_policy_enabled", "quality_min_resolution", "wash_enabled", "wash_archive_dir",
            "space_guard_enabled",
            "tmdb_api_key",
            "bark_url", "pushplus_token", "pushplus_topic", "ntfy_url", "ntfy_token",
            "webhook_url", "webhook_preset", "webhook_template",
            "digest_enabled", "digest_time",
            "auto_backup_enabled", "auto_backup_days", "auto_backup_time",
            "auto_backup_channels",
            "bundle_meta_mode", "bundle_slim_db", "bundle_include_credentials",
            "bundle_export_dest", "bundle_restore_source",
            "bundle_restore_credentials", "bundle_restore_meta",
            "api_token", "auto_move_mode", "auto_move_season_folder", "auto_move_fuzzy_t2s",
            "auto_move_dedup_by_episode", "auto_move_enabled"]
    # 未配置过的键必须回代码默认值而非 null：前端会把 null 当「关」显示，
    # 且保存时 bool(null)=false 落库，把默认开启的功能（如每日摘要）悄悄关掉
    defaults = {"enable_share_transfer": True, "enable_offline_download": True,
                "notify_transfer_success": True, "schedule_enabled": False,
                "schedule_start": "22:00", "schedule_end": "08:00",
                "schedule_jitter_min": 5, "batch_rest_enabled": True,
                "batch_rest_count": 3, "batch_rest_min_min": 2, "batch_rest_max_min": 5,
                "health_check_interval_min": 10, "dedup_cache_ttl_sec": 90,
                "subscription_only": False, "dedup_by_name": True,
                "organize_enabled": False, "share_update_enabled": True,
                "quality_policy_enabled": False, "quality_min_resolution": "",
                "wash_enabled": False, "wash_archive_dir": "",
                "space_guard_enabled": True,
                "digest_enabled": True, "digest_time": "21:00",
                "auto_backup_enabled": False, "auto_backup_days": 7, "auto_backup_time": "04:00",
                "auto_backup_channels": [],
                "bundle_meta_mode": "nfo", "bundle_slim_db": True, "bundle_include_credentials": False,
                "bundle_export_dest": "local", "bundle_restore_source": "local",
                "bundle_restore_credentials": False, "bundle_restore_meta": True,
                "auto_move_enabled": False, "auto_move_dedup_by_episode": True,
                "running_timeout_min": 60,
                "webhook_preset": "wecom", "webhook_template": ""}
    # 密钥类键不回传明文（token/api_hash/推送 key/代理凭据等，口径与备份脱敏一致）：
    # 已配置 → 回哨兵值 SECRET_MASK（前端据此显示「已配置」），未配置 → 空串
    _SECRET_KEYS = {"notify_token", "tg_api_hash", "tg_phone", "proxy",
                    "bark_url", "pushplus_token", "ntfy_token", "webhook_url", "api_token", "tmdb_api_key"}
    out = {k: db.get_setting(k, defaults.get(k)) for k in keys}
    for k in _SECRET_KEYS:
        out[k] = SECRET_MASK if str(out.get(k) or "").strip() else ""
    return out


@router.put("/settings")
def put_settings(body: dict, _=Depends(require_user)):
    allowed = {"tg_mode", "enable_share_transfer", "enable_offline_download",
               "poll_interval_min", "poll_interval_max", "transfer_interval_min",
               "transfer_interval_max", "running_timeout_min", "daily_limit",
               "retry_backoff", "min_size_gb",
               "unclassified_action", "notify_token", "notify_chat_id",
               "notify_transfer_success", "proxy",
               "schedule_enabled", "schedule_start", "schedule_end",
               "schedule_jitter_min", "batch_rest_enabled", "batch_rest_count",
               "batch_rest_min_min", "batch_rest_max_min", "health_check_interval_min",
               "dedup_cache_ttl_sec",
               "tg_api_id", "tg_api_hash", "tg_phone",
               "subscription_only", "dedup_by_name", "organize_enabled",
               "share_update_enabled",
               "quality_policy_enabled", "quality_min_resolution", "wash_enabled", "wash_archive_dir",
            "space_guard_enabled",
               "tmdb_api_key",
               "bark_url", "pushplus_token", "pushplus_topic", "ntfy_url", "ntfy_token",
               "webhook_url", "webhook_preset", "webhook_template",
               "digest_enabled", "digest_time",
               "auto_backup_enabled", "auto_backup_days", "auto_backup_time",
               "auto_backup_channels",
               "bundle_meta_mode", "bundle_slim_db", "bundle_include_credentials",
               "bundle_export_dest", "bundle_restore_source",
               "bundle_restore_credentials", "bundle_restore_meta",
               "api_token", "auto_move_mode", "auto_move_season_folder", "auto_move_fuzzy_t2s",
               "auto_move_dedup_by_episode", "auto_move_enabled"}
    # 先整体校验再落库：任一字段非法时不写入，避免半新半旧配置。
    # null 表示「前端从未配置过该键」，跳过不写，防止 bool(null)=false 把默认开启的功能关掉
    # 哨兵值 SECRET_MASK 表示「密钥保持已存值不变」（GET 掩码后的表单整体保存原样回传）
    _SECRET_KEYS = {"notify_token", "tg_api_hash", "tg_phone", "proxy",
                    "bark_url", "pushplus_token", "ntfy_token", "webhook_url", "api_token", "tmdb_api_key"}
    updates = {k: _norm_setting(k, v) for k, v in body.items()
               if k in allowed and v is not None
               and not (k in _SECRET_KEYS and v == SECRET_MASK)}
    # 间隔成对提交时保证 min <= max
    for lo_k, hi_k in (("poll_interval_min", "poll_interval_max"),
                       ("transfer_interval_min", "transfer_interval_max")):
        if lo_k in updates and hi_k in updates and updates[lo_k] > updates[hi_k]:
            updates[lo_k], updates[hi_k] = updates[hi_k], updates[lo_k]
    if "batch_rest_min_min" in updates and "batch_rest_max_min" in updates:
        if updates["batch_rest_min_min"] > updates["batch_rest_max_min"]:
            raise HTTPException(400, f"小憩时间区间有误：最短小憩时间（{updates['batch_rest_min_min']}分）不能大于最长小憩时间（{updates['batch_rest_max_min']}分）")

    # 1. 115防风控定时计划校验：开启时开始时间与结束时间不能相同
    sched_on = updates.get("schedule_enabled")
    if sched_on is None:
        sched_on = bool(db.get_setting("schedule_enabled", False))
    if sched_on:
        s_start = str(updates.get("schedule_start", db.get_setting("schedule_start", "22:00")) or "22:00").strip()
        s_end = str(updates.get("schedule_end", db.get_setting("schedule_end", "08:00")) or "08:00").strip()
        if s_start == s_end:
            raise HTTPException(400, "防风控定时计划的开始时间与结束时间不能相同（如需全天候运行请关闭定时计划开关）！")

    # 2. 定时自动备份校验：仅当勾选 tgbot 途径时强制要求 Telegram Bot 凭据，仅当勾选 webdav 时校验配置
    backup_on = updates.get("auto_backup_enabled")
    if backup_on is None:
        backup_on = bool(db.get_setting("auto_backup_enabled", False))
    if backup_on:
        channels = updates.get("auto_backup_channels")
        if channels is None:
            channels = db.get_setting("auto_backup_channels", [])
        if not isinstance(channels, (list, tuple)):
            channels = [x.strip() for x in str(channels).split(",") if x.strip()]
        if "tgbot" in channels:
            tg_token = str(updates.get("notify_token", db.get_setting("notify_token", "")) or "").strip()
            tg_chat_id = str(updates.get("notify_chat_id", db.get_setting("notify_chat_id", "")) or "").strip()
            if not tg_token or not tg_chat_id:
                raise HTTPException(400, "定时自动备份已勾选 Telegram Bot 途径，请先配置 Telegram Bot Token 与 Chat ID")
        if "webdav" in channels:
            from ...core import webdav
            if not webdav.is_configured():
                raise HTTPException(400, "定时自动备份已勾选 WebDAV 云端途径，请先配置 WebDAV 云端备份并测试通过")

    # 3. 洗版升级必须指定旧版归档目录：否则只能重复转存、无法安全替换旧版本
    wash_on = updates.get("wash_enabled")
    if wash_on is None:
        wash_on = bool(db.get_setting("wash_enabled", False))
    if wash_on:
        archive = str(updates.get("wash_archive_dir", db.get_setting("wash_archive_dir", "")) or "").strip()
        # 根目录 "/" 归一化后为空：视为未配置，避免"归档到根目录"这种危险默认
        if not archive.strip("/"):
            raise HTTPException(400, "开启「洗版升级」必须先填写旧版归档目录（如 /媒体库/洗版归档），"
                                     "升级后的旧版本会移入该目录而不是被删除")

    for k, v in updates.items():
        db.set_setting(k, v)
    logstream.info("system", f"系统设置已更新（共 {len(updates)} 项）" if updates else "系统设置已保存（无变更）")
    return {"ok": True}


@router.post("/settings/proxy/test")
def test_proxy(body: dict = None, _=Depends(require_user)):
    """测试 TG 代理连通性（用输入框当前值，未保存也可直接测）。"""
    body = body or {}
    proxy = str(body.get("proxy") or "").strip()
    if not proxy or proxy == SECRET_MASK:
        # 输入框是掩码（未改动）或为空：回落到已保存配置
        proxy = str(db.get_setting("proxy", "") or "").strip()
    else:
        # 与保存入口（_norm_setting）同一格式校验：防止把任意 http://内网IP:端口 当代理做内网探测
        proxy = _norm_setting("proxy", proxy)
        if not proxy:
            raise HTTPException(400, "代理地址不能为空")
    t0 = time.monotonic()
    ok, detail = jobs.test_proxy(proxy)
    latency_ms = round((time.monotonic() - t0) * 1000)
    now_ts = time.time()
    if not ok:
        raise HTTPException(400, detail)
    return {"ok": True, "detail": detail, "latency_ms": latency_ms, "tested_at": now_ts}


@router.post("/settings/notify/test")
def test_notify(body: dict = None, _=Depends(require_user)):
    """发送测试通知。优先用传入的 Token/Chat ID（未保存也可直接测），否则用已保存配置。"""
    body = body or {}
    token = str(body.get("token") or "").strip()
    if not token or token == SECRET_MASK:
        token = str(db.get_setting("notify_token", "") or "").strip()
    if token and not re.fullmatch(r"[0-9A-Za-z_:\-]+", token):
        raise HTTPException(400, "Telegram Bot Token 格式无效")
    chat_id = str(body.get("chat_id") or "").strip() or str(db.get_setting("notify_chat_id", "") or "").strip()
    t0 = time.monotonic()
    ok, detail = jobs.send_test(token, chat_id)
    latency_ms = round((time.monotonic() - t0) * 1000)
    now_ts = time.time()
    if not ok:
        raise HTTPException(400, detail)
    return {"ok": True, "detail": "测试通知已成功发送至 Telegram", "latency_ms": latency_ms, "tested_at": now_ts}


@router.post("/settings/notify/test/{channel}")
def test_channel(channel: str, _=Depends(require_user)):
    """按渠道发送测试通知（bark / pushplus / ntfy），用已保存的配置。

    返回 ok + 说明；失败时说明里含未配置提示或失败原因，前端直接展示。
    """
    t0 = time.monotonic()
    ok, detail = jobs.test_channel(channel)
    latency_ms = round((time.monotonic() - t0) * 1000)
    now_ts = time.time()
    if not ok:
        raise HTTPException(400, detail)
    return {"ok": True, "detail": detail, "latency_ms": latency_ms, "tested_at": now_ts}


@router.post("/settings/digest/test")
def digest_test(_=Depends(require_user)):
    """立即推送一次每日摘要（内容与定时推送完全一致），用于验证摘要功能与通知渠道。"""
    detail = jobs.notify_detail(jobs.digest_text())
    ok = any(detail.values())
    logstream.info("system", "已手动推送每日摘要（测试）" if ok else
                   "手动推送每日摘要失败：所有通知渠道都未成功（未配置或发送失败）")
    return {"ok": ok, "detail": detail}




# ---------- logs ----------
@router.get("/logs/days")
def log_days(_=Depends(require_user)):
    """最近 7 天按天统计（天、总数、错误数、警告数）。"""
    rows = db.query(
        "SELECT date(created_at,'unixepoch','localtime') d, count(*) c,"
        " sum(CASE WHEN level='error' THEN 1 ELSE 0 END) err,"
        " sum(CASE WHEN level='warn' THEN 1 ELSE 0 END) warn"
        " FROM logs GROUP BY d ORDER BY d DESC LIMIT 7")
    return [dict(r) for r in rows]


@router.get("/logs/modules")
def log_modules(_=Depends(require_user)):
    rows = db.query("SELECT DISTINCT module FROM logs ORDER BY module")
    return [r["module"] for r in rows if r["module"]]


@router.get("/logs")
def get_logs(date: str = "", level: str = "", module: str = "", search: str = "",
             page: int = 1, size: int = 100, _=Depends(require_user)):
    """历史日志查询：按天（服务器本地时区）/级别/模块/关键词筛选，最多回溯 7 天。"""
    from datetime import datetime, timedelta
    page, size = _clamp_page(page, size, 500)
    where, params = ["1=1"], []
    if date:
        try:
            start = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "日期格式应为 YYYY-MM-DD")
        where += ["created_at>=?", "created_at<?"]
        params += [start.timestamp(), (start + timedelta(days=1)).timestamp()]
    else:
        where.append("created_at>=?")
        params.append(time.time() - 7 * 86400)
    if level:
        where.append("level=?"); params.append(level)
    if module:
        where.append("module=?"); params.append(module)
    if search:
        where.append("message LIKE ? ESCAPE '\\'")
        params.append(f"%{_like_escape(search.strip()[:100])}%")
    w = " AND ".join(where)
    total = db.query(f"SELECT count(*) c FROM logs WHERE {w}", tuple(params), one=True)["c"]
    rows = db.query(f"SELECT * FROM logs WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?",
                    tuple(params + [size, (page - 1) * size]))
    return {"total": total, "items": [dict(r) for r in rows]}


@router.get("/logs/export")
def export_logs(date: str = "", _=Depends(require_user)):
    """导出某天（默认最近 7 天）日志为文本文件。"""
    from datetime import datetime, timedelta
    from fastapi.responses import PlainTextResponse
    if date:
        try:
            start = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "日期格式应为 YYYY-MM-DD")
        rows = db.query("SELECT * FROM logs WHERE created_at>=? AND created_at<? ORDER BY id",
                        (start.timestamp(), (start + timedelta(days=1)).timestamp()))
        fname = f"logs_{date}.txt"
    else:
        rows = db.query("SELECT * FROM logs WHERE created_at>=? ORDER BY id",
                        (time.time() - 7 * 86400,))
        fname = "logs_7days.txt"
    lines = [f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['created_at']))}"
             f" [{r['level']}] {r['module']} {r['message']}" for r in rows]
    return PlainTextResponse(
        "\n".join(lines),
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})
