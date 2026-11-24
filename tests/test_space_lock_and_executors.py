"""空间查询等锁超时与外部网络线程池的回归测试。

覆盖两处修复，防止后续重构回退：

1. `space_info` 在锁被长期占用时**不得无限阻塞**：缓存为空时必须限时抛错，
   由调用方（空间门闸 / 预检 / 仪表盘 / doctor）走既有降级分支；
2. 外部网络（TMDB / WebDAV / 通知）必须使用与 115 专用池**隔离**的线程池，
   避免分钟级外部请求挤占 115 请求与 TG 频道轮询的线程额度。
"""
import asyncio
import os
import tempfile
import time

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_lock_"))

from backend import database as db  # noqa: E402
from backend.core import executors  # noqa: E402
from backend.pan115 import client as pan  # noqa: E402
from backend.worker import queue_worker as qw  # noqa: E402

db.init()


def test_space_info_cache_hit_skips_lock(monkeypatch):
    """缓存有效时直接返回：不取锁、不发请求，即使锁正被另一个线程占用。"""
    monkeypatch.setattr(pan, "_space_cache", (
        time.time() + 300,
        {"total": 5, "use": 1, "free": 4, "ratio": 20.0},
    ))
    pan._space_call_lock.acquire()
    try:
        got = pan.space_info()
    finally:
        pan._space_call_lock.release()
    assert got["total"] == 5


def test_space_info_lock_wait_has_timeout(monkeypatch):
    """缓存为空且锁被占用时：限时抛 Pan115Error，而不是无限等待。"""
    monkeypatch.setattr(pan, "_space_cache", None)
    monkeypatch.setattr(pan, "_SPACE_LOCK_TIMEOUT", 0.2)
    pan._space_call_lock.acquire()
    t0 = time.time()
    try:
        with pytest.raises(pan.Pan115Error):
            pan.space_info()
    finally:
        pan._space_call_lock.release()
    assert time.time() - t0 < 3.0, "等锁超时后应立即抛错，不能无限阻塞"


def test_space_gate_degrades_on_probe_error(monkeypatch):
    """探测抛错时沿用上次空间状态，不把队列误判为空间充足。"""
    db.set_setting("space_guard_enabled", True)
    db.set_setting("space_blocked", True)
    db.set_setting("space_blocked_reason", "已用 95.0%，剩余 3.00 GB")
    # 强制本地探测缓存过期，进入真实探测分支
    qw._SPACE_PROBE["ts"] = 0.0

    def _boom(*args, **kwargs):
        raise pan.Pan115Error("115 容量查询排队超时，请稍后重试")

    monkeypatch.setattr(pan, "space_info", _boom)
    try:
        blocked, reason = qw._space_gate()
    finally:
        db.set_setting("space_blocked", False)
        db.set_setting("space_blocked_reason", "")
        qw._SPACE_PROBE["ts"] = 0.0
    assert blocked is True
    assert "95.0%" in reason


def test_run_ext_thread_executes():
    """外部网络专用池可正常执行阻塞调用并返回结果。"""
    assert asyncio.run(executors.run_ext_thread(lambda x: x + 1, 41)) == 42


def test_ext_pool_isolated_from_pan_pool():
    """外部网络池必须与 115 专用池是不同实例，避免互相挤占。"""
    assert executors._ext_executor is not pan._pan_executor
