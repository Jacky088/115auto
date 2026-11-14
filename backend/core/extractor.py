"""资源链接提取：115 分享链接（115.com/115cdn/anxia/115://）+ ED2K/磁力链云下载链接。"""
import hashlib
import re

# 链接 + 可选的 ?password=xxx。左边界 (?<![0-9A-Za-z])：防止 "xx115.com/s/xxx"、
# "9115.com/s/xxx"（无关域名）从中间截取误判为 115 分享；子域名（www./m. 等）不受影响
URL_RE = re.compile(
    r"(?<![0-9A-Za-z])(?:https?://)?(?:www\.)?(115\.com|115cdn\.com|anxia\.com)/s/(?P<code>[0-9A-Za-z]+)"
    r"(?:\?password=(?P<pw>[0-9A-Za-z]+))?",
    re.IGNORECASE,
)
# 115://code?password=xxx 秒传式写法。password 可选（公开分享无提取码）；左边界防
# "xx115://…" 之类的子串误提取。无提取码时与网页链接同口径以空 receive_code 入队
PROTO_RE = re.compile(r"(?<![0-9A-Za-z])115://(?P<code>[0-9A-Za-z]+)(?:\?password=(?P<pw>[0-9A-Za-z]+))?", re.IGNORECASE)
# 独立提取码写法：提取码：xxxx / 访问码 xxxx / 密码: xxxx / 提取码 xxxx
# 4~8 位 + 右边界：固定 4 位会把 5 位以上的提取码截断成错误前 4 位（转存必败）；
# 右边界防止从 9 位以上的长串截出前 8 位——宁可不提取也不提取错误的码
PW_RE = re.compile(r"(?:提取码|访问码|密码|访问密码|取件码)\s*[:：]?\s*([0-9A-Za-z]{4,8})(?![0-9A-Za-z])", re.IGNORECASE)
# ED2K 电驴 / 磁力链（115 云下载支持）
ED2K_RE = re.compile(r"ed2k://\|file\|[^|\s]+\|\d+\|[0-9A-Fa-f]{32}\|/?", re.IGNORECASE)
# xt 参数前的其他参数（如 magnet:?dn=xx&xt=…）同样识别
MAGNET_RE = re.compile(r"magnet:\?(?:[^\s]*&)?xt=urn:btih:[0-9A-Za-z]{32,40}[^\s]*", re.IGNORECASE)
# 迅雷链接（thunder:// = base64("AA" + url + "ZZ")），解码后转为 http/ftp 云下载
THUNDER_RE = re.compile(r"thunder://([A-Za-z0-9+/=]{20,})", re.IGNORECASE)


def _decode_thunder(token: str) -> str:
    """解码迅雷链接为 http/ftp 地址，失败返回空串。"""
    import base64
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4)).decode("utf-8", "ignore")
        if raw.startswith("AA") and raw.endswith("ZZ"):
            url = raw[2:-2]
            return url if url.lower().startswith(("http://", "https://", "ftp://")) else ""
    except Exception:
        pass
    return ""


def _offline(url: str) -> dict:
    code = hashlib.sha1(url.encode()).hexdigest()[:16]
    return {"kind": "offline", "share_code": code, "receive_code": "", "share_url": url}


def extract(text: str) -> list[dict]:
    """返回 [{kind, share_code, receive_code, share_url}]，按出现顺序去重。"""
    found: list[dict] = []
    seen: set[str] = set()

    # 云下载链接（ED2K / 磁力 / 迅雷）
    for m in ED2K_RE.finditer(text):
        url = m.group(0).rstrip("/")
        if url not in seen:
            seen.add(url)
            found.append(_offline(url))
    for m in MAGNET_RE.finditer(text):
        url = m.group(0)
        if url not in seen:
            seen.add(url)
            found.append(_offline(url))
    for m in THUNDER_RE.finditer(text):
        url = _decode_thunder(m.group(1))
        if url and url not in seen:
            seen.add(url)
            found.append(_offline(url))

    for m in PROTO_RE.finditer(text):
        code = m.group("code").lower()
        if code not in seen:
            seen.add(code)
            found.append({
                "kind": "share",
                "share_code": m.group("code"),
                "receive_code": m.group("pw") or "",
                "share_url": f"https://115.com/s/{m.group('code')}?password={m.group('pw') or ''}",
            })

    # 带域名的链接（含或不含 password）
    spans = [(m, m.group("code")) for m in URL_RE.finditer(text)]
    proto_matches = list(PROTO_RE.finditer(text))
    consumed_spans = {m.start() for m in proto_matches}
    # 全部分享链接的起点：用于判定「就近提取码」是否其实属于后一个链接
    link_starts = sorted([m.start() for m, _ in spans] + [m.start() for m in proto_matches])
    for m, code in spans:
        if m.start() in consumed_spans:
            continue
        if code.lower() in seen:
            continue
        seen.add(code.lower())
        pw = m.group("pw") or ""
        if not pw:
            # 优先在链接之后的 60 字符内找独立提取码写法；
            # 再回看链接之前的 60 字符（「提取码：xxxx https://…」前置写法同样常见）
            tail = text[m.end(): m.end() + 60]
            pw_match = PW_RE.search(tail)
            if pw_match:
                # 命中位置与本链接之间若还隔着另一个分享链接，说明该提取码属于后者
                seg_end_abs = m.end() + pw_match.start()
                if any(s > m.end() and s < seg_end_abs for s in link_starts):
                    pw_match = None
            if not pw_match:
                head_matches = list(PW_RE.finditer(text[max(0, m.start() - 60): m.start()]))
                if head_matches:
                    pw_match = head_matches[-1]  # 取离链接最近的一个
            if pw_match:
                pw = pw_match.group(1)
        domain = m.group(1).lower()
        url = f"https://{domain}/s/{m.group('code')}"
        if pw:
            url += f"?password={pw}"
        found.append({"kind": "share", "share_code": m.group("code"), "receive_code": pw, "share_url": url})

    return found
