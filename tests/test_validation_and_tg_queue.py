import os
from fastapi.testclient import TestClient
import tempfile
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_"))
from backend.main import app
from backend import auth, database as db
from backend.worker import jobs

def test_numerical_validation():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {auth.make_token('admin')}"}

    # 1. 测试 daily_limit 边界校验
    r = client.put("/api/settings", json={"daily_limit": 0}, headers=headers)
    assert r.status_code == 400
    assert "每日转存上限" in r.json()["detail"]

    r = client.put("/api/settings", json={"daily_limit": 51}, headers=headers)
    assert r.status_code == 400
    assert "每日转存上限" in r.json()["detail"]

    r = client.put("/api/settings", json={"daily_limit": "not_int"}, headers=headers)
    assert r.status_code == 400

    # 2. 测试 auto_backup_days 边界校验
    r = client.put("/api/settings", json={"auto_backup_days": 0}, headers=headers)
    assert r.status_code == 400
    assert "自动备份间隔" in r.json()["detail"]

    r = client.put("/api/settings", json={"auto_backup_days": 366}, headers=headers)
    assert r.status_code == 400
    assert "自动备份间隔" in r.json()["detail"]

    # 3. 测试 auto_backup_time 格式校验
    r = client.put("/api/settings", json={"auto_backup_time": "invalid_time"}, headers=headers)
    assert r.status_code == 400
    assert "HH:MM" in r.json()["detail"]

    r = client.put("/api/settings", json={"auto_backup_time": "04:30"}, headers=headers)
    assert r.status_code == 200
    assert db.get_setting("auto_backup_time") == "04:30"

    # 4. 测试 schedule_jitter_min
    r = client.put("/api/settings", json={"schedule_jitter_min": -1}, headers=headers)
    assert r.status_code == 400
    assert "开工随机扰动" in r.json()["detail"]

    r = client.put("/api/settings", json={"schedule_jitter_min": 31}, headers=headers)
    assert r.status_code == 400
    assert "开工随机扰动" in r.json()["detail"]

    # 5. 测试 batch_rest_count
    r = client.put("/api/settings", json={"batch_rest_count": 1}, headers=headers)
    assert r.status_code == 400
    assert "连存小憩笔数" in r.json()["detail"]

    r = client.put("/api/settings", json={"batch_rest_count": 21}, headers=headers)
    assert r.status_code == 400
    assert "连存小憩笔数" in r.json()["detail"]

    # 6. 测试 batch_rest_min_min 与 batch_rest_max_min 区间大小校验
    r = client.put("/api/settings", json={"batch_rest_min_min": 10, "batch_rest_max_min": 5}, headers=headers)
    assert r.status_code == 400
    assert "小憩时间区间有误" in r.json()["detail"]

    # 合法区间
    r = client.put("/api/settings", json={"batch_rest_min_min": 3, "batch_rest_max_min": 8}, headers=headers)
    assert r.status_code == 200

    # 7. 测试 min_size_gb
    r = client.put("/api/settings", json={"min_size_gb": -0.5}, headers=headers)
    assert r.status_code == 400
    assert "分享体积下限" in r.json()["detail"]

    # 8. 测试 rules priority
    r = client.put("/api/rules", json={"rules": [{"pattern": "test", "category": "电影", "priority": 0}]}, headers=headers)
    assert r.status_code == 400
    assert "优先级必须是大于等于 1 的整数" in r.json()["detail"]
    print("test_numerical_validation passed.")


def test_tg_notify_queue():
    # 测试加入队列
    db.set_setting("notify_token", "123456:ABC-DEF")
    db.set_setting("notify_chat_id", "987654321")

    # 清空可能残留的队列
    while not jobs._tg_notify_queue.empty():
        try:
            jobs._tg_notify_queue.get_nowait()
        except Exception:
            break

    # 加入一条文本消息
    assert jobs.enqueue_tg_message("测试消息 1") is True
    assert jobs._tg_notify_queue.qsize() == 1
    item = jobs._tg_notify_queue.get_nowait()
    assert item["type"] == "text"
    assert item["text"] == "测试消息 1"
    assert item["token"] == "123456:ABC-DEF"
    assert item["chat_id"] == "987654321"

    # 加入一份文档
    assert jobs.enqueue_tg_document("backup.json", b"{}", "application/json", "备份") is True
    assert jobs._tg_notify_queue.qsize() == 1
    item2 = jobs._tg_notify_queue.get_nowait()
    assert item2["type"] == "document"
    assert item2["fname"] == "backup.json"
    assert item2["caption"] == "备份"

    print("test_tg_notify_queue passed.")


