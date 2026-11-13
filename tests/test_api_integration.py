"""API 集成测试（需要服务器已启动 + 可访问 Telegram）。

运行: .venv/Scripts/python tests/test_api_integration.py [base_url]
前提: 用全新的 data 目录启动 uvicorn backend.main:app --port 8768
"""
import sys
import time

import httpx

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  [{detail}]" if detail and not cond else ""))


def run_integration():
    BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8768"
    c = httpx.Client(base_url=BASE, timeout=30)
    h = {}

    print("\n=== A. 认证与安全 ===")
    r = c.get("/api/dashboard/stats")
    check("未登录访问被拒 401", r.status_code == 401, str(r.status_code))
    r = c.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    check("错误密码被拒 401", r.status_code == 401)
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    check("正确密码登录", r.status_code == 200 and "token" in r.json())
    h = {"Authorization": "Bearer " + r.json()["token"]}

    print("\n=== B. 向导与设置 ===")
    w = c.get("/api/wizard/status", headers=h).json()
    check("向导初始未完成", w["completed"] is False)
    s = c.get("/api/settings", headers=h).json()
    check("默认设置读取", s["daily_limit"] == 50 and s["tg_mode"] == "polling")
    c.put("/api/settings", headers=h, json={"poll_interval_min": 10, "poll_interval_max": 15})
    s2 = c.get("/api/settings", headers=h).json()
    check("设置热更新", s2["poll_interval_min"] == 10 and s2["poll_interval_max"] == 15)

    print("\n=== C. 目录选择 ===")
    dirs = c.get("/api/dirs/115", headers=h).json()
    check("115 目录接口连通 (无论是否已绑 cookie)", "items" in dirs)

    print("\n=== D. 频道管理 ===")
    r = c.post("/api/channels", headers=h, json={"name": "test_ch_integration", "type": "public"})
    check("添加频道", r.status_code == 200 and r.json().get("ok") is True)
    chs = c.get("/api/channels", headers=h).json()
    check("频道列表可见", any(x["name"] == "test_ch_integration" for x in chs))
    ch_id = [x for x in chs if x["name"] == "test_ch_integration"][0]["id"]
    c.delete(f"/api/channels/{ch_id}", headers=h)
    chs2 = c.get("/api/channels", headers=h).json()
    check("删除频道", not any(x["name"] == "test_ch_integration" for x in chs2))

    print("\n=== E. 订阅规则 ===")
    r = c.post("/api/subscriptions", headers=h, json={
        "name": "集成测试规则",
        "keywords": "4K, 1080p",
        "save_dir_id": "0",
        "save_dir_name": "默认根目录",
    })
    check("添加订阅", r.status_code == 200 and r.json().get("ok") is True)
    subs = c.get("/api/subscriptions", headers=h).json()
    check("订阅列表可见", any(x["name"] == "集成测试规则" for x in subs))
    sub_id = [x for x in subs if x["name"] == "集成测试规则"][0]["id"]
    c.delete(f"/api/subscriptions/{sub_id}", headers=h)
    subs2 = c.get("/api/subscriptions", headers=h).json()
    check("删除订阅", not any(x["name"] == "集成测试规则" for x in subs2))

    print("\n=== F. 注入消息与转存队列 ===")
    c.post("/api/channels", headers=h, json={"name": "integ_test_source", "type": "public"})
    src_ch = [x for x in c.get("/api/channels", headers=h).json() if x["name"] == "integ_test_source"][0]
    raw_texts = [
        "🔥 新片发布: 电影A.2024.1080p https://115.com/s/sw3fake01?password=abcd",
        "另一部好片: 剧集B.S01E01.4K 访问码: 1234 链接: https://anotepad.com/note/read/fakelink",
        "无关纯文本无链接",
    ]
    for i, t in enumerate(raw_texts, start=1):
        c.post(f"/api/channels/{src_ch['id']}/test_inject", headers=h, json={"message_id": 9000 + i, "text": t})
    tr = c.get("/api/transfers", headers=h, params={"size": 100}).json()
    queued = [i for i in tr["items"] if i["channel_name"] == "integ_test_source"]
    check("消息解析入队 (至少 1 条 115 链接)", len(queued) >= 1, f"入队 {len(queued)} 条")

    print("\n=== G. 队列处理与重试（等待工作器扫盘）===")
    print("  ⏳ 等待 45 秒观察后台 worker 执行结果...")
    time.sleep(45)
    tr2 = c.get("/api/transfers", headers=h, params={"size": 100}).json()
    running_or_tried = [i for i in tr2["items"] if i["attempts"] > 0]
    check("工作器已尝试处理队列", len(running_or_tried) > 0,
          f"{len(running_or_tried)} 个任务已尝试")
    if running_or_tried:
        sample = running_or_tried[0]
        check("失败任务标记 115 未登录", "未登录" in sample["error"] or "cookie" in sample["error"].lower(),
              sample["error"][:80])
        check("已安排退避重试", sample["next_retry_at"] is not None)

    print("\n=== H. 转存记录操作 ===")
    if queued:
        tid = queued[0]["id"]
        r = c.post(f"/api/transfers/{tid}/retry", headers=h)
        check("手动重试入队", r.json().get("ok") is True)
    r = c.get("/api/logs", headers=h).json()
    check("历史日志可查", r["total"] > 0)

    print("\n=== I. 黑名单 CRUD ===")
    c.post("/api/blacklist/keyword", headers=h, json={"value": "测试关键词ABC\n另一个DEF"})
    kws = [k["value"] for k in c.get("/api/blacklist/keyword", headers=h).json()]
    check("批量添加关键词", "测试关键词ABC" in kws and "另一个DEF" in kws)
    kid = [k for k in c.get("/api/blacklist/keyword", headers=h).json() if k["value"] == "测试关键词ABC"][0]["id"]
    c.delete(f"/api/blacklist/keyword/{kid}", headers=h)
    kws = [k["value"] for k in c.get("/api/blacklist/keyword", headers=h).json()]
    check("删除关键词", "测试关键词ABC" not in kws)

    print(f"\n{'='*40}\n结果: {len(PASS)} 通过, {len(FAIL)} 失败")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    run_integration()
