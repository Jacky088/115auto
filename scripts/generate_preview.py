"""自动生成项目预览截图：docs/preview.png

基于纯净临时数据库注入演示数据，启动轻量 Web 服务，利用本地 Chrome 截取高质量全景图。
"""
import asyncio
import base64
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

TEMP_DIR = Path(tempfile.mkdtemp(prefix="preview_screen_"))
os.environ["DATA_DIR"] = str(TEMP_DIR)
os.environ["WEB_PASSWORD"] = "Admin123456!"

from backend import auth  # noqa: E402
from backend import database as db  # noqa: E402
from backend.core import logstream  # noqa: E402
from backend.pan115 import client as pan  # noqa: E402
from backend.worker import queue_worker  # noqa: E402


def seed_data():
    db.init()
    auth.ensure_initial_password()
    db.set_setting("setup_completed", True)
    db.set_setting("running", True)
    db.set_setting("daily_limit", 50)
    db.set_setting("cookie_ok", 1)

    now = time.time()
    today_str = datetime.date.today().isoformat()
    db.execute(
        "INSERT INTO daily_quota (day, count) VALUES (?, 12) "
        "ON CONFLICT(day) DO UPDATE SET count=12",
        (today_str,),
    )

    # 监控频道
    channels = [
        ("@movie_4k_hub", "polling", 1),
        ("@guoman_115", "polling", 1),
        ("@hd_series_share", "polling", 1),
        ("@documentary_channel", "polling", 1),
    ]
    for ch, tp, en in channels:
        db.execute(
            "INSERT INTO channels (name, type, enabled, created_at) VALUES (?, ?, ?, ?)",
            (ch, tp, en, now - 3600),
        )

    # 分类路由
    routes = [
        ("电影", "/媒体库/电影", "/云下载/电影"),
        ("电视剧", "/媒体库/电视剧", "/云下载/电视剧"),
        ("动漫", "/媒体库/动漫", "/云下载/动漫"),
        ("纪录片", "/媒体库/纪录片", "/云下载/纪录片"),
        ("综艺", "/媒体库/综艺", "/云下载/综艺"),
        ("少儿", "/媒体库/少儿", "/云下载/少儿"),
    ]
    for cat, td, od in routes:
        db.execute(
            "INSERT INTO routes (category, target_dir, offline_dir) VALUES (?, ?, ?) "
            "ON CONFLICT(category) DO UPDATE SET target_dir=excluded.target_dir, offline_dir=excluded.offline_dir",
            (cat, td, od),
        )

    # 转存任务数据 (最新 20 条)
    records = [
        # (id, title, category, kind, target_dir, size_gb, status, increment, priority, offset_s)
        (20, "凡人修仙传 第126集 4K国漫", "动漫", "share", "/媒体库/动漫/凡人修仙传", 2.6, "queued", 1, 0, 30),
        (19, "庆余年 第二季 第04集 补全资源", "电视剧", "share", "/媒体库/电视剧/庆余年", 4.1, "queued", 1, 1, 120),
        (18, "阿凡达：水之道 (2022) 2160p 杜比视界", "电影", "share", "/媒体库/电影", 38.4, "success", 0, 0, 300),
        (17, "地球脉动 第三季 全8集 4K HDR", "纪录片", "offline", "/云下载/纪录片", 46.2, "success", 0, 0, 600),
        (16, "漫长的季节 (2023) 全12集 4K高码", "电视剧", "share", "/媒体库/电视剧/漫长的季节", 54.8, "success", 0, 0, 1200),
        (15, "葬送的芙莉莲 全28集 [ANi 简繁外挂]", "动漫", "share", "/媒体库/动漫/葬送的芙莉莲", 31.5, "success", 0, 0, 2400),
        (14, "流浪地球2 (2023) 2160p REMUX", "电影", "share", "/媒体库/电影", 68.9, "success", 0, 0, 3600),
        (13, "奥本海默 (2023) IMAX 4K 原盘", "电影", "share", "/媒体库/电影", 79.3, "success", 0, 0, 5400),
        (12, "歌手2024 第12期 纯享版 1080p", "综艺", "share", "/媒体库/综艺", 6.4, "success", 0, 0, 7200),
        (11, "小猪佩奇 第九季 全52集 国粤双语", "少儿", "share", "/媒体库/少儿", 12.8, "success", 0, 0, 9000),
        # 历史数据，充实分类环形图
        (10, "繁花 (2023) 全30集 4K", "电视剧", "share", "/媒体库/电视剧/繁花", 52.3, "success", 0, 0, 86400 + 100),
        (9, "封神第一部：朝歌风云 4K HDR", "电影", "share", "/媒体库/电影", 42.1, "success", 0, 0, 86400 + 200),
        (8, "间谍过家家 第二季 全12集", "动漫", "share", "/媒体库/动漫/间谍过家家", 16.5, "success", 0, 0, 86400 + 300),
        (7, "蓝色星球2 全7集 4K", "纪录片", "share", "/媒体库/纪录片", 39.8, "success", 0, 0, 86400 + 400),
        (6, "脱口秀和Ta的朋友们 2024", "综艺", "share", "/媒体库/综艺", 8.2, "success", 0, 0, 86400 + 500),
        (5, "汪汪队立大功 第七季", "少儿", "share", "/媒体库/少儿", 9.4, "success", 0, 0, 86400 + 600),
        (4, "蜘蛛侠：纵横宇宙 4K", "电影", "share", "/媒体库/电影", 36.7, "success", 0, 0, 86400 + 700),
        (3, "三体 (2023) 4K 60帧", "电视剧", "share", "/媒体库/电视剧/三体", 62.4, "success", 0, 0, 86400 + 800),
        (2, "鬼灭之刃 柱训练篇", "动漫", "share", "/媒体库/动漫/鬼灭之刃", 21.0, "success", 0, 0, 86400 + 900),
        (1, "航拍中国 第四季", "纪录片", "share", "/媒体库/纪录片", 32.5, "success", 0, 0, 86400 + 1000),
    ]

    for rid, title, cat, kind, tdir, sz_gb, st, inc, prio, offset in records:
        sz_bytes = int(sz_gb * (1024**3))
        db.execute(
            "INSERT INTO transfers (id, share_code, title, category, kind, target_dir, size, "
            "status, increment, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, f"code_{rid}", title, cat, kind, tdir, sz_bytes, st, inc, prio, now - offset),
        )

    # 实时日志种子
    logstream.info("system", "系统启动完成：4 个监控频道在线，115 防风控串行队列已就绪")
    logstream.info("polling", "已抓取 @movie_4k_hub 最新 6 条消息，命中 2 条有效资源并入队")
    logstream.info("transfer", "转存成功：阿凡达：水之道 (2022) 2160p 杜比视界 -> /媒体库/电影 (38.40 GB)")
    logstream.info("polling", "已抓取 @guoman_115 最新消息：凡人修仙传 第126集 加入追更队列")
    logstream.info("transfer", "云下载已完成：地球脉动 第三季 全8集 4K HDR -> /云下载/纪录片 (46.20 GB)")
    logstream.info("auto_move", "定时文件整理门闸完成本轮归档：移动 3 个正片视频到季目录，耗时 4.2s")


