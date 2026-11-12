import gzip
import io
import json
import os
import tempfile
import time
import zipfile
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_backup_"))

from fastapi.testclient import TestClient

from backend.main import app
from backend import auth, config, database as db
from backend.core import migration_bundle
from backend.worker import bot_commands, jobs

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    db.init()
    db.set_setting("running", False)
    db.execute("DELETE FROM transfers")
    db.execute("DELETE FROM media_tasks")
    bot_commands._RESTORE_PENDING.clear()
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


def test_tg_verify_restore_handles_legacy_runtime_keys_and_media_tables():
    # 目标实例当前已有凭据与本机登录态，及恢复前残留的 media_tasks
    db.set_setting("web_password_hash", "keep_hash")
    db.set_setting("notify_token", "keep_tok")
    db.set_setting("cookie_ok", True)
    db.set_setting("userbot_ok", False)
    db.set_setting("setup_completed", False)
    db.set_setting("running", False)
    db.execute("DELETE FROM media_tasks")
    db.execute(
        "INSERT INTO media_tasks(task_kind, rule_id, file_id, orig_name, status, created_at, updated_at) "
        "VALUES('scrape', 999, 'stale_f1', 'stale.mkv', 'need_confirm', 1, 1)"
    )

    # 模拟旧版导出的备份（含 paused / auto_stop_reason / backfill_pending / poll_fail_* / cookie_ok / setup_completed）
    legacy_backup = {
        "_meta": {"version": 1, "exported_at": "2026-01-01 00:00:00"},
        "settings": {
            "daily_limit": "77",
            "running": "true",
            "paused": "false",
            "auto_stop_reason": "",
            "backfill_pending": "[]",
            "poll_fail_12": "3",
            "cookie_ok": "false",
            "userbot_ok": "true",
            "setup_completed": "true",
        },
        "channels": [],
        "rules": [],
        "routes": [],
        "blacklist": [],
        "subscriptions": [],
        "transfers": [],
        "auto_move_rules": [],
        "media_rules": [
            {
                "id": 3, "name": "剧集整理", "source_dir": "/待整理", "target_dir": "/媒体库",
                "media_type": "tv", "organize_enabled": 1, "scrape_enabled": 1, "enabled": 1,
                "created_at": 1700000000.0, "last_run_at": None, "last_count": 0,
            }
        ],
        "organized_records": [
            {
                "id": 5, "task_id": 1, "file_id": "f5", "parent_cid": "10", "sha1": "abc",
                "size": 123, "orig_path": "/a", "orig_name": "a.mkv", "dest_cid": "20",
                "dest_path": "/b", "dest_name": "b.mkv", "action": "move", "tmdb_id": 100,
                "batch_id": "b1", "undone": 0, "created_at": 1700000000.0,
            }
        ],
    }

    chat_id = "tg_admin_1"
    bot_commands._RESTORE_PENDING[chat_id] = (legacy_backup, 9e18)
    reply = bot_commands._do_restore(chat_id)
    assert reply.startswith("✅ 已从备份恢复"), reply
    assert "整理刮削 1" in reply and "操作账本 1" in reply

    # 本机状态未被旧备份覆写，且残留挂起任务已随回滚清空
    assert db.get_setting("web_password_hash") == "keep_hash"
    assert db.get_setting("cookie_ok") is True
    assert db.get_setting("userbot_ok") is False
    assert db.get_setting("setup_completed") is False
    assert db.get_setting("running") is False
    assert db.get_setting("daily_limit") == 77
    assert db.query("SELECT count(*) c FROM media_tasks", one=True)["c"] == 0

    # 导出时不再包含运行态或本机登录态
    exported = db.backup_payload()["settings"]
    for hidden in ("running", "paused", "auto_stop_reason", "backfill_pending", "poll_fail_12",
                   "cookie_ok", "userbot_ok", "setup_completed", "web_password_hash", "notify_token"):
        assert hidden not in exported

    # _verify_restore 应能捕获 media_rules / organized_records 数量不一致
    db.execute("DELETE FROM media_rules")
    mismatch = bot_commands._verify_restore(legacy_backup)
    assert "media_rules 数量不一致" in mismatch


