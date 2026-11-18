"""115 扫码登录与 Cookie 校验/导入模块（独立于文件读写客户端，避免循环依赖）。"""
import io
import json
import threading
import time

import qrcode
import qrcode.image.svg

from .. import config, database as db
from ..core import logstream


def _pan():
    """延迟获取 client 模块引用，避免循环导入且保证测试 patch 透明生效。"""
    from . import client
    return client


# ---------- 扫码登录 ----------

# 扫码登录可用的 115 客户端标识（与 p115client 的 APP_TO_SSOENT 已知集合一致）：
# qrcode_poll 的 app 参数来自请求体，白名单防止任意字符串直打 115 登录接口
_QR_LOGIN_APPS = frozenset({
    "web", "desktop", "ios", "bios", "115ios", "android", "bandroid", "115android",
    "ipad", "bipad", "115ipad", "tv", "apple_tv", "qandroid", "qios", "qipad",
    "os_windows", "os_mac", "os_linux", "windows", "mac", "linux",
    "wechatmini", "alipaymini", "harmony",
})


def _norm_qr_app(app) -> str:
    a = str(app or "").strip() or "alipaymini"
    return a if a in _QR_LOGIN_APPS else "alipaymini"


def qrcode_create() -> dict:
    """生成登录二维码。返回 {uid, qr_svg, token}。"""
    from p115client import P115Client
    app = _norm_qr_app(db.get_setting("115_app", "alipaymini"))
    resp = P115Client.login_qrcode_token(app=app)
    token = resp["data"]
    qr_content = token.pop("qrcode", "") or f"https://115.com/scan/dg-{token['uid']}"
    img = qrcode.make(qr_content, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return {
        "uid": token["uid"],
        "qr_svg": buf.getvalue().decode(),
        "token": token,  # 含 uid/time/sign，用于轮询
        "app": app,
    }


def qrcode_poll(token: dict) -> dict:
    """长轮询扫码状态（115 接口挂起最长 30s）。

    返回 status: 0 等待 / 1 已扫描 / 2 已确认 / 负数 过期或取消。
    """
    from p115client import P115Client
    try:
        resp = P115Client.login_qrcode_scan_status(token, timeout=32)
    except Exception as e:
        logstream.warn("115", f"扫码状态查询异常（视为继续等待）: {e}")
        return {"status": 0}  # 网络抖动视为继续等待
    data = resp.get("data") or {}
    status = data.get("status", 0)
    return {"status": status}


_finish_lock = threading.Lock()  # 串行化取 cookie：并发调用会触发 115 风控

# 115 登录风控的已知错误 → 给用户的可操作指引（海外/机房 IP 最常触发）
_LOGIN_RISK_HINT = {
    "40101017": "115 风控拦截了本次登录（常见于海外/机房 IP）。可稍后重试或更换网络出口，"
                "或使用「导入 Cookie」：在本地浏览器登录 115.com 后复制 Cookie 粘贴。",
}


def _login_err_hint(resp: dict) -> str:
    """把取 cookie 失败的原始返回翻译成带指引的错误信息（已知风控错误不展示原始 JSON）。"""
    errno = str(resp.get("errno") or resp.get("code") or "")
    if errno in _LOGIN_RISK_HINT:
        return _LOGIN_RISK_HINT[errno]
    raw = json.dumps(resp, ensure_ascii=False)[:300]
    if "IP登录异常" in raw:
        return ("115 登录风控（IP 登录异常），一般当日 0 点自动解禁；可更换网络出口重试，"
                "或点「手动导入 Cookie」。")
    return f"获取 cookie 失败: {_pan().Pan115Error._scrub(raw)}"


def qrcode_finish(uid: str, app: str) -> dict:
    """扫码确认成功后取 cookie 并保存。

    取 cookie 接口对并发/重复调用敏感（易报"IP登录异常"），这里：
    - 进程内加锁串行化，避免多端同时轮询时并发取 cookie
    - 失败自动重试 3 次（间隔 1.5s），规避瞬时风控；确定性风控错误（如 40101017）不重试
    """
    from p115client import P115Client
    last_err: Exception | None = None
    app = _norm_qr_app(app)  # 白名单校验（qrcode_poll 的 app 来自请求体）
    with _finish_lock:
        for _ in range(3):
            try:
                resp = P115Client.login_qrcode_scan_result(uid, app)
                cookie = (resp.get("data") or {}).get("cookie", "") or resp.get("cookie", "")
                if cookie:
                    config.COOKIE_FILE.write_text(cookie)
                    config.secure_file(config.COOKIE_FILE)
                    _pan().reset_client()
                    db.set_setting("cookie_ok", True)
                    logstream.info("115", "扫码登录成功")
                    return {"ok": True}
                # 确定性风控错误（老乡验证等）：重试无意义且会加重风控，直接给出指引
                errno = str(resp.get("errno") or resp.get("code") or "")
                if errno in _LOGIN_RISK_HINT:
                    raise _pan().Pan115Error(_login_err_hint(resp))
                last_err = _pan().Pan115Error(_login_err_hint(resp))
            except _pan().Pan115Error:
                raise
            except Exception as e:
                last_err = e
            time.sleep(1.5)
    raise last_err or _pan().Pan115Error("获取 cookie 失败")


def validate_cookie(cookie: str) -> dict:
    """Cookie 有效性校验统一入口：项目里所有「新增 Cookie」的地方（手动导入及
    未来其他方式）都必须经此校验通过后才能落盘。
    返回 {ok, name, cookie(归一化后), err}。"""
    from p115client import P115Client
    cookie = cookie.strip().strip('"').strip("'")
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    if not cookie:
        return {"ok": False, "name": "", "cookie": "", "err": "Cookie 为空"}
    if not all(k in cookie for k in ("UID=", "CID=", "SEID=")):
        return {"ok": False, "name": "", "cookie": "",
                "err": "Cookie 里缺少 UID/CID/SEID 字段：请复制完整的 115 登录 Cookie"
                       "（形如 UID=xxx;CID=xxx;SEID=xxx）"}
    try:
        client = P115Client(cookie)
        resp = client.user_my_info()
    except Exception as e:
        return {"ok": False, "name": "", "cookie": cookie, "err": f"Cookie 无法使用: {e}"}
    if not resp.get("state"):
        return {"ok": False, "name": "", "cookie": cookie, "err": f"Cookie 校验失败: {str(resp)[:200]}"}
    data = resp.get("data", {}) or {}
    name = data.get("user_name", "") or data.get("uname", "")
    return {"ok": True, "name": name, "cookie": cookie, "err": ""}


def import_cookie(cookie: str) -> dict:
    """手动导入 cookie（海外 VPS 扫码被风控时的替代方案）：经统一校验入口通过后保存。"""
    v = validate_cookie(cookie)
    if not v["ok"]:
        raise _pan().Pan115Error(v["err"])
    config.COOKIE_FILE.write_text(v["cookie"])  # 落盘用校验时归一化后的同一份
    config.secure_file(config.COOKIE_FILE)
    _pan().reset_client()
    db.set_setting("cookie_ok", True)
    logstream.info("115", f"手动导入 Cookie 成功（账号：{v['name']}）")
    return {"ok": True, "name": v["name"]}


