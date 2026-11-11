"""转存与整理互斥协调门闸（Transfer Gate）：
实现三阶段门禁（Phase Gating System）：
1. 排空阶段 (DRAINING)：停止接收新任务（暂停 TG 轮询/入库），转存工作器继续消化现有队列直至清空（所有任务成功/失败）；
2. 整理阶段 (ORGANIZING)：独占执行文件移动，转存工作器完全挂起，确保 115 零并发；
3. 恢复阶段 (IDLE)：整理完毕，停止移动任务，恢复新任务接收与转存消费，循环往复。
"""
import asyncio
import time

from . import logstream
from .. import database as db

# 运行状态：'idle' | 'draining' | 'organizing'
_state: str = "idle"
_transfer_active: bool = False

# 事件：
# _intake_event: 控制新任务接收（TG 轮询 / 消息解析）。开闸 set()，关闸 clear()
_intake_event: asyncio.Event | None = None

# _worker_event: 控制转存工作器领取任务。开闸 set()，关闸 clear()
_worker_event: asyncio.Event | None = None


def _get_intake_event() -> asyncio.Event:
    global _intake_event
    if _intake_event is None:
        _intake_event = asyncio.Event()
        _intake_event.set()  # 默认开闸，允许接收新任务
    return _intake_event


def _get_worker_event() -> asyncio.Event:
    global _worker_event
    if _worker_event is None:
        _worker_event = asyncio.Event()
        _worker_event.set()  # 默认开闸，允许工作器运行
    return _worker_event


def get_gate_state() -> str:
    """获取当前门闸状态：idle | draining | organizing"""
    return _state


def is_auto_move_running() -> bool:
    """当前是否正在执行文件整理（整理移动中）。"""
    return _state == "organizing"


def is_draining() -> bool:
    """当前是否处于排空等待阶段（等待现有转存队列处理完毕）。"""
    return _state == "draining"


def is_transfer_active() -> bool:
    """当前是否有单笔转存任务正在调用 115 API。"""
    return _transfer_active


def is_in_schedule_window() -> bool:
    """115 防风控定时计划判断：
    未开启时放行全天候运行；开启后判定当前时钟是否处于运行窗口（支持跨零点，如 22:00 ~ 08:00）。
    若 start == end 则视为全天放行。
    """
    if not db.get_setting("schedule_enabled", False):
        return True
    from datetime import datetime
    now = datetime.now().strftime("%H:%M")
    start = str(db.get_setting("schedule_start", "22:00") or "22:00").strip()
    end = str(db.get_setting("schedule_end", "08:00") or "08:00").strip()
    if not start or not end or start == end:
        return True
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end  # 跨零点情况


def mark_transfer_active(active: bool):
    """转存工作器在开始/结束执行单笔任务时标记。"""
    global _transfer_active
    _transfer_active = bool(active)


def is_transfer_active() -> bool:
    """当前是否有单笔转存/云下载任务正在线程池中执行。"""
    return bool(_transfer_active)


async def wait_intake_allowed():
    """TG 轮询与 Userbot 抓取消息前调用：
    若处于排空或整理期，异步挂起等待整理完成；空闲期立即放行。
    """
    event = _get_intake_event()
    if not event.is_set():
        logstream.info("transfer", "📁 正在执行文件整理/排空，新消息与新转存任务暂停接收，等待整理结束...")
        await event.wait()
        logstream.info("transfer", "📁 文件整理已结束，新转存任务接收已恢复。")


async def wait_transfer_worker_allowed():
    """转存工作器每次领取任务前调用：
    若处于整理移动期，挂起等待整理结束；排空期和空闲期放行（排空期允许工作器消化现有队列）。
    """
    event = _get_worker_event()
    if not event.is_set():
        logstream.info("transfer", "📁 文件整理正在独占移动中，转存工作器已暂停，等待整理完成...")
        await event.wait()
        logstream.info("transfer", "📁 文件整理流程已结束，转存工作器自动恢复运行。")


def _get_unfinished_transfers_count() -> int:
    """查询当前未完成的转存与云下载任务数（queued / running / submitted）。"""
    row = db.query(
        "SELECT count(*) c FROM transfers WHERE status IN ('queued', 'running', 'submitted')",
        one=True
    )
    return int(row["c"] if row else 0)


def get_drain_status() -> dict:
    """获取当前排空与未完成任务的详细明细（包括转存中、云下载中、重试倒计时中）。"""
    row = db.query("""
        SELECT 
            SUM(CASE WHEN status='queued' AND (attempts IS NULL OR attempts=0) THEN 1 ELSE 0 END) as queued_count,
            SUM(CASE WHEN status='queued' AND attempts>0 THEN 1 ELSE 0 END) as retry_count,
            SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) as running_count,
            SUM(CASE WHEN status='submitted' THEN 1 ELSE 0 END) as submitted_count
        FROM transfers WHERE status IN ('queued', 'running', 'submitted')
    """, one=True)
    q = int(row["queued_count"] or 0) if row else 0
    ret = int(row["retry_count"] or 0) if row else 0
    run = int(row["running_count"] or 0) if row else 0
    sub = int(row["submitted_count"] or 0) if row else 0
    return {
        "gate_state": _state,
        "unfinished": q + ret + run + sub,
        "queued": q,
        "retrying": ret,
        "running": run,
        "submitted": sub,
    }


