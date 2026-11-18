"""manage 子路由公共工具：代理/IP/Cookie、分页夹紧、LIKE 转义、路径动作校验。"""
from ipaddress import ip_address, ip_network

from fastapi import HTTPException, Request, Response

from ... import auth, config, database as db
from ..deps import AUTH_COOKIE

# 海报专用 HttpOnly Cookie：路径收窄到海报接口，避免随全部 API 请求发送；
# 浏览器 <img> 无法带 Authorization 头，靠同源 Cookie 鉴权（不再把长期 JWT 放 URL）。
POSTER_COOKIE = "sa_poster"

# 设置接口的密钥掩码：GET /api/settings 对凭据类键不再回传明文，
# 改回哨兵值；PUT 收到哨兵值表示「保持已存值不变」，防止表单整体保存把密钥冲掉
SECRET_MASK = "__SAVED__"


def _is_trusted_proxy(host: str) -> bool:
    """判断直连来源是否在可信代理列表（支持 CIDR）。"""
    try:
        ip = ip_address(host)
    except ValueError:
        return False
    for item in config.TRUSTED_PROXIES:
        try:
            if "/" in item:
                if ip in ip_network(item, strict=False):
                    return True
            elif ip == ip_address(item):
                return True
        except ValueError:
            continue
    return False


def _client_ip(request: Request) -> str:
    """来源 IP：仅当直连来源是可信代理时才解析 X-Forwarded-For，
    并从右往左取第一个非可信地址；否则用 socket 地址，防止伪造 XFF 绕过限速。"""
    remote = request.client.host if request.client else ""
    fwd = request.headers.get("x-forwarded-for", "")
    if not fwd or not _is_trusted_proxy(remote):
        return remote
    parts = [x.strip() for x in fwd.split(",") if x.strip()]
    for item in reversed(parts):
        if not _is_trusted_proxy(item):
            return item
    return parts[0] if parts else remote


def _is_https(request: Request) -> bool:
    """请求是否经 HTTPS 到达用户浏览器。

    反代/CDN 场景下容器内看到的是 http（request.url.scheme 恒为 http），
    必须参考 X-Forwarded-Proto；但该头可被客户端伪造，因此仅当直连来源
    命中 TRUSTED_PROXIES 白名单时才采信（与 _client_ip 的 XFF 防伪同一套信任模型）。
    公网反代部署务必设置 TRUSTED_PROXIES，否则 Cookie 不带 Secure 标记。"""
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if proto != "https":
        return False
    remote = request.client.host if request.client else ""
    return _is_trusted_proxy(remote)


def _set_poster_cookie(response: Response, request: Request, token: str) -> None:
    """下发海报鉴权 Cookie（HttpOnly + SameSite=Lax，路径限定于 TMDB 与本地媒体海报接口）。"""
    secure_flag = _is_https(request)
    for p in ("/api/tmdb/poster", "/api/media/poster"):
        response.set_cookie(
            POSTER_COOKIE, token, max_age=auth.TOKEN_TTL,
            httponly=True, samesite="lax", path=p,
            secure=secure_flag,
        )


def _set_auth_cookie(response: Response, request: Request, token: str) -> None:
    """下发登录态 Cookie（HttpOnly + SameSite=Strict，全站路径）。

    token 不再落 localStorage：脚本读不到，登出/改密即时失效。"""
    secure_flag = _is_https(request)
    response.set_cookie(
        AUTH_COOKIE, token, max_age=auth.TOKEN_TTL,
        httponly=True, samesite="strict", path="/",
        secure=secure_flag,
    )


def _like_escape(s: str) -> str:
    """LIKE 通配符转义（%/_/\），防止用户输入触发全表扫描。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------- auth ----------
def _web_username() -> str:
    """当前登录用户名：优先 DB（网页可改），回退环境变量初始值。"""
    return str(db.get_setting("web_username") or config.WEB_USERNAME or "admin")



def _clamp_page(page: int, size: int, max_size: int) -> tuple[int, int]:
    """分页参数收紧：size 设上限防一次性拖全表（DoS/内存），page 至少为 1。"""
    return max(page, 1), max(1, min(size, max_size))



def _clean_dir_action(v) -> str:
    """未分类动作：skip，或 / 开头的 115 网盘子目录（拒绝根目录、..、空格）。"""
    s = str(v or "").strip()
    if not s or s == "skip":
        return "skip"
    if not s.startswith("/") or not s.strip("/"):
        raise HTTPException(400, "未分类动作需为 skip 或 / 开头的网盘目录（如 /未整理）")
    if any(p == ".." for p in s.split("/")) or any(ch.isspace() for ch in s):
        raise HTTPException(400, "未分类目录不能包含 .. 或空格（如 /未整理）")
    if len(s) > 200:
        raise HTTPException(400, "未分类目录过长，请控制在 200 个字符以内")
    return s.rstrip("/")
