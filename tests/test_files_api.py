import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_files_test_"))

from backend import auth, database as db
from backend.api import files as files_api
from backend.main import app


@pytest.fixture(autouse=True)
def _reset():
    db.init()
    files_api._clear_stat_cache()
    files_api._last_stat_at = 0.0


def _authed_client() -> TestClient:
    token = auth.make_token("admin")
    return TestClient(app, cookies={"sa_auth": token})


def test_list_files_sort_filter_and_preserve_order():
    mock_client = MagicMock()
    mock_client.fs_files.return_value = {
        "state": True,
        "count": 2,
        "folder_count": 1,
        "file_count": 1,
        "data": [
            {"cid": "20", "n": "Z目录", "fc": "0", "user_utime": 200},
            {"fid": "10", "n": "A.mkv", "fc": "1", "s": 1024, "user_utime": 100, "user_ptime": 90},
        ],
        "path": [["根目录", "0"], ["媒体库", "100"]],
    }

    with patch("backend.pan115.client.check_login", return_value={"ok": True}), \
            patch("backend.pan115.client._get_client", return_value=mock_client):
        tc = _authed_client()
        r = tc.get("/api/files/list", params={
            "cid": 100, "sort": "mtime", "order": "desc", "file_type": 4, "page": 1, "size": 50,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        assert body["folder_count"] == 1
        assert body["file_count"] == 1
        # 保留 115 服务端顺序（不再强制按名称升序重排）
        assert [it["name"] for it in body["items"]] == ["Z目录", "A.mkv"]
        assert body["items"][1]["ctime"] == 90

        payload = mock_client.fs_files.call_args.args[0]
        assert payload["o"] == "user_utime"
        assert payload["asc"] == 0
        assert payload["type"] == 4
        assert payload["cur"] == 1
        assert payload["count_folders"] == 1

        # 不支持按大小排序：非法 sort=size 自动回落到 file_name
        r2 = tc.get("/api/files/list", params={"cid": 100, "sort": "size", "order": "desc"})
        assert r2.status_code == 200
        payload2 = mock_client.fs_files.call_args.args[0]
        assert payload2["o"] == "file_name"


def test_list_files_search_scope_dir_and_all():
    mock_client = MagicMock()
    mock_client.fs_search.return_value = {"state": True, "count": 0, "data": []}

    with patch("backend.pan115.client.check_login", return_value={"ok": True}), \
            patch("backend.pan115.client._get_client", return_value=mock_client):
        tc = _authed_client()

        r1 = tc.get("/api/files/list", params={"cid": 555, "search": "太阳雨", "search_scope": "dir", "sort": "mtime", "order": "desc"})
        assert r1.status_code == 200
        p1 = mock_client.fs_search.call_args.args[0]
        assert p1["cid"] == 555
        assert p1["o"] == "user_utime"
        assert p1["asc"] == 0

        r2 = tc.get("/api/files/list", params={"cid": 555, "search": "太阳雨", "search_scope": "all"})
        assert r2.status_code == 200
        p2 = mock_client.fs_search.call_args.args[0]
        assert "cid" not in p2


def test_stat_dir_on_demand_cache_and_invalidation():
    mock_client = MagicMock()
    # 115 /category/get 成功时默认不带 state 字段
    mock_client.fs_category_get.return_value = {
        "count": "12",
        "folder_count": "2",
        "size": "42.35GB",
        "size_byte": 45472959123,
        "file_name": "今天是太阳雨 (2026)",
        "paths": [
            {"file_id": 0, "file_name": "根目录"},
            {"file_id": 2914617898344070548, "file_name": "最近接收"},
        ],
    }
    mock_client.fs_delete.return_value = {"state": True}

    with patch("backend.pan115.client.check_login", return_value={"ok": True}), \
            patch("backend.pan115.client._get_client", return_value=mock_client):
        tc = _authed_client()

        # 根目录 cid=0 不允许统计
        assert tc.get("/api/files/stat", params={"cid": 0}).status_code == 422

        # 首次查询打 115
        r1 = tc.get("/api/files/stat", params={"cid": 307853})
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["size"] == 45472959123
        assert d1["file_count"] == 12
        assert d1["folder_count"] == 2
        assert d1["parent_name"] == "最近接收"
        assert d1["full_path"] == "/最近接收/今天是太阳雨 (2026)"
        assert d1["cached"] is False
        assert mock_client.fs_category_get.call_count == 1

        # 再次查询命中缓存，不重复请求 115
        r2 = tc.get("/api/files/stat", params={"cid": 307853})
        assert r2.status_code == 200
        assert r2.json()["cached"] is True
        assert mock_client.fs_category_get.call_count == 1

        # 写操作后缓存清空
        r_del = tc.post("/api/files/delete", json={"ids": ["999"]})
        assert r_del.status_code == 200
        r3 = tc.get("/api/files/stat", params={"cid": 307853})
        assert r3.status_code == 200
        assert r3.json()["cached"] is False
        assert mock_client.fs_category_get.call_count == 2

        # 115 返回失败态时正确返回 400
        mock_client.fs_category_get.return_value = {"state": False, "errno": 70005, "error": "目录不存在"}
        r_err = tc.get("/api/files/stat", params={"cid": 888, "refresh": True})
        assert r_err.status_code == 400
        assert "目录不存在" in r_err.json()["detail"]


def test_get_item_name_cache_and_root():
    mock_client = MagicMock()
    mock_client.fs_file_skim.return_value = {
        "state": True,
        "data": [{"file_id": "2914617898344070548", "file_name": "最近接收"}],
    }

    with patch("backend.pan115.client.check_login", return_value={"ok": True}), \
            patch("backend.pan115.client._get_client", return_value=mock_client):
        tc = _authed_client()

        r0 = tc.get("/api/files/name", params={"cid": 0})
        assert r0.status_code == 200
        assert r0.json()["name"] == "根目录"
        assert mock_client.fs_file_skim.call_count == 0

        r1 = tc.get("/api/files/name", params={"cid": 2914617898344070548})
        assert r1.status_code == 200
        assert r1.json()["name"] == "最近接收"
        assert r1.json()["cached"] is False
        assert mock_client.fs_file_skim.call_count == 1

        # 命中缓存
        r2 = tc.get("/api/files/name", params={"cid": 2914617898344070548})
        assert r2.status_code == 200
        assert r2.json()["cached"] is True
        assert mock_client.fs_file_skim.call_count == 1


def test_recyclebin_list_and_revert():
    mock_client = MagicMock()
    mock_client.recyclebin_list.return_value = {
        "state": True,
        "count": "2",
        "data": [
            {
                "id": "9001",
                "file_name": "误删电影.mkv",
                "type": "1",
                "file_size": "2147483648",
                "dtime": "1731800000",
                "parent_name": "最近接收",
                "cid": "2914617898344070548",
            },
            {
                "id": "9002",
                "file_name": "误删剧集",
                "type": "2",
                "file_size": "0",
                "dtime": "1731800100",
                "parent_name": "电视剧",
                "cid": "1001",
            },
        ],
    }
    mock_client.recyclebin_revert.return_value = {"state": True}

    with patch("backend.pan115.client.check_login", return_value={"ok": True}), \
            patch("backend.pan115.client._get_client", return_value=mock_client):
        tc = _authed_client()

        r_list = tc.get("/api/files/recyclebin", params={"page": 1, "size": 50})
        assert r_list.status_code == 200
        body = r_list.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2
        assert body["items"][0]["id"] == "9001"
        assert body["items"][0]["is_dir"] is False
        assert body["items"][0]["parent_name"] == "最近接收"
        assert body["items"][1]["id"] == "9002"
        assert body["items"][1]["is_dir"] is True

        r_rev = tc.post("/api/files/recyclebin/revert", json={"ids": ["9001", "9002"]})
        assert r_rev.status_code == 200
        assert r_rev.json() == {"ok": True, "count": 2}
        mock_client.recyclebin_revert.assert_called_once_with(["9001", "9002"])
