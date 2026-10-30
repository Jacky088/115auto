"""账号路由：115 扫码登录 / 状态 / 容量，TG Userbot 登录流程。"""
import asyncio

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
    try:
        token = body.get("token") or {}
        result = pan.qrcode_poll(token)
        if result["status"] == 2:  # 已确认 → 取 cookie
            return {"status": 2, **pan.qrcode_finish(token["uid"], body.get("app", "alipaymini"))}
        return result
    except Exception as e:
        raise HTTPException(500, f"轮询失败: {e}")


# ---------- TG userbot ----------
@router.get("/tg/userbot/status")
def tg_status(_=Depends(require_user)):
    return userbot.status()


@router.post("/tg/userbot/start-login")
async def tg_start(body: dict, _=Depends(require_user)):
    try:
        result = await asyncio.to_thread(
            userbot.login_start, int(body["api_id"]), body["api_hash"], body["phone"])
        return result
    except Exception as e:
        raise HTTPException(400, f"发送验证码失败: {e}")


@router.post("/tg/userbot/submit-code")
async def tg_code(body: dict, _=Depends(require_user)):
    try:
        return await asyncio.to_thread(userbot.login_submit, body["code"])
    except Exception as e:
        raise HTTPException(400, f"验证码登录失败: {e}")


@router.post("/tg/userbot/submit-password")
async def tg_pwd(body: dict, _=Depends(require_user)):
    try:
        return await asyncio.to_thread(userbot.login_password, body["password"])
    except Exception as e:
        raise HTTPException(400, f"两步验证失败: {e}")
