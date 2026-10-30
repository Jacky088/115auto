"""主业务路由：登录/向导/仪表盘/频道/规则/黑名单/转存记录/设置/日志。"""
import time

from fastapi import APIRouter, Depends, HTTPException

from .. import auth, database as db
from ..core import classifier, logstream, pipeline
from ..worker import jobs
from .deps import require_user

router = APIRouter(prefix="/api")


# ---------- auth ----------
@router.post("/auth/login")
def login(body: dict):
    username = body.get("username", "")
    password = body.get("password", "")
    if auth.login_throttled():
        logstream.warn("system", "登录请求被限速（连续失败过多）")
        raise HTTPException(429, "失败次数过多，请 1 分钟后再试")
    stored = db.get_setting("web_password_hash", "")
    if username != "admin" or not auth.verify_password(password, stored):
        auth.record_login_fail()
        logstream.warn("system", "网页登录失败（用户名或密码错误）")
        raise HTTPException(401, "用户名或密码错误")
    logstream.info("system", "网页登录成功")
    return {"token": auth.make_token(username)}


@router.post("/auth/password")
def change_pwd(body: dict, _=Depends(require_user)):
    old, new = body.get("old", ""), body.get("new", "")
    if not auth.verify_password(old, db.get_setting("web_password_hash", "")):
        raise HTTPException(400, "旧密码错误")
    if len(new) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    auth.change_password(new)
    logstream.info("system", "管理员密码已在网页修改")
    return {"ok": True}


# ---------- wizard ----------
@router.get("/wizard/status")
def wizard_status(_=Depends(require_user)):
    return {
        "completed": bool(db.get_setting("setup_completed", False)),
        "cookie_ok": db.get_setting("cookie_ok", None),
        "userbot_ok": db.get_setting("userbot_ok", None),
        "channels": db.query("SELECT count(*) c FROM channels", one=True)["c"],
        "tg_mode": db.get_setting("tg_mode", "polling"),
    }


@router.post("/wizard/complete")
def wizard_complete(_=Depends(require_user)):
    db.set_setting("setup_completed", True)
    logstream.info("wizard", "初始化向导完成，等待用户点击「开始运行」")
    return {"ok": True}


@router.post("/system/start")
def system_start(_=Depends(require_user)):
    """开始运行：启动监听与转存。启动前做就绪检查。"""
    if not db.get_setting("cookie_ok", None):
        raise HTTPException(400, "115 未登录，请先到「账号管理」扫码登录")
    n_ch = db.query("SELECT count(*) c FROM channels WHERE enabled=1", one=True)["c"]
    if n_ch == 0:
        raise HTTPException(400, "尚未添加监控频道，请先到「频道管理」添加")
    db.set_setting("running", True)
    db.set_setting("paused", False)
    logstream.info("system", "▶ 系统开始运行：监控 TG 频道并自动转存")
    return {"ok": True}


@router.post("/system/stop")
def system_stop(_=Depends(require_user)):
    """停止运行：暂停监听与转存（队列与配置保留）。"""
    db.set_setting("running", False)
    logstream.warn("system", "⏸ 系统已停止运行（配置与队列保留，可随时再次启动）")
    return {"ok": True}


@router.get("/system/state")
def system_state(_=Depends(require_user)):
    """运行状态 + 就绪检查结果（供前端显示启动条件）。"""
    cookie_ok = db.get_setting("cookie_ok", None)
    n_ch = db.query("SELECT count(*) c FROM channels WHERE enabled=1", one=True)["c"]
    return {
        "running": bool(db.get_setting("running", False)),
        "setup_completed": bool(db.get_setting("setup_completed", False)),
        "ready": bool(cookie_ok) and n_ch > 0,
        "cookie_ok": cookie_ok,
        "channels": n_ch,
        "paused": bool(db.get_setting("paused", False)),
    }


