"""外部网络阻塞任务的专用线程池。

默认 asyncio 线程池在小核容器上通常只有 5~6 个线程，一旦被 TMDB 刮削、
WebDAV 上传下载、通知渠道推送等「分钟级」网络调用占满，就会饿死
TG 频道轮询、Userbot 与本地 SQLite 读写（事件循环拿不到线程而整体卡顿）。

这里与 115 专用池（`pan115.client._pan_executor`）刻意隔离：
- 115 池：转存/云下载/目录遍历/移动等对 115 的调用，受风控节流约束；
- 外部池：TMDB / WebDAV / 通知 / 海报下载等第三方网络，互不挤占。
"""
from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor

# 外部网络并发不需要很高（且多数场景是串行刮削/单次上传），6 个足够
_ext_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="ext-net")


async def run_ext_thread(fn, *args, **kwargs):
    """在外部网络专用池中执行阻塞调用，避免占用默认 asyncio 池。"""
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(_ext_executor, functools.partial(fn, *args, **kwargs))
    return await loop.run_in_executor(_ext_executor, fn, *args)
