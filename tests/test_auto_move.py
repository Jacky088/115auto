"""自动文件整理（按目录映射移动中文文件）功能与三阶段门禁测试集"""
import asyncio
import os
import re
import sys
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="autosave_test_move_")

from backend import database as db
from backend.core import transfer_gate
from backend.core import episode
from backend.pan115 import client as pan
from backend.worker import jobs
from backend.api import manage


def test_parse_season_number():
    # 用户明确要求：Season01、S01、S1 都应理解为第一季，大小写不影响
    assert pan.parse_season_number("Season01") == 1
    assert pan.parse_season_number("season01") == 1
    assert pan.parse_season_number("SEASON01") == 1
    assert pan.parse_season_number("Season1") == 1
    assert pan.parse_season_number("season1") == 1
    assert pan.parse_season_number("Season 01") == 1
    assert pan.parse_season_number("Season 1") == 1
    assert pan.parse_season_number("Season.01") == 1
    assert pan.parse_season_number("Season-01") == 1
    assert pan.parse_season_number("Season_01") == 1
    assert pan.parse_season_number("S01") == 1
    assert pan.parse_season_number("s01") == 1
    assert pan.parse_season_number("S1") == 1
    assert pan.parse_season_number("s1") == 1
    assert pan.parse_season_number("[Season 1]") == 1
    assert pan.parse_season_number("(S01)") == 1
    assert pan.parse_season_number("第一季") == 1
    assert pan.parse_season_number("第1季") == 1
    assert pan.parse_season_number("第01季") == 1
    assert pan.parse_season_number("第 1 季") == 1
    # 第二季测试
    assert pan.parse_season_number("Season02") == 2
    assert pan.parse_season_number("S02") == 2
    assert pan.parse_season_number("s02") == 2
    assert pan.parse_season_number("S2") == 2
    assert pan.parse_season_number("s2") == 2
    assert pan.parse_season_number("第二季") == 2
    assert pan.parse_season_number("第2季") == 2
    # 特别篇测试
    assert pan.parse_season_number("特别篇") == 0
    assert pan.parse_season_number("Specials") == 0
    assert pan.parse_season_number("SP") == 0
    assert pan.parse_season_number("sp") == 0
    assert pan.parse_season_number("折月亮 (2026)") is None
    assert pan.parse_season_number("庆余年") is None


def test_filename_season_parsing():
    # 用户明确要求：文件名称内 S1 或者 S01 都应理解为第一季，大小写不影响
    test_cases = [
        # S01 / s01 / S1 / s1
        ("折月亮.S01E12.第12集.2160p.WEB-DL.H265.DDP 5.1 Atmos.mkv", 1),
        ("折月亮.s01e12.第12集.2160p.mkv", 1),
        ("折月亮.S1E12.第12集.mkv", 1),
        ("折月亮.s1e12.第12集.mkv", 1),
        ("折月亮.S01.第12集.2160p.mkv", 1),
        ("折月亮.s01.第12集.2160p.mkv", 1),
        ("折月亮.S1.第12集.2160p.mkv", 1),
        ("折月亮.s1.第12集.2160p.mkv", 1),
        ("折月亮.Season01.第12集.mkv", 1),
        ("折月亮.season01.第12集.mkv", 1),
        ("折月亮.Season1.第12集.mkv", 1),
        ("折月亮.season1.第12集.mkv", 1),
        ("折月亮.Season 01.第12集.mkv", 1),
        ("折月亮.Season 1.第12集.mkv", 1),
        ("[S01] 折月亮.第12集.mkv", 1),
        ("[s01] 折月亮.第12集.mkv", 1),
        ("[S1] 折月亮.第12集.mkv", 1),
        ("[s1] 折月亮.第12集.mkv", 1),
        ("折月亮 S01 1080p.mkv", 1),
        ("折月亮 S1 1080p.mkv", 1),
        ("折月亮-S01-12.mkv", 1),
        ("折月亮_S1_12.mkv", 1),
        # 中文季名
        ("折月亮.第一季.第12集.mkv", 1),
        ("折月亮.第1季.第12集.mkv", 1),
        ("折月亮.第01季.第12集.mkv", 1),
        # 第二季
        ("折月亮.S02E01.mkv", 2),
        ("折月亮.s02e01.mkv", 2),
        ("折月亮.S2E01.mkv", 2),
        ("折月亮.s2e01.mkv", 2),
        ("折月亮.S02.第01集.mkv", 2),
        ("折月亮.S2.第01集.mkv", 2),
        ("折月亮.第二季.第01集.mkv", 2),
        ("折月亮.第2季.第01集.mkv", 2),
        # 特别篇
        ("折月亮.特别篇.mkv", 0),
        ("折月亮.SP01.mkv", 0),
    ]
    for fname, expected_season in test_cases:
        season, ep, kind = episode.parse(fname)
        assert season == expected_season, f"Filename '{fname}' got season {season}, expected {expected_season}"


