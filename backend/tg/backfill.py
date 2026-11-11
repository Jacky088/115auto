"""历史消息补漏：轮询页面固定只返回最新 20 条，频道高频更新或停机一段时间后
会产生消息缺口。本模块利用 t.me/s/<频道>?before=<id> 分页参数逐页向前回溯，
把缺口补齐——按每日转存限额的节奏慢慢消费，避免一次性打爆队列/触发风控。

缺口检测（polling 主循环内）：最新 20 条中最小 id > last_message_id+1 → 存在缺口
回溯方式：从「最新 20 条里最小的 id」开始向旧方向翻页，直到覆盖 last_message_id
节奏控制：每轮只处理 limited 条（与每日限额挂钩），未补完的缺口保留到下一轮

可靠性：
- 未补完时**绝不推进** last_message_id（否则缺口会被永久跳过）
- 分页异常/网络异常时同样不推进，保留缺口等下一轮重试
- 无文本消息也写入 messages 表，避免每轮重复扫描同一批消息
- 未补完时把续补游标存入 _backfill_state，下一轮从断点继续，不重复翻已处理的页
"""
import asyncio
import random
import time

import httpx

from .. import database as db
from ..core import logstream
from . import resolver

_TIMEOUT = httpx.Timeout(20.0)
_backfill_state: dict[int, dict] = {}  # channel_id -> {until_id, before, hi}（内存态，重启重新检测）


def _proxy() -> str | None:
    return str(db.get_setting("proxy", "") or "") or None


def _parse_page(html: str):
    """延迟导入 parse_page，避免与 polling.py 循环导入。"""
    from .polling import parse_page
    return parse_page(html)


async def _fetch_before(client: httpx.AsyncClient, name: str, before: int) -> list[dict]:
    resp = await client.get(f"https://t.me/s/{name}?before={before}", timeout=_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return _parse_page(resp.text)


def _daily_backfill_budget() -> int:
    """每轮补漏的消息处理上限：与每日转存限额挂钩（限额的 2 倍，最低 40）。"""
    try:
        limit = max(1, int(db.get_setting("daily_limit", 50)))
    except Exception:
        limit = 50
    return max(40, limit * 2)


def _remember_message(name: str, m: dict, text: str | None = None):
    """写入 messages 表（去重）。用于无文本消息与解析失败的消息，避免重复处理。"""
    db.execute(
        "INSERT OR IGNORE INTO messages(channel,message_id,text,processed_at) VALUES(?,?,?,?)",
        (name, m["id"], (text if text is not None else (m.get("text") or ""))[:2000], time.time()))


async def backfill_channel(client: httpx.AsyncClient, ch: dict, newest_min_id: int) -> bool:
    """为一个频道补一段缺口 [last_message_id+1, newest_min_id-1]。

    返回 True 表示缺口已全部补完，False 表示本轮预算用完/分页异常、还有剩余。
    未补完时不会推进 last_message_id，下一轮从 _backfill_state 记录的游标继续。
    """
    name = ch["name"]
    until_id = int(ch["last_message_id"])
    budget = await asyncio.to_thread(_daily_backfill_budget)
    processed = 0

    state = _backfill_state.get(ch["id"])
    if state and state.get("until_id") == until_id and int(state.get("before", 0)) > until_id:
        # 继续上一轮未补完的缺口（游标指向下一处未处理的右边界，且上方已全部处理）
        before = int(state["before"])
        hi = int(state.get("hi", newest_min_id))
        logstream.info("backfill", f"[{name}] 继续补漏缺口（{until_id + 1} ~ {before - 1}），"
                                   f"本轮预算 {budget} 条")
    else:
        before = int(newest_min_id)
        hi = int(newest_min_id)
        if before <= until_id:
            _backfill_state.pop(ch["id"], None)  # 无缺口：清掉可能残留的续补状态
            return True
        _backfill_state[ch["id"]] = {"until_id": until_id, "before": before, "hi": hi}
        logstream.info("backfill", f"[{name}] 检测到消息缺口（{until_id + 1} ~ {hi - 1}），"
                                   f"开始按页回溯（本轮预算 {budget} 条）")

    def remember(before_cursor: int):
        _backfill_state[ch["id"]] = {"until_id": until_id, "before": int(before_cursor), "hi": hi}

    async def finish_complete() -> bool:
        """缺口全部覆盖：接缝推进到缺口右端（hi-1）。"""
        marker = max(hi - 1, until_id)
        if marker > until_id:
            await asyncio.to_thread(
                db.execute,
                "UPDATE channels SET last_message_id=?, last_fetch_at=? WHERE id=?",
                (marker, time.time(), ch["id"]),
            )
            ch["last_message_id"] = marker
        _backfill_state.pop(ch["id"], None)
        logstream.info("backfill", f"[{name}] 缺口补漏完成，本轮共处理 {processed} 条")
        return True

    def partial(cursor: int) -> bool:
        remaining = max(cursor - until_id - 1, 0)
        logstream.info("backfill", f"[{name}] 本轮预算用完，已补 {processed} 条，"
                                   f"剩余约 {remaining} 条下一轮继续（接缝保持 {until_id}）")
        return False

    # 从测试注入的采集器（存在时记录每次翻页的 ID 范围，便于测试与调试）
    import sys
    page_log = getattr(sys.modules[__name__], "_page_log", None)

    while True:
        msgs = await _fetch_before(client, name, before)
        if not msgs:
            return await finish_complete()  # 已到频道最早消息，缺口范围内再没有可补的消息
        ids = [m["id"] for m in msgs]
        if max(ids) >= before:
            # 防御：服务端偶尔忽略 before 参数。不推进接缝，下一轮重试，避免死循环与漏消息
            logstream.warn("backfill", f"[{name}] 分页参数异常（before={before} 未生效），"
                                       f"本轮终止并保留缺口")
            remember(before)
            return False
        if page_log is not None:
            page_log.append((min(ids), max(ids)))

        # 只处理缺口范围内的（从旧到新）；已入过库的（上次截断点之后重复拉到的）不重复计数
        in_range = sorted([m for m in msgs if until_id < m["id"] < before], key=lambda m: m["id"])
        for m in in_range:
            if processed >= budget:
                # 预算在页中间用完：把游标停在第一条未处理消息上，下一轮从这里继续
                remember(m["id"] + 1)
                return partial(m["id"] + 1)
            exists = await asyncio.to_thread(
                db.query, "SELECT 1 FROM messages WHERE channel=? AND message_id=?",
                (name, m["id"]), True)
            if exists:
                continue
            if m.get("text") or m.get("buttons"):
                try:
                    full_text = await resolver.resolve_message(m["text"], m.get("buttons") or [])
                    if full_text:
                        from ..core import pipeline
                        # 与 polling/userbot 一致：管道含多条 DB 读写，放线程池防阻塞事件循环
                        await asyncio.to_thread(
                            pipeline.process_message, name, m["id"], full_text)
                    else:
                        await asyncio.to_thread(_remember_message, name, m)
                except Exception as e:
                    logstream.error("backfill", f"[{name}] 补漏消息 {m['id']} 处理失败: {e}")
                    await asyncio.to_thread(_remember_message, name, m)
            else:
                await asyncio.to_thread(_remember_message, name, m)
            processed += 1

        before = min(ids)
        if before <= until_id:
            return await finish_complete()
        remember(before)
        if processed >= budget:
            return partial(before)
        await asyncio.sleep(random.uniform(3, 6))  # 翻页间隔，避免触发风控
