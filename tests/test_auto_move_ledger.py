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
from backend.api import manage

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


async def fake_drain(*args, **kwargs):
    """execute 排空阶段的通用桩：直接视为已排空。"""
    return True


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


def test_media_type_movie_skips_marked_files():
    """电影库规则跳过带集数标识的文件；tv/auto 规则不受影响。"""
    mock_client = MagicMock()
    mock_client.fs_move.return_value = {"state": True}

    files = [
        {"id": "ep1", "name": "斗罗大陆.S01E01.第1集.1080p.mkv", "is_dir": False},
        {"id": "ep2", "name": "斗罗大陆.第2集.mkv", "is_dir": False},
        {"id": "mv1", "name": "斗罗大陆 (2026).mkv", "is_dir": False},
    ]

    def run(media_type):
        moved: list = []
        with patch.object(pan, "_get_client", return_value=mock_client), \
             patch.object(pan, "mkdirs", side_effect=lambda p: 100 if "接收" in p else 200), \
             patch.object(pan, "list_file_names", return_value=set()), \
             patch("time.sleep", return_value=None), \
             patch.object(pan, "list_entries", side_effect=lambda cid, limit=2000: (
                 files if cid == 100 else [{"id": "201", "name": "斗罗大陆", "is_dir": True}])):
            mock_client.fs_move.reset_mock()
            cnt, samples = pan.move_matched_files_for_rule(
                "/最近接收", "/电影", mode="library", scan_subdirs=False,
                dedup_by_episode=False, moved_out=moved, rule_media_type=media_type)
        claimed = sorted(x["id"] for x in moved)
        return cnt, claimed

    cnt_movie, claimed_movie = run("movie")
    check("movie 规则跳过集数文件只认裸片名", claimed_movie == ["mv1"], str(claimed_movie))
    cnt_auto, claimed_auto = run("auto")
    check("auto 规则行为与历史一致（全部认领）", claimed_auto == ["ep1", "ep2", "mv1"], str(claimed_auto))
    cnt_tv, claimed_tv = run("tv")
    check("tv 规则不跳集数文件", claimed_tv == ["ep1", "ep2", "mv1"], str(claimed_tv))


def test_dry_run_reports_movie_and_ambiguous_skips():
    """dry_run 计划如实反映 movie 守卫与同名歧义跳过统计。"""
    mock_client = MagicMock()

    with patch.object(pan, "_get_client", return_value=mock_client), \
         patch.object(pan, "mkdirs", side_effect=lambda p: 100 if "接收" in p else 200), \
         patch.object(pan, "list_file_names", return_value=set()), \
         patch("time.sleep", return_value=None), \
         patch.object(pan, "list_entries", side_effect=lambda cid, limit=2000: (
             [{"id": "ep1", "name": "斗罗大陆.S01E01.mkv", "is_dir": False},
              {"id": "bare", "name": "斗罗大陆.mkv", "is_dir": False}]
             if cid == 100 else
             [{"id": "201", "name": "斗罗大陆", "is_dir": True}] if cid == 200 else [])):
        cnt, plan = pan.move_matched_files_for_rule(
            "/最近接收", "/电影", mode="library", scan_subdirs=False,
            dedup_by_episode=False, dry_run=True,
            rule_media_type="movie", ambiguous_kws={"斗罗大陆"})
        check("集数文件被 movie 守卫跳过", plan["skip_movie"] == 1, str(plan))
        check("无标识裸文件名被歧义拦截", plan["skip_ambiguous"] == 1
              and plan["skip_ambiguous_samples"] == ["斗罗大陆.mkv"], str(plan))
        check("计划为空且无移动", cnt == 0 and plan["items"] == [] and mock_client.fs_move.call_count == 0)


