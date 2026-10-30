"""FastAPI 依赖：JWT 鉴权。"""
from fastapi import Header, HTTPException

from .. import auth


def require_user(authorization: str = Header(default="")) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    username = auth.check_token(authorization[7:])
    if not username:
        raise HTTPException(401, "登录已过期，请重新登录")
    return username