def test_bot_link_header_routing():
    """指令头只区分队列：转存/云下载；空格不作有效信息；目录读「分类 → 目录映射」。"""
    from backend.worker import bot_commands

    db.execute("DELETE FROM transfers")
    db.execute("DELETE FROM messages")
    db.set_setting("unclassified_action", "/待整理")
    m1 = "magnet:?xt=urn:btih:" + "a" * 40
    m2 = "magnet:?xt=urn:btih:" + "b" * 40

    # 1. 没有指令头 → 回复格式说明，不落库
    reply = bot_commands._bot_enqueue_links(m1, 993001)
    assert "指令头" in reply
    assert db.query("SELECT count(*) c FROM transfers", one=True)["c"] == 0

    # 2. 「云下载」+ 空格 + 链接 → 云下载队列，目录取「未分类动作」
    reply = bot_commands._bot_enqueue_links(f"云下载  {m1}", 993002)
    assert "已入队 1 个云下载任务" in reply
    row = db.query("SELECT kind, target_dir, status FROM transfers"
                   " ORDER BY id DESC LIMIT 1", one=True)
    assert (row["kind"], row["target_dir"], row["status"]) == ("offline", "/待整理", "queued")

    # 3. 指令头紧贴链接（无空格）也能正确读取
    reply = bot_commands._bot_enqueue_links("云下载" + m2, 993003)
    assert "已入队 1 个云下载任务" in reply
    assert db.query("SELECT count(*) c FROM transfers", one=True)["c"] == 2

    # 4. 指令头与链接类型不符 → 提示换头，不入队
    reply = bot_commands._bot_enqueue_links(f"转存\n{m2}", 993004)
    assert "请用「云下载」开头" in reply
    assert db.query("SELECT count(*) c FROM transfers", one=True)["c"] == 2

    # 5. 「转存」+ 分享链接 → 分享转存队列
    share = "https://115.com/s/sw123abcd?password=x1y2"
    reply = bot_commands._bot_enqueue_links(f"转存 {share}", 993005)
    assert "已入队 1 个转存任务" in reply
    row = db.query("SELECT kind, target_dir, status FROM transfers"
                   " ORDER BY id DESC LIMIT 1", one=True)
    assert (row["kind"], row["target_dir"], row["status"]) == ("share", "/待整理", "queued")

    # 6. 未分类动作为跳过 → 明确回复去哪里设置，不静默吞掉
    db.set_setting("unclassified_action", "skip")
    m3 = "magnet:?xt=urn:btih:" + "c" * 40
    reply = bot_commands._bot_enqueue_links(f"云下载 {m3}", 993006)
    assert "未入队" in reply and "未分类" in reply and "转存整理" in reply

    # 7. 指令头后写分类名 → 按该分类的云下载目录存放
    db.set_setting("unclassified_action", "/待整理")
    db.execute("INSERT OR REPLACE INTO routes(category,target_dir,offline_dir)"
               " VALUES('测试分类','/测试转存','/测试云下载')")
    m4 = "magnet:?xt=urn:btih:" + "d" * 40
    reply = bot_commands._bot_enqueue_links(f"云下载 测试分类 {m4}", 993007)
    assert "已入队 1 个云下载任务" in reply
    row = db.query("SELECT category, target_dir FROM transfers"
                   " ORDER BY id DESC LIMIT 1", one=True)
    assert (row["category"], row["target_dir"]) == ("测试分类", "/测试云下载")

    # 8. 分类名不存在 → 拒绝入队并列出已有分类；紧贴链接也能截出分类名
    before = db.query("SELECT count(*) c FROM transfers", one=True)["c"]
    reply = bot_commands._bot_enqueue_links("云下载 不存在的分类 " + m4, 993008)
    assert "不存在" in reply and "本次未入队" in reply and "测试分类" in reply
    reply = bot_commands._bot_enqueue_links("云下载动画" + m4, 993009)
    assert "不存在" in reply and "动画" in reply
    assert db.query("SELECT count(*) c FROM transfers", one=True)["c"] == before

    # 9. 未分类目录非法值被后端拒绝（前端弹窗校验的后端兜底）
    from backend.main import app
    client = TestClient(app)
    h = {"Authorization": f"Bearer {auth.make_token('admin')}"}
    for bad in ("未整理", "/", "/未 整理", "/a/../b"):
        r = client.put("/api/rules", json={"unclassified_action": bad}, headers=h)
        assert r.status_code == 400, bad
    r = client.put("/api/rules", json={"unclassified_action": "/未整理/"}, headers=h)
    assert r.status_code == 200 and db.get_setting("unclassified_action") == "/未整理"
    db.set_setting("unclassified_action", "skip")

    print("test_bot_link_header_routing passed.")


