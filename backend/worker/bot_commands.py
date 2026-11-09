"""TG Bot 远程指令：通过你配置的 Bot 发送 /status /pause /resume /retry 等指令远程控制系统。

实现方式：long polling Telegram getUpdates（不需要公网回调地址），每 3 秒一次。
只有配置的 Chat ID 发来的指令会被执行（防止陌生人控制）。
"""
import asyncio
import json
import time

import httpx

from .. import database as db
from ..core import logstream
from . import jobs

_last_update_id = 0

# /sub 搜索结果缓存：chat_id -> (缓存时间, 候选列表)（subpick 按钮回调时取回对应条目）
_SUB_SEARCH_CACHE: dict[str, tuple[float, list]] = {}
_SUB_SEARCH_TTL = 1800  # 30 分钟：旧消息里的按钮过期后不再订阅到别的片
_SUB_NUMS = ["①", "②", "③", "④", "⑤", "⑥"]

# 备份文件恢复待确认区：chat_id -> (payload, 缓存时间)。
# 转发备份 JSON 给 Bot 后先暂存，回复 /restore confirm 才真正写库（防误触）。
_RESTORE_PENDING: dict[str, tuple[dict, float]] = {}
_RESTORE_TTL = 600  # 10 分钟内有效


def _backup_summary(data: dict) -> str:
    """备份 payload 的一行人量摘要（用于确认提示）。"""
    meta = data.get("_meta") or {}
    n = lambda t: len(data.get(t) or [])  # noqa: E731
    return (f"导出于 {meta.get('exported_at', '?')}｜设置 {len(data.get('settings') or {})} 项｜"
            f"频道 {n('channels')}｜规则 {n('rules')}｜订阅 {n('subscriptions')}｜"
            f"转存记录 {n('transfers')}")


async def restore_upload_flow(client: "httpx.AsyncClient", token: str, chat_id: str,
                              doc: dict) -> str:
    """用户转发备份 JSON 文件给 Bot：下载 → 校验 → 暂存待确认。"""
    size = int(doc.get("file_size") or 0)
    if size > 10 * 1024 * 1024:
        return f"文件过大（{size // 1024} KB），备份文件不应超过 10 MB"
    # 下载文件：getFile 拿路径，再从 file 接口取内容（都走 TG 代理）
    r = await client.post(f"https://api.telegram.org/bot{token}/getFile",
                          json={"file_id": doc.get("file_id")},
                          proxy=_tg_proxy(), timeout=30)
    fp = (r.json().get("result") or {}).get("file_path") if r.status_code == 200 else None
    if not fp:
        return "无法读取该文件（Telegram getFile 失败），请重新发送"
    f = await client.get(f"https://api.telegram.org/file/bot{token}/{fp}",
                         proxy=_tg_proxy(), timeout=60)
    try:
        data = json.loads(f.content)
    except Exception:
        return "文件不是合法 JSON，已忽略（确认发送的是自动备份/网页导出的 .json 文件）"
    if not isinstance(data, dict) or not isinstance(data.get("_meta"), dict) or "settings" not in data:
        return "不是本程序的备份文件（缺少备份标记），已忽略"
    _RESTORE_PENDING[chat_id] = (data, time.time())
    return ("⚠️ 检测到备份文件\n" + _backup_summary(data) +
            "\n\n恢复将【整体覆盖】当前的设置/频道/规则/订阅/转存记录（登录密码不变）。\n"
            "确认恢复请回复：/restore confirm（10 分钟内有效，或直接忽略即取消）")


def _restore_guide() -> str:
    """恢复操作三步引导（/menu 按钮、/restore 指令共用）。"""
    return ("♻️ 从备份恢复（三步）：\n"
            "① 直接把备份 JSON 文件发送给本 Bot（自动备份的文件或网页导出的文件均可）\n"
            "② 我会校验并回显备份摘要\n"
            "③ 回复 /restore confirm 确认恢复（10 分钟内有效）\n\n"
            "恢复会整体覆盖当前的设置/频道/规则/订阅/转存记录（登录密码不变），"
            "完成并校验一致后我会回复恢复结果")


def _verify_restore(data: dict) -> str:
    """恢复写库后逐表核对：返回空串=校验通过，否则返回不一致说明。"""
    tables = ("channels", "rules", "routes", "blacklist", "subscriptions", "transfers")
    for t in tables:
        want = len(data.get(t) or [])
        got = db.query(f"SELECT count(*) c FROM {t}", one=True)["c"]
        if want != got:
            return f"{t} 数量不一致（备份 {want} 条，实际 {got} 条）"
    # settings 逐键核对（排除恢复时保留当前值的凭据项；running 会被强制改写为停止态）
    got_map = {r["key"]: r["value"] for r in db.query("SELECT key, value FROM settings")}
    for k, v in (data.get("settings") or {}).items():
        if k in db.BACKUP_EXCLUDE_SETTINGS or k == "running":
            continue
        if got_map.get(k) != str(v):
            return f"settings[{k}] 恢复后与备份不一致"
    return ""


