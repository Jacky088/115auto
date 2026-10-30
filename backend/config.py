"""全局配置：路径与环境变量。其余可变配置一律存 SQLite settings 表，网页可改。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
STATIC_DIR = BACKEND_DIR / "static"
DB_PATH = DATA_DIR / "app.db"
COOKIE_FILE = DATA_DIR / "115_cookie.txt"
LOG_DIR = DATA_DIR / "logs"
TG_SESSION_FILE = DATA_DIR / "tg_session"
SECRET_FILE = DATA_DIR / "secret.key"

WEB_USERNAME = os.environ.get("WEB_USERNAME", "admin")
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "admin123")

for d in (DATA_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)
