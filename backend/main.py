"""FastAPI 入口：装配路由、托管前端静态文件、启动后台任务。"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import auth, config, database as db
from .api import account, manage, ws
from .core import logstream
from .tg import polling, userbot
from .worker import bot_commands, jobs, queue_worker

db.init()
_background: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.ensure_initial_password()
    logstream.info("system", "115 自动转存助手启动")
    # 后台任务
    mode = db.get_setting("tg_mode", "polling")
    if mode in ("polling", "both"):
        _background.append(asyncio.create_task(polling.polling_loop()))
    if mode in ("userbot", "both"):
        await userbot.start_if_configured()
    _background.append(asyncio.create_task(queue_worker.queue_worker()))
    _background.append(asyncio.create_task(jobs.cookie_check_loop()))
    _background.append(asyncio.create_task(jobs.log_cleanup_loop()))
    _background.append(asyncio.create_task(jobs.offline_check_loop()))
    _background.append(asyncio.create_task(bot_commands.bot_command_loop()))
    yield
    for t in _background:
        t.cancel()


app = FastAPI(title="115 自动转存助手", lifespan=lifespan)
app.include_router(manage.router)
app.include_router(account.router)
app.include_router(ws.router)


@app.get("/api/health")
def health():
    return {"ok": True}


# 前端静态文件（Docker 构建 / 本地 npm run build 后存在）
if config.STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")
else:
    from fastapi.responses import JSONResponse

    @app.get("/")
    async def _no_frontend():
        return JSONResponse({"msg": "前端未构建：请运行 npm run build，或使用 Docker 镜像"})
