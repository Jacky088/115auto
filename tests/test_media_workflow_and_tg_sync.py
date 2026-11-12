"""整理刮削四方互斥门禁、单队列顺序执行、账本撤销与 Web↔TG 双向实时联动集成测试集。"""
import asyncio
import json
import os
import sys
import tempfile
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_mw_"))

from backend import database as db
from backend.core import transfer_gate, tmdb_scraper
from backend.pan115 import client as pan
from backend.worker import bot_commands, jobs, media_worker, queue_worker


@pytest.fixture(autouse=True)
def _clean_state():
    db.init()
    transfer_gate.reset_for_tests()
    transfer_gate.clear_cancel()
    for tbl in ("media_rules", "media_tasks", "organized_records", "media_tg_batches", "tmdb_cache", "transfers"):
        db.execute(f"DELETE FROM {tbl}")
    while not jobs._tg_notify_queue.empty():
        try:
            jobs._tg_notify_queue.get_nowait()
            jobs._tg_notify_queue.task_done()
        except Exception:
            break
    db.set_setting("running", True)
    db.set_setting("tmdb_api_key", "test_tmdb_key")
    media_worker._COMMIT_WAKEUP_REQUESTED = False
    media_worker._ACTIVE_TASK_REF["organize"] = None
    media_worker._ACTIVE_TASK_REF["scrape"] = None
    db.set_setting("notify_token", "123:ABC")
    db.set_setting("notify_chat_id", "-100123456")
    db.set_setting("media_op_delay_min", 0.01)
    db.set_setting("media_op_delay_max", 0.02)
    yield
    transfer_gate.reset_for_tests()
    transfer_gate.clear_cancel()


def test_top_running_switch_and_four_way_mutex():
    # 1. 顶部开关关闭时，组织、刮削、提交队列均被拒绝
    db.set_setting("running", False)
    res_org = asyncio.run(media_worker.execute_organize_queue())
    res_scr = asyncio.run(media_worker.execute_scrape_queue())
    res_com = media_worker.commit_staged_tasks()
    assert res_org["ok"] is False
    assert res_scr["ok"] is False
    assert res_com["ok"] is False and res_com["code"] == "system_stopped"

    # 2. 四方互斥：任一模式占锁期间，另外三个模式无法进入
    db.set_setting("running", True)
    ok1, _ = transfer_gate.try_begin_exclusive("organize")
    assert ok1 is True
    assert transfer_gate.is_organize_running() is True
    assert transfer_gate.is_exclusive_busy() is True

    ok_scr, msg_scr = transfer_gate.try_begin_exclusive("scrape")
    ok_am, msg_am = transfer_gate.try_begin_exclusive("auto_move")
    assert ok_scr is False and "整理" in msg_scr
    assert ok_am is False

    # 当 organize 正在跑，commit_staged_tasks 也受互斥保护拒绝启动 scrape
    tid = db.insert(
        "INSERT INTO media_tasks(task_kind,orig_name,status,selected_tmdb_id,created_at,updated_at) "
        "VALUES('scrape','a.mkv','staged',123,1,1)"
    )
    com_busy = media_worker.commit_staged_tasks(task_ids=[tid], source="web")
    assert com_busy["ok"] is False and com_busy["code"] == "mutex_busy"

    transfer_gate.release_exclusive("organize")
    assert transfer_gate.get_active_mode() == "none"
    assert transfer_gate.is_exclusive_busy() is False


def test_organize_sequential_execution_undo_and_transfer_dedup():
    rule_id = db.insert(
        "INSERT INTO media_rules(name,source_dir,target_dir,media_type,organize_enabled,scrape_enabled,enabled,created_at) "
        "VALUES('电影整理','/未整理电影','/电影库','movie',1,1,1,?)",
        (time.time(),),
    )

    # 模拟 115 目录树、移动、重命名、建子目录（同时校验零并发串行调用）
    pan_ops = []

    def fake_list_dir_id(path):
        return {"/未整理电影": 100, "/电影库": 200}.get(path, 0)

    def fake_walk(root_cid, root_path, max_depth=4):
        return {
            "files": [
                {
                    "id": "5001",
                    "name": "Inception.2010.1080p.BluRay.x264.mkv",
                    "size": 8 * 1024 * 1024 * 1024,
                    "sha1": "SHA1_INCEPTION",
                    "parent_cid": "100",
                    "dir_path": "/未整理电影",
                    "parent_dirs": [],
                    "sibling_names": ["Inception.2010.1080p.BluRay.x264.mkv", "Inception.2010.1080p.BluRay.x264.chs.ass"],
                    "sibling_entries": [
                        {"id": "5001", "name": "Inception.2010.1080p.BluRay.x264.mkv", "size": 8000, "sha1": "SHA1_INCEPTION", "is_dir": False},
                        {"id": "5002", "name": "Inception.2010.1080p.BluRay.x264.chs.ass", "size": 200, "sha1": "SHA1_SUB", "is_dir": False},
                    ],
                }
            ],
            "bdmv_dirs": [],
            "dirs": [],
        }

    def fake_mkdirs(path):
        pan_ops.append(("mkdirs", path))
        return 200

    def fake_ensure_subdir(base_cid, rel_dir):
        pan_ops.append(("ensure_subdir", base_cid, rel_dir))
        return 201

    def fake_list_entries(cid, limit=2000):
        return []

    def fake_rename(fid, new_name):
        pan_ops.append(("rename", str(fid), new_name))

    def fake_move(fids, target_cid):
        pan_ops.append(("move", [str(x) for x in fids], int(target_cid)))

    with patch.object(pan, "resolve_dir_cid", side_effect=fake_list_dir_id), \
         patch.object(pan, "walk_media_tree", side_effect=fake_walk), \
         patch.object(pan, "mkdirs", side_effect=fake_mkdirs), \
         patch.object(pan, "ensure_subdir", side_effect=fake_ensure_subdir), \
         patch.object(pan, "list_entries", side_effect=fake_list_entries), \
         patch.object(pan, "rename_entry", side_effect=fake_rename), \
         patch.object(pan, "move_entries", side_effect=fake_move):
        res = asyncio.run(media_worker.execute_organize_queue(rule_ids=[rule_id], trigger="manual"))

    assert res["ok"] is True
    assert res["done"] == 1
    assert res["failed"] == 0
    # 结束后门禁已恢复
    assert transfer_gate.get_gate_state() == "idle"
    assert transfer_gate.get_active_mode() == "none"

    # 主视频与配套字幕都被重命名且移动到了 /电影库/Inception (2010)/
    rename_targets = [op[2] for op in pan_ops if op[0] == "rename"]
    assert "Inception (2010) - 1080p.mkv" in rename_targets
    assert "Inception (2010) - 1080p.chs.ass" in rename_targets

    recs = [dict(r) for r in db.query("SELECT * FROM organized_records WHERE undone=0 ORDER BY id ASC")]
    assert len(recs) == 2
    assert recs[0]["orig_name"] == "Inception.2010.1080p.BluRay.x264.mkv"
    assert recs[0]["dest_name"] == "Inception (2010) - 1080p.mkv"

    # 验证转存按文件名去重会跨库联动查询 organized_records，不因改名移动而重复转存
    db.set_setting("dedup_by_name", True)
    queue_worker._dir_cache.clear()
    with patch.object(pan, "list_file_names", return_value=set()):
        dup_reason = queue_worker._dedup_by_name(
            200, "Inception 2010", {"top_names": [], "files": [{"fn": "Inception.2010.1080p.BluRay.x264.mkv"}]}
        )
    assert "账本去重" in dup_reason

    # 验证整批安全撤销能原路恢复目录和文件名
    pan_ops.clear()
    with patch.object(pan, "rename_entry", side_effect=fake_rename), \
         patch.object(pan, "move_entries", side_effect=fake_move):
        undo_res = asyncio.run(media_worker.undo_organized(batch_id=res["batch_id"]))
    assert undo_res["ok"] is True and undo_res["undone"] == 2
    assert ("move", ["5001"], 100) in pan_ops
    assert ("rename", "5001", "Inception.2010.1080p.BluRay.x264.mkv") in pan_ops


