"""转存/云下载终态门禁与文件整理灵活定时调度测试集"""
import asyncio
import os
import sys
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="test_gate_")

from backend import database as db
from backend.core import transfer_gate
from backend.worker import jobs


def test_compute_next_runs_interval():
    db.init()
    db.set_setting("auto_move_schedule_type", "interval")
    db.set_setting("auto_move_interval_hours", 6)
    db.set_setting("auto_move_last_run", time.time())

    runs = jobs.compute_next_runs()
    assert len(runs) >= 6, f"Expected at least 6 runs in 48 hours for 6h interval, got {len(runs)}"
    assert any("今天" in r or "明天" in r for r in runs)
    print("test_compute_next_runs_interval passed.")


def test_compute_next_runs_fixed_times():
    db.init()
    db.set_setting("auto_move_schedule_type", "fixed_times")
    db.set_setting("auto_move_fixed_times", '["06:00", "12:00", "18:00"]')

    runs = jobs.compute_next_runs()
    assert len(runs) >= 3, f"Expected at least 3 runs, got {len(runs)}"
    for r in runs:
        assert any(t in r for t in ("06:00", "12:00", "18:00"))
    print("test_compute_next_runs_fixed_times passed.")


def test_drain_status_with_submitted_and_retrying():
    db.init()
    db.execute("DELETE FROM transfers")

    # 1. 插入一个常规 queued 任务
    db.execute("""
        INSERT INTO transfers(kind, share_code, title, status, attempts, next_retry_at, created_at)
        VALUES('share', 'code1', 'Task 1', 'queued', 0, NULL, ?)
    """, (time.time(),))

    # 2. 插入一个正在重试倒计时中的任务（attempts=2, next_retry_at 为 3 分钟后）
    db.execute("""
        INSERT INTO transfers(kind, share_code, title, status, attempts, next_retry_at, created_at)
        VALUES('share', 'code2', 'Task 2 Retry', 'queued', 2, ?, ?)
    """, (time.time() + 180, time.time()))

    # 3. 插入一个正在转存中的任务
    db.execute("""
        INSERT INTO transfers(kind, share_code, title, status, attempts, next_retry_at, created_at)
        VALUES('share', 'code3', 'Task 3 Running', 'running', 1, NULL, ?)
    """, (time.time(),))

    # 4. 插入一个正在 115 云下载中的任务（submitted）
    db.execute("""
        INSERT INTO transfers(kind, share_code, title, status, attempts, next_retry_at, created_at)
        VALUES('offline', 'hash4', 'Task 4 Cloud Download', 'submitted', 0, NULL, ?)
    """, (time.time(),))

    # 5. 插入已达终态的任务（success, failed, skipped）
    db.execute("""
        INSERT INTO transfers(kind, share_code, title, status, attempts, next_retry_at, created_at)
        VALUES('share', 'code5', 'Task 5 Success', 'success', 0, NULL, ?)
    """, (time.time(),))
    db.execute("""
        INSERT INTO transfers(kind, share_code, title, status, attempts, next_retry_at, created_at)
        VALUES('share', 'code6', 'Task 6 Failed', 'failed', 5, NULL, ?)
    """, (time.time(),))

    status = transfer_gate.get_drain_status()
    assert status["unfinished"] == 4, f"Expected 4 unfinished, got {status['unfinished']}"
    assert status["queued"] == 1
    assert status["retrying"] == 1
    assert status["running"] == 1
    assert status["submitted"] == 1

    print("test_drain_status_with_submitted_and_retrying passed.")


def test_prepare_and_drain_waits_for_terminal_states():
    db.init()
    db.execute("DELETE FROM transfers")
    transfer_gate.reset_for_tests()

    # 初始插入一个 submitted 任务和一个 retrying 任务
    db.execute("""
        INSERT INTO transfers(kind, share_code, title, status, attempts, next_retry_at, created_at)
        VALUES('offline', 'hash1', 'Cloud Download In Progress', 'submitted', 0, NULL, ?)
    """, (time.time(),))
    db.execute("""
        INSERT INTO transfers(kind, share_code, title, status, attempts, next_retry_at, created_at)
        VALUES('share', 'code2', 'Retry Task', 'queued', 1, ?, ?)
    """, (time.time() + 2, time.time()))

    async def simulate_drain_progression():
        # 启动排空协程
        drain_task = asyncio.create_task(transfer_gate.prepare_and_drain_transfers(max_wait_seconds=10))

        await asyncio.sleep(0.5)
        # 此时应处于 draining 状态，门闸未开启整理
        assert transfer_gate.get_gate_state() == "draining"
        assert not transfer_gate.is_auto_move_running()

        # 模拟后台将 submitted 转为 success（云下载完成落盘）
        db.execute("UPDATE transfers SET status='success' WHERE kind='offline'")
        await asyncio.sleep(0.5)
        # 此时还有一个 retrying 任务，依然必须保持等待
        assert transfer_gate.get_gate_state() == "draining"

        # 模拟 retrying 任务执行完毕，转为终态 failed
        db.execute("UPDATE transfers SET status='failed' WHERE status='queued'")
        await asyncio.sleep(0.5)

        # 等待排空完成
        await drain_task
        # 排空完毕后，门闸进入 organizing 独占状态
        assert transfer_gate.get_gate_state() == "organizing"
        assert transfer_gate.is_auto_move_running()

        # 整理结束
        transfer_gate.resume_all()
        assert transfer_gate.get_gate_state() == "idle"

    asyncio.run(simulate_drain_progression())
    print("test_prepare_and_drain_waits_for_terminal_states passed.")


if __name__ == "__main__":
    test_compute_next_runs_interval()
    test_compute_next_runs_fixed_times()
    test_drain_status_with_submitted_and_retrying()
    test_prepare_and_drain_waits_for_terminal_states()
    print("ALL TESTS IN test_gate_and_schedule.py PASSED SUCCESSFULLY!")
