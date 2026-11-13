import asyncio
import os
import queue
import time
import pytest
from fastapi.testclient import TestClient
import os
import tempfile
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_"))
from backend.main import app
from backend import auth, database as db
from backend.worker import jobs

def test_numerical_validation():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {auth.make_token('admin')}"}

    # 1. 测试 daily_limit 边界校验
    r = client.put("/api/settings", json={"daily_limit": 0}, headers=headers)
    assert r.status_code == 400
    assert "每日转存上限" in r.json()["detail"]

    r = client.put("/api/settings", json={"daily_limit": 51}, headers=headers)
    assert r.status_code == 400
    assert "每日转存上限" in r.json()["detail"]

    r = client.put("/api/settings", json={"daily_limit": "not_int"}, headers=headers)
    assert r.status_code == 400

    # 2. 测试 auto_backup_days 边界校验
    r = client.put("/api/settings", json={"auto_backup_days": 0}, headers=headers)
    assert r.status_code == 400
    assert "自动备份间隔" in r.json()["detail"]

    r = client.put("/api/settings", json={"auto_backup_days": 366}, headers=headers)
    assert r.status_code == 400
    assert "自动备份间隔" in r.json()["detail"]

    # 3. 测试 auto_backup_time 格式校验
    r = client.put("/api/settings", json={"auto_backup_time": "invalid_time"}, headers=headers)
    assert r.status_code == 400
    assert "HH:MM" in r.json()["detail"]

    r = client.put("/api/settings", json={"auto_backup_time": "04:30"}, headers=headers)
    assert r.status_code == 200
    assert db.get_setting("auto_backup_time") == "04:30"

    # 4. 测试 schedule_jitter_min
    r = client.put("/api/settings", json={"schedule_jitter_min": -1}, headers=headers)
    assert r.status_code == 400
    assert "开工随机扰动" in r.json()["detail"]

    r = client.put("/api/settings", json={"schedule_jitter_min": 31}, headers=headers)
    assert r.status_code == 400
    assert "开工随机扰动" in r.json()["detail"]

    # 5. 测试 batch_rest_count
    r = client.put("/api/settings", json={"batch_rest_count": 1}, headers=headers)
    assert r.status_code == 400
    assert "连存小憩笔数" in r.json()["detail"]

    r = client.put("/api/settings", json={"batch_rest_count": 21}, headers=headers)
    assert r.status_code == 400
    assert "连存小憩笔数" in r.json()["detail"]

    # 6. 测试 batch_rest_min_min 与 batch_rest_max_min 区间大小校验
    r = client.put("/api/settings", json={"batch_rest_min_min": 10, "batch_rest_max_min": 5}, headers=headers)
    assert r.status_code == 400
    assert "小憩时间区间有误" in r.json()["detail"]

    # 合法区间
    r = client.put("/api/settings", json={"batch_rest_min_min": 3, "batch_rest_max_min": 8}, headers=headers)
    assert r.status_code == 200

    # 7. 测试 min_size_gb
    r = client.put("/api/settings", json={"min_size_gb": -0.5}, headers=headers)
    assert r.status_code == 400
    assert "分享体积下限" in r.json()["detail"]

    # 8. 测试 rules priority
    r = client.put("/api/rules", json={"rules": [{"pattern": "test", "category": "电影", "priority": 0}]}, headers=headers)
    assert r.status_code == 400
    assert "优先级必须是大于等于 1 的整数" in r.json()["detail"]
    print("test_numerical_validation passed.")


def test_tg_notify_queue():
    # 测试加入队列
    db.set_setting("notify_token", "123456:ABC-DEF")
    db.set_setting("notify_chat_id", "987654321")

    # 清空可能残留的队列
    while not jobs._tg_notify_queue.empty():
        try:
            jobs._tg_notify_queue.get_nowait()
        except Exception:
            break

    # 加入一条文本消息
    assert jobs.enqueue_tg_message("测试消息 1") is True
    assert jobs._tg_notify_queue.qsize() == 1
    item = jobs._tg_notify_queue.get_nowait()
    assert item["type"] == "text"
    assert item["text"] == "测试消息 1"
    assert item["token"] == "123456:ABC-DEF"
    assert item["chat_id"] == "987654321"

    # 加入一份文档
    assert jobs.enqueue_tg_document("backup.json", b"{}", "application/json", "备份") is True
    assert jobs._tg_notify_queue.qsize() == 1
    item2 = jobs._tg_notify_queue.get_nowait()
    assert item2["type"] == "document"
    assert item2["fname"] == "backup.json"
    assert item2["caption"] == "备份"

    print("test_tg_notify_queue passed.")


if __name__ == "__main__":
    test_numerical_validation()
    test_tg_notify_queue()
    print("ALL TESTS IN test_validation_and_tg_queue.py PASSED SUCCESSFULLY!")
