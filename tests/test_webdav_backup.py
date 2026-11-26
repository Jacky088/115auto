"""WebDAV 云端备份功能单元测试与 API 集成测试。"""
from __future__ import annotations

import json
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend import auth, database as db
from backend.core.webdav import WebDAVClient, get_webdav_config, save_webdav_config
from backend.main import app


@pytest.fixture(autouse=True)
def _reset_db():
    db.init()
    db.set_setting("webdav_url", "")
    db.set_setting("webdav_username", "")
    db.set_setting("webdav_password", "")
    db.set_setting("webdav_root_dir", "/115AutoSave_Backup")
    db.set_setting("webdav_keep_count", 10)
    db.set_setting("webdav_auto_backup_enabled", False)
    db.set_setting("webdav_last_test_ok", False)


def _authed_client() -> TestClient:
    token = auth.make_token("admin")
    return TestClient(app, cookies={"sa_auth": token})


def test_webdav_config_get_and_save():
    save_webdav_config(
        url="https://dav.example.com/dav/",
        username="user1",
        password="SecretPassword123",
        root_dir="/MyBackups",
        keep_count=5,
        auto_backup_enabled=True,
    )
    cfg = get_webdav_config()
    assert cfg["enabled"] is True
    assert cfg["url"] == "https://dav.example.com/dav"
    assert cfg["username"] == "user1"
    assert cfg["password_set"] is True
    assert cfg["root_dir"] == "/MyBackups"
    assert cfg["keep_count"] == 5
    assert cfg["auto_backup_enabled"] is True

    # 密码传空时不覆盖已有密码
    save_webdav_config(
        url="https://dav.example.com/dav/",
        username="user1",
        password="",
        root_dir="/MyBackups",
        keep_count=7,
        auto_backup_enabled=False,
    )
    cfg2 = get_webdav_config()
    assert cfg2["keep_count"] == 7
    assert cfg2["password_set"] is True
    assert db.get_setting("webdav_password", "") == "SecretPassword123"


def test_webdav_client_test_connection_success():
    client = WebDAVClient("https://dav.example.com", "user", "pass", "/Backups")

    mock_resp_opt = MagicMock(status_code=200)
    mock_resp_propfind = MagicMock(status_code=207)
    mock_resp_put = MagicMock(status_code=201)
    mock_resp_del = MagicMock(status_code=204)

    mock_http_client = MagicMock()

    def mock_request(method, url, **kwargs):
        if method == "OPTIONS":
            return mock_resp_opt
        if method == "PROPFIND":
            return mock_resp_propfind
        return MagicMock(status_code=200)

    mock_http_client.request.side_effect = mock_request
    mock_http_client.put.return_value = mock_resp_put
    mock_http_client.delete.return_value = mock_resp_del
    mock_http_client.__enter__.return_value = mock_http_client

    with patch.object(client, "_client", return_value=mock_http_client):
        res = client.test_connection()
        assert res["ok"] is True
        assert "测试成功" in res["message"]


def test_webdav_client_test_connection_auth_failure():
    client = WebDAVClient("https://dav.example.com", "baduser", "badpass")

    mock_resp_401 = MagicMock(status_code=401)
    mock_http_client = MagicMock()
    mock_http_client.request.return_value = mock_resp_401
    mock_http_client.__enter__.return_value = mock_http_client

    with patch.object(client, "_client", return_value=mock_http_client):
        res = client.test_connection()
        assert res["ok"] is False
        assert "401" in res["message"] or "认证失败" in res["message"]


def test_webdav_client_list_and_prune_backups(tmp_path):
    client = WebDAVClient("https://dav.example.com", "user", "pass", "/Backups")

    sample_xml = b"""<?xml version="1.0" encoding="utf-8"?>
    <D:multistatus xmlns:D="DAV:">
      <D:response>
        <D:href>/Backups/</D:href>
        <D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop></D:propstat>
      </D:response>
      <D:response>
        <D:href>/Backups/autosave_migration_20261120_000000.zip</D:href>
        <D:propstat><D:prop><D:getcontentlength>1000</D:getcontentlength></D:prop></D:propstat>
      </D:response>
      <D:response>
        <D:href>/Backups/autosave_migration_20261121_120000.zip</D:href>
        <D:propstat><D:prop><D:getcontentlength>2000</D:getcontentlength></D:prop></D:propstat>
      </D:response>
    </D:multistatus>
    """

    mock_http_client = MagicMock()
    mock_http_client.request.return_value = MagicMock(status_code=207, content=sample_xml)
    mock_http_client.delete.return_value = MagicMock(status_code=204)
    mock_http_client.__enter__.return_value = mock_http_client

    with patch.object(client, "_client", return_value=mock_http_client):
        items = client.list_backups()
        assert len(items) == 2
        # 按文件名时间戳降序
        assert items[0]["name"] == "autosave_migration_20261121_120000.zip"
        assert items[1]["name"] == "autosave_migration_20261120_000000.zip"

        # 保留 1 份，超额删除 1 份
        pruned = client.prune_backups(keep_count=1)
        assert pruned == 1
        assert mock_http_client.delete.call_count == 1