def test_extract_target_folder_name():
    assert pan.extract_target_folder_name("/电视剧/庆余年") == "庆余年"
    assert pan.extract_target_folder_name("/电视剧/庆余年/") == "庆余年"
    assert pan.extract_target_folder_name("庆余年") == "庆余年"
    assert pan.extract_target_folder_name("/媒体库/华语剧/甄嬛传") == "甄嬛传"
    # 年份后缀剥离
    assert pan.extract_target_folder_name("/电视剧/繁花 (2023)") == "繁花"
    assert pan.extract_target_folder_name("/电影/流浪地球2 [2023]") == "流浪地球2"
    assert pan.extract_target_folder_name("/电视剧/折月亮 (2026)") == "折月亮"
    # 季目录智能回溯剧名
    assert pan.extract_target_folder_name("/电视剧/折月亮 (2026)/第一季") == "折月亮"
    assert pan.extract_target_folder_name("/电视剧/折月亮 (2026)/第01季") == "折月亮"
    assert pan.extract_target_folder_name("/电视剧/折月亮 (2026)/Season 1") == "折月亮"
    assert pan.extract_target_folder_name("/电视剧/折月亮 (2026)/特别篇") == "折月亮"
    assert pan.extract_target_folder_name("/电视剧/庆余年/Season 1") == "庆余年"
    assert pan.extract_target_folder_name("/电视剧/庆余年/S01") == "庆余年"
    assert pan.extract_target_folder_name("/") == ""
    assert pan.extract_target_folder_name("") == ""
    assert pan.extract_target_folder_name("   ") == ""


def test_chinese_matching_logic():
    chinese_re = pan._CHINESE_CHAR_RE
    media_exts = pan._MEDIA_EXTENSIONS
    extra_kws = pan._EXTRA_KEYWORDS

    def is_match(name: str, kw: str) -> bool:
        if not name:
            return False
        ext = os.path.splitext(name)[1].lower()
        if ext not in media_exts:
            return False
        if not chinese_re.search(name):
            return False
        name_lower = name.lower()
        for extra in extra_kws:
            if extra not in kw.lower() and extra in name_lower:
                return False
        norm_kw = pan.normalize_title(kw, fuzzy_t2s=True)
        norm_name = pan.normalize_title(name, fuzzy_t2s=True)
        return bool(norm_kw and norm_kw in norm_name)

    kw = "庆余年"

    # 正例：包含中文、媒体扩展名、包含目标名（支持空格/符号软化）
    assert is_match("庆余年S01E01.mp4", kw) is True
    assert is_match("[4K] 庆余年 第01集.mkv", kw) is True
    assert is_match("庆余年.第一季.1080p.mp4", kw) is True
    assert is_match("庆 余 年 S01E01.mp4", kw) is True
    assert is_match("庆余年_01.ts", kw) is True

    # 用户真实用例验证：折月亮
    assert is_match("折月亮.S01E12.第12集.2160p.WEB-DL.H265.DDP 5.1 Atmos.mkv", "折月亮") is True

    # 简繁体互通自适应匹配
    assert is_match("慶餘年.S01E01.mp4", "庆余年") is True
    assert is_match("庆余年.S01E01.mp4", "慶餘年") is True
    assert is_match("摺月亮.S01E12.第12集.mkv", "折月亮") is True

    # 罗马数字与阿拉伯数字软化匹配测试
    assert is_match("流浪地球.2.2023.2160p.mkv", "流浪地球2") is True
    assert is_match("流浪地球Ⅱ.mp4", "流浪地球2") is True
    assert is_match("流浪地球2.mp4", "流浪地球Ⅱ") is True

    # 扩展名白名单拦截：广告、说明文本、海报、种子等坚决不移动
    assert is_match("庆余年.扫码关注微信.txt", kw) is False
    assert is_match("庆余年第二季.nfo", kw) is False
    assert is_match("庆余年海报.jpg", kw) is False
    assert is_match("庆余年.torrent", kw) is False

    # 预告、花絮非正片拦截
    assert is_match("庆余年.预告片.mp4", kw) is False
    assert is_match("庆余年-花絮01.mp4", kw) is False
    assert is_match("庆余年.sample.mkv", kw) is False

    # 反例：不含中文（即便包含关键字或在对应文件夹里）
    assert is_match("1.mp4", kw) is False
    assert is_match("S01E01.mp4", kw) is False
    assert is_match("EP01.mkv", kw) is False
    assert is_match("Joy.of.Life.S01E01.mp4", kw) is False

    # 反例：包含中文，但不是庆余年
    assert is_match("甄嬛传S01E01.mp4", kw) is False
    assert is_match("权力的游戏S01E01.mp4", kw) is False


