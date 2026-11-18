"""认证路由：登录 / 登出 / 当前会话 / 修改账号密码。"""
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from ... import auth, database as db
from ...core import logstream
from ..deps import AUTH_COOKIE, require_user
from ._common import (
    POSTER_COOKIE,
    _client_ip,
    _is_https,
    _set_auth_cookie,
    _set_poster_cookie,
    _web_username,
)

router = APIRouter()


@router.post("/auth/login")
def login(body: dict, request: Request, response: Response):
    ip = _client_ip(request)
    username = str(body.get("username", ""))[:64]
    password = str(body.get("password", ""))
    if len(password) > 128:
        auth.record_login_fail(ip)
        raise HTTPException(400, "密码过长", headers={"Retry-After": "60"})
    if auth.login_throttled(ip):
        logstream.warn("system", f"登录请求被限速（来源 {ip}）")
        raise HTTPException(429, "失败次数过多，请 1 分钟后再试", headers={"Retry-After": "60"})
    stored = db.get_setting("web_password_hash", "")
    username_ok = username == _web_username()
    if not username_ok:
        # 用户名不匹配时也执行一次等价 PBKDF2 验证拉平响应耗时（防用户名枚举时序侧信道）
        auth.verify_password(password, auth.hash_password("timing-equalizer"))
    if not username_ok or not auth.verify_password(password, stored):
        auth.record_login_fail(ip)
        logstream.warn("system", f"网页登录失败（来源 {ip}）")
        raise HTTPException(401, "用户名或密码错误")
    # 用户名正确但密码哈希仍是旧格式/低迭代：借这次成功的验证透明升级到当前强度
    if auth.password_needs_rehash(stored):
        db.set_setting("web_password_hash", auth.hash_password(password))
    logstream.info("system", "网页登录成功")
    token = auth.make_token(username)
    _set_poster_cookie(response, request, token)
    _set_auth_cookie(response, request, token)
    return {"token": token}


@router.post("/auth/logout")
def logout(request: Request, response: Response, _=Depends(require_user)):
    """退出登录：清除 Cookie 并吊销全部已签发 token（版本号 +1）。

    单管理员系统下「登出即全端下线」：被盗的 token 即使在 7 天有效期内也无法继续使用。
    需登录态：吊销全端会话是敏感操作，不校验会被未认证请求反复调用（登出 DoS）。
    """
    auth.revoke_all_tokens()
    response.delete_cookie(AUTH_COOKIE, path="/", secure=_is_https(request))
    for p in ("/api/tmdb/poster", "/api/media/poster"):
        response.delete_cookie(POSTER_COOKIE, path=p)
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request, response: Response, authorization: str = Header(default="")):
    require_user(request, response, authorization)
    if authorization.startswith("Bearer "):
        # 旧会话迁移：token 经 Bearer 头到达时补发 HttpOnly Cookie，后续请求不再依赖前端保存
        token = authorization[7:]
        _set_poster_cookie(response, request, token)
        _set_auth_cookie(response, request, token)
    return {"username": _web_username()}


# 显式列出允许字符（不用 \w：Python 的 \w 会放行西里尔文/韩文等所有 Unicode 文字，与前端规则不一致）
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_@\.\-\u4e00-\u9fa5]{2,32}$")


@router.post("/auth/password")
def change_pwd(body: dict, request: Request, response: Response, _=Depends(require_user)):
    """修改用户名和/或密码（都需验证旧密码）。body: {old, new?, username?}"""
    ip = _client_ip(request)
    old = body.get("old", "")
    # 限速检查必须在 PBKDF2 验证之前：否则第 5 次失败后每个请求仍会完整跑一次哈希，
    # 持有效会话的攻击者可高并发调用本端点打满单进程 CPU
    if auth.login_throttled(ip):
        raise HTTPException(429, "失败次数过多，请 1 分钟后再试", headers={"Retry-After": "60"})
    stored = db.get_setting("web_password_hash", "")
    if not auth.verify_password(old, stored):
        auth.record_login_fail(ip)  # 旧密码连续错误同样触发限速，防止已登录会话被暴力猜密码
        raise HTTPException(400, "旧密码错误")
    username = str(body.get("username") or "").strip()
    new = str(body.get("new") or "")
    if not username and not new:
        raise HTTPException(400, "新用户名与新密码至少填写一项")
    changed = []
    if username and username != _web_username():
        if not _USERNAME_RE.fullmatch(username):
            raise HTTPException(400, "用户名需为 2-32 位，仅限中英文、数字、@ . _ -")
        db.set_setting("web_username", username)
        changed.append(f"用户名 → {username}")
        try:  # 改用户名同样清掉随机初始密码文件（已完成使命）
            auth.INITIAL_PASSWORD_FILE.unlink(missing_ok=True)
        except OSError:
            pass
    if new:
        if len(new) < 8:
            raise HTTPException(400, "新密码至少 8 位")
        if len(new) > 128:
            raise HTTPException(400, "新密码最长 128 位")
        auth.change_password(new)
        changed.append("密码")
    logstream.info("system", "管理员账号已在网页修改（" + "、".join(changed) + "）")
    # 轮换 JWT 签名密钥：所有旧 token（含可能被窃取的）立即失效，本会话换取新 token 无感续期
    auth.rotate_secret()
    new_token = auth.make_token(_web_username())
    _set_poster_cookie(response, request, new_token)
    _set_auth_cookie(response, request, new_token)
    return {"ok": True, "username": _web_username(), "token": new_token}
