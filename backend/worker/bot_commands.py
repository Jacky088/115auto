"""TG Bot 远程指令：通过你配置的 Bot 发送 /status /pause /resume /retry 等指令远程控制系统。

实现方式：long polling Telegram getUpdates（不需要公网回调地址），每 3 秒一次。
只有配置的 Chat ID 发来的指令会被执行（防止陌生人控制）。
"""
import asyncio
import time

import httpx

from .. import database as db
from ..core import logstream
from . import jobs

_last_update_id = 0


def _tg_proxy() -> str | None:
    return str(db.get_setting("proxy", "") or "") or None


HELP = (
    "可用指令：\n"
    "/status — 查看运行状态（队列/限额/空间/频道）\n"
    "/pause — 暂停转存（停止消费队列）\n"
    "/resume — 恢复转存\n"
    "/retry — 重试全部失败任务\n"
    "/help — 显示本帮助"
)


def _handle_command(text: str, chat_id: str) -> str:
    """执行指令，返回回复文本。"""
    cmd = text.strip().split()[0].lower().split("@")[0]
    if cmd == "/status":
        queued = db.query("SELECT count(*) c FROM transfers WHERE status='queued'", one=True)["c"]
        today_s = db.query("SELECT count(*) c FROM transfers WHERE status='success' AND created_at>?",
                           (time.time() - 86400,), one=True)["c"]
        today_f = db.query("SELECT count(*) c FROM transfers WHERE status IN ('failed','blocked')"
                           " AND created_at>?", (time.time() - 86400,), one=True)["c"]
        quota = db.quota_today()
        limit = db.get_setting("daily_limit", 50)
        paused = db.get_setting("paused", False)
        space_line = ""
        if db.get_setting("cookie_ok", None):
            try:
                from ..pan115 import client as pan
                sp = pan.space_info()
                gb = lambda b: f"{b / (1 << 30):.1f}GB"
                space_line = f"\n💾 115 空间：已用 {gb(sp['use'])}/{gb(sp['total'])}（{sp['ratio']}%）"
            except Exception:
                pass
        running = db.get_setting("running", False)
        state_line = ("▶ 运行中" if running and not paused
                      else "⏸ 已暂停" if paused else "⏹ 未启动（网页顶栏可启动）")
        return (f"📊 运行状态\n\n{state_line}\n"
                f"📥 队列：{queued} 个待转存\n"
                f"✅ 24h 成功：{today_s}｜❌ 失败/拦截：{today_f}\n"
                f"📈 今日限额：{quota}/{limit}{space_line}")
    if cmd == "/pause":
        db.set_setting("paused", True)
        logstream.warn("transfer", "已通过 TG 指令暂停转存（/pause）")
        return "⏸ 已暂停转存（队列保留）。发送 /resume 恢复"
    if cmd == "/resume":
        if not db.get_setting("running", False):
            return "⏹ 系统尚未启动，请先在网页顶栏点「开始运行」"
        db.set_setting("paused", False)
        logstream.info("transfer", "已通过 TG 指令恢复转存（/resume）")
        return "▶ 已恢复转存"
    if cmd == "/retry":
        n = db.execute("UPDATE transfers SET status='queued', attempts=0, error='', next_retry_at=NULL"
                       " WHERE status='failed'")
        logstream.info("transfer", f"已通过 TG 指令重试 {n} 个失败任务（/retry）")
        return f"🔄 已重试 {n} 个失败任务" if n else "当前没有失败任务"
    if cmd == "/help":
        return HELP
    return f"未知指令 {cmd}\n\n{HELP}"


async def bot_command_loop():
    """TG Bot long polling：处理远程指令。未配置 Token 时跳过。"""
    global _last_update_id
    while True:
        token = db.get_setting("notify_token", "")
        chat_id = str(db.get_setting("notify_chat_id", "") or "")
        if not token or not chat_id:
            await asyncio.sleep(30)
            continue
        try:
            async with httpx.AsyncClient(proxy=_tg_proxy(), timeout=40) as client:
                logstream.info("system", "TG Bot 远程指令已启动（/help 查看指令）")
                while True:
                    # 每轮重新读 paused/token，保证 /pause 即时生效、配置热更新
                    params = {"timeout": 30, "offset": _last_update_id + 1,
                              "allowed_updates": '["message"]'}
                    resp = await client.get(f"https://api.telegram.org/bot{token}/getUpdates",
                                            params=params)
                    if resp.status_code != 200:
                        await asyncio.sleep(10)
                        continue
                    for upd in resp.json().get("result", []):
                        _last_update_id = max(_last_update_id, upd["update_id"])
                        msg = upd.get("message") or {}
                        if str(msg.get("chat", {}).get("id", "")) != chat_id:
                            continue  # 非授权用户
                        text = (msg.get("text") or "").strip()
                        if not text.startswith("/"):
                            continue
                        reply = _handle_command(text, chat_id)
                        await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                          json={"chat_id": chat_id, "text": reply})
        except Exception as e:
            logstream.warn("system", f"TG Bot 指令轮询异常: {e}")
            await asyncio.sleep(15)
        await asyncio.sleep(1)
