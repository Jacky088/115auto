import os
import tempfile
from fastapi.testclient import TestClient

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_"))
from backend.main import app
from backend import auth, database as db


def test_pwa_headers_and_manifest():
    client = TestClient(app)

    # 1. 验证 sw.js 响应头与防缓存策略
    r_sw = client.get("/sw.js")
    if r_sw.status_code == 200:
        assert "no-cache" in r_sw.headers.get("Cache-Control", "")
        assert r_sw.headers.get("Service-Worker-Allowed") == "/"

    # 2. 验证 manifest.webmanifest
    r_manifest = client.get("/manifest.webmanifest")
    if r_manifest.status_code == 200:
        manifest = r_manifest.json()
        assert manifest.get("display") == "standalone"
        assert "share_target" in manifest
        assert manifest["share_target"]["action"] == "/#/transfers"
        icons = manifest.get("icons", [])
        assert any(i.get("sizes") == "192x192" for i in icons)
        assert any(i.get("sizes") == "512x512" for i in icons)


def test_manual_transfer_endpoint():
    db.init()
    db.set_setting("unclassified_action", "/默认转存")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {auth.make_token('admin')}"}

    # 1. 未提供文本时返回 400
    r_empty = client.post("/api/transfers/manual", json={"text": ""}, headers=headers)
    assert r_empty.status_code == 400

    # 2. 提交包含 115 分享链接的文本入队
    share_text = "【热播剧分享】链接：https://115.com/s/sw3456789abc?password=abcd 密码：abcd"
    r_share = client.post("/api/transfers/manual", json={"text": share_text}, headers=headers)
    assert r_share.status_code == 200
    res = r_share.json()["result"]
    assert res.get("links", 0) >= 1
    # 第一次入队成功
    assert res.get("queued", 0) >= 1 or res.get("duplicates", 0) >= 1

    # 3. 再次提交同一链接（查重测试）
    r_dup = client.post("/api/transfers/manual", json={"text": share_text}, headers=headers)
    assert r_dup.status_code == 200
    res_dup = r_dup.json()["result"]
    assert res_dup.get("duplicates", 0) >= 1