def _do_restore(chat_id: str) -> str:
    """执行恢复：校验待确认区 → 写库 → 复核数量 → 返回结果。"""
    pending = _RESTORE_PENDING.pop(chat_id, None)
    if not pending:
        return _restore_guide()
    data, ts = pending
    if time.time() - ts > _RESTORE_TTL:
        return "待确认的备份已超时（10 分钟），请重新发送备份文件"
    try:
        stats = db.restore_payload(data)
    except ValueError as e:
        logstream.warn("system", f"TG 恢复备份失败（校验未通过）: {e}")
        return f"❌ 恢复失败：{e}\n当前数据未做任何更改，可放心重试"
    # 写库成功后再逐表复核，确认真实落库才报成功
    mismatch = _verify_restore(data)
    if mismatch:
        logstream.error("system", f"TG 恢复备份后复核不一致: {mismatch}")
        return f"❌ 恢复结果异常（{mismatch}），请查看系统日志，必要时用网页入口重新恢复"
    logstream.info("system", f"已通过 TG 指令恢复备份（{_backup_summary(data)}），复核一致")
    return ("✅ 已从备份恢复（已与备份逐项核对一致）\n" + _backup_summary(data) +
            "\n\n系统已回到停止状态，确认无误后到网页顶栏点「开始运行」。")


def _tg_proxy() -> str | None:
    return str(db.get_setting("proxy", "") or "") or None


def _check_sender(msg: dict) -> tuple[bool, str]:
    """发送者鉴权：chat_id 匹配之外，还要求发送者是绑定的管理员用户。

    返回 (是否放行, 拒绝提示文本)。提示文本非空时调用方应将其回复给发送者。

    - notify_chat_id 配成群组时，群里任何人都能给 Bot 发消息；不校验发送者
      等于把 /retry /restore 等指令开放给全体群成员。管理员绑定（TOFU）仅在
      **私聊**中自动完成——群聊首用会被拒绝并引导先私聊绑定，防止群成员抢绑。
    - 绑定值带上 chat_id（f"{chat_id}:{user_id}"）：换 Bot/换对话后自动重新绑定。
    - channel_post 等无发送者信息（from 为空）的消息：保持原有 chat_id 口径放行。
    """
    chat_id = str(msg.get("chat", {}).get("id", ""))
    chat_type = str(msg.get("chat", {}).get("type") or "")
    sender = str((msg.get("from") or {}).get("id") or "")
    if not sender:
        return True, ""
    stored = str(db.get_setting("tg_admin_id", "") or "")
    bound = f"{chat_id}:{sender}"
    if not stored:
        if chat_type != "private":
            logstream.warn("system", f"TG Bot 收到群聊 {chat_id} 中用户 {sender} 的首次指令，"
                                     "已拒绝（管理员绑定仅限私聊完成）")
            return False, ("该 Bot 尚未绑定管理员。出于安全考虑，请在与 Bot 的私聊中发送任意指令"
                           "完成绑定，之后即可在群内使用。")
        db.set_setting("tg_admin_id", bound)
        logstream.warn("system", f"TG Bot 已绑定管理员用户 {sender}（私聊自动绑定；"
                                 "如非本人操作，请立即到网页改绑或停止使用该 Bot）")
        return True, ""
    if stored != bound:
        logstream.warn("system", f"TG Bot 收到非管理员用户 {sender} 的指令，已拒绝")
        return False, "仅绑定的管理员用户可使用 Bot 指令"
    return True, ""


def _restore_allowed(msg: dict) -> tuple[bool, str]:
    """备份恢复仅限与 Bot 的私聊：群聊里转发/伪造备份文件的风险面太大。"""
    if str(msg.get("chat", {}).get("type") or "") == "private":
        return True, ""
    return False, ("出于安全考虑，备份恢复仅支持在与 Bot 的私聊中进行：\n"
                   "请私聊本 Bot 发送备份 JSON 文件")


USAGE_ADD = (
    "用法：/add <频道名或链接> on|off\n"
    "· on  = 开启「仅订阅」（仅转存命中订阅关键词的内容）\n"
    "· off = 关闭（全盘转存）\n"
    "例如：/add @somechannel on"
)

HELP = (
    "可用指令：\n"
    "/status — 查看运行状态（队列/限额/空间/频道）\n"
    "/space — 查看 115 剩余空间\n"
    "/ls — 最近 5 条转存记录\n"
    "/menu — 快捷操作菜单（按钮面板）\n"
    "/add <频道名或链接> on|off — 添加监控频道（on=仅订阅，off=全盘转存；on/off 必填）\n"
    "/sub — 查看订阅；/sub <片名> 搜索 TMDB 点选订阅；/sub del <关键词> 删除\n"
    "/pause — 暂停转存（暂停处理队列中的任务）\n"
    "/resume — 恢复转存\n"
    "/retry — 重试全部失败任务\n"
    "/backfill [剧名] — 向前补全早期分集（如从 E03 开始追，补全 E01/E02）\n"
    "/restore — 发送备份 JSON 文件后确认恢复数据\n"
    "/help — 显示本帮助"
)

# 注册到 Telegram 的指令菜单：输入框左侧「菜单」按钮 / 输入 / 时可见
BOT_MENU_COMMANDS = [
    {"command": "status", "description": "运行状态（队列/限额/空间）"},
    {"command": "space", "description": "115 剩余空间"},
    {"command": "ls", "description": "最近 5 条转存记录"},
    {"command": "menu", "description": "快捷操作菜单（按钮面板）"},
    {"command": "add", "description": "添加频道：/add @频道 on|off"},
    {"command": "sub", "description": "追剧订阅：查看/添加/删除"},
    {"command": "pause", "description": "暂停转存"},
    {"command": "resume", "description": "恢复转存"},
    {"command": "retry", "description": "重试全部失败任务"},
    {"command": "backfill", "description": "向前补全剧集（查找早期分集）"},
    {"command": "restore", "description": "从备份文件恢复数据"},
    {"command": "help", "description": "显示帮助"},
]


