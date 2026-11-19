"""API 核心接口集成测试（鉴权、安全、向导、设置、频道、订阅、转存与黑名单）。

采用 FastAPI TestClient 纯内存执行，秒级运行，可稳定常驻 CI 与本地 pytest。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_api_"))

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend import auth, database as db
from backend.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _setup_db():
    db.init()
    db.set_setting("web_username", "admin")
    db.set_setting("web_password_hash", auth.hash_password("admin123"))
    with auth._fail_lock:
        auth._fail_records.clear()
    yield


def test_auth_and_security():
    # 1. 未登录访问受保护接口
    r = client.get("/api/dashboard/stats")
    assert r.status_code == 401

    # 2. 错误密码被拒
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrongpassword"})
    assert r.status_code == 401

    # 3. 正确密码登录
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    token = r.json().get("token")
    assert token

    headers = {"Authorization": f"Bearer {token}"}

    # 4. 携带 Bearer Token 访问正常
    r = client.get("/api/dashboard/stats", headers=headers)
    assert r.status_code == 200
    assert "quota" in r.json()

    # 5. 退出登录
    r = client.post("/api/auth/logout", headers=headers)
    assert r.status_code == 200

    # 6. 已吊销的 Token 再次访问被拒
    r = client.get("/api/dashboard/stats", headers=headers)
    assert r.status_code == 401


def test_wizard_and_settings():
    token = auth.make_token("admin")
    h = {"Authorization": f"Bearer {token}"}

    # 向导状态
    w = client.get("/api/wizard/status", headers=h).json()
    assert "completed" in w

    # 读取设置
    s = client.get("/api/settings", headers=h).json()
    assert "daily_limit" in s

    # 热更新设置（合法区间：poll_interval_min / max 在 15~3600）
    r = client.put("/api/settings", headers=h, json={"poll_interval_min": 20, "poll_interval_max": 40})
    assert r.status_code == 200
    s2 = client.get("/api/settings", headers=h).json()
    assert s2["poll_interval_min"] == 20
    assert s2["poll_interval_max"] == 40


@patch("backend.tg.validate.check_channel_access", new=AsyncMock(return_value={"ok": True, "title": "测试频道"}))
def test_channels_crud():
    token = auth.make_token("admin")
    h = {"Authorization": f"Bearer {token}"}

    # 添加频道
    r = client.post("/api/channels", headers=h, json={"name": "test_ch_integ", "type": "polling"})
    assert r.status_code == 200 and r.json().get("ok") is True

    # 列表可见
    chs = client.get("/api/channels", headers=h).json()
    matched = [x for x in chs if x["name"] == "test_ch_integ"]
    assert len(matched) == 1
    ch_id = matched[0]["id"]

    # 删除频道
    r = client.delete(f"/api/channels/{ch_id}", headers=h)
    assert r.status_code == 200
    chs2 = client.get("/api/channels", headers=h).json()
    assert not any(x["name"] == "test_ch_integ" for x in chs2)


def test_subscriptions_crud():
    token = auth.make_token("admin")
    h = {"Authorization": f"Bearer {token}"}

    # 添加订阅
    r = client.post("/api/subscriptions", headers=h, json={
        "keyword": "测试剧集",
        "note": "集成测试用",
    })
    assert r.status_code == 200 and r.json().get("ok") is True

    # 列表可见
    res = client.get("/api/subscriptions", headers=h).json()
    items = res.get("items", [])
    matched = [x for x in items if x["keyword"] == "测试剧集"]
    assert len(matched) == 1
    sub_id = matched[0]["id"]

    # 删除订阅
    r = client.delete(f"/api/subscriptions/{sub_id}", headers=h)
    assert r.status_code == 200
    res2 = client.get("/api/subscriptions", headers=h).json()
    items2 = res2.get("items", [])
    assert not any(x["keyword"] == "测试剧集" for x in items2)


def test_blacklist_keyword_crud():
    token = auth.make_token("admin")
    h = {"Authorization": f"Bearer {token}"}

    # 批量添加关键词
    client.post("/api/blacklist/keyword", headers=h, json={"value": "测试词AAA\n测试词BBB"})
    kws = [k["value"] for k in client.get("/api/blacklist/keyword", headers=h).json()]
    assert "测试词AAA" in kws and "测试词BBB" in kws

    # 删除关键词
    all_kws = client.get("/api/blacklist/keyword", headers=h).json()
    kid = [k for k in all_kws if k["value"] == "测试词AAA"][0]["id"]
    client.delete(f"/api/blacklist/keyword/{kid}", headers=h)

    kws2 = [k["value"] for k in client.get("/api/blacklist/keyword", headers=h).json()]
    assert "测试词AAA" not in kws2


def test_manual_transfer_and_retry():
    token = auth.make_token("admin")
    h = {"Authorization": f"Bearer {token}"}

    # 手动转存入队
    r = client.post("/api/transfers/manual", headers=h, json={
        "text": "https://115cdn.com/s/testinteg01?password=1234",
        "target_dir": "/最近接收",
    })
    assert r.status_code == 200
    assert r.json().get("result", {}).get("queued") == 1

    # 查看列表
    tr = client.get("/api/transfers", headers=h, params={"size": 50}).json()
    items = [i for i in tr["items"] if i.get("share_code") == "testinteg01"]
    assert len(items) == 1
    tid = items[0]["id"]

    # 重试转存（running/success 拒绝，但先把状态设为 failed 模拟可重试）
    db.execute("UPDATE transfers SET status='failed', error='模拟失败' WHERE id=?", (tid,))
    r = client.post(f"/api/transfers/{tid}/retry", headers=h)
    assert r.status_code == 200
    assert r.json().get("ok") is True

    # 日志可查
    logs = client.get("/api/logs", headers=h).json()
    assert logs["total"] >= 0