def test_extract_chinese_subject():
    assert pan.extract_chinese_subject("Doctor X: 白色黑手党时代") == "白色黑手党时代"
    assert pan.extract_chinese_subject("权力的游戏 Game of Thrones") == "权力的游戏"
    assert pan.extract_chinese_subject("Game of Thrones 权力的游戏") == "权力的游戏"
    assert pan.extract_chinese_subject("神探夏洛克 Sherlock") == "神探夏洛克"
    assert pan.extract_chinese_subject("怪奇物语 Stranger Things") == "怪奇物语"
    assert pan.extract_chinese_subject("繁花") == "繁花"
    assert pan.extract_chinese_subject("Doctor X: 剧场版") == ""


def test_mixed_cn_en_matching():
    mock_client = MagicMock()
    mock_client.fs_move.return_value = {"state": True}

    with patch.object(pan, "_get_client", return_value=mock_client), \
         patch.object(pan, "mkdirs", side_effect=lambda p: 300 if "Doctor" in p else 100), \
         patch.object(pan, "list_file_names", return_value=set()), \
         patch("time.sleep", return_value=None):

        def fake_list_entries(cid, limit=2000):
            if cid == 100:
                return [
                    # 1. 完整中英文命中（截图情况）
                    {"id": "m1", "name": "Doctor X: 白色黑手党时代.S01E05.第5集.1080p.WEB-DL.x264.AAC.mkv", "is_dir": False},
                    # 2. 只有中文主体，无英文 Doctor X
                    {"id": "m2", "name": "白色黑手党时代.S01E05.第5集.1080p.mkv", "is_dir": False},
                    # 3. 点号连接的中英文
                    {"id": "m3", "name": "Doctor.X.白色黑手党时代.S01E05.mkv", "is_dir": False},
                    # 4. 纯英文无中文，坚决拦截
                    {"id": "m4", "name": "Doctor.X.S01E05.1080p.mkv", "is_dir": False},
                    # 5. 只有英文剧名+第5集，无副标题，防跨季串门拦截
                    {"id": "m5", "name": "Doctor.X.S01E05.第5集.mkv", "is_dir": False},
                    # 6. 其他剧
                    {"id": "m6", "name": "甄嬛传.S01E01.mp4", "is_dir": False},
                ]
            return []

        with patch.object(pan, "list_entries", side_effect=fake_list_entries):
            moved_count, samples = pan.move_matched_files_for_rule(
                "/自动转存", "/电视剧/Doctor X: 白色黑手党时代 (2026)", mode="exact", scan_subdirs=False
            )
            # m1, m2, m3 应该成功移动！
            assert moved_count == 3
            called_ids, kwargs = mock_client.fs_move.call_args
            assert set(called_ids[0]) == {"m1", "m2", "m3"}
            assert kwargs.get("pid") == 300