async def register_bot_menu(client: "httpx.AsyncClient", token: str) -> None:
    """把指令清单注册到 Telegram（setMyCommands），失败不影响运行。"""
    try:
        r = await client.post(f"https://api.telegram.org/bot{token}/setMyCommands",
                              json={"commands": BOT_MENU_COMMANDS})
        if r.status_code == 200 and r.json().get("ok"):
            logstream.info("system", "TG Bot 指令菜单已注册（输入框左侧菜单按钮，或输入 / 时可见）")
        else:
            logstream.warn("system", f"TG Bot 菜单注册失败: {str(r.text)[:120]}")
    except Exception as e:
        logstream.warn("system", f"TG Bot 菜单注册异常: {logstream.exc(e)}")


def _gb(b: float) -> str:
    return f"{b / (1 << 30):.1f}GB"


def _handle_command(text: str, chat_id: str) -> str:
    """执行指令，返回回复文本。"""
    parts = text.strip().split()
    cmd = parts[0].lower().split("@")[0]
    args = parts[1:]
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
                space_line = f"\n💾 115 空间：已用 {_gb(sp['use'])}/{_gb(sp['total'])}（{sp['ratio']}%）"
            except Exception:
                pass
        running = db.get_setting("running", False)
        state_line = ("▶ 运行中" if running and not paused
                      else "⏸ 已暂停" if paused else "⏹ 未启动（点击网页顶栏「开始运行」启动）")
        return (f"📊 运行状态\n\n{state_line}\n"
                f"📥 队列：{queued} 个待转存\n"
                f"✅ 近 24 小时成功：{today_s}｜❌ 失败/拦截：{today_f}\n"
                f"📈 每日限额：{quota}/{limit}{space_line}")
    if cmd == "/space":
        try:
            from ..pan115 import client as pan
            sp = pan.space_info()
            return (f"💾 115 空间\n\n已用 {_gb(sp['use'])} / {_gb(sp['total'])}"
                    f"（{sp['ratio']}%）\n剩余 {_gb(sp.get('free', 0))}")
        except Exception as e:
            return f"查询空间失败: {str(e)[:120]}"
    if cmd == "/ls":
        rows = db.query("SELECT title, status, kind, increment, target_dir, created_at FROM transfers"
                        " ORDER BY id DESC LIMIT 5")
        if not rows:
            return "暂无转存记录"
        label = {"success": "✅", "failed": "❌", "blocked": "⛔", "queued": "⏳",
                 "running": "🔄", "skipped": "⏭", "duplicate": "🔁", "submitted": "☁️"}
        lines = ["🕘 最近转存", ""]
        for r in rows:
            title = (r["title"] or "-")[:40]
            mark = label.get(r["status"], "·")
            kind_mark = ("☁️" if r["kind"] == "offline"
                     else "🔁" if r["increment"] else "")
        lines.append(f"{mark} {kind_mark}{title}")
        return "\n".join(lines)
    if cmd == "/add":
        # 真正的添加流程由 bot_command_loop 的异步分支执行（需 await 频道校验），
        # 同步路径仅做用法提示兜底
        return USAGE_ADD
    if cmd == "/quota":
        return "Bot 暂不支持修改每日限额，请在网页「系统设置」中调整（默认 50）"
    if cmd == "/sub":
        return _handle_sub(args)
    if cmd in ("/pause", "/resume"):
        return _do_pause() if cmd == "/pause" else _do_resume()
    if cmd == "/retry":
        n = db.execute(
            "UPDATE transfers SET status='queued', attempts=0, error='', next_retry_at=NULL"
            " WHERE status='failed'"
            " AND id=(SELECT MAX(id) FROM transfers t2"
            "         WHERE t2.share_code=transfers.share_code AND t2.status='failed')"
            " AND share_code NOT IN (SELECT share_code FROM transfers"
            "                         WHERE status IN ('queued','running','submitted'))")
        logstream.info("transfer", f"已通过 TG 指令重试 {n} 个失败任务（/retry）")
        return f"🔄 已重试 {n} 个失败任务" if n else "当前没有失败任务"
    if cmd == "/help":
        return HELP
    if cmd == "/menu":
        return "发送 /menu 打开快捷操作菜单"
    return f"未知指令 {cmd}\n\n{HELP}"


def _do_pause() -> str:
    db.set_setting("paused", True)
    logstream.warn("transfer", "已通过 TG 指令暂停转存（/pause）")
    return "⏸ 已暂停转存（队列保留）。发送 /resume 恢复"


def _do_resume() -> str:
    if not db.get_setting("running", False):
        return "⏹ 系统尚未启动，请先在网页顶栏点「开始运行」"
    db.set_setting("paused", False)
    logstream.info("transfer", "已通过 TG 指令恢复转存（/resume）")
    return "▶ 已恢复转存"


def _menu_keyboard() -> dict:
    """快捷操作菜单：按钮随当前状态变化（暂停/恢复）。订阅模式按频道独立设置。"""
    return {"inline_keyboard": [
        [{"text": "📊 运行状态", "callback_data": "c:status"},
         {"text": "💾 115 空间", "callback_data": "c:space"}],
        [{"text": "🕘 最近转存", "callback_data": "c:ls"},
         {"text": "🔄 重试失败任务", "callback_data": "c:retry"}],
        [{"text": "⏸ 暂停转存" if not db.get_setting("paused", False) else "▶ 恢复转存",
          "callback_data": "c:toggle_pause"}],
        [{"text": "🎬 订阅列表", "callback_data": "c:sub"},
         {"text": "📺 频道订阅模式", "callback_data": "c:submode"}],
        [{"text": "♻️ 从备份恢复", "callback_data": "c:restore"},
         {"text": "❓ 全部指令帮助", "callback_data": "c:help"}],
    ]}