async def prepare_and_drain_transfers(max_wait_seconds: float = 3600.0):
    """文件整理开始前的排空与独占协调：
    1. 停止接收新转存任务（关停 intake 门闸）；
    2. 转存工作器与云下载核对循环保持运转，继续消化当前数据库中已有的 queued、running、submitted 任务；
    3. 铁律：所有任务必须走完终态（成功或重试次数用尽彻底失败），且云下载必须确认落盘，绝不在重试间隙偷跑；
    4. 队列全部到达终态后，关停转存工作器（worker 门闸），进入独占整理状态。
    """
    global _state
    intake = _get_intake_event()
    intake.clear()  # 停止接收新任务
    _state = "draining"

    start_time = time.time()
    last_log_time = 0.0

    def _drain_snapshot():
        stat = get_drain_status()
        limit = max(1, int(float(db.get_setting("daily_limit", 50) or 50)))
        quota_exhausted = (limit > 0 and db.quota_today() >= limit)
        return stat, limit, quota_exhausted

    while True:
        drain_stat, limit, quota_exhausted = await asyncio.to_thread(_drain_snapshot)
        unfinished = drain_stat["unfinished"]
        q, ret, run, sub = drain_stat["queued"], drain_stat["retrying"], drain_stat["running"], drain_stat["submitted"]

        # 每日额度检查：若今日额度已用尽，剩下的 queued 任务在明日零点前不会执行
        # 但已在执行的 running 和已提交的云下载 submitted 必须等其彻底结束落盘！
        if quota_exhausted:
            if run == 0 and sub == 0 and not _transfer_active:
                logstream.info("auto_move",
                               f"今日转存已达每日限额（{limit} 条），剩余排队任务挂起，且进行中的转存与云下载已全部落盘，开始开启文件整理。")
                break
        else:
            if unfinished == 0 and not _transfer_active:
                logstream.info("auto_move", "📁 现有转存队列已全部处理完毕（所有转存、云下载与重试均已达到终态），开始开启文件整理。")
                break

        # 铁律：如果是重试，必须等待重试完成，绝不能因为当前暂时没有活跃 running 线程就提前开启移动！

        now = time.time()
        # 超时保护（默认 60 分钟兜底，防止意外死锁）
        if now - start_time > max_wait_seconds:
            logstream.warn("auto_move", f"转存队列排空等待超时（>{int(max_wait_seconds)}s），按计划强制启动文件整理。")
            break

        # 定期输出进度日志
        if now - last_log_time >= 10.0:
            parts = []
            if run > 0:
                parts.append(f"{run} 个转存中")
            if sub > 0:
                parts.append(f"{sub} 个云下载中")
            if ret > 0:
                parts.append(f"{ret} 个重试等待中")
            if q > 0:
                parts.append(f"{q} 个排队中")
            detail_desc = "，".join(parts) if parts else f"{unfinished} 个任务"
            logstream.info("auto_move", f"📁 等待现有转存/云下载队列终态：剩余 {detail_desc}，全部完成后将开启整理...")
            last_log_time = now

        await asyncio.sleep(2)

    # 排空完毕（或超时触发），先关停转存工作器阻止领取新任务，若当前仍有在途线程则短暂等待收尾，再进入独占整理阶段
    worker = _get_worker_event()
    worker.clear()
    if _transfer_active:
        grace_limit = min(120.0, max(5.0, max_wait_seconds))
        grace_start = time.time()
        logstream.warn("auto_move", "转存队列排空已达收口点，等待最后一笔在途转存线程退出后开始整理...")
        while _transfer_active and (time.time() - grace_start < grace_limit):
            await asyncio.sleep(1)
    _state = "organizing"
    logstream.info("auto_move", "⏸️ 转存工作器已挂起，开始独占执行文件整理（零并发，防封风控最高保障）。")


def resume_all():
    """文件整理任务完全结束后调用：
    停止整理状态，恢复转存工作器与新任务接收门闸。
    """
    global _state
    _state = "idle"
    worker = _get_worker_event()
    worker.set()
    intake = _get_intake_event()
    intake.set()
    logstream.info("auto_move", "▶️ 文件整理任务全部结束，已恢复新任务接收与转存工作器。")


# 兼容旧函数签名
async def pause_transfers_for_auto_move(timeout: float = 120.0):
    await prepare_and_drain_transfers(max_wait_seconds=timeout)


def resume_transfers_after_auto_move():
    resume_all()


def reset_for_tests():
    """测试用重置函数。"""
    global _state, _transfer_active, _intake_event, _worker_event
    _state = "idle"
    _transfer_active = False
    if _intake_event is not None:
        _intake_event.set()
    if _worker_event is not None:
        _worker_event.set()
    _intake_event = None
    _worker_event = None
