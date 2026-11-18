"""后台定时任务与通知门面：TG 推送队列、健康巡检、日志清理、云下载巡检、摘要备份与自动整理。

本包由原单体 `backend/worker/jobs.py` 拆分而来，按业务域划分为：
- `notify.py`：TG 异步推送队列、多渠道通知与转存/空间告警
- `health.py`：健康巡检、熔断停机与周期日志/记录清理
- `offline.py`：115 离线/云下载巡检与完成落库
- `digest_backup.py`：每日摘要与定时自动备份
- `auto_move.py`：定时文件整理调度、预览、执行与撤销

本 `__init__.py` 统一持有共享队列与门面重导出，确保业务调用与单测 100% 兼容。
"""
import asyncio  # noqa: F401
import datetime  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import queue
import re  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401
import uuid  # noqa: F401
from urllib.parse import unquote  # noqa: F401

import httpx  # noqa: F401  供测试直接 patch jobs.httpx.post / AsyncClient

from ... import database as db  # noqa: F401
from ...core import classifier, logstream, transfer_gate  # noqa: F401
from ...pan115 import client as pan  # noqa: F401
from .. import scan_index  # noqa: F401

# 线程安全的 FIFO 队列，防止多任务并发或定时任务集中触发 TG 429 频控或消息丢失
_tg_notify_queue: queue.Queue = queue.Queue(maxsize=1000)

from .auto_move import (  # noqa: E402, F401
    _auto_move_lock,
    _get_auto_move_lock,
    _last_auto_move_result,
    _undo_auto_move_sync,
    auto_move_loop,
    cloud_download_dirs,
    compute_next_runs,
    execute_auto_move,
    get_last_auto_move_result,
    preview_auto_move,
    undo_auto_move_records,
)
from .digest_backup import (  # noqa: E402, F401
    _backup_json,
    _bj_now,
    auto_backup_loop,
    digest_loop,
    digest_text,
)
from .health import (  # noqa: E402, F401
    DUP_RETENTION_SECONDS,
    HITS_RETENTION_SECONDS,
    LOG_RETENTION_SECONDS,
    MEDIA_TASK_RETENTION_SECONDS,
    MSG_RETENTION_SECONDS,
    POLL_FAIL_THRESHOLD,
    _cleanup_once,
    _polling_all_failed,
    _running_timeout_secs,
    _tg_health_ok,
    health_check_loop,
    log_cleanup_loop,
    stop_system,
)
from .notify import (  # noqa: E402, F401
    BIG_TRANSFER_SIZE,
    CHANNEL_EMPTY_HINT,
    CHANNEL_FNS,
    _fmt_size,
    _notify_bark,
    _notify_ntfy,
    _notify_pushplus,
    _notify_tg,
    _tg_proxy,
    check_space_alert,
    enqueue_tg_document,
    enqueue_tg_edit,
    enqueue_tg_message,
    extract_episode,
    notify,
    notify_detail,
    notify_failure,
    notify_transfer,
    send_test,
    test_channel,
    test_proxy,
    tg_notify_worker,
)
from .offline import (  # noqa: E402, F401
    OFFLINE_CONFIRM_WINDOW,
    _extract_btih,
    _finish_offline,
    _offline_key,
    offline_check_loop,
)
