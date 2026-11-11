"""FastAPI 入口：装配路由、托管前端静态文件、启动后台任务。"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config, database as db
from .api import account, ext, files, manage, ws
from .core import logstream
from .tg import episode_backfill, polling, userbot
from .worker import bot_commands, jobs, queue_worker

db.init()
_background: list[asyncio.Task] = []

# 请求体上限：未认证端点（如登录）也可被超大 JSON 在小内存 NAS 上直接 OOM；
# 恢复备份的 body 放宽到 20MB（与 Bot 端 10MB 检查对齐并留余量）
_MAX_BODY_BYTES = 1 * 1024 * 1024
_MAX_BODY_EXEMPT_PREFIXES = ("/api/backup/",)
_MAX_BODY_EXEMPT = 20 * 1024 * 1024


def _harden_credential_files():
    """启动时收紧凭据文件权限（cookie / JWT 密钥 / TG session）。已存在才处理，尽力而为。"""
    import os
    candidates = [config.COOKIE_FILE, config.SECRET_FILE, auth.INITIAL_PASSWORD_FILE,
                  config.TG_SESSION_FILE.with_name(config.TG_SESSION_FILE.name + ".session"),
                  config.TG_SESSION_FILE.with_name(config.TG_SESSION_FILE.name + ".session-journal")]
    for p in candidates:
        try:
            if p.exists():
                os.chmod(p, 0o600)
        except OSError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    _harden_credential_files()
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
    _background.append(asyncio.create_task(episode_backfill.episode_backfill_loop()))
    _background.append(asyncio.create_task(jobs.digest_loop()))
    _background.append(asyncio.create_task(jobs.auto_backup_loop()))
    _background.append(asyncio.create_task(jobs.tg_notify_worker()))
    _background.append(asyncio.create_task(bot_commands.bot_command_loop()))
    _background.append(asyncio.create_task(jobs.auto_move_loop()))
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


app = FastAPI(title="115 自动转存助手", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


class _BodyTooLarge(BaseException):
    """请求体超过上限（在读取阶段触发，避免 chunked 绕过 Content-Length 检查）。

    继承 BaseException 而非 Exception：FastAPI 解析请求体时会把 Exception 统一包成
    400（There was an error parsing the body），从而吞掉本信号；BaseException 不会被
    其 `except Exception` 捕获，可稳定返回 413。
    """


class _BodyLimitMiddleware:
    """按实际读取字节数限制请求体，防止无 Content-Length 的 chunked 请求打爆内存。"""

    def __init__(self, app, limit: int = _MAX_BODY_BYTES,
                 exempt_prefixes: tuple = _MAX_BODY_EXEMPT_PREFIXES,
                 exempt_limit: int = _MAX_BODY_EXEMPT):
        self.app = app
        self.limit = limit
        self.exempt_prefixes = exempt_prefixes
        self.exempt_limit = exempt_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        limit = self.exempt_limit if path.startswith(self.exempt_prefixes) else self.limit
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            try:
                response = JSONResponse({"detail": "请求体过大"}, status_code=413)
                await response(scope, receive, send)
            except RuntimeError:
                pass  # 响应已开始发送，无法再改写状态码，直接结束


app.add_middleware(_BodyLimitMiddleware)


@app.middleware("http")
async def security_headers(request, call_next):
    """基础安全响应头 + 请求体上限：防 MIME 嗅探 / 防 iframe 嵌套 / 限制 Referer 外泄 /
    防未认证端点被超大 JSON OOM。不加严格 CSP（Element Plus 依赖内联样式，SPA 场景收错会白屏）。
    """
    limit = (_MAX_BODY_EXEMPT
             if request.url.path.startswith(_MAX_BODY_EXEMPT_PREFIXES) else _MAX_BODY_BYTES)
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > limit:
        return JSONResponse({"detail": "请求体过大"}, status_code=413)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # CSP：脚本只允许同源打包产物；Element Plus 依赖内联样式，样式保留 unsafe-inline。
    # 不启用 unsafe-eval（Vue 为构建期编译），兼顾 XSS 缓解与页面正常渲染。
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data:; "
        "connect-src 'self' ws: wss:; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'")
    return resp


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
