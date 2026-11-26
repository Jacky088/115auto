import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_quality_test_"))

import pytest

from backend import database as db
from backend.core import quality
from backend.pan115 import client as pan
from backend.worker import queue_worker


@pytest.fixture(autouse=True)
def init_test_db():
    db.init()
    yield


def test_quality_parse_ranks():
    assert quality.parse("剧名 2160p REMUX H265")["score"] > quality.parse("剧名 1080p WEB-DL")["score"]
    assert quality.parse("剧名 1080p")["score"] > quality.parse("剧名 720p")["score"]
    # 没有画质标识 → 未知（0 分），不得据此判定高低
    assert quality.parse("剧名 第05集")["score"] == 0
    assert quality.parse("")["score"] == 0


def test_min_resolution_unknown_passes():
    # 识别不出画质一律放行，避免误拦标题没写分辨率的好资源
    assert quality.min_resolution_allowed(quality.parse("无画质标识"), "1080p") is True
    assert quality.min_resolution_allowed(quality.parse("剧名 720p"), "1080p") is False
    assert quality.min_resolution_allowed(quality.parse("剧名 1080p"), "1080p") is True
    assert quality.min_resolution_allowed(quality.parse("剧名 720p"), "") is True


def test_wash_archive_only_moves_present_ids(monkeypatch):
    db.execute(
        "INSERT INTO transfers(share_code,title,dest_cid,dest_ids,status,created_at)"
        " VALUES(?,?,?,?,?,?)",
        ("abcde", "旧版", 777, '["1","2","3"]', "success", 0),
    )
    old_id = db.query("SELECT id FROM transfers WHERE share_code='abcde'", one=True)["id"]
    db.set_setting("wash_archive_dir", "/归档")
    moved = {}
    monkeypatch.setattr(pan, "list_entries",
                        lambda cid, limit=2000: [{"id": "2"}, {"id": "3"}, {"id": "9"}])
    monkeypatch.setattr(pan, "mkdirs", lambda path: 888)
    monkeypatch.setattr(pan, "move_entries",
                        lambda ids, cid: moved.update(ids=ids, cid=cid))
    queue_worker._archive_washed_version({"wash_from": old_id})
    # 只移动仍存在于原目录的条目，用户已挪走的不跨目录误移
    assert moved == {"ids": ["2", "3"], "cid": 888}


def test_wash_archive_failure_never_raises(monkeypatch):
    db.execute(
        "INSERT INTO transfers(share_code,title,dest_cid,dest_ids,status,created_at)"
        " VALUES(?,?,?,?,?,?)",
        ("fghij", "旧版2", 777, '["5"]', "success", 0),
    )
    old_id = db.query("SELECT id FROM transfers WHERE share_code='fghij'", one=True)["id"]
    db.set_setting("wash_archive_dir", "/归档")

    def boom(*a, **kw):
        raise RuntimeError("115 down")

    monkeypatch.setattr(pan, "list_entries", boom)
    queue_worker._archive_washed_version({"wash_from": old_id})  # 不抛异常即通过


def test_doctor_never_leaks_secrets(monkeypatch):
    from backend.api import manage

    db.set_setting("notify_token", "SECRET_TOKEN_VALUE")
    db.set_setting("bark_url", "https://api.day.app/SECRETKEY")
    monkeypatch.setattr(pan, "check_login", lambda force=False: {"ok": True, "name": "tester"})
    monkeypatch.setattr(pan, "space_info", lambda force=False: {"total": 100, "use": 10, "free": 90})
    res = manage.system_doctor(None)
    assert res["overall"] in ("ok", "warn", "fail")
    assert res["checks"] and all({"id", "name", "status", "detail"} <= set(c) for c in res["checks"])
    assert "SECRET_TOKEN_VALUE" not in res["report"]
    assert "SECRETKEY" not in res["report"]


def _reset():
    from backend.core import subscribe as submod
    db.execute("DELETE FROM transfers")
    db.execute("DELETE FROM subscriptions")
    db.execute("DELETE FROM messages")
    submod.invalidate_cache()


