"""Telethon Userbot：用户账号实时监听频道。支持网页驱动登录（手机号→验证码→两步验证密码）。

登录状态机（供 API 调用）：
  login_start(api_id, api_hash, phone) → 发送验证码
  login_submit(code) → 若有两步验证则返回 needs_password
  login_password(pwd) → 完成
登录态保存在 data/tg_session.session，重启后自动连接。
"""
import asyncio
import threading

from telethon import TelegramClient, events

from .. import config, database as db
from ..core import logstream, pipeline
from . import resolver

_lock = threading.Lock()
client: TelegramClient | None = None
_login_flow: dict = {}  # 网页登录中间状态


def _make(api_id: int, api_hash: str, loop: asyncio.AbstractEventLoop | None = None) -> TelegramClient:
    proxy = db.get_setting("proxy", "")
    kw = {}
    if loop is not None:
        kw["loop"] = loop
    if proxy:
        # socks5://host:port 或 http://host:port（python-socks 两种都支持）
        try:
            import python_socks  # noqa: F401
            from telethon import connection
            from python_socks import parse_proxy_url
            kw["connection"] = connection.ConnectionTcpSOCKS5
            kw["proxy"] = parse_proxy_url(proxy)
        except ImportError:
            logstream.warn("userbot", "未安装 python-socks，忽略代理设置")
        except Exception as e:
            logstream.warn("userbot", f"代理地址解析失败（{proxy}）: {e}")
    return TelegramClient(config.TG_SESSION_FILE, api_id, api_hash, **kw)


def status() -> dict:
    ok = db.get_setting("userbot_ok", None)
    return {"configured": bool(db.get_setting("tg_api_id") and db.get_setting("tg_api_hash")),
            "connected": bool(client and client.is_connected() and client.is_user_authorized()),
            "login_pending": bool(_login_flow.get("pending")),
            "ok": ok}


def login_start(api_id: int, api_hash: str, phone: str) -> dict:
    """同步接口（在线程池跑），发送验证码。"""
    with _lock:
        global client
        _login_flow.clear()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        c = _make(api_id, api_hash, loop=loop)
        c.connect()
        sent = c.loop.run_until_complete(c.send_code_request(phone))
        _login_flow.update({"pending": True, "client": c, "loop": loop, "phone": phone,
                            "phone_code_hash": sent.phone_code_hash,
                            "api_id": api_id, "api_hash": api_hash})
        return {"sent": True}


def login_submit(code: str) -> dict:
    with _lock:
        flow = dict(_login_flow)
        if not flow.get("pending"):
            raise RuntimeError("没有进行中的登录")
        c: TelegramClient = flow["client"]
        loop: asyncio.AbstractEventLoop = flow["loop"]
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(c.sign_in(phone=flow["phone"], code=code,
                                              phone_code_hash=flow["phone_code_hash"]))
        except Exception as e:
            if "PASSWORD" in str(e).upper() or "SessionPasswordNeeded" in type(e).__name__:
                _login_flow["needs_password"] = True
                return {"needs_password": True}
            raise
        _finish_login(c, flow)
        return {"ok": True}


def login_password(pwd: str) -> dict:
    with _lock:
        flow = dict(_login_flow)
        if not flow.get("needs_password"):
            raise RuntimeError("当前不需要两步验证密码")
        c: TelegramClient = flow["client"]
        loop: asyncio.AbstractEventLoop = flow["loop"]
        asyncio.set_event_loop(loop)
        loop.run_until_complete(c.sign_in(password=pwd))
        _finish_login(c, flow)
        return {"ok": True}


def _finish_login(c: TelegramClient, flow: dict):
    global client
    me = c.loop.run_until_complete(c.get_me())
    db.set_setting("tg_api_id", flow["api_id"])
    db.set_setting("tg_api_hash", flow["api_hash"])
    db.set_setting("tg_phone", flow["phone"])
    db.set_setting("userbot_ok", True)
    client = c
    _login_flow.clear()
    logstream.info("userbot", f"Userbot 登录成功: {getattr(me, 'username', None) or getattr(me, 'first_name', '')}")
    # 注册消息处理器并开始接收
    _register_handlers(c)
    # 拉起接收循环
    asyncio.get_event_loop().create_task(_pump(c))


async def _pump(c: TelegramClient):
    try:
        await c.run_until_disconnected()
    except Exception as e:
        logstream.error("userbot", f"接收循环退出: {e}")
        db.set_setting("userbot_ok", False)


def _register_handlers(c: TelegramClient):
    if getattr(c, "_autosave_handlers", False):
        return

    @c.on(events.NewMessage())
    async def on_new_message(event):
        try:
            chat = await event.get_chat()
            name = getattr(chat, "username", None) or str(getattr(chat, "id", ""))
            watched = db.query("SELECT name FROM channels WHERE type='userbot' AND enabled=1")
            names = {r["name"].lstrip("@").lower() for r in watched}
            if names and (name or "").lower() not in names:
                return
            text = event.message.message or ""
            buttons = []
            rows = event.message.buttons
            if rows is None:
                try:
                    rows = await event.get_buttons()
                except Exception:
                    rows = []
            for row in (rows or []):
                for b in row:
                    if getattr(b, "url", None):
                        buttons.append(b.url)
            if text or buttons:
                full_text = await resolver.resolve_message(text, buttons)
                if full_text:
                    pipeline.process_message(name or "unknown", event.message.id, full_text)
        except Exception as e:
            logstream.error("userbot", f"消息处理失败: {e}")

    c._autosave_handlers = True


async def start_if_configured():
    """应用启动时：若已配置且 session 存在，自动连接。"""
    global client
    api_id = db.get_setting("tg_api_id")
    api_hash = db.get_setting("tg_api_hash")
    if not (api_id and api_hash):
        return
    try:
        c = _make(int(api_id), api_hash)
        await c.connect()
        if await c.is_user_authorized():
            client = c
            db.set_setting("userbot_ok", True)
            _register_handlers(c)
            asyncio.get_event_loop().create_task(_pump(c))
            logstream.info("userbot", "Userbot 已自动连接")
        else:
            db.set_setting("userbot_ok", False)
            logstream.warn("userbot", "已有配置但 session 失效，请在网页重新登录")
    except Exception as e:
        logstream.error("userbot", f"自动连接失败: {e}")
