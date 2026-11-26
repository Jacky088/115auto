"""API 核心接口集成测试（鉴权、安全、向导、设置、频道、订阅、转存与黑名单）。

采用 FastAPI TestClient 纯内存执行，秒级运行，可稳定常驻 CI 与本地 pytest。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_api_"))

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend import auth, database as db
from backend.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _setup_db():
    db.init()
    db.set_setting("web_username", "admin")
    db.set_setting("web_password_hash", auth.hash_password("admin123"))
    with auth._fail_lock:
        auth._fail_records.clear()
    yield


def test_auth_and_security():
    # 1. 未登录访问受保护接口
    r = client.get("/api/dashboard/stats")
    assert r.status_code == 401

    # 2. 错误密码被拒
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrongpassword"})
    assert r.status_code == 401

    # 3. 正确密码登录
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    token = r.json().get("token")
    assert token

    headers = {"Authorization": f"Bearer {token}"}

    # 4. 携带 Bearer Token 访问正常
    r = client.get("/api/dashboard/stats", headers=headers)
    assert r.status_code == 200
    assert "quota" in r.json()

    # 5. 退出登录
    r = client.post("/api/auth/logout", headers=headers)
    assert r.status_code == 200

    # 6. 已吊销的 Token 再次访问被拒
    r = client.get("/api/dashboard/stats", headers=headers)
    assert r.status_code == 401


def test_wizard_and_settings():
    token = auth.make_token("admin")
    h = {"Authorization": f"Bearer {token}"}

    # 向导状态
    w = client.get("/api/wizard/status", headers=h).json()
    assert "completed" in w

    # 读取设置
    s = client.get("/api/settings", headers=h).json()
    assert "daily_limit" in s

    # 热更新设置（合法区间：poll_interval_min / max 在 15~3600）
    r = client.put("/api/settings", headers=h, json={"poll_interval_min": 20, "poll_interval_max": 40})
    assert r.status_code == 200
    s2 = client.get("/api/settings", headers=h).json()
    assert s2["poll_interval_min"] == 20
    assert s2["poll_interval_max"] == 40


@patch("backend.tg.validate.check_channel_access", new=AsyncMock(return_value={"ok": True, "title": "测试频道"}))
def test_channels_crud():
    token = auth.make_token("admin")
    h = {"Authorization": f"Bearer {token}"}

    # 添加频道
    r = client.post("/api/channels", headers=h, json={"name": "test_ch_integ", "type": "polling"})
    assert r.status_code == 200 and r.json().get("ok") is True

    # 列表可见
    chs = client.get("/api/channels", headers=h).json()
    matched = [x for x in chs if x["name"] == "test_ch_integ"]
    assert len(matched) == 1
    ch_id = matched[0]["id"]

    # 删除频道
    r = client.delete(f"/api/channels/{ch_id}", headers=h)
    assert r.status_code == 200
    chs2 = client.get("/api/channels", headers=h).json()
    assert not any(x["name"] == "test_ch_integ" for x in chs2)


def test_subscriptions_crud():
    token = auth.make_token("admin")
    h = {"Authorization": f"Bearer {token}"}

    # 添加订阅
    r = client.post("/api/subscriptions", headers=h, json={
        "keyword": "测试剧集",
        "note": "集成测试用",
    })
    assert r.status_code == 200 and r.json().get("ok") is True

    # 列表可见
    res = client.get("/api/subscriptions", headers=h).json()
    items = res.get("items", [])
    matched = [x for x in items if x["keyword"] == "测试剧集"]
    assert len(matched) == 1
    sub_id = matched[0]["id"]

    # 删除订阅
    r = client.delete(f"/api/subscriptions/{sub_id}", headers=h)
    assert r.status_code == 200
    res2 = client.get("/api/subscriptions", headers=h).json()
    items2 = res2.get("items", [])
    assert not any(x["keyword"] == "测试剧集" for x in items2)


def test_blacklist_keyword_crud():
    token = auth.make_token("admin")
    h = {"Authorization": f"Bearer {token}"}

    # 批量添加关键词
    client.post("/api/blacklist/keyword", headers=h, json={"value": "测试词AAA\n测试词BBB"})
    kws = [k["value"] for k in client.get("/api/blacklist/keyword", headers=h).json()]
    assert "测试词AAA" in kws and "测试词BBB" in kws

    # 删除关键词
    all_kws = client.get("/api/blacklist/keyword", headers=h).json()
    kid = [k for k in all_kws if k["value"] == "测试词AAA"][0]["id"]
    client.delete(f"/api/blacklist/keyword/{kid}", headers=h)

    kws2 = [k["value"] for k in client.get("/api/blacklist/keyword", headers=h).json()]
    assert "测试词AAA" not in kws2


def test_manual_transfer_and_retry():
    token = auth.make_token("admin")
    h = {"Authorization": f"Bearer {token}"}

    # 手动转存入队
    r = client.post("/api/transfers/manual", headers=h, json={
        "text": "https://115cdn.com/s/testinteg01?password=1234",
        "target_dir": "/最近接收",
    })
    assert r.status_code == 200
    assert r.json().get("result", {}).get("queued") == 1

    # 查看列表
    tr = client.get("/api/transfers", headers=h, params={"size": 50}).json()
    items = [i for i in tr["items"] if i.get("share_code") == "testinteg01"]
    assert len(items) == 1
    tid = items[0]["id"]

    # 重试转存（running/success 拒绝，但先把状态设为 failed 模拟可重试）
    db.execute("UPDATE transfers SET status='failed', error='模拟失败' WHERE id=?", (tid,))
    r = client.post(f"/api/transfers/{tid}/retry", headers=h)
    assert r.status_code == 200
    assert r.json().get("ok") is True

    # 日志可查
    logs = client.get("/api/logs", headers=h).json()
    assert logs["total"] >= 0


def test_security_hardening_regressions():
    # 1. change_pwd 传入非字符串 old=None 不得触发 500
    token = auth.make_token("admin")
    h = {"Authorization": f"Bearer {token}"}
    r_pwd = client.post("/api/auth/password", headers=h, json={"old": None, "new": "newpass123"})
    assert r_pwd.status_code == 400

    # 2. /auth/me 携带有效 Cookie + 无效 Bearer 时，不得下发 set-cookie 覆盖有效会话
    tc_cookie = TestClient(app, cookies={"sa_auth": token})
    r_me = tc_cookie.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer invalid.jwt.token"},
    )
    assert r_me.status_code == 200
    assert "set-cookie" not in {k.lower() for k in r_me.headers.keys()}

    # 3. 嵌套量词正则 ReDoS 模式必须被规则保存接口拦截
    r_rule = client.put(
        "/api/rules",
        headers=h,
        json={"rules": [{"pattern": "(?:(a|aa)+)", "category": "电影", "priority": 10, "enabled": 1}], "routes": []},
    )
    assert r_rule.status_code == 400


def test_upgrade_features_regressions():
    import asyncio
    import json
    import time
    from unittest.mock import MagicMock
    from backend.pan115 import client as pan
    from backend.worker.bot import scrape_flow
    from backend.worker import jobs

    token = auth.make_token("admin")
    h = {"Authorization": f"Bearer {token}"}

    # 1. 115 风控冷却 + 手动探测恢复
    pan._consec_risk = 0
    pan._risk_cooldown_until = 0.0
    pan.note_115_error("errno=405 rate limit")
    rs = pan.risk_status()
    assert rs["level"] >= 1 and rs["cooling"] is True and rs["cooldown_remaining_sec"] > 0

    with patch.object(pan, "check_login", return_value={"ok": True, "name": "ok"}):
        r_probe = client.post("/api/system/115/probe", headers=h)
        assert r_probe.status_code == 200
        assert r_probe.json()["ok"] is True
        assert r_probe.json()["risk"]["cooling"] is False

    # 2. 规则测试沙盒（频道名 + 订阅命中 + 链接提取）
    from backend.core import subscribe as submod
    db.execute("INSERT OR REPLACE INTO subscriptions(id, keyword, note, enabled, created_at) VALUES(501, '三体', '', 1, ?)", (time.time(),))
    submod.invalidate_cache()
    r_test = client.post("/api/rules/test", headers=h, json={
        "text": "三体 S01E01 1080p https://115cdn.com/s/sw3test01",
        "channel": "ch_demo",
    })
    assert r_test.status_code == 200
    td = r_test.json()
    assert "三体" in td.get("matched_subscriptions", [])
    assert td.get("extracted_links") == 1 and td.get("share_links") == 1

    # 3. 仪表盘统计包含 risk_115 与 30 天趋势
    r_dash = client.get("/api/dashboard/stats", headers=h)
    assert r_dash.status_code == 200
    dd = r_dash.json()
    assert "risk_115" in dd and isinstance(dd.get("trend"), list) and len(dd["trend"]) == 30

    # 4. Webhook 通用推送配置与测试
    db.set_setting("webhook_url", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=demo")
    db.set_setting("webhook_preset", "wecom")
    fake_resp = MagicMock(status_code=200)
    with patch.object(jobs.httpx, "post", return_value=fake_resp) as mock_post:
        r_wh = client.post("/api/settings/notify/test/webhook", headers=h)
        assert r_wh.status_code == 200
        assert mock_post.call_args.kwargs["json"]["msgtype"] == "text"

    # 5. 按剧名一键批量套用 TMDB + TG Bot 快捷审批回调
    now = time.time()
    cands = json.dumps([{"tmdb_id": 12345, "kind": "tv", "title": "演示剧集", "year": "2024", "poster_path": "", "score": 0.8}])
    parsed = json.dumps({"title": "演示剧集", "media_type": "tv"})
    db.execute(
        "INSERT INTO media_tasks(task_kind, status, source_cid, file_id, orig_path, orig_name, parsed_json, candidates_json, created_at, updated_at) "
        "VALUES('scrape', 'need_confirm', '1', 'f1', '/a/1.mkv', '1.mkv', ?, ?, ?, ?)",
        (parsed, cands, now, now),
    )
    db.execute(
        "INSERT INTO media_tasks(task_kind, status, source_cid, file_id, orig_path, orig_name, parsed_json, candidates_json, created_at, updated_at) "
        "VALUES('scrape', 'need_confirm', '1', 'f2', '/a/2.mkv', '2.mkv', ?, ?, ?, ?)",
        (parsed, cands, now, now),
    )
    tids = [r["id"] for r in db.query("SELECT id FROM media_tasks WHERE file_id IN ('f1','f2') ORDER BY id ASC")]
    db.set_setting("tmdb_api_key", "dummy_key")
    with patch("backend.core.tmdb_scraper.get_tmdb_detail", return_value={"id": 12345, "name": "演示剧集", "first_air_date": "2024-01-01", "poster_path": ""}):
        r_batch = client.post("/api/media/tasks/batch-apply-tmdb", headers=h, json={"parsed_title": "演示剧集", "tmdb_id": 12345, "kind": "tv"})
        assert r_batch.status_code == 200
        assert r_batch.json()["count"] == 2

    fake_tg = MagicMock()
    fake_tg.post = AsyncMock(return_value=MagicMock(status_code=200))
    asyncio.run(scrape_flow._handle_scrape_callback(fake_tg, "tok", "123", {"id": "cq1", "data": f"m:skip:{tids[0]}", "message": {"message_id": 10}}))
    row0 = db.query("SELECT status FROM media_tasks WHERE id=?", (tids[0],), one=True)
    assert row0["status"] == "ignored"


def test_subscription_cache_excludes_and_auto_disable_movie():
    from backend.core import subscribe as submod

    h = {"Authorization": f"Bearer {auth.make_token('admin')}"}
    submod.invalidate_cache()

    # 1. 创建带排除词与完结自动停用的电影订阅
    r = client.post("/api/subscriptions", headers=h, json={
        "keyword": "沙丘2",
        "kind": "movie",
        "aliases": ["Dune 2"],
        "exclude_keywords": ["枪版", "TS"],
        "auto_disable_movie": True,
        "note": "等4K",
    })
    assert r.status_code == 200 and r.json().get("ok") is True

    items = client.get("/api/subscriptions", headers=h).json()["items"]
    row = [i for i in items if i["keyword"] == "沙丘2"][0]
    sub_id = row["id"]
    assert row["exclude_keywords"] == ["枪版", "TS"]
    assert row["auto_disable_movie"] is True
    assert row["note"] == "等4K"

    # 2. 负向排除词过滤 + 别名命中（走 5s 缓存）
    assert submod.match_all("Dune 2 1080p 枪版") == []
    hits = submod.match_all("Dune 2 2160p REMUX")
    assert [s["id"] for s in hits] == [sub_id]

    # 3. 电影命中并记录进度后自动停用 + 立即失效缓存
    changed = submod.record_progress(hits[0], "Dune 2 2160p REMUX")
    assert changed is True
    assert db.query("SELECT enabled FROM subscriptions WHERE id=?", (sub_id,), one=True)["enabled"] == 0
    assert submod.match_all("Dune 2 2160p REMUX") == []

    # 4. PUT 更新排除词并关闭自动停用，缓存立即生效
    r_put = client.put(f"/api/subscriptions/{sub_id}", headers=h, json={
        "enabled": True,
        "exclude_keywords": ["CAM"],
        "auto_disable_movie": False,
    })
    assert r_put.status_code == 200
    hits2 = submod.match_all("Dune 2 1080p 枪版")
    assert [s["id"] for s in hits2] == [sub_id]
    submod.record_progress(hits2[0], "Dune 2 1080p 枪版")
    assert db.query("SELECT enabled FROM subscriptions WHERE id=?", (sub_id,), one=True)["enabled"] == 1

    # 清理
    assert client.delete(f"/api/subscriptions/{sub_id}", headers=h).status_code == 200


def test_tmdb_browse_categories_region_and_search():
    from unittest.mock import MagicMock
    from backend.api.manage import tmdb_browse

    h = {"Authorization": f"Bearer {auth.make_token('admin')}"}
    db.set_setting("tmdb_api_key", "dummy_tmdb_key")
    tmdb_browse._DISCOVER_CACHE.clear()
    tmdb_browse._TV_SEASON_CACHE.clear()

    def _fake_get(path, params, timeout=10):
        resp = MagicMock(status_code=200)
        if path == "/trending/all/week":
            resp.json.return_value = {
                "total_pages": 3,
                "results": [
                    {"id": 101, "media_type": "tv", "name": "国产热剧", "origin_country": ["CN"], "original_language": "zh", "first_air_date": "2026-10-01", "vote_average": 8.5, "popularity": 99.0},
                    {"id": 102, "media_type": "movie", "title": "US Movie", "original_language": "en", "release_date": "2026-10-02", "vote_average": 7.4, "popularity": 88.0},
                ],
            }
        elif path == "/discover/tv":
            resp.json.return_value = {
                "total_pages": 2,
                "results": [
                    {"id": 201, "name": "日漫新番", "origin_country": ["JP"], "first_air_date": "2026-10-05", "vote_average": 8.8, "popularity": 95.0},
                ],
            }
        elif path == "/discover/movie":
            resp.json.return_value = {"total_pages": 1, "results": []}
        elif path.startswith("/tv/"):
            resp.json.return_value = {
                "origin_country": ["CN"] if path.endswith("/101") else ["JP"],
                "seasons": [{"season_number": 2, "air_date": "2026-11-01", "episode_count": 12}],
            }
        elif path == "/find/tt1234567":
            resp.json.return_value = {
                "tv_results": [{"id": 301, "name": "IMDb 命中剧", "origin_country": ["US"], "first_air_date": "2026-09-01", "vote_average": 8.2, "popularity": 50.0}],
                "movie_results": [],
            }
        else:
            resp.json.return_value = {"total_pages": 1, "results": []}
        return resp

    with patch.object(tmdb_browse, "_tmdb_get", side_effect=_fake_get):
        r_tr = client.get("/api/tmdb/discover", headers=h, params={"kind": "trending", "region": "CN"})
        assert r_tr.status_code == 200
        tr_items = r_tr.json()["items"]
        assert [i["id"] for i in tr_items] == [101]
        assert tr_items[0]["episode_count"] == 12 and tr_items[0]["season"] == "S02"

        r_var = client.get("/api/tmdb/discover", headers=h, params={"kind": "variety"})
        assert r_var.status_code == 200 and len(r_var.json()["items"]) == 1

        r_imdb = client.get("/api/tmdb/search", headers=h, params={"query": "tt1234567"})
        assert r_imdb.status_code == 200
        assert r_imdb.json()["items"][0]["id"] == 301
        assert r_imdb.json()["total_pages"] == 1


def test_rules_note_match_source_and_hit_remove_rule():
    h = {"Authorization": f"Bearer {auth.make_token('admin')}"}

    # 1. 规则备注读写 + 沙盒 match_source（tag vs rule）
    r_save = client.put("/api/rules", headers=h, json={
        "rules": [{"pattern": "自定义测试剧", "category": "电视剧", "priority": 10, "enabled": 1, "note": "测试说明"}],
    })
    assert r_save.status_code == 200
    saved = client.get("/api/rules", headers=h).json()["rules"]
    assert saved[0]["note"] == "测试说明"

    t_rule = client.post("/api/rules/test", headers=h, json={"text": "自定义测试剧 S01E01"}).json()
    assert t_rule["match_source"] == "rule" and t_rule["match_priority"] == 10

    t_tag = client.post("/api/rules/test", headers=h, json={"text": "#电影 自定义测试剧"}).json()
    assert t_tag["category"] == "电影" and t_tag["match_source"] == "tag"

    # 恢复默认规则集
    assert client.post("/api/rules/reset", headers=h).status_code == 200

    # 2. 命中放行并移除对应黑名单词条
    client.post("/api/blacklist/keyword", headers=h, json={"value": "临时误杀词"})
    db.execute(
        "INSERT INTO blacklist_hits(channel, message_id, text, share_code, rule_type, rule_value, stage, created_at) "
        "VALUES('ch_x', 9901, '临时误杀词 测试内容', '', 'keyword', '临时误杀词', 'message', ?)",
        (1700000000.0,),
    )
    hid = db.query("SELECT id FROM blacklist_hits WHERE message_id=9901", one=True)["id"]
    r_rel = client.post(f"/api/blacklist/hits/{hid}/release", headers=h, params={"remove_rule": "true"})
    assert r_rel.status_code == 200
    assert db.query("SELECT 1 FROM blacklist WHERE type='keyword' AND value='临时误杀词'", one=True) is None


def test_transfers_filters_clear_stale_and_auto_move_allow_english():
    h = {"Authorization": f"Bearer {auth.make_token('admin')}"}

    # 1. 转存列表按 kind 筛选 + status_counts + 清理无效记录
    db.execute(
        "INSERT INTO transfers(kind, share_code, title, category, status, created_at) "
        "VALUES('share', 'stale01', '跳过片', '电影', 'skipped', 1.0),"
        "      ('offline', 'stale02', '磁力片', '电影', 'duplicate', 2.0),"
        "      ('share', 'alive01', '正常片', '电影', 'success', 3.0)"
    )
    r_list = client.get("/api/transfers", headers=h, params={"kind": "offline"})
    assert r_list.status_code == 200
    data = r_list.json()
    assert any(i["share_code"] == "stale02" for i in data["items"])
    assert data["status_counts"]["all"] >= 3

    r_clr = client.post("/api/transfers/clear-stale", headers=h, json={"statuses": ["skipped", "duplicate"]})
    assert r_clr.status_code == 200 and r_clr.json()["deleted"] >= 2
    assert db.query("SELECT 1 FROM transfers WHERE share_code='alive01'", one=True) is not None

    # 2. 整理规则保存 allow_english 字段
    r_am = client.post("/api/auto-move/rules", headers=h, json={
        "source_dir": "/待整理", "target_dir": "/美剧", "mode": "library",
        "media_type": "tv", "allow_english": True,
    })
    assert r_am.status_code == 200
    rules = client.get("/api/auto-move/rules", headers=h).json()["rules"]
    assert any(r["source_dir"] == "/待整理" and r["allow_english"] == 1 for r in rules)

    # 3. webhook_url 格式校验
    assert client.put("/api/settings", headers=h, json={"webhook_url": "ftp://bad"}).status_code == 400