# ---------- dashboard ----------
@router.get("/dashboard/stats")
def dashboard(_=Depends(require_user)):
    today_quota = db.quota_today()
    today_start = time.time() - (time.time() % 86400) - 8 * 3600  # 近似当日 0 点（东八区）
    rows = db.query(
        "SELECT status, count(*) c FROM transfers WHERE created_at>=? GROUP BY status", (today_start,))
    by_status = {r["status"]: r["c"] for r in rows}
    queued = db.query("SELECT count(*) c FROM transfers WHERE status='queued'", one=True)["c"]
    channels = db.query("SELECT count(*) c FROM channels WHERE enabled=1", one=True)["c"]
    return {
        "today": by_status,
        "today_success": by_status.get("success", 0),
        "today_failed": by_status.get("failed", 0) + by_status.get("blocked", 0),
        "quota": today_quota,
        "daily_limit": db.get_setting("daily_limit", 50),
        "queue": queued,
        "channels": channels,
        "cookie_ok": db.get_setting("cookie_ok", None),
        "userbot_ok": db.get_setting("userbot_ok", None),
    }


@router.get("/dashboard/recent")
def recent_transfers(_=Depends(require_user)):
    return [dict(r) for r in db.query(
        "SELECT id,title,category,target_dir,size,status,created_at,channel FROM transfers"
        " ORDER BY id DESC LIMIT 20")]


# ---------- channels ----------
@router.get("/channels")
def list_channels(_=Depends(require_user)):
    return [dict(r) for r in db.query("SELECT * FROM channels ORDER BY id DESC")]


@router.post("/channels")
def add_channel(body: dict, _=Depends(require_user)):
    name = (body.get("name") or "").strip()
    ctype = body.get("type", "polling")
    # 允许粘贴完整链接
    import re
    m = re.search(r"t\.me/(?:s/)?@?([0-9A-Za-z_]+)", name)
    if m:
        name = m.group(1)
    name = name.lstrip("@")
    if not name:
        raise HTTPException(400, "频道名不能为空")
    if ctype not in ("polling", "userbot"):
        raise HTTPException(400, "type 必须是 polling 或 userbot")
    if db.query("SELECT 1 FROM channels WHERE type=? AND name=?", (ctype, name), one=True):
        raise HTTPException(400, "该频道已存在")
    db.execute("INSERT INTO channels(type,name,created_at) VALUES(?,?,?)", (ctype, name, time.time()))
    logstream.info("channels", f"添加监控频道 @{name}（接入方式：{ctype}）")
    return {"ok": True, "name": name}


@router.put("/channels/{ch_id}")
def update_channel(ch_id: int, body: dict, _=Depends(require_user)):
    if "enabled" in body:
        ch = db.query("SELECT name FROM channels WHERE id=?", (ch_id,), one=True)
        db.execute("UPDATE channels SET enabled=? WHERE id=?", (1 if body["enabled"] else 0, ch_id))
        if ch:
            state = "启用" if body["enabled"] else "停用"
            logstream.info("channels", f"已{state}监控频道 @{ch['name']}")
    return {"ok": True}


@router.delete("/channels/{ch_id}")
def delete_channel(ch_id: int, _=Depends(require_user)):
    ch = db.query("SELECT name FROM channels WHERE id=?", (ch_id,), one=True)
    db.execute("DELETE FROM channels WHERE id=?", (ch_id,))
    if ch:
        logstream.info("channels", f"删除监控频道 @{ch['name']}")
    return {"ok": True}


@router.get("/channels/{ch_id}/messages")
def channel_messages(ch_id: int, _=Depends(require_user)):
    ch = db.query("SELECT * FROM channels WHERE id=?", (ch_id,), one=True)
    if not ch:
        raise HTTPException(404, "频道不存在")
    rows = db.query(
        "SELECT m.*, (SELECT status FROM transfers t WHERE t.channel=m.channel AND t.message_id=m.message_id"
        " LIMIT 1) transfer_status FROM messages m WHERE m.channel=? ORDER BY m.message_id DESC LIMIT 50",
        (ch["name"],))
    return [dict(r) for r in rows]


# ---------- rules & routes ----------
@router.get("/rules")
def get_rules(_=Depends(require_user)):
    return {
        "rules": [dict(r) for r in db.query("SELECT * FROM rules ORDER BY priority, id")],
        "routes": [dict(r) for r in db.query("SELECT * FROM routes ORDER BY category")],
        "unclassified_action": db.get_setting("unclassified_action", "skip"),
    }


