"""Telethon Userbot：用户账号实时监听频道。支持网页驱动登录（手机号→验证码→两步验证密码）。

登录状态机（供 API 调用，全部为 async，运行在主事件循环上）：
  await login_start(api_id, api_hash, phone) → 发送验证码
  await login_submit(code) → 若有两步验证则返回 needs_password
  await login_password(pwd) → 完成
登录态保存在 data/tg_session.session，重启后自动连接。

实现要点：
- 登录过程使用独立的临时 session（data/tg_login.session），成功后原子替换主 session，
  避免与正在运行的 userbot 争抢同一个 SQLite session 文件；
- 所有 Telethon 调用直接 await，不在线程池里另建事件循环；
- 接收循环（_pump）在主事件循环上用 asyncio.create_task 拉起。
"""
import asyncio
import re
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError

from .. import config, database as db
from ..core import logstream, pipeline
from . import resolver

try:
    from telethon.errors import PhoneCodeExpiredError, SessionExpiredError
except Exception:  # pragma: no cover - 兼容不同 Telethon 版本
    class PhoneCodeExpiredError(Exception):
        pass

    class SessionExpiredError(Exception):
        pass

_lock = asyncio.Lock()
client: TelegramClient | None = None
_login_flow: dict = {}  # 网页登录中间状态

_LOGIN_SESSION = config.TG_SESSION_FILE.parent / "tg_login"
_SESSION_SUFFIXES = (".session", ".session-journal")


def _session_files(base: Path) -> list[Path]:
    return [base.with_name(base.name + suffix) for suffix in _SESSION_SUFFIXES]


def _remove_session_files(base: Path):
    for path in _session_files(base):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def _proxy_kw() -> dict:
    """把设置页的代理地址转成 Telethon 支持的 proxy 参数。

    python-socks 的 parse_proxy_url 返回 (type, host, port, username, password)，
    而 Telethon 需要 (proxy_type, addr, port, username, password, rdns) 或等价 dict；
    这里显式构造 dict，避免元组顺序错位（rdns 被误当成用户名）。
    """
    proxy = str(db.get_setting("proxy", "") or "").strip()
    if not proxy:
        return {}
    try:
        from python_socks import parse_proxy_url

        parsed = parse_proxy_url(proxy)
        proxy_type, host, port = parsed[0], parsed[1], parsed[2]
        username = parsed[3] if len(parsed) > 3 else None
        password = parsed[4] if len(parsed) > 4 else None
        return {"proxy": {
            "proxy_type": proxy_type,
            "addr": host,
            "port": int(port),
            "username": username or None,
            "password": password or None,
            "rdns": True,
        }}
    except Exception as e:
        logstream.warn("userbot", f"代理配置无效（{proxy}），本次将直连: {e}")
        return {}


def _make(api_id: int, api_hash: str, session_path: Path | None = None) -> TelegramClient:
    return TelegramClient(
        str(session_path or config.TG_SESSION_FILE), int(api_id), str(api_hash), **_proxy_kw())


async def status() -> dict:
    connected = False
    if client is not None:
        try:
            connected = bool(client.is_connected()) and bool(await client.is_user_authorized())
        except Exception:
            connected = False
    return {
        "configured": bool(db.get_setting("tg_api_id") and db.get_setting("tg_api_hash")),
        "connected": connected,
        "login_pending": bool(_login_flow.get("pending")),
        "ok": db.get_setting("userbot_ok", None),
    }