def test_web_api_gz_zip_preview_restore_and_1mb_exempt():
    import zipfile

    token = auth.make_token("admin")
    headers = {"Authorization": f"Bearer {token}"}
    db.set_setting("running", False)
    db.set_setting("daily_limit", 55)

    payload = db.backup_payload()
    json_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    gz_bytes = gzip.compress(json_bytes)

    # 1. /api/backup/preview 预览 .json.gz（支持中文文件名头编码）
    r_prev = client.post(
        "/api/backup/preview",
        content=gz_bytes,
        headers={**headers, "Content-Type": "application/octet-stream", "X-Backup-Filename": "%E5%A4%87%E4%BB%BD.json.gz"},
    )
    assert r_prev.status_code == 200, r_prev.text
    assert r_prev.json()["summary"]["settings"] == len(payload["settings"])

    # 2. /api/backup/restore 直接上传 .json.gz 恢复
    db.set_setting("daily_limit", 11)
    r_gz = client.post(
        "/api/backup/restore",
        content=gz_bytes,
        headers={**headers, "Content-Type": "application/octet-stream", "X-Backup-Filename": "backup.json.gz"},
    )
    assert r_gz.status_code == 200, r_gz.text
    assert db.get_setting("daily_limit") == 55

    # 3. /api/backup/restore 支持 .zip（跳过 __MACOSX 元文件）
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr("__MACOSX/._backup.json", b"junk")
        zf.writestr("autosave_backup.json", json_bytes)
    r_zip = client.post(
        "/api/backup/restore",
        content=zbuf.getvalue(),
        headers={**headers, "Content-Type": "application/octet-stream", "X-Backup-Filename": "backup.zip"},
    )
    assert r_zip.status_code == 200, r_zip.text

    # 4. 别名路由 /api/settings/backup/restore 放行 > 1MB 备份（不被 1MB 中间件误杀）
    big_payload = db.backup_payload()
    big_payload["transfers"] = [
        {"id": i + 1, "kind": "share", "share_code": f"big_{i}", "status": "success",
         "title": "x" * 1200, "created_at": 1700000000}
        for i in range(1100)
    ]
    big_bytes = json.dumps(big_payload).encode("utf-8")
    assert len(big_bytes) > 1 * 1024 * 1024
    r_big = client.post(
        "/api/settings/backup/restore",
        content=big_bytes,
        headers={**headers, "Content-Type": "application/json"},
    )
    assert r_big.status_code == 200, r_big.text
    assert r_big.json()["stats"]["transfers"] == 1100

    # 5. 非法负载返回 400 而非 500；解压炸弹上限生效
    r_bad = client.post("/api/backup/restore", json={"_meta": {"version": 1}}, headers=headers)
    assert r_bad.status_code == 400
    bomb_gz = gzip.compress(b"{" + b" " * 4096 + b"}")
    with pytest.raises(ValueError, match="上限"):
        db.parse_backup_bytes(bomb_gz, filename="bomb.json.gz", max_decompressed_bytes=1024)


