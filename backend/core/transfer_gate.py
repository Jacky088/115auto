"""转存与整理/刮削互斥协调门闸（Transfer Gate）：
实现排空 + 四向互斥独占（auto_move / organize / scrape 不能同时运行）：
1. 排空阶段 (DRAINING)：记下进入时刻的转存水线 cutoff_id，停止接收新补全任务，
   让转存工作器与云下载核对消化 cutoff_id 之前已入队的全部在途任务直到终态；
   新进 TG 消息可安全排队在 cutoff_id 之后等待恢复，不再无限饿死排空。
2. 独占阶段 (ORGANIZING)：_active_mode 锁定为 'auto_move' | 'organize' | 'scrape' 之一，
   转存/云下载工作器完全挂起，各独占模块严格 FIFO 串行访问 115，防止并发风控与冲突；
3. 恢复阶段 (IDLE)：释放独占锁与工作器门闸，恢复转存与云下载消费。
"""
import asyncio
import threading
import time

from . import logstream
from .. import database as db

_MODE_LABELS = {
    "none": "空闲",
    "auto_move": "自动分流归档",
    "organize": "目录整理",
    "scrape": "媒体刮削",
}

# 运行状态：'idle' | 'draining' | 'organizing'
_state: str = "idle"
# 独占模式：'none' | 'auto_move' | 'organize' | 'scrape'
_active_mode: str = "none"
_cutoff_id: int | None = None
_cancel_requested: bool = False
_transfer_active: bool = False
_mode_lock = threading.Lock()
_media_progress: dict = {}

# 事件：
# _intake_event: 控制新任务接收（追剧补全等）。开闸 set()，关闸 clear()
_intake_event: asyncio.Event | None = None

# _worker_event: 控制转存工作器与云下载核对领取任务。开闸 set()，关闸 clear()
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


def get_active_mode() -> str:
    """获取当前占用的独占模块：none | auto_move | organize | scrape"""
    return _active_mode


def is_auto_move_running() -> bool:
    """兼容旧接口：auto_move 整理处于执行期（含 legacy 未标注模式）时返回 True；全域互斥请使用 `is_exclusive_busy()`。"""
    return _state == "organizing" and _active_mode in ("auto_move", "none")


def is_organize_running() -> bool:
    """目录整理是否处于独占占用期（含排空中）。"""
    return _active_mode == "organize" and _state in ("draining", "organizing")


def is_scrape_running() -> bool:
    """媒体刮削是否处于独占占用期（含排空中）。"""
    return _active_mode == "scrape" and _state in ("draining", "organizing")


def is_exclusive_busy() -> bool:
    """是否有任一独占任务（auto_move / organize / scrape）占用或正在排空。"""
    return _active_mode != "none" or _state != "idle"


def request_cancel() -> None:
    """请求温和中止当前正在运行的整理/刮削批次（处理完当前条目即退出并归还门闸）。"""
    global _cancel_requested
    _cancel_requested = True


def clear_cancel() -> None:
    """清除中止请求标志。"""
    global _cancel_requested
    _cancel_requested = False


def is_cancel_requested() -> bool:
    """是否已被手动请求停止或系统总开关已关闭。"""
    if _cancel_requested:
        return True
    return str(db.get_setting("running", False)).lower() != "true"


def set_media_progress(info: dict | None) -> None:
    """更新当前整理/刮削执行的实时进度摘要。"""
    global _media_progress
    _media_progress = dict(info) if info else {}


def try_begin_exclusive(mode: str) -> tuple[bool, str]:
    """原子申请占用独占执行权（auto_move | organize | scrape）。

    强制约束：
    1. 顶部全局系统运行开关必须为开启；
    2. 任一时刻仅允许一个独占模块运行（整理与刮削、自动分流彼此完全互斥）。
    """
    global _active_mode, _state, _cancel_requested, _media_progress
    if mode not in ("auto_move", "organize", "scrape"):
        return False, f"无效的独占模式: {mode}"
    if str(db.get_setting("running", False)).lower() != "true":
        return False, "系统处于停止状态，请先点击顶栏「运行」启动系统"
    with _mode_lock:
        if _active_mode != "none" or _state != "idle":
            cur = _MODE_LABELS.get(_active_mode, _active_mode)
            return False, f"当前「{cur}」正在排队或执行中，同一时刻仅允许运行一个整理/刮削模块"
        _active_mode = mode
        _state = "draining"
        _cancel_requested = False
        _media_progress = {"mode": mode, "phase": "draining", "started_at": time.time()}
        return True, ""


def release_exclusive(mode: str | None = None) -> None:
    """释放独占占用（异常兜底时使用）。"""
    if mode is None or _active_mode == mode:
        resume_all()


def _reset_gate_on_drain_abort() -> None:
    """排空阶段收到取消信号时将门闸复位为空闲（不触碰工作器事件，保持转存继续运行）。"""
    global _state, _active_mode, _cutoff_id
    _state = "idle"
    _active_mode = "none"
    _cutoff_id = 0
    _media_progress.pop("drain_started_at", None)


