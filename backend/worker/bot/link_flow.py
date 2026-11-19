"""TG Bot 直发链接解析、分类提取与入队逻辑。"""
from ... import database as db
from ...core import logstream
from .state import USAGE_ADD

_LINK_HEADERS = (
    ("云下载", "offline"), ("离线", "offline"),
    ("转存", "share"), ("保存", "share"),
)
_LINK_PREFIXES = ("http://", "https://", "magnet:", "ed2k:", "thunder://", "ftp://")
_TRIM_CHARS = " \t\r\n\u3000:：,，"


def _usage_add() -> str:
    """/add 用法回复：先列已添加频道，再接命令用法；没有任何频道时只返回用法。"""
    rows = db.query("SELECT name, sub_only FROM channels ORDER BY id")
    if not rows:
        return USAGE_ADD
    lines = [f"📺 已添加频道（{len(rows)}）", ""]
    for r in rows:
        lines.append(f"· @{r['name']}（{'仅订阅' if r['sub_only'] else '全盘转存'}）")
    lines.append("")
    lines.append(USAGE_ADD)
    return "\n".join(lines)


def _submode_text() -> str:
    """各频道的订阅模式状态（只读展示，网页「账号管理」可调整）。"""
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
    lines.append("新增频道时必须选择订阅模式（/add 的 on|off 必填）；在网页「账号管理」可按频道调整")
    return "\n".join(lines)


def _parse_link_header(text: str) -> tuple[str, str]:
    """解析直发链接的指令头，返回 (intent, 指令头之后的正文)；未带指令头返回 ("", 原文)。"""
    s = (text or "").lstrip(_TRIM_CHARS)
    for word, intent in _LINK_HEADERS:
        if s.startswith(word):
            return intent, s[len(word):].strip(_TRIM_CHARS)
    return "", s


def _route_categories() -> list[str]:
    """「分类 → 目录映射」里的分类名（直发消息可选的分类前缀）。"""
    try:
        return [str(r["category"]) for r in db.query("SELECT category FROM routes ORDER BY category")]
    except Exception:
        return []


def _starts_link(s: str) -> bool:
    """正文是否直接以链接开头（说明没写分类）。"""
    return s.lower().startswith(_LINK_PREFIXES)


def _link_index(s: str) -> int:
    """正文中第一个链接的起始位置；没有链接返回 -1（用于截出紧贴链接的分类名）。"""
    low = s.lower()
    hits = [i for i in (low.find(p) for p in _LINK_PREFIXES) if i >= 0]
    return min(hits) if hits else -1


def _split_category(body: str) -> tuple[str, str, str]:
    """取出正文开头的分类名，返回 (category, 去掉分类名后的正文, 写错的分类名)。

    分类名必须与「分类 → 目录映射」完全一致：
    - 不写分类（正文直接是链接）→ ("", 原文, "")，由标题规则/未分类动作决定目录；
    - 写了已配置的分类名 → 返回该分类，并从正文中去掉，避免污染标题；
    - 写了不存在的分类名 → 第三项回传该词，由调用方报错并列出可选分类（不入队）。
    """
    s = (body or "").strip(_TRIM_CHARS)
    if not s:
        return "", s, ""
    if _starts_link(s):
        return "", s, ""
    cats = _route_categories()
    token = s.split()[0] if s.split() else ""
    if token in cats:
        return token, s[len(token):].strip(_TRIM_CHARS), ""
    # 中文常不带空格（如「动漫magnet:?xt=…」）：按分类名做前缀匹配，长名优先
    for cat in sorted(cats, key=len, reverse=True):
        if len(cat) >= 2 and s.startswith(cat):
            return cat, s[len(cat):].strip(_TRIM_CHARS), ""
    # 开头是文字却不是已配置分类名：视为分类名写错，截到链接前作为错误提示
    pos = _link_index(s)
    return "", s, (s[:pos] if pos > 0 else token).strip(_TRIM_CHARS)[:30]


def _link_usage(reason: str = "") -> str:
    """直发链接格式说明（缺指令头 / 链接类型不符 / 无法确定目录时统一回复）。"""
    cats = " / ".join(_route_categories())
    lines = [f"❌ {reason}", ""] if reason else []
    lines += [
        "直发链接需以指令头开头，程序据此判断进哪个队列：",
        "",
        "转存 [分类] <115 分享链接>",
        "云下载 [分类] <磁力 / 电驴 / 迅雷链接>",
        "",
        "示例（空格不影响识别，链接可紧贴指令头）：",
        "云下载 magnet:?xt=urn:btih:xxxxxxxx",
        "云下载 动漫 magnet:?xt=urn:btih:xxxxxxxx",
        "转存 电影 https://115.com/s/xxxx?password=yyyy",
        "",
        "[分类] 可选，须与「分类 → 目录映射」的分类名完全一致：" + (cats or "（尚未配置分类）"),
        "分类名写错会拒绝入队并列出现有分类；不写分类时按标题规则识别，识别不出则用「未分类动作」。",
    ]
    return "\n".join(lines)


