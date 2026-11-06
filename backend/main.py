"""FastAPI 入口：装配路由、托管前端静态文件、启动后台任务。"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import auth, config, database as db
from .api import account, ext, files, manage, ws
from .core import logstream
from .tg import polling, userbot
from .worker import bot_commands, jobs, queue_worker

db.init()
_background: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.ensure_initial_password()
    logstream.info("system", "115 自动转存助手启动")
    # 进程重启恢复：上一轮被中断的 running 任务重新排队，否则会永久卡在「转存中」
    recovered = db.execute("UPDATE transfers SET status='queued' WHERE status='running'")
    queued = db.query("SELECT count(*) c FROM transfers WHERE status='queued'", one=True)["c"]
    if recovered or queued:
        logstream.info("transfer",
                       f"队列恢复：待转存 {queued} 个任务（分享+云下载）；今日已用额度续算不清零，"
                       "额度足时按顺序自动接续")
    if recovered:
        logstream.warn("transfer", f"检测到 {recovered} 个上次中断的转存任务，已重新加入队列")
    # 后台任务
    mode = db.get_setting("tg_mode", "polling")
    if mode in ("polling", "both"):
        _background.append(asyncio.create_task(polling.polling_loop()))
    if mode in ("userbot", "both"):
        await userbot.start_if_configured()
    _background.append(asyncio.create_task(queue_worker.queue_worker()))
    _background.append(asyncio.create_task(jobs.health_check_loop()))
    _background.append(asyncio.create_task(jobs.log_cleanup_loop()))
    _background.append(asyncio.create_task(jobs.offline_check_loop()))
    _background.append(asyncio.create_task(jobs.digest_loop()))
    _background.append(asyncio.create_task(jobs.auto_backup_loop()))
    _background.append(asyncio.create_task(bot_commands.bot_command_loop()))
    yield
    # 优雅停机：先取消后台任务并等待退出，再断开 Userbot —— 保证 Telethon session
    # 与 SQLite（WAL）完整落盘，避免强杀后 session 文件损坏需要重新登录
    for t in _background:
        t.cancel()
    if _background:
        await asyncio.gather(*_background, return_exceptions=True)
    try:
        if userbot.client is not None:
            await userbot.client.disconnect()
    except Exception:
        pass


app = FastAPI(title="115 自动转存助手", lifespan=lifespan)
app.include_router(manage.router)
app.include_router(ext.router)
app.include_router(account.router)
app.include_router(files.router)
app.include_router(ws.router)


@app.get("/api/health")
def health():
    return {"ok": True, "version": config.APP_VERSION}


# 前端静态文件（Docker 构建 / 本地 npm run build 后存在）
if config.STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")
else:
    from fastapi.responses import JSONResponse

    @app.get("/")
    async def _no_frontend():
        return JSONResponse({"msg": "前端未构建：请运行 npm run build，或使用 Docker 镜像"})
