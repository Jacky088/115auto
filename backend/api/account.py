"""账号路由：115 扫码登录 / Cookie 导入 / 状态 / 容量，TG Userbot 登录流程。"""
import threading
import time

from fastapi import APIRouter, Depends, HTTPException

from .. import database as db
from ..pan115 import client as pan
from ..tg import userbot
from .deps import require_user

router = APIRouter(prefix="/api")

# 115 登录类接口的简易限速（单管理员场景够用）：这些接口直打 115，被滥用会加重 IP 风控
_rl_lock = threading.Lock()
_rl_records: dict[str, list[float]] = {}
_RL_RULES = {  # key -> (窗口秒, 最大次数)
    "qrcode": (60.0, 6),       # 生成二维码：每分钟最多 6 次
    "cookie_import": (300.0, 5),  # 导入 Cookie：每 5 分钟最多 5 次（失败重试会触发校验）
    "userbot_login": (300.0, 5),  # TG 发验证码：每 5 分钟最多 5 次（TG 侧也有 FloodWait）
}


def _rate_limited(key: str) -> bool:
    """滑动窗口限速：超限返回 True。"""
    window, limit = _RL_RULES[key]
    now = time.time()
    with _rl_lock:
        recs = [t for t in _rl_records.get(key, []) if now - t < window]
        if len(recs) >= limit:
            _rl_records[key] = recs
            return True
        recs.append(now)
        _rl_records[key] = recs
        return False


def _reject(key: str, hint: str):
    window, limit = _RL_RULES[key]
    raise HTTPException(429, f"操作过于频繁（{limit} 次/{int(window // 60) if window >= 60 else int(window)} 分钟内），请稍后再试。{hint}")


# ---------- 115 ----------
@router.get("/account/115/status")
def status_115(_=Depends(require_user)):
    st = pan.check_login()
    data = {"login": st["ok"], "name": st.get("name", ""), "err": st.get("err", ""),
            "cookie_ok": db.get_setting("cookie_ok", None)}
    if st["ok"]:
        try:
            data["space"] = pan.space_info()
        except Exception as e:
            data["space_err"] = str(e)[:200]
    return data


@router.post("/account/115/qrcode")
def qrcode_create(_=Depends(require_user)):
    if _rate_limited("qrcode"):
        _reject("qrcode", "频繁刷新二维码会触发 115 风控")
    try:
        return pan.qrcode_create()
    except Exception as e:
        raise HTTPException(500, f"生成二维码失败: {e}")


@router.post("/account/115/qrcode/poll")
def qrcode_poll(body: dict, _=Depends(require_user)):
    token = body.get("token")
    if not isinstance(token, dict) or not token.get("uid"):
        raise HTTPException(400, "二维码已失效，请重新生成")
    try:
        result = pan.qrcode_poll(token)
        if result["status"] == 2:  # 已确认 → 取 cookie
            try:
                return {"status": 2, **pan.qrcode_finish(str(token["uid"]),
                                                         str(body.get("app") or "alipaymini"))}
            except Exception as e:
                # cookie 已被其他请求先行取得（如双端同时登录）也视为成功
                from .. import config
                if config.COOKIE_FILE.exists():
                    return {"status": 2, "ok": True}
                # 显式把失败原因带回前端展示，而不是抛 500 被前端静默吞掉
                return {"status": 2, "ok": False, "error": str(e)[:300]}
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"轮询失败: {e}")


@router.post("/account/115/cookie")
def import_115_cookie(body: dict, _=Depends(require_user)):
    """手动导入 Cookie（海外 VPS 扫码被风控时的替代登录方式）。body: {cookie}"""
    if _rate_limited("cookie_import"):
        _reject("cookie_import", "每次导入都会向 115 发起校验请求")
    cookie = str(body.get("cookie") or "")
    if not cookie.strip():
        raise HTTPException(400, "请粘贴 Cookie 字符串")
    if len(cookie) > 4000:
        raise HTTPException(400, "Cookie 内容过长，请只粘贴 UID/CID/SEID 部分")
    try:
        return pan.import_cookie(cookie)
    except Exception as e:
        raise HTTPException(400, str(e)[:400])


# ---------- TG userbot ----------
@router.get("/tg/userbot/status")
async def tg_status(_=Depends(require_user)):
    return await userbot.status()


@router.post("/tg/userbot/test")
async def tg_userbot_test(_=Depends(require_user)):
    """一键自检：连接 + 授权 + Userbot 频道可见性，帮助确认配置正确。"""
    try:
        return await userbot.test()
    except Exception as e:
        raise HTTPException(400, f"测试失败: {type(e).__name__}: {e}")


@router.post("/tg/userbot/start-login")
async def tg_start(body: dict, _=Depends(require_user)):
    if _rate_limited("userbot_login"):
        _reject("userbot_login", "TG 对频繁发送验证码的账号有封禁风险")
    api_id = str(body.get("api_id") or "").strip()
    api_hash = str(body.get("api_hash") or "").strip()
    phone = str(body.get("phone") or "").strip()
    if not api_id.isdigit():
        raise HTTPException(400, "api_id 必须是数字（在 my.telegram.org 申请）")
    if not api_hash:
        raise HTTPException(400, "请填写 api_hash")
    if not phone:
        raise HTTPException(400, "请填写手机号（含国际区号，如 +8613800000000）")
    try:
        return await userbot.login_start(int(api_id), api_hash, phone)
    except Exception as e:
        raise HTTPException(400, f"发送验证码失败: {e}")


@router.post("/tg/userbot/submit-code")
async def tg_code(body: dict, _=Depends(require_user)):
    code = str(body.get("code") or "").strip()
    if not code:
        raise HTTPException(400, "请填写验证码")
    try:
        return await userbot.login_submit(code)
    except Exception as e:
        raise HTTPException(400, f"验证码登录失败: {e}")


@router.post("/tg/userbot/submit-password")
async def tg_pwd(body: dict, _=Depends(require_user)):
    password = str(body.get("password") or "")
    if not password:
        raise HTTPException(400, "请填写两步验证密码")
    try:
        return await userbot.login_password(password)
    except Exception as e:
        raise HTTPException(400, f"两步验证失败: {e}")