def test_quality_best_pick_per_episode():
    """同一订阅同一集：同画质跳过、更高画质替代排队中的低画质任务。"""
    from backend.core import pipeline, subscribe as submod

    _reset()
    db.set_setting("quality_policy_enabled", True)
    db.set_setting("quality_min_resolution", "")
    db.execute("INSERT INTO subscriptions(keyword,enabled,min_quality,created_at) VALUES(?,?,?,?)",
               ("测试质量剧", 1, "", 0))
    submod.invalidate_cache()

    r1 = pipeline.process_message("qtest", 900001,
                                  "测试质量剧 S01E01 1080p WEB-DL https://115.com/s/qq1080")
    assert r1["queued"] == 1
    # 同集同画质 → 判重跳过
    r2 = pipeline.process_message("qtest", 900002,
                                  "测试质量剧 S01E01 1080p WEB-DL https://115.com/s/qqnext")
    assert r2["skipped"] == 1
    # 同集更高画质 → 排队中的 1080p 被替代，2160p 入队
    r3 = pipeline.process_message("qtest", 900003,
                                  "测试质量剧 S01E01 2160p REMUX https://115.com/s/qq2160")
    assert r3["queued"] == 1
    old = db.query("SELECT status, error FROM transfers WHERE share_code='qq1080'", one=True)
    assert old["status"] == "skipped" and "更高画质" in old["error"]
    # 更低画质 → 直接跳过，不再入队
    r4 = pipeline.process_message("qtest", 900004,
                                  "测试质量剧 S01E01 720p https://115.com/s/qq720p")
    assert r4["skipped"] == 1
    row = db.query("SELECT error FROM transfers WHERE share_code='qq720p'", one=True)
    assert "同等或更高画质" in row["error"]


def test_min_quality_subscription_override():
    """订阅级最低画质：明确低于门槛才拦截，识别不出画质一律放行。"""
    from backend.core import pipeline, subscribe as submod

    _reset()
    db.set_setting("quality_policy_enabled", True)
    db.set_setting("quality_min_resolution", "")
    db.execute("INSERT INTO subscriptions(keyword,enabled,min_quality,created_at) VALUES(?,?,?,?)",
               ("测试画质门槛", 1, "1080p", 0))
    submod.invalidate_cache()

    r1 = pipeline.process_message("qtest", 900011,
                                  "测试画质门槛 S01E02 720p https://115.com/s/q2min1")
    assert r1["skipped"] == 1
    row = db.query("SELECT error FROM transfers WHERE share_code='q2min1'", one=True)
    assert "画质低于最低要求" in row["error"]
    # 达标 → 入队
    r2 = pipeline.process_message("qtest", 900012,
                                  "测试画质门槛 S01E02 1080p https://115.com/s/q2min2")
    assert r2["queued"] == 1
    # 未写画质 → 放行（不得误拦）
    r3 = pipeline.process_message("qtest", 900013,
                                  "测试画质门槛 S01E03 https://115.com/s/q2min3")
    assert r3["queued"] == 1


def test_quality_settings_and_doctor_api():
    """设置校验 / 订阅画质读写 / 自检接口鉴权与不泄露凭据。"""
    from fastapi.testclient import TestClient

    from backend import auth
    from backend.main import app

    db.set_setting("notify_token", "SECRET_TOKEN_VALUE")
    client = TestClient(app)
    # 未登录不可访问自检
    assert client.get("/api/system/doctor").status_code == 401
    headers = {"Authorization": f"Bearer {auth.make_token('admin')}"}

    r = client.put("/api/settings", json={"quality_min_resolution": "1440p"}, headers=headers)
    assert r.status_code == 400
    # 开洗版必须填归档目录
    r = client.put("/api/settings", json={"wash_enabled": True, "wash_archive_dir": ""}, headers=headers)
    assert r.status_code == 400
    r = client.put("/api/settings", json={
        "quality_policy_enabled": True, "quality_min_resolution": "1080p",
        "wash_enabled": True, "wash_archive_dir": "/媒体库/洗版归档",
    }, headers=headers)
    assert r.status_code == 200
    got = client.get("/api/settings", headers=headers).json()
    assert got["quality_min_resolution"] == "1080p"
    assert got["wash_archive_dir"] == "/媒体库/洗版归档"

    # 订阅级最低画质可写可读
    r = client.post("/api/subscriptions", json={"keyword": "画质接口剧", "min_quality": "2160p"},
                    headers=headers)
    assert r.status_code == 200
    items = client.get("/api/subscriptions", headers=headers).json()["items"]
    assert any(i["keyword"] == "画质接口剧" and i["min_quality"] == "2160p" for i in items)

    r = client.get("/api/system/doctor", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["report"] and "SECRET_TOKEN_VALUE" not in body["report"]
    assert all(c["status"] in ("ok", "warn", "fail", "skip") for c in body["checks"])


