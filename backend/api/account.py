"""账号路由：115 扫码登录 / 状态 / 容量，TG Userbot 登录流程。"""
from fastapi import APIRouter, Depends, HTTPException

from .. import database as db
from ..pan115 import client as pan
from ..tg import userbot
from .deps import require_user

router = APIRouter(prefix="/api")


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
            return {"status": 2, **pan.qrcode_finish(str(token["uid"]),
                                                     str(body.get("app") or "alipaymini"))}
        return result
    except Exception as e:
        raise HTTPException(500, f"轮询失败: {e}")


# ---------- TG userbot ----------
@router.get("/tg/userbot/status")
async def tg_status(_=Depends(require_user)):
    return await userbot.status()


@router.post("/tg/userbot/start-login")
async def tg_start(body: dict, _=Depends(require_user)):
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
