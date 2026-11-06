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

# /sub 搜索结果缓存：chat_id -> 候选列表（subpick 按钮回调时取回对应条目）
_SUB_SEARCH_CACHE: dict[str, list] = {}
_SUB_NUMS = ["①", "②", "③", "④", "⑤", "⑥"]


def _tg_proxy() -> str | None:
    return str(db.get_setting("proxy", "") or "") or None


HELP = (
    "可用指令：\n"
    "/status — 查看运行状态（队列/限额/空间/频道）\n"
    "/space — 查看 115 剩余空间\n"
    "/ls — 最近 5 条转存记录\n"
    "/menu — 快捷操作菜单（按钮面板）\n"
    "/add <频道> — 添加监控频道\n"
    "/quota <数量> — 设置每日转存限额\n"
    "/sub — 查看订阅；/sub <片名> 搜索 TMDB 点选订阅；/sub del <关键词> 删除\n"
    "/pause — 暂停转存（停止消费队列）\n"
    "/resume — 恢复转存\n"
    "/retry — 重试全部失败任务\n"
    "/help — 显示本帮助"
)

# 注册到 Telegram 的指令菜单：输入框左侧「菜单」按钮 / 输入 / 时可见
BOT_MENU_COMMANDS = [
    {"command": "status", "description": "运行状态（队列/限额/空间）"},
    {"command": "space", "description": "115 剩余空间"},
    {"command": "ls", "description": "最近 5 条转存记录"},
    {"command": "menu", "description": "快捷操作菜单（按钮面板）"},
    {"command": "add", "description": "添加监控频道：/add @频道"},
    {"command": "quota", "description": "设置每日限额：/quota 30"},
    {"command": "sub", "description": "追剧订阅：查看/添加/删除"},
    {"command": "pause", "description": "暂停转存"},
    {"command": "resume", "description": "恢复转存"},
    {"command": "retry", "description": "重试全部失败任务"},
    {"command": "help", "description": "显示帮助"},
]


async def register_bot_menu(client: "httpx.AsyncClient", token: str) -> None:
    """把指令清单注册到 Telegram（setMyCommands），失败不影响运行。"""
    try:
        r = await client.post(f"https://api.telegram.org/bot{token}/setMyCommands",
                              json={"commands": BOT_MENU_COMMANDS})
        if r.status_code == 200 and r.json().get("ok"):
            logstream.info("system", "TG Bot 指令菜单已注册（输入框菜单按钮 / 输入 / 可见）")
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
                      else "⏸ 已暂停" if paused else "⏹ 未启动（网页顶栏可启动）")
        return (f"📊 运行状态\n\n{state_line}\n"
                f"📥 队列：{queued} 个待转存\n"
                f"✅ 24h 成功：{today_s}｜❌ 失败/拦截：{today_f}\n"
                f"📈 今日限额：{quota}/{limit}{space_line}")
    if cmd == "/space":
        try:
            from ..pan115 import client as pan
            sp = pan.space_info()
            return (f"💾 115 空间\n\n已用 {_gb(sp['use'])} / {_gb(sp['total'])}"
                    f"（{sp['ratio']}%）\n剩余 {_gb(sp.get('free', 0))}")
        except Exception as e:
            return f"查询空间失败: {str(e)[:120]}"
    if cmd == "/ls":
        rows = db.query("SELECT title, status, target_dir, created_at FROM transfers"
                        " ORDER BY id DESC LIMIT 5")
        if not rows:
            return "暂无转存记录"
        label = {"success": "✅", "failed": "❌", "blocked": "⛔", "queued": "⏳",
                 "running": "🔄", "skipped": "⏭", "duplicate": "🔁", "submitted": "☁️"}
        lines = ["🕘 最近转存", ""]
        for r in rows:
            title = (r["title"] or "-")[:40]
            mark = label.get(r["status"], "·")
            lines.append(f"{mark} {title}")
        return "\n".join(lines)
    if cmd == "/add":
        # 真正的添加流程由 bot_command_loop 的异步分支执行（需 await 频道校验），
        # 同步路径仅做用法提示兜底
        return "用法：/add <频道名或链接>\n例如：/add @somechannel"
    if cmd == "/quota":
        if not args or not args[0].lstrip("-").isdigit():
            return "用法：/quota <数量>（0 = 不限制，每日转存限额）"
        n = max(0, min(int(args[0]), 10000))
        db.set_setting("daily_limit", n)
        logstream.info("transfer", f"已通过 TG 指令设置每日限额 {n}（/quota）")
        return f"📈 每日限额已设为 {n}" + ("（不限制）" if n == 0 else "")
    if cmd == "/sub":
        return _handle_sub(args)
    if cmd in ("/pause", "/resume"):
        return _do_pause() if cmd == "/pause" else _do_resume()
    if cmd == "/retry":
        n = db.execute("UPDATE transfers SET status='queued', attempts=0, error='', next_retry_at=NULL"
                       " WHERE status='failed'")
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
        [{"text": "❓ 全部指令帮助", "callback_data": "c:help"}],
    ]}


