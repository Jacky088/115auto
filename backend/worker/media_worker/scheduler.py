"""目录整理与媒体刮削定时调度循环（间隔模式与每日固定时段，避让独占写门禁）。"""
from __future__ import annotations

import asyncio
import datetime
import json
import re
import time

from ... import database as db
from ...core import logstream, transfer_gate
from .common import is_system_running

def _mw():
    """延迟获取 media_worker 包门面，避免循环导入并保证测试 patch 透明生效。"""
    from .. import media_worker
    return media_worker


def compute_media_organize_next_runs() -> list[str]:
    """计算未来 48 小时（2 天）内预计的目录整理触发时间点列表。"""
    sched_type = str(db.get_setting("media_organize_schedule_type", "interval") or "interval")
    now = time.time()
    next_times = []

    if sched_type == "fixed_times":
        fixed_times_raw = db.get_setting("media_organize_fixed_times", '["06:00", "12:00", "18:00"]')
        try:
            if isinstance(fixed_times_raw, str):
                fixed_times = json.loads(fixed_times_raw)
            elif isinstance(fixed_times_raw, list):
                fixed_times = fixed_times_raw
            else:
                fixed_times = []
        except Exception:
            fixed_times = []

        valid_times = []
        for item in fixed_times:
            m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", str(item).strip())
            if m:
                valid_times.append(f"{int(m.group(1)):02d}:{int(m.group(2)):02d}")
        valid_times = sorted(set(valid_times))
        if not valid_times:
            return []

        today = datetime.date.today()
        for day_offset in range(4):
            d = today + datetime.timedelta(days=day_offset)
            d_str = d.strftime("%Y-%m-%d")
            for t_str in valid_times:
                dt_str = f"{d_str} {t_str}"
                try:
                    t_struct = time.strptime(dt_str, "%Y-%m-%d %H:%M")
                    ts = time.mktime(t_struct)
                    if now < ts <= now + 48 * 3600:
                        next_times.append(ts)
                except Exception:
                    pass
    else:
        interval_hours = max(float(db.get_setting("media_organize_schedule_hours", 12) or 12), 1.0)
        interval_sec = interval_hours * 3600
        last_run = float(db.get_setting("media_organize_last_run", 0) or 0)

        if last_run <= 0:
            base_t = now
        else:
            base_t = last_run
            while base_t + interval_sec <= now:
                base_t += interval_sec

        cur_t = base_t + interval_sec if base_t <= now else base_t
        while cur_t <= now + 48 * 3600:
            next_times.append(cur_t)
            cur_t += interval_sec

    next_times.sort()

    results = []
    today_str = time.strftime("%Y-%m-%d")
    tomorrow_str = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    day_after_str = (datetime.date.today() + datetime.timedelta(days=2)).strftime("%Y-%m-%d")

    for ts in next_times:
        d_part = time.strftime("%Y-%m-%d", time.localtime(ts))
        hm_part = time.strftime("%H:%M", time.localtime(ts))
        if d_part == today_str:
            prefix = "今天"
        elif d_part == tomorrow_str:
            prefix = "明天"
        elif d_part == day_after_str:
            prefix = "后天"
        else:
            prefix = time.strftime("%m月%d日", time.localtime(ts))
        results.append(f"{prefix} {hm_part}")
    return results


_last_media_fixed_trigger_minute = ""
_pending_scheduled_organize = False
_pending_scheduled_scrape = False

async def media_organize_loop():
    """后台定时自动整理与刮削调度循环：
    1. 支持分别开启「定时整理」与「定时刮削」；
    2. 到达触发时间时，记录排队意图（pending）；
    3. 严格按顺序排队：整理优先执行，刮削在整理完全结束且四方门禁空闲后才执行；
    4. 若遇到转存队列或其它独占任务在运行，持续等待其到达终点，绝不并发、绝不丢弃计划。
    """
    global _last_media_fixed_trigger_minute, _pending_scheduled_organize, _pending_scheduled_scrape
    logstream.info("organize", "定时整理与刮削调度协程已启动")
    while True:
        try:
            await asyncio.sleep(15)
            if not is_system_running():
                continue

            org_enabled = bool(db.get_setting("media_organize_schedule_enabled", False))
            scr_enabled = bool(db.get_setting("media_scrape_schedule_enabled", False))
            if not org_enabled and not scr_enabled:
                _pending_scheduled_organize = False
                _pending_scheduled_scrape = False
                continue

            if not transfer_gate.is_in_schedule_window():
                continue

            sched_type = str(db.get_setting("media_organize_schedule_type", "interval") or "interval")
            now = time.time()

            if sched_type == "fixed_times":
                fixed_times_raw = db.get_setting("media_organize_fixed_times", '["06:00", "12:00", "18:00"]')
                try:
                    if isinstance(fixed_times_raw, str):
                        fixed_times = json.loads(fixed_times_raw)
                    elif isinstance(fixed_times_raw, list):
                        fixed_times = fixed_times_raw
                    else:
                        fixed_times = []
                except Exception:
                    fixed_times = []

                cur_hm = time.strftime("%H:%M")
                cur_min_key = time.strftime("%Y-%m-%d %H:%M")
                if cur_hm in fixed_times and cur_min_key != _last_media_fixed_trigger_minute:
                    _last_media_fixed_trigger_minute = cur_min_key
                    if org_enabled:
                        _pending_scheduled_organize = True
                    if scr_enabled:
                        _pending_scheduled_scrape = True
            else:
                hours = max(1, min(168, int(db.get_setting("media_organize_schedule_hours", 12) or 12)))
                last_org = float(db.get_setting("media_organize_last_run", 0) or 0)
                if org_enabled and (now - last_org >= hours * 3600):
                    _pending_scheduled_organize = True
                last_scr = float(db.get_setting("media_scrape_last_run", 0) or 0)
                if scr_enabled and (now - last_scr >= hours * 3600):
                    _pending_scheduled_scrape = True

            # 严格排队与互斥执行：
            # 1. 优先执行「定时整理」
            if _pending_scheduled_organize:
                if transfer_gate.is_exclusive_busy():
                    # 门禁正忙（转存中、其它排空或独占中），保持排队等待
                    continue
                _pending_scheduled_organize = False
                logstream.info("organize", "⏰ 定时整理排队到达开启条件，开始执行...")
                await _mw().execute_organize_queue(trigger="schedule")
                continue

            # 2. 整理未排队或已完成，执行「定时刮削」
            if _pending_scheduled_scrape:
                if transfer_gate.is_exclusive_busy():
                    # 门禁正忙（整理中、转存中等），排队等待
                    continue
                _pending_scheduled_scrape = False
                logstream.info("scrape", "⏰ 定时刮削排队到达开启条件，开始执行...")
                await _mw().execute_scrape_queue(mode="scan_and_run")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logstream.warn("organize", f"定时整理与刮削调度检查异常: {e}")
            await asyncio.sleep(20)