async def test() -> dict:
    """一键自检：连接 + 授权 + 逐个验证 Userbot 频道可见性（能否真正收到消息）。

    返回 {ok, me, channels: [{name, ok, title, err}], err}；未连接时 ok=False。
    """
    if client is None:
        return {"ok": False, "err": "Userbot 未登录，请先完成登录"}
    try:
        if not (client.is_connected() and await client.is_user_authorized()):
            return {"ok": False, "err": "Userbot 已断开或登录失效，请重新登录"}
    except Exception as e:
        return {"ok": False, "err": f"连接检查失败: {type(e).__name__}: {e}"}
    try:
        me = await client.get_me()
    except Exception as e:
        return {"ok": False, "err": f"获取账号信息失败: {type(e).__name__}: {e}"}
    who = getattr(me, "username", None) or getattr(me, "first_name", "") or ""

    rows = [dict(r) for r in db.query("SELECT name FROM channels WHERE type='userbot' AND enabled=1")]
    channels: list[dict] = []
    ok_all = True
    if rows:
        # 预热对话缓存，提高 get_entity 对已加入频道的识别率
        try:
            await client.get_dialogs(limit=200)
        except Exception:
            pass
    for r in rows:
        name = str(r["name"] or "").strip()
        title, err = "", ""
        try:
            entity = await client.get_entity(_entity_ref(name))
            title = getattr(entity, "title", "") or name
        except Exception as e:
            ok_all = False
            err = (f"无法访问（账号未加入该频道，或用户名/ID 填错？）"
                   f"{type(e).__name__}: {str(e)[:120]}")
        channels.append({"name": name, "ok": not err, "title": title, "err": err})
    if not channels:
        # 尚未添加 Userbot 类型频道：只验证连接与授权
        pass
    return {"ok": ok_all, "me": who, "channels": channels}


async def check_channel(name: str) -> dict:
    """添加频道前的可访问性校验：账号能解析到该频道实体才算通过。

    返回 {ok, title, err}。未登录时直接失败，避免添加一个永远收不到消息的 Userbot 频道。
    """
    if client is None:
        return {"ok": False, "title": "", "err": "Userbot 未登录，请先在「账号管理」完成登录"}
    try:
        if not (client.is_connected() and await client.is_user_authorized()):
            return {"ok": False, "title": "", "err": "Userbot 已断开或登录失效，请重新登录"}
    except Exception as e:
        return {"ok": False, "title": "", "err": f"连接检查失败: {type(e).__name__}: {e}"}
    try:
        entity = await client.get_entity(_entity_ref(name))
    except Exception as e:
        return {"ok": False, "title": "",
                "err": (f"无法访问该频道（账号未加入该频道，或用户名/ID 填错？）"
                        f"{type(e).__name__}: {str(e)[:120]}")}
    return {"ok": True, "title": getattr(entity, "title", "") or name, "err": ""}


def _entity_ref(name: str):
    """频道名 → Telethon 实体引用：数字 ID 用 PeerChannel，其余按 username 处理。"""
    s = name.strip().lstrip("@")
    if re.fullmatch(r"-100\d+", s):
        from telethon.tl.types import PeerChannel
        return PeerChannel(int(s[4:]))
    if re.fullmatch(r"-?\d{8,}", s):
        from telethon.tl.types import PeerChannel
        return PeerChannel(int(s.lstrip("-")))
    return s


async def _cleanup_flow():
    """放弃当前登录流程：断开临时客户端并清理临时 session。"""
    flow = _login_flow
    c = flow.get("client")
    if c is not None:
        try:
            await c.disconnect()
        except Exception:
            pass
    _login_flow.clear()
    _remove_session_files(_LOGIN_SESSION)


async def login_start(api_id: int, api_hash: str, phone: str) -> dict:
    """发送登录验证码（在临时 session 中完成，成功后替换主 session）。"""
    async with _lock:
        await _cleanup_flow()
        c = _make(api_id, api_hash, _LOGIN_SESSION)
        try:
            await c.connect()
            sent = await c.send_code_request(phone)
        except Exception:
            try:
                await c.disconnect()
            except Exception:
                pass
            _remove_session_files(_LOGIN_SESSION)
            raise
        _login_flow.update({
            "pending": True,
            "client": c,
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash,
            "api_id": int(api_id),
            "api_hash": api_hash,
        })
        return {"sent": True}