def test_blacklist_defaults_and_match():
    """默认关键词黑名单：覆盖垃圾内容、大小写不敏感、空白条目不会误拦。"""
    from backend.core import filters

    client = TestClient(app)
    h = {"Authorization": f"Bearer {auth.make_token('admin')}"}
    assert client.post("/api/blacklist/restore-defaults", headers=h).status_code == 200
    # 幂等：再次补齐不应重复插入（依赖 UNIQUE(type,value)）
    assert client.post("/api/blacklist/restore-defaults", headers=h).json()["added"] == 0

    filters.invalidate()
    for junk in ("某某剧 第3集 抢先版 1080p", "新片 预告 1080p",
                 "资源 加群 客服", "movie.ts版", "某片 影视解说"):
        blocked, kind, _ = filters.check_message(junk)
        assert blocked and kind == "keyword", junk
    for normal in ("三体 第5集 2160p WEB-DL", "繁花 全30集 4K", "奥本海默 (2023) 2160p"):
        blocked, _, val = filters.check_message(normal)
        assert not blocked, (normal, val)

    # 空白关键词被跳过，不会拦掉所有消息
    db.execute("INSERT OR IGNORE INTO blacklist(type,value,note,created_at)"
               " VALUES('keyword','   ','',0)")
    filters.invalidate()
    blocked, _, _ = filters.check_message("三体 第5集")
    assert not blocked
    print("test_blacklist_defaults_and_match passed.")


def test_big_transfer_and_space_alert():
    """≥1TB 转存醒目提示；空间 ≥90% 推送告急，同档 24h 去重、跨档再提醒。"""
    from backend.worker import jobs
    from backend.pan115 import client as pan

    sent = []
    orig_notify = jobs.notify
    orig_space = pan.space_info
    jobs.notify = lambda text, **kw: sent.append(text)
    try:
        db.set_setting("notify_transfer_success", True)
        jobs.notify_transfer("某剧 全8集", "电视剧", "chan", "/媒体库", size=2 << 30)
        assert "🟥" not in sent[-1]
        jobs.notify_transfer("某合集", "电影", "chan", "/媒体库", size=1 << 40)
        assert "🟥" in sent[-1] and "≥ 1 TB" in sent[-1]

        db.set_setting("space_alert_level", 0)
        db.set_setting("space_alert_at", 0)
        pan.space_info = lambda force=False: {"total": 100 << 40, "use": 91 << 40,
                                              "free": 9 << 40, "ratio": 91.0}
        n0 = len(sent)
        jobs.check_space_alert("测试转存后")
        assert len(sent) == n0 + 1 and "空间告急" in sent[-1]
        jobs.check_space_alert("测试转存后")
        assert len(sent) == n0 + 1, "同档 24 小时内不应重复推送"

        pan.space_info = lambda force=False: {"total": 100 << 40, "use": 96 << 40,
                                              "free": 4 << 40, "ratio": 96.0}
        jobs.check_space_alert("测试转存后")
        assert len(sent) == n0 + 2, "跨到 95% 档应再提醒一次"

        pan.space_info = lambda force=False: {"total": 100 << 40, "use": 50 << 40,
                                              "free": 50 << 40, "ratio": 50.0}
        jobs.check_space_alert("测试转存后")
        assert int(db.get_setting("space_alert_level") or 0) == 0
        assert len(sent) == n0 + 2
    finally:
        jobs.notify = orig_notify
        pan.space_info = orig_space
        db.set_setting("space_alert_level", 0)
        db.set_setting("space_alert_at", 0)
    print("test_big_transfer_and_space_alert passed.")