def test_move_matched_files_exact_mock():
    mock_client = MagicMock()
    mock_client.fs_move.return_value = {"state": True}

    with patch.object(pan, "_get_client", return_value=mock_client), \
         patch.object(pan, "mkdirs", side_effect=lambda p: 200 if "庆余年" in p else 100), \
         patch.object(pan, "list_file_names", return_value=set()), \
         patch("time.sleep", return_value=None):

        def fake_list_entries(cid, limit=2000):
            if cid == 100:
                return [
                    {"id": "1", "name": "庆余年S01E01.mp4", "is_dir": False},
                    {"id": "2", "name": "1.mp4", "is_dir": False},
                    {"id": "3", "name": "庆余年 第一季", "is_dir": True},
                ]
            elif cid == 3:
                return [
                    {"id": "4", "name": "庆余年S01E02.mp4", "is_dir": False},
                    {"id": "5", "name": "readme.txt", "is_dir": False},
                ]
            return []

        with patch.object(pan, "list_entries", side_effect=fake_list_entries):
            moved_count, samples = pan.move_matched_files_for_rule(
                "/自动转存", "/电视剧/庆余年", mode="exact", scan_subdirs=True
            )
            assert moved_count == 2
            mock_client.fs_move.assert_called_once()
            called_ids, kwargs = mock_client.fs_move.call_args
            assert set(called_ids[0]) == {"1", "4"}
            assert kwargs.get("pid") == 200


def test_move_matched_files_library_mode():
    mock_client = MagicMock()
    mock_client.fs_move.return_value = {"state": True}

    with patch.object(pan, "_get_client", return_value=mock_client), \
         patch.object(pan, "mkdirs", side_effect=lambda p: 200 if p == "/电视剧" else 100), \
         patch.object(pan, "list_file_names", return_value=set()), \
         patch("time.sleep", return_value=None):

        def fake_list_entries(cid, limit=2000):
            if cid == 200:  # 总库 /电视剧 下的已有剧集文件夹
                return [
                    {"id": "201", "name": "折月亮 (2026)", "is_dir": True},
                    {"id": "202", "name": "繁花 (2023)", "is_dir": True},
                    {"id": "203", "name": "其他", "is_dir": True},
                ]
            elif cid == 201:  # 折月亮 下的季文件夹
                return [
                    {"id": "2011", "name": "Season 1", "is_dir": True},
                ]
            elif cid == 202:  # 繁花 下无季文件夹
                return []
            elif cid == 100:  # 源目录 /自动转存
                return [
                    # 应该命中 折月亮，并按季自动进入 Season 1 (cid 2011)
                    {"id": "f1", "name": "折月亮.S01E12.第12集.2160p.WEB-DL.H265.DDP 5.1 Atmos.mkv", "is_dir": False},
                    # 应该命中 繁花，直接进入 繁花 根目录 (cid 202)
                    {"id": "f2", "name": "繁花.S01E01.2023.1080p.mp4", "is_dir": False},
                    # 库里没有该剧目录，保留在原地不乱移
                    {"id": "f3", "name": "大奉打更人.S01E01.mp4", "is_dir": False},
                    # 无中文，拦截
                    {"id": "f4", "name": "1.mp4", "is_dir": False},
                    # 非媒体，拦截
                    {"id": "f5", "name": "折月亮.扫码微信.txt", "is_dir": False},
                    # 预告，拦截
                    {"id": "f6", "name": "折月亮.预告片.mp4", "is_dir": False},
                ]
            return []

        with patch.object(pan, "list_entries", side_effect=fake_list_entries):
            moved_count, samples = pan.move_matched_files_for_rule(
                "/自动转存", "/电视剧", mode="library", season_folder=True, scan_subdirs=False
            )
            assert moved_count == 2
            # 应该调用两次移动：一次给 折月亮 Season 1 (pid=2011)，一次给 繁花 (pid=202)
            assert mock_client.fs_move.call_count == 2
            call_pids = {call.kwargs.get("pid"): call.args[0] for call in mock_client.fs_move.call_args_list}
            assert 2011 in call_pids and call_pids[2011] == ["f1"]
            assert 202 in call_pids and call_pids[202] == ["f2"]


def test_move_matched_files_invalid_target():
    try:
        pan.move_matched_files_for_rule("/自动转存", "/", keyword="", mode="exact")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "未包含有效文件夹名称" in str(e)