def test_scrape_0sec_skip_plain_tg_report_and_web_commit(tmp_path):
    db.set_setting("media_local_meta_dir", str(tmp_path / "meta"))
    rule_id = db.insert(
        "INSERT INTO media_rules(name,source_dir,target_dir,media_type,organize_enabled,scrape_enabled,enabled,created_at) "
        "VALUES('剧集刮削','/待刮削','/媒体库','auto',1,1,1,?)",
        (time.time(),),
    )

    pan_ops = []

    def fake_walk(root_cid, root_path, max_depth=4):
        return {
            "files": [
                {
                    "id": "9000", "name": "Star.Wars.1977.1080p.mkv", "size": 3 * 1024**3, "sha1": "S0",
                    "parent_cid": "300", "dir_path": "/待刮削", "parent_dirs": [],
                    "sibling_names": ["Star.Wars.1977.1080p.mkv"], "sibling_entries": [],
                },
                {
                    "id": "9001", "name": "沙丘.1080p.mkv", "size": 2 * 1024**3, "sha1": "S1",
                    "parent_cid": "300", "dir_path": "/待刮削", "parent_dirs": [],
                    "sibling_names": ["沙丘.1080p.mkv"], "sibling_entries": [],
                },
            ],
            "bdmv_dirs": [],
            "dirs": [],
        }

    sw_cand = {"id": 11, "title": "星球大战", "original_title": "Star Wars", "release_date": "1977-05-25",
               "overview": "SW", "vote_average": 8.2, "vote_count": 19000, "popularity": 95.0, "poster_path": "/sw.jpg"}
    cand1 = {"id": 438631, "title": "沙丘", "original_title": "Dune", "release_date": "2021-10-22",
             "overview": "2021 版", "vote_average": 7.8, "vote_count": 9000, "popularity": 90.0, "poster_path": "/p1.jpg"}
    cand2 = {"id": 841, "title": "沙丘", "original_title": "Dune", "release_date": "1984-12-14",
             "overview": "1984 版", "vote_average": 6.2, "vote_count": 2900, "popularity": 30.0, "poster_path": "/p2.jpg"}

    def fake_recall(parsed, api_key, type_hint="auto", ep_stats=None):
        if "Star" in (parsed.title or "") or "星球" in (parsed.orig_name or ""):
            s0 = tmdb_scraper.score_candidate(sw_cand, "movie", parsed, ep_stats)
            s0["score"] = 95.0
            s0["detail"] = sw_cand
            return [s0]
        s1 = tmdb_scraper.score_candidate(cand1, "movie", parsed, ep_stats)
        s2 = tmdb_scraper.score_candidate(cand2, "movie", parsed, ep_stats)
        # 构造分数差 < 12 的模糊候选触发 0 秒挂起进网页待确认列表
        s1["score"], s2["score"] = 78.0, 74.0
        return [s1, s2]

    with patch.object(pan, "resolve_dir_cid", return_value=300), \
         patch.object(pan, "walk_media_tree", side_effect=fake_walk), \
         patch.object(media_worker, "recall_and_score", side_effect=fake_recall), \
         patch.object(tmdb_scraper, "tmdb_download_image", return_value=b"\xff\xd8\xff\xe0POSTER"), \
         patch.object(pan, "mkdirs", return_value=400), \
         patch.object(pan, "ensure_subdir", side_effect=lambda b, r: (pan_ops.append(("subdir", r)), 401)[1]), \
         patch.object(pan, "list_entries", return_value=[]), \
         patch.object(pan, "rename_entry", side_effect=lambda f, n: pan_ops.append(("rename", n))), \
         patch.object(pan, "move_entries", side_effect=lambda fs, c: pan_ops.append(("move", c))):
        scr_res = asyncio.run(media_worker.execute_scrape_queue(mode="scan_and_run", rule_ids=[rule_id]))

    assert scr_res["ok"] is True
    assert scr_res["auto"] == 1
    assert scr_res["need_confirm"] == 1
    assert transfer_gate.get_gate_state() == "idle"
    assert transfer_gate.get_active_mode() == "none"

    # 事后仅推送 1 条纯文本刮削结果通知（绝不推交互卡片或控制台按钮）
    q_items = []
    while not jobs._tg_notify_queue.empty():
        it = jobs._tg_notify_queue.get_nowait()
        q_items.append(it)
        jobs._tg_notify_queue.task_done()
    assert len(q_items) == 1
    assert q_items[0]["type"] == "text"
    assert q_items[0].get("reply_markup") is None
    msg = q_items[0]["text"]
    assert "`" not in msg
    assert "🎬 115 媒体刮削本轮完成" in msg
    assert "自动落盘 1 项｜跳过待确认 1 部" in msg
    assert "🎬 星球大战 (1977) [tmdbid=11]（1 部｜NFO + 海报）" in msg
    assert "已跳过并加入网页「待确认列表」" in msg
    assert "沙丘" in msg and "最高候选" in msg
    assert "待确认列表" in msg and "提交队列" in msg
    assert "▶️ 主程序转存与云下载已恢复运行" in msg

    task_row = dict(db.query("SELECT * FROM media_tasks WHERE status='need_confirm' ORDER BY id DESC LIMIT 1", one=True))
    tid = int(task_row["id"])

    # 1) 网页点选第 1 候选 (438631) -> 仅暂存 staged，不推送任何 TG 编辑队列
    res_web_sel = media_worker.stage_task_selection(tid, cand_idx=0, source="web")
    assert res_web_sel["ok"] is True
    assert res_web_sel["task"]["status"] == "staged"
    assert res_web_sel["task"]["select_source"] == "web"
    assert res_web_sel["task"]["selected_tmdb_id"] == 438631
    assert jobs._tg_notify_queue.empty() is True

    # 2) 网页撤销暂存后改选第 2 候选 (841) -> 点击「🚀 提交队列」锁定为 queued
    res_undo = media_worker.unstage_task_selection(tid, source="web")
    assert res_undo["ok"] is True and res_undo["task"]["status"] == "need_confirm"
    res_web_sel2 = media_worker.stage_task_selection(tid, cand_idx=1, source="web")
    assert res_web_sel2["ok"] is True and res_web_sel2["task"]["selected_tmdb_id"] == 841

    res_commit = media_worker.commit_staged_tasks(task_ids=[tid], source="web")
    assert res_commit["ok"] is True and res_commit["committed_count"] == 1
    assert db.query("SELECT status FROM media_tasks WHERE id=?", (tid,), one=True)["status"] == "queued"

    # 提交入队后禁止再次修改候选
    res_late = media_worker.stage_task_selection(tid, cand_idx=0, source="web")
    assert res_late["ok"] is False and res_late["code"] == "locked_status"

    # 消费 queued 队列落盘（含本地写 NFO 与海报，零 115 上传），完成后推送纯文本提交结果通知
    pan_ops.clear()
    with patch.object(media_worker, "get_tmdb_detail", return_value=cand2), \
         patch.object(tmdb_scraper, "tmdb_download_image", return_value=b"\xff\xd8\xff\xe0POSTER"), \
         patch.object(pan, "mkdirs", return_value=400), \
         patch.object(pan, "ensure_subdir", side_effect=lambda b, r: (pan_ops.append(("subdir", r)), 401)[1]), \
         patch.object(pan, "list_entries", return_value=[]), \
         patch.object(pan, "rename_entry", side_effect=lambda f, n: pan_ops.append(("rename", n))), \
         patch.object(pan, "move_entries", side_effect=lambda fs, c: pan_ops.append(("move", c))):
        run_res = asyncio.run(media_worker.execute_scrape_queue(mode="commit_only"))

    assert run_res["ok"] is True and run_res["auto"] == 1
    done_row = dict(db.query("SELECT * FROM media_tasks WHERE id=?", (tid,), one=True))
    assert done_row["status"] == "done"
    assert "[tmdbid=841]" in done_row["target_path"]
    assert ("subdir", "沙丘 (1984) [tmdbid=841]") in pan_ops
    assert os.path.exists(os.path.join(done_row["local_meta_dir"], "movie.nfo"))
    assert os.path.exists(os.path.join(done_row["local_meta_dir"], "poster.jpg"))

    commit_msgs = []
    while not jobs._tg_notify_queue.empty():
        commit_msgs.append(jobs._tg_notify_queue.get_nowait())
        jobs._tg_notify_queue.task_done()
    assert len(commit_msgs) == 1
    assert commit_msgs[0].get("reply_markup") is None
    c_txt = commit_msgs[0]["text"]
    assert "`" not in c_txt
    assert "🚀 115 刮削提交队列执行完毕" in c_txt
    assert "沙丘 (1984) [tmdbid=841]（1 部｜NFO + 海报）" in c_txt
    assert "▶️ 主程序转存与云下载已恢复运行" in c_txt