def test_space_guard_and_preflight():
    """空间门闸：≥90% 挂起、2% 回差恢复；预检不足跳过；空间错误不重试只跳过。"""
    from backend.pan115 import client as pan
    from backend.worker import queue_worker as qw

    db.set_setting("space_guard_enabled", True)
    db.set_setting("space_blocked", False)
    qw._SPACE_PROBE.update(ts=0.0, blocked=False, reason="", ratio=0.0, free=0)
    orig_space = pan.space_info
    try:
        pan.space_info = lambda force=False: {"total": 100 << 40, "use": 91 << 40,
                                              "free": 9 << 40, "ratio": 91.0}
        blocked, reason = qw._space_gate()
        assert blocked and "已用 91.0%" in reason

        # 回差：已挂起时 89% 仍保持挂起，回落到 87% 才解除
        db.set_setting("space_blocked", True)
        pan.space_info = lambda force=False: {"total": 100 << 40, "use": 89 << 40,
                                              "free": 11 << 40, "ratio": 89.0}
        qw._SPACE_PROBE["ts"] = 0
        assert qw._space_gate()[0], "89% 处于 2% 回差内，应保持挂起"
        pan.space_info = lambda force=False: {"total": 100 << 40, "use": 87 << 40,
                                              "free": 13 << 40, "ratio": 87.0}
        qw._SPACE_PROBE["ts"] = 0
        assert not qw._space_gate()[0], "回落到 88% 以下应自动恢复"

        # 单任务预检：剩余不足 → 跳过；关闭总开关后完全放行
        pan.space_info = lambda force=False: {"total": 100 << 40, "use": 95 << 40,
                                              "free": 5 << 40, "ratio": 95.0}
        assert qw._space_preflight(10 << 40) is False
        db.set_setting("space_guard_enabled", False)
        assert qw._space_preflight(10 << 40) is True
        assert qw._space_gate() == (False, "")
        assert db.get_setting("space_blocked") in (False, None, 0)

        # 错误串识别 + 空间不足直接标 skipped 且不重试，同时挂起队列
        assert qw._is_space_error("115 空间不足，转存失败")
        assert not qw._is_space_error("分享链接已失效")
        db.set_setting("space_guard_enabled", True)
        db.execute("DELETE FROM transfers")
        db.execute("INSERT INTO transfers(kind,share_code,status,attempts,created_at)"
                   " VALUES('share','abc','running',0,0)")
        rid = db.query("SELECT last_insert_rowid() AS id", one=True)["id"]
        qw._fail({"id": rid, "attempts": 0, "share_code": "abc",
                  "title": "某剧", "kind": "share"}, "115 空间不足")
        row = db.query("SELECT status,attempts,error FROM transfers WHERE id=?",
                       (rid,), one=True)
        assert row["status"] == "skipped" and row["attempts"] == 1
        assert row["error"].startswith("SPACE_FULL")
        assert bool(db.get_setting("space_blocked", False)) is True
    finally:
        pan.space_info = orig_space
        db.set_setting("space_guard_enabled", True)
        db.set_setting("space_blocked", False)
        db.set_setting("space_blocked_reason", "")
        db.execute("DELETE FROM transfers")
    print("test_space_guard_and_preflight passed.")


if __name__ == "__main__":
    test_numerical_validation()
    test_tg_notify_queue()
    test_bot_link_header_routing()
    test_blacklist_defaults_and_match()
    test_big_transfer_and_space_alert()
    test_space_guard_and_preflight()
    print("ALL TESTS IN test_validation_and_tg_queue.py PASSED SUCCESSFULLY!")