def test_switch_enforcement():
    db.init()
    transfer_gate.reset_for_tests()

    # 1. 系统停止时，受顶栏总开关约束拒绝开启与执行
    db.set_setting("running", "false")
    res_stopped = asyncio.run(jobs.execute_auto_move(trigger="manual"))
    assert res_stopped["ok"] is False
    assert "系统处于停止状态" in res_stopped["msg"]

    # 2. 系统运行时，若自动归档未开启，拒绝执行
    db.set_setting("running", "true")
    db.set_setting("auto_move_enabled", False)
    res = asyncio.run(jobs.execute_auto_move(trigger="manual"))
    assert res["ok"] is False
    assert "未开启" in res["msg"]

    # 3. 默认关闭时，API run_auto_move_now 抛出 400
    try:
        asyncio.run(manage.run_auto_move_now())
        assert False, "Should have raised HTTPException 400"
    except manage.HTTPException as e:
        assert e.status_code == 400
        assert "未开启" in str(e.detail)

    # 4. 验证用户主动停止或系统致命故障停止时，auto_move_enabled 联动重置为 False
    db.set_setting("running", "true")
    db.set_setting("auto_move_enabled", True)
    manage.system_stop()
    assert str(db.get_setting("running")).lower() == "false"
    assert str(db.get_setting("auto_move_enabled")).lower() == "false"

    db.set_setting("running", "true")
    db.set_setting("auto_move_enabled", True)
    jobs.stop_system("测试故障停止")
    assert str(db.get_setting("running")).lower() == "false"
    assert str(db.get_setting("auto_move_enabled")).lower() == "false"


def test_phase_gating_and_drain():
    db.init()
    transfer_gate.reset_for_tests()

    # 清空测试表
    db.execute("DELETE FROM transfers")

    async def scenario():
        # 1. 放入 2 个 queued 任务，模拟转存正在排队
        db.execute(
            "INSERT INTO transfers(share_code, title, status, created_at) VALUES (?, ?, 'queued', ?)",
            ("code1", "庆余年S01E01", time.time())
        )
        db.execute(
            "INSERT INTO transfers(share_code, title, status, created_at) VALUES (?, ?, 'queued', ?)",
            ("code2", "庆余年S01E02", time.time())
        )

        # 异步启动排空准备
        drain_task = asyncio.create_task(transfer_gate.prepare_and_drain_transfers(max_wait_seconds=10.0))

        # 检查：此时已进入 draining 状态，intake 已关门（停止接收新任务）
        await asyncio.sleep(0.1)
        assert transfer_gate.is_draining() is True
        assert transfer_gate.get_gate_state() == "draining"

        # 模拟工作器处理第 1 个任务：置为 success
        db.execute("UPDATE transfers SET status='success' WHERE share_code='code1'")
        await asyncio.sleep(0.1)
        # 仍然还有 1 个 queued，排空尚未结束
        assert drain_task.done() is False

        # 模拟工作器处理第 2 个任务：置为 failed（终态）
        db.execute("UPDATE transfers SET status='failed' WHERE share_code='code2'")
        await asyncio.sleep(2.5)

        # 队列已无未完成任务，排空完成，进入 organizing 独占整理状态
        assert drain_task.done() is True
        assert transfer_gate.is_auto_move_running() is True
        assert transfer_gate.get_gate_state() == "organizing"

        # 整理完毕，调用 resume_all 恢复
        transfer_gate.resume_all()
        assert transfer_gate.is_auto_move_running() is False
        assert transfer_gate.get_gate_state() == "idle"

    asyncio.run(scenario())


def test_api_endpoints():
    db.init()
    db.execute("DELETE FROM auto_move_rules")

    # 1. 保存规则（总库模式）
    res = manage.save_auto_move_rule({
        "source_dir": "/自动转存",
        "target_dir": "/电视剧",
        "mode": "library",
    })
    assert res["ok"] is True

    # 2. 读取规则列表
    rules_resp = manage.get_auto_move_rules()
    rules = rules_resp["rules"]
    assert len(rules) == 1
    assert rules[0]["source_dir"] == "/自动转存"
    assert rules[0]["target_dir"] == "/电视剧"
    assert rules[0]["mode"] == "library"

    rule_id = rules[0]["id"]

    # 3. 更新设置（系统停止时拒绝开启，系统运行时允许开启）
    db.set_setting("running", "false")
    try:
        manage.update_auto_move_settings({"auto_move_enabled": True})
        assert False, "Should fail when system is stopped"
    except manage.HTTPException as e:
        assert "系统处于停止状态" in str(e.detail)

    db.set_setting("running", "true")
    manage.update_auto_move_settings({
        "auto_move_enabled": True,
        "auto_move_interval_hours": 12,
        "auto_move_mode": "library",
        "auto_move_season_folder": True,
        "auto_move_fuzzy_t2s": True,
        "auto_move_notify": True,
    })
    settings = manage.get_auto_move_rules()["settings"]
    assert settings["auto_move_enabled"] is True
    assert settings["auto_move_interval_hours"] == 12
    assert settings["auto_move_mode"] == "library"
    assert settings["auto_move_season_folder"] is True
    assert settings["auto_move_fuzzy_t2s"] is True

    # 4. 删除规则
    del_res = manage.delete_auto_move_rule(rule_id)
    assert del_res["ok"] is True
    assert len(manage.get_auto_move_rules()["rules"]) == 0