def test_collect_library_keyword_conflicts():
    """跨库同名预检：两库共有目录名 → 归一化关键词入歧义集；单库返回空。"""
    def fake_list_entries(cid, limit=2000):
        if cid == 201:
            return [{"id": "a1", "name": "斗罗大陆", "is_dir": True},
                    {"id": "a2", "name": "Season 1", "is_dir": True},
                    {"id": "a3", "name": "折月亮 (2026)", "is_dir": True}]
        if cid == 202:
            return [{"id": "b1", "name": "斗罗大陆 (2024)", "is_dir": True},
                    {"id": "b2", "name": "庆余年", "is_dir": True}]
        return []

    with patch.object(pan, "mkdirs", side_effect=lambda p: {"/电影": 201, "/电视剧": 202}.get(p, 0)), \
         patch.object(pan, "list_entries", side_effect=fake_list_entries):
        amb = pan.collect_library_keyword_conflicts(["/电影", "/电视剧"], fuzzy_t2s=False)
        check("两库同名关键词进歧义集", "斗罗大陆" in amb and "折月亮(2026)" not in amb, str(amb))
        check("单库不产生歧义", pan.collect_library_keyword_conflicts(["/电影"]) == set())


def test_execute_passes_media_type_and_ambiguous():
    """execute 将规则的 media_type 与预检歧义集传给移动函数。"""
    db.execute("DELETE FROM auto_move_rules")
    db.execute("DELETE FROM organized_records WHERE action='auto_move'")
    db.insert("INSERT INTO auto_move_rules(source_dir,target_dir,keyword,mode,media_type,enabled,created_at,last_run_at,last_moved_count)"
              " VALUES('/r1','/电视剧','', 'library','tv',1,?,NULL,0)", (time.time(),))
    db.insert("INSERT INTO auto_move_rules(source_dir,target_dir,keyword,mode,media_type,enabled,created_at,last_run_at,last_moved_count)"
              " VALUES('/r1','/电影','', 'library','movie',1,?,NULL,0)", (time.time(),))
    db.set_setting("running", True)
    db.set_setting("auto_move_enabled", True)
    db.set_setting("auto_move_notify", False)

    captured = []

    def fake_move(*args, **kwargs):
        captured.append({"media_type": kwargs.get("rule_media_type"),
                         "ambiguous": kwargs.get("ambiguous_kws")})
        return 0, []

    async def scenario():
        with patch.object(transfer_gate, "try_begin_exclusive", return_value=(True, "")), \
             patch.object(transfer_gate, "prepare_and_drain_transfers", fake_drain), \
             patch.object(pan, "move_matched_files_for_rule", side_effect=fake_move), \
             patch.object(pan, "collect_library_keyword_conflicts",
                          side_effect=lambda targets, *a, **k: {"斗罗大陆"} if len(targets) >= 2 else set()), \
             patch.object(jobs, "notify", new=lambda *a, **k: None):
            return await jobs.execute_auto_move(trigger="manual")

    asyncio.run(scenario())
    check("两条规则都收到 media_type", [c["media_type"] for c in captured] == ["tv", "movie"], str(captured))
    check("歧义集传入两条规则", all(c["ambiguous"] == {"斗罗大陆"} for c in captured), str(captured))
    db.set_setting("running", False)
    db.set_setting("auto_move_enabled", False)
    db.execute("DELETE FROM auto_move_rules")
    db.execute("DELETE FROM organized_records WHERE action='auto_move'")