def test_media_api_routes_and_bot_callbacks(tmp_path):
    from fastapi.testclient import TestClient
    from backend import auth, config
    from backend.main import app

    token = auth.make_token("admin")
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as tc:
        # 1) 规则 CRUD 与状态概览
        r_add = tc.post("/api/media/rules", json={
            "name": "测试剧库", "source_dir": "/未刮削剧集", "target_dir": "/剧集库",
            "media_type": "tv", "organize_enabled": True, "scrape_enabled": True, "enabled": True,
        }, headers=headers)
        assert r_add.status_code == 200 and r_add.json()["ok"] is True

        # 顶栏停止时 run 接口必须返回 400
        db.set_setting("running", False)
        r_stop_org = tc.post("/api/media/organize/run", json={}, headers=headers)
        r_stop_scr = tc.post("/api/media/scrape/run", json={}, headers=headers)
        assert r_stop_org.status_code == 400
        assert r_stop_scr.status_code == 400

        # 2) 配置读写与范围校验
        r_cfg = tc.put("/api/media/settings", json={
            "media_append_tmdb_tag": True,
            "media_conflict_policy": "replace_if_better",
            "media_scrape_confidence_threshold": 88,
        }, headers=headers)
        assert r_cfg.status_code == 200
        s_data = tc.get("/api/media/settings", headers=headers).json()
        assert s_data["media_conflict_policy"] == "replace_if_better"
        assert s_data["media_scrape_confidence_threshold"] == 88

        # 3) 创建待确认任务 -> 历史 TG /scid 与 sc* 按钮均已下线且不改状态 -> 网页选片提交生效
        cands = [{"tmdb_id": 215403, "kind": "tv", "title": "狂飙", "year": "2023", "score": 76.0, "reasons": ["年"]}]
        tid = db.insert(
            "INSERT INTO media_tasks(task_kind,media_type,source_dir,orig_name,orig_path,status,candidates_json,created_at,updated_at) "
            "VALUES('scrape','tv','/未刮削剧集','KuangBiao.E01.mkv','/未刮削剧集/KuangBiao.E01.mkv','need_confirm',?,1,1)",
            (json.dumps(cands, ensure_ascii=False),),
        )
        # /scid 已下线，返回引导网页提示，数据库状态保持 need_confirm
        scid_reply = bot_commands._handle_scid_command(f"/scid {tid} tv:215403")
        assert "网页" in scid_reply and "提交队列" in scid_reply
        assert db.query("SELECT status FROM media_tasks WHERE id=?", (tid,), one=True)["status"] == "need_confirm"

        # 历史遗留 TG 卡片按钮 scsel / scsub 只弹窗提示去网页操作，不修改任务也不编辑消息
        calls = []

        class _FakeClient:
            async def post(self, url, json=None):
                calls.append((url.rsplit("/", 1)[-1], json))

                class _R:
                    status_code = 200
                    text = "{}"
                return _R()

        asyncio.run(bot_commands._handle_scrape_callback(
            _FakeClient(), "tok", "-100123",
            {"id": "cq_legacy", "data": f"scsel:{tid}:0", "message": {"message_id": 9101}},
        ))
        assert calls == [
            ("answerCallbackQuery", {
                "callback_query_id": "cq_legacy",
                "text": "刮削确认与提交队列已调整为统一在网页「整理刮削 → 待确认列表」操作",
                "show_alert": True,
            })
        ]
        assert db.query("SELECT status FROM media_tasks WHERE id=?", (tid,), one=True)["status"] == "need_confirm"

        # 网页选片暂存 -> 网页查看 staged -> 撤销暂存 -> 重新选片 -> running=False 拒提交 -> 开启 running 后提交成功
        r_sel = tc.post(f"/api/media/tasks/{tid}/select", json={"cand_idx": 0}, headers=headers)
        assert r_sel.status_code == 200
        tasks_web = tc.get("/api/media/tasks", params={"status": "staged"}, headers=headers).json()
        assert len(tasks_web) == 1 and tasks_web[0]["select_source"] == "web"

        r_un = tc.post(f"/api/media/tasks/{tid}/unstage", headers=headers)
        assert r_un.status_code == 200 and r_un.json()["task"]["status"] == "need_confirm"

        r_sel2 = tc.post(f"/api/media/tasks/{tid}/select", json={"cand_idx": 0}, headers=headers)
        assert r_sel2.status_code == 200
        r_com_stop = tc.post("/api/media/tasks/commit", json={"task_ids": [tid]}, headers=headers)
        assert r_com_stop.status_code == 400

        db.set_setting("running", True)
        with patch.object(media_worker, "schedule_commit_runner_if_idle", return_value=True):
            r_com = tc.post("/api/media/tasks/commit", json={"task_ids": [tid]}, headers=headers)
        assert r_com.status_code == 200 and r_com.json()["committed_count"] == 1
        assert db.query("SELECT status FROM media_tasks WHERE id=?", (tid,), one=True)["status"] == "queued"


def test_conflict_replace_if_better_bdmv_and_cancel_mid_queue(tmp_path):
    rule_id = db.insert(
        "INSERT INTO media_rules(name,source_dir,target_dir,media_type,organize_enabled,scrape_enabled,enabled,created_at) "
        "VALUES('升级与原盘','/来片','/影库','movie',1,1,1,?)",
        (time.time(),),
    )
    db.set_setting("media_conflict_policy", "replace_if_better")

    pan_ops = []

    def fake_walk(root_cid, root_path, max_depth=4):
        return {
            "files": [
                {
                    "id": "6001", "name": "Dune.2021.2160p.WEB-DL.mkv", "size": 20 * 1024**3, "sha1": "NEW2160",
                    "parent_cid": "500", "dir_path": "/来片", "parent_dirs": [],
                    "sibling_names": [], "sibling_entries": [],
                },
                {
                    "id": "6002", "name": "Interstellar.2014.1080p.mkv", "size": 5 * 1024**3, "sha1": "INT",
                    "parent_cid": "500", "dir_path": "/来片", "parent_dirs": [],
                    "sibling_names": [], "sibling_entries": [],
                },
            ],
            "bdmv_dirs": [
                {
                    "id": "7001", "name": "Avatar.2009.BluRay.2160p",
                    "path": "/来片/Avatar.2009.BluRay.2160p",
                    "parent_dirs": [], "parent_cid": "500", "is_bdmv": True,
                }
            ],
            "dirs": [],
        }

    def fake_ensure_subdir(base_cid, rel_dir):
        pan_ops.append(("subdir", rel_dir))
        if rel_dir == "_待清理旧版":
            return 999
        return 601

    def fake_list_entries(cid, limit=2000):
        # 目的目录已存在 720p 旧版本 `Dune (2021).mkv` 应触发高画质洗版搬去 _待清理旧版
        if cid == 601:
            return [{"id": "old720", "name": "Dune (2021) - 2160p.mkv", "size": 2 * 1024**3, "is_dir": False}]
        return []

    def fake_rename(fid, new_name):
        pan_ops.append(("rename", str(fid), new_name))
        # 处理完 BDMV(7001) 或首条后，模拟用户点击顶栏「停止」触发安全中断
        if str(fid) == "7001":
            transfer_gate.request_cancel()

    with patch.object(pan, "resolve_dir_cid", return_value=500), \
         patch.object(pan, "walk_media_tree", side_effect=fake_walk), \
         patch.object(pan, "mkdirs", return_value=600), \
         patch.object(pan, "ensure_subdir", side_effect=fake_ensure_subdir), \
         patch.object(pan, "list_entries", side_effect=fake_list_entries), \
         patch.object(pan, "rename_entry", side_effect=fake_rename), \
         patch.object(pan, "move_entries", side_effect=lambda fs, c: pan_ops.append(("move", [str(x) for x in fs], int(c)))):
        res = asyncio.run(media_worker.execute_organize_queue(rule_ids=[rule_id], trigger="manual"))

    # BDMV 原盘完成后发出 cancel，6002 不应继续执行，且 gate 必须恢复 idle
    assert res["ok"] is False and res.get("cancelled") is True
    assert transfer_gate.get_gate_state() == "idle"
    assert transfer_gate.get_active_mode() == "none"
    assert transfer_gate.is_cancel_requested() is False
    # 验证 BDMV 原盘整体改名移动，且 6001/6002 未在取消后越界处理
    assert ("rename", "7001", "Avatar (2009) - 2160p") in pan_ops
    assert not any(op[0] == "rename" and op[1] == "6002" for op in pan_ops)

    # 独立校验高画质洗版逻辑（不中断，6001 比 old720 清晰度/体积均胜出 -> 旧版进 999）
    pan_ops.clear()
    # 目标名为 Dune (2021) - 2160p.mkv，旧文件为同名但体积 1GB < 新 20GB
    db.execute("DELETE FROM media_tasks")
    with patch.object(pan, "resolve_dir_cid", return_value=500), \
         patch.object(pan, "walk_media_tree", return_value={
             "files": [{"id": "6001", "name": "Dune.2021.2160p.mkv", "size": 20 * 1024**3, "sha1": "NEW", "parent_cid": "500", "dir_path": "/来片", "parent_dirs": [], "sibling_names": [], "sibling_entries": []}],
             "bdmv_dirs": [], "dirs": [],
         }), \
         patch.object(pan, "mkdirs", return_value=600), \
         patch.object(pan, "ensure_subdir", side_effect=fake_ensure_subdir), \
         patch.object(pan, "list_entries", return_value=[{"id": "old1", "name": "Dune (2021) - 2160p.mkv", "size": 2 * 1024**3, "is_dir": False}]), \
         patch.object(pan, "rename_entry", side_effect=lambda f, n: pan_ops.append(("rename", str(f), n))), \
         patch.object(pan, "move_entries", side_effect=lambda fs, c: pan_ops.append(("move", [str(x) for x in fs], int(c)))):
        res2 = asyncio.run(media_worker.execute_organize_queue(rule_ids=[rule_id]))
    assert res2["ok"] is True and res2["done"] == 1
    assert ("move", ["old1"], 999) in pan_ops