def get_exclusive_status() -> dict:
    """返回完整的门闸、模式与排空水线状态（供管理面板实时展示）。"""
    busy = is_exclusive_busy()
    drain_stat = get_drain_status()
    mp = dict(_media_progress)
    drain_started_at = mp.pop("drain_started_at", None)
    elapsed_sec = int(max(0, time.time() - drain_started_at)) if (_state == "draining" and drain_started_at) else 0
    return {
        "active_mode": _active_mode,
        "mode_label": _MODE_LABELS.get(_active_mode, "空闲"),
        "gate_state": _state,
        "busy": busy,
        "exclusive_busy": busy,
        "organize_running": (_active_mode == "organize" and busy),
        "scrape_running": (_active_mode == "scrape" and busy),
        "auto_move_running": (_active_mode == "auto_move" and busy),
        "cancel_requested": _cancel_requested,
        "cutoff_id": _cutoff_id,
        "progress": mp,
        "media_progress": mp,
        "drain_status": drain_stat,
        "drain_progress": {
            "remaining_total": int(drain_stat.get("unfinished", 0)),
            "elapsed_sec": elapsed_sec,
            **drain_stat,
        },
    }


def is_draining() -> bool:
    """当前是否处于排空等待阶段（等待现有转存队列处理完毕）。"""
    return _state == "draining"


def is_transfer_active() -> bool:
    """当前是否有单笔转存任务正在调用 115 API。"""
    return _transfer_active


def _norm_hhmm(raw: str, fallback: str) -> str:
    """把用户输入的窗口时间规范为补零 HH:MM（「9:00」→「09:00」），
    与 now 的 %H:%M 字符串比较口径一致；无法解析时回退默认值。"""
    s = str(raw or "").strip()
    try:
        hh, mm = s.split(":")
        return f"{int(hh):02d}:{int(mm):02d}"
    except (ValueError, AttributeError):
        return fallback


def is_in_schedule_window() -> bool:
    """115 防风控定时计划判断：
    未开启时放行全天候运行；开启后判定当前时钟是否处于运行窗口（支持跨零点，如 22:00 ~ 08:00）。
    若 start == end 则视为全天放行。
    """
    if not db.get_setting("schedule_enabled", False):
        return True
    from datetime import datetime
    now = datetime.now().strftime("%H:%M")
    # 入口统一补零规范化，避免 "9:00" 与 "09:30" 的字符串比较错位（'9' > '0'）
    start = _norm_hhmm(db.get_setting("schedule_start", "22:00") or "22:00", "22:00")
    end = _norm_hhmm(db.get_setting("schedule_end", "08:00") or "08:00", "08:00")
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
    """获取当前排空与未完成任务的详细明细（包括转存中、云下载中、重试倒计时中）。

    排空期若已锁定番号水线 _cutoff_id，queued / retrying 只统计水线之前的任务，
    水线之后刚由 TG 采集入库的新任务留在队列里等待整理/刮削完毕后再继续。
    """
    cutoff = _cutoff_id
    if cutoff is not None and _state in ("draining", "organizing"):
        row = db.query("""
            SELECT
                SUM(CASE WHEN status='queued' AND (attempts IS NULL OR attempts=0) AND id<=? THEN 1 ELSE 0 END) as queued_count,
                SUM(CASE WHEN status='queued' AND attempts>0 AND id<=? THEN 1 ELSE 0 END) as retry_count,
                SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) as running_count,
                SUM(CASE WHEN status='submitted' THEN 1 ELSE 0 END) as submitted_count
            FROM transfers WHERE status IN ('queued', 'running', 'submitted')
        """, (cutoff, cutoff), one=True)
    else:
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
        "active_mode": _active_mode,
        "cutoff_id": cutoff,
        "unfinished": q + ret + run + sub,
        "queued": q,
        "retrying": ret,
        "running": run,
        "submitted": sub,
    }