async def login_submit(code: str) -> dict:
    async with _lock:
        flow = _login_flow
        if not flow.get("pending"):
            raise RuntimeError("没有进行中的登录，请先发送验证码")
        # 验证码错误次数上限：防止会话内无限暴力尝试（TG 侧也有错误次数限制，超限会封 session）
        tries = int(flow.get("code_tries", 0))
        if tries >= 5:
            await _cleanup_flow()
            raise RuntimeError("验证码错误次数过多，登录流程已取消，请重新发送验证码")
        c: TelegramClient = flow["client"]
        try:
            await c.sign_in(phone=flow["phone"], code=code,
                            phone_code_hash=flow["phone_code_hash"])
        except SessionPasswordNeededError:
            flow["needs_password"] = True
            return {"needs_password": True}
        except (PhoneCodeExpiredError, SessionExpiredError) as e:
            await _cleanup_flow()
            raise RuntimeError(f"验证码已失效，请重新发送验证码（{type(e).__name__}）")
        except Exception:
            flow["code_tries"] = tries + 1
            raise
        try:
            await _finish_login(c, flow)
        except Exception:
            # 收尾阶段失败（如网络中断导致新 session 校验失败）：清理流程，让用户重新发起登录
            await _cleanup_flow()
            raise
        return {"ok": True}


async def login_password(pwd: str) -> dict:
    async with _lock:
        flow = _login_flow
        if not flow.get("needs_password"):
            raise RuntimeError("当前不需要两步验证密码")
        tries = int(flow.get("pwd_tries", 0))
        if tries >= 5:
            await _cleanup_flow()
            raise RuntimeError("两步验证密码错误次数过多，登录流程已取消，请重新发送验证码")
        c: TelegramClient = flow["client"]
        try:
            await c.sign_in(password=pwd)
        except Exception:
            flow["pwd_tries"] = tries + 1
            raise
        try:
            await _finish_login(c, flow)
        except Exception:
            await _cleanup_flow()
            raise
        return {"ok": True}


async def _finish_login(c: TelegramClient, flow: dict):
    """登录成功：用临时 session 替换主 session，并拉起接收循环。"""
    global client
    me = await c.get_me()
    await c.disconnect()

    # 断开旧客户端，确保主 session 文件可替换
    old = client
    client = None
    if old is not None:
        try:
            await old.disconnect()
        except Exception:
            pass

    _remove_session_files(config.TG_SESSION_FILE)
    for suffix in _SESSION_SUFFIXES:
        src = _LOGIN_SESSION.with_name(_LOGIN_SESSION.name + suffix)
        dst = config.TG_SESSION_FILE.with_name(config.TG_SESSION_FILE.name + suffix)
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dst)

    db.set_setting("tg_api_id", int(flow["api_id"]))
    db.set_setting("tg_api_hash", flow["api_hash"])
    db.set_setting("tg_phone", flow["phone"])

    # 用新的主 session 重新连接并校验，确认登录态真正可用
    c2 = _make(int(flow["api_id"]), flow["api_hash"])
    await c2.connect()
    if not await c2.is_user_authorized():
        await c2.disconnect()
        raise RuntimeError("登录状态校验失败，请重新发送验证码登录")

    client = c2
    db.set_setting("userbot_ok", True)
    _login_flow.clear()
    _remove_session_files(_LOGIN_SESSION)
    _register_handlers(c2)
    asyncio.create_task(_pump(c2), name="userbot-pump")
    asyncio.create_task(catch_up(c2), name="userbot-catchup")
    logstream.info("userbot",
                   f"Userbot 登录成功: {getattr(me, 'username', None) or getattr(me, 'first_name', '')}")


async def _pump(c: TelegramClient):
    try:
        await c.run_until_disconnected()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logstream.error("userbot", f"接收循环退出: {logstream.exc(e)}")
    else:
        logstream.warn("userbot", "Userbot 连接已断开")
    # 只有当前客户端退出才标记失效：重新登录时旧客户端断开不应覆盖新登录状态
    if client is c:
        db.set_setting("userbot_ok", False)


