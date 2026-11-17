"""扫描索引（scan_index）测试。

覆盖：
- 根目录 "/" 拒绝即时全盘预览
- 目录索引 TTL 内二次读取 0 请求；写操作标脏后才会重扫
- 增量扫描时消失的条目软删除（gone_at）
- 基于索引的 sha1 重复分组
- 异步预览任务（start → status → done）生命周期
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_scan_"))

import pytest

from backend import database as db
from backend.worker import scan_index


@pytest.fixture(autouse=True)
def _init_db():
    db.init()
    db.execute("DELETE FROM scan_entries")
    db.execute("DELETE FROM scan_dirs")
    yield


def _file(fid, name, sha1="", size=10, uptime=1.0, is_dir=False):
    return {"id": fid, "name": name, "is_dir": is_dir, "sha1": sha1,
            "size": size, "uptime": uptime}


def test_root_scan_rejected():
    ok, msg = scan_index.validate_scan_root("/")
    assert not ok and "根目录" in msg
    assert scan_index.validate_scan_root("/电影")[0]


def test_index_ttl_and_dirty(monkeypatch):
    calls = []
    state = {"entries": [_file("f1", "a.mkv", "shaA", 100)]}

    def fake_list(cid, limit=2000):
        calls.append(int(cid))
        return list(state["entries"])

    monkeypatch.setattr(scan_index.client, "list_entries", fake_list)
    ctx = scan_index.ScanContext("test")

    first = scan_index.list_dir_cached(10, "/电影", ctx)
    assert [e["id"] for e in first] == ["f1"]
    assert len(calls) == 1

    # TTL 内且未变脏：直接读索引，0 请求
    second = scan_index.list_dir_cached(10, "/电影", ctx)
    assert [e["id"] for e in second] == ["f1"]
    assert len(calls) == 1

    # 写操作标脏后重扫，并能发现新增条目、软删消失条目
    scan_index.mark_dirty_for_file_ids(["f1"])
    state["entries"] = [_file("f2", "b.mkv", "shaB", 200)]
    third = scan_index.list_dir_cached(10, "/电影", ctx)
    assert [e["id"] for e in third] == ["f2"]
    assert len(calls) == 2
    row = db.query("SELECT gone_at FROM scan_entries WHERE cid=10 AND file_id='f1'", one=True)
    assert row and row["gone_at"] is not None


def test_dedupe_groups_from_index(monkeypatch):
    entries = [
        _file("a1", "电影.mkv", "shaX", 100, uptime=3),
        _file("a2", "电影 副本.mkv", "shaX", 100, uptime=2),
        _file("a3", "唯一.mkv", "shaY", 100, uptime=1),
        _file("a4", "无sha.mkv", "", 100),
        _file("d1", "子目录", "", 0, is_dir=True),
    ]
    monkeypatch.setattr(scan_index.client, "list_entries",
                        lambda cid, limit=2000: entries if int(cid) == 5 else [])
    ctx = scan_index.ScanContext("test")
    files, cut = scan_index.walk_files(5, "/媒体库", True, 8, ctx, collect=False)
    assert files == [] and cut == 0

    stats = scan_index.dedupe_subtree_stats("/媒体库", 0)
    assert stats["scanned"] == 4 and stats["no_sha1"] == 1

    groups = scan_index.dedupe_groups("/媒体库", 0)
    assert len(groups) == 1
    assert groups[0]["sha1"] == "shaX"
    assert {it["file_id"] for it in groups[0]["items"]} == {"a1", "a2"}

    # 前缀不越界：/媒体库2 不应命中 /媒体库 的条目
    assert scan_index.dedupe_groups("/媒体库2", 0) == []


def test_preview_job_lifecycle():
    async def runner(ctx):
        ctx.add_files(3)
        return {"ok": True, "value": 42}

    res = scan_index.start_job("test_job", runner)
    assert res["ok"] and res["job_id"]
    st = scan_index.job_status(res["job_id"])
    assert st["state"] == "done"
    assert st["files"] == 3
    assert st["result"]["value"] == 42


def test_scope_guard_tiers(monkeypatch):
    # 索引为空：无法预估，放行
    assert scan_index.scope_guard(["/电影"]) is None
    assert scan_index.estimate_scope(["/电影"])["known"] is False

    # 小范围：放行
    db.execute("INSERT INTO scan_dirs(cid,path,entry_count) VALUES(1,'/电影',2)")
    db.execute(
        "INSERT INTO scan_entries(cid,file_id,name,is_dir,path) VALUES(1,'f1','a.mkv',0,'/电影')")
    assert scan_index.scope_guard(["/电影"]) is None

    # 超过 2 万：先确认；确认后放行
    monkeypatch.setattr(scan_index, "SCOPE_CONFIRM_DIRS", 0)
    est = scan_index.estimate_scope(["/电影"])
    assert est["known"] and est["tier"] == "confirm" and est["dirs"] == 1
    guard = scan_index.scope_guard(["/电影"])
    assert guard and guard["need_confirm"] and "轮扫描" in guard["msg"]
    assert scan_index.scope_guard(["/电影"], confirm_large=True) is None

    # 超过 10 万：即使确认也拒绝
    monkeypatch.setattr(scan_index, "SCOPE_CONFIRM_DIRS", 0)
    monkeypatch.setattr(scan_index, "SCOPE_REJECT_DIRS", 0)
    guard = scan_index.scope_guard(["/电影"], confirm_large=True)
    assert guard and guard["reject"] and "拒绝" in guard["msg"]


def test_estimate_scope_dedupes_nested_roots():
    db.execute("INSERT INTO scan_dirs(cid,path,entry_count) VALUES(2,'/电影',1)")
    db.execute("INSERT INTO scan_dirs(cid,path,entry_count) VALUES(3,'/电影/合集',1)")
    est = scan_index.estimate_scope(["/电影", "/电影/合集"])
    assert est["dirs"] == 2  # 嵌套根只统计一次，不重复计数


def test_client_early_stop_flag(monkeypatch):
    full = [{"fid": f"f{i}", "file_name": f"{i}.mkv", "fc": 1, "s": 1, "sha": "", "upt": 1}
            for i in range(1150)]

    def fake_call(fn, params):
        return {"state": True, "data": [dict(x) for x in pages["data"]]}

    pages = {"data": full}
    fake_client = type("FakeClient", (), {"fs_files": staticmethod(lambda *a, **k: None)})()
    monkeypatch.setattr(scan_index.client, "_get_client", lambda: fake_client)
    monkeypatch.setattr(scan_index.client, "pan_call", fake_call)
    known = {e["fid"] for e in full}

    # 整页（1150 条）全部已知：提前停止，且标记 early_stopped（更旧页未观察）
    state: dict = {}
    out = scan_index.client.list_entries(9, limit=2000, known_ids=known, state=state)
    assert len(out) == 1150
    assert state["complete"] is True and state["early_stopped"] is True

    # 不足一页且全部已知：说明没有更旧条目，不算提前停止
    pages["data"] = full[:3]
    state2: dict = {}
    scan_index.client.list_entries(9, limit=2000, known_ids=known, state=state2)
    assert state2["complete"] is True and state2["early_stopped"] is False


def test_early_stop_keeps_unseen_entries(monkeypatch):
    db.execute("INSERT INTO scan_entries(cid,file_id,name,is_dir,path,seen_at) "
               "VALUES(20,'old','older.mkv',0,'/电影',1)")

    def fake_list(cid, limit=2000, known_ids=None, state=None, before_request=None):
        if before_request is not None:
            before_request()
        if state is not None:
            state.update({"complete": True, "early_stopped": True, "requests": 1})
        return [_file("new1", "new.mkv")]

    monkeypatch.setattr(scan_index.client, "list_entries", fake_list)
    scan_index.list_dir_cached(20, "/电影", scan_index.ScanContext("test"))

    # 提前停止时未翻到的旧条目不能被软删
    old = db.query("SELECT gone_at FROM scan_entries WHERE cid=20 AND file_id='old'", one=True)
    assert old and old["gone_at"] is None
    new = db.query("SELECT gone_at FROM scan_entries WHERE cid=20 AND file_id='new1'", one=True)
    assert new and new["gone_at"] is None
