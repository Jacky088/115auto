"""整理账本（移动记录/撤销）与预览（dry_run）功能测试集。

覆盖：
- move_matched_files_for_rule 的 dry_run 计划模式（不触发任何 115 写操作）与 moved_out 明细收集
- execute_auto_move 落账（organized_records action='auto_move'）与 90 天保留清理
- 撤销：单条/整批、位置漂移防护（不盲移）、已在原位置直标、停用规则与总开关联动
- 老库 organized_records 缺 undo_note 列时的自动补列迁移
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="autosave_test_ledger_")

from backend import database as db
from backend.core import transfer_gate
from backend.pan115 import client as pan
from backend.worker import jobs

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  [{detail}]" if detail and not cond else ""))


db.init()


def _mk_rule(source="/最近接收", target="/电视剧", keyword="", mode="default", enabled=1):
    rid = db.insert(
        "INSERT INTO auto_move_rules(source_dir,target_dir,keyword,mode,enabled,created_at,last_run_at,last_moved_count)"
        " VALUES(?,?,?,?,?,?,NULL,0)", (source, target, keyword, mode, enabled, time.time()))
    return rid


def _mk_record(fid="f1", name="折月亮.S01E01.mkv", rule_id=1, batch="b001",
               from_cid=100, dest_cid=200, undone=0, age_days=0, orig_path="/最近接收"):
    return db.insert(
        "INSERT INTO organized_records(task_id,file_id,parent_cid,size,orig_path,orig_name,"
        "dest_cid,dest_path,dest_name,action,batch_id,undone,undo_note,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,'auto_move',?,?,?,'',?)",
        (rule_id, fid, from_cid, 1024, orig_path, name, dest_cid,
         "/电视剧/折月亮 (2026)/Season 1", "auto_move" if False else "auto_move",  # action
         batch, undone, time.time() - age_days * 86400))


def test_migration_undo_note_column():
    """新库直建与老库迁移都应具备 undo_note 列。"""
    cols = {r["name"] for r in db.query("PRAGMA table_info(organized_records)")}
    check("新库 organized_records 含 undo_note 列", "undo_note" in cols)


def test_old_db_migration_adds_undo_note():
    """模拟老库（无 undo_note 列）：db.init 迁移后应自动补列且数据保留。"""
    old_dir = tempfile.mkdtemp(prefix="autosave_test_ledger_old_")
    old_db = os.path.join(old_dir, "app.db")
    conn = sqlite3.connect(old_db)
    # 旧版 organized_records 结构（无 undo_note）
    conn.execute("""CREATE TABLE organized_records(
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER DEFAULT 0, file_id TEXT DEFAULT '',
        parent_cid TEXT DEFAULT '', sha1 TEXT DEFAULT '', size INTEGER DEFAULT 0,
        orig_path TEXT DEFAULT '', orig_name TEXT DEFAULT '', dest_cid TEXT DEFAULT '',
        dest_path TEXT DEFAULT '', dest_name TEXT DEFAULT '', action TEXT DEFAULT '',
        tmdb_id INTEGER, batch_id TEXT DEFAULT '', undone INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL)""")
    conn.execute("INSERT INTO organized_records(file_id,orig_name,created_at) VALUES('legacy1','老记录.mkv',1)")
    conn.commit()
    conn.close()

    import importlib
    from backend import config
    with patch.object(config, "DATA_DIR", __import__("pathlib").Path(old_dir)), \
         patch.object(config, "DB_PATH", __import__("pathlib").Path(old_db)):
        importlib.reload(db)
        db.init()
        cols = {r["name"] for r in db.query("PRAGMA table_info(organized_records)")}
        check("老库迁移后补出 undo_note 列", "undo_note" in cols, str(cols))
        row = db.query("SELECT orig_name FROM organized_records WHERE file_id='legacy1'", one=True)
        check("迁移不丢失既有账本数据", bool(row) and row["orig_name"] == "老记录.mkv")
    # 还原测试库连接（reload 指向了临时老库）
    importlib.reload(db)
    db.init()


def test_dry_run_returns_plan_without_move():
    """dry_run 只返回计划明细，绝不触发 fs_move，也不写 moved_out。"""
    mock_client = MagicMock()
    mock_client.fs_move.return_value = {"state": True}

    with patch.object(pan, "_get_client", return_value=mock_client), \
         patch.object(pan, "mkdirs", side_effect=lambda p: 100 if "接收" in p else 200), \
         patch.object(pan, "list_file_names", return_value=set()), \
         patch("time.sleep", return_value=None), \
         patch.object(pan, "list_entries", side_effect=lambda cid, limit=2000: (
             [{"id": "m1", "name": "折月亮.S01E01.第1集.1080p.mkv", "is_dir": False}]
             if cid == 100 else
             [{"id": "201", "name": "折月亮 (2026)", "is_dir": True}] if cid == 200 else [])):
        moved_out: list = []
        cnt, plan = pan.move_matched_files_for_rule(
            "/最近接收", "/电视剧", mode="library", scan_subdirs=False,
            dedup_by_episode=False, dry_run=True, moved_out=moved_out)
        check("dry_run 返回计划 dict", isinstance(plan, dict) and "items" in plan, str(type(plan)))
        check("计划包含命中文件", len(plan["items"]) == 1 and plan["items"][0]["name"].startswith("折月亮"),
              str(plan))
        it = plan["items"][0]
        check("计划含源/目标路径与命中词", it["from_path"] == "/最近接收"
              and "折月亮 (2026)" in it["dest_path"] and it["matched"] == "折月亮",
              str(it))  # matched 为剥离年份后的剧名关键词
        check("dry_run 不触发 fs_move", mock_client.fs_move.call_count == 0)
        check("dry_run 不产生 moved_out 明细", moved_out == [])
        check("计划数与条目一致", cnt == 1)


def test_normal_mode_collects_moved_out():
    """正常模式返回值保持 (移动数, 样例名) 兼容，同时 moved_out 收集完整明细。"""
    mock_client = MagicMock()
    mock_client.fs_move.return_value = {"state": True}

    with patch.object(pan, "_get_client", return_value=mock_client), \
         patch.object(pan, "mkdirs", side_effect=lambda p: 100 if "接收" in p else 200), \
         patch.object(pan, "list_file_names", return_value=set()), \
         patch("time.sleep", return_value=None), \
         patch.object(pan, "list_entries", side_effect=lambda cid, limit=2000: (
             [{"id": "m1", "name": "折月亮.S01E01.第1集.1080p.mkv", "is_dir": False}]
             if cid == 100 else
             [{"id": "201", "name": "折月亮 (2026)", "is_dir": True}] if cid == 200 else [])):
        moved_out: list = []
        moved_count, samples = pan.move_matched_files_for_rule(
            "/最近接收", "/电视剧", mode="library", scan_subdirs=False,
            dedup_by_episode=False, moved_out=moved_out)
        check("正常模式返回移动数", moved_count == 1)
        check("moved_out 收集明细", len(moved_out) == 1, str(moved_out))
        it = moved_out[0]
        check("明细含 file_id/源/目标路径", it["id"] == "m1" and it["from_cid"] == 100
              and "折月亮 (2026)" in it["dest_path"], str(it))


def test_execute_writes_ledger_and_retention():
    """execute_auto_move 成功移动后落账本（batch_id 一致），90 天前旧记录被清理。"""
    db.execute("DELETE FROM auto_move_rules")
    db.execute("DELETE FROM organized_records WHERE action='auto_move'")
    db.execute("DELETE FROM organized_records WHERE action!='auto_move' AND file_id='legacy-old'")
    rid = _mk_rule()
    # 90 天前的旧账本记录（应被清理）
    _mk_record(fid="old1", name="旧文件.mkv", rule_id=rid, batch="bold", age_days=91)
    db.set_setting("running", True)
    db.set_setting("auto_move_enabled", True)
    db.set_setting("auto_move_notify", False)

    def fake_move(*args, **kwargs):
        out = kwargs.get("moved_out")
        if out is not None:
            out.append({"id": "m9", "name": "折月亮.S01E02.mkv", "from_cid": 100,
                        "from_path": "/最近接收", "dest_cid": 200,
                        "dest_path": "/电视剧/折月亮 (2026)", "drama": "折月亮 (2026)",
                        "season": "", "size": 2048})
        return 1, ["折月亮.S01E02.mkv"]

    async def fake_drain(*args, **kwargs):
        return True

    async def scenario():
        with patch.object(transfer_gate, "try_begin_exclusive", return_value=(True, "")), \
             patch.object(transfer_gate, "prepare_and_drain_transfers", fake_drain), \
             patch.object(pan, "move_matched_files_for_rule", side_effect=fake_move), \
             patch.object(jobs, "notify", new=lambda *a, **k: None):
            return await jobs.execute_auto_move(trigger="manual")

    res = asyncio.run(scenario())
    check("执行成功且移动 1 个", res.get("ok") and res.get("total_moved") == 1, str(res))
    rows = db.query("SELECT * FROM organized_records WHERE action='auto_move' AND file_id='m9'")
    check("账本落 1 条新记录", len(rows) == 1, str(len(rows)))
    if rows:
        r = dict(rows[0])
        check("账本记录字段完整", r["task_id"] == rid and r["dest_cid"] == "200"
              and r["orig_name"] == "折月亮.S01E02.mkv" and r["undone"] == 0
              and r["batch_id"], str(r))
    old_left = db.query("SELECT count(*) c FROM organized_records WHERE action='auto_move' AND file_id='old1'", one=True)
    check("90 天前的旧账本已被清理", old_left["c"] == 0)
    db.set_setting("running", False)
    db.set_setting("auto_move_enabled", False)
    db.execute("DELETE FROM auto_move_rules")
    db.execute("DELETE FROM organized_records WHERE action='auto_move'")


def test_undo_single_record_moves_back():
    """文件仍在目标目录：撤销移回源目录并标记已撤销。"""
    db.execute("DELETE FROM organized_records WHERE action='auto_move'")
    rec_id = _mk_record(fid="f1", name="折月亮.S01E01.mkv", batch="bu1", from_cid=100, dest_cid=200)

    mock_calls = []

    def fake_list_entries(cid, limit=2000):
        if cid == 200:
            return [{"id": "f1", "name": "折月亮.S01E01.mkv", "is_dir": False}]
        return []

    def fake_move(fids, target_cid):
        mock_calls.append((list(fids), target_cid))

    with patch.object(pan, "list_entries", side_effect=fake_list_entries), \
         patch.object(pan, "move_entries", side_effect=fake_move), \
         patch.object(pan, "mkdirs", return_value=100), \
         patch.object(pan, "pace_factor", return_value=1), \
         patch("time.sleep", return_value=None):
        res = asyncio.run(jobs.undo_auto_move_records(record_id=rec_id))
    check("撤销成功 1 条", res.get("ok") and res.get("undone") == 1, str(res))
    check("文件移回源目录 cid=100", mock_calls and mock_calls[0] == (["f1"], 100), str(mock_calls))
    row = dict(db.query("SELECT * FROM organized_records WHERE id=?", (rec_id,), one=True))
    check("记录标记已撤销", row["undone"] == 1 and row["undo_note"] == "", str(row))


def test_undo_already_home_and_drift():
    """文件已在源目录 → 直接标记；文件两边都不在 → 跳过并记 undo_note。"""
    db.execute("DELETE FROM organized_records WHERE action='auto_move'")
    rid_home = _mk_record(fid="home1", name="已回来.mkv", batch="bu2", from_cid=100, dest_cid=200)
    rid_drift = _mk_record(fid="drift1", name="被移走了.mkv", batch="bu2", from_cid=100, dest_cid=200)

    def fake_list_entries(cid, limit=2000):
        # 目标目录 200 为空；源目录 100 里只有 home1（用户自己移回来了）
        if cid == 100:
            return [{"id": "home1", "name": "已回来.mkv", "is_dir": False}]
        return []

    move_calls = []

    with patch.object(pan, "list_entries", side_effect=fake_list_entries), \
         patch.object(pan, "move_entries", side_effect=lambda f, t: move_calls.append((f, t))), \
         patch.object(pan, "pace_factor", return_value=1), \
         patch("time.sleep", return_value=None):
        res = asyncio.run(jobs.undo_auto_move_records(batch_id="bu2"))
    check("整批撤销：1 条直标 1 条跳过", res.get("undone") == 1 and len(res.get("skipped", [])) == 1, str(res))
    check("不在目标目录的文件绝不被盲移", move_calls == [], str(move_calls))
    row_home = dict(db.query("SELECT * FROM organized_records WHERE id=?", (rid_home,), one=True))
    check("已在原位置：undone=1 且备注说明", row_home["undone"] == 1 and "原位置" in row_home["undo_note"], str(row_home))
    row_drift = dict(db.query("SELECT * FROM organized_records WHERE id=?", (rid_drift,), one=True))
    check("位置漂移：保持未撤销并记 undo_note", row_drift["undone"] == 0 and "位置已变化" in row_drift["undo_note"], str(row_drift))


def test_undo_stop_rules_and_master_switch():
    """撤销时停用涉及规则；无关规则 id 被忽略；启用规则归零才联动关闭总开关。"""
    db.execute("DELETE FROM auto_move_rules")
    db.execute("DELETE FROM organized_records WHERE action='auto_move'")
    rid_rule = _mk_rule()
    rid_other = _mk_rule()  # 未参与本批的规则，不应被停用
    rec_id = _mk_record(fid="f9", name="折月亮.S01E09.mkv", rule_id=rid_rule, batch="bu3",
                        from_cid=100, dest_cid=200)
    db.set_setting("auto_move_enabled", True)

    def fake_list_entries(cid, limit=2000):
        if cid == 200:
            # 目标目录里同时放着两批的文件（move_entries 为 mock，不改变目录状态）
            return [{"id": "f9", "name": "折月亮.S01E09.mkv", "is_dir": False},
                    {"id": "f10", "name": "折月亮.S01E10.mkv", "is_dir": False}]
        return []

    with patch.object(pan, "list_entries", side_effect=fake_list_entries), \
         patch.object(pan, "move_entries", return_value=None), \
         patch.object(pan, "pace_factor", return_value=1), \
         patch("time.sleep", return_value=None):
        res = asyncio.run(jobs.undo_auto_move_records(
            batch_id="bu3", stop_rule_ids=[rid_rule, 99999]))
    check("撤销成功", res.get("undone") == 1, str(res))
    check("涉及规则被停用", res.get("disabled_rules") == [rid_rule], str(res))
    row = dict(db.query("SELECT enabled FROM auto_move_rules WHERE id=?", (rid_rule,), one=True))
    check("规则 enabled=0", row["enabled"] == 0)
    row_other = dict(db.query("SELECT enabled FROM auto_move_rules WHERE id=?", (rid_other,), one=True))
    check("无关规则不受影响", row_other["enabled"] == 1)
    # rid_other 仍在启用：总开关保持开启（未归零不联动）
    check("启用规则未归零时总开关保持", bool(db.get_setting("auto_move_enabled", True)) is True)

    # 第二阶段：最后一条启用规则也被撤销停用 → 启用规则归零 → 联动关闭总开关
    _mk_record(fid="f10", name="折月亮.S01E10.mkv", rule_id=rid_other, batch="bu3b",
               from_cid=100, dest_cid=200)
    with patch.object(pan, "list_entries", side_effect=fake_list_entries), \
         patch.object(pan, "move_entries", return_value=None), \
         patch.object(pan, "pace_factor", return_value=1), \
         patch("time.sleep", return_value=None):
        res2 = asyncio.run(jobs.undo_auto_move_records(
            batch_id="bu3b", stop_rule_ids=[rid_other]))
    check("第二批撤销成功", res2.get("undone") == 1, str(res2))
    check("启用规则归零联动关闭总开关", bool(db.get_setting("auto_move_enabled", True)) is False)


def test_undo_rejects_when_exclusive_busy():
    db.execute("DELETE FROM organized_records WHERE action='auto_move'")
    _mk_record(fid="fz", name="x.mkv", batch="bu4")
    with patch.object(transfer_gate, "is_exclusive_busy", return_value=True):
        res = asyncio.run(jobs.undo_auto_move_records(batch_id="bu4"))
    check("独占执行中拒绝撤销", res.get("ok") is False and "互斥" in res.get("msg", ""), str(res))


def test_preview_auto_move_flow():
    """预览：正常返回计划；独占忙碌拒绝；0 规则返回空。"""
    db.execute("DELETE FROM auto_move_rules")
    db.execute("DELETE FROM organized_records WHERE action='auto_move'")
    _mk_rule(source="/最近接收", target="/电视剧", mode="library")
    db.set_setting("running", True)
    db.set_setting("auto_move_enabled", True)

    def fake_dry_run(*args, **kwargs):
        assert kwargs.get("dry_run") is True, "预览必须以 dry_run 调用"
        return 1, {"items": [{"id": "m1", "name": "折月亮.S01E01.mkv", "from_path": "/最近接收",
                              "dest_path": "/电视剧/折月亮 (2026)/Season 1", "matched": "折月亮 (2026)",
                              "drama": "折月亮 (2026)", "season": "Season 1", "size": 1024}],
                   "skip_ep": 2, "skip_ep_samples": ["a.mkv", "b.mkv"]}

    with patch.object(transfer_gate, "is_exclusive_busy", return_value=False), \
         patch.object(pan, "move_matched_files_for_rule", side_effect=fake_dry_run):
        res = asyncio.run(jobs.preview_auto_move())
    check("预览返回计划", res.get("ok") and res.get("total_planned") == 1, str(res))
    if res.get("rules"):
        entry = res["rules"][0]
        check("预览含规则与计划明细", entry["planned"] and entry["planned"][0]["dest_path"].startswith("/电视剧"),
              str(entry))
        check("预览含防重跳过统计", entry["skip_ep"] == 2, str(entry))

    with patch.object(transfer_gate, "is_exclusive_busy", return_value=True):
        res_busy = asyncio.run(jobs.preview_auto_move())
    check("独占忙碌时拒绝预览", res_busy.get("ok") is False and "互斥" in res_busy.get("msg", ""), str(res_busy))

    db.execute("DELETE FROM auto_move_rules")
    with patch.object(transfer_gate, "is_exclusive_busy", return_value=False):
        res_empty = asyncio.run(jobs.preview_auto_move())
    check("无启用规则返回空计划", res_empty.get("ok") and res_empty.get("total_planned") == 0, str(res_empty))

    db.set_setting("running", False)
    db.set_setting("auto_move_enabled", False)


print("\n=== 整理账本与预览功能测试 ===")
test_migration_undo_note_column()
test_old_db_migration_adds_undo_note()
test_dry_run_returns_plan_without_move()
test_normal_mode_collects_moved_out()
test_execute_writes_ledger_and_retention()
test_undo_single_record_moves_back()
test_undo_already_home_and_drift()
test_undo_stop_rules_and_master_switch()
test_undo_rejects_when_exclusive_busy()
test_preview_auto_move_flow()

print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
