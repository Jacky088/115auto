"""向导、系统启停/自检、仪表盘与版本信息路由。"""
import time

from fastapi import APIRouter, Depends, HTTPException

from ... import config, database as db
from ...core import logstream
from ...pan115 import client as pan
from ..deps import require_user

router = APIRouter()


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


def _readiness(strict_115: bool) -> dict:
    """启动就绪检查：115 登录 + 启用中频道 +（纯 Userbot 频道时）Userbot 在线。"""
    st = pan.check_login(strict_115)  # strict=True 强制校验真实登录态（绕过缓存）
    rows = db.query("SELECT type, enabled FROM channels")
    enabled = [dict(r) for r in rows if r["enabled"]]
    polling = [r for r in enabled if r["type"] == "polling"]
    userbots = [r for r in enabled if r["type"] == "userbot"]
    userbot_ok = bool(db.get_setting("userbot_ok", False))
    # 全部是 Userbot 频道（无网页轮询兜底）时，必须 Userbot 在线才能收到消息
    userbot_needed = bool(userbots) and not polling
    reasons = []
    if not st.get("ok"):
        reasons.append("115 未登录或 Cookie 失效，请到「账号管理」重新扫码登录或导入 Cookie")
    if not enabled:
        reasons.append("尚未添加启用中的频道")
    if userbot_needed and not userbot_ok:
        reasons.append("监控的频道均为 Userbot 频道，但 Userbot 未登录或已断开")
    return {
        "ok_115": bool(st.get("ok")),
        "channels": len(enabled),
        "polling": len(polling),
        "userbot": len(userbots),
        "userbot_ok": userbot_ok,
        "userbot_needed": userbot_needed,
        "ready": not reasons,
        "reasons": reasons,
    }


@router.get("/wizard/readiness")
def wizard_readiness(_=Depends(require_user)):
    """向导完成页的就绪清单（复用登录缓存，不打额外 115 请求）。"""
    return _readiness(strict_115=False)


@router.post("/system/start")
def system_start(_=Depends(require_user)):
    """开始运行：启动监听与转存。服务端强制就绪检查（防止前端被绕过）。"""
    r = _readiness(strict_115=True)
    if not r["ready"]:
        raise HTTPException(400, "无法开始运行：" + "；".join(r["reasons"]))
    db.set_setting("running", True)
    db.set_setting("paused", False)
    db.set_setting("auto_stop_reason", "")  # 清除上一次自动停止的原因
    logstream.info("system", "▶ 系统开始运行：监控 TG 频道并自动转存")
    return {"ok": True}


@router.post("/system/stop")
def system_stop(_=Depends(require_user)):
    """停止运行：暂停监听与转存（队列与配置保留）。联动关闭自动整理功能。"""
    from ...core import transfer_gate
    db.set_setting("running", False)
    db.set_setting("auto_stop_reason", "")  # 手动停止时清除自动停止标记
    db.set_setting("auto_move_enabled", False)  # 顶部停止时，自动归档功能联动关闭
    transfer_gate.request_cancel()
    logstream.warn("system", "⏸ 系统已停止运行（自动归档功能已联动关闭，开关已重置为禁用状态）")
    return {"ok": True}


@router.post("/system/resume")
def system_resume(_=Depends(require_user)):
    """恢复被暂停的转存（清除暂停标记；需系统处于运行状态）。"""
    if not db.get_setting("running", False):
        raise HTTPException(400, "系统未在运行，请先点击「开始运行」")
    db.set_setting("paused", False)
    logstream.info("system", "▶ 已恢复转存（清除暂停）")
    return {"ok": True}


@router.get("/system/state")
def system_state(_=Depends(require_user)):
    """运行状态 + 完整就绪检查（供顶栏「开始运行」按钮门控）。

    复用登录缓存（不增加 115 请求压力）；前端按钮据此灰置，服务端 /system/start 仍会强制校验兜底。
    自动停止原因在对应故障恢复后就地清除：Cookie/Userbot 恢复而停止原因与之对应时不再返回
    （并写库清除），前端 10 秒轮询到空值即撤下红色告警，用户点「开始运行」恢复运行。
    """
    r = _readiness(strict_115=False)
    reason = str(db.get_setting("auto_stop_reason", "") or "")
    if reason:
        if "Cookie 失效" in reason and r.get("ok_115"):
            reason = ""
        elif "Userbot 已断开" in reason and r.get("userbot_ok"):
            reason = ""
        if not reason:
            db.set_setting("auto_stop_reason", "")
            logstream.info("system", "故障已恢复，自动停止警示已清除（点击「开始运行」恢复转存）")
    return {
        "running": bool(db.get_setting("running", False)),
        "setup_completed": bool(db.get_setting("setup_completed", False)),
        "paused": bool(db.get_setting("paused", False)),
        "auto_stop_reason": reason,
        # 空间门闸：任务仍在排队（不是失败），仅暂停消费，供顶栏醒目提示
        "space_blocked": bool(db.get_setting("space_blocked", False)),
        "space_blocked_reason": str(db.get_setting("space_blocked_reason", "") or ""),
        **r,
    }


