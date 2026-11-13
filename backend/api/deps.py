"""FastAPI 依赖：JWT 鉴权（Bearer 头 / HttpOnly Cookie 双通道）+ 滑动续期。"""
from fastapi import Header, HTTPException, Request, Response

from .. import auth

# 登录态 HttpOnly Cookie：token 不再存 localStorage（脚本不可读），
# 与 sa_poster 的机制对齐；SameSite=Strict 同源使用，天然免疫跨站 CSRF。
AUTH_COOKIE = "sa_auth"


def _is_https(request: Request | None) -> bool:
    """请求是否经 HTTPS 到达用户浏览器（反代场景参考可信来源的 X-Forwarded-Proto）。"""
    if request is None:
        return False
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if proto != "https":
        return False
    from .manage import _is_trusted_proxy  # 局部导入避免循环依赖
    remote = request.client.host if request.client else ""
    return _is_trusted_proxy(remote)


def _candidate_tokens(request: Request | None, authorization: str) -> list[str]:
    """鉴权候选：优先 Bearer 头（兼容既有集成/测试），其次 HttpOnly Cookie。"""
    out = []
    if authorization.startswith("Bearer "):
        out.append(authorization[7:])
    if request is not None:
        cookie_token = str(request.cookies.get(AUTH_COOKIE) or "")
        if cookie_token:
            out.append(cookie_token)
    return out


def require_user(request: Request = None, response: Response = None,
                 authorization: str = Header(default="")) -> str:
    username = ""
    used_token = ""
    for tok in _candidate_tokens(request, authorization):
        username = auth.check_token(tok)
        if username:
            used_token = tok
            break
    if not username:
        raise HTTPException(401, "登录已过期，请重新登录")
    # 滑动续期：剩余有效期不足一半时签发新 token，经响应头 + HttpOnly Cookie 同步下发
    #（Cookie 由浏览器自动携带，前端无需保存；响应头仅为兼容外部集成保留）
    if response is not None and auth.token_renewable(used_token):
        renewed = auth.make_token(username)
        response.headers["X-Renewed-Token"] = renewed
        try:
            response.set_cookie(
                AUTH_COOKIE, renewed, max_age=auth.TOKEN_TTL,
                httponly=True, samesite="strict", path="/", secure=_is_https(request),
            )
        except Exception:
            pass
    return username