def test_security_audit_cases():
    """安全与健壮性审计专项测试：
    1. 纯中文策略硬约束：纯英文/纯数字文件坚决不移动
    2. 特殊片名防误判：SP/特别篇与剧名（如 SP 要人警护官）精细区分
    3. 集数带 Special Edition 时优先常规季号而不是误认成特别篇
    4. 中英文标点全面软化（逗号、感叹号、问号、冒号自适应）
    5. 年份后缀剥离：仅剥离末尾年份，保留剧名前置年份（如 2001太空漫游）
    6. 目标目录查重与跨子目录同名文件防撞击
    7. 已经在目标目录的文件绝不重复移动（_parent_cid 拦截）
    """
    # 1. 纯英文/纯数字拒绝策略
    for non_cn in ["Game.of.Thrones.S01E01.mkv", "1.mp4", "S01E01.mp4", "avatar.2024.1080p.mkv", "Show.s01.mkv"]:
        assert not pan._CHINESE_CHAR_RE.search(non_cn), f"{non_cn} 不应包含汉字"

    # 2. 特殊剧名与季目录区分
    assert pan.parse_season_number("SP 要人警护官") is None
    assert pan.parse_season_number("SP") == 0
    assert pan.parse_season_number("SP01") == 0
    assert pan.parse_season_number("特别篇") == 0
    assert pan.parse_season_number("Specials") == 0
    assert pan.parse_season_number("[SP]") == 0
    assert pan.parse_season_number("Season 1") == 1
    assert pan.parse_season_number("第一季") == 1

    # 3. 带 Special Edition 的常规剧集识别
    s, ep, _ = episode.parse("折月亮.S01E05.Special.Edition.1080p.mkv")
    assert s == 1
    assert ep == 5

    # 4. 标点归一化
    assert pan.normalize_title("Doctor X, 白色黑手党时代!") == pan.normalize_title("Doctor X: 白色黑手党时代")
    assert pan.normalize_title("庆余年，第二季？") == pan.normalize_title("庆余年: 第二季")

    # 5. 年份剥离与保护
    assert pan.extract_target_folder_name("/影视/2001太空漫游") == "2001太空漫游"
    assert pan.extract_target_folder_name("/影视/折月亮 (2026)") == "折月亮"
    assert pan.extract_target_folder_name("/影视/繁花.2023") == "繁花"
    assert pan.extract_target_folder_name("/影视/繁花 [2024]") == "繁花"

    # 6. Mock 验证：跨目录同名防撞与 _parent_cid 拦截
    fake_client = MagicMock()
    fake_client.fs_files.side_effect = lambda req: (
        {"state": True, "data": []} if req.get("offset", 0) > 0 else (
            {"state": True, "data": [{"fid": "999", "fn": "折月亮.S01E01.mkv", "fc": "4"}]}
            if req.get("cid") == 200 else
            {"state": True, "data": [
                {"fid": "101", "fn": "折月亮.S01E01.mkv", "fc": "4"},  # 与目标目录重名，应跳过
                {"fid": "102", "fn": "折月亮.S01E02.mkv", "fc": "4"},  # 正常文件，应移动
                {"fid": "103", "fn": "折月亮.S01E02.mkv", "fc": "4"},  # 另一子文件夹中的同名文件，应拦截防撞
                {"fid": "104", "fn": "S01E03.mkv", "fc": "4"},         # 纯英文文件，安全策略严禁移动
            ]}
        )
    )
    fake_client.fs_move.return_value = {"state": True}

    with patch("backend.pan115.client._get_client", return_value=fake_client), \
         patch("backend.pan115.client.mkdirs", side_effect=lambda p: 100 if "源" in p else 200), \
         patch("backend.pan115.client.pace_factor", return_value=0.0), \
         patch("time.sleep", return_value=None):

        moved, samples = pan.move_matched_files_for_rule(
            source_dir="/源",
            target_dir="/目标/折月亮",
            scan_subdirs=False,
            mode="exact",
            season_folder=False,
        )

        assert moved == 1  # 只有 102 成功移动
        assert fake_client.fs_move.call_count == 1
        call_ids = fake_client.fs_move.call_args[0][0]
        assert call_ids == ["102"]

    print("test_security_audit_cases passed.")


