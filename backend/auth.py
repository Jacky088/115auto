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

_PBKDF2_ITERATIONS = 600_000   # OWASP 2023 建议（PBKDF2-SHA256 ≥600k）
_LEGACY_ITERATIONS = 120_000   # 旧格式哈希的迭代数（仅用于兼容校验，登录成功后透明升级）


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


def hash_password(password: str, salt: str | None = None,
                  iterations: int = _PBKDF2_ITERATIONS) -> str:
    """生成带算法/迭代数标注的密码哈希（旧版无标注格式在 verify_password 中兼容）。"""
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """兼容两种存储格式：新 pbkdf2_sha256$iter$salt$hash 与旧 salt$hex。"""
    if not stored:
        return False
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iters, salt, digest = stored.split("$", 3)
            dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters))
            return secrets.compare_digest(dk.hex(), digest)
        except (ValueError, TypeError):
            return False
    if "$" not in stored:
        return False
    salt, _ = stored.split("$", 1)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _LEGACY_ITERATIONS)
    return secrets.compare_digest(f"{salt}${dk.hex()}", stored)


def password_needs_rehash(stored: str) -> bool:
    """旧格式/低迭代哈希：验证成功后应调用 hash_password 透明升级。"""
    return bool(stored) and not stored.startswith("pbkdf2_sha256$")


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


def clear_secret_cache():
    """清空内存缓存的 JWT 密钥，下次校验/签发时从 SECRET_FILE 重新读取。"""
    global _jwt_secret
    with _secret_lock:
        _jwt_secret = ""


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
               "iat": int(time.time()), "ver": _token_version()}
    return jwt.encode(payload, _load_secret(), algorithm="HS256")


def token_renewable(token: str) -> bool:
    """剩余有效期不足一半时返回 True（滑动续期，活跃用户无需定期重新登录）。"""
    try:
        payload = jwt.decode(token, _load_secret(), algorithms=["HS256"])
    except Exception:
        return False
    exp = payload.get("exp") or 0
    return exp - time.time() < _RENEW_AFTER


def _token_version() -> int:
    """token 版本号（存 settings）：登出时递增使全部已签发 token 立即失效。"""
    try:
        return int(db.get_setting("token_version", 0) or 0)
    except (TypeError, ValueError):
        return 0


def revoke_all_tokens():
    """吊销全部已签发 token：递增版本号，check_token 校验 ver 不匹配即拒绝。

    单管理员系统下「登出即全端下线」代价可控；改密仍走 rotate_secret（连签名密钥一起换）。
    """
    db.set_setting("token_version", _token_version() + 1)


def check_token(token: str) -> str | None:
    """返回 username，无效（过期/签名错/版本已吊销）返回 None。"""
    try:
        payload = jwt.decode(token, _load_secret(), algorithms=["HS256"])
        if int(payload.get("ver", 0)) != _token_version():
            return None
        return payload.get("sub")
    except Exception:
        return None