def _submode_text() -> str:
    """各频道的订阅模式状态（只读展示，网页「频道管理」可调整）。"""
    rows = db.query("SELECT name, enabled, sub_only FROM channels ORDER BY id")
    if not rows:
        return "还没有监控频道。发送 /add <频道> 添加"
    lines = ["📺 频道订阅模式", ""]
    for r in rows:
        state = "🟢 全盘转存" if not r["sub_only"] else "🛎 仅订阅"
        if not r["enabled"]:
            state += "（频道已停用）"
        lines.append(f"@{r['name']} — {state}")
    lines.append("")
    lines.append("在网页「频道管理」页可按频道开关订阅模式；新增频道默认全盘转存")
    return "\n".join(lines)


def _handle_callback(data: str) -> tuple[str, dict]:
    """处理菜单按钮点击，返回 (回复文本, 键盘)。动作执行后菜单会原位刷新。"""
    data = (data or "").strip()
    if data == "toggle_pause":
        text = _do_resume() if db.get_setting("paused", False) else _do_pause()
        return text, _menu_keyboard()
    if data == "c:submode":
        return _submode_text(), _menu_keyboard()
    if data.startswith("c:"):
        cmd = data[2:].split()[0] if data[2:].strip() else ""
        args = data[2:].split()[1:]
        if cmd in ("status", "space", "ls", "sub", "retry", "help"):
            text = _handle_command(f"/{cmd} {' '.join(args)}".strip(), "")
            return text, (_menu_keyboard() if cmd != "help" else {})
    return "未知操作", _menu_keyboard()


async def add_channel_flow(name: str) -> str:
    """/add 指令的完整流程：解析名称 → 可访问性校验 → 入库。与网页添加同一套校验。"""
    import re as _re
    m = _re.search(r"t\.me/(?:s/)?@?([0-9A-Za-z_]+)", name)
    name = (m.group(1) if m else name).lstrip("@").strip()
    if not name:
        return "频道名不能为空。用法：/add <频道名或链接>"
    if len(name) > 64:
        return "频道名过长（最多 64 字符）"
    if db.query("SELECT 1 FROM channels WHERE type='polling' AND name=?", (name,), one=True):
        return f"频道 @{name} 已存在"
    n = db.query("SELECT count(*) c FROM channels", one=True)["c"]
    if n >= 50:
        return "监控频道已达上限 50 个，请先清理不需要的频道"
    from ..tg import polling as tg_polling
    res = await tg_polling.check_channel(name)
    if not res.get("ok"):
        return f"无法添加 @{name}：{res.get('err') or '频道不可访问，请核对后重试'}"
    title = str(res.get("title") or "")
    from ..core import subscribe as _submod  # 新频道继承全局订阅模式，之后可在网页按频道调整
    db.execute("INSERT INTO channels(type,name,sub_only,created_at) VALUES('polling',?,?,?)",
               (name, 1 if _submod.subscription_only() else 0, time.time()))
    logstream.info("channels",
                   f"已通过 TG 指令添加监控频道 @{name}（{title}，已通过访问校验）")
    return f"✅ 已添加频道 @{name}（{title}），下一轮轮询开始抓取"


