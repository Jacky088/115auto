"""用户自定义正则的安全校验：保存时拦截灾难性回溯模式，防一条坏规则卡死转存管道。

匹配对象是不可信的频道消息文本（正则引擎单线程执行，回溯爆炸 = pipeline 无声停摆），
因此除语法校验外，还限制长度并拒绝典型嵌套量词结构（如 (a+)+ / (.*)* ）。
"""
import re

MAX_PATTERN_LEN = 200

# 嵌套量词启发式：量词紧跟在「本身已带量词的组」之后。
# 匹配形如 `+)+` `*}*` `{2,}){` 的序列；字面量括号写作 \) 带反斜杠，不会被误伤。
_NESTED_QUANT = re.compile(r"[+*}]\)[+*{]")


def validate_pattern(pattern: str) -> str | None:
    """校验用户提交的正则，返回错误说明；合法返回 None。"""
    pattern = pattern or ""
    if not pattern.strip():
        return "正则不能为空"
    if len(pattern) > MAX_PATTERN_LEN:
        return f"正则过长（最多 {MAX_PATTERN_LEN} 字符，当前 {len(pattern)}）"
    try:
        re.compile(pattern)
    except re.error as e:
        return f"正则无效: {e}"
    if _NESTED_QUANT.search(pattern):
        return "正则包含嵌套量词（如 (a+)+），长文本下可能卡死分类引擎，请改写"
    return None
