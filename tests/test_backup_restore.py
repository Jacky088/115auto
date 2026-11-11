import gzip
import io
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend import auth, database as db
from backend.worker import bot_commands, jobs

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    db.init()
    db.set_setting("running", False)
    yield


def test_backup_payload_structure_and_security():
    # 写入测试数据
    db.set_setting("notify_token", "secret_bot_token_123")
    db.set_setting("web_password_hash", "secret_hash_abc")
    db.set_setting("web_username", "admin_user")
    db.set_setting("daily_limit", 60)

    db.execute("INSERT OR REPLACE INTO channels(id, type, name, enabled, created_at) VALUES(1, 'polling', 'test_chan', 1, 1700000000)")
    db.execute("INSERT OR REPLACE INTO rules(id, pattern, category, priority, enabled) VALUES(1, '.*', '电影', 10, 1)")
    db.execute("INSERT OR REPLACE INTO routes(category, target_dir, offline_dir) VALUES('电影', '0', '0')")
    db.execute("INSERT OR REPLACE INTO blacklist(id, type, value, note, created_at) VALUES(1, 'keyword', 'ad_keyword', 'note', 1700000000)")
    db.execute("INSERT OR REPLACE INTO subscriptions(id, keyword, note, enabled, created_at) VALUES(1, '庆余年', '热播', 1, 1700000000)")
    db.execute("INSERT OR REPLACE INTO auto_move_rules(id, source_dir, target_dir, keyword, mode, enabled, created_at) VALUES(1, '10', '20', '剧名', 'default', 1, 1700000000)")
    db.execute("INSERT OR REPLACE INTO transfers(id, share_code, receive_code, title, status, file_count, size, created_at) "
               "VALUES(1, 'code123', 'pwd1', 'Test Movie', 'success', 1, 1024, 1700000000)")

    payload = db.backup_payload()

    # 1. 验证必须包含的结构段
    assert "_meta" in payload
    assert payload["_meta"]["version"] == 1
    for key in ("settings", "channels", "rules", "routes", "blacklist", "subscriptions", "transfers", "auto_move_rules"):
        assert key in payload
        assert len(payload[key]) >= 1

    # 2. 验证敏感凭据绝对不泄露
    settings = payload["settings"]
    assert "notify_token" not in settings
    assert "web_password_hash" not in settings
    assert "web_username" not in settings
    # 非敏感设置正常备份
    assert settings.get("daily_limit") == "60" or settings.get("daily_limit") == 60


def test_restore_payload_integrity_and_credentials_preservation():
    # 准备初始现场并备份
    db.set_setting("daily_limit", 88)
    db.set_setting("web_password_hash", "my_original_password_hash")
    db.set_setting("notify_token", "my_original_tg_token")
    db.execute("INSERT OR REPLACE INTO channels(id, type, name, enabled, created_at) VALUES(99, 'polling', 'chan99', 1, 1700000000)")
    db.set_setting("running", False)

    backup = db.backup_payload()

    # 模拟数据被修改或清空
    db.set_setting("daily_limit", 10)
    db.execute("DELETE FROM channels")
    assert db.query("SELECT count(*) c FROM channels", one=True)["c"] == 0

    # 执行恢复
    stats = db.restore_payload(backup)
    assert stats["channels"] >= 1
    assert stats["settings"] >= 1

    # 验证数据已完全恢复
    assert db.query("SELECT count(*) c FROM channels WHERE id=99", one=True)["c"] == 1
    assert db.get_setting("daily_limit") == 88

    # 验证敏感凭据未被破坏，且状态置为停止
    assert db.get_setting("web_password_hash") == "my_original_password_hash"
    assert db.get_setting("notify_token") == "my_original_tg_token"
    assert db.get_setting("running") is False


def test_web_api_export_and_restore():
    token = auth.make_token("admin")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. 测试导出接口 /api/backup/export
    r = client.get("/api/backup/export", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "attachment; filename=" in r.headers.get("content-disposition", "")
    data = r.json()
    assert "_meta" in data
    assert "settings" in data

    # 2. 测试恢复接口 /api/backup/restore
    db.set_setting("running", False)
    r2 = client.post("/api/backup/restore", json=data, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
    assert "stats" in r2.json()

    # 3. 测试别名接口 /api/settings/backup/restore 兼容性
    r3 = client.post("/api/settings/backup/restore", json=data, headers=headers)
    assert r3.status_code == 200
    assert r3.json()["ok"] is True


@pytest.mark.anyio
async def test_tg_bot_backup_and_gzip_flow():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_client.post.return_value = mock_resp

    # 1. 正常小体积文件发送（.json）
    await bot_commands.send_backup_flow(mock_client, "fake_token", "123456")
    assert mock_client.post.called
    call_kwargs = mock_client.post.call_args[1]
    files = call_kwargs["files"]
    fname, content, mime = files["document"]
    assert fname.endswith(".json")
    assert mime == "application/json"

    # 2. 模拟超大备份（>8MB）触发 gzip 自动压缩
    big_transfers = [{"id": i, "share_code": f"sc_{i}", "raw_text": "x" * 2000} for i in range(5000)]
    fake_big_payload = db.backup_payload()
    fake_big_payload["transfers"] = big_transfers

    with patch("backend.database.backup_payload", return_value=fake_big_payload):
        mock_client.reset_mock()
        await bot_commands.send_backup_flow(mock_client, "fake_token", "123456")
        assert mock_client.post.called
        call_kwargs = mock_client.post.call_args[1]
        files = call_kwargs["files"]
        fname, content, mime = files["document"]
        assert fname.endswith(".json.gz")
        assert mime == "application/gzip"
        decompressed = json.loads(gzip.decompress(content))
        assert len(decompressed["transfers"]) == 5000


@pytest.mark.anyio
async def test_tg_bot_restore_upload_flow_json_and_gz():
    token = "fake_token"
    chat_id = "123456"
    bot_commands._RESTORE_PENDING.clear()

    payload = db.backup_payload()
    json_bytes = json.dumps(payload).encode("utf-8")
    gz_bytes = gzip.compress(json_bytes)

    mock_client = AsyncMock()
    mock_getfile_resp = MagicMock()
    mock_getfile_resp.status_code = 200
    mock_getfile_resp.json.return_value = {"result": {"file_path": "documents/backup.json.gz"}}

    mock_file_resp = MagicMock()
    mock_file_resp.status_code = 200
    mock_file_resp.content = gz_bytes

    async def fake_post(url, **kwargs):
        return mock_getfile_resp

    async def fake_get(url, **kwargs):
        return mock_file_resp

    mock_client.post.side_effect = fake_post
    mock_client.get.side_effect = fake_get

    doc = {"file_id": "file_123", "file_name": "autosave_backup.json.gz", "file_size": len(gz_bytes)}
    reply = await bot_commands.restore_upload_flow(mock_client, token, chat_id, doc)
    assert "检测到备份文件" in reply
    assert chat_id in bot_commands._RESTORE_PENDING

    cached_data, _ = bot_commands._RESTORE_PENDING[chat_id]
    assert "_meta" in cached_data
    assert "settings" in cached_data