def test_duplicate_suffix_deduplication():
    """测试 115 重复转存产生冲突后缀 (1)、(2)、（1）、（2）时的单文件去重移动保障"""
    # 1. 基础剥离测试（半角、全角、空格、中括号）
    assert pan.strip_duplicate_suffix("庆余年S01E01.mp4") == ("庆余年S01E01.mp4", 0)
    assert pan.strip_duplicate_suffix("庆余年S01E01(1).mp4") == ("庆余年S01E01.mp4", 1)
    assert pan.strip_duplicate_suffix("庆余年S01E01（1）.mp4") == ("庆余年S01E01.mp4", 1)
    assert pan.strip_duplicate_suffix("庆余年S01E01 (2).mp4") == ("庆余年S01E01.mp4", 2)
    assert pan.strip_duplicate_suffix("庆余年S01E01（3）.mkv") == ("庆余年S01E01.mkv", 3)
    assert pan.strip_duplicate_suffix("庆余年S01E01【1】.mp4") == ("庆余年S01E01.mp4", 1)
    assert pan.strip_duplicate_suffix("庆余年S01E01[2].mp4") == ("庆余年S01E01.mp4", 2)

    # 年份保护：4位年份（2019/2026）不得被误认为冲突编号
    assert pan.strip_duplicate_suffix("庆余年(2019).mp4") == ("庆余年(2019).mp4", 0)
    assert pan.strip_duplicate_suffix("庆余年(2019)(1).mp4") == ("庆余年(2019).mp4", 1)
    assert pan.strip_duplicate_suffix("庆余年(2019)（2）.mp4") == ("庆余年(2019).mp4", 2)

    # 2. Mock 场景 A：源目录存在原版和多份副本，优先只移动原版（1 份）
    fake_client_a = MagicMock()
    fake_client_a.fs_files.side_effect = lambda req: (
        {"state": True, "data": []} if req.get("offset", 0) > 0 else (
            {"state": True, "data": []} if req.get("cid") == 200 else  # 目标目录暂无文件
            {"state": True, "data": [
                {"fid": "201", "fn": "庆余年S01E01.mp4", "fc": "4", "s": 1000},
                {"fid": "202", "fn": "庆余年S01E01(1).mp4", "fc": "4", "s": 1000},
                {"fid": "203", "fn": "庆余年S01E01（2）.mp4", "fc": "4", "s": 1000},
            ]}
        )
    )
    fake_client_a.fs_move.return_value = {"state": True}

    with patch("backend.pan115.client._get_client", return_value=fake_client_a), \
         patch("backend.pan115.client.mkdirs", side_effect=lambda p: 100 if "源" in p else 200), \
         patch("backend.pan115.client.pace_factor", return_value=0.0), \
         patch("time.sleep", return_value=None):

        moved, samples = pan.move_matched_files_for_rule(
            source_dir="/源",
            target_dir="/目标/庆余年",
            scan_subdirs=False,
            mode="exact",
            season_folder=False,
        )
        assert moved == 1
        assert fake_client_a.fs_move.call_args[0][0] == ["201"]  # 只移动原版 201

    # 3. Mock 场景 B：源目录无原版，只有 (1) 和 (2)，取编号最小的 (1) 移动（1 份）
    fake_client_b = MagicMock()
    fake_client_b.fs_files.side_effect = lambda req: (
        {"state": True, "data": []} if req.get("offset", 0) > 0 else (
            {"state": True, "data": []} if req.get("cid") == 200 else
            {"state": True, "data": [
                {"fid": "301", "fn": "庆余年S01E01(1).mp4", "fc": "4", "s": 1000},
                {"fid": "302", "fn": "庆余年S01E01（2）.mp4", "fc": "4", "s": 1000},
            ]}
        )
    )
    fake_client_b.fs_move.return_value = {"state": True}

    with patch("backend.pan115.client._get_client", return_value=fake_client_b), \
         patch("backend.pan115.client.mkdirs", side_effect=lambda p: 100 if "源" in p else 200), \
         patch("backend.pan115.client.pace_factor", return_value=0.0), \
         patch("time.sleep", return_value=None):

        moved, samples = pan.move_matched_files_for_rule(
            source_dir="/源",
            target_dir="/目标/庆余年",
            scan_subdirs=False,
            mode="exact",
            season_folder=False,
        )
        assert moved == 1
        assert fake_client_b.fs_move.call_args[0][0] == ["301"]  # 取编号较小的 (1)

    # 4. Mock 场景 C：目标目录已存在原版文件，源目录中的全部副本 (1)、(2) 均不移动
    fake_client_c = MagicMock()
    fake_client_c.fs_files.side_effect = lambda req: (
        {"state": True, "data": []} if req.get("offset", 0) > 0 else (
            {"state": True, "data": [{"fid": "888", "fn": "庆余年S01E01.mp4", "fc": "4"}]}
            if req.get("cid") == 200 else  # 目标目录已存在
            {"state": True, "data": [
                {"fid": "401", "fn": "庆余年S01E01(1).mp4", "fc": "4", "s": 1000},
                {"fid": "402", "fn": "庆余年S01E01（2）.mp4", "fc": "4", "s": 1000},
            ]}
        )
    )
    fake_client_c.fs_move.return_value = {"state": True}

    with patch("backend.pan115.client._get_client", return_value=fake_client_c), \
         patch("backend.pan115.client.mkdirs", side_effect=lambda p: 100 if "源" in p else 200), \
         patch("backend.pan115.client.pace_factor", return_value=0.0), \
         patch("time.sleep", return_value=None):

        moved, samples = pan.move_matched_files_for_rule(
            source_dir="/源",
            target_dir="/目标/庆余年",
            scan_subdirs=False,
            mode="exact",
            season_folder=False,
        )
        assert moved == 0
        assert fake_client_c.fs_move.call_count == 0  # 绝不重复移动

    # 5. Mock 场景 D：目标目录已存在带全角 (1) 的历史文件，源目录的 (2) 也应被拦截
    fake_client_d = MagicMock()
    fake_client_d.fs_files.side_effect = lambda req: (
        {"state": True, "data": []} if req.get("offset", 0) > 0 else (
            {"state": True, "data": [{"fid": "777", "fn": "庆余年S01E01（1）.mp4", "fc": "4"}]}
            if req.get("cid") == 200 else
            {"state": True, "data": [
                {"fid": "501", "fn": "庆余年S01E01(2).mp4", "fc": "4", "s": 1000},
            ]}
        )
    )
    fake_client_d.fs_move.return_value = {"state": True}

    with patch("backend.pan115.client._get_client", return_value=fake_client_d), \
         patch("backend.pan115.client.mkdirs", side_effect=lambda p: 100 if "源" in p else 200), \
         patch("backend.pan115.client.pace_factor", return_value=0.0), \
         patch("time.sleep", return_value=None):

        moved, samples = pan.move_matched_files_for_rule(
            source_dir="/源",
            target_dir="/目标/庆余年",
            scan_subdirs=False,
            mode="exact",
            season_folder=False,
        )
        assert moved == 0
        assert fake_client_d.fs_move.call_count == 0

    print("test_duplicate_suffix_deduplication passed.")


if __name__ == "__main__":
    test_parse_season_number()
    test_filename_season_parsing()
    test_extract_target_folder_name()
    test_chinese_matching_logic()
    test_extract_chinese_subject()
    test_mixed_cn_en_matching()
    test_move_matched_files_exact_mock()
    test_move_matched_files_library_mode()
    test_move_matched_files_invalid_target()
    test_switch_enforcement()
    test_phase_gating_and_drain()
    test_api_endpoints()
    test_security_audit_cases()
    test_duplicate_suffix_deduplication()
    print("ALL TESTS IN test_auto_move.py PASSED SUCCESSFULLY!")
