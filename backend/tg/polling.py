"""TG 公开频道网页轮询：抓取 https://t.me/s/<频道>，按 message_id 增量解析。免 TG 账号、零风控。"""
import asyncio
import random
import re
import time

import httpx
from bs4 import BeautifulSoup

from .. import database as db
from ..core import logstream, pipeline
from . import backfill, resolver

_TIMEOUT = httpx.Timeout(20.0)


def _proxy() -> str | None:
    return str(db.get_setting("proxy", "") or "") or None


def parse_page(html: str) -> list[dict]:
    """解析 t.me/s 页面，返回 [{id, text, buttons}]。buttons 为内联按钮的 URL。

    关键：频道常把 115 链接放在消息正文 <a href> 里（显示为"点击跳转"），
    get_text() 会把 href 丢掉只剩这四个字 → 链接提取为空。
    因此把正文内所有 <a> 的 href 注回到文本中，保证提取器能看到真实链接。
    """
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for msg in soup.select(".tgme_widget_message[data-post]"):
        try:
            mid = int(msg["data-post"].rsplit("/", 1)[-1])
        except Exception:
            continue
        parts = []
        for node in msg.select(".tgme_widget_message_text"):
            # 先把 <a href> 的地址注回文本（Telegram 网页版正文链接的真实位置）
            for a in node.find_all("a", href=True):
                href = a["href"]
                if href.startswith(("http://", "https://", "magnet:", "ed2k:", "thunder:",
                                    "115:", "ftp://")):
                    a.insert_before(soup.new_string(f"\n{href}\n"))
            parts.append(node.get_text("\n", strip=True))
        text = "\n".join(parts)
        buttons = [a.get("href") for a in msg.select(".tgme_widget_message_inline_button")
                   if a.get("href")]
        out.append({"id": mid, "text": text, "buttons": buttons})
    return out


async def fetch_channel(client: httpx.AsyncClient, name: str) -> list[dict]:
    resp = await client.get(f"https://t.me/s/{name}", timeout=_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}（频道不存在或为私密频道）")
    # BeautifulSoup 解析约 1MB 页面是 CPU 密集操作（单次可达上百毫秒），
    # 移到线程池执行，避免卡住事件循环导致 WS 心跳超时（前端显示「未连接」）与页面请求排队
    return await asyncio.to_thread(parse_page, resp.text)