@router.put("/rules")
def save_rules(body: dict, _=Depends(require_user)):
    rules = body.get("rules", [])
    db.execute("DELETE FROM rules")
    for i, r in enumerate(rules):
        db.execute("INSERT INTO rules(pattern,category,priority,enabled) VALUES(?,?,?,?)",
                   (r["pattern"], r["category"], int(r.get("priority", i + 1)),
                    1 if r.get("enabled", 1) else 0))
    for r in body.get("routes", []):
        db.execute(
            "INSERT INTO routes(category,target_dir,offline_dir) VALUES(?,?,?)"
            " ON CONFLICT(category) DO UPDATE SET target_dir=excluded.target_dir,"
            " offline_dir=excluded.offline_dir",
            (r["category"], r["target_dir"], r.get("offline_dir", "")))
    if "unclassified_action" in body:
        db.set_setting("unclassified_action", body["unclassified_action"])
    logstream.info("rules", f"分类规则/目录映射已更新（{len(body.get('rules', []))} 条规则, "
                            f"{len(body.get('routes', []))} 个分类映射）")
    return {"ok": True}


@router.post("/rules/reset")
def reset_rules(_=Depends(require_user)):
    """重置为最新内置规则集；缺失的默认分类目录映射自动补齐（不覆盖已有映射）。"""
    from ..database import DEFAULT_RULES, DEFAULT_ROUTES
    db.execute("DELETE FROM rules")
    for pat, cat, pri in DEFAULT_RULES:
        db.execute("INSERT INTO rules(pattern,category,priority) VALUES(?,?,?)", (pat, cat, pri))
    for cat, d in DEFAULT_ROUTES:
        db.execute("INSERT OR IGNORE INTO routes(category,target_dir,offline_dir) VALUES(?,?,'')", (cat, d))
    logstream.info("rules", f"已恢复默认规则（{len(DEFAULT_RULES)} 条），缺失的默认分类目录已补齐")
    return {"ok": True, "count": len(DEFAULT_RULES)}


@router.post("/rules/test")
def test_rule(body: dict, _=Depends(require_user)):
    return classifier.test_text(body.get("text", ""))


# ---------- blacklist ----------
# 注意：固定路径（hits/restore-defaults）必须注册在 /{btype} 之前，否则会被通配匹配
@router.get("/blacklist/hits")
def hits(_=Depends(require_user)):
    return [dict(r) for r in db.query("SELECT * FROM blacklist_hits ORDER BY id DESC LIMIT 200")]


@router.post("/blacklist/hits/{hit_id}/release")
async def release_hit(hit_id: int, _=Depends(require_user)):
    """误杀放行：忽略黑名单重新处理。share_info 环节的命中给对应任务打免检标记重新入队。"""
    hit = db.query("SELECT * FROM blacklist_hits WHERE id=?", (hit_id,), one=True)
    if not hit:
        raise HTTPException(404, "记录不存在")
    if hit["stage"] == "share_info" and hit["share_code"]:
        changed = db.execute(
            "UPDATE transfers SET bypass_filters=1, status='queued', attempts=0, error='',"
            " next_retry_at=NULL WHERE share_code=? AND status='blocked'",
            (hit["share_code"],))
        db.execute("DELETE FROM blacklist_hits WHERE id=?", (hit_id,))
        if changed:
            return {"ok": True, "result": {"requeued": True}}
    # 消息环节（或无对应任务）：删除消息记录后带 bypass 重走管道
    db.execute("DELETE FROM messages WHERE channel=? AND message_id=?", (hit["channel"], hit["message_id"]))
    db.execute("DELETE FROM blacklist_hits WHERE id=?", (hit_id,))
    result = pipeline.process_message(hit["channel"], hit["message_id"], hit["text"], bypass=True)
    logstream.info("blacklist",
                   f"误杀放行: 消息 #{hit['message_id']}（{hit['rule_type']}:{hit['rule_value']}）已忽略黑名单重新处理")
    return {"ok": True, "result": result}