async def prepare_and_drain_transfers(max_wait_seconds: float = 3600.0, mode: str = "auto_move"):
    """文件整理/刮削开始前的排空与独占协调：
    1. 记录水线 cutoff_id 并关停 intake 门闸；
    2. 转存工作器与云下载核对循环保持运转，消化 cutoff_id 之前已入队的 queued/running/submitted 任务；
    3. 队列全部到达终态后，关停转存工作器（worker 门闸），进入独占状态。
    """
    global _state, _active_mode, _cutoff_id
    intake = _get_intake_event()
    intake.clear()  # 停止接收新补全任务
    with _mode_lock:
        if _active_mode == "none":
            _active_mode = mode
        _state = "draining"
        row = db.query("SELECT COALESCE(MAX(id), 0) m FROM transfers", one=True)
        _cutoff_id = int(row["m"] or 0) if row else 0

    mode_name = _MODE_LABELS.get(_active_mode, "文件整理")
    log_mod = _active_mode if _active_mode in ("organize", "scrape") else "auto_move"
    start_time = time.time()
    last_log_time = 0.0

    def _drain_snapshot():
        stat = get_drain_status()
        # 与 queue_worker 口径一致：daily_limit<=0 按 1 处理（旧的 `0 or 50` 写法会把 0 当成 50）
        limit = max(1, int(float(db.get_setting("daily_limit", 50))))
        quota_exhausted = (limit > 0 and db.quota_today() >= limit)
        running_flag = (str(db.get_setting("running", False)).lower() == "true")
        return stat, limit, quota_exhausted, running_flag

    started_with_running = await asyncio.to_thread(
        lambda: str(db.get_setting("running", False)).lower() == "true"
    )
    _media_progress["drain_started_at"] = start_time

    def _check_abort(running_flag: bool) -> bool:
        if _cancel_requested:
            return True
        if started_with_running and not running_flag:
            request_cancel()
            return True
        return False

    while True:
        drain_stat, limit, quota_exhausted, running_flag = await asyncio.to_thread(_drain_snapshot)
        if _check_abort(running_flag):
            logstream.info(log_mod, f"⏹ 收到中断/停止信号，已放弃本次{mode_name}排空等待（主转存队列保持运行）。")
            _reset_gate_on_drain_abort()
            return False

        unfinished = drain_stat["unfinished"]
        q, ret, run, sub = drain_stat["queued"], drain_stat["retrying"], drain_stat["running"], drain_stat["submitted"]

        if quota_exhausted:
            if run == 0 and sub == 0 and not _transfer_active:
                logstream.info(log_mod,
                               f"今日转存已达每日限额（{limit} 条），在途转存与云下载已全部落盘，开始{mode_name}。")
                break
        else:
            if unfinished == 0 and not _transfer_active:
                logstream.info(log_mod, f"📁 现有转存队列（水线 #{_cutoff_id}）已全部到达终态，开始独占{mode_name}。")
                break

        now = time.time()
        if now - start_time > max_wait_seconds:
            logstream.warn(log_mod, f"转存队列排空等待超时（>{int(max_wait_seconds)}s），按计划开始{mode_name}。")
            break

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
            logstream.info(log_mod, f"📁 等待现有转存/云下载队列终态：剩余 {detail_desc}，全部完成后将开启{mode_name}...")
            last_log_time = now

        await asyncio.sleep(min(0.5, max(0.05, max_wait_seconds / 4)))
        if _cancel_requested:
            logstream.info(log_mod, f"⏹ 收到中断信号，已立即退出{mode_name}排空等待（主转存队列保持运行）。")
            _reset_gate_on_drain_abort()
            return False

    _media_progress.pop("drain_started_at", None)
    # 排空完毕（或超时触发），关停转存工作器阻止领取新任务，等待在途单笔线程收尾后进入独占阶段
    worker = _get_worker_event()
    worker.clear()
    if _transfer_active:
        grace_limit = min(120.0, max(5.0, max_wait_seconds))
        grace_start = time.time()
        logstream.warn(log_mod, f"转存队列排空已达收口点，等待最后一笔在途转存线程退出后开始{mode_name}...")
        while _transfer_active and (time.time() - grace_start < grace_limit):
            await asyncio.sleep(0.5)
    _state = "organizing"
    set_media_progress({**_media_progress, "phase": "running", "exclusive_at": time.time()})
    logstream.info(log_mod, f"⏸️ 转存/云下载工作器已挂起，开始独占执行{mode_name}（严格队列串行，防 115 风控）。")


def resume_all():
    """独占任务完全结束后调用：释放独占模式锁，恢复转存工作器与新任务接收门闸。"""
    global _state, _active_mode, _cutoff_id, _cancel_requested, _media_progress
    log_mod = _active_mode if _active_mode in ("organize", "scrape") else "auto_move"
    with _mode_lock:
        _state = "idle"
        _active_mode = "none"
        _cutoff_id = None
        _cancel_requested = False
        _media_progress = {}
    worker = _get_worker_event()
    worker.set()
    intake = _get_intake_event()
    intake.set()
    logstream.info(log_mod, "▶️ 整理/刮削独占任务已结束，已恢复新任务接收与转存工作器。")


# 兼容旧函数签名
async def pause_transfers_for_auto_move(timeout: float = 120.0):
    await prepare_and_drain_transfers(max_wait_seconds=timeout, mode="auto_move")


def resume_transfers_after_auto_move():
    resume_all()


def reset_for_tests():
    """测试用重置函数。"""
    global _state, _active_mode, _cutoff_id, _cancel_requested, _transfer_active
    global _intake_event, _worker_event, _media_progress
    with _mode_lock:
        _state = "idle"
        _active_mode = "none"
        _cutoff_id = None
        _cancel_requested = False
        _transfer_active = False
        _media_progress = {}
    if _intake_event is not None:
        _intake_event.set()
    if _worker_event is not None:
        _worker_event.set()
    _intake_event = None
    _worker_event = None