def test_plan_a_media_tasks_backup_restore_and_retention():
    now = time.time()
    old = now - 35 * 86400
    recent = now - 3 * 86400
    db.execute("DELETE FROM media_tasks")
    rows = [
        ("scrape", 1, "f_nc", "NeedConfirm.mkv", "need_confirm", "/meta/nc", None, "movie", recent, recent),
        ("scrape", 1, "f_sk", "Skipped.mkv", "skipped", "/meta/sk", 102, "movie", old, old),
        ("scrape", 1, "f_q", "Queued.mkv", "queued", "/meta/q", 103, "tv", recent, recent),
        ("scrape", 1, "f_run", "Running.mkv", "running", "/meta/run", None, "movie", recent, recent),
        ("scrape", 1, "f_done_old", "DoneOld.mkv", "done", "/meta/done1", 105, "movie", old, old),
        ("scrape", 1, "f_fail_old", "FailOld.mkv", "failed", "/meta/fail1", 106, "movie", old, old),
        ("scrape", 1, "f_ign_old", "IgnOld.mkv", "ignored", "/meta/ign1", 107, "movie", old, old),
        ("scrape", 1, "f_done_rec", "DoneRecent.mkv", "done", "/meta/done2", 108, "movie", recent, recent),
    ]
    for r in rows:
        db.execute(
            "INSERT INTO media_tasks(task_kind, rule_id, file_id, orig_name, status, local_meta_dir, selected_tmdb_id, media_type, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            r,
        )

    # 1. JSON 备份仅包含待确认/待处理/跳过/运行中，不含 done/failed/ignored
    payload = db.backup_payload()
    exported_ids = {t["file_id"] for t in payload["media_tasks"]}
    assert exported_ids == {"f_nc", "f_sk", "f_q", "f_run"}

    # 2. 恢复后 running 归一化离开 running（无选定 TMDB 时回退 need_confirm），其它保留原始状态与元数据目录，且通过一致性校验
    db.execute("DELETE FROM media_tasks")
    stats = db.restore_payload(payload)
    assert stats["media_tasks"] == 4
    restored = {r["file_id"]: r for r in db.query("SELECT * FROM media_tasks")}
    assert restored["f_run"]["status"] == "skipped"
    assert restored["f_sk"]["status"] == "skipped"
    assert restored["f_sk"]["local_meta_dir"] == "/meta/sk"
    assert restored["f_sk"]["selected_tmdb_id"] == 102
    assert restored["f_q"]["status"] == "queued"
    assert not bot_commands._verify_restore(payload)

    db.execute("DELETE FROM media_tasks WHERE file_id='f_sk'")
    err = bot_commands._verify_restore(payload)
    assert err and "media_tasks" in err

    # 3. 30 天保留清理：仅清理超 30 天 done/failed/ignored，保留近期 finished 与超 90 天 skipped
    db.execute("DELETE FROM media_tasks")
    very_old = now - 90 * 86400
    for r in [
        ("scrape", 1, "k_done_old", "a.mkv", "done", old, old),
        ("scrape", 1, "k_fail_old", "b.mkv", "failed", old, old),
        ("scrape", 1, "k_ign_old", "c.mkv", "ignored", old, old),
        ("scrape", 1, "k_done_new", "d.mkv", "done", recent, recent),
        ("scrape", 1, "k_skip_old", "e.mkv", "skipped", very_old, very_old),
    ]:
        db.execute(
            "INSERT INTO media_tasks(task_kind, rule_id, file_id, orig_name, status, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            r,
        )
    cleanup_res = jobs._cleanup_once()
    assert len(cleanup_res) == 7
    assert cleanup_res[6] == 3
    remain = {r["file_id"] for r in db.query("SELECT file_id FROM media_tasks")}
    assert remain == {"k_done_new", "k_skip_old"}


