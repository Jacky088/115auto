"""历史消息补漏：轮询页面固定只返回最新 20 条，频道高频更新或停机一段时间后
会产生消息缺口。本模块利用 t.me/s/<频道>?before=<id> 分页参数逐页向前回溯，
把缺口补齐——按每日转存限额的节奏慢慢消费，避免一次性打爆队列/触发风控。

缺口检测（polling 主循环内）：最新 20 条中最小 id > last_message_id+1 → 存在缺口
回溯方式：从「最新 20 条里最小的 id」开始向旧方向翻页，直到覆盖 last_message_id
节奏控制：每轮只处理 limited 条（与每日限额挂钩），未补完的缺口保留到下一轮
"""
import asyncio
import random
import time

import httpx

from .. import database as db
from ..core import logstream
from . import resolver

_TIMEOUT = httpx.Timeout(20.0)
_backfill_state: dict[int, dict] = {}  # channel_id -> {until_id, done}（内存态，重启重新检测）


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
        limit = int(db.get_setting("daily_limit", 50))
    except Exception:
        limit = 50
    return max(40, limit * 2)


async def backfill_channel(client: httpx.AsyncClient, ch: dict, newest_min_id: int) -> bool:
    """为一个频道补一段缺口 [last_message_id+1, newest_min_id-1]。

    返回 True 表示缺口已全部补完，False 表示本轮预算用完、还有剩余。
    """
    name = ch["name"]
    until_id = ch["last_message_id"]  # 补到这里为止（含）
    budget = _daily_backfill_budget()
    processed = 0
    before = newest_min_id  # 从最新一页的最小 id 向旧方向翻
    logstream.info("backfill", f"[{name}] 检测到消息缺口（{until_id + 1} ~ {newest_min_id - 1}），"
                               f"开始按页回溯（本轮预算 {budget} 条）")
    # 从测试注入的采集器（存在时记录每次翻页的 ID 范围，便于测试与调试）
    import sys
    page_log = getattr(sys.modules[__name__], "_page_log", None)

    while processed < budget:
        msgs = await _fetch_before(client, name, before)
        if not msgs:
            break  # 已到频道最早消息
        ids = [m["id"] for m in msgs]
        if max(ids) >= before:
            # 防御：服务端偶尔忽略 before 参数，避免死循环
            logstream.warn("backfill", f"[{name}] 分页参数异常（before={before} 未生效），本轮终止")
            break
        if page_log is not None:
            page_log.append((min(ids), max(ids)))
        # 只处理缺口范围内的（从旧到新）；已入过库的（上次截断点之后重复拉到的）不重复计数
        in_range = sorted([m for m in msgs if until_id < m["id"] < before], key=lambda m: m["id"])
        for m in in_range:
            exists = db.query("SELECT 1 FROM messages WHERE channel=? AND message_id=?",
                              (name, m["id"]), one=True)
            if not exists:
                if m["text"] or m.get("buttons"):
                    try:
                        full_text = await resolver.resolve_message(m["text"], m.get("buttons") or [])
                        if full_text:
                            from ..core import pipeline
                            pipeline.process_message(name, m["id"], full_text)
                    except Exception as e:
                        logstream.error("backfill", f"[{name}] 补漏消息 {m['id']} 处理失败: {e}")
                processed += 1  # 只统计真正新处理的，与预算语义一致
        if processed >= budget:
            # 接缝点 = 缺口内已入库的最小 id - 1（限定缺口范围防其他来源 id 污染）。
            # 下轮从接缝+1 继续向新补，已入库的会被去重，未补的中间段不会被跳过
            filled_min = db.query(
                "SELECT min(message_id) m FROM messages WHERE channel=? AND message_id>? AND message_id<?",
                (name, until_id, newest_min_id), one=True)["m"]
            marker = (filled_min - 1) if filled_min else until_id
            if marker > until_id:
                db.execute("UPDATE channels SET last_message_id=?, last_fetch_at=? WHERE id=?",
                           (marker, time.time(), ch["id"]))
                ch["last_message_id"] = marker
            remaining = newest_min_id - 1 - marker
            if remaining > 0:
                logstream.info("backfill", f"[{name}] 本轮预算用完，已补 {processed} 条，"
                                           f"接缝推进到 {marker}，剩余约 {remaining} 条下一轮继续")
                return False
        # 这一页全部落在缺口之外（更旧）→ 已补完
        if min(ids) <= until_id:
            break
        before = min(ids)
        await asyncio.sleep(random.uniform(3, 6))  # 翻页间隔，避免触发风控
    # 循环结束（预算内补完）：缺口已全部覆盖，接缝推进到缺口右端
    # （newest_min_id-1 即本轮回溯的起点消息 id，该点之前的缺口全部补完；
    #   若循环因"到达频道最早消息"退出且未补到缺口右端，说明缺口范围超出了频道历史，
    #   也以已入库消息的最大 id 兜底）
    filled_max = db.query(
        "SELECT max(message_id) m FROM messages WHERE channel=? AND message_id>? AND message_id<?",
        (name, until_id, newest_min_id), one=True)["m"]
    marker = max(newest_min_id - 1, (filled_max or until_id))
    if marker > until_id:
        db.execute("UPDATE channels SET last_message_id=?, last_fetch_at=? WHERE id=?",
                   (marker, time.time(), ch["id"]))
    logstream.info("backfill", f"[{name}] 缺口补漏完成，本轮共处理 {processed} 条")
    return True
