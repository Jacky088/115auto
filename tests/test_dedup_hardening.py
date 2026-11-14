import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import os
import tempfile
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_"))
from backend.worker import queue_worker
from backend.pan115 import client as pan
from backend import database as db


@pytest.fixture(autouse=True)
def init_test_db():
    db.init()
    yield


def test_dedup_by_name_deep_files(monkeypatch):
    db.set_setting("dedup_by_name", True)
    # 模拟目标目录只有 1.mp4
    monkeypatch.setattr(queue_worker, "_get_dir_names_cached", lambda cid: {"1.mp4", "剧集文件夹"})

    # 1. 标题完全不同，但分享内包含 1.mp4 -> 必须被拦截为同名去重！
    snap_single = {
        "title": "全新的完全不同的标题 (2026)",
        "files": [{"fn": "1.mp4", "fid": "101"}],
        "top_names": []
    }
    reason = queue_worker._dedup_by_name(12345, "全新的完全不同的标题 (2026)", snap_single)
    assert "同名去重" in reason
    assert "1.mp4" in reason

    # 2. 分享包含顶层文件夹「剧集文件夹」 -> 必须被拦截为同名去重！
    snap_folder = {
        "title": "消息标题",
        "files": [{"fn": "E01.mp4", "fid": "102"}],
        "top_names": ["剧集文件夹"]
    }
    reason_folder = queue_worker._dedup_by_name(12345, "消息标题", snap_folder)
    assert "同名去重" in reason_folder
    assert "剧集文件夹" in reason_folder

    # 3. 分享内容是全新的文件 2.mp4 -> 放行
    snap_new = {
        "title": "新资源",
        "files": [{"fn": "2.mp4", "fid": "103"}],
        "top_names": []
    }
    reason_new = queue_worker._dedup_by_name(12345, "新资源", snap_new)
    assert reason_new == ""


def test_retry_preflight_network_error(monkeypatch):
    db.set_setting("dedup_by_name", True)

    def _raise_net_err(cid):
        raise ConnectionResetError("Connection reset by peer")

    monkeypatch.setattr(pan, "list_file_names", _raise_net_err)

    # 构造重试任务 (attempts=1)
    job = {
        "id": 99999,
        "share_code": "retryerr001",
        "receive_code": "ab12",
        "share_url": "https://115.com/s/retryerr001?password=ab12",
        "title": "测试网络抖动重试",
        "target_dir": "/测试目录",
        "channel": "test_ch",
        "message_id": 123,
        "attempts": 1,
        "bypass_filters": 1,
        "kind": "share"
    }

    monkeypatch.setattr(pan, "mkdirs", lambda p: 12345)
    monkeypatch.setattr(pan, "share_snap_all", lambda c, r: {
        "title": "测试", "files": [{"fn": "1.mp4", "fid": "1"}], "total_size": 1 << 30, "top_names": []
    })

    # 当重试预检遇到网络异常时，必须抛出 RuntimeError 触发稍后安全重试，绝不能盲目调用 receive
    recv_called = []
    monkeypatch.setattr(pan, "receive", lambda *args, **kwargs: recv_called.append(True))

    with pytest.raises(RuntimeError) as exc_info:
        queue_worker._process_one(job)

    assert "重试预检目标目录网络异常" in str(exc_info.value)
    assert len(recv_called) == 0, "网络异常时不应盲目调用 receive 接口！"
