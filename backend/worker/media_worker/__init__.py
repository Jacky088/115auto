"""目录整理与媒体刮削核心执行引擎门面（严格单队列串行、四方互斥门禁、Web↔TG 双向实时联动）。

本包由原单体 `backend/worker/media_worker.py` 拆分而来，按职责划分为：
- `common.py`：异常、配置、防风控节流与历史转存提示
- `scanner.py`：115 媒体树扫描、移动/重命名/字幕随动与空目录清理
- `reports.py`：Telegram 汇总汇报排版
- `organize.py`：目录整理预览、串行队列执行与操作账本撤销
- `scrape.py`：TMDB 刮削、暂存/提交/忽略原子状态机与 Web↔TG 实时同步
- `scheduler.py`：定时调度计算与后台循环

本 `__init__.py` 统一持有共享运行态并完整重导出所有既有符号，保证调用方与单测 100% 兼容。
"""
from __future__ import annotations

import asyncio

from .. import jobs, scan_index  # noqa: F401
from ... import database as db  # noqa: F401
from ...core import logstream, transfer_gate  # noqa: F401
from ...core.media_parser import (  # noqa: F401
    ParsedMedia,
    SUBTITLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    format_media_target,
    match_subtitle_for_video,
    parse_media_context,
    parse_media_filename,
    resolution_rank,
    sanitize_filename,
)
from ...core.tmdb_scraper import (  # noqa: F401
    compact_candidates,
    compute_local_meta_dir,
    decide_match,
    get_tmdb_detail,
    recall_and_score,
    remap_absolute_episode,
    save_local_metadata,
)
from ...pan115 import client  # noqa: F401
from ...pan115.client import normalize_title, strip_duplicate_suffix  # noqa: F401

_TASK_LOCKS: dict[str, asyncio.Lock] = {}
_ACTIVE_TASK_REF: dict[str, asyncio.Task | None] = {"organize": None, "scrape": None}
_COMMIT_WAKEUP_REQUESTED: bool = False

from .common import (  # noqa: E402, F401
    MediaCancelled,
    _RiskCircuitBreak,
    _check_not_cancelled,
    _get_lock,
    _interruptible_batch_rest,
    _is_risk_error,
    _load_db_transfer_hints,
    _match_hint_for_parsed,
    _pacing_sleep,
    _should_skip_extra,
    _split_rel_and_name,
    get_media_settings,
    is_system_running,
)
from .organize import (  # noqa: E402, F401
    _undo_records_sync,
    execute_organize_queue,
    preview_organize,
    undo_organized,
)
from .reports import (  # noqa: E402, F401
    _build_organize_tg_report,
    _build_scrape_tg_report,
    _format_media_detail_lines,
    _format_organize_detail_lines,
)
from .scanner import (  # noqa: E402, F401
    _apply_media_placement_sync,
    _clean_empty_dirs_sync,
    _move_and_rename_single_sync,
    _pick_unique_name,
    _scan_rules_sync,
)
from .scheduler import (  # noqa: E402, F401
    _last_media_fixed_trigger_minute,
    _pending_scheduled_organize,
    _pending_scheduled_scrape,
    compute_media_organize_next_runs,
    media_organize_loop,
)
from .scrape import (  # noqa: E402, F401
    STATUS_ZH,
    _finalize_scraped_task,
    _placed_to_report_item,
    commit_staged_tasks,
    execute_scrape_queue,
    ignore_task,
    schedule_commit_runner_if_idle,
    stage_task_selection,
    unstage_task_selection,
)