def test_webdav_api_endpoints():
    tc = _authed_client()

    # 1. 读配置
    r = tc.get("/api/backup/webdav/config")
    assert r.status_code == 200
    assert r.json()["enabled"] is False

    # 未测试直接保存 -> 400 拦截
    r_untested = tc.post(
        "/api/backup/webdav/config",
        json={"url": "https://dav.test.com", "username": "admin", "password": "pwd", "root_dir": "/Backup"},
    )
    assert r_untested.status_code == 400
    assert "必须先点击「测试连接」" in r_untested.json()["detail"]

    # 2. 测试连接（模拟成功）
    with patch("backend.core.webdav.WebDAVClient.test_connection", return_value={"ok": True, "message": "测试成功", "elapsed_ms": 100}):
        r_test = tc.post(
            "/api/backup/webdav/test",
            json={"url": "https://dav.test.com", "username": "admin", "password": "pwd", "root_dir": "/Backup"},
        )
        assert r_test.status_code == 200
        assert r_test.json()["ok"] is True
        assert db.get_setting("webdav_last_test_ok") is True

    # 3. 测试通过后保存配置 -> 200
    r_save = tc.post(
        "/api/backup/webdav/config",
        json={"url": "https://dav.test.com", "username": "admin", "password": "pwd", "root_dir": "/Backup", "keep_count": 5},
    )
    assert r_save.status_code == 200
    assert r_save.json()["enabled"] is True

    # 更换地址但未重新测试连接 -> 400 拦截
    r_change_untested = tc.post(
        "/api/backup/webdav/config",
        json={"url": "https://dav.changed.com", "username": "admin", "password": "pwd"},
    )
    assert r_change_untested.status_code == 400
    assert "必须先点击「测试连接」" in r_change_untested.json()["detail"]

    # 4. 立即导出至 WebDAV
    with patch("backend.core.migration_bundle.build_migration_bundle", return_value=(tempfile.mkdtemp(), tempfile.NamedTemporaryFile().name, "autosave_migration_test.zip", {})), \
            patch("backend.core.webdav.WebDAVClient.upload_file", return_value={"ok": True, "bytes": 1024, "elapsed_sec": 0.5}), \
            patch("backend.core.webdav.WebDAVClient.prune_backups", return_value=0):
        r_exp = tc.post("/api/backup/webdav/export_now", json={"meta_mode": "nfo", "slim_db": True})
        assert r_exp.status_code == 200
        assert r_exp.json()["ok"] is True
        assert r_exp.json()["filename"] == "autosave_migration_test.zip"

    # 5. 清除配置
    r_del = tc.delete("/api/backup/webdav/config")
    assert r_del.status_code == 200
    assert r_del.json()["enabled"] is False
    assert r_del.json()["url"] == ""


@pytest.mark.parametrize("bad_name", [
    "../evil.zip",
    "..\\evil.zip",
    "sub/autosave_x.zip",
    "autosave_../evil.zip",
    "autosave_ok.txt",
    "other.zip",
])
def test_webdav_restore_and_delete_reject_path_traversal(bad_name):
    """预览/恢复/删除统一使用白名单文件名，阻断路径穿越与非法包名。"""
    tc = _authed_client()

    r_prev = tc.post("/api/backup/webdav/restore_preview", params={"filename": bad_name})
    assert r_prev.status_code == 400
    assert "非法的备份文件名" in r_prev.json()["detail"]

    r_apply = tc.post("/api/backup/webdav/restore_apply", params={"filename": bad_name})
    assert r_apply.status_code == 400
    assert "非法的备份文件名" in r_apply.json()["detail"]

    r_del = tc.delete("/api/backup/webdav/file", params={"filename": bad_name})
    assert r_del.status_code == 400
    assert "非法的备份文件名" in r_del.json()["detail"]


def test_webdav_download_rejects_traversal(tmp_path):
    """底层下载客户端同样拒绝带路径分量的远端文件名。"""
    client = WebDAVClient("https://dav.example.com", "user", "pass", "/Backups")
    with pytest.raises(ValueError):
        client.download_file("../evil.zip", tmp_path / "evil.zip")


def test_manual_export_uses_wall_clock_timestamp():
    """手动导出后的最近备份时间必须是 Unix 时间戳，而不是事件循环单调时钟。"""
    save_webdav_config(
        url="https://dav.example.com/dav",
        username="user1",
        password="SecretPassword123",
        root_dir="/Backups",
        keep_count=5,
        auto_backup_enabled=False,
    )
    db.set_setting("webdav_last_test_ok", True)
    tc = _authed_client()
    with patch("backend.core.migration_bundle.build_migration_bundle", return_value=(tempfile.mkdtemp(), tempfile.NamedTemporaryFile().name, "autosave_migration_test.zip", {})), \
            patch("backend.core.webdav.WebDAVClient.upload_file", return_value={"ok": True, "bytes": 1024, "elapsed_sec": 0.5}), \
            patch("backend.core.webdav.WebDAVClient.prune_backups", return_value=0):
        r_exp = tc.post("/api/backup/webdav/export_now", json={"meta_mode": "nfo", "slim_db": True})
        assert r_exp.status_code == 200

    ts = float(db.get_setting("webdav_last_backup_at") or 0)
    assert ts > 1_600_000_000
    assert ts <= time.time() + 5


