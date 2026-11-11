import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend import auth, database as db
from backend.api import manage

client = TestClient(app)


@pytest.fixture(autouse=True)
def init_database():
    db.init()
    manage._POSTER_CACHE.clear()


def test_poster_auth_levels():
    token = auth.make_token("admin")

    # 1. 未带任何凭据 -> 401
    r = client.get("/api/tmdb/poster?path=/test.jpg")
    assert r.status_code == 401

    # 2. 错误 token -> 401
    r = client.get("/api/tmdb/poster?path=/test.jpg&token=bad_token_123")
    assert r.status_code == 401

    # 预设缓存以验证各种鉴权方式均可通过
    fake_png = b"\x89PNG\r\n\x1a\nfakeimagecontent"
    manage._POSTER_CACHE["/test.jpg"] = (fake_png, "image/png")

    # 3. Query 参数携带 token
    r = client.get(f"/api/tmdb/poster?path=/test.jpg&token={token}")
    assert r.status_code == 200
    assert r.content == fake_png
    assert r.headers.get("X-Poster-Cache") == "HIT"

    # 4. Bearer Header 鉴权
    r = client.get("/api/tmdb/poster?path=/test.jpg", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

    # 5. Cookie 鉴权
    r = client.get("/api/tmdb/poster?path=/test.jpg", cookies={"sa_poster": token})
    assert r.status_code == 200


def test_poster_path_validation():
    token = auth.make_token("admin")

    # 路径穿越测试 -> 400
    r = client.get(f"/api/tmdb/poster?path=/../../etc/passwd&token={token}")
    assert r.status_code == 400

    # 非法扩展名 -> 400
    r = client.get(f"/api/tmdb/poster?path=/test.exe&token={token}")
    assert r.status_code == 400

    # 缺少前置斜杠时自动补全规范化
    manage._POSTER_CACHE["/normalized.jpg"] = (b"image_data", "image/jpeg")
    r = client.get(f"/api/tmdb/poster?path=normalized.jpg&token={token}")
    assert r.status_code == 200
    assert r.content == b"image_data"


def test_poster_proxy_fallback_to_direct():
    token = auth.make_token("admin")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "image/jpeg"}
    mock_resp.content = b"downloaded_poster_bytes"

    # 模拟第一次带代理调用失败，降级为直连调用成功
    call_count = {"count": 0}

    def fake_httpx_get(url, headers=None, proxy=None, timeout=None):
        call_count["count"] += 1
        if proxy is not None:
            # 代理抛异常（模拟代理宕机或无法连接）
            raise RuntimeError("Proxy connection refused")
        return mock_resp

    with patch("backend.worker.jobs._tg_proxy", return_value="http://127.0.0.1:8888"):
        with patch("httpx.get", side_effect=fake_httpx_get):
            r = client.get(f"/api/tmdb/poster?path=/fallback.jpg&token={token}")
            assert r.status_code == 200
            assert r.content == b"downloaded_poster_bytes"
            assert call_count["count"] == 2  # 第一次代理失败，第二次直连成功
            # 验证写入缓存
            assert "/fallback.jpg" in manage._POSTER_CACHE