async def check_channel(name: str) -> dict:
    """添加频道前的可访问性校验：网页能打开该频道的 t.me/s 页面才算通过。

    返回 {ok, title, err}。区分「不存在/用户名错」「存在但网页预览不可用」两类原因，
    便于用户按提示修正（改用 Userbot 或核对名称）。
    """
    async with httpx.AsyncClient(proxy=_proxy(), follow_redirects=True) as client:
        try:
            resp = await client.get(
                f"https://t.me/s/{name}", timeout=_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0"})
        except Exception as e:
            return {"ok": False, "title": "", "err": (
                f"无法访问 Telegram 网页，请检查网络/代理设置："
                f"{type(e).__name__}: {str(e)[:120]}")}
    if resp.status_code != 200:
        return {"ok": False, "title": "", "err": (
            f"HTTP {resp.status_code}（频道不存在、已注销或为私密频道）")}
    html = resp.text
    if "tgme_channel_info" not in html:
        if "Telegram: Contact @" in html:
            return {"ok": False, "title": "", "err": "频道不存在或用户名错误，请核对 @用户名/链接"}
        return {"ok": False, "title": "", "err": (
            "该频道无法通过网页访问（私密频道或未开启网页预览），请改用 Userbot 接入")}
    m = re.search(r'<meta property="og:title" content="([^"]*)"', html)
    title = (m.group(1).strip() if m else "") or name
    return {"ok": True, "title": title, "err": ""}


async def _poll_one(client: httpx.AsyncClient, ch: dict):
    name = ch["name"]
    msgs = await fetch_channel(client, name)
    if not msgs:
        return
    last = ch["last_message_id"]
    new = [m for m in msgs if m["id"] > last]
    if not new:
        return
    new.sort(key=lambda m: m["id"])

    # 缺口检测：最新一页的最小 id 超过 last+1 → 中间有消息没见过（高频更新/停机漏掉）
    # 交给 backfill 按 ID 向旧方向逐页回溯补齐（按每日限额节奏慢慢补）
    preserve_gap = False
    if last > 0 and new[0]["id"] > last + 1:
        try:
            done = await backfill.backfill_channel(client, ch, new[0]["id"])
        except Exception as e:
            # 补漏失败（网络/分页异常）：缺口必须保留，否则 last_message_id 一推进就永久漏消息
            logstream.error("backfill", f"[{name}] 补漏任务异常（保留缺口，下一轮重试）: {e}")
            preserve_gap = True
        else:
            if not done:
                return  # 本轮预算用完，剩余缺口下轮继续；最新消息下轮一起入队
            # 续补完成后，最新页与接缝之间可能仍有新缺口（期间又来了新消息），再补一次
            if new[0]["id"] > ch["last_message_id"] + 1:
                try:
                    done = await backfill.backfill_channel(client, ch, new[0]["id"])
                except Exception as e:
                    logstream.error("backfill", f"[{name}] 补漏任务异常（保留缺口，下一轮重试）: {e}")
                    preserve_gap = True
                else:
                    if not done:
                        return

    for m in new:
        if m["text"] or m.get("buttons"):
            try:
                # 解析内联按钮里的资源链接（如"查看资源"→ telegra.ph 文章）
                full_text = await resolver.resolve_message(m["text"], m.get("buttons") or [])
                if full_text:
                    # 消息入库/分类/去重是同步 DB 操作：放线程池，不阻塞事件循环
                    await asyncio.to_thread(pipeline.process_message, name, m["id"], full_text)
            except Exception as e:
                logstream.error("polling", f"[{name}] 消息 {m['id']} 处理失败: {e}")
    if preserve_gap:
        logstream.warn("polling", f"[{name}] 补漏未完成，本轮不推进 last_message_id（下一轮继续补）")
        return
    await asyncio.to_thread(
        db.execute,
        "UPDATE channels SET last_message_id=?, last_fetch_at=? WHERE id=?",
        (new[-1]["id"], time.time(), ch["id"]),
    )
    logstream.info("polling", f"[{name}] 新增 {len(new)} 条消息")


async def polling_loop():
    logstream.info("polling", "网页轮询已启动（等待「开始运行」）")
    waiting_logged = False
    while True:
        if not await asyncio.to_thread(db.get_setting, "running", False):
            if not waiting_logged:
                logstream.info("polling", "系统未启动，轮询待机中")
                waiting_logged = True
            await asyncio.sleep(5)
            continue
        waiting_logged = False
        # TG 网页轮询仅解析消息并入库到 SQLite，不触达 115 接口，不受整理排空门闸阻塞

        chans = [dict(r) for r in await asyncio.to_thread(
            db.query, "SELECT * FROM channels WHERE type='polling' AND enabled=1")]
        if chans:
            async with httpx.AsyncClient(proxy=await asyncio.to_thread(_proxy), follow_redirects=True) as client:
                for ch in chans:
                    try:
                        await _poll_one(client, ch)
                        await asyncio.to_thread(db.set_setting, f"poll_fail_{ch['id']}", 0)  # 成功即清零失败计数
                    except Exception as e:
                        # 连续失败计数：供运行健康监控判定 TG 通道失效（全频道超阈值 → 自动停止）
                        def _bump_poll_fail(cid: int) -> int:
                            try:
                                fails = int(db.get_setting(f"poll_fail_{cid}", 0) or 0)
                            except (TypeError, ValueError):
                                fails = 0
                            db.set_setting(f"poll_fail_{cid}", fails + 1)
                            return fails + 1

                        new_fails = await asyncio.to_thread(_bump_poll_fail, ch["id"])
                        logstream.error("polling", f"[{ch['name']}] 抓取失败（连续 {new_fails} 次）: "
                                                   f"{logstream.exc(e)}")

                    def _read_poll_interval():
                        try:
                            lo = max(1, int(db.get_setting("poll_interval_min", 30)))
                            hi = max(1, int(db.get_setting("poll_interval_max", 120)))
                        except (TypeError, ValueError):
                            lo, hi = 30, 120
                        return lo, hi

                    lo, hi = await asyncio.to_thread(_read_poll_interval)
                    # 按设置页承诺的「区间内随机」取值；旧实现把上限截到 lo+5，导致 max 基本不生效
                    await asyncio.sleep(random.uniform(lo, hi) if hi > lo else lo)
        else:
            await asyncio.sleep(15)