# ---------- dashboard ----------
@router.get("/system/doctor")
def system_doctor(_=Depends(require_user)):
    """一键自检：只读诊断，输出可复制报告。

    安全约束：不返回 Cookie/Token/代理密码等任何凭据原文（只回布尔/掩码/计数），
    单项检查异常只降级为 fail 项，绝不向调用方抛 500。
    """
    import os
    import shutil
    from datetime import datetime

    checks: list[dict] = []

    def add(cid: str, name: str, status: str, detail: str, ms: float = 0.0):
        checks.append({"id": cid, "name": name, "status": status,
                       "detail": detail, "ms": int(ms)})

    def run(cid: str, name: str, fn):
        t0 = time.perf_counter()
        try:
            status, detail = fn()
        except Exception as e:  # 单项失败不影响其它检查
            status, detail = "fail", f"检查异常：{type(e).__name__}: {str(e)[:160]}"
        add(cid, name, status, detail, (time.perf_counter() - t0) * 1000)

    # 1. 数据库
    def _db():
        row = db.query("PRAGMA quick_check", one=True)
        val = str(next(iter(dict(row).values()))) if row else ""
        if val.lower() != "ok":
            return "fail", f"完整性检查未通过：{val[:120]}"
        size = config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0
        return "ok", f"SQLite 完整性正常，数据库 {size / 1048576:.2f}MB，WAL 模式"
    run("db", "数据库完整性", _db)

    # 2. 数据目录可写
    def _data_dir():
        probe = config.DATA_DIR / f".doctor_probe_{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return "ok", f"数据目录可读写：{config.DATA_DIR}"
    run("data_dir", "数据目录读写", _data_dir)

    # 3. 磁盘空间
    def _disk():
        usage = shutil.disk_usage(str(config.DATA_DIR))
        free_gb = usage.free / (1 << 30)
        status = "warn" if free_gb < 2 else "ok"
        return status, f"剩余 {free_gb:.1f}GB / 共 {usage.total / (1 << 30):.1f}GB"
    run("disk", "磁盘空间", _disk)

    # 4. 115 登录（复用缓存，不打额外请求）
    def _login():
        st = pan.check_login(False)
        if st.get("ok"):
            name = str(st.get("name") or st.get("nickname") or "")
            return "ok", "115 登录态有效" + (f"（{name[:40]}）" if name else "")
        return "fail", "115 未登录或 Cookie 失效，请到「账号管理」重新扫码/导入 Cookie"
    run("pan_login", "115 登录态", _login)

    # 5. 115 容量
    def _space():
        sp = pan.space_info()
        total, used = int(sp.get("total") or 0), int(sp.get("use") or 0)
        if not total:
            return "warn", "容量接口未返回总空间"
        pct = used / total * 100
        status = "warn" if pct >= 95 else "ok"
        return status, f"已用 {used / (1 << 40):.2f}TB / {total / (1 << 40):.2f}TB（{pct:.1f}%）"
    run("pan_space", "115 网盘容量", _space)

    # 6. 频道
    def _channels():
        rows = db.query("SELECT type, enabled FROM channels")
        enabled = [r for r in rows if r["enabled"]]
        if not enabled:
            return "fail", "没有启用中的频道，无法接收任何消息"
        polling = sum(1 for r in enabled if r["type"] == "polling")
        return "ok", f"启用频道 {len(enabled)} 个（轮询 {polling} / Userbot {len(enabled) - polling}）"
    run("channels", "频道配置", _channels)

    # 7. Telegram 配置（只报是否配置，不回传值）
    def _tg():
        token = bool(str(db.get_setting("notify_token", "") or "").strip())
        chat = bool(str(db.get_setting("notify_chat_id", "") or "").strip())
        if token and chat:
            return "ok", "Bot Token 与 Chat ID 均已配置"
        if token or chat:
            return "warn", "Bot Token 与 Chat ID 只配置了其中一项，通知可能发送失败"
        return "warn", "未配置 Telegram 通知（不影响转存，仅影响消息推送）"
    run("tg", "Telegram 通知", _tg)

    # 8. 运行状态
    def _running():
        running = bool(db.get_setting("running", False))
        paused = bool(db.get_setting("paused", False))
        reason = str(db.get_setting("auto_stop_reason", "") or "").strip()
        if reason:
            return "fail", f"已自动停止：{reason[:120]}"
        if not running:
            return "warn", "系统当前未运行，不会监听与转存"
        if paused:
            return "warn", "系统运行中但转存已暂停"
        return "ok", "系统运行中"
    run("running", "运行状态", _running)

    # 9. 转存队列健康
    def _queue():
        timeout = 60
        try:
            timeout = max(1, int(float(db.get_setting("running_timeout_min", 60))))
        except (TypeError, ValueError):
            pass
        now = time.time()
        stale = db.query(
            "SELECT COUNT(*) AS n FROM transfers WHERE status='running'"
            " AND running_at IS NOT NULL AND running_at < ?",
            (now - (timeout * 60 + 300),), one=True)["n"]
        queued = db.query("SELECT COUNT(*) AS n FROM transfers WHERE status='queued'", one=True)["n"]
        failed = db.query(
            "SELECT COUNT(*) AS n FROM transfers WHERE status='failed' AND finished_at > ?",
            (now - 86400,), one=True)["n"]
        if stale:
            return "fail", f"{stale} 个任务长时间 running（超过 {timeout} 分钟未完成），等待看门狗回收"
        if failed:
            return "warn", f"排队 {queued} 个；近 24 小时失败 {failed} 个，可到「转存记录」查看原因"
        return "ok", f"排队 {queued} 个，近 24 小时无失败任务"
    run("queue", "转存队列", _queue)

    # 10. 通知渠道 / 定时计划 / 质量策略（配置概览）
    def _features():
        chans = [n for n, k in (("Bark", "bark_url"), ("PushPlus", "pushplus_token"),
                                ("ntfy", "ntfy_url")) if str(db.get_setting(k, "") or "").strip()]
        parts = [f"通知渠道：{'、'.join(chans) if chans else '仅 Telegram/未配置'}"]
        if db.get_setting("schedule_enabled", False):
            parts.append(f"定时转存 {db.get_setting('schedule_start', '22:00')}"
                         f"-{db.get_setting('schedule_end', '08:00')}")
        if db.get_setting("quality_policy_enabled", False):
            mq = str(db.get_setting("quality_min_resolution", "") or "") or "不限"
            parts.append(f"质量策略最低画质 {mq}")
            if db.get_setting("wash_enabled", False):
                parts.append(f"洗版归档 {db.get_setting('wash_archive_dir', '') or '未配置'}")
        return "ok", "；".join(parts)
    run("features", "功能配置", _features)

    # 11. 日志目录
    def _logs():
        if not config.LOG_DIR.exists():
            return "warn", "日志目录不存在（启动后会自动重建）"
        size = sum(f.stat().st_size for f in config.LOG_DIR.glob("*") if f.is_file())
        return "ok", f"日志目录正常，占用 {size / 1048576:.1f}MB"
    run("logs", "日志目录", _logs)

    # ---- 汇总 ----
    counts = {"ok": 0, "warn": 0, "fail": 0, "skip": 0}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    overall = "fail" if counts["fail"] else ("warn" if counts["warn"] else "ok")
    rank = {"fail": "FAIL", "warn": "WARN", "ok": "OK", "skip": "SKIP"}
    lines = [f"115AutoSave {config.APP_VERSION} 自检报告",
             f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
             f"结论：{rank.get(overall, overall)}"
             f"（正常 {counts['ok']} / 警告 {counts['warn']} / 失败 {counts['fail']}）"]
    lines += [f"[{rank.get(c['status'], c['status'])}] {c['name']}：{c['detail']}" for c in checks]
    return {"overall": overall, "version": config.APP_VERSION, "checks": checks,
            "counts": counts, "report": "\n".join(lines),
            "generated_at": time.time()}


# ---------- dashboard ----------
@router.get("/dashboard/stats")
def dashboard(_=Depends(require_user)):
    from datetime import datetime

    today_quota = db.quota_today()
    # 以服务器本地时区的 0 点为今日起点（与 quota_today 的 date.today() 保持一致）
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
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
        "SELECT id,title,category,target_dir,size,status,created_at,channel,"
        "kind,increment,priority FROM transfers ORDER BY id DESC LIMIT 20")]




# ---------- version ----------
@router.get("/version")
def version(_=Depends(require_user)):
    from ... import config
    return {"version": config.APP_VERSION}
