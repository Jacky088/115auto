"""仪表盘全历史分类占比聚合测试。

覆盖：
- 仅统计有效转存（success + submitted），过滤 failed/blocked/queued/skipped/duplicate
- 同时返回 all / 30d / 7d 三个时间口径
- 空/空白分类归入「未分类」，负值/空体积安全按 0 累加
- 排序遵循 count DESC, size DESC, category ASC
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_dash_"))

import pytest

from backend import database as db
from backend.api.manage import system as sys_api


@pytest.fixture(autouse=True)
def _init_db():
    db.init()
    db.execute("DELETE FROM transfers")
    yield


def _add_transfer(code, category, status, size, age_days):
    db.insert(
        "INSERT INTO transfers(share_code, title, category, status, size, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (code, f"item-{code}", category, status, size, time.time() - age_days * 86400),
    )


def test_category_breakdown_ranges_and_status_filter():
    # 近 7 天：电视剧 x2 (含 submitted)、电影 x1、空分类 x1
    _add_transfer("c1", "电视剧", "success", 2_000, 1)
    _add_transfer("c2", "电视剧", "submitted", 1_500, 3)
    _add_transfer("c3", "电影", "success", 5_000, 2)
    _add_transfer("c4", "   ", "success", 0, 1)

    # 8~30 天：电影 x2、动漫 x1
    _add_transfer("c5", "电影", "success", 3_000, 10)
    _add_transfer("c6", "电影", "success", 4_000, 20)
    _add_transfer("c7", "动漫", "success", 800, 15)

    # > 30 天（历史）：电视剧 x3、纪录片 x1
    _add_transfer("c8", "电视剧", "success", 1_000, 45)
    _add_transfer("c9", "电视剧", "success", 1_000, 60)
    _add_transfer("c10", "电视剧", "success", 1_000, 90)
    _add_transfer("c11", "纪录片", "success", -100, 120)  # 异常负体积按 0 处理

    # 无效状态：不应计入分类占比
    _add_transfer("f1", "电视剧", "failed", 9_999, 1)
    _add_transfer("f2", "电影", "blocked", 9_999, 1)
    _add_transfer("f3", "电影", "queued", 9_999, 1)
    _add_transfer("f4", "动漫", "skipped", 9_999, 1)
    _add_transfer("f5", "动漫", "duplicate", 9_999, 1)

    res = sys_api.dashboard(_=None)
    cats = res["categories"]

    # all: 电视剧 5 (6500), 电影 3 (12000), 动漫 1 (800), 未分类 1 (0), 纪录片 1 (0)
    assert cats["all"]["total"] == 11
    assert cats["all"]["total_size"] == 19_300
    assert [it["category"] for it in cats["all"]["items"]] == ["电视剧", "电影", "动漫", "未分类", "纪录片"]
    assert cats["all"]["items"][0] == {"category": "电视剧", "count": 5, "size": 6_500}
    assert cats["all"]["items"][1] == {"category": "电影", "count": 3, "size": 12_000}
    assert cats["all"]["items"][4] == {"category": "纪录片", "count": 1, "size": 0}

    # 30d: 电影 3 (12000), 电视剧 2 (3500), 动漫 1 (800), 未分类 1 (0)
    assert cats["30d"]["total"] == 7
    assert cats["30d"]["total_size"] == 16_300
    assert [it["category"] for it in cats["30d"]["items"]] == ["电影", "电视剧", "动漫", "未分类"]

    # 7d: 电视剧 2 (3500), 电影 1 (5000), 未分类 1 (0)
    assert cats["7d"]["total"] == 4
    assert cats["7d"]["total_size"] == 8_500
    assert [it["category"] for it in cats["7d"]["items"]] == ["电视剧", "电影", "未分类"]


def test_category_breakdown_empty_table():
    res = sys_api.dashboard(_=None)
    for k in ("all", "30d", "7d"):
        assert res["categories"][k] == {"total": 0, "total_size": 0, "items": []}
