"""认证：pbkdf2 密码哈希 + JWT + 登录失败限速（按来源 IP）。"""
import hashlib
import os
import secrets
import threading
import time

import jwt

from . import config, database as db

_jwt_secret = ""
_secret_lock = threading.Lock()
_fail_lock = threading.Lock()
_fail_records: dict[str, list[float]] = {}  # 来源 IP → 失败时间戳（内存，重启清零）
FAIL_LIMIT = 5        # 窗口内单 IP 最大失败次数
FAIL_WINDOW = 60.0    # 窗口长度（秒）
_FAIL_MAX_IPS = 1000  # 防内存膨胀：跟踪的 IP 数上限

_TOKEN_TTL = 7 * 86400          # JWT 有效期
TOKEN_TTL = _TOKEN_TTL          # 对外公开（海报 Cookie 等需要对齐有效期）
_RENEW_AFTER = _TOKEN_TTL // 2  # 剩余不足一半时滑动续期


def _evict_oldest_ip():
    """IP 跟踪数超限时淘汰最旧条目（需持 _fail_lock 调用）。"""
    if len(_fail_records) > _FAIL_MAX_IPS:
        oldest = min(_fail_records, key=lambda k: _fail_records[k][-1] if _fail_records[k] else 0)
        _fail_records.pop(oldest, None)


def login_throttled(ip: str = "") -> bool:
    """按来源 IP 的登录失败限速：60 秒内失败满 5 次则暂时锁定该 IP。

    按 IP 而非全局计数：攻击者无法用错误密码把管理员的正常登录入口锁死。
    """
    now = time.time()
    with _fail_lock:
        recs = [t for t in _fail_records.get(ip, []) if now - t < FAIL_WINDOW]
        _fail_records[ip] = recs
        _evict_oldest_ip()
        return len(recs) >= FAIL_LIMIT


def record_login_fail(ip: str = ""):
    now = time.time()
    with _fail_lock:
        recs = [t for t in _fail_records.get(ip, []) if now - t < FAIL_WINDOW]
        recs.append(now)
        _fail_records[ip] = recs
        _evict_oldest_ip()


def _load_secret() -> str:
    global _jwt_secret
    if _jwt_secret:
        return _jwt_secret
    with _secret_lock:
        if not _jwt_secret:
            if config.SECRET_FILE.exists():
                _jwt_secret = config.SECRET_FILE.read_text().strip()
            if not _jwt_secret:
                _jwt_secret = secrets.token_hex(32)
                config.SECRET_FILE.write_text(_jwt_secret)
                config.secure_file(config.SECRET_FILE)
    return _jwt_secret


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    salt, _ = stored.split("$", 1)
    return secrets.compare_digest(hash_password(password, salt), stored)


# 弱默认密码：首次初始化时检测到这些值会改为生成随机密码（防止 admin123 裸奔公网）
_WEAK_PASSWORDS = ("admin", "admin123", "123456", "password", "admin888", "12345678", "123456789")
INITIAL_PASSWORD_FILE = config.DATA_DIR / "initial_password.txt"


def _initial_password() -> tuple[str, bool]:
    """返回 (初始密码, 是否为随机生成)。写入失败时返回 ("", True)，调用方跳过初始化。

    环境变量 WEB_PASSWORD 已设置且非弱值 → 原样使用；
    未设置或为弱默认值 → 生成随机强密码，写入 data/initial_password.txt（600 权限），
    日志只提示文件路径不回显密码本身（日志可能被截图/转发）。
    """
    from .core import logstream
    pwd = str(config.WEB_PASSWORD or "").strip()
    if pwd and pwd.lower() not in _WEAK_PASSWORDS:
        return pwd, False
    pwd = secrets.token_urlsafe(12)
    try:
        INITIAL_PASSWORD_FILE.write_text(pwd)
        config.secure_file(INITIAL_PASSWORD_FILE)
        logstream.warn("system", "⚠️ 未设置 WEB_PASSWORD 或为弱默认值，已生成随机初始密码，"
                                 "请打开 data/initial_password.txt 查看（登录修改密码后该文件自动删除）")
    except OSError:
        # 写入失败时不返回密码：避免把"无人知晓的密码"哈希入库造成永久锁死，重启会自动重试
        logstream.error("system", "随机初始密码写入失败，本次跳过初始化，重启后将自动重试；"
                                  "也可手动设置环境变量 WEB_PASSWORD")
        return "", True
    return pwd, True


def ensure_initial_password():
    """首次运行：把初始密码写进 settings。"""
    if not db.get_setting("web_password_hash"):
        pwd, generated = _initial_password()
        if not pwd:
            return  # 写入失败：不落哈希，重启自动重试
        db.set_setting("web_password_hash", hash_password(pwd))
        if not generated and pwd.lower() in _WEAK_PASSWORDS:
            from .core import logstream
            logstream.warn("system", "⚠️ 当前为弱初始密码，请登录后立即在右上角修改")


def change_password(new_password: str):
    db.set_setting("web_password_hash", hash_password(new_password))
    try:  # 改密后随机初始密码已完成使命，删除文件避免残留
        INITIAL_PASSWORD_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def rotate_secret():
    """轮换 JWT 签名密钥：改密/改用户名后使所有已签发 token 立即失效。

    旧会话（含可能被窃取的 token）无法继续访问；调用方需在响应里签发新 token 供当前会话续期。
    """
    global _jwt_secret
    with _secret_lock:
        _jwt_secret = secrets.token_hex(32)
        config.SECRET_FILE.write_text(_jwt_secret)
        config.secure_file(config.SECRET_FILE)


def make_token(username: str) -> str:
    payload = {"sub": username, "exp": int(time.time()) + _TOKEN_TTL,
               "iat": int(time.time())}
    return jwt.encode(payload, _load_secret(), algorithm="HS256")


def token_renewable(token: str) -> bool:
    """剩余有效期不足一半时返回 True（滑动续期，活跃用户无需定期重新登录）。"""
    try:
        payload = jwt.decode(token, _load_secret(), algorithms=["HS256"])
    except Exception:
        return False
    exp = payload.get("exp") or 0
    return exp - time.time() < _RENEW_AFTER


def check_token(token: str) -> str | None:
    """返回 username，无效返回 None。"""
    try:
        payload = jwt.decode(token, _load_secret(), algorithms=["HS256"])
        return payload.get("sub")
    except Exception:
        return None
