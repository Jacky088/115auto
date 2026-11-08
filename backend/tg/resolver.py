"""内联按钮链接解析：TG 消息正文中没有资源链接、链接藏在"查看资源"等内联按钮里时，
抓取按钮指向的页面（telegra.ph 文章 / 跳转页等），从中提取真实的 115 分享 / ED2K / 磁力链接。

- 文本里已有资源链接时不再解析按钮（省请求）
- t.me 站内跳转按钮直接忽略
- 解析结果按按钮 URL 缓存（Telegraph 页面内容不变）
"""
import asyncio
import ipaddress
import re
from urllib.parse import urljoin, urlparse

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


_FAKEIP_NET = ipaddress.ip_network("198.18.0.0/15")  # Clash/mihomo Fake-IP DNS 的默认网段


def _ip_dangerous(ip, literal: bool) -> bool:
    """判断单个 IP 是否危险（内网/环回/链路本地/保留段）。literal=True 表示 URL 里直接写的 IP。"""
    if ip in _FAKEIP_NET:
        # Fake-IP 网段：本机代理 DNS 会把所有域名解析到这里，连接实际由代理接管，
        # 不代表内网可达；但出现在字面量 URL 里毫无正当用途 → 拒绝
        return literal
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


async def _url_safe(url: str) -> bool:
    """SSRF 防护：仅允许 http(s)；拒绝内网/环回/链路本地等危险地址。

    频道消息里的按钮 URL 是不可信输入：无防护时后端会被诱导请求内网服务
    （如 http://127.0.0.1:*、云主机元数据 169.254.169.254）。
    - 字面量内网 IP → 直接拒绝（真实 SSRF 向量）
    - 域名：任一解析结果为环回/链路本地/组播 → 拒绝；其余地址全部为内网/保留段 → 拒绝；
      命中 Fake-IP 网段视为代理环境放行（否则该环境下所有域名解析都会被误杀）
    - 解析失败 → 视为不安全，不发起请求
    """
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        try:
            return not _ip_dangerous(ipaddress.ip_address(p.hostname), literal=True)
        except ValueError:
            pass  # 域名而非 IP 字面量 → 走解析检查
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(p.hostname, None)
        ips = [ipaddress.ip_address(info[4][0]) for info in infos]
    except Exception:
        return False
    if not ips:
        return False
    if any(ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
           for ip in ips):
        return False
    non_fakeip = [ip for ip in ips if ip not in _FAKEIP_NET]
    if not non_fakeip:
        return True  # 全部是 Fake-IP：代理接管解析与连接
    return any(not (ip.is_private or ip.is_reserved) for ip in non_fakeip)


async def _fetch_page(client: httpx.AsyncClient, url: str) -> str:
    """抓取中间页：手动跟随重定向（最多 3 跳），每一跳都重跑 SSRF 校验——
    频道按钮指向的页面是第三方可控输入，follow_redirects=True 会被 302 绕过
    内网防护（跳向 127.0.0.1/云元数据等）。"""
    current = url
    for _ in range(3):
        if not await _url_safe(current):
            logstream.warn("resolver", f"重定向目标指向内网或非法地址，已终止: {current.split('?')[0][:80]}")
            return ""
        resp = await client.get(current, timeout=15, follow_redirects=False,
                                headers={"User-Agent": _UA})
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location")
            if not loc:
                return ""
            current = urljoin(current, loc)
            continue
        if resp.status_code != 200:
            return ""
        return resp.text[:_MAX_BYTES]
    return ""


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
        # 中间页：先做 SSRF 校验，再查缓存 / 抓取解析
        if not await _url_safe(url):
            logstream.warn("resolver", f"按钮指向内网或非法地址，已跳过解析: {url.split('?')[0][:80]}")
            continue
        resolved += 1
        if url in _cache:
            extracted = _cache[url]
        else:
            try:
                async with httpx.AsyncClient(proxy=_tg_proxy()) as client:
                    html = await _fetch_page(client, url)
                extracted = _extract_from_html(html) if html else ""
            except Exception as e:
                logstream.warn("resolver", f"按钮页面解析失败 {url.split('?')[0][:80]}: {e}")
                extracted = ""
            if len(_cache) >= _CACHE_MAX:
                _cache.clear()
            _cache[url] = extracted
        if extracted:
            combined += "\n" + extracted
    return combined