def test_backup_payload_excludes_webdav_credentials():
    """WebDAV 地址/账号/密码与运行态时间戳不得进入 JSON 备份快照。"""
    save_webdav_config(
        url="https://dav.example.com/dav",
        username="user1",
        password="SecretPassword123",
        root_dir="/Backups",
        keep_count=5,
        auto_backup_enabled=True,
    )
    db.set_setting("webdav_last_backup_at", time.time())
    db.set_setting("webdav_last_backup_status", "成功")
    settings = db.backup_payload()["settings"]
    for key in ("webdav_url", "webdav_username", "webdav_password",
                "webdav_tested_url", "webdav_tested_user", "webdav_last_test_ok",
                "webdav_last_backup_at", "webdav_last_backup_date", "webdav_last_backup_status"):
        assert key not in settings
    # 非敏感配置仍应正常导出
    assert json.loads(settings["webdav_root_dir"]) == "/Backups"
    assert json.loads(settings["webdav_keep_count"]) == 5


def test_migrate_backfills_auto_backup_channels_for_upgrade():
    """旧库升级（缺少 auto_backup_channels 且已开启备份）应回填 TG Bot 通道。"""
    db.set_setting("auto_backup_enabled", True)
    db._conn.execute("DELETE FROM settings WHERE key='auto_backup_channels'")
    db._conn.commit()
    try:
        from backend.db.migrate import _migrate
        _migrate()
        assert db.get_setting("auto_backup_channels") == ["tgbot"]
    finally:
        db.set_setting("auto_backup_enabled", False)
        db.set_setting("auto_backup_channels", [])


def test_corrupted_1970_timestamp_auto_healing():
    """历史异常时间戳（如 17391000 导致 1970 年）能够从备份文件名自愈还原真实时间戳。"""
    # 模拟历史数据库里残留的异常时间戳 17391000
    db.set_setting("webdav_last_backup_at", 17391000.0)
    db.set_setting("webdav_last_backup_status", "成功 (autosave_migration_20261121_220639.zip)")

    cfg = get_webdav_config()
    # 验证纠偏后的时间戳大于 2000 年（2026 年）
    assert cfg["last_backup_at"] > 946684800
    import datetime
    dt = datetime.datetime.fromtimestamp(cfg["last_backup_at"])
    assert dt.year == 2026
    assert dt.month == 11
    assert dt.day == 21
    assert dt.hour == 22
    assert dt.minute == 6

    # 验证已自动回写至数据库，避免下次重复纠偏
    persisted = float(db.get_setting("webdav_last_backup_at") or 0)
    assert persisted == cfg["last_backup_at"]


def test_webdav_export_credentials_requires_password_and_restore_rotates_token():
    """含凭据的 WebDAV 导出必须校验登录密码；恢复凭据后必须下发新会话凭证。"""
    db.set_setting("web_username", "admin")
    db.set_setting("web_password_hash", auth.hash_password("WebPass123!"))
    save_webdav_config(
        url="https://dav.example.com/dav",
        username="user1",
        password="SecretPassword123",
        root_dir="/Backups",
        keep_count=5,
        auto_backup_enabled=False,
    )
    db.set_setting("webdav_last_test_ok", True)
    db.set_setting("running", False)
    tc = _authed_client()

    # 未提供密码 / 错误密码均返回 400
    r_miss = tc.post("/api/backup/webdav/export_now", json={"include_credentials": True, "meta_mode": "nfo", "slim_db": True})
    assert r_miss.status_code == 400
    r_bad = tc.post("/api/backup/webdav/export_now", json={"include_credentials": True, "password": "Wrong", "meta_mode": "nfo", "slim_db": True})
    assert r_bad.status_code == 400

    with patch("backend.core.migration_bundle.build_migration_bundle", return_value=(tempfile.mkdtemp(), tempfile.NamedTemporaryFile().name, "autosave_migration_test.zip", {})), \
            patch("backend.core.webdav.WebDAVClient.upload_file", return_value={"ok": True, "bytes": 1024, "elapsed_sec": 0.5}), \
            patch("backend.core.webdav.WebDAVClient.prune_backups", return_value=0):
        r_ok = tc.post(
            "/api/backup/webdav/export_now",
            json={"include_credentials": True, "password": "WebPass123!", "meta_mode": "nfo", "slim_db": True},
        )
        assert r_ok.status_code == 200

    # 恢复含凭据的远端迁移包时签发新 Token
    with patch("backend.core.webdav.WebDAVClient.download_file", return_value=1024), \
            patch("backend.core.migration_bundle.apply_migration_bundle", return_value={"restored_credentials": True, "summary": {}, "warnings": []}):
        r_apply = tc.post("/api/backup/webdav/restore_apply", params={"filename": "autosave_migration_20261125_010000.zip"})
        assert r_apply.status_code == 200
        body = r_apply.json()
        assert body["ok"] is True
        assert body.get("token")


