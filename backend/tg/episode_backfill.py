"""追剧向前补全：周播剧从中间集数开始追时（如首看即 E03），自动向前翻找
频道历史里同一剧集的早期分集（E01、E02）并补转存。

触发：用户显式发起（网页 /api/backfill/episodes 或 Bot /backfill）→ submit_backfill()
入队 → episode_backfill_loop() 向前翻页 https://t.me/s/<频道>?before=<消息ID>，
匹配「同剧且集数更早」的帖子，
正文经 resolver 解析链接后交给 pipeline.process_message（复用黑名单/分类/判重/限额），
任务带增量标记（increment=1）：已存过的集数自动跳过，不会重复转存。

链接失效的处理：同一集收集频道里能找到的全部候选链接逐个入队——
先成功的落盘，其余因增量比对自动跳过；全部无效则任务失败并注明原因，
之后分享方补发新链接时会照常走转存链路。

限制：仅支持网页轮询类型的频道（t.me/s 公开网页可翻页）；向前翻页有预算上限，
防止在超活跃频道里无限翻页；跨帖匹配要求集数标记格式一致（S01E52 / 第52集 / EP52）。
"""
import asyncio
import re

import httpx

from .. import database as db
from ..core import classifier, episode, logstream, pipeline, transfer_gate
from ..worker import jobs
from . import polling, resolver

_MAX_PAGES = 50       # 向前翻页预算（页），约覆盖上千条历史消息
_PAGE_SLEEP = 1.5     # 翻页间隔（秒），防限流

_queue: asyncio.Queue | None = None
_searched_min: dict[tuple[str, str], int] = {}  # (频道, 剧名) → 已翻到的最早消息 ID
_PENDING_KEY = "backfill_pending"  # 待处理补全请求持久化（重启后重新入队）


def _persist_add(req: dict) -> None:
    """补全请求落库：重启后不丢失用户显式发起的补全。"""
    try:
        pending = db.get_setting(_PENDING_KEY, []) or []
        pending.append(req)
        db.set_setting(_PENDING_KEY, pending)
    except Exception:
        pass


def _persist_done(req: dict) -> None:
    try:
        pending = db.get_setting(_PENDING_KEY, []) or []
        pending = [p for p in pending if p != req]
        db.set_setting(_PENDING_KEY, pending)
    except Exception:
        pass

_YEAR_PAREN = re.compile(r"[（(]\s*\d{4}\s*[）)]\s*")


def parse_episode(title: str) -> tuple[int | None, int | None]:
    """从标题解析 (季, 集)。识别不出返回 (None, None)。

    与转存通知/订阅追更共用 backend/core/episode.py 的解析口径，
    支持 S01E05、EP12、第5集、5集、第3期 等写法。
    """
    season, ep, _kind = episode.parse(title)
    return season, ep


def base_title(title: str) -> str:
    """去掉集数标记与年份括号后的「剧名」（去空白归一化）。

    用于跨帖匹配同一剧集：同剧不同集的标题去掉集数标记与 (年份) 后应完全一致。
    年份括号是标题中最常见的不稳定因素（有的帖子带有的不带），剥掉后匹配更可靠。
    """
    t = episode.strip_markers(title or "")
    t = _YEAR_PAREN.sub("", t)
    t = t.strip(" 　-—_|·：:，,。.")
    return re.sub(r"\s+", "", t)


def should_backfill(title: str):
    """成功任务是否需要向前补全。

    返回 (剧名, 季|None, 当前集)；集数未识别、或就是第 1 集（无需向前）时返回 None。
    """
    season, ep = parse_episode(title)
    if ep is None or ep <= 1:
        return None
    return base_title(title), season, ep


def _match_post(text: str, base: str, cur_season: int | None, cur_ep: int) -> int | None:
    """帖子是否属于同一剧集且集数早于当前集。返回其集数；不匹配返回 None。

    base 为剧名关键词（已归一化）：自动解析时是完整剧名，手动发起时允许用户填写
    简短关键词。匹配要求 base 之后不能紧跟数字/字母/汉字（边界校验），
    防止《斗罗大陆》误配《斗罗大陆2》《斗罗大陆剧场版》——错剧分集帖会带着
    原剧的目标目录入队转存。手动关键词为兼容模糊搜索保留后缀宽松匹配
    （如「斗罗大陆 4K」仍可命中）。
    季号必须双方都能识别且相等，任一方缺失即保守跳过——防跨季误补
    （存错季不可接受，宁可漏配后在汇总中透明列出）。
    """
    head = classifier.headline(text or "")
    if not head:
        return None
    head_norm = base_title(head)
    if not head_norm:
        return None
    if head_norm != base and not re.search(re.escape(base) + r"(?![0-9A-Za-z\u4e00-\u9fff])", head_norm):
        return None
    season, ep = parse_episode(text or "")
    if ep is None or ep >= cur_ep:
        return None
    if (cur_season is None) != (season is None):
        return None
    if cur_season is not None and season != cur_season:
        return None
    return ep