def _submode_text() -> str:
    """各频道的订阅模式状态（只读展示，网页「频道管理」可调整）。"""
    rows = db.query("SELECT name, enabled, sub_only FROM channels ORDER BY id")
    if not rows:
        return "还没有监控频道，发送 /add <频道名或链接> on|off 添加"
    lines = ["📺 频道订阅模式", ""]
    for r in rows:
        state = "🟢 全盘转存" if not r["sub_only"] else "🛎 仅订阅"
        if not r["enabled"]:
            state += "（频道已停用）"
        lines.append(f"@{r['name']} — {state}")
    lines.append("")
    lines.append("新增频道时必须选择订阅模式（/add 的 on|off 必填）；在网页「频道管理」可按频道调整")
    return "\n".join(lines)


def _handle_callback(data: str) -> tuple[str, dict]:
    """处理菜单按钮点击，返回 (回复文本, 键盘)。动作执行后菜单会原位刷新。"""
    data = (data or "").strip()
    if data == "toggle_pause":
        text = _do_resume() if db.get_setting("paused", False) else _do_pause()
        return text, _menu_keyboard()
    if data == "c:submode":
        return _submode_text(), _menu_keyboard()
    if data == "c:restore":
        return _restore_guide(), _menu_keyboard()
    if data.startswith("c:"):
        cmd = data[2:].split()[0] if data[2:].strip() else ""
        args = data[2:].split()[1:]
        if cmd in ("status", "space", "ls", "sub", "retry", "help"):
            text = _handle_command(f"/{cmd} {' '.join(args)}".strip(), "")
            return text, (_menu_keyboard() if cmd != "help" else {})
    return "未知操作", _menu_keyboard()


async def add_channel_flow(raw: str) -> str:
    """/add 指令完整流程：语法 /add <频道名或链接> on|off（on=开启仅订阅，off=全盘转存）。

    语法必须完整：缺 on/off 或写别的值都添加失败，并返回正确用法。
    """
    import re as _re
    tokens = str(raw or "").split()
    if len(tokens) != 2 or tokens[1].lower() not in ("on", "off"):
        return USAGE_ADD
    name, mode = tokens[0], tokens[1].lower()
    m = _re.search(r"t\.me/(?:s/)?@?([0-9A-Za-z_]+)", name)
    name = (m.group(1) if m else name).lstrip("@").strip()
    if not name:
        return USAGE_ADD
    if len(name) > 64:
        return "频道名过长（最多 64 字符）"
    if not _re.fullmatch(r"[0-9A-Za-z_]{1,64}", name):
        return "频道名仅支持字母、数字与下划线（1-64 位）"
    if db.query("SELECT 1 FROM channels WHERE name=?", (name,), one=True):
        # 不限制 type：同一频道若已有 userbot 记录，再加 polling 会导致消息被两条链路重复处理
        return f"频道 @{name} 已存在。订阅模式可在网页「频道管理」按频道开关"
    n = db.query("SELECT count(*) c FROM channels", one=True)["c"]
    if n >= 50:
        return "监控频道已达上限 50 个，请先清理不需要的频道"
    from ..tg.validate import check_channel_access
    res = await check_channel_access(name, "polling")  # Bot /add 固定网页轮询接入
    if not res.get("ok"):
        return f"无法添加 @{name}：{res.get('err') or '频道不可访问，请核对后重试'}"
    title = str(res.get("title") or "")
    sub_only = 1 if mode == "on" else 0
    db.execute("INSERT INTO channels(type,name,sub_only,created_at) VALUES('polling',?,?,?)",
               (name, sub_only, time.time()))
    mode_txt = "仅订阅（仅转存命中订阅关键词的内容）" if sub_only else "全盘转存"
    logstream.info("channels",
                   f"已通过 TG 指令添加监控频道 @{name}（{title}，订阅模式：{'开' if sub_only else '关'}，已通过访问校验）")
    return (f"✅ 已添加频道 @{name}（{title}）\n"
            f"🛎 订阅模式：{mode_txt}\n"
            "下一轮轮询开始抓取；之后可在网页「频道管理」调整该开关")


def _handle_sub(args: list[str]) -> str:
    """/sub 查看订阅；/sub <片名> 走搜索订阅流程（见 sub_search_flow）；/sub del <关键词> 删除。"""
    from ..core import subscribe as _sub
    if args and args[0].lower() == "del":
        kw = " ".join(args[1:]).strip()
        if not kw:
            return "用法：/sub del <关键词>"
        # 与网页一致的归一化口径：大小写/空格/标点差异也能删到；关键词或别名均可
        target = _sub.normalize(kw)
        matched = [dict(r) for r in db.query("SELECT * FROM subscriptions")
                   if target and target in {_sub.normalize(t) for t in _sub.terms_of(dict(r))}]
        for row in matched:
            db.execute("DELETE FROM subscriptions WHERE id=?", (row["id"],))
        if matched:
            names = "、".join(f"「{r['keyword']}」" for r in matched[:3])
            logstream.info("subscribe", f"已通过 TG 指令删除订阅: {names}")
            return f"🗑 已删除订阅{names}"
        return f"未找到订阅「{kw}」"
    if args:
        return "添加订阅请发送 /sub <片名>，我会搜索 TMDB 返回海报供你点选订阅"
    rows = db.query("SELECT keyword, enabled, last_ep, last_title FROM subscriptions ORDER BY id")
    if not rows:
        return "暂无订阅。发送 /sub <片名> 搜索并点选订阅"
    lines = ["🎬 追剧订阅", ""]
    for r in rows:
        ep = f"（{r['last_ep']}）" if r["last_ep"] else ""
        mark = "" if r["enabled"] else "⏸ "
        lines.append(f"· {mark}{r['keyword']}{ep}")
    lines.append("")
    lines.append("ℹ️ 仅订阅/全盘转存按频道独立设置：网页「频道管理」或菜单「📺 频道订阅模式」查看")
    return "\n".join(lines)