def _bot_enqueue_links(text: str, message_id: int) -> str:
    """管理员直发链接：带「转存 / 云下载」指令头即可入队，目录读「分类 → 目录映射」。

    指令头只决定队列（分享转存 / 云下载）；指令头后可再写一个分类名（如「云下载 动漫」），
    写了就按该分类的目录存放，不写则按标题规则识别、识别不出用「未分类动作」。
    任务进入与网页完全相同的串行队列，来源标记 tgbot，在「分流记录」与 /ls 中可区分。
    """
    from ...core import classifier, extractor, pipeline

    intent, body = _parse_link_header(text)
    if not intent:
        return _link_usage("未识别到指令头（消息需以「转存」或「云下载」开头）")
    links = extractor.extract(body)
    if not links:
        return _link_usage("指令头之后没有识别到链接")
    kinds = {l.get("kind", "share") for l in links}
    if len(kinds) > 1:
        return _link_usage("同一条消息混有分享链接和磁力/电驴链接，请分开两条发送")
    kind = kinds.pop()
    if kind != intent:
        is_offline = kind == "offline"
        return _link_usage(
            f"这条消息里是{'磁力/电驴（云下载）' if is_offline else '115 分享（转存）'}链接，"
            f"请用「{'云下载' if is_offline else '转存'}」开头")
    category, body, unknown = _split_category(body)
    if unknown:
        cats = " / ".join(_route_categories()) or "（尚未配置分类）"
        return ("⚠️ 分类「" + unknown + "」不存在，本次未入队。\n\n"
                "现有分类（须与「分类 → 目录映射」完全一致）：\n" + cats + "\n\n"
                "请修正分类名后重发，例如：\n"
                "云下载 动漫 <磁力链接>\n"
                "转存 电影 <115 分享链接>\n\n"
                "也可以不写分类：云下载 <链接>（按标题规则识别，识别不出用「未分类动作」）")
    if category and not classifier.resolve_target(category, kind=kind):
        label = "云下载" if kind == "offline" else "转存"
        return (f"⚠️ 分类「{category}」没有可用的{label}目录（未配置或设为跳过）。\n"
                "请到「分类 → 目录映射」为该分类设置目录，或不写分类后重发。")
    mid = int(message_id or 0)
    try:
        res = pipeline.process_message(channel="tgbot", message_id=mid,
                                       text=body or text, force_category=category)
    except Exception as e:
        logstream.error("transfer", f"TG Bot 链接入队失败: {logstream.exc(e)}")
        return f"入队失败: {str(e)[:120]}"
    if not res.get("links"):
        return "未识别到可转存的链接（支持 115 分享 / 磁力 / 电驴 / 迅雷）"
    label = "云下载" if kind == "offline" else "转存"
    parts = []
    if res.get("queued"):
        parts.append(f"✅ 已入队 {res['queued']} 个{label}任务（串行队列自动执行）")
    if res.get("duplicates"):
        parts.append(f"🔁 重复跳过 {res['duplicates']} 个")
    if res.get("blocked"):
        parts.append("⛔ 命中黑名单拦截")
    if res.get("skipped") and not res.get("queued") and not res.get("duplicates"):
        # 取出管线写入的跳过原因，直接把「该去哪里改目录」告诉用户
        row = db.query(
            "SELECT error FROM transfers WHERE channel='tgbot' AND message_id=?"
            " AND status='skipped' ORDER BY id DESC LIMIT 1", (mid,), one=True)
        reason = str((row["error"] if row else "") or "未分类，且没有可用的目标目录")
        parts.append(f"⏭ 未入队：{reason}")
        if "未分类" in reason:
            parts.append("可在「分类 → 目录映射」把「未分类动作」设为目录（如 /待整理）后重发。")
    elif res.get("skipped"):
        parts.append(f"⏭ 另有 {res['skipped']} 个未入队（原因见「分流记录」记录）")
    return "\n".join(parts) or "未入队（原因见日志）"
