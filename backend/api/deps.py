"""FastAPI 依赖：JWT 鉴权 + 滑动续期。"""
from fastapi import Header, HTTPException, Response

from .. import auth


def require_user(authorization: str = Header(default=""), response: Response = None) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    token = authorization[7:]
    username = auth.check_token(token)
    if not username:
        raise HTTPException(401, "登录已过期，请重新登录")
    # 滑动续期：剩余有效期不足一半时经响应头签发新 token，活跃用户无需定期重新登录
    if response is not None and auth.token_renewable(token):
        response.headers["X-Renewed-Token"] = auth.make_token(username)
    return username