@router.post("/blacklist/restore-defaults")
def restore_blacklist_defaults(_=Depends(require_user)):
    """补齐缺失的默认关键词（不删除、不覆盖用户自定义条目）。"""
    from ..database import DEFAULT_BLACKLIST_KEYWORDS
    added = 0
    for kw in DEFAULT_BLACKLIST_KEYWORDS:
        added += db.execute(
            "INSERT OR IGNORE INTO blacklist(type,value,note,created_at) VALUES('keyword',?,'',?)",
            (kw, time.time()))
    logstream.info("blacklist", f"已补齐默认关键词黑名单（新增 {added} 条）")
    return {"ok": True, "added": added, "total": len(DEFAULT_BLACKLIST_KEYWORDS)}


@router.get("/blacklist/{btype}")
def get_blacklist(btype: str, _=Depends(require_user)):
    if btype not in ("channel", "keyword", "file_pattern"):
        raise HTTPException(404, "未知类型")
    return [dict(r) for r in db.query(
        "SELECT * FROM blacklist WHERE type=? ORDER BY id DESC", (btype,))]


@router.post("/blacklist/{btype}")
def add_blacklist(btype: str, body: dict, _=Depends(require_user)):
    if btype not in ("channel", "keyword", "file_pattern"):
        raise HTTPException(404, "未知类型")
    value = (body.get("value") or "").strip()
    if not value:
        raise HTTPException(400, "内容不能为空")
    # 支持批量（每行一条）
    for line in value.splitlines():
        line = line.strip()
        if line:
            db.execute("INSERT OR IGNORE INTO blacklist(type,value,note,created_at) VALUES(?,?,?,?)",
                       (btype, line, body.get("note", ""), time.time()))
    label = {"keyword": "关键词", "channel": "频道", "file_pattern": "文件名正则"}[btype]
    logstream.info("blacklist", f"添加{label}黑名单: {value.splitlines()[0][:40]}"
                                f"{' 等' if len(value.splitlines()) > 1 else ''}")
    return {"ok": True}


@router.delete("/blacklist/{btype}/{item_id}")
def del_blacklist(btype: str, item_id: int, _=Depends(require_user)):
    row = db.query("SELECT value FROM blacklist WHERE id=? AND type=?", (item_id, btype))
    db.execute("DELETE FROM blacklist WHERE id=? AND type=?", (item_id, btype))
    if row:
        logstream.info("blacklist", f"删除黑名单条目: {row['value'][:40]}")
    return {"ok": True}