def test_tg_notify_worker_sends_cards_and_edits_sequentially_and_local_poster_api(tmp_path):
    from fastapi.testclient import TestClient
    from backend import auth
    from backend.main import app

    # 1) 测试 tg_notify_worker 对 sendMessage(reply_markup+on_sent) 与 editMessageText 的严格顺序消费
    sent_requests = []
    cb_hits = []

    class _FakeResp:
        def __init__(self, status_code=200, mid=777, text="{}"):
            self.status_code = status_code
            self._mid = mid
            self.text = text

        def json(self):
            return {"ok": True, "result": {"message_id": self._mid}}

    class _FakeAsyncClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, **kw):
            endpoint = url.rsplit("/", 1)[-1]
            sent_requests.append((endpoint, kw.get("json")))
            if endpoint == "editMessageText" and len(sent_requests) == 2:
                return _FakeResp(400, 0, "Bad Request: message is not modified")
            return _FakeResp(200, 777)

    jobs.enqueue_tg_message(
        "card1",
        reply_markup={"inline_keyboard": [[{"text": "1", "callback_data": "menu:status"}]]},
        on_sent=lambda mid, cid: cb_hits.append((mid, cid)),
        gap_sec=0.4,
    )
    jobs.enqueue_tg_edit(777, "card1_edited", reply_markup={"inline_keyboard": []}, gap_sec=0.4)

    async def _run_worker_briefly():
        task = asyncio.create_task(jobs.tg_notify_worker())
        for _ in range(40):
            if len(sent_requests) >= 2 and cb_hits:
                break
            await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    with patch("backend.worker.jobs.httpx.AsyncClient", _FakeAsyncClient):
        asyncio.run(_run_worker_briefly())

    assert [r[0] for r in sent_requests] == ["sendMessage", "editMessageText"]
    assert sent_requests[0][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "menu:status"
    assert cb_hits == [(777, "-100123456")]

    # 2) 测试本地 NFO/海报接口与路径穿越防护
    meta_root = tmp_path / "meta_root"
    show_dir = meta_root / "狂飙 (2023) [tmdbid=215403]"
    show_dir.mkdir(parents=True)
    (show_dir / "tvshow.nfo").write_text("<tvshow><title>狂飙</title></tvshow>", encoding="utf-8")
    (show_dir / "poster.jpg").write_bytes(b"\xff\xd8\xff\xe0LOCAL_IMG")
    db.set_setting("media_local_meta_dir", str(meta_root))

    tid = db.insert(
        "INSERT INTO media_tasks(task_kind,orig_name,status,local_meta_dir,created_at,updated_at) VALUES('scrape','a.mkv','done',?,1,1)",
        (str(show_dir),),
    )
    bad_tid = db.insert(
        "INSERT INTO media_tasks(task_kind,orig_name,status,local_meta_dir,created_at,updated_at) VALUES('scrape','b.mkv','done',?,1,1)",
        (str(tmp_path),),  # 超出允许根目录
    )

    token = auth.make_token("admin")
    with TestClient(app) as tc:
        r_meta = tc.get("/api/media/meta/inspect", params={"task_id": tid}, headers={"Authorization": f"Bearer {token}"})
        assert r_meta.status_code == 200 and r_meta.json()["has_poster"] is True
        assert "狂飙" in r_meta.json()["nfos"][0]["content"]

        r_img = tc.get("/api/media/poster/local", params={"task_id": tid, "file": "poster.jpg"}, cookies={"sa_poster": token})
        assert r_img.status_code == 200 and r_img.content.startswith(b"\xff\xd8\xff")

        r_bad = tc.get("/api/media/meta/inspect", params={"task_id": bad_tid}, headers={"Authorization": f"Bearer {token}"})
        assert r_bad.status_code == 403


def test_exclusive_status_fields_drain_immediate_cancel_and_auto_move_409():
    from fastapi.testclient import TestClient
    from backend import auth
    from backend.core import transfer_gate
    from backend.main import app

    db.init()
    db.execute("DELETE FROM transfers")
    transfer_gate.reset_for_tests()

    # 1) 验证 get_exclusive_status 同时包含新前端依赖的键
    ok, _ = transfer_gate.try_begin_exclusive("scrape")
    assert ok is True
    transfer_gate.set_media_progress({"mode": "scrape", "phase": "scanning", "current": 2, "total": 5, "label": "扫描中"})
    st = transfer_gate.get_exclusive_status()
    assert st["exclusive_busy"] is True
    assert st["scrape_running"] is True
    assert st["organize_running"] is False
    assert st["auto_move_running"] is False
    assert st["media_progress"]["current"] == 2
    assert "remaining_total" in st["drain_progress"]
    assert "elapsed_sec" in st["drain_progress"]

    # 2) 刮削占用门闸期间，/api/auto-move/rules 暴露互斥标志且 /api/auto-move/run 返回 409
    db.set_setting("running", True)
    db.set_setting("auto_move_enabled", True)
    token = auth.make_token("admin")
    with TestClient(app) as tc:
        r_rules = tc.get("/api/auto-move/rules", headers={"Authorization": f"Bearer {token}"})
        assert r_rules.status_code == 200
        assert r_rules.json()["active_mode"] == "scrape"
        assert r_rules.json()["exclusive_busy"] is True

        r_run = tc.post("/api/auto-move/run", headers={"Authorization": f"Bearer {token}"})
        assert r_run.status_code == 409

    # 3) 排空等待期间收到安全中断：立刻退出、不暂停转存工作器事件、门闸回到 idle
    db.insert(
        "INSERT INTO transfers(share_code,share_url,title,category,target_dir,status,created_at) "
        "VALUES('c_drain','https://115cdn.com/s/drain','DrainItem','剧集','/电视剧','queued',1)"
    )

    async def _run_cancel_during_drain():
        transfer_gate.clear_cancel()
        worker_ev = transfer_gate._get_worker_event()
        worker_ev.set()

        async def _trigger_cancel():
            await asyncio.sleep(0.05)
            transfer_gate.request_cancel()

        t = asyncio.create_task(_trigger_cancel())
        res = await transfer_gate.prepare_and_drain_transfers(mode="scrape", max_wait_seconds=10.0)
        await t
        assert res is False
        assert worker_ev.is_set() is True
        assert transfer_gate.get_gate_state() == "idle"

    asyncio.run(_run_cancel_during_drain())
    transfer_gate.reset_for_tests()


def test_scrape_groups_ambiguous_tv_episodes_and_risk_circuit_breaker(tmp_path):
    from backend.core import transfer_gate, tmdb_scraper
    from backend.core.media_parser import parse_media_filename
    from backend.worker import media_worker

    db.init()
    db.execute("DELETE FROM media_tasks")
    db.execute("DELETE FROM media_rules")
    db.execute("DELETE FROM organized_records")
    db.execute("DELETE FROM media_tg_batches")
    db.execute("DELETE FROM transfers")
    transfer_gate.reset_for_tests()

    meta_root = tmp_path / "grouped_meta"
    db.set_setting("running", True)
    db.set_setting("tmdb_api_key", "fake_tmdb_key")
    db.set_setting("media_op_delay_min", 0.01)
    db.set_setting("media_op_delay_max", 0.02)
    db.set_setting("media_scrape_confidence_threshold", 85)
    db.set_setting("media_scrape_margin_threshold", 12)
    db.set_setting("media_local_meta_dir", str(meta_root))

    rule_id = db.insert(
        "INSERT INTO media_rules(name,source_dir,target_dir,media_type,organize_enabled,scrape_enabled,enabled,created_at) "
        "VALUES('剧集规则','/待刮削','/电视剧','tv',1,1,1,1)"
    )
    rule_dict = dict(db.query("SELECT * FROM media_rules WHERE id=?", (rule_id,), one=True))

    eps = []
    for ep_n in (1, 2, 3):
        pm = parse_media_filename(f"迷雾之城.S01E0{ep_n}.mkv", is_dir=False)
        pm.media_type = "tv"
        eps.append({
            "rule_id": rule_id,
            "rule_name": "剧集规则",
            "rule_media_type": "tv",
            "source_dir": "/待刮削",
            "source_cid": "100",
            "dest_root": "/电视剧",
            "parent_cid": "101",
            "file_id": f"fid_show_{ep_n}",
            "orig_name": f"迷雾之城.S01E0{ep_n}.mkv",
            "orig_path": f"/待刮削/迷雾之城.S01E0{ep_n}.mkv",
            "size": 1024 * 1024 * 800,
            "sha1": f"sha_{ep_n}",
            "is_bdmv": False,
            "subtitles": [],
            "parsed": pm,
            "ep_stats": {"max_ep": 3, "video_count": 3},
        })

    det1 = {
        "id": 77001,
        "name": "迷雾之城",
        "original_name": "迷雾之城",
        "first_air_date": "2024-02-01",
        "overview": "新版悬疑剧",
        "poster_path": "/p1.jpg",
        "seasons": [{"season_number": 1, "episode_count": 12, "name": "Season 1"}],
    }
    det2 = {
        "id": 77002,
        "name": "迷雾之城",
        "original_name": "迷雾之城",
        "first_air_date": "2018-05-01",
        "overview": "旧版剧集",
        "poster_path": "/p2.jpg",
        "seasons": [{"season_number": 1, "episode_count": 12, "name": "Season 1"}],
    }
    ambiguous_scored = [
        {"tmdb_id": 77001, "kind": "tv", "title": "迷雾之城", "year": "2024", "score": 74.0,
         "poster_path": "/p1.jpg", "overview": "新版", "reasons": ["同名匹配"], "detail": det1},
        {"tmdb_id": 77002, "kind": "tv", "title": "迷雾之城", "year": "2018", "score": 70.0,
         "poster_path": "/p2.jpg", "overview": "旧版", "reasons": ["同名匹配"], "detail": det2},
    ]

    scr_sent = []
    with patch.object(media_worker, "_scan_rules_sync", return_value=([rule_dict], eps)), \
         patch.object(media_worker, "recall_and_score", return_value=ambiguous_scored), \
         patch.object(jobs, "enqueue_tg_message", side_effect=lambda txt, **kw: scr_sent.append(txt)):
        scan_res = asyncio.run(media_worker.execute_scrape_queue(mode="scan_and_run"))

    assert scan_res["ok"] is True
    assert scan_res["need_confirm"] == 1  # 同剧 3 集只生成 1 张剧集级待确认记录
    tasks = db.query("SELECT * FROM media_tasks WHERE task_kind='scrape'")
    assert len(tasks) == 1
    card_row = dict(tasks[0])
    pj = __import__("json").loads(card_row["parsed_json"])
    assert len(pj["_grouped_items"]) == 3
    assert len(scr_sent) == 1
    assert "（共 3 集）" in scr_sent[0]
    assert "待确认列表" in scr_sent[0]
    assert "`" not in scr_sent[0]

    # 再次扫描时，组内全部 3 集 file_id 都被跳过，绝不重复生卡
    with patch.object(media_worker, "_scan_rules_sync", return_value=([rule_dict], eps)), \
         patch.object(jobs, "enqueue_tg_message"):
        rescan = asyncio.run(media_worker.execute_scrape_queue(mode="scan_and_run"))
    assert rescan["need_confirm"] == 0
    assert rescan["skipped"] == 3

    # 暂存第 1 候选 -> 提交队列 -> 执行 commit_only 应一次性落盘 3 集 + 本地全套 .nfo
    st_res = media_worker.stage_task_selection(int(card_row["id"]), cand_idx=0, source="web")
    assert st_res["ok"] is True
    sub_res = media_worker.commit_staged_tasks(source="web")
    assert sub_res["ok"] is True and sub_res["committed_count"] == 1

    placed = []

    def _fake_place(it, rel_dir, target_name, *a, **kw):
        placed.append((it["orig_name"], rel_dir, target_name))
        return {"status": "done", "action": "move", "dest_path": f"/电视剧/{rel_dir}", "dest_name": target_name}

    commit_msgs = []
    with patch.object(media_worker, "_apply_media_placement_sync", side_effect=_fake_place), \
         patch.object(media_worker, "get_tmdb_detail", return_value=det1), \
         patch.object(tmdb_scraper, "get_tv_season_episodes", return_value=[
             {"id": i, "episode_number": i, "name": f"第{i}集", "air_date": "2024-02-01", "overview": "ep"} for i in (1, 2, 3)
         ]), \
         patch.object(tmdb_scraper, "tmdb_download_image", return_value=b"\xff\xd8\xffFAKE"), \
         patch.object(jobs, "enqueue_tg_message", side_effect=lambda txt, **kw: commit_msgs.append(txt)):
        commit_res = asyncio.run(media_worker.execute_scrape_queue(mode="commit_only"))

    assert commit_res["ok"] is True
    assert commit_res["auto"] == 1
    assert len(placed) == 3
    assert len(commit_msgs) == 1
    assert "🚀 115 刮削提交队列执行完毕" in commit_msgs[0]
    assert "迷雾之城 (2024) [tmdbid=77001]（3 集｜NFO + 海报）" in commit_msgs[0]
    show_meta_dir = next(meta_root.iterdir())
    assert (show_meta_dir / "tvshow.nfo").exists()
    assert (show_meta_dir / "Season 01" / "season.nfo").exists()
    assert len(list((show_meta_dir / "Season 01").glob("*.nfo"))) == 4  # season.nfo + E01/E02/E03.nfo

    # 验证连续 2 次命中 115 风控关键字时安全熔断并释放门闸
    tg_alerts = []
    risk_items = eps[:3]
    with patch.object(media_worker, "_scan_rules_sync", return_value=([rule_dict], risk_items)), \
         patch.object(media_worker, "_apply_media_placement_sync", side_effect=RuntimeError("操作过于频繁，触发115风控")), \
         patch.object(jobs, "enqueue_tg_message", side_effect=lambda m, **kw: tg_alerts.append(m)):
        org_res = asyncio.run(media_worker.execute_organize_queue())

    assert org_res["ok"] is False
    assert org_res.get("risk_aborted") is True
    assert org_res["failed"] == 2  # 第2次即熔断，第3项不再发起写请求
    assert any("风控" in str(a) for a in tg_alerts)
    assert transfer_gate.is_exclusive_busy() is False


def test_organize_tg_notification_aggregation_and_schedule_silence():
    from backend.core import transfer_gate
    from backend.core.media_parser import parse_media_filename
    from backend.worker import media_worker

    db.init()
    db.execute("DELETE FROM media_tasks")
    db.execute("DELETE FROM media_rules")
    db.execute("DELETE FROM organized_records")
    db.execute("DELETE FROM transfers")
    transfer_gate.reset_for_tests()

    db.set_setting("running", True)
    db.set_setting("media_op_delay_min", 0.01)
    db.set_setting("media_op_delay_max", 0.02)
    rule_id = db.insert(
        "INSERT INTO media_rules(name,source_dir,target_dir,media_type,organize_enabled,scrape_enabled,enabled,created_at) "
        "VALUES('混合整理','/待整理','/媒体库','auto',1,0,1,1)"
    )
    rule_dict = dict(db.query("SELECT * FROM media_rules WHERE id=?", (rule_id,), one=True))

    # 1) 构造同剧 2 集 + 7 部电影（合计 8 部作品，超过 6 部显示上限）
    items = []
    for ep_n in (1, 2):
        pm = parse_media_filename(f"狂飙.S01E0{ep_n}.[tmdbid=215403].mkv")
        pm.media_type = "tv"
        pm.title = "狂飙"
        pm.year = "2023"
        pm.tmdb_id = 215403
        items.append({
            "rule_id": rule_id, "rule_name": "混合整理", "rule_media_type": "auto",
            "source_dir": "/待整理", "source_cid": "10", "dest_root": "/媒体库",
            "parent_cid": "11", "file_id": f"f_kb_{ep_n}",
            "orig_name": f"狂飙.S01E0{ep_n}.mkv", "orig_path": f"/待整理/狂飙.S01E0{ep_n}.mkv",
            "size": 1000, "sha1": "s1", "is_bdmv": False, "subtitles": [], "parsed": pm,
        })

    for m_idx in range(1, 8):
        pm = parse_media_filename(f"电影{m_idx}.202{m_idx % 5}.mkv")
        pm.media_type = "movie"
        pm.title = f"电影{m_idx}"
        pm.year = f"202{m_idx % 5}"
        items.append({
            "rule_id": rule_id, "rule_name": "混合整理", "rule_media_type": "auto",
            "source_dir": "/待整理", "source_cid": "10", "dest_root": "/媒体库",
            "parent_cid": "11", "file_id": f"f_mv_{m_idx}",
            "orig_name": f"电影{m_idx}.mkv", "orig_path": f"/待整理/电影{m_idx}.mkv",
            "size": 2000, "sha1": f"sm_{m_idx}", "is_bdmv": False, "subtitles": [], "parsed": pm,
        })

    def _fake_place_many(it, rel_dir, fname, *a, **kw):
        sub_cnt = 2 if it["file_id"] == "f_mv_1" else 0
        if a and isinstance(a[-1], list):
            a[-1].append(str(it.get("parent_cid") or "11"))
        return {
            "status": "done",
            "action": "move",
            "dest_path": f"/媒体库/{rel_dir}",
            "dest_name": fname,
            "subtitles_moved": sub_cnt,
        }

    sent_msgs = []
    with patch.object(media_worker, "_scan_rules_sync", return_value=([rule_dict], items)), \
         patch.object(media_worker, "_apply_media_placement_sync", side_effect=_fake_place_many), \
         patch.object(media_worker, "_clean_empty_dirs_sync", return_value=2), \
         patch.object(jobs, "enqueue_tg_message", side_effect=lambda txt, **kw: sent_msgs.append(txt)):
        res = asyncio.run(media_worker.execute_organize_queue(trigger="manual"))

    assert res["ok"] is True and res["done"] == 9
    assert len(sent_msgs) == 1
    msg = sent_msgs[0]
    assert "`" not in msg  # 纯文本无反引号
    assert "📁 115 目录整理完成" in msg
    assert "手动执行" in msg
    assert "空目录清理：已清理 2 个" in msg
    assert "📺 狂飙 (2023) [tmdbid=215403]（2 集）" in msg
    assert "🎬 电影1" in msg and "含 2 个字幕" in msg
    assert "另有 2 部作品（共 2 项）" in msg  # 8 部作品仅展示前 6 部，余 2 部折叠
    assert "操作账本" in msg
    assert "▶️ 主程序转存与云下载已恢复运行" in msg

    # 2) 定时整理 0 移动 0 失败 -> 静默不推 TG；手动触发 0 移动 0 失败 -> 推送简洁反馈
    skip_item = items[0]

    def _fake_skip(*a, **kw):
        return {"status": "skipped", "reason": "已规范", "dest_path": "/媒体库", "dest_name": "a.mkv"}

    sched_msgs = []
    with patch.object(media_worker, "_scan_rules_sync", return_value=([rule_dict], [skip_item])), \
         patch.object(media_worker, "_apply_media_placement_sync", side_effect=_fake_skip), \
         patch.object(jobs, "enqueue_tg_message", side_effect=lambda txt, **kw: sched_msgs.append(txt)):
        res_sched = asyncio.run(media_worker.execute_organize_queue(trigger="schedule"))
    assert res_sched["ok"] is True and res_sched["done"] == 0 and res_sched["failed"] == 0
    assert sched_msgs == []  # 定时无变动静默

    manual_noop_msgs = []
    with patch.object(media_worker, "_scan_rules_sync", return_value=([rule_dict], [skip_item])), \
         patch.object(media_worker, "_apply_media_placement_sync", side_effect=_fake_skip), \
         patch.object(jobs, "enqueue_tg_message", side_effect=lambda txt, **kw: manual_noop_msgs.append(txt)):
        res_man = asyncio.run(media_worker.execute_organize_queue(trigger="manual"))
    assert res_man["ok"] is True and res_man["done"] == 0
    assert len(manual_noop_msgs) == 1
    assert "无需变动" in manual_noop_msgs[0]

    # 3) 定时整理出现失败项 -> 依然推送异常明细，不静默
    sched_fail_msgs = []
    with patch.object(media_worker, "_scan_rules_sync", return_value=([rule_dict], [skip_item])), \
         patch.object(media_worker, "_apply_media_placement_sync", side_effect=RuntimeError("目标目录权限不足")), \
         patch.object(jobs, "enqueue_tg_message", side_effect=lambda txt, **kw: sched_fail_msgs.append(txt)):
        res_fail = asyncio.run(media_worker.execute_organize_queue(trigger="schedule"))
    assert res_fail["ok"] is True and res_fail["failed"] == 1
    assert len(sched_fail_msgs) == 1
    assert "失败摘要" in sched_fail_msgs[0]
    assert "目标目录权限不足" in sched_fail_msgs[0]


def test_scrape_noop_and_abort_tg_reports():
    from backend.core.media_parser import parse_media_filename

    rule_id = db.insert(
        "INSERT INTO media_rules(name,source_dir,target_dir,media_type,organize_enabled,scrape_enabled,enabled,created_at) "
        "VALUES('刮削中断验证','/待刮削','/媒体库','movie',0,1,1,1)"
    )
    rule_dict = dict(db.query("SELECT * FROM media_rules WHERE id=?", (rule_id,), one=True))

    # 1) 0 新增可刮削条目 -> 推送简洁纯文本反馈
    noop_msgs = []
    with patch.object(media_worker, "_scan_rules_sync", return_value=([rule_dict], [])), \
         patch.object(jobs, "enqueue_tg_message", side_effect=lambda txt, **kw: noop_msgs.append(txt)):
        noop_res = asyncio.run(media_worker.execute_scrape_queue(mode="scan_and_run"))
    assert noop_res["ok"] is True and noop_res["auto"] == 0 and noop_res["need_confirm"] == 0
    assert len(noop_msgs) == 1
    assert "无新增可刮削条目" in noop_msgs[0]
    assert "▶️ 主程序转存与云下载已恢复运行" in noop_msgs[0]

    # 2) 中途被顶栏停止中断：已加入待确认的条目仍会在中断报告中通知并确认门闸恢复
    items = []
    for idx in (1, 2):
        pm = parse_media_filename(f"迷雾第{idx}部.2020.mkv")
        pm.media_type = "movie"
        pm.title = f"迷雾第{idx}部"
        pm.year = "2020"
        items.append({
            "rule_id": rule_id, "rule_name": "刮削中断验证", "rule_media_type": "movie",
            "source_dir": "/待刮削", "source_cid": "10", "dest_root": "/媒体库",
            "parent_cid": "11", "file_id": f"f_abort_{idx}",
            "orig_name": f"迷雾第{idx}部.2020.mkv", "orig_path": f"/待刮削/迷雾第{idx}部.2020.mkv",
            "size": 2 * 1024**3, "sha1": f"sa_{idx}", "is_bdmv": False, "subtitles": [], "parsed": pm,
        })

    def _recall_then_cancel(parsed, *a, **kw):
        if parsed.title == "迷雾第2部":
            transfer_gate.request_cancel()
            media_worker._check_not_cancelled()
        return [{
            "tmdb_id": 55001, "kind": "movie", "title": "迷雾第1部", "year": "2020", "score": 68.0,
            "poster_path": "", "overview": "", "reasons": ["年"], "detail": {"id": 55001, "title": "迷雾第1部"},
        }]

    abort_msgs = []
    with patch.object(media_worker, "_scan_rules_sync", return_value=([rule_dict], items)), \
         patch.object(media_worker, "recall_and_score", side_effect=_recall_then_cancel), \
         patch.object(jobs, "enqueue_tg_message", side_effect=lambda txt, **kw: abort_msgs.append(txt)):
        abort_res = asyncio.run(media_worker.execute_scrape_queue(mode="scan_and_run"))

    assert abort_res["ok"] is False and abort_res.get("cancelled") is True
    assert abort_res["need_confirm"] == 1
    assert transfer_gate.is_exclusive_busy() is False
    assert len(abort_msgs) == 1
    assert "⏸ 115 媒体刮削已安全中断" in abort_msgs[0]
    assert "跳过待确认 1 部" in abort_msgs[0]
    assert "迷雾第1部" in abort_msgs[0]
    assert "▶️ 主程序转存与云下载已恢复运行" in abort_msgs[0]


def test_audit_template_injection_and_local_meta_security(tmp_path):
    from fastapi.testclient import TestClient
    from backend import auth
    from backend.main import app
    from backend.core.media_parser import format_media_target, parse_media_filename, validate_organize_template

    for bad_tpl in (
        "{title.__class__.__init__.__globals__}/{title}{ext}",
        "{title[0]}/{title}{ext}",
        "{year:1000000}/{title}{ext}",
        "{unknown_field}/{title}{ext}",
        "{unclosed",
    ):
        with pytest.raises(ValueError):
            validate_organize_template(bad_tpl, kind="movie")

    # 即使数据库被写入畸形模板，format_media_target 也应安全回退默认模板并剥离退化空段
    pm = parse_media_filename("沙丘.2021.1080p.mkv")
    rel_dir, fname = format_media_target(pm, {"media_organize_template_movie": "{title.__class__}/{title}{ext}"})
    assert "__" not in rel_dir and "沙丘 (2021)" in rel_dir and fname.startswith("沙丘 (2021)")

    rel_dot, fn_dot = format_media_target(pm, {"media_organize_template_movie": "../{title}/{title}{ext}"})
    assert ".." not in rel_dot.split("/") and not rel_dot.startswith("/") and fn_dot.endswith(".mkv")

    tc = TestClient(app)
    headers = {"Authorization": f"Bearer {auth.make_token('admin')}"}
    r_bad_tpl = tc.put("/api/media/settings", json={"media_organize_template_movie": "{title.__class__}"}, headers=headers)
    assert r_bad_tpl.status_code == 400

    # 禁止将本地元数据根目录设为系统根目录或 backend 源码目录
    root_path = os.path.abspath(os.sep)
    r_root = tc.put("/api/media/settings", json={"media_local_meta_dir": root_path}, headers=headers)
    assert r_root.status_code == 400
    backend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
    r_code = tc.put("/api/media/settings", json={"media_local_meta_dir": backend_dir}, headers=headers)
    assert r_code.status_code == 400

    # _resolve_task_meta_dir 禁止 task 指向元数据根目录本身
    meta_root = tmp_path / "safe_meta_root"
    meta_root.mkdir(parents=True)
    db.set_setting("media_local_meta_dir", str(meta_root))
    tid = db.insert(
        "INSERT INTO media_tasks(task_kind,orig_name,status,local_meta_dir,created_at,updated_at) VALUES('scrape','a.mkv','done',?,1,1)",
        (str(meta_root),),
    )
    r_base = tc.get(f"/api/media/meta/inspect?task_id={tid}", headers=headers)
    assert r_base.status_code == 403


def test_audit_transfer_hints_success_and_xml_sanitization(tmp_path):
    db.execute(
        "INSERT INTO subscriptions(keyword,aliases,last_title,kind,tmdb_id,enabled,created_at) "
        "VALUES('漫长的季节','[\"The Long Season\"]','漫长的季节.S01E12','tv',224365,1,1700000000)"
    )
    db.execute(
        "INSERT INTO transfers(share_code,title,category,target_dir,status,created_at) VALUES('sc_ok','三体.2023.2160p','电视剧','/剧集','success',1700000000)"
    )
    hints_by_title, _ = media_worker._load_db_transfer_hints()
    sub_key = pan.normalize_title("The Long Season", fuzzy_t2s=True)
    assert sub_key in hints_by_title and hints_by_title[sub_key]["tmdb_id"] == 224365
    norm_key = pan.normalize_title("三体", fuzzy_t2s=True)
    assert norm_key in hints_by_title and hints_by_title[norm_key]["kind"] == "tv"

    # XML 1.0 非法控制字符不应触发 minidom ExpatError
    nfo_xml = tmdb_scraper.build_movie_nfo({"id": 999, "title": "测试\x00\x0b片名", "overview": "简介\x08内容"})
    assert "测试片名" in nfo_xml and "简介内容" in nfo_xml
    assert tmdb_scraper.tmdb_download_image("/../etc/passwd.jpg") is None


def test_audit_undo_recreate_conflict_skip_and_backup(tmp_path):
    # 1) 撤销时若原父目录已被清理，应按 orig_path 自动重建后再移回
    rec_id = db.insert(
        "INSERT INTO organized_records(task_id,file_id,parent_cid,orig_path,orig_name,dest_cid,dest_path,dest_name,action,batch_id,undone,created_at) "
        "VALUES(1,'f_u1','999','/待整理/旧片Dir/old.mkv','old.mkv','200','/媒体库/Movie (2024)','Movie (2024).mkv','move_rename','org_b1',0,1)"
    )
    calls = []

    def fake_move(fids, target_cid):
        calls.append(("move", list(fids), target_cid))
        if target_cid == 999:
            raise pan.Pan115Error("目录不存在")

    with patch.object(pan, "move_entries", side_effect=fake_move), \
         patch.object(pan, "mkdirs", return_value=1001) as mock_mkdirs, \
         patch.object(pan, "rename_entry") as mock_ren:
        undone, failed = media_worker._undo_records_sync([dict(db.query("SELECT * FROM organized_records WHERE id=?", (rec_id,), one=True))])

    assert undone == 1 and failed == 0
    mock_mkdirs.assert_called_once_with("/待整理/旧片Dir")
    mock_ren.assert_called_once_with("f_u1", "old.mkv")
    assert db.query("SELECT undone FROM organized_records WHERE id=?", (rec_id,), one=True)["undone"] == 1

    # 2) 刮削冲突跳过不得伪装为 done / 自动刮削成功
    db.set_setting("media_conflict_policy", "skip")
    db.set_setting("media_local_meta_dir", str(tmp_path / "skip_meta"))
    rule_id = db.insert(
        "INSERT INTO media_rules(name,source_dir,target_dir,media_type,organize_enabled,scrape_enabled,enabled,created_at) "
        "VALUES('冲突测试','/待刮削','/媒体库','movie',0,1,1,1)"
    )
    rule_dict = dict(db.query("SELECT * FROM media_rules WHERE id=?", (rule_id,), one=True))
    from backend.core.media_parser import parse_media_filename
    pm = parse_media_filename("沙丘.2021.mkv")
    pm.media_type = "movie"
    item = {
        "rule_id": rule_id, "rule_name": "冲突测试", "rule_media_type": "movie",
        "source_dir": "/待刮削", "source_cid": "10", "dest_root": "/媒体库",
        "parent_cid": "11", "file_id": "f_new_1",
        "orig_name": "沙丘.2021.mkv", "orig_path": "/待刮削/沙丘.2021.mkv",
        "size": 2 * 1024**3, "sha1": "s1", "is_bdmv": False, "subtitles": [], "parsed": pm,
    }
    det = {"id": 438631, "title": "沙丘", "release_date": "2021-09-15"}
    cands = [{"tmdb_id": 438631, "kind": "movie", "title": "沙丘", "year": "2021", "score": 96.0, "detail": det}]
    tg_msgs = []
    with patch.object(media_worker, "_scan_rules_sync", return_value=([rule_dict], [item])), \
         patch.object(media_worker, "recall_and_score", return_value=cands), \
         patch.object(pan, "mkdirs", return_value=20), \
         patch.object(pan, "ensure_subdir", return_value=21), \
         patch.object(pan, "list_entries", return_value=[{"id": "f_existing_99", "name": "沙丘 (2021).mkv", "is_dir": False, "size": 2 * 1024**3}]), \
         patch.object(jobs, "enqueue_tg_message", side_effect=lambda t, **kw: tg_msgs.append(t)):
        res_skip = asyncio.run(media_worker.execute_scrape_queue(mode="scan_and_run"))

    assert res_skip["ok"] is True and res_skip["auto"] == 0 and res_skip["skipped"] == 1
    row_st = db.query("SELECT status FROM media_tasks WHERE file_id='f_new_1'", one=True)["status"]
    assert row_st == "skipped"
    assert tg_msgs and "✅ 已刮削入库" not in tg_msgs[0]

    # 3) 备份与恢复应包含 media_rules 与 organized_records
    db.set_setting("running", False)
    payload = db.backup_payload()
    assert len(payload.get("media_rules") or []) == 1
    assert len(payload.get("organized_records") or []) == 1
    db.execute("DELETE FROM media_rules")
    db.execute("DELETE FROM organized_records")
    stats = db.restore_payload(payload)
    assert stats["media_rules"] == 1 and stats["organized_records"] == 1
    assert db.query("SELECT count(*) c FROM media_rules", one=True)["c"] == 1


def test_audit_commit_wakeup_race_and_tv_cross_run_grouping(tmp_path):
    db.set_setting("media_local_meta_dir", str(tmp_path / "group_meta"))
    rule_id = db.insert(
        "INSERT INTO media_rules(name,source_dir,target_dir,media_type,organize_enabled,scrape_enabled,enabled,created_at) "
        "VALUES('跨批分组','/待刮削','/媒体库','tv',0,1,1,1)"
    )
    rule_dict = dict(db.query("SELECT * FROM media_rules WHERE id=?", (rule_id,), one=True))
    from backend.core.media_parser import parse_media_filename

    def _tv_item(ep):
        pm = parse_media_filename(f"神秘剧.S01E0{ep}.mkv")
        pm.media_type = "tv"
        pm.title = "神秘剧"
        pm.season = 1
        pm.episode = ep
        return {
            "rule_id": rule_id, "rule_name": "跨批分组", "rule_media_type": "tv",
            "source_dir": "/待刮削", "source_cid": "10", "dest_root": "/媒体库",
            "parent_cid": "11", "file_id": f"tv_f_{ep}",
            "orig_name": f"神秘剧.S01E0{ep}.mkv", "orig_path": f"/待刮削/神秘剧.S01E0{ep}.mkv",
            "size": 1024**3, "sha1": f"tvs{ep}", "is_bdmv": False, "subtitles": [], "parsed": pm,
        }

    low_cands = [{"tmdb_id": 88001, "kind": "tv", "title": "神秘剧", "year": "2024", "score": 68.0, "detail": {"id": 88001, "name": "神秘剧"}}]
    with patch.object(media_worker, "_scan_rules_sync", return_value=([rule_dict], [_tv_item(1)])), \
         patch.object(media_worker, "recall_and_score", return_value=low_cands), \
         patch.object(jobs, "enqueue_tg_message"):
        r1 = asyncio.run(media_worker.execute_scrape_queue(mode="scan_and_run"))
    assert r1["need_confirm"] == 1

    # 第二轮扫描同剧第2集：应合并到现有 need_confirm 剧集卡片，而非重复建卡
    with patch.object(media_worker, "_scan_rules_sync", return_value=([rule_dict], [_tv_item(2)])), \
         patch.object(media_worker, "recall_and_score", return_value=low_cands), \
         patch.object(jobs, "enqueue_tg_message"):
        r2 = asyncio.run(media_worker.execute_scrape_queue(mode="scan_and_run"))
    assert r2["ok"] is True
    tv_rows = db.query("SELECT id, parsed_json FROM media_tasks WHERE task_kind='scrape' AND status='need_confirm'")
    assert len(tv_rows) == 1
    grp_items = json.loads(tv_rows[0]["parsed_json"]).get("_grouped_items") or []
    assert len(grp_items) == 2

    # 2) 验证提交队列在清理收尾窗口期间不丢唤醒（rerun_committed -> 自动续消费）
    async def _run_race():
        tid_late = db.insert(
            "INSERT INTO media_tasks(task_kind,rule_id,media_type,source_dir,source_cid,file_id,orig_name,orig_path,"
            "parsed_json,status,selected_tmdb_id,selected_kind,selected_title,created_at,updated_at) "
            "VALUES('scrape',?,'movie','/待刮削','10','late_f1','延迟电影.2024.mkv','/待刮削/延迟电影.2024.mkv',"
            "'{\"title\":\"延迟电影\",\"year\":\"2024\",\"media_type\":\"movie\",\"ext\":\".mkv\"}','staged',77701,'movie','延迟电影',1,1)",
            (rule_id,),
        )
        tid_early = db.insert(
            "INSERT INTO media_tasks(task_kind,rule_id,media_type,source_dir,source_cid,file_id,orig_name,orig_path,"
            "parsed_json,status,selected_tmdb_id,selected_kind,selected_title,created_at,updated_at) "
            "VALUES('scrape',?,'movie','/待刮削','10','early_f1','先发电影.2024.mkv','/待刮削/先发电影.2024.mkv',"
            "'{\"title\":\"先发电影\",\"year\":\"2024\",\"media_type\":\"movie\",\"ext\":\".mkv\"}','queued',77702,'movie','先发电影',1,1)",
            (rule_id,),
        )
        # 确保存在 moved_parent_cids 触发 _clean_empty_dirs_sync（模拟收尾清理窗口提交）
        triggered_during_clean = {"done": False}

        def _clean_and_commit(*a, **kw):
            if not triggered_during_clean["done"]:
                triggered_during_clean["done"] = True
                res_com = media_worker.commit_staged_tasks(task_ids=[tid_late], source="web")
                assert res_com["ok"] is True
                # 此时 scrape 仍在运行中，schedule_commit_runner_if_idle 返回 False 并记录补唤醒请求
                assert media_worker.schedule_commit_runner_if_idle() is False
            return 0

        async def _fake_finalize(task_row, *a, **kw):
            fid = int(task_row["id"])
            db.execute("UPDATE media_tasks SET status='done', updated_at=?, finished_at=? WHERE id=?", (time.time(), time.time(), fid))
            return {
                "status": "done", "dest_path": "/媒体库/电影", "dest_paths": ["/媒体库/电影"],
                "dest_name": task_row["orig_name"], "title": task_row.get("selected_title"), "year": "2024",
                "tmdb_id": task_row.get("selected_tmdb_id"), "kind": "movie", "ok_count": 1,
                "subtitles_moved": 0, "has_nfo": True, "has_poster": True, "is_bdmv": False,
            }

        # 让 _finalize_scraped_task 触发 moved_parent_cids 难以直接注入，改用 patch get_tmdb_detail + placement
        with patch.object(media_worker, "get_tmdb_detail", return_value={"id": 777, "title": "电影", "release_date": "2024-01-01"}), \
             patch.object(media_worker, "_apply_media_placement_sync", side_effect=lambda it, rel, fn, st, bid, tid, *a, **kw: (a[-1].append("99") if len(a) >= 4 and isinstance(a[-1], list) else None) or {"status": "done", "action": "move", "dest_path": "/媒体库/电影", "dest_name": fn, "subtitles_moved": 0}), \
             patch.object(media_worker, "_clean_empty_dirs_sync", side_effect=_clean_and_commit), \
             patch.object(jobs, "enqueue_tg_message"):
            media_worker._ACTIVE_TASK_REF["scrape"] = asyncio.create_task(media_worker.execute_scrape_queue(mode="commit_only"))
            await media_worker._ACTIVE_TASK_REF["scrape"]
            await asyncio.sleep(0.05)
            t2 = media_worker._ACTIVE_TASK_REF.get("scrape")
            if t2:
                await t2

        late_status = db.query("SELECT status FROM media_tasks WHERE id=?", (tid_late,), one=True)["status"]
        early_status = db.query("SELECT status FROM media_tasks WHERE id=?", (tid_early,), one=True)["status"]
        assert early_status == "done" and late_status == "done"

    asyncio.run(_run_race())