def test_plan_b_migration_bundle_export_preview_and_restore(tmp_path):
    cred_files = {
        "115_cookie.txt": config.COOKIE_FILE,
        "tg_session.session": config.TG_SESSION_FILE.with_name(config.TG_SESSION_FILE.name + ".session"),
        "secret.key": config.SECRET_FILE,
    }
    saved_files = {k: (p.read_bytes() if p.exists() else None) for k, p in cred_files.items()}
    saved_pw = db.get_setting("web_password_hash", "")
    saved_meta_root = db.get_setting("media_local_meta_dir", "")

    src_meta = tmp_path / "src_meta"
    tgt_meta = tmp_path / "tgt_meta"
    movie_dir = src_meta / "Movies" / "TestFilm (2025)"
    movie_dir.mkdir(parents=True)
    (movie_dir / "movie.nfo").write_text("<movie><title>测试电影</title></movie>", encoding="utf-8")
    (movie_dir / "poster.jpg").write_bytes(b"\xff\xd8\xff\xe0FAKE_POSTER_DATA" * 16)
    tgt_meta.mkdir(parents=True)

    try:
        # 1. 准备源机器状态（设置、凭据文件、密钥、刮削任务、日志缓存）
        db.set_setting("web_password_hash", auth.hash_password("SrcPass123!"))
        db.set_setting("daily_limit", 88)
        db.set_setting("media_local_meta_dir", str(src_meta))
        cred_files["115_cookie.txt"].write_text("UID=src_115;CID=c1", encoding="utf-8")
        cred_files["tg_session.session"].write_bytes(b"SRC_TG_SESSION_BIN")
        cred_files["secret.key"].write_text("src_secret_key_00001111222233334455", encoding="utf-8")
        auth.clear_secret_cache()

        db.execute("INSERT INTO logs(level, module, message, created_at) VALUES('INFO', 'test', 'noise', 1000)")
        db.execute(
            "INSERT INTO media_tasks(task_kind, rule_id, file_id, orig_name, status, local_meta_dir, created_at, updated_at) "
            "VALUES('scrape', 1, 'bundle_f1', 'TestFilm.mkv', 'skipped', ?, 1700000000, 1700000000)",
            (str(movie_dir),),
        )

        headers = {"Authorization": f"Bearer {auth.make_token('admin')}"}

        # 2. 体积估算接口
        r_est = client.get("/api/backup/bundle/estimate", headers=headers)
        assert r_est.status_code == 200, r_est.text
        est = r_est.json()
        assert est["meta_nfo_count"] == 1
        assert est["meta_asset_count"] == 1
        assert est["pending_media_tasks"] == 1
        assert est["credentials_count"] >= 2

        # 3. 含凭据导出必须校验当前登录密码
        r_bad_pw = client.post(
            "/api/backup/bundle/export",
            json={"include_credentials": True, "meta_mode": "nfo", "slim_db": True, "password": "WrongPassword!"},
            headers=headers,
        )
        assert r_bad_pw.status_code == 400

        # 4. 导出仅 NFO（不含凭据、数据库瘦身）
        r_nfo = client.post(
            "/api/backup/bundle/export",
            json={"include_credentials": False, "meta_mode": "nfo", "slim_db": True},
            headers=headers,
        )
        assert r_nfo.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r_nfo.content), "r") as zf:
            names = set(zf.namelist())
            assert "manifest.json" in names
            assert "app.db" in names
            assert "media_meta/Movies/TestFilm (2025)/movie.nfo" in names
            assert "media_meta/Movies/TestFilm (2025)/poster.jpg" not in names
            assert not any(n.startswith("credentials/") for n in names)

        # 5. 导出完整迁移包（含海报 + 含凭据，密码正确）
        r_full = client.post(
            "/api/backup/bundle/export",
            json={"include_credentials": True, "meta_mode": "all", "slim_db": True, "password": "SrcPass123!"},
            headers=headers,
        )
        assert r_full.status_code == 200
        full_zip = r_full.content
        with zipfile.ZipFile(io.BytesIO(full_zip), "r") as zf:
            names = set(zf.namelist())
            assert "credentials/115_cookie.txt" in names
            assert "credentials/tg_session.session" in names
            assert "credentials/secret.key" in names
            assert "media_meta/Movies/TestFilm (2025)/movie.nfo" in names
            assert "media_meta/Movies/TestFilm (2025)/poster.jpg" in names
            # 图片采用 ZIP_STORED 不重复压缩
            assert zf.getinfo("media_meta/Movies/TestFilm (2025)/poster.jpg").compress_type == zipfile.ZIP_STORED

        # 6. 模拟目标机器环境并校验流式预览与恢复
        db.set_setting("web_password_hash", auth.hash_password("TgtPass999!"))
        db.set_setting("daily_limit", 12)
        db.set_setting("media_local_meta_dir", str(tgt_meta))
        cred_files["115_cookie.txt"].write_text("UID=tgt_only", encoding="utf-8")
        cred_files["tg_session.session"].write_bytes(b"TGT_SESSION")
        cred_files["secret.key"].write_text("tgt_secret_key_99998888777766665544", encoding="utf-8")
        auth.clear_secret_cache()
        db.execute("DELETE FROM media_tasks")
        tgt_headers = {"Authorization": f"Bearer {auth.make_token('admin')}"}

        r_prev = client.post(
            "/api/backup/bundle/preview",
            content=full_zip,
            headers={**tgt_headers, "Content-Type": "application/octet-stream"},
        )
        assert r_prev.status_code == 200, r_prev.text
        prev = r_prev.json()["preview"]
        assert prev["has_credentials"] is True
        assert prev["has_meta"] is True
        assert prev["meta_nfo_count"] == 1
        assert prev["meta_asset_count"] == 1

        # 6a. restore_credentials=false：保留目标机器凭据与密码，还原元数据并跨目录重映射 local_meta_dir
        r_keep_cred = client.post(
            "/api/backup/bundle/restore?restore_credentials=false&restore_meta=true",
            content=full_zip,
            headers={**tgt_headers, "Content-Type": "application/octet-stream"},
        )
        assert r_keep_cred.status_code == 200, r_keep_cred.text
        res_kc = r_keep_cred.json()
        assert res_kc["restored_credentials"] is False
        assert res_kc["restored_meta"] is True
        assert res_kc["meta_files_restored"] == 2
        assert cred_files["115_cookie.txt"].read_text(encoding="utf-8") == "UID=tgt_only"
        assert auth.verify_password("TgtPass999!", db.get_setting("web_password_hash"))
        assert db.get_setting("daily_limit") == 88
        # slim_db=True 时日志噪声已被剥离
        assert db.query("SELECT count(*) c FROM logs WHERE message='noise'", one=True)["c"] == 0
        remapped_dir = (tgt_meta / "Movies" / "TestFilm (2025)").resolve()
        assert (remapped_dir / "movie.nfo").is_file()
        assert (remapped_dir / "poster.jpg").is_file()
        task_row = db.query("SELECT local_meta_dir FROM media_tasks WHERE file_id='bundle_f1'", one=True)
        assert Path(task_row["local_meta_dir"]).resolve() == remapped_dir

        # 6b. restore_credentials=true：完整替换凭据与 secret.key，并返回可用新 JWT Token
        r_with_cred = client.post(
            "/api/backup/bundle/restore?restore_credentials=true&restore_meta=true",
            content=full_zip,
            headers={**tgt_headers, "Content-Type": "application/octet-stream"},
        )
        assert r_with_cred.status_code == 200, r_with_cred.text
        res_wc = r_with_cred.json()
        assert res_wc["restored_credentials"] is True
        assert res_wc.get("token")
        assert cred_files["115_cookie.txt"].read_text(encoding="utf-8") == "UID=src_115;CID=c1"
        assert cred_files["tg_session.session"].read_bytes() == b"SRC_TG_SESSION_BIN"
        assert auth.verify_password("SrcPass123!", db.get_setting("web_password_hash"))
        # 使用响应签发的新 token 可立即访问受保护接口
        r_auth_check = client.get("/api/settings", headers={"Authorization": f"Bearer {res_wc['token']}"})
        assert r_auth_check.status_code == 200

        # 7. 运行中禁止导入（400）
        fresh_headers = {"Authorization": f"Bearer {res_wc['token']}"}
        db.set_setting("running", True)
        r_running = client.post(
            "/api/backup/bundle/restore?restore_credentials=false&restore_meta=false",
            content=full_zip,
            headers={**fresh_headers, "Content-Type": "application/octet-stream"},
        )
        assert r_running.status_code == 400
        db.set_setting("running", False)

        # 8. Zip-Slip 路径穿越与非法压缩包拒绝（400）
        evil_buf = io.BytesIO()
        with zipfile.ZipFile(evil_buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"format_version": 1}))
            zf.writestr("app.db", b"not-sqlite")
            zf.writestr("media_meta/../../evil.txt", b"pwn")
        r_evil = client.post(
            "/api/backup/bundle/preview",
            content=evil_buf.getvalue(),
            headers={**fresh_headers, "Content-Type": "application/octet-stream"},
        )
        assert r_evil.status_code == 400

        # 9. 跨系统路径重映射（Linux 容器 /data/media_meta -> 当前平台目录）
        mapped = migration_bundle.remap_meta_path("/data/media_meta/Movies/Demo (2024)", "/data/media_meta", tgt_meta)
        assert Path(mapped).resolve() == (tgt_meta / "Movies" / "Demo (2024)").resolve()
    finally:
        for k, p in cred_files.items():
            if saved_files[k] is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(saved_files[k])
        auth.clear_secret_cache()
        db.set_setting("web_password_hash", saved_pw)
        db.set_setting("media_local_meta_dir", saved_meta_root)
        db.set_setting("running", False)
        db.execute("DELETE FROM media_tasks")
        db.execute("DELETE FROM logs WHERE message='noise'")