# ---------- transfers ----------
@router.get("/transfers")
def list_transfers(status: str = "", category: str = "", search: str = "",
                   page: int = 1, size: int = 30, _=Depends(require_user)):
    where, params = ["1=1"], []
    if status:
        where.append("status=?"); params.append(status)
    if category:
        where.append("category=?"); params.append(category)
    if search:
        where.append("(title LIKE ? OR share_code LIKE ?)"); params += [f"%{search}%", f"%{search}%"]
    w = " AND ".join(where)
    total = db.query(f"SELECT count(*) c FROM transfers WHERE {w}", tuple(params), one=True)["c"]
    rows = db.query(
        f"SELECT * FROM transfers WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(params + [size, (page - 1) * size]))
    return {"total": total, "items": [dict(r) for r in rows]}


@router.post("/transfers/{t_id}/retry")
def retry_transfer(t_id: int, _=Depends(require_user)):
    row = db.query("SELECT status FROM transfers WHERE id=?", (t_id,), one=True)
    if not row:
        raise HTTPException(404, "记录不存在")
    db.execute("UPDATE transfers SET status='queued', attempts=0, error='', next_retry_at=NULL WHERE id=?", (t_id,))
    logstream.info("transfer", f"手动重试任务 #{t_id}（原状态: {row['status']}）")
    return {"ok": True}


@router.post("/transfers/retry-failed")
def retry_all_failed(_=Depends(require_user)):
    """批量重试全部失败任务。"""
    n = db.execute("UPDATE transfers SET status='queued', attempts=0, error='', next_retry_at=NULL"
                   " WHERE status='failed'")
    logstream.info("transfer", f"批量重试 {n} 个失败任务")
    return {"ok": True, "count": n}


@router.delete("/transfers/{t_id}")
def cancel_transfer(t_id: int, _=Depends(require_user)):
    db.execute("DELETE FROM transfers WHERE id=? AND status IN ('queued','running','failed','skipped')", (t_id,))
    return {"ok": True}


# ---------- settings ----------
@router.get("/settings")
def get_settings(_=Depends(require_user)):
    keys = ["tg_mode", "enable_share_transfer", "enable_offline_download",
            "poll_interval_min", "poll_interval_max", "transfer_interval_min",
            "transfer_interval_max", "daily_limit", "retry_backoff", "min_size_gb",
            "unclassified_action", "notify_token", "notify_chat_id",
            "notify_transfer_success", "proxy",
            "schedule_enabled", "schedule_start", "schedule_end",
            "tg_api_id", "tg_api_hash", "tg_phone"]
    return {k: db.get_setting(k) for k in keys}


@router.put("/settings")
def put_settings(body: dict, _=Depends(require_user)):
    allowed = {"tg_mode", "enable_share_transfer", "enable_offline_download",
               "poll_interval_min", "poll_interval_max", "transfer_interval_min",
               "transfer_interval_max", "daily_limit", "retry_backoff", "min_size_gb",
               "unclassified_action", "notify_token", "notify_chat_id",
               "notify_transfer_success", "proxy",
               "schedule_enabled", "schedule_start", "schedule_end",
               "tg_api_id", "tg_api_hash", "tg_phone"}
    for k, v in body.items():
        if k in allowed:
            db.set_setting(k, v)
    changed = [k for k in body if k in allowed]
    logstream.info("system", f"系统设置已修改: {', '.join(changed) if changed else '（无变更）'}")
    return {"ok": True}


@router.post("/settings/proxy/test")
def test_proxy(body: dict = None, _=Depends(require_user)):
    """测试 TG 代理连通性（用输入框当前值，未保存也可直接测）。"""
    body = body or {}
    proxy = body.get("proxy") or db.get_setting("proxy", "")
    ok, detail = jobs.test_proxy(proxy)
    if not ok:
        raise HTTPException(400, detail)
    return {"ok": True, "detail": detail}


@router.post("/settings/notify/test")
def test_notify(body: dict = None, _=Depends(require_user)):
    """发送测试通知。优先用传入的 Token/Chat ID（未保存也可直接测），否则用已保存配置。"""
    body = body or {}
    token = body.get("token") or db.get_setting("notify_token", "")
    chat_id = body.get("chat_id") or db.get_setting("notify_chat_id", "")
    ok, detail = jobs.send_test(token, chat_id)
    if not ok:
        raise HTTPException(400, detail)
    return {"ok": True}


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
        where.append("message LIKE ?"); params.append(f"%{search}%")
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


# ---------- 备份 ----------
@router.get("/backup/export")
def backup_export(_=Depends(require_user)):
    """导出全部数据（设置/频道/规则/黑名单/转存记录/日志）为 JSON 备份文件。"""
    import json
    from fastapi.responses import Response

    def dump(sql):
        return [dict(r) for r in db.query(sql)]

    payload = {
        "_meta": {"version": 1, "exported_at": time.strftime("%Y-%m-%d %H:%M:%S")},
        "settings": {r["key"]: r["value"] for r in dump("SELECT * FROM settings")},
        "channels": dump("SELECT * FROM channels"),
        "rules": dump("SELECT * FROM rules"),
        "routes": dump("SELECT * FROM routes"),
        "blacklist": dump("SELECT * FROM blacklist"),
        "transfers": dump("SELECT * FROM transfers ORDER BY id"),
    }
    content = json.dumps(payload, ensure_ascii=False, indent=1)
    fname = f"autosave_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
    logstream.info("system", "已导出数据备份（JSON）")
    return Response(
        content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/backup/import")
async def backup_import(_=Depends(require_user)):
    """备份导入为 Phase 3 预留：当前版本请直接恢复 data 目录。"""
    raise HTTPException(400, "请直接恢复 data 目录完成导入（备份含 cookie/session，更完整）")
