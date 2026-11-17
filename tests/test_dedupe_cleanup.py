"""重复文件清理（sha1 判重）核心逻辑测试。

覆盖：
- 仅 sha1 相同才成组；sha1 为空、小于阈值、唯一文件均不参与
- 仅当前层 / 含子目录两种扫描范围
- 整组被勾选删除时强制保留第一条（绝不删空整组）
- 预览到执行重新校验 file_id + sha1 + size，失效项跳过
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_dedupe_"))

import pytest

from backend import database as db
from backend.worker import dedupe_worker


@pytest.fixture(autouse=True)
def _init_db():
    db.init()
    db.execute("DELETE FROM dedupe_rules")
    db.execute("DELETE FROM dedupe_records")
    yield


def _mk_rule(source="/媒体库", include_subdirs=1, min_size_mb=10):
    return db.insert(
        "INSERT INTO dedupe_rules(name,source_dir,include_subdirs,min_size_mb,enabled,created_at) "
        "VALUES(?,?,?,?,1,?)",
        ("测试清理", source, include_subdirs, min_size_mb, time.time()),
    )


def _file(fid, name, sha1, size_mb=100):
    return {"id": fid, "name": name, "is_dir": False, "sha1": sha1, "size": size_mb * 1024 * 1024}


def test_scan_groups_only_same_sha1(monkeypatch):
    rid = _mk_rule(min_size_mb=10)
    root = [
        _file("f1", "电影A.mkv", "shaA", 100),
        _file("f2", "电影A 副本.mkv", "shaA", 100),
        _file("f3", "无sha1.mkv", "", 100),
        _file("f4", "唯一.mkv", "shaUnique", 100),
        _file("f5", "小文件.mkv", "shaA", 5),
        {"id": "9", "name": "子目录", "is_dir": True, "sha1": "", "size": 0},
    ]
    monkeypatch.setattr(dedupe_worker.client, "resolve_dir_cid", lambda path: 1)
    monkeypatch.setattr(
        dedupe_worker.client, "list_entries",
        lambda cid, limit=2000: root if int(cid) == 1 else [],
    )

    rules, groups, stats = dedupe_worker._scan_rules_sync([rid])
    assert [r["id"] for r in rules] == [rid]
    assert len(groups) == 1
    g = groups[0]
    assert g["sha1"] == "shaA"
    assert g["size"] == 100 * 1024 * 1024
    # f5 小于阈值、f3 无 sha1、f4 唯一，均不参与；f1/f2 同层
    ids = {it["file_id"] for it in g["items"]}
    assert ids == {"f1", "f2"}
    assert g["freeable"] == 100 * 1024 * 1024
    assert stats["scanned"] == 5
    assert stats["no_sha1"] == 1
    assert stats["small_skipped"] == 1


def test_scan_include_subdirs(monkeypatch):
    rid = _mk_rule(include_subdirs=1, min_size_mb=0)
    root = [
        _file("f1", "电影A.mkv", "shaA", 100),
        {"id": "9", "name": "子目录", "is_dir": True, "sha1": "", "size": 0},
    ]
    sub = [_file("f6", "电影A 子目录副本.mkv", "shaA", 100)]
    monkeypatch.setattr(dedupe_worker.client, "resolve_dir_cid", lambda path: 1)
    monkeypatch.setattr(
        dedupe_worker.client, "list_entries",
        lambda cid, limit=2000: root if int(cid) == 1 else (sub if int(cid) == 9 else []),
    )
    _, groups, _ = dedupe_worker._scan_rules_sync([rid])
    assert len(groups) == 1
    assert {it["file_id"] for it in groups[0]["items"]} == {"f1", "f6"}


def test_scan_current_layer_only(monkeypatch):
    rid = _mk_rule(include_subdirs=0, min_size_mb=0)
    calls = {"sub": 0}

    def fake_list(cid, limit=2000):
        if int(cid) == 1:
            return [
                _file("f1", "电影A.mkv", "shaA", 100),
                {"id": "d1", "name": "子目录", "is_dir": True, "sha1": "", "size": 0},
            ]
        calls["sub"] += 1
        return [_file("f6", "电影A 子目录副本.mkv", "shaA", 100)]

    monkeypatch.setattr(dedupe_worker.client, "resolve_dir_cid", lambda path: 1)
    monkeypatch.setattr(dedupe_worker.client, "list_entries", fake_list)
    _, groups, _ = dedupe_worker._scan_rules_sync([rid])
    assert groups == []
    assert calls["sub"] == 0


def _group(items):
    return [{"sha1": "shaA", "size": items[0]["size"], "freeable": items[0]["size"] * (len(items) - 1),
             "keep_id": items[0]["file_id"], "items": items}]


def test_select_deletions_rejects_whole_group():
    items = [
        {"file_id": "f1", "file_name": "a.mkv", "file_path": "/a.mkv", "sha1": "shaA", "size": 100, "rule_id": 1},
        {"file_id": "f2", "file_name": "b.mkv", "file_path": "/b.mkv", "sha1": "shaA", "size": 100, "rule_id": 1},
    ]
    valid, stale, protected = dedupe_worker._select_valid_deletions(
        _group(items),
        [{"file_id": "f1", "sha1": "shaA", "size": 100}, {"file_id": "f2", "sha1": "shaA", "size": 100}],
    )
    # 整组都被勾选：强制保留第一条 f1，仅允许删除 f2
    assert [v["file_id"] for v in valid] == ["f2"]
    assert stale == 0
    assert protected == 1


def test_select_deletions_stale_after_preview_change():
    items = [
        {"file_id": "f1", "file_name": "a.mkv", "file_path": "/a.mkv", "sha1": "shaA", "size": 100, "rule_id": 1},
        {"file_id": "f2", "file_name": "b.mkv", "file_path": "/b.mkv", "sha1": "shaA", "size": 100, "rule_id": 1},
    ]
    valid, stale, _ = dedupe_worker._select_valid_deletions(
        _group(items),
        [
            {"file_id": "f2", "sha1": "shaA", "size": 100},
            {"file_id": "f1", "sha1": "shaA", "size": 999},   # 大小已变化
            {"file_id": "gone", "sha1": "shaA", "size": 100},  # 已不存在
        ],
    )
    assert [v["file_id"] for v in valid] == ["f2"]
    assert stale == 2


def test_execute_deletes_only_non_keep_and_writes_ledger(monkeypatch):
    import asyncio

    rid = _mk_rule(source="/媒体库", include_subdirs=1, min_size_mb=0)
    db.set_setting("running", True)
    files = [
        _file("f1", "电影A.mkv", "shaA", 100),
        _file("f2", "电影A 副本.mkv", "shaA", 100),
    ]
    monkeypatch.setattr(dedupe_worker.client, "resolve_dir_cid", lambda path: 1)
    monkeypatch.setattr(dedupe_worker.client, "list_entries", lambda cid, limit=2000: files)
    deleted_batches: list[list[str]] = []

    def fake_delete(fids):
        deleted_batches.append(list(fids))

    monkeypatch.setattr(dedupe_worker.client, "delete_entries", fake_delete)

    async def fake_run(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(dedupe_worker.client, "run_pan_thread", fake_run)
    monkeypatch.setattr(dedupe_worker.transfer_gate, "try_begin_exclusive", lambda mode: (True, ""))
    monkeypatch.setattr(dedupe_worker.transfer_gate, "clear_cancel", lambda: None)
    monkeypatch.setattr(dedupe_worker.transfer_gate, "resume_all", lambda: None)
    monkeypatch.setattr(dedupe_worker.transfer_gate, "set_media_progress", lambda info: None)
    monkeypatch.setattr(dedupe_worker.transfer_gate, "is_cancel_requested", lambda: False)

    async def fake_drain(max_wait_seconds=3600.0, mode="auto_move"):
        return True

    monkeypatch.setattr(dedupe_worker.transfer_gate, "prepare_and_drain_transfers", fake_drain)

    res = asyncio.run(dedupe_worker._run_dedupe(
        rid, [{"file_id": "f2", "sha1": "shaA", "size": 100 * 1024 * 1024}], "ded_test01"))
    assert res["ok"] is True
    assert res["deleted"] == 1
    assert deleted_batches == [["f2"]]
    rows = db.query("SELECT * FROM dedupe_records WHERE batch_id='ded_test01'")
    assert len(rows) == 1
    assert rows[0]["file_id"] == "f2"
    assert rows[0]["status"] == "deleted"
    rule = db.query("SELECT * FROM dedupe_rules WHERE id=?", (rid,), one=True)
    assert int(rule["last_deleted_count"]) == 1


def test_record_table_created():
    cols = {r["name"] for r in db.query("PRAGMA table_info(dedupe_records)")}
    assert {"batch_id", "file_id", "file_name", "file_path", "sha1", "size", "status"} <= cols
