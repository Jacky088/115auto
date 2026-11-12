"""全局配置：路径与环境变量。其余可变配置一律存 SQLite settings 表，网页可改。"""
import os
from pathlib import Path

APP_VERSION = "1.2.0"  # 展示于网页页脚与 /api/version，随发行包更新

BASE_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
STATIC_DIR = BACKEND_DIR / "static"
DB_PATH = DATA_DIR / "app.db"
COOKIE_FILE = DATA_DIR / "115_cookie.txt"
LOG_DIR = DATA_DIR / "logs"
MEDIA_META_DIR = DATA_DIR / "media_meta"
TG_SESSION_FILE = DATA_DIR / "tg_session"
SECRET_FILE = DATA_DIR / "secret.key"

WEB_USERNAME = os.environ.get("WEB_USERNAME", "admin")
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "admin123")

# 可信反向代理（逗号分隔 IP/CIDR）。只有直连来源命中此列表时才解析 X-Forwarded-For，
# 否则一律使用 socket 来源地址，防止客户端伪造 XFF 绕过登录限速。
TRUSTED_PROXIES = tuple(
    p.strip() for p in os.environ.get("TRUSTED_PROXIES", "").split(",") if p.strip()
)

for d in (DATA_DIR, LOG_DIR, MEDIA_META_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _ensure_writable_home(posix: bool | None = None) -> None:
    """HOME 兜底（仅 POSIX/容器场景）：p115client 在模块 import 时就会往
    ~/.p115client.cache.d 写缓存目录（const.py 硬编码 ~ 展开，无法用参数改路径）。
    若进程 HOME 指向不可写目录（如容器降权后残留 root 的 /root），115 客户端会整体
    初始化失败，表现为 Cookie「失效」、重新导入也报 Permission denied。
    这里在所有模块导入 p115client 之前检查 HOME：不可写则改指 DATA_DIR/home 并建目录。
    """
    import os
    if posix is None:
        posix = os.name == "posix"
    if not posix:
        return  # Windows 开发环境权限模型不同，不做处理
    home = os.environ.get("HOME", "")
    if home:
        try:
            (Path(home) / ".p115client.cache.d").mkdir(parents=True, exist_ok=True)
            return  # HOME 可写，维持原状
        except OSError:
            pass
    fallback = DATA_DIR / "home"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        os.environ["HOME"] = str(fallback)
    except OSError:
        pass  # 数据卷也不可写：维持原状，让后续错误保持原样便于排查


_ensure_writable_home()


def secure_file(p: "Path") -> None:
    """收紧凭据文件权限（Linux 下 600）；Windows 文件权限模型不同，尽力而为不报错。"""
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