def _handle_sub(args: list[str]) -> str:
    """/sub 查看订阅；/sub <片名> 走搜索订阅流程（见 sub_search_flow）；/sub del <关键词> 删除。"""
    from ..core import subscribe as _sub
    if args and args[0].lower() == "del":
        kw = " ".join(args[1:]).strip()
        if not kw:
            return "用法：/sub del <关键词>"
        n = db.execute("DELETE FROM subscriptions WHERE keyword=?", (kw,))
        if n:
            logstream.info("subscribe", f"已通过 TG 指令删除订阅: {kw}")
            return f"🗑 已删除订阅「{kw}」"
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


def _sub_add(name: str, note: str) -> str:
    """添加订阅（去重），返回给用户的回复文案。"""
    from ..core import subscribe as _sub
    exists = db.query("SELECT 1 FROM subscriptions WHERE keyword=?", (name,), one=True)
    if exists:
        return f"订阅「{name}」已存在"
    db.execute("INSERT INTO subscriptions(keyword,note,created_at) VALUES(?,?,?)",
               (name, note, time.time()))
    logstream.info("subscribe", f"已通过 TG Bot 搜索订阅: {name}（{note}）")
    return (f"✅ 已订阅「{name}」" +
            ("，订阅模式下仅转存命中它的内容" if _sub.subscription_only() else
             "，命中后会推送追更通知"))


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
        except Exception:
            continue
    cands.sort(key=lambda x: -x["popularity"])
    cands = [c for c in cands if c["poster_path"]][:6]
    if not cands:
        await _bot_send_text(client, token, chat_id,
                             f"没有找到与「{query}」相关的影视，试试换个名称")
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
    _SUB_SEARCH_CACHE[str(chat_id)] = kept

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


async def _handle_subpick(client: "httpx.AsyncClient", token: str,
                          chat_id: str, data: str) -> None:
    """处理 subpick 按钮回调：完成订阅/取消，回复新消息（不动搜索结果）。"""
    idx = data.split(":", 1)[1].strip() if ":" in data else "x"
    cands = _SUB_SEARCH_CACHE.get(str(chat_id), [])
    if idx == "x" or not cands:
        reply = "已取消"
    else:
        try:
            it = cands[int(idx)]
        except (ValueError, IndexError):
            reply = "选项已过期，请重新发送 /sub <片名> 搜索"
        else:
            note = f"TMDB {'电视剧' if it['kind'] == 'tv' else '电影'}" + \
                   (f" · {it['year']}" if it["year"] else "")
            reply = _sub_add(it["name"], note)
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
                        msg = upd.get("message") or {}
                        if str(msg.get("chat", {}).get("id", "")) != chat_id:
                            continue  # 非授权用户
                        text = (msg.get("text") or "").strip()
                        if not text.startswith("/"):
                            continue
                        try:
                            if text.lower().startswith("/menu"):
                                reply = "🛠 快捷操作（点按钮执行，执行后菜单原位刷新）"
                                try:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json={"chat_id": chat_id, "text": reply,
                                              "reply_markup": _menu_keyboard()})
                                except Exception:
                                    pass
                                continue
                            if text.lower().startswith("/add"):
                                # /add 需要异步做频道可访问性校验，单独处理
                                reply = await add_channel_flow(text.split(None, 1)[1]) \
                                    if len(text.split(None, 1)) > 1 else "用法：/add <频道名或链接>"
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