def _norm_id(value) -> str:
    s = str(value or "").strip().lstrip("@").lower()
    if s.startswith("-100"):
        return s[4:]
    if s.startswith("-"):
        return s[1:]
    return s


def _match_channel(rows, chat) -> str | None:
    """按 username 或数字 id 匹配已配置的 userbot 频道，返回配置里的频道名。"""
    username = (getattr(chat, "username", None) or "").strip().lstrip("@").lower()
    chat_id = _norm_id(getattr(chat, "id", ""))
    for r in rows:
        name = str(r["name"] or "").strip().lstrip("@")
        if not name:
            continue
        if username and name.lower() == username:
            return name
        if chat_id and _norm_id(name) == chat_id:
            return name
    return None


def _register_handlers(c: TelegramClient):
    if getattr(c, "_autosave_handlers", False):
        return

    @c.on(events.NewMessage())
    async def on_new_message(event):
        try:
            chat = await event.get_chat()
            watched = db.query("SELECT name FROM channels WHERE type='userbot' AND enabled=1")
            name = _match_channel(watched, chat)
            if name is None:
                # 未配置任何 userbot 频道时保持旧行为：处理所有消息
                if watched:
                    return
                name = getattr(chat, "username", None) or str(getattr(chat, "id", ""))
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
            logstream.error("userbot", f"消息处理失败: {logstream.exc(e)}")

    c._autosave_handlers = True


async def catch_up(c: TelegramClient):
    """断线/重启期间漏掉的消息补偿：逐个已启用的 Userbot 频道回溯最近 100 条。

    依赖 messages 表（主键 channel+message_id）天然去重，已处理过的不会重复入队；
    补偿在连接建立后立即执行，事件流继续实时收新消息，两边互不冲突。
    """
    try:
        watched = [dict(r) for r in
                   db.query("SELECT name FROM channels WHERE type='userbot' AND enabled=1")]
        if not watched:
            return
        for r in watched:
            name = str(r["name"] or "").strip()
            try:
                entity = await c.get_entity(_entity_ref(name))
            except Exception as e:
                logstream.warn("userbot", f"[{name}] 补漏跳过（频道实体不可访问）: {type(e).__name__}")
                continue
            try:
                msgs = await c.get_messages(entity, limit=100)
            except Exception as e:
                logstream.warn("userbot", f"[{name}] 补漏拉取失败（下一轮不再自动重试）: {type(e).__name__}: {str(e)[:120]}")
                continue
            fresh = 0
            for m in reversed(msgs):  # 从旧到新处理，与频道推送顺序一致
                if not m or m.id <= 0:
                    continue
                text = m.message or ""
                buttons = [b.url for row in (m.buttons or [])
                           for b in row if getattr(b, "url", None)]
                if text or buttons:
                    try:
                        full_text = await resolver.resolve_message(text, buttons)
                        if full_text:
                            pipeline.process_message(name, m.id, full_text)
                            fresh += 1
                    except Exception as e:
                        logstream.error("userbot", f"[{name}] 补漏消息 {m.id} 处理失败: {logstream.exc(e)}")
            if fresh:
                logstream.info("userbot", f"[{name}] 断线补漏完成，补处理 {fresh} 条消息")
    except Exception as e:
        logstream.error("userbot", f"断线补漏异常: {logstream.exc(e)}")


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
            asyncio.create_task(_pump(c), name="userbot-pump")
            asyncio.create_task(catch_up(c), name="userbot-catchup")
            logstream.info("userbot", "Userbot 已自动连接")
        else:
            await c.disconnect()
            db.set_setting("userbot_ok", False)
            logstream.warn("userbot", "已有配置但 session 失效，请在网页重新登录")
    except Exception as e:
        db.set_setting("userbot_ok", False)
        logstream.error("userbot", f"自动连接失败: {logstream.exc(e)}")
