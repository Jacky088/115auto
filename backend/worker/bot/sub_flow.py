"""TG Bot 监控频道添加、追剧订阅与向前补全逻辑。"""
import json
import re
import time

import httpx

from ... import database as db
from ...core import logstream
from ...core.executors import run_ext_thread
from .link_flow import _usage_add
from .state import USAGE_SUB, _SUB_NUMS, _SUB_SEARCH_CACHE, _SUB_SEARCH_TTL, _tg_proxy


async def add_channel_flow(raw: str) -> str:
    """/add 指令完整流程：语法 /add <频道名或链接> on|off（on=开启仅订阅，off=全盘转存）。

    语法必须完整：缺 on/off 或写别的值都添加失败，并返回正确用法。
    """
    tokens = str(raw or "").split()
    if len(tokens) != 2 or tokens[1].lower() not in ("on", "off"):
        return _usage_add()
    name, mode = tokens[0], tokens[1].lower()
    m = re.search(r"t\.me/(?:s/)?@?([0-9A-Za-z_]+)", name)
    name = (m.group(1) if m else name).lstrip("@").strip()
    if not name:
        return _usage_add()
    if len(name) > 64:
        return "频道名过长（最多 64 字符）"
    if not re.fullmatch(r"[0-9A-Za-z_]{1,64}", name):
        return "频道名仅支持字母、数字与下划线（1-64 位）"
    if db.query("SELECT 1 FROM channels WHERE name=? COLLATE NOCASE", (name,), one=True):
        # 大小写不敏感查重：@Foo 与 @foo 是同一频道，双链路接入会导致消息重复处理
        # 不限制 type：同一频道若已有 userbot 记录，再加 polling 会导致消息被两条链路重复处理
        return f"频道 @{name} 已存在。订阅模式可在网页「账号管理」按频道开关"
    n = db.query("SELECT count(*) c FROM channels", one=True)["c"]
    if n >= 50:
        return "监控频道已达上限 50 个，请先清理不需要的频道"
    from ...tg.validate import check_channel_access
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
            "下一轮轮询开始抓取；之后可在网页「账号管理」调整该开关")


def _handle_sub(args: list[str]) -> str:
    """/sub 查看订阅；/sub <片名> 走搜索订阅流程（见 sub_search_flow）；/sub del <关键词> 删除。"""
    from ...core import subscribe as _sub
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
        return USAGE_SUB
    lines = ["🎬 追剧订阅", ""]
    for r in rows:
        ep = f"（{r['last_ep']}）" if r["last_ep"] else ""
        mark = "" if r["enabled"] else "⏸ "
        lines.append(f"· {mark}{r['keyword']}{ep}")
    lines.append("")
    lines.append(USAGE_SUB)
    return "\n".join(lines)


async def _bot_send_text(client: httpx.AsyncClient, token: str, chat_id: str,
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
    from ...core import subscribe as _sub
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
            "是否仅转存命中内容由「账号管理」的仅订阅开关决定")


async def sub_search_flow(client: httpx.AsyncClient, token: str,
                          chat_id: str, query: str) -> None:
    """/sub <片名>：搜索 TMDB → 相册发海报（带编号说明）→ 编号按钮点选订阅。"""
    from ...api.manage import _tmdb_get, _tmdb_item
    api_key = str(db.get_setting("tmdb_api_key", "") or "").strip()
    if not api_key:
        await _bot_send_text(client, token, chat_id,
                             "请先在网页「订阅追剧」页填写并保存 TMDB API Key，再使用搜索订阅")
        return
    cands = []
    for k in ("tv", "movie"):
        try:
            # TMDB 搜索是同步 httpx 调用，必须放外部网络专用池，
            # 否则慢代理下既会卡死事件循环，也会挤占默认池拖累 TG 轮询
            resp = await run_ext_thread(
                _tmdb_get, f"/search/{k}",
                {"api_key": api_key, "query": query, "language": "zh-CN"})
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
            img = await run_ext_thread(
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
    from ...tg.episode_backfill import should_backfill
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
    if not row or row["status"] not in ("success", "failed") or row["kind"] != "share" or not row["message_id"]:
        return "该记录不可作为补全锚点（需为已转存的分享，含已失效的）", None
    from ...tg.episode_backfill import base_title, parse_episode
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
    from ...tg.episode_backfill import submit_backfill
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


async def _handle_subpick(client: httpx.AsyncClient, token: str,
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
