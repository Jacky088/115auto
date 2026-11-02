"""API 集成测试（需要服务器已启动 + 可访问 Telegram）。

运行: .venv/Scripts/python tests/test_api_integration.py [base_url]
前提: 用全新的 data 目录启动 uvicorn backend.main:app --port 8768
"""
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8768"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  [{detail}]" if detail and not cond else ""))


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

print("\n=== C. 频道管理 ===")
r = c.post("/api/channels", headers=h, json={"name": "https://t.me/s/regeng115", "type": "polling"})
check("添加频道(自动提取名称)", r.json().get("name") == "regeng115", str(r.json()))
r = c.post("/api/channels", headers=h, json={"name": "regeng115", "type": "polling"})
check("重复添加被拒", r.status_code == 400)
chans = c.get("/api/channels", headers=h).json()
check("频道列表", len(chans) == 1 and chans[0]["name"] == "regeng115")

print("\n=== D. 规则引擎 ===")
t = c.post("/api/rules/test", headers=h, json={"text": "[ANi] 某某番 S01E05 1080p"}).json()
check("字幕组标签→动漫", t["category"] == "动漫")
t = c.post("/api/rules/test", headers=h, json={"text": "Inception.2010.2160p.BluRay"}).json()
check("年份+画质→电影", t["category"] == "电影")
t = c.post("/api/rules/test", headers=h, json={"text": "某剧 第05集 全12集"}).json()
check("集数→电视剧", t["category"] == "电视剧")
t = c.post("/api/rules/test", headers=h, json={"text": "奔跑吧 第3期"}).json()
check("期数→综艺", t["category"] == "综艺")
t = c.post("/api/rules/test", headers=h, json={"text": "某某电影预告"}).json()
check("关键词黑名单拦截", t["blocked"] and t["block_rule_value"] == "预告")

print("\n=== E. 真实 TG 轮询（等待抓取）===")
state = c.get("/api/system/state", headers=h).json()
running = bool(state.get("running"))
queued = []
if not running:
    print("  ⏭️  系统未开始运行（需先扫码登录 115 并在「开始运行」后启动），"
          "跳过真实频道抓取与工作器验证")
else:
    print("  等待轮询器抓取 regeng115 真实消息（最长 60s）...")
    deadline = time.time() + 60
    msgs = []
    while time.time() < deadline:
        msgs = c.get("/api/channels/1/messages", headers=h).json()
        if msgs:
            break
        time.sleep(5)
    check("抓到真实频道消息", len(msgs) > 0, f"{len(msgs)} 条")
    tr = c.get("/api/transfers", headers=h, params={"size": 100}).json()
    print(f"  频道消息 {len(msgs)} 条，提取分享链接 {tr['total']} 条")
    cats = {}
    for i in tr["items"]:
        cats[i["category"] or "(未分类)"] = cats.get(i["category"] or "(未分类)", 0) + 1
    print(f"  分类分布: {cats}")
    queued = [i for i in tr["items"] if i["status"] == "queued"]
    check("真实分享已入队", len(queued) > 0, str(len(queued)))
    check("入队任务均有目标目录", all(i["target_dir"] for i in queued))

print("\n=== F. 115 扫码 API 状态机 ===")
q = c.post("/api/account/115/qrcode", headers=h).json()
check("二维码生成(真实 115 接口)", "qr_svg" in q and "token" in q, str(list(q.keys())))
p = c.post("/api/account/115/qrcode/poll", headers=h, json={"token": q["token"], "app": q["app"]},
           timeout=45).json()
check("长轮询扫码状态返回", p.get("status") == 0, str(p))

print("\n=== G. 无 Cookie 时工作器行为（等待一轮处理）===")
if not running:
    print("  ⏭️  系统未运行，跳过工作器消费验证")
else:
    print("  等待 45s 观察工作器消费队列...")
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
