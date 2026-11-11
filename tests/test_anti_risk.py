"""115 防风控拟人化设置与功能测试集"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="autosave_test_risk_")

from backend import database as db
from backend.api import manage
from backend.worker import queue_worker


def test_anti_risk_settings_api():
    db.init()
    db.set_setting("schedule_jitter_min", 5)
    db.set_setting("batch_rest_enabled", True)
    db.set_setting("batch_rest_count", 3)
    db.set_setting("batch_rest_min_min", 2)
    db.set_setting("batch_rest_max_min", 5)
    db.set_setting("health_check_interval_min", 10)
    db.set_setting("dedup_cache_ttl_sec", 90)

    # 1. 获取默认设置，包含所有拟人化防风控新字段
    s = manage.get_settings()
    assert s["schedule_jitter_min"] == 5
    assert s["batch_rest_enabled"] is True
    assert s["batch_rest_count"] == 3
    assert s["batch_rest_min_min"] == 2
    assert s["batch_rest_max_min"] == 5
    assert s["health_check_interval_min"] == 10
    assert s["dedup_cache_ttl_sec"] == 90

    # 2. 验证 min > max 必须报错阻断
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        manage.put_settings({
            "batch_rest_min_min": 6,
            "batch_rest_max_min": 3,
        })
    assert exc_info.value.status_code == 400
    assert "最短小憩时间" in exc_info.value.detail

    # 3. 合法更新设置
    manage.put_settings({
        "schedule_jitter_min": 10,
        "batch_rest_enabled": True,
        "batch_rest_count": 5,
        "batch_rest_min_min": 3,
        "batch_rest_max_min": 6,
        "health_check_interval_min": 15,
        "dedup_cache_ttl_sec": 120,
    })

    updated = manage.get_settings()
    assert updated["schedule_jitter_min"] == 10
    assert updated["batch_rest_count"] == 5
    assert updated["batch_rest_min_min"] == 3
    assert updated["batch_rest_max_min"] == 6
    assert updated["health_check_interval_min"] == 15
    assert updated["dedup_cache_ttl_sec"] == 120


def test_dir_dedup_cache():
    db.init()
    queue_worker._dir_cache.clear()
    cid = 99999
    now = time.time()
    queue_worker._dir_cache[cid] = (now, {"庆余年S01E01.mp4", "庆余年S01E02.mp4"})

    # 缓存命中
    cached = queue_worker._get_dir_names_cached(cid)
    assert "庆余年S01E01.mp4" in cached

    # 缓存动态累加新转存文件
    queue_worker._add_to_dir_cache(cid, "庆余年S01E03.mp4")
    assert "庆余年S01E03.mp4" in queue_worker._dir_cache[cid][1]


def test_parent_child_switch_linkage():
    db.init()
    # 1. 频道联动：停用频道时，下级 sub_only 必须自动归零关闭
    db.execute("INSERT OR REPLACE INTO channels (id, name, type, enabled, sub_only, created_at) VALUES (888, 'test_ch', 'polling', 1, 1, ?)", (time.time(),))
    ch = db.query("SELECT enabled, sub_only FROM channels WHERE id=888", one=True)
    assert ch["enabled"] == 1
    assert ch["sub_only"] == 1

    manage.update_channel(888, {"enabled": 0})
    ch_after = db.query("SELECT enabled, sub_only FROM channels WHERE id=888", one=True)
    assert ch_after["enabled"] == 0
    assert ch_after["sub_only"] == 0, "频道停用后，下级 sub_only 必须联动归零！"

    # 2. 系统设置联动：auto_move_enabled 能被正常读取与保存
    manage.put_settings({"auto_move_enabled": True})
    assert manage.get_settings()["auto_move_enabled"] is True
    manage.put_settings({"auto_move_enabled": False})
    assert manage.get_settings()["auto_move_enabled"] is False


if __name__ == "__main__":
    test_anti_risk_settings_api()
    test_dir_dedup_cache()
    test_parent_child_switch_linkage()
    print("ALL TESTS IN test_anti_risk.py PASSED SUCCESSFULLY!")
