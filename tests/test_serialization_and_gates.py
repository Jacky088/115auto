"""全局 115 串行队列、门闸协调与事件循环非阻塞回归测试集"""
import asyncio
import os
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="autosave_test_serial_")

from backend import database as db  # noqa: E402
from backend.core import transfer_gate  # noqa: E402
from backend.pan115 import client as pan  # noqa: E402
from backend.tg import episode_backfill  # noqa: E402
from backend.worker import jobs  # noqa: E402


def test_pan_call_serializes_concurrent_threads_and_enforces_gap():
    db.init()
    pan._consec_risk = 0
    pan._last_api_at = -1000.0

    active = 0
    max_active = 0
    timestamps: list[tuple[float, float]] = []
    guard = threading.Lock()

    def slow_fake_115(idx: int):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
            start = time.monotonic()
        time.sleep(0.03)
        with guard:
            end = time.monotonic()
            timestamps.append((start, end))
            active -= 1
        return idx

    orig_gap = pan._MIN_API_GAP
    pan._MIN_API_GAP = 0.08
    try:
        threads = [threading.Thread(target=pan.pan_call, args=(slow_fake_115, i)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
    finally:
        pan._MIN_API_GAP = orig_gap

    assert len(timestamps) == 4
    assert max_active == 1, f"115 请求发生了并发执行（max_active={max_active}）"
    timestamps.sort(key=lambda x: x[0])
    for i in range(1, len(timestamps)):
        gap = timestamps[i][0] - timestamps[i - 1][1]
        assert gap >= 0.06, f"相邻两次 115 请求间隔过短（{gap:.3f}s < 0.06s）"


def test_pan_call_is_reentrant():
    db.init()

    def inner():
        return "ok"

    def outer():
        return pan.pan_call(inner)

    assert pan.pan_call(outer) == "ok"


def test_files_api_call_uses_pan_call():
    from backend.api import files

    called = []

    def fake_fs(arg):
        return {"state": True, "echo": arg}

    orig = pan.pan_call
    try:
        def spy(fn, *args, **kwargs):
            called.append(fn)
            return orig(fn, *args, **kwargs)

        pan.pan_call = spy
        res = files._call("测试", fake_fs, "hello")
        assert res["echo"] == "hello"
        assert fake_fs in called
    finally:
        pan.pan_call = orig


def test_drain_timeout_waits_for_active_transfer_before_organizing():
    db.init()
    db.execute("DELETE FROM transfers")
    transfer_gate.reset_for_tests()

    # 插入一条滞留 queued 任务触发超时分支，同时标记已有在途转存线程运行中
    db.execute(
        "INSERT INTO transfers(kind, share_code, title, status, created_at) VALUES('share', 'stuck1', 'Stuck', 'queued', ?)",
        (time.time(),),
    )
    transfer_gate.mark_transfer_active(True)

    async def scenario():
        drain_task = asyncio.create_task(transfer_gate.prepare_and_drain_transfers(max_wait_seconds=0.3))
        await asyncio.sleep(0.6)
        # 已超 0.3s 且仍在等待在途线程收尾：必须尚未进入 organizing
        assert transfer_gate.get_gate_state() != "organizing"

        # 模拟在途线程安全退出
        transfer_gate.mark_transfer_active(False)
        await drain_task
        assert transfer_gate.get_gate_state() == "organizing"
        transfer_gate.resume_all()
        assert transfer_gate.get_gate_state() == "idle"

    asyncio.run(scenario())


def test_episode_backfill_waits_for_intake_gate():
    db.init()
    transfer_gate.reset_for_tests()

    processed: list[str] = []

    async def fake_process_request(req):
        processed.append(req["base"])

    async def scenario():
        intake = transfer_gate._get_intake_event()
        intake.clear()  # 模拟处于排空/整理阶段

        episode_backfill._queue = asyncio.Queue()
        db.set_setting(episode_backfill._PENDING_KEY, [])
        await episode_backfill._queue.put({
            "channel": "ch1",
            "message_id": 100,
            "title": "Test Show S01E05",
            "base": "TestShow",
            "season": 1,
            "ep": 5,
            "anchor_category": "电视剧",
            "anchor_target": "/媒体库/电视剧",
        })

        with patch.object(episode_backfill, "_process_request", side_effect=fake_process_request):
            worker = asyncio.create_task(episode_backfill.episode_backfill_loop())
            try:
                await asyncio.sleep(0.3)
                assert processed == [], f"整理/排空期间不应提前消费追剧补全队列: {processed}"

                transfer_gate.resume_all()
                await asyncio.sleep(0.3)
                assert processed == ["TestShow"]
            finally:
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass

    asyncio.run(scenario())


def test_offline_check_respects_organizing_and_dedups_fallback_dir():
    db.init()
    db.execute("DELETE FROM transfers")
    transfer_gate.reset_for_tests()

    now = time.time()
    for i in range(3):
        db.execute(
            "INSERT INTO transfers(kind, share_code, share_url, title, target_dir, status, created_at)"
            " VALUES('offline', ?, ?, ?, '/云下载', 'submitted', ?)",
            (f"off_{i}", f"ed2k://|file|Movie{i}.mkv|1000|{'a'*32}|/", f"电影{i}", now - 60),
        )

    async def scenario():
        # 1. 当整理处于 organizing 状态时，offline_check_loop 挂起且不请求 115
        transfer_gate._get_worker_event().clear()
        transfer_gate._state = "organizing"

        task_list_calls = 0

        def fake_task_list_all():
            nonlocal task_list_calls
            task_list_calls += 1
            return []

        list_entries_calls = 0

        def fake_list_entries(cid, limit=500):
            nonlocal list_entries_calls
            list_entries_calls += 1
            return []

        with patch.object(pan, "task_list_all", side_effect=fake_task_list_all), \
             patch.object(pan, "mkdirs", return_value=777), \
             patch.object(pan, "list_entries", side_effect=fake_list_entries):
            loop_task = asyncio.create_task(jobs.offline_check_loop())
            try:
                await asyncio.sleep(0.25)
                assert task_list_calls == 0, "organizing 阶段不应请求 115 云下载列表"

                # 2. 整理恢复空闲后，3 个共享同一目标目录的未命中任务在本轮只扫描 1 次目录
                transfer_gate.resume_all()
                await asyncio.sleep(0.4)
                assert task_list_calls >= 1
                assert list_entries_calls == 1, f"同轮共享同一目标目录应缓存去重（实际扫描 {list_entries_calls} 次）"
            finally:
                loop_task.cancel()
                try:
                    await loop_task
                except asyncio.CancelledError:
                    pass

    asyncio.run(scenario())


def test_digest_loop_does_not_block_event_loop():
    db.init()
    db.set_setting("digest_enabled", True)
    db.set_setting("digest_time", "00:00")
    db.set_setting("digest_last_sent", "")

    entered = threading.Event()
    release = threading.Event()

    def slow_digest_text():
        entered.set()
        release.wait(timeout=3)
        return "📊 测试摘要"

    async def scenario():
        with patch.object(jobs, "digest_text", side_effect=slow_digest_text), \
             patch.object(jobs, "notify_detail", return_value={"tg": True}):
            t = asyncio.create_task(jobs.digest_loop())
            try:
                # 等 digest_loop 进入同步慢函数
                for _ in range(50):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.02)
                assert entered.is_set(), "digest_loop 未按预期触发 digest_text"

                # 验证主事件循环在此期间完全不阻塞，其他协程能立刻响应
                t0 = time.monotonic()
                await asyncio.sleep(0.05)
                elapsed = time.monotonic() - t0
                assert elapsed < 0.25, f"主事件循环疑似被 digest_text 阻塞（耗时 {elapsed:.2f}s）"
            finally:
                release.set()
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    asyncio.run(scenario())


def test_lightweight_ui_and_read_calls_bypass_pan_call_lock():
    """网盘只读浏览/搜索、Cookie 检测、空间查询不得被后台 _api_lock 长任务卡死。"""
    db.init()
    from backend.api import files

    class _Fake115:
        def user_my_info(self):
            return {"state": True, "data": {"user_name": "tester", "user_id": 1}}

        def user_space_info(self):
            return {"state": True, "data": {
                "all_total": {"size": 1000},
                "all_use": {"size": 250},
                "all_remain": {"size": 750},
            }}

    lock_entered = threading.Event()
    release_lock = threading.Event()
    pan_called = []
    orig_pan_call = pan.pan_call

    def slow_holder():
        lock_entered.set()
        release_lock.wait(timeout=5)
        return "done"

    holder = threading.Thread(target=lambda: orig_pan_call(slow_holder), daemon=True)
    holder.start()
    assert lock_entered.wait(timeout=2), "后台线程未持有 _api_lock"

    try:
        def spy_pan_call(fn, *args, **kwargs):
            pan_called.append(fn)
            return orig_pan_call(fn, *args, **kwargs)

        with patch.object(pan, "pan_call", side_effect=spy_pan_call), \
             patch.object(pan, "_get_client", return_value=_Fake115()):
            t0 = time.monotonic()
            fs_res = files._call_read("读取目录", lambda p: {"state": True, "data": [], "q": p}, {"cid": 0})
            st = pan.check_login(force=True)
            sp = pan.space_info(force=True)
            elapsed = time.monotonic() - t0

        assert fs_res["state"] is True
        assert st["ok"] is True and st["name"] == "tester"
        assert sp["total"] == 1000 and sp["use"] == 250 and sp["free"] == 750
        assert pan_called == [], f"轻量只读接口不应进入 pan_call 队列: {pan_called}"
        assert elapsed < 1.0, f"轻量只读接口耗时过长（{elapsed:.2f}s），疑被后台锁阻塞"
    finally:
        release_lock.set()
        holder.join(timeout=2)


def test_polling_loop_not_blocked_by_intake_gate():
    """TG 网页轮询只抓取消息写本地队列，即使处于整理/排空门闸也不能被卡住。"""
    db.init()
    db.execute("DELETE FROM channels")
    transfer_gate.reset_for_tests()
    db.set_setting("running", True)
    db.execute(
        "INSERT INTO channels(type, name, enabled, created_at) VALUES('polling', 'demo_ch', 1, ?)",
        (time.time(),),
    )

    from backend.tg import polling

    polled = asyncio.Event()
    seen: list[str] = []

    async def fake_poll_one(_client, ch):
        seen.append(ch["name"])
        polled.set()

    async def scenario():
        intake = transfer_gate._get_intake_event()
        intake.clear()  # 模拟自动整理排空期间
        with patch.object(polling, "_poll_one", side_effect=fake_poll_one):
            task = asyncio.create_task(polling.polling_loop())
            try:
                await asyncio.wait_for(polled.wait(), timeout=1.5)
                assert seen == ["demo_ch"]
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    asyncio.run(scenario())


def test_run_pan_thread_does_not_starve_default_to_thread_pool():
    """专用 115 线程池占满阻塞时，默认 asyncio.to_thread（TG/DB）仍可立即响应。"""
    max_w = pan._pan_executor._max_workers
    started = 0
    started_guard = threading.Lock()
    all_blocked = threading.Event()
    release = threading.Event()

    def block_pan():
        nonlocal started
        with started_guard:
            started += 1
            if started >= max_w:
                all_blocked.set()
        release.wait(timeout=5)
        return "ok"

    async def scenario():
        tasks = [asyncio.create_task(pan.run_pan_thread(block_pan)) for _ in range(max_w + 2)]
        try:
            for _ in range(50):
                if all_blocked.is_set():
                    break
                await asyncio.sleep(0.02)
            assert all_blocked.is_set(), "_pan_executor 未被占满"

            t0 = time.monotonic()
            val = await asyncio.wait_for(asyncio.to_thread(lambda: "db_ok"), timeout=0.5)
            elapsed = time.monotonic() - t0
            assert val == "db_ok"
            assert elapsed < 0.4, f"默认线程池疑似被 115 任务饿死（{elapsed:.2f}s）"
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())