async def submit_backfill(channel: str, message_id: int, title: str,
                          base: str = "", season: int | None = None,
                          ep: int | None = None,
                          anchor_category: str = "", anchor_target: str = "") -> dict:
    """用户显式发起向前补全（网页 /api/backfill/episodes 与 Bot /backfill 共用入口）。

    剧名/集数默认从锚点标题解析，解析不出时由调用方传入（网页弹窗可手动填写）。
    anchor_category/anchor_target 为锚点任务的分类与目标目录：补全的历史分集帖
    沿用它们，避免同剧不同帖分类漂移。返回摘要；无法确定时抛 ValueError。
    """
    global _queue
    title = str(title or "")
    parsed_season, parsed_ep = parse_episode(title)
    season = season if season is not None else parsed_season
    ep = ep if ep is not None else parsed_ep
    base = str(base or "").strip()
    if not base:
        base = base_title(title)
    if not base:
        raise ValueError("无法从标题解析剧名，请手动填写剧名关键词后再发起补全")
    if ep is None:
        raise ValueError("无法从标题解析当前集数（如 S01E52 / 第52集），请手动指定后再发起补全")
    if ep <= 1:
        raise ValueError("当前已是第 1 集，没有更早的分集需要补全")
    channel = str(channel or "").strip().lstrip("@")
    if not channel or message_id <= 0:
        raise ValueError("缺少来源频道或消息锚点，无法向前翻页")
    ch = await asyncio.to_thread(db.query, "SELECT type FROM channels WHERE name=?", (channel,), True)
    if ch and ch["type"] != "polling":
        # 发起时就明确拒绝，避免"回执成功但永远没有结果"的假成功
        raise ValueError("该频道为 Userbot 接入，暂不支持向前补全（仅网页轮询频道可翻页查找）")
    base = re.sub(r"\s+", "", base)
    if _queue is None:
        _queue = asyncio.Queue()
    req = {"channel": channel, "message_id": int(message_id), "title": title,
           "base": base, "season": season, "ep": int(ep),
           "anchor_category": str(anchor_category or ""),
           "anchor_target": str(anchor_target or "")}
    await _queue.put(req)
    await asyncio.to_thread(_persist_add, req)
    episodes = (f"S{season:02d} " if season is not None else "") + f"E01~E{ep - 1:02d}"
    return {"base": base, "ep": int(ep), "episodes": episodes, "channel": channel}


async def episode_backfill_loop():
    logstream.info("system", "追剧向前补全任务启动（等待网页/Bot 发起补全请求）")
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    try:  # 重启恢复：上次未处理完的补全请求重新入队
        pending = await asyncio.to_thread(db.get_setting, _PENDING_KEY, []) or []
        for req in pending:
            await _queue.put(req)
        if pending:
            logstream.info("system", f"恢复 {len(pending)} 个待处理的向前补全请求")
    except Exception:
        pass
    while True:
        req = await _queue.get()
        try:
            # 门闸协同：处于整理排空或独占移动期时挂起等待，避免向排空中队列插入新任务
            await transfer_gate.wait_intake_allowed()
            await _process_request(req)
            await asyncio.to_thread(_persist_done, req)
        except Exception as e:
            logstream.error("backfill", f"追剧向前补全异常: {logstream.exc(e)}")


