"""资源画质识别与比较（订阅质量策略 / 洗版用）。

设计原则：
- 只识别标题里**明确写出**的分辨率/来源/编码；识别不出时 score=0（未知），
  调用方必须按「放行」处理，不能因为标题没写 1080p 就拦截正常资源；
- 分数只用于同一订阅、同一集（S01E05）之间的择优比较，不参与分类与路由；
- 无副作用、无 IO，便于单测与在管道/工作器两侧复用。
"""
import re

# 分辨率档位：数值越大画质越高；0 = 未识别
RES_RANK = {"480p": 1, "720p": 2, "1080p": 3, "2160p": 4}
RES_LABEL = {"480p": "480p", "720p": "720p", "1080p": "1080p", "2160p": "2160p"}

_RES_RULES = (
    (re.compile(r"(?<![0-9a-z])(?:2160p|4k|uhd)(?![0-9a-z])", re.I), "2160p"),
    (re.compile(r"(?<![0-9a-z])1080[pi](?![0-9a-z])", re.I), "1080p"),
    (re.compile(r"(?<![0-9a-z])720p(?![0-9a-z])", re.I), "720p"),
    (re.compile(r"(?<![0-9a-z])(?:480p|540p|360p)(?![0-9a-z])", re.I), "480p"),
)

_SOURCE_RULES = (
    (re.compile(r"(remux|blu-?ray|原盘)", re.I), "REMUX", 3),
    (re.compile(r"(web-?dl|webrip|web)", re.I), "WEB", 2),
    (re.compile(r"(hdtv)", re.I), "HDTV", 1),
)

_CODEC_RE = re.compile(r"(h\.?265|x265|hevc)", re.I)


def parse(text: str) -> dict:
    """从标题/正文提取画质信息。返回 {score, resolution, source, codec, tag}。"""
    s = str(text or "")
    resolution = ""
    res_rank = 0
    for pattern, label in _RES_RULES:
        if pattern.search(s):
            resolution, res_rank = label, RES_RANK[label]
            break
    source, source_rank = "", 0
    for pattern, label, rank in _SOURCE_RULES:
        if pattern.search(s):
            source, source_rank = label, rank
            break
    codec = "H265" if _CODEC_RE.search(s) else ""
    # 未识别出分辨率时按未知处理（score=0）：来源/编码单独出现不足以判断高低
    score = 0 if not res_rank else res_rank * 100 + source_rank * 10 + (1 if codec else 0)
    tag = " ".join(x for x in (resolution, source, codec) if x)
    return {"score": score, "resolution": resolution, "source": source,
            "codec": codec, "tag": tag}


def resolve_rank(label: str) -> int:
    """最低画质设置值 → 档位；未知/空/「不限」返回 0（不限制）。"""
    return RES_RANK.get(str(label or "").strip().lower(), 0)


def min_resolution_allowed(parsed: dict, min_label: str) -> bool:
    """是否达到最低画质要求。

    - 未设置最低画质 → 允许；
    - 资源画质未知（标题没写）→ 允许（宁可放过，不能误拦）；
    - 明确低于门槛 → 拒绝。
    """
    need = resolve_rank(min_label)
    if need <= 0:
        return True
    got = RES_RANK.get(str(parsed.get("resolution") or ""), 0)
    if got <= 0:
        return True
    return got >= need
