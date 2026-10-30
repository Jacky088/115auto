"""内联按钮链接解析：TG 消息正文中没有资源链接、链接藏在"查看资源"等内联按钮里时，
抓取按钮指向的页面（telegra.ph 文章 / 跳转页等），从中提取真实的 115 分享 / ED2K / 磁力链接。

- 文本里已有资源链接时不再解析按钮（省请求）
- t.me 站内跳转按钮直接忽略
- 解析结果按按钮 URL 缓存（Telegraph 页面内容不变）
"""
import asyncio
import re

import httpx

from .. import database as db
from ..core import extractor, logstream

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_MAX_BUTTONS = 2          # 每条消息最多解析的按钮数
_MAX_BYTES = 300_000      # 中间页读取上限
_cache: dict[str, str] = {}
_CACHE_MAX = 5000


def _tg_proxy() -> str | None:
    """Telegraph 等中间页在国内可能无法直连，与 TG 流量共用代理设置。"""
    return str(db.get_setting("proxy", "") or "") or None


def is_direct_resource(url: str) -> bool:
    """按钮本身就是资源链接（115 分享/秒传/ed2k/magnet）。"""
    return bool(re.match(
        r"^(https?://)?(www\.)?(115\.com|115cdn\.com|anxia\.com)/s/|115://|ed2k://|magnet:\?xt=",
        url, re.IGNORECASE))


def is_tg_internal(url: str) -> bool:
    return url.startswith("t.me/") or url.startswith("https://t.me/")
    # t.me 站内跳转（机器人/频道推广），无资源价值


def _extract_from_html(html: str) -> str:
    """从页面 HTML 里提取资源链接文本（链接可能在 href 或正文中）。"""
    found = extractor.extract(html)
    return "\n".join(l["share_url"] for l in found)


async def _fetch_page(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, timeout=15, follow_redirects=True,
                            headers={"User-Agent": _UA})
    if resp.status_code != 200:
        return ""
    return resp.text[:_MAX_BYTES]


async def resolve_message(text: str, buttons: list[str]) -> str:
    """合并消息正文与按钮解析出的链接，返回增强后的完整文本。"""
    combined = (text or "").rstrip()
    if extractor.extract(combined):
        return combined  # 正文已有链接，按钮多为推广，跳过
    resolved = 0
    for url in buttons or []:
        if resolved >= _MAX_BUTTONS or len(combined) > 20_000:
            break  # 每条消息最多解析 2 个按钮 + 内容过长保护
        url = (url or "").strip()
        if not url:
            continue
        if is_tg_internal(url):
            continue
        if is_direct_resource(url):
            combined += "\n" + url
            continue
        # 中间页：查缓存 / 抓取解析
        resolved += 1
        if url in _cache:
            extracted = _cache[url]
        else:
            try:
                async with httpx.AsyncClient(proxy=_tg_proxy()) as client:
                    html = await _fetch_page(client, url)
                extracted = _extract_from_html(html) if html else ""
            except Exception as e:
                logstream.warn("resolver", f"按钮页面解析失败 {url[:80]}: {e}")
                extracted = ""
            if len(_cache) >= _CACHE_MAX:
                _cache.clear()
            _cache[url] = extracted
        if extracted:
            combined += "\n" + extracted
    return combined