async def _process_request(req: dict) -> None:
    channel = req["channel"]
    message_id = req["message_id"]
    base = req["base"]
    cur_season = req["season"]
    cur_ep = req["ep"]
    anchor = ({"category": req["anchor_category"], "target_dir": req["anchor_target"]}
              if req.get("anchor_category") or req.get("anchor_target") else None)

    # Userbot 接入的频道不可翻页（t.me/s 仅公开频道）；发起时已在 submit 校验，此处兜底
    ch = await asyncio.to_thread(db.query, "SELECT type FROM channels WHERE name=?", (channel,), True)
    if ch and ch["type"] != "polling":
        logstream.info("backfill", f"向前补全跳过：频道 @{channel} 非网页轮询接入（暂不支持向前翻页）")
        return

    key = (channel, base)
    searched_floor = _searched_min.get(key, 0)  # 此前已翻到的最早位置：避免重复翻同一段历史
    logstream.info("backfill",
                   f"[{channel}] 《{base}》当前已转存第 {cur_ep} 集，向前翻找早期分集（E01~E{cur_ep - 1:02d}）")

    found: dict[int, list[dict]] = {}  # 集数 → 候选帖子（含全部链接，逐个尝试）
    before = message_id
    pages = 0
    aborted = False  # 翻页异常中断：本轮不推进已搜索范围（下次可重搜这段历史）
    lowest_visited = message_id  # 本轮实际翻到的最早位置（含空页与找齐提前停止的场景）
    async with httpx.AsyncClient(proxy=polling._proxy(), follow_redirects=True) as client:
        while pages < _MAX_PAGES:
            if before <= searched_floor:
                break  # 已翻到此前搜索过的最早位置，这段历史无需重翻
            try:
                resp = await client.get(f"https://t.me/s/{channel}",
                                        params={"before": before}, timeout=20)
            except Exception as e:
                logstream.warn("backfill", f"[{channel}] 向前翻页失败（停止本轮）: {e}")
                aborted = True
                break
            if resp.status_code != 200:
                logstream.warn("backfill", f"[{channel}] 向前翻页 HTTP {resp.status_code}（停止本轮）")
                aborted = True
                break
            posts = await asyncio.to_thread(polling.parse_page, resp.text)
            if not posts:
                lowest_visited = min(lowest_visited, before)  # 空页也算翻到该位置（已到频道最早消息）
                break  # 已到频道最早消息
            ids = [m["id"] for m in posts]
            if min(ids) >= before:
                # 防御：服务端偶尔忽略 before 参数。不推进已搜索范围，避免死循环
                logstream.warn("backfill", f"[{channel}] 分页参数异常（before={before} 未生效），本轮终止")
                aborted = True
                break
            pages += 1
            lowest_visited = min(lowest_visited, min(ids))
            for p in posts:
                if p["id"] >= message_id or p["id"] <= searched_floor:
                    continue
                epn = _match_post(p["text"] or "", base, cur_season, cur_ep)
                if epn is not None:
                    found.setdefault(epn, []).append(p)
            # 更早的每一集都找到了候选 → 无需继续翻
            if all(epn in found for epn in range(1, cur_ep)):
                break
            before = min(ids)
            await asyncio.sleep(_PAGE_SLEEP)

    # 只有自然结束（预算用尽/翻到频道头部/集数找齐）才推进已搜索范围：
    # 异常中断的历史段保持"未搜索"，之后再次发起补全时会重搜，不留下永久盲区。
    # 记录本轮实际翻到的最早位置而非"最早命中的帖 id"——否则早期因集数不匹配被
    # 跳过的分集帖会落在已搜索范围之外，再次发起补全时永久漏补
    if not aborted:
        _searched_min[key] = min(_searched_min.get(key, message_id), lowest_visited)

    if not found:
        logstream.info("backfill",
                       f"[{channel}] 《{base}》向前翻找 {pages} 页：未找到第 1~{cur_ep - 1} 集的历史分集帖"
                       "（可能超出翻页预算或频道未发过）")
        await asyncio.to_thread(
            jobs.notify,
            "🔎 追剧向前补全\n\n"
            f"📺 《{base}》当前已转存第 {cur_ep} 集\n"
            f"向前翻找 {pages} 页历史：未找到第 1~{cur_ep - 1} 集的分集帖"
            "（可能超出翻页预算，或频道从未发过这些集）")
        return

    # 逐集补全：老集数先入队；同一集的全部候选帖子都交给管道（增量标记保证
    # 先成功的落盘、其余自动跳过），链接失效的任务会失败并注明原因
    enqueued = 0
    episodes = []
    already: set[int] = set()  # 已在网盘/已处理过、无需重复转存的集数
    for epn in sorted(found):
        episodes.append(f"E{epn:02d}")
        for p in sorted(found[epn], key=lambda x: x["id"]):
            # 翻页耗时较长，正式入队前再次确认未进入排空/整理阶段
            await transfer_gate.wait_intake_allowed()
            try:
                full_text = await resolver.resolve_message(p["text"] or "", p.get("buttons") or [])
            except Exception as e:
                logstream.warn("backfill", f"[{channel}] 补全帖 #{p['id']} 链接解析失败: {e}")
                continue
            if not full_text:
                continue
            r = await asyncio.to_thread(pipeline.process_message,
                                        channel, p["id"], full_text, False, True, 1, anchor)
            enqueued += r.get("queued", 0)
            # 该集此前已处理过（消息级去重命中）或分享此前已成功（判重留痕）
            # → 说明网盘里已经有这一集，汇总里单独标注，不与新增混淆
            if r.get("duplicate") or r.get("duplicates"):
                already.add(epn)

    logstream.info("backfill",
                   f"[{channel}] 《{base}》向前补全完成：翻找 {pages} 页，"
                   f"找到 {len(found)} 集的候选链接，新入队 {enqueued} 个转存任务"
                   + (f"，{len(already)} 集已存在无需重复" if already else ""))
    missing = [f"E{epn:02d}" for epn in range(1, cur_ep) if epn not in found]
    lines = ["🔎 追剧向前补全", "",
             f"📺 《{base}》当前已转存第 {cur_ep} 集",
             f"✅ 已找到早期分集：{'、'.join(episodes)}（新加入转存队列 {enqueued} 个任务）"]
    if already:
        lines.append(f"ℹ️ 已在网盘、无需重复转存："
                     f"{'、'.join(f'E{e:02d}' for e in sorted(already))}")
    if missing:
        lines.append(f"⚠️ 频道历史中未找到：{'、'.join(missing)}")
    lines.append("💡 早期链接若已失效，对应任务会失败并注明原因；分享方补发新链接后会照常转存")
    await asyncio.to_thread(jobs.notify, "\n".join(lines))
