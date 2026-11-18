"""网页手动转存（指定分类 / 直接指定 115 目录）测试。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_manual_"))

import pytest
from fastapi import HTTPException

from backend import database as db
from backend.api.manage import transfers as transfers_api


@pytest.fixture(autouse=True)
def _init_db():
    db.init()
    db.execute("DELETE FROM transfers")
    db.execute("DELETE FROM messages")
    db.set_setting("unclassified_action", "skip")
    db.set_setting("subscription_only", False)
    db.set_setting("offline_enabled", True)
    yield


def test_manual_transfer_with_explicit_target_dir_bypasses_unclassified_and_sub_only():
    db.set_setting("subscription_only", True)
    res = transfers_api.manual_transfer(
        {
            "text": "https://115cdn.com/s/manual001?password=abcd",
            "target_dir": "/自定义资料/2026/",
        },
        _=None,
    )
    assert res["ok"] is True
    assert res["result"]["queued"] == 1
    row = db.query("SELECT * FROM transfers WHERE share_code='manual001'", one=True)
    assert row is not None
    assert row["status"] == "queued"
    assert row["target_dir"] == "/自定义资料/2026"
    assert row["channel"] == "web"


def test_manual_transfer_with_explicit_target_dir_for_offline():
    res = transfers_api.manual_transfer(
        {
            "text": "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
            "target_dir": "/云下载自选",
        },
        _=None,
    )
    assert res["ok"] is True
    assert res["result"]["queued"] == 1
    row = db.query("SELECT * FROM transfers WHERE kind='offline'", one=True)
    assert row is not None
    assert row["target_dir"] == "/云下载自选"
    assert row["status"] == "queued"


def test_manual_transfer_with_category_uses_route():
    db.execute("INSERT OR REPLACE INTO routes(category, target_dir, offline_dir) VALUES('学习', '/学习库', '')")
    res = transfers_api.manual_transfer(
        {
            "text": "https://115cdn.com/s/manualcat1?password=1234",
            "category": "学习",
        },
        _=None,
    )
    assert res["ok"] is True
    assert res["result"]["queued"] == 1
    row = db.query("SELECT * FROM transfers WHERE share_code='manualcat1'", one=True)
    assert row["category"] == "学习"
    assert row["target_dir"] == "/学习库"


@pytest.mark.parametrize("bad_dir", ["/", "///", "relative/dir", "/a/../b", "/" + "a" * 205])
def test_manual_transfer_rejects_invalid_target_dir(bad_dir):
    with pytest.raises(HTTPException) as exc:
        transfers_api.manual_transfer(
            {"text": "https://115cdn.com/s/manualbad?password=1234", "target_dir": bad_dir},
            _=None,
        )
    assert exc.value.status_code == 400
