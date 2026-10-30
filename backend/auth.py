"""认证：pbkdf2 密码哈希 + JWT + 登录失败限速。"""
import hashlib
import os
import secrets
import threading
import time

import jwt

from . import config, database as db

_jwt_secret = ""
_fail_lock = threading.Lock()
_fail_records: list[float] = []  # 登录失败时间戳（内存，重启清零）
FAIL_LIMIT = 5       # 窗口内最大失败次数
FAIL_WINDOW = 60.0   # 窗口长度（秒）


def login_throttled() -> bool:
    """登录失败限速：60 秒内失败满 5 次则暂时锁定。"""
    with _fail_lock:
        now = time.time()
        while _fail_records and now - _fail_records[0] > FAIL_WINDOW:
            _fail_records.pop(0)
        return len(_fail_records) >= FAIL_LIMIT


def record_login_fail():
    with _fail_lock:
        _fail_records.append(time.time())


def _load_secret() -> str:
    global _jwt_secret
    if not _jwt_secret:
        if config.SECRET_FILE.exists():
            _jwt_secret = config.SECRET_FILE.read_text().strip()
        else:
            _jwt_secret = secrets.token_hex(32)
            config.SECRET_FILE.write_text(_jwt_secret)
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


def ensure_initial_password():
    """首次运行：把环境变量里的初始密码写进 settings。"""
    if not db.get_setting("web_password_hash"):
        db.set_setting("web_password_hash", hash_password(config.WEB_PASSWORD))
        if config.WEB_PASSWORD in ("admin", "admin123", "123456", "password"):
            from .core import logstream
            logstream.warn("system", "⚠️ 当前为弱初始密码，请登录后立即在右上角修改！")


def change_password(new_password: str):
    db.set_setting("web_password_hash", hash_password(new_password))


def make_token(username: str) -> str:
    payload = {"sub": username, "exp": int(time.time()) + 7 * 86400}
    return jwt.encode(payload, _load_secret(), algorithm="HS256")


def check_token(token: str) -> str | None:
    """返回 username，无效返回 None。"""
    try:
        payload = jwt.decode(token, _load_secret(), algorithms=["HS256"])
        return payload.get("sub")
    except Exception:
        return None