def test_old_db_migration_adds_media_type():
    """老库 auto_move_rules 缺 media_type 列时自动补列。"""
    old_dir = tempfile.mkdtemp(prefix="autosave_test_ledger_mt_")
    old_db = os.path.join(old_dir, "app.db")
    conn = sqlite3.connect(old_db)
    conn.execute("""CREATE TABLE auto_move_rules(
        id INTEGER PRIMARY KEY AUTOINCREMENT, source_dir TEXT NOT NULL, target_dir TEXT NOT NULL,
        keyword TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'default', enabled INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL, last_run_at REAL, last_moved_count INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("INSERT INTO auto_move_rules(source_dir,target_dir,keyword,created_at) VALUES('/a','/b','k',1)")
    conn.commit()
    conn.close()

    import importlib
    import pathlib
    from backend import config
    with patch.object(config, "DATA_DIR", pathlib.Path(old_dir)), \
         patch.object(config, "DB_PATH", pathlib.Path(old_db)):
        importlib.reload(db)
        db.init()
        cols = {r["name"] for r in db.query("PRAGMA table_info(auto_move_rules)")}
        check("老库迁移后补出 media_type 列", "media_type" in cols, str(cols))
        row = db.query("SELECT media_type, source_dir FROM auto_move_rules", one=True)
        check("存量规则默认 auto 且数据保留", row["media_type"] == "auto" and row["source_dir"] == "/a")
    importlib.reload(db)
    db.init()


def test_dir_match_movie_via_parent_name():
    """云下载电影：中文标题在任务目录名上、文件纯英文 → 按目录名匹配认领真媒体，广告垃圾全部拦截。"""
    mock_client = MagicMock()
    moved: list = []
    task_dir_name = ("【更多高清影视请访问 www.5BT0.com】 奥德赛[简繁英字幕]."
                     "The.Odyssey.2026.2160p.UHD.BluRay.x265.10bit-QuickIO")

    def fake_list_entries(cid, limit=2000):
        if cid == 100:  # /云下载 根：任务目录
            return [{"id": "150", "name": task_dir_name, "is_dir": True}]
        if cid == 150:  # 任务目录内
            return [
                {"id": "mv", "name": "The.Odyssey.2026.2160p.UHD.BluRay.x265.10bit.HEVC.HDR.DTS-HD.MA.5.1-QuickIO.mkv",
                 "is_dir": False, "size": 21 * 1024 * 1024 * 1024},
                {"id": "j1", "name": "【更多无水印高品质资源请访问 www.Butailing.com】 .MKV",
                 "is_dir": False, "size": 637000},
                {"id": "j2", "name": "【更多无水印高品质资源请访问 www.Butailing.com】 .MP4",
                 "is_dir": False, "size": 296000},
                {"id": "j3", "name": "【更多无水印高品质资源请访问 www.Butailing.com】 .DOC",
                 "is_dir": False, "size": 296000},
            ]
        if cid == 300:  # /电影 总库
            return [{"id": "301", "name": "奥德赛", "is_dir": True}]
        return []

    with patch.object(pan, "_get_client", return_value=mock_client), \
         patch.object(pan, "mkdirs", side_effect=lambda p: {"/云下载": 100, "/电影": 300}.get(p, 0)), \
         patch.object(pan, "list_file_names", return_value=set()), \
         patch("time.sleep", return_value=None), \
         patch.object(pan, "list_entries", side_effect=fake_list_entries):
        cnt, samples = pan.move_matched_files_for_rule(
            "/云下载", "/电影", mode="library", scan_subdirs=True,
            dedup_by_episode=False, moved_out=moved,
            rule_media_type="movie", match_by_dir=True)
        called = mock_client.fs_move.call_args
    check("按目录名匹配认领真媒体电影", cnt == 1, f"cnt={cnt}")
    check("广告垃圾文件全部拦截", called[0][0] == ["mv"], str(called))
    check("归档目标为电影库对应目录", moved and moved[0]["dest_path"] == "/电影/奥德赛", str(moved))
    check("账本源路径含任务目录名", "奥德赛" in moved[0]["from_path"], str(moved))


def test_dir_match_tv_season_routing():
    """云下载电视剧：目录名匹配剧名，内层文件按 S01E05 定季进已存在的 Season 1。"""
    mock_client = MagicMock()
    moved: list = []
    task_dir_name = ("【高清剧集网发布 www.BPHDTV.com】 小城良方[全29集][国语音轨+简繁英字幕]."
                     "Small.Town.Remedy.S01.2026.2160p.IQiyi-ColorWEB")

    def fake_list_entries(cid, limit=2000):
        if cid == 100:
            return [{"id": "150", "name": task_dir_name, "is_dir": True}]
        if cid == 150:
            return [
                {"id": f"e{n}", "name": f"Small.Town.Remedy.S01E{n:02d}.2026.2160p.IQ.WEB-DL.H265.DDP5.1-ColorWEB.mkv",
                 "is_dir": False, "size": 1024 * 1024 * 1024}
                for n in (1, 3, 5)
            ]
        if cid == 200:  # /电视剧 总库
            return [{"id": "201", "name": "小城良方", "is_dir": True}]
        if cid == 201:  # 剧目目录（已有 Season 1）
            return [{"id": "210", "name": "Season 1", "is_dir": True}]
        return []

    with patch.object(pan, "_get_client", return_value=mock_client), \
         patch.object(pan, "mkdirs", side_effect=lambda p: {"/云下载": 100, "/电视剧": 200}.get(p, 0)), \
         patch.object(pan, "list_file_names", return_value=set()), \
         patch("time.sleep", return_value=None), \
         patch.object(pan, "list_entries", side_effect=fake_list_entries):
        cnt, samples = pan.move_matched_files_for_rule(
            "/云下载", "/电视剧", mode="library", scan_subdirs=True,
            dedup_by_episode=True, moved_out=moved,
            rule_media_type="tv", match_by_dir=True, season_folder=True)
        called = mock_client.fs_move.call_args
    check("按目录名匹配认领剧集 3 个文件", cnt == 3, f"cnt={cnt}")
    check("按文件名 S01E0x 进已存在的 Season 1", called and called[1].get("pid") == 210, str(called))
    check("账本目标路径含 Season 1", moved and all("Season 1" in m["dest_path"] for m in moved), str(moved))


def test_dir_match_movie_guard_skips_marked_dir():
    """目录名或目录内文件带集数标识 → movie 规则整组跳过（剧集交给剧集规则）。"""
    mock_client = MagicMock()

    def fake_list_entries(cid, limit=2000):
        if cid == 100:
            return [{"id": "150", "name": "某剧名.S01.2026.2160p", "is_dir": True}]
        if cid == 150:
            return [{"id": "e1", "name": "Some.Show.S01E01.2026.2160p.mkv", "is_dir": False,
                     "size": 1024 * 1024 * 1024}]
        if cid == 300:
            return [{"id": "301", "name": "某剧名", "is_dir": True}]
        return []

    with patch.object(pan, "_get_client", return_value=mock_client), \
         patch.object(pan, "mkdirs", side_effect=lambda p: {"/云下载": 100, "/电影": 300}.get(p, 0)), \
         patch.object(pan, "list_file_names", return_value=set()), \
         patch("time.sleep", return_value=None), \
         patch.object(pan, "list_entries", side_effect=fake_list_entries):
        cnt, plan = pan.move_matched_files_for_rule(
            "/云下载", "/电影", mode="library", scan_subdirs=True,
            dedup_by_episode=False, dry_run=True,
            rule_media_type="movie", match_by_dir=True)
    check("带集数标识的任务目录被 movie 规则整组跳过", cnt == 0 and mock_client.fs_move.call_count == 0, str(plan))


def test_dir_match_skips_root_files_and_ambiguous():
    """源目录根部散落的文件不参与目录匹配；两库同名的裸目录按歧义拦截。"""
    mock_client = MagicMock()

    def fake_list_entries(cid, limit=2000):
        if cid == 100:
            # 根部散落文件三种形态：有中文名 / 纯英文无标识 / 纯英文带集数标识——一律不参与
            return [
                {"id": "root1", "name": "斗罗大陆.mkv", "is_dir": False, "size": 1024 * 1024 * 1024},
                {"id": "root2", "name": "a.mp4", "is_dir": False, "size": 1024 * 1024 * 1024},
                {"id": "root3", "name": "aS1E01.mp4", "is_dir": False, "size": 1024 * 1024 * 1024},
                {"id": "150", "name": "斗罗大陆", "is_dir": True},
            ]
        if cid == 150:
            return [{"id": "in1", "name": "斗罗大陆.mkv", "is_dir": False, "size": 1024 * 1024 * 1024}]
        if cid == 300:
            return [{"id": "301", "name": "斗罗大陆", "is_dir": True}]
        return []

    with patch.object(pan, "_get_client", return_value=mock_client), \
         patch.object(pan, "mkdirs", side_effect=lambda p: {"/云下载": 100, "/电影": 300}.get(p, 0)), \
         patch.object(pan, "list_file_names", return_value=set()), \
         patch("time.sleep", return_value=None), \
         patch.object(pan, "list_entries", side_effect=fake_list_entries):
        cnt, plan = pan.move_matched_files_for_rule(
            "/云下载", "/电影", mode="library", scan_subdirs=True,
            dedup_by_episode=False, dry_run=True, match_by_dir=True,
            ambiguous_kws={"斗罗大陆"})
    check("根部散落文件不参与目录匹配", plan["items"] == [], str(plan))
    check("两库同名裸目录按歧义拦截", plan["skip_ambiguous"] == 1
          and plan["skip_ambiguous_samples"] == ["斗罗大陆.mkv"], str(plan))


def test_cloud_download_dirs_from_routes():
    """云下载目录集合：offline_dir 优先，未设置回落 target_dir，skip/空排除并归一化。"""
    db.execute("DELETE FROM routes")
    db.execute("INSERT INTO routes(category,target_dir,offline_dir) VALUES('电视剧','/电视剧','/云下载')")
    db.execute("INSERT INTO routes(category,target_dir,offline_dir) VALUES('电影','/电影','')")
    db.execute("INSERT INTO routes(category,target_dir,offline_dir) VALUES('少儿','skip','')")
    dirs = jobs.cloud_download_dirs()
    check("offline_dir 优先", "/云下载" in dirs, str(dirs))
    check("未设置 offline_dir 回落 target_dir", "/电影" in dirs, str(dirs))
    check("skip 分类被排除", "/少儿" not in dirs and len(dirs) == 2, str(dirs))
    db.execute("DELETE FROM routes")


def test_execute_skips_stale_dir_match_rule():
    """云下载目录配置漂移：dir_match 规则源目录失效 → 本轮跳过并标注，不带着错误语义执行。"""
    db.execute("DELETE FROM auto_move_rules")
    db.execute("DELETE FROM organized_records WHERE action='auto_move'")
    db.insert("INSERT INTO auto_move_rules(source_dir,target_dir,keyword,mode,dir_match,enabled,created_at,last_run_at,last_moved_count)"
              " VALUES('/某电影目录','/电影','','library',1,1,?,NULL,0)", (time.time(),))
    db.set_setting("running", True)
    db.set_setting("auto_move_enabled", True)
    db.set_setting("auto_move_notify", False)

    async def scenario():
        with patch.object(transfer_gate, "try_begin_exclusive", return_value=(True, "")), \
             patch.object(transfer_gate, "prepare_and_drain_transfers", fake_drain), \
             patch.object(pan, "move_matched_files_for_rule", side_effect=AssertionError("不应调用移动")), \
             patch.object(jobs, "notify", new=lambda *a, **k: None):
            return await jobs.execute_auto_move(trigger="manual")

    res = asyncio.run(scenario())
    d = (res.get("details") or [{}])[0]
    check("失效规则本轮跳过且不触发移动", res.get("total_moved") == 0
          and "云下载模式未生效" in (d.get("error") or ""), str(res))
    db.set_setting("running", False)
    db.set_setting("auto_move_enabled", False)
    db.execute("DELETE FROM auto_move_rules")


def test_save_dir_match_requires_cloud_dir():
    """save 端点校验：dir_match 开启时源目录必须是配置的云下载目录。"""
    db.execute("DELETE FROM routes")
    db.execute("INSERT INTO routes(category,target_dir,offline_dir) VALUES('电视剧','/电视剧','/云下载')")
    from fastapi import HTTPException
    # 源目录不是云下载目录 → 拒绝
    try:
        manage.save_auto_move_rule({"source_dir": "/最近接收", "target_dir": "/电视剧",
                                    "dir_match": 1})
        raise AssertionError("应当拒绝")
    except HTTPException as e:
        check("非云下载目录被拒绝并给出指引", e.status_code == 400 and "云下载目录" in e.detail, str(e.detail))
    # 源目录是云下载目录 → 通过
    res = manage.save_auto_move_rule({"source_dir": "/云下载", "target_dir": "/电视剧",
                                      "mode": "library", "dir_match": 1})
    check("云下载目录允许开启", res.get("ok") is True)
    row = db.query("SELECT dir_match FROM auto_move_rules ORDER BY id DESC", one=True)
    check("dir_match 落库", row["dir_match"] == 1)
    db.execute("DELETE FROM auto_move_rules")
    db.execute("DELETE FROM routes")


def test_dir_match_dedup_same_name_and_sha1():
    """云下载目录模式查重：同名跳过；目标库被刮削改名后按 sha1 内容级跳过；全新文件正常认领。"""
    mock_client = MagicMock()
    moved: list = []

    def fake_list_entries(cid, limit=2000):
        if cid == 100:
            return [{"id": "150", "name": "【广告www..】 奥德赛[简繁英字幕].The.Odyssey.2026", "is_dir": True}]
        if cid == 150:
            return [
                {"id": "dup", "name": "The.Odyssey.2026.2160p.mkv", "is_dir": False,
                 "size": 1024 * 1024 * 1024, "sha1": "shaA"},
                {"id": "renamed", "name": "The.Odyssey.2026.REPACK.2160p.mkv", "is_dir": False,
                 "size": 1024 * 1024 * 1024, "sha1": "shaB"},
                {"id": "fresh", "name": "The.Odyssey.2026.NEWROUND.2160p.mkv", "is_dir": False,
                 "size": 1024 * 1024 * 1024, "sha1": "shaC"},
            ]
        if cid == 300:
            return [{"id": "301", "name": "奥德赛", "is_dir": True}]
        if cid == 301:  # 目标剧目目录：已有同名旧文件 + 已被刮削改名的同内容文件
            return [
                {"id": "x1", "name": "The.Odyssey.2026.2160p.mkv", "is_dir": False, "sha1": "shaA"},
                {"id": "x2", "name": "奥德赛 (2026) [tmdbid=12345].mkv", "is_dir": False, "sha1": "shaB"},
            ]
        return []

    def fake_list_file_names(cid, limit=2000):
        if cid == 301:
            return {"The.Odyssey.2026.2160p.mkv"}  # 名称查重命中的同名文件
        return set()

    with patch.object(pan, "_get_client", return_value=mock_client),          patch.object(pan, "mkdirs", side_effect=lambda p: {"/云下载": 100, "/电影": 300}.get(p, 0)),          patch.object(pan, "list_file_names", side_effect=fake_list_file_names),          patch("time.sleep", return_value=None),          patch.object(pan, "list_entries", side_effect=fake_list_entries):
        cnt, samples = pan.move_matched_files_for_rule(
            "/云下载", "/电影", mode="library", scan_subdirs=True,
            dedup_by_episode=False, moved_out=moved, match_by_dir=True,
            rule_media_type="movie")
        called = mock_client.fs_move.call_args
    check("同名文件按名称查重跳过", cnt == 1, f"cnt={cnt}")
    check("被刮削改名的同内容文件按 sha1 跳过", called and called[0][0] == ["fresh"], str(called))
    check("仅全新文件被归档", moved and moved[0]["name"].endswith("NEWROUND.2160p.mkv"), str(moved))

def test_dry_run_stats_always_dict_including_dedup():
    """dry_run 任何路径（含早退与查重）都必须返回统计 dict——回归：dedup 计数未初始化导致预览全挂。"""
    mock_client = MagicMock()

    # 场景 1：目录匹配 + 查重（同名 + sha1 各一）→ skip_dedup=2，且不抛 UnboundLocalError
    def fake_list_entries(cid, limit=2000):
        if cid == 100:
            return [{"id": "150", "name": "【广告www..】 奥德赛[简繁英字幕].The.Odyssey.2026", "is_dir": True}]
        if cid == 150:
            return [
                {"id": "dup", "name": "The.Odyssey.2026.2160p.mkv", "is_dir": False,
                 "size": 1024 * 1024 * 1024, "sha1": "shaA"},
                {"id": "renamed", "name": "The.Odyssey.2026.REPACK.mkv", "is_dir": False,
                 "size": 1024 * 1024 * 1024, "sha1": "shaB"},
            ]
        if cid == 300:
            return [{"id": "301", "name": "奥德赛", "is_dir": True}]
        if cid == 301:
            return [
                {"id": "x1", "name": "The.Odyssey.2026.2160p.mkv", "is_dir": False, "sha1": "shaA"},
                {"id": "x2", "name": "奥德赛 (2026) [tmdbid=1].mkv", "is_dir": False, "sha1": "shaB"},
            ]
        return []

    def fake_lfn(cid, limit=2000):
        if cid == 301:
            return {"The.Odyssey.2026.2160p.mkv"}
        return set()

    with patch.object(pan, "_get_client", return_value=mock_client),          patch.object(pan, "mkdirs", side_effect=lambda p: {"/云下载": 100, "/电影": 300}.get(p, 0)),          patch.object(pan, "list_file_names", side_effect=fake_lfn),          patch("time.sleep", return_value=None),          patch.object(pan, "list_entries", side_effect=fake_list_entries):
        cnt, plan = pan.move_matched_files_for_rule(
            "/云下载", "/电影", mode="library", scan_subdirs=True,
            dedup_by_episode=False, dry_run=True, match_by_dir=True,
            rule_media_type="movie")
    check("dry_run 查重路径返回统计 dict", isinstance(plan, dict), str(type(plan)))
    check("查重跳过计数 correct", plan.get("skip_dedup") == 2, str(plan))

    # 场景 2：早退路径（总库无任何中文子目录）→ 仍返回统计 dict 且全 0
    with patch.object(pan, "_get_client", return_value=mock_client),          patch.object(pan, "mkdirs", side_effect=lambda p: {"/云下载": 100, "/电影": 300}.get(p, 0)),          patch("time.sleep", return_value=None),          patch.object(pan, "list_entries", side_effect=lambda cid, limit=2000: (
             [{"id": "150", "name": "【广告www..】 奥德赛.2026", "is_dir": True}] if cid == 100 else
             [{"id": "301", "name": "English.Only.2026", "is_dir": True}] if cid == 300 else [])):
        cnt2, plan2 = pan.move_matched_files_for_rule(
            "/云下载", "/电影", mode="library", scan_subdirs=True,
            dedup_by_episode=False, dry_run=True, match_by_dir=True,
            rule_media_type="movie")
    check("dry_run 早退路径返回统计 dict", isinstance(plan2, dict), str(type(plan2)))
    check("早退路径统计全 0", plan2.get("items") == [] and plan2.get("skip_dedup") == 0
          and plan2.get("skip_movie") == 0, str(plan2))


def test_collect_conflicts_dedupes_targets():
    """回归：同一总库被两条规则指向（目标重复）时，不得把整库剧名误判为两库同名。"""
    def fake_list_entries(cid, limit=2000):
        if cid == 201:  # /电视剧：斗罗大陆 + 折月亮
            return [{"id": "a1", "name": "斗罗大陆", "is_dir": True},
                    {"id": "a2", "name": "折月亮 (2026)", "is_dir": True}]
        if cid == 300:  # /电影：奥德赛 + 斗罗大陆剧场版（kw 不同）
            return [{"id": "b1", "name": "奥德赛 (2026)", "is_dir": True},
                    {"id": "b2", "name": "斗罗大陆", "is_dir": True}]
        return []

    with patch.object(pan, "mkdirs", side_effect=lambda p: {"/电视剧": 201, "/电影": 300}.get(p, 0)),          patch.object(pan, "list_entries", side_effect=fake_list_entries):
        # 目标列表含重复（电视剧/电影/电视剧/电影——四条规则的真实形态）
        amb = pan.collect_library_keyword_conflicts(
            ["/电视剧", "/电影", "/电视剧", "/电影"], fuzzy_t2s=False)
        check("真两库同名照常进歧义集", "斗罗大陆" in amb, str(amb))
        check("单库独有的名字不被误判", "奥德赛" not in amb and "折月亮(2026)" not in amb
              and "折月亮2026" not in amb, str(amb))
        # 去重后仅剩一个库 → 无歧义
        check("单库重复列出不再产生歧义",
              pan.collect_library_keyword_conflicts(["/电视剧", "/电视剧"], fuzzy_t2s=False) == set())


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
test_media_type_movie_skips_marked_files()
test_dry_run_reports_movie_and_ambiguous_skips()
test_collect_library_keyword_conflicts()
test_execute_passes_media_type_and_ambiguous()
test_old_db_migration_adds_media_type()
test_dir_match_movie_via_parent_name()
test_dir_match_tv_season_routing()
test_dir_match_movie_guard_skips_marked_dir()
test_dir_match_skips_root_files_and_ambiguous()
test_cloud_download_dirs_from_routes()
test_execute_skips_stale_dir_match_rule()
test_save_dir_match_requires_cloud_dir()
test_dir_match_dedup_same_name_and_sha1()
test_dry_run_stats_always_dict_including_dedup()
test_collect_conflicts_dedupes_targets()

print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
