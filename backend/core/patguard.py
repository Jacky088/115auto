"""用户自定义正则的安全校验：保存时拦截灾难性回溯模式，防一条坏规则卡死转存管道。

匹配对象是不可信的频道消息文本（正则引擎单线程执行，回溯爆炸 = pipeline 无声停摆），
因此除语法校验外，还限制长度并拒绝典型嵌套量词/歧义分支结构。

启发式覆盖三类经典回溯爆炸形态：
1. 量词紧跟「本身已带量词的组」： (a+)+ / (.*)* / (a{1,2})+
2. 「内含量词或可空分支的组」再套量词： (a?)+ / (|a)+ / (a|a?)+
3. 量化分组的分支共享重叠前缀： (a|aa)+ —— 指数回溯的另一种典型来源
固定长度的有界量词（如 (?:S\\d{2})+ 中的 \\d{2}）无歧义膨胀空间，予以放行。
CPython re 无匹配超时，宁可保守拦截（可改写为 (?:a{1,3}) 这类有界形式）。
"""
import re

MAX_PATTERN_LEN = 200

# 形态 1：量词紧跟在「本身已带可变量词的组」之后（字面量括号写作 \) 带反斜杠，不会被误伤）。
# 组尾是 +/*（可变长）必危险；组尾是 {n,m} 可变区间同样危险；精确 {n}（如 (?:S\d{2})+）无歧义膨胀，交由平衡扫描判定
_NESTED_QUANT = re.compile(r"[+*]\)[+*{]|\{\d+,\d*\}\)[+*{]")


def _group_is_risky(body: str) -> bool:
    """组体是否具备灾难性回溯的必要条件（量词可变长 / 分支可空 / 分支前缀重叠）。"""
    # 先把非捕获/命名组前缀与内联修饰还原成普通括号，避免 "(?:" 中的 ? 被误当量词
    body = re.sub(r"\(\?[a-zA-Z]+\)", "", body)      # (?i) 等内联修饰
    body = re.sub(r"\(\?P<[^>]*>", "(", body)        # (?P<name>
    body = body.replace("(?:", "(")                  # (?:...
    if re.search(r"[+*?]|\{\d*,", body):
        return True
    if "|" in body:
        parts = body.split("|")
        if any(p == "" for p in parts):
            return True
        firsts = [p[:1] for p in parts if p]
        if len(firsts) > 1 and len(set(firsts)) < len(firsts):
            return True
    return False


def _find_risky_quantified_group(pattern: str) -> bool:
    """平衡扫描顶层分组：检查「分组后紧跟量词」且分组体存在歧义膨胀空间的形态。"""
    cleaned = re.sub(r"\\[()]", "", pattern)          # 剥离字面量括号
    cleaned = re.sub(r"\[(?:[^\]\\]|\\.)*\]", " ", cleaned)  # 剥离字符类
    n = len(cleaned)
    i = 0
    while i < n:
        if cleaned[i] != "(":
            i += 1
            continue
        depth, j, nested = 1, i + 1, False
        while j < n and depth:
            if cleaned[j] == "(":
                depth += 1
                nested = True
            elif cleaned[j] == ")":
                depth -= 1
            j += 1
        if depth:  # 括号不平衡：re.compile 已通过，理论上不会走到这里
            break
        body = cleaned[i + 1:j - 1]
        # 剥离 (?i) 内联修饰 / (?:...) / (?P<name>...) 前缀后再判定
        body = re.sub(r"^\?(?:[:P<]|<[^>]*>)?", "", body) if body.startswith("?") else body
        body = re.sub(r"^P<[^>]*>", "", body)
        nxt = cleaned[j:j + 1]
        # 含嵌套组的组体也直接判定（如 ((a+))+ 的危险来自内层量词），宁可保守
        if nxt in ("+", "*", "{") and _group_is_risky(body):
            return True
        i = j
    return False


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
    if _find_risky_quantified_group(pattern):
        return ("正则包含「可回溯膨胀的分组+量词」组合（如 (a|aa)+ / (a?)+ ），"
                "长文本下可能卡死分类引擎；请改写为有界形式（如 (?:a{1,3})）")
    return None