def mock_pan():
    total = 20 * (1024**4)
    use = int(7.42 * (1024**4))
    free = total - use
    pan.check_login = lambda strict=False: {"ok": True, "name": "测试账号", "nickname": "115VIP"}
    pan.space_info = lambda: {
        "total": total,
        "use": use,
        "free": free,
        "ratio": 37.1,
    }
    # 阻止工作器在截图期间真实尝试处理模拟任务
    queue_worker.start = lambda: None
    queue_worker.restart_worker_task = lambda: asyncio.create_task(asyncio.sleep(3600))
    queue_worker._process_one = lambda job: None
    queue_worker.is_running = lambda: True


async def capture_cdp(port: int, token: str, output_path: Path):
    import urllib.request
    import websockets

    # 1. 查找 Chrome 调试目标页
    json_url = f"http://127.0.0.1:{port}/json/new?http://127.0.0.1:8000/"
    req = urllib.request.Request(json_url, method="PUT")
    with urllib.request.urlopen(req) as resp:
        target = json.loads(resp.read().decode())
    ws_url = target["webSocketDebuggerUrl"]

    async with websockets.connect(ws_url) as ws:
        msg_id = 0

        async def send_cmd(method, params=None):
            nonlocal msg_id
            msg_id += 1
            await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            while True:
                resp = json.loads(await ws.recv())
                if resp.get("id") == msg_id:
                    return resp.get("result", {})

        await send_cmd("Page.enable")
        await send_cmd("Network.enable")
        await send_cmd("Runtime.enable")

        # 注入 HttpOnly 登录 Token
        await send_cmd("Network.setCookie", {
            "name": "token",
            "value": token,
            "domain": "127.0.0.1",
            "path": "/",
        })

        # 设置 1440 x 1640 视图（标准桌面视口，完整容纳实时日志窗口）
        await send_cmd("Emulation.setDeviceMetricsOverride", {
            "width": 1440,
            "height": 1640,
            "deviceScaleFactor": 1,
            "mobile": False,
        })

        # 打开页面
        await send_cmd("Page.navigate", {"url": "http://127.0.0.1:8000/"})
        await asyncio.sleep(1.5)

        # 在前端上下文执行标准登录流程
        login_script = """
        (async () => {
            const resp = await fetch('/api/auth/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username: 'admin', password: 'Admin123456!'})
            });
            if (resp.ok) {
                localStorage.setItem('logged_in', '1');
                window.location.hash = '#/dashboard';
            }
            return resp.ok;
        })()
        """
        eval_res = await send_cmd("Runtime.evaluate", {
            "expression": login_script,
            "awaitPromise": True,
            "returnByValue": True,
        })
        print(f"登录结果: {eval_res}")

        # 等待页面渲染（DOM、ECharts 环形图及实时日志加载完毕）
        await asyncio.sleep(4.5)

        # 截图
        res = await send_cmd("Page.captureScreenshot", {
            "format": "png",
            "quality": 100,
            "fromSurface": True,
        })
        img_bytes = base64.b64decode(res["data"])
        output_path.write_bytes(img_bytes)
        print(f"✅ 截图保存成功: {output_path} ({len(img_bytes)} 字节)")


def main():
    seed_data()
    mock_pan()
    token = auth.make_token("admin")

    import uvicorn
    from backend.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning")
    server = uvicorn.Server(config)

    import threading
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(1.5)

    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    cdp_port = 9333
    chrome_proc = subprocess.Popen([
        chrome_path,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={TEMP_DIR / 'chrome_prof'}",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.0)

    try:
        output_preview = ROOT_DIR / "docs" / "preview.png"
        asyncio.run(capture_cdp(cdp_port, token, output_preview))
    finally:
        chrome_proc.terminate()
        try:
            chrome_proc.wait(timeout=3)
        except Exception:
            chrome_proc.kill()
        server.should_exit = True
        shutil.rmtree(TEMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