async def _bot_send_text(client: "httpx.AsyncClient", token: str, chat_id: str,
                         text: str, reply_markup: dict | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
    except Exception:
        pass


def _sub_add(name: str, note: str, aliases: list | None = None,
             tmdb_id: int | None = None, kind: str = "") -> str:
    """添加订阅（归一化去重），返回给用户的回复文案。

    别名（如 TMDB 原始名）会一并保存，频道帖子用任一名称都能命中。
    """
    from ..core import subscribe as _sub
    name = str(name or "").strip()
    if not name:
        return "片名不能为空"
    if len(name) > _sub.MAX_KEYWORD_LEN:
        return f"片名过长（最多 {_sub.MAX_KEYWORD_LEN} 字符）"
    al = [a for a in (str(x or "").strip() for x in (aliases or []))
          if a and len(a) <= _sub.MAX_KEYWORD_LEN][:_sub.MAX_ALIASES]
    conflict = _sub.find_conflict(name, al)
    if conflict:
        return f"订阅「{name}」已存在（与「{conflict['keyword']}」重复）"
    db.execute(
        "INSERT INTO subscriptions(keyword,note,aliases,tmdb_id,kind,created_at)"
        " VALUES(?,?,?,?,?,?)",
        (name, note, json.dumps(al, ensure_ascii=False), tmdb_id,
         kind if kind in ("tv", "movie") else "", time.time()))
    logstream.info("subscribe", f"已通过 TG Bot 搜索订阅: {name}（{note}，别名 {len(al)} 个）")
    return (f"✅ 已订阅「{name}」\n"
            "命中该剧的频道消息后会记录追更进度并推送通知；"
            "是否仅转存命中内容由「频道管理」的仅订阅开关决定")


async def sub_search_flow(client: "httpx.AsyncClient", token: str,
                          chat_id: str, query: str) -> None:
    """/sub <片名>：搜索 TMDB → 相册发海报（带编号说明）→ 编号按钮点选订阅。"""
    from ..api.manage import _tmdb_get, _tmdb_item
    api_key = str(db.get_setting("tmdb_api_key", "") or "").strip()
    if not api_key:
        await _bot_send_text(client, token, chat_id,
                             "请先在网页「订阅追剧」页填写并保存 TMDB API Key，再使用搜索订阅")
        return
    cands = []
    for k in ("tv", "movie"):
        try:
            resp = _tmdb_get(f"/search/{k}", {"api_key": api_key, "query": query,
                                              "language": "zh-CN"})
            if resp.status_code == 200:
                cands += [x for x in (_tmdb_item(it, k)
                                      for it in (resp.json().get("results") or [])) if x]
            else:
                logstream.warn("system", f"TG /sub 搜索 /search/{k} 返回 {resp.status_code}")
        except Exception as e:
            logstream.warn("system", f"TG /sub 搜索 /search/{k} 失败: {logstream.exc(e)}")
            continue
    cands.sort(key=lambda x: -x["popularity"])
    cands = [c for c in cands if c["poster_path"]][:6]
    if not cands:
        await _bot_send_text(client, token, chat_id,
                             f"未找到与「{query}」相关的影视，请换个名称重试")
        return

    proxy = _tg_proxy()
    media, files, kept = [], {}, []  # files 必须是 dict（httpx multipart 字段名 → 内容）
    for it in cands:
        try:  # 海报经代理下载后以上传方式发给 TG（服务端通常也连不上 image.tmdb.org）
            img = await asyncio.to_thread(
                lambda p=it["poster_path"]: httpx.get(
                    f"https://image.tmdb.org/t/p/w200{p}", proxy=proxy, timeout=15).content)
        except Exception:
            continue
        i = len(kept)
        cap = f"{_SUB_NUMS[i]} {it['name']} · {'电视剧' if it['kind'] == 'tv' else '电影'}"
        if it["year"]:
            cap += f" · {it['year']} 开播"
        if it["rating"]:
            cap += f" · ⭐{it['rating']}"
        files[f"f{i}"] = ("poster.jpg", img, "image/jpeg")
        media.append({"type": "photo", "media": f"attach://f{i}", "caption": cap})
        kept.append(it)
    if not kept:
        await _bot_send_text(client, token, chat_id, "海报获取失败，请检查网络/代理设置后重试")
        return
    _SUB_SEARCH_CACHE[str(chat_id)] = (time.time(), kept)

    try:  # 相册消息：一次发齐全部海报
        await client.post(
            f"https://api.telegram.org/bot{token}/sendMediaGroup",
            data={"chat_id": chat_id, "media": json.dumps(media, ensure_ascii=False)},
            files=files, timeout=60)
    except Exception:  # 相册失败退化为纯文本列表，按钮选择不受影响
        lines = [f"{_SUB_NUMS[i]} {it['name']}（{'电视剧' if it['kind'] == 'tv' else '电影'}"
                 f"{('·' + it['year']) if it['year'] else ''}）" for i, it in enumerate(kept)]
        await _bot_send_text(client, token, chat_id, "搜索结果：\n" + "\n".join(lines))

    kb = {"inline_keyboard": [
        [{"text": f"{_SUB_NUMS[i]} {kept[i]['name'][:24]}", "callback_data": f"subpick:{i}"}]
        for i in range(len(kept))] + [[{"text": "❌ 取消", "callback_data": "subpick:x"}]]}
    await _bot_send_text(client, token, chat_id,
                         f"「{query}」的搜索结果（按人气排序），点按钮完成订阅：", kb)


def _like_escape(s: str) -> str:
    """LIKE 通配符转义（%/_/\），防止关键词触发全表扫描。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _backfill_candidates(arg: str) -> tuple[str, dict | None]:
    """/backfill 入口：构建候选列表（按剧名关键词或最近的成功分集转存）。

    支持「/backfill <剧名> <集数>」：末尾数字作为当前集数（标题识别不出集数时兜底）。
    """
    arg = arg.strip()[:100]
    ep_hint = 0
    tokens = arg.rsplit(" ", 1)
    if len(tokens) == 2 and tokens[1].isdigit():
        arg, ep_hint = tokens[0].strip(), int(tokens[1])
    if arg:
        rows = db.query(
            "SELECT id, title FROM transfers WHERE status IN ('success','failed')"
            " AND kind='share' AND message_id>0 AND title LIKE ? ESCAPE '\\'"
            " ORDER BY id DESC LIMIT 5", (f"%{_like_escape(arg)}%",))
        if not rows:
            return f"没有找到标题包含「{arg}」的成功转存记录", None
        kb = {"inline_keyboard": [
            [{"text": (r["title"] or "-")[:32], "callback_data": f"bfpick:{r['id']}:{ep_hint}"}]
            for r in rows
        ] + [[{"text": "❌ 取消", "callback_data": "bfcancel:x"}]]}
        hint = f"（当前集数按 {ep_hint} 计）" if ep_hint else ""
        return "选择要向前补全的记录（将查找其早期分集）" + hint + "：", kb
    rows = db.query(
        "SELECT id, title FROM transfers WHERE status IN ('success','failed') AND kind='share'"
        " AND message_id>0 ORDER BY id DESC LIMIT 20")
    from ..tg.episode_backfill import should_backfill
    cands, seen = [], set()
    for r in rows:
        parsed = should_backfill(r["title"] or "")
        if not parsed or (r["title"] or "") in seen:
            continue
        seen.add(r["title"])
        cands.append(r)
        if len(cands) >= 6:
            break
    if not cands:
        return ("最近的成功转存里没有能识别集数的分集记录。\n"
                "用法：/backfill <剧名关键词> [当前集数]，按标题查找后选择"), None
    kb = {"inline_keyboard": [
        [{"text": (r["title"] or "-")[:32], "callback_data": f"bfpick:{r['id']}:0"}]
        for r in cands
    ] + [[{"text": "❌ 取消", "callback_data": "bfcancel:x"}]]}
    return "选择要向前补全的成功转存（将查找其早期分集）：", kb


def _backfill_confirm(tid: int, ep_hint: int = 0) -> tuple[str, dict | None]:
    """bfpick 回调：回显补全摘要 + 确认按钮。ep_hint 为 /backfill 手动指定的集数。"""
    row = db.query("SELECT id, title, channel, message_id, status, kind FROM transfers WHERE id=?",
                   (tid,), one=True)
    if not row or row["status"] not in ("success", "failed") or row["kind"] != "share"             or not row["message_id"]:
        return "该记录不可作为补全锚点（需为已转存的分享，含已失效的）", None
    from ..tg.episode_backfill import base_title, parse_episode
    base = base_title(row["title"] or "")
    season, ep = parse_episode(row["title"] or "")
    if ep is None and ep_hint > 1:
        season, ep = None, ep_hint  # 手动指定集数兜底
    if ep is None or ep <= 1:
        return ("该记录标题未识别出集数（需 S01E52 / 第52集 等格式）。\n"
                "请改用 /backfill <剧名关键词> <当前集数> 发起，"
                "或在网页「转存记录」页发起并手动填写"), None
    kb = {"inline_keyboard": [[{"text": "✅ 确认补全", "callback_data": f"bfgo:{tid}:{ep}"}]]}
    rng = (f"S{season:02d} " if season is not None else "") + f"E01~E{ep - 1:02d}"
    return (f"向前补全：《{base}》\n"
            f"频道：@{row['channel']}\n"
            f"当前已存：E{ep:02d}\n"
            f"将向前查找：{rng}（最多翻 50 页历史）\n"
            f"补全任务将优先消耗每日额度；早期链接已失效的集数会失败并注明原因。"), kb


async def _backfill_execute(tid: int, ep_hint: int = 0) -> str:
    """bfgo 回调：执行向前补全请求（与网页 API 共用同一执行器）。"""
    from ..tg.episode_backfill import submit_backfill
    row = db.query("SELECT id, title, channel, message_id, category, target_dir FROM transfers WHERE id=?",
                   (tid,), one=True)
    if not row:
        return "该记录已不存在"
    try:
        summary = await submit_backfill(row["channel"], row["message_id"], row["title"] or "",
                                        ep=ep_hint or None,
                                        anchor_category=row["category"] or "",
                                        anchor_target=row["target_dir"] or "")
    except ValueError as e:
        return f"❌ 无法发起补全：{e}"
    logstream.info("system", f"用户（TG）发起向前补全：《{summary['base']}》{summary['episodes']}，"
                             "补全任务将优先入队")
    return (f"🔎 已开始向前补全：《{summary['base']}》{summary['episodes']}\n"
            "补全任务将优先入队并优先消耗每日额度；完成后会推送汇总。")


async def _handle_subpick(client: "httpx.AsyncClient", token: str,
                          chat_id: str, data: str) -> None:
    """处理 subpick 按钮回调：完成订阅/取消，回复新消息（不动搜索结果）。"""
    idx = data.split(":", 1)[1].strip() if ":" in data else "x"
    entry = _SUB_SEARCH_CACHE.get(str(chat_id))
    if not entry or time.time() - entry[0] > _SUB_SEARCH_TTL:
        cands = []  # 过期按钮不再指向新的缓存，避免订错片
    else:
        cands = entry[1]
    if idx == "x" or not cands:
        reply = "已取消，或搜索选项已过期，请重新发送 /sub <片名>"
    else:
        try:
            it = cands[int(idx)]
        except (ValueError, IndexError):
            reply = "选项已过期，请重新发送 /sub <片名> 搜索"
        else:
            note = f"TMDB {'电视剧' if it['kind'] == 'tv' else '电影'}" + \
                   (f" · {it['year']}" if it["year"] else "")
            aliases = []
            if it.get("original") and it["original"] != it["name"]:
                aliases.append(it["original"])
            reply = _sub_add(it["name"], note, aliases=aliases,
                             tmdb_id=it.get("id"), kind=it.get("kind") or "")
    await _bot_send_text(client, token, chat_id, reply)


async def bot_command_loop():
    """TG Bot long polling：处理远程指令。未配置 Token 时跳过。

    每轮 long polling 前重新读取 Token/Chat ID/代理：网页改了配置后，
    当前客户端会被主动关闭并按新配置重建（配置热更新）。

    可靠性：
    - TG long polling 服务端最长挂起 timeout 秒才返回，读超时（ReadTimeout）是
      正常现象（代理抖动/网络慢时尤其常见）：静默重试即可，不当异常刷屏，
      也不会重建客户端（连接复用，避免每 40 秒重启一轮）。
    - 「已启动」只在配置变化后的第一次连接时提示一次，异常恢复重连不重复刷屏。
    - 指令处理（含查 115 空间等同步网络调用）放线程池执行，不阻塞事件循环。
    """
    global _last_update_id
    announced = None  # 已提示「已启动」的配置指纹 (token, chat_id, proxy)
    while True:
        token = str(db.get_setting("notify_token", "") or "").strip()
        chat_id = str(db.get_setting("notify_chat_id", "") or "").strip()
        proxy = _tg_proxy()
        if not token or not chat_id:
            announced = None
            await asyncio.sleep(30)
            continue
        try:
            # 读超时必须明显大于 TG 服务端挂起时长，正常 long polling 轮次不会触顶；
            # 剩余的偶发超时由下方 TimeoutException 分支静默处理
            timeout = httpx.Timeout(45.0, connect=15.0)
            async with httpx.AsyncClient(proxy=proxy, timeout=timeout) as client:
                fingerprint = (token, chat_id, proxy)
                if announced != fingerprint:
                    await register_bot_menu(client, token)  # 顺手把指令菜单注册到 TG
                    logstream.info("system", "TG Bot 远程指令已启动（/help 查看指令）")
                    announced = fingerprint
                while True:
                    # 配置变化 → 退出内层循环，用新配置重建客户端
                    cur_token = str(db.get_setting("notify_token", "") or "").strip()
                    cur_chat = str(db.get_setting("notify_chat_id", "") or "").strip()
                    if cur_token != token or cur_chat != chat_id or _tg_proxy() != proxy:
                        break
                    params = {"timeout": 25, "offset": _last_update_id + 1,
                              "allowed_updates": '["message","callback_query"]'}
                    try:
                        resp = await client.get(
                            f"https://api.telegram.org/bot{token}/getUpdates", params=params)
                    except httpx.TimeoutException:
                        continue  # long polling 超时/网络抖动：正常现象，立即发起下一轮
                    if resp.status_code != 200:
                        await asyncio.sleep(10)
                        continue
                    try:
                        updates = resp.json().get("result", [])
                    except Exception:
                        await asyncio.sleep(5)
                        continue
                    for upd in updates:
                        _last_update_id = max(_last_update_id, upd["update_id"])

                        # ---- 菜单按钮点击（callback_query）：原位编辑菜单消息 ----
                        cq = upd.get("callback_query")
                        if cq:
                            if str(cq.get("message", {}).get("chat", {}).get("id", "")) != chat_id:
                                continue
                            sender_ok, sender_hint = _check_sender(
                                {"chat": cq.get("message", {}).get("chat", {}),
                                 "from": cq.get("from") or {}})
                            if not sender_ok:
                                try:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json={"chat_id": chat_id, "text": sender_hint})
                                except Exception:
                                    pass
                                continue
                            try:  # 先应答回调，去掉按钮上的加载动画
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cq.get("id")})
                            except Exception:
                                pass
                            # 订阅搜索点选：独立流程，回复新消息
                            if (cq.get("data") or "").startswith("subpick:"):
                                await _handle_subpick(client, token, chat_id, cq["data"])
                                continue
                            # 向前补全：候选点选 / 确认执行 / 取消
                            if (cq.get("data") or "").startswith("bfpick:"):
                                parts = (cq.get("data") or "").split(":")
                                try:
                                    tid = int(parts[1])
                                    ep_hint = int(parts[2]) if len(parts) > 2 else 0
                                except ValueError:
                                    tid = 0
                                reply, kb = await asyncio.to_thread(
                                    _backfill_confirm, tid, ep_hint)
                                payload = {"chat_id": chat_id, "text": reply}
                                if kb:
                                    payload["reply_markup"] = kb
                                try:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json=payload)
                                except Exception:
                                    pass
                                continue
                            if (cq.get("data") or "").startswith("bfgo:"):
                                parts = (cq.get("data") or "").split(":")
                                try:
                                    tid = int(parts[1])
                                    ep_hint = int(parts[2]) if len(parts) > 2 else 0
                                except ValueError:
                                    tid, ep_hint = 0, 0
                                reply = await _backfill_execute(tid, ep_hint)
                                try:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json={"chat_id": chat_id, "text": reply})
                                except Exception:
                                    pass
                                continue
                            if (cq.get("data") or "").startswith("bfcancel:"):
                                try:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json={"chat_id": chat_id, "text": "已取消"})
                                except Exception:
                                    pass
                                continue
                            try:
                                reply, kb = await asyncio.to_thread(
                                    _handle_callback, cq.get("data") or "")
                            except Exception as e:
                                reply, kb = f"操作失败: {e}", {}
                            try:  # 原位编辑菜单消息（结果 + 刷新后的按钮）
                                payload = {"chat_id": chat_id,
                                           "message_id": cq.get("message", {}).get("message_id"),
                                           "text": reply}
                                if kb:
                                    payload["reply_markup"] = kb
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/editMessageText",
                                    json=payload)
                            except Exception:
                                try:  # 消息未变化等编辑失败场景：退化为发新消息
                                    payload = {"chat_id": chat_id, "text": reply}
                                    if kb:
                                        payload["reply_markup"] = kb
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json=payload)
                                except Exception:
                                    pass
                            continue

                        # ---- 文本指令 ----
                        msg = (upd.get("message")
                               or upd.get("channel_post")
                               or upd.get("edited_message") or {})
                        if str(msg.get("chat", {}).get("id", "")) != chat_id:
                            continue  # 非授权对话
                        sender_ok, sender_hint = _check_sender(msg)
                        if not sender_ok:
                            try:
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/sendMessage",
                                    json={"chat_id": chat_id, "text": sender_hint})
                            except Exception:
                                pass
                            continue  # 非绑定的管理员用户（群聊防成员滥用）
                        # ---- 备份文件恢复：直接发送/转发备份 JSON 给 Bot ----
                        doc = msg.get("document")
                        if doc and (str(doc.get("mime_type") or "").endswith("json")
                                    or str(doc.get("file_name") or "").lower().endswith(".json")):
                            allowed, hint = _restore_allowed(msg)
                            if not allowed:
                                reply = hint
                            else:
                                try:
                                    reply = await restore_upload_flow(client, token, chat_id, doc)
                                except Exception as e:
                                    reply = f"备份文件处理失败: {e}"
                            try:
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/sendMessage",
                                    json={"chat_id": chat_id, "text": reply})
                            except Exception:
                                pass
                            continue
                        text = (msg.get("text") or "").strip()
                        if not text.startswith("/"):
                            continue
                        try:
                            if text.lower().startswith("/restore"):
                                allowed, hint = _restore_allowed(msg)
                                arg = text.split(None, 1)[1].strip().lower() \
                                    if len(text.split(None, 1)) > 1 else ""
                                if not allowed:
                                    reply = hint  # 群聊里禁止恢复（含 /restore confirm）
                                elif arg == "confirm":
                                    reply = await asyncio.to_thread(_do_restore, chat_id)
                                else:
                                    reply = _restore_guide()
                            elif text.lower().startswith("/menu"):
                                reply = "🛠 快捷操作（点按钮执行，执行后菜单原位刷新）"
                                try:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json={"chat_id": chat_id, "text": reply,
                                              "reply_markup": _menu_keyboard()})
                                except Exception:
                                    pass
                                continue
                            elif text.lower().startswith("/add"):
                                # /add 需要异步做频道可访问性校验，单独处理
                                reply = await add_channel_flow(text.split(None, 1)[1]) \
                                    if len(text.split(None, 1)) > 1 else USAGE_ADD
                            elif text.lower().startswith("/backfill"):
                                arg = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
                                reply, kb = await asyncio.to_thread(_backfill_candidates, arg)
                                try:
                                    payload = {"chat_id": chat_id, "text": reply}
                                    if kb:
                                        payload["reply_markup"] = kb
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json=payload)
                                except Exception:
                                    pass
                                continue
                            elif text.lower().startswith("/sub"):
                                # /sub <片名>（非 del）：搜索订阅流程（异步 + 海报上传）
                                parts = text.split()
                                if len(parts) > 1 and parts[1].lower() != "del":
                                    await sub_search_flow(client, token, chat_id,
                                                          " ".join(parts[1:])[:100])
                                    continue
                                reply = await asyncio.to_thread(_handle_command, text, chat_id)
                            else:
                                reply = await asyncio.to_thread(_handle_command, text, chat_id)
                        except Exception as e:
                            reply = f"指令执行失败: {e}"
                        try:
                            await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                              json={"chat_id": chat_id, "text": reply})
                        except Exception:
                            pass  # 回复发送失败不影响指令已执行，下轮继续
        except Exception as e:
            announced = None  # 连接层异常（代理断开等）：恢复后重新提示一次「已启动」
            logstream.warn("system", f"TG Bot 指令轮询异常: {logstream.exc(e)}")
            await asyncio.sleep(15)
        await asyncio.sleep(1)
