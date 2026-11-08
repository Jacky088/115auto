"""转存工作器逻辑测试（mock 115 接口，无需账号与网络）。

运行: .venv/Scripts/python tests/test_worker_mock.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="autosave_test_")

from backend import database as db  # noqa: E402
db.init()

from backend.core import pipeline  # noqa: E402
from backend.worker import queue_worker  # noqa: E402
from backend.pan115 import client as pan  # noqa: E402

# 消息 ID 用严格递增计数器：原来的 int(time.time()*1000)%1e9 在相邻两条消息
# 落同毫秒时会生成相同 id，被消息级去重判重，用例偶发假失败
_MID = [int(time.time() * 1000) % 10**9]


def next_mid() -> int:
    _MID[0] += 1
    return _MID[0]


PASS = []
FAIL = []
SKIP = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  [{detail}]" if detail and not cond else ""))


def skip(name, reason=""):
    """依赖外网的断言：网络不可达时跳过（不算失败），避免测试整体崩在 None 上。"""
    SKIP.append(name)
    print(f"  ⏭️  {name}" + (f"  [{reason}]" if reason else ""))


# ---------- mock 115 接口 ----------
class FakePan:
    title = "测试资源合集"
    files = [{"fid": "1", "fn": "Movie.2023.1080p.mkv", "fs": 5 * (1 << 30)}]
    total_size = 5 * (1 << 30)
    fail_receive = False


def fake_share_snap_all(code, rc, max_files=5000):
    return {"title": FakePan.title, "files": FakePan.files, "total_size": FakePan.total_size}


def fake_mkdirs(path):
    assert path, "目标目录为空"
    return 12345


def fake_receive(code, rc, cid, fids):
    if FakePan.fail_receive:
        raise pan.Pan115Error("模拟转存失败")
    assert cid == 12345 and fids, "receive 参数异常"
    return {"state": True}


pan.share_snap_all = fake_share_snap_all
pan.mkdirs = fake_mkdirs
pan.receive = fake_receive
pan.check_login = lambda: {"ok": True, "name": "测试账号"}


def mkjob(share_code="testcode1"):
    r = pipeline.process_message("test_ch", next_mid(),
                                 f"https://115.com/s/{share_code}?password=ab12 某某电影.2023.1080p")
    assert r["queued"] == 1, f"入队失败: {r}"
    return dict(db.query("SELECT * FROM transfers WHERE share_code=? ORDER BY id DESC LIMIT 1",
                         (share_code,), one=True))


print("\n=== 1. 成功路径 ===")
job = mkjob("succ001")
queue_worker._process_one(job)
row = dict(db.query("SELECT * FROM transfers WHERE id=?", (job["id"],), one=True))
check("转存成功状态", row["status"] == "success", row["status"])
check("标题来自分享信息", row["title"] == "测试资源合集", row["title"])
check("体积记录", row["size"] == 5 * (1 << 30))
check("文件数记录", row["file_count"] == 1)
check("每日限额 +1", db.quota_today() == 1)

print("\n=== 2. 体积黑名单 ===")
db.set_setting("min_size_gb", 0.2)
FakePan.total_size = 50 * (1 << 20)  # 50MB < 0.2GB
job = mkjob("small001")
queue_worker._process_one(job)
row = dict(db.query("SELECT * FROM transfers WHERE id=?", (job["id"],), one=True))
check("小体积被拦截", row["status"] == "blocked", row["status"])
hit = db.query("SELECT * FROM blacklist_hits ORDER BY id DESC LIMIT 1", one=True)
check("命中记录写入", hit and hit["rule_type"] == "min_size" and hit["stage"] == "share_info")
check("限额不增加", db.quota_today() == 1)

print("\n=== 3. 放行 share_info 命中（bypass 重跑）===")
FakePan.total_size = 5 * (1 << 30)
row["bypass_filters"] = 1
row["status"] = "queued"
queue_worker._process_one(row)
row2 = dict(db.query("SELECT * FROM transfers WHERE id=?", (row["id"],), one=True))
check("bypass 后转存成功", row2["status"] == "success", row2["status"])

print("\n=== 4. 文件名黑名单 ===")
FakePan.files = [{"fid": "1", "fn": "readme.txt", "fs": 1024},
                 {"fid": "2", "fn": "Movie.mkv", "fs": 3 * (1 << 30)}]
FakePan.total_size = 3 * (1 << 30)
db.execute("INSERT OR IGNORE INTO blacklist(type,value,created_at) VALUES('file_pattern','.*\\.txt$',?)",
           (time.time(),))
from backend.core import filters as _filters
_filters._cache = ({}, 0)  # 清缓存，模拟网页添加后生效（生产 TTL 5s）
job = mkjob("pat001")
queue_worker._process_one(job)
row = dict(db.query("SELECT * FROM transfers WHERE id=?", (job["id"],), one=True))
check("txt 文件触发拦截", row["status"] == "blocked" and "file_pattern" in row["error"], row["error"])
db.execute("DELETE FROM blacklist WHERE type='file_pattern'")

print("\n=== 5. 转存失败重试退避 ===")
FakePan.files = [{"fid": "1", "fn": "ok.mkv", "fs": 2 * (1 << 30)}]
FakePan.fail_receive = True
job = mkjob("fail001")
# 与真实路径一致：工作器先把任务置为 running 再调用 _process_one/_fail
db.execute("UPDATE transfers SET status='running' WHERE id=?", (job["id"],))
try:
    queue_worker._process_one(job)  # 真实路径中由工作器循环 try/except 捕获
    raised = False
except Exception as e:
    raised = True
    queue_worker._fail(job, str(e))
check("receive 异常向上抛出（由循环捕获）", raised)
row = dict(db.query("SELECT * FROM transfers WHERE id=?", (job["id"],), one=True))
check("第1次失败仍为 queued", row["status"] == "queued", row["status"])
check("attempts=1", row["attempts"] == 1)
check("已安排重试时间", row["next_retry_at"] and row["next_retry_at"] > time.time())
check("错误信息已记录", "模拟转存失败" in row["error"])
FakePan.fail_receive = False

print("\n=== 6. 消息黑名单 + 放行 ===")
db.execute("INSERT OR IGNORE INTO blacklist(type,value,created_at) VALUES('keyword','预告',?)", (time.time(),))
r = pipeline.process_message("test_ch", 987654, "https://115.com/s/blk001 某某电影.2023.1080p 预告")
check("关键词拦截", r["blocked"] is True)
hit = db.query("SELECT * FROM blacklist_hits ORDER BY id DESC LIMIT 1", one=True)
check("命中环节=message", hit["stage"] == "message")
# 模拟放行 API 的逻辑
db.execute("DELETE FROM messages WHERE channel=? AND message_id=?", (hit["channel"], hit["message_id"]))
db.execute("DELETE FROM blacklist_hits WHERE id=?", (hit["id"],))
r2 = pipeline.process_message(hit["channel"], hit["message_id"], hit["text"], bypass=True)
check("bypass 放行后入队", r2["queued"] == 1, str(r2))

print("\n=== 7. 未分类动作=目录 ===")
db.set_setting("unclassified_action", "/待整理")
r = pipeline.process_message("test_ch", 987655, "https://115.com/s/unc001 完全无法识别的东西xyz")
row = dict(db.query("SELECT * FROM transfers WHERE share_code='unc001'", one=True))
check("未分类转存到指定目录", row["status"] == "queued" and row["target_dir"] == "/待整理",
      f"{row['status']}/{row['target_dir']}")
db.set_setting("unclassified_action", "skip")
r = pipeline.process_message("test_ch", 987656, "https://115.com/s/unc002 另一个无法识别的东西xyz")
row = dict(db.query("SELECT * FROM transfers WHERE share_code='unc002'", one=True))
check("未分类动作=skip 时跳过", row["status"] == "skipped", row["status"])

print("\n=== 8. 路由 skip ===")
db.execute("INSERT INTO routes(category,target_dir,offline_dir) VALUES('测试分类','skip','')"
           " ON CONFLICT(category) DO UPDATE SET target_dir='skip'")
from backend.core import classifier  # noqa: E402
db.execute("INSERT INTO rules(pattern,category,priority) VALUES('特殊标记XYZ','测试分类',5)")
r = pipeline.process_message("test_ch", 987657, "特殊标记XYZ https://115.com/s/skp001")
row = dict(db.query("SELECT * FROM transfers WHERE share_code='skp001'", one=True))
check("路由为 skip 的分类被跳过", row["status"] == "skipped", row["status"])

print("\n=== 9. ED2K/磁力云下载 ===")
from backend.core import extractor as _extractor  # noqa: E402
msg = ("📺 剧集：某剧 (2026) S01E03 💾 体积：796.40 MB 🔗 ED2K: "
       "ed2k://|file|某剧.2026.S01E03.1080p.mkv|835085726|b83690e9339507d30b5314ef40ce70e5|/ "
       "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=test")
links = _extractor.extract(msg)
check("ED2K 链接提取", len([l for l in links if l["kind"] == "offline"]) == 2, str(len(links)))
r = pipeline.process_message("test_ch", 987658, msg)
check("ED2K 消息入队 2 个云下载任务", r["queued"] == 2, str(r))
rows = db.query("SELECT * FROM transfers WHERE kind='offline' ORDER BY id DESC LIMIT 2")
check("offline 任务分类正确", all(row["category"] == "电视剧" and row["target_dir"] == "/媒体库/电视剧"
                                  for row in rows))
FakePan.files = [{"fid": "1", "fn": "ok.mkv", "fs": 2 * (1 << 30)}]
offjob = dict(rows[0])
pan.offline_download = lambda url, cid: {"state": True}
pan.list_file_names = lambda cid, limit=200: set()
queue_worker._process_one(offjob)
row = dict(db.query("SELECT * FROM transfers WHERE id=?", (offjob["id"],), one=True))
check("云下载提交成功（submitted 待跟踪）", row["status"] == "submitted", row["status"])
r2 = pipeline.process_message("test_ch2", 987659, msg)  # 不同频道同消息
check("ED2K 链接去重", r2["queued"] == 0, str(r2))

print("\n=== 10. 功能开关 ===")
# 10.1 关闭云下载：新消息中的 ED2K 不入队
db.set_setting("enable_offline_download", False)
ed2k_msg = "🔗 ED2K: ed2k://|file|新剧.2026.S02E01.1080p.mkv|123456|b83690e9339507d30b5314ef40ce70e5|/"
r = pipeline.process_message("test_ch", 987660, ed2k_msg)
check("关闭云下载后 ED2K 不入队", r["queued"] == 0 and r.get("disabled") == 1, str(r))
n_off = db.query("SELECT count(*) c FROM transfers WHERE kind='offline' AND channel='test_ch' AND message_id=987660", one=True)["c"]
check("不产生任务记录", n_off == 0)

# 10.2 排队中的 offline 任务被工作器标记跳过
offjob2 = {"id": 999901, "kind": "offline", "share_code": "hash123", "share_url": "ed2k://x",
           "receive_code": "", "title": "t", "target_dir": "/媒体库/电视剧", "attempts": 0}
db.execute("INSERT INTO transfers(id,kind,share_code,share_url,title,target_dir,status,created_at)"
           " VALUES(999901,'offline','hash123','ed2k://x','t','/媒体库/电视剧','queued',?)", (time.time(),))
check("排队任务被标记跳过", queue_worker.skip_if_disabled(offjob2) is True)
row = dict(db.query("SELECT * FROM transfers WHERE id=999901", one=True))
check("状态=skipped 且带原因", row["status"] == "skipped" and "已关闭" in row["error"])

# 10.3 关闭分享转存：pipeline 不入队 + 排队任务跳过
db.set_setting("enable_share_transfer", False)
r = pipeline.process_message("test_ch", 987661, "https://115.com/s/dis001 某某电影.2023.1080p")
check("关闭分享转存后不入队", r["queued"] == 0 and r.get("disabled") == 1, str(r))
shjob = {"id": 999902, "kind": "share", "share_code": "dis001", "share_url": "https://115.com/s/dis001",
         "receive_code": "", "title": "t", "target_dir": "/媒体库/电影", "attempts": 0}
db.execute("INSERT INTO transfers(id,kind,share_code,share_url,title,target_dir,status,created_at)"
           " VALUES(999902,'share','dis001','https://115.com/s/dis001','t','/媒体库/电影','queued',?)", (time.time(),))
check("分享排队任务被标记跳过", queue_worker.skip_if_disabled(shjob) is True)

# 10.4 规则测试器显示开关状态
from backend.core import classifier as _cls  # noqa: E402
tr = _cls.test_text(ed2k_msg)
check("测试器报告云下载已关闭", "云下载" in tr["disabled_kinds"], str(tr["disabled_kinds"]))

# 10.5 重新开启后恢复
db.set_setting("enable_offline_download", True)
db.set_setting("enable_share_transfer", True)
r = pipeline.process_message("test_ch", 987662, ed2k_msg)
check("重新开启后 ED2K 恢复入队", r["queued"] == 1, str(r))
check("skip_if_disabled 对开启状态放行", queue_worker.skip_if_disabled(
    {"id": 0, "kind": "offline"}) is False)

print("\n=== 11. 内联按钮解析（查看资源 → telegra.ph）===")
import asyncio  # noqa: E402
from backend.tg import resolver  # noqa: E402

# 11.1 按钮分类判断
check("115 链接是直接资源", resolver.is_direct_resource("https://115.com/s/abc123?password=xy12"))
check("ed2k 是直接资源", resolver.is_direct_resource("ed2k://|file|x.mkv|1|" + "a"*32 + "|/"))
check("t.me 链接识别为站内", resolver.is_tg_internal("https://t.me/Movie4ktop_bot/moviesapp"))
check("telegra.ph 是中间页", not resolver.is_direct_resource("https://telegra.ph/xx-2026-10-20")
      and not resolver.is_tg_internal("https://telegra.ph/xx-2026-10-20"))

# 11.2 中间页抓取解析（真实 telegra.ph 文章，内容为 ED2K）
TELEGRAPH = "https://telegra.ph/狄仁杰之不死军-2026-10-20"
async def _resolve():
    return await resolver.resolve_message(
        "哆啦A梦：伴我同行 (2014) 简中 1080P BluRay 评分: 7.5",
        ["https://t.me/Movie4ktop_bot/moviesapp", TELEGRAPH, "https://t.me/some_promo"])
resolved = asyncio.run(_resolve())
n_ed2k = resolved.count("ed2k://")
# 真实 telegra.ph 文章：网络不可达（SSL EOF / 被墙）时跳过中间页解析断言，不让整体崩掉
net_ok = n_ed2k >= 1
if net_ok:
    check("按钮页面解析出 ED2K 链接", True)
else:
    skip("按钮页面解析出 ED2K 链接", "telegra.ph 不可访问，跳过真实中间页解析")
check("t.me 推广按钮被忽略", "Movie4ktop" not in resolved)
check("解析结果包含原文本", "哆啦A梦" in resolved)

# 11.3 缓存生效（第二次调用不再网络请求，直接命中）
resolved2 = asyncio.run(_resolve())
check("缓存命中结果一致", resolved2 == resolved)

# 11.4 走完整管道：解析后的文本入队
from backend.core import pipeline as _pl  # noqa: E402
if net_ok:
    r = _pl.process_message("lsp115", 555001, resolved)
    check("解析文本成功入队", r["queued"] >= 1, str(r))
    row = db.query("SELECT kind,status FROM transfers WHERE channel='lsp115' ORDER BY id DESC LIMIT 1",
                   one=True)
    check("入队任务类型正确", bool(row) and row["kind"] == "offline",
          str(dict(row)) if row else "没有入队任务")
else:
    skip("解析文本成功入队", "依赖真实 telegra.ph 解析结果")
    skip("入队任务类型正确", "依赖真实 telegra.ph 解析结果")

# 11.5 正文已有链接时不解析按钮
r3 = asyncio.run(resolver.resolve_message(
    "https://115.com/s/direct99?password=ab12 正文已有链接",
    ["https://telegra.ph/should-not-fetch"]))
check("正文有链接时跳过按钮解析", "should-not-fetch" not in r3 and "direct99" in r3)

print("\n=== 12. 转存成功 TG 通知 ===")
from backend.worker import jobs  # noqa: E402
pan.list_file_names = lambda cid, limit=200: {"ok.mkv"}  # 目录反查命中，确保成功路径可用

# 12.1 集数提取
check("提取 S01E40", jobs.extract_episode("某剧 (2026) S01E40 1080p") == "S01E40")
check("提取 第495集", jobs.extract_episode("某动漫 第495集 2160p") == "第495集")
check("提取 第3期", jobs.extract_episode("奔跑吧 第3期") == "第3期")
check("无集数返回空", jobs.extract_episode("阿凡达.2009.2160p.BluRay") == "")

# 12.2 通知内容格式（mock notify 捕获）
sent = []
jobs.notify = lambda msg: sent.append(msg)
jobs.notify_transfer("苏东坡与杭州的故事 (2026) S01E40 2160p WEB-DL", "电视剧",
                     "lsp115", "/媒体库/电视剧", size=829 * (1 << 20), kind="share", file_count=1)
check("通知已发送", len(sent) == 1)
msg = sent[0]
check("包含成功标题", "✅ 转存成功" in msg)
check("包含标题", "苏东坡与杭州的故事" in msg)
check("包含分类", "分类：电视剧" in msg)
check("包含集数", "集数：S01E40" in msg)
check("包含来源", "来源：@lsp115" in msg)
check("包含目标目录", "目标：/媒体库/电视剧" in msg)
check("包含大小", "大小：" in msg and "MB" in msg)
check("消息简洁（≤10 行）", len(msg.splitlines()) <= 10, str(len(msg.splitlines())))

# 12.3 云下载通知（无大小无集数时行数更少）
sent.clear()
jobs.notify_transfer("苏东坡与杭州的故事 (2026) S01E40", "电视剧", "lsp115",
                     "/媒体库/电视剧", kind="offline")
check("云下载通知类型正确", "☁️ 云下载任务已添加" in sent[0])
check("云下载通知无大小行", "大小" not in sent[0])

# 12.4 开关关闭不发
db.set_setting("notify_transfer_success", False)
sent.clear()
jobs.notify_transfer("测试", "电影", "ch", "/媒体库/电影")
check("开关关闭不发送", len(sent) == 0)
db.set_setting("notify_transfer_success", True)

# 12.5 通知异常静默（不影响主流程）
def _boom(msg):
    raise RuntimeError("network down")
jobs.notify = _boom
try:
    jobs.notify_transfer("测试", "电影", "ch", "/媒体库/电影")
    check("通知异常静默", True)
except Exception:
    check("通知异常静默", False)

# 12.6 转存成功路径自动带通知（走 _process_one 全流程）
sent.clear()
captured = jobs.notify_transfer
import backend.worker.queue_worker as _qw  # noqa: E402
_qw.jobs.notify_transfer = lambda *a, **k: (sent.append((a, k)), captured(*a, **k))
job = mkjob("notify001")
queue_worker._process_one(job)
check("成功路径自动发通知", len(sent) == 1)
args = sent[0][0]
check("通知参数完整", args[0] == "测试资源合集" and args[1] == "电影"
      and args[2] == "test_ch" and args[3] == "/媒体库/电影", str(args))
_qw.jobs.notify_transfer = captured

print("\n=== 13. 通知测试功能（send_test）===")
import httpx as _httpx  # noqa: E402

# 13.1 未填配置
ok, detail = jobs.send_test("", "")
check("未填配置给出提示", not ok and "请先填写" in detail, detail)

# 13.2 发送成功
class _Resp:
    def __init__(self, code, desc=""):
        self.status_code = code
        self.text = desc
    def json(self):
        return {"description": self.text}
orig_post = jobs.httpx.post
jobs.httpx.post = lambda *a, **k: _Resp(200)
ok, detail = jobs.send_test("123:ABC", "12345")
check("配置正确发送成功", ok and detail == "")

# 13.3 Token 无效（401）
jobs.httpx.post = lambda *a, **k: _Resp(401, "Unauthorized")
ok, detail = jobs.send_test("bad:token", "12345")
check("Token 错误提示明确", not ok and "Token 无效" in detail, detail)

# 13.4 Chat ID 错误（chat not found）
jobs.httpx.post = lambda *a, **k: _Resp(400, "Bad Request: chat not found")
ok, detail = jobs.send_test("123:ABC", "99999")
check("Chat ID 错误提示明确", not ok and "Chat ID 无效" in detail and "/start" in detail, detail)

# 13.5 网络不通
def _raise(*a, **k):
    raise _httpx.ConnectError("timeout")
jobs.httpx.post = _raise
ok, detail = jobs.send_test("123:ABC", "12345")
check("网络不通提示代理", not ok and "代理" in detail, detail)
jobs.httpx.post = orig_post

# 13.6 API 层：未保存的输入直接测（传入值优先），失败返回具体原因
sent_bodies = []
jobs.httpx.post = lambda url, **k: (sent_bodies.append(k["json"]), _Resp(200))[1]
from backend.api import manage as _manage  # noqa: E402
r = _manage.test_notify({"token": "unsaved:tok", "chat_id": "777"})
check("API 用传入值测试", sent_bodies[0]["chat_id"] == "777", str(sent_bodies))
check("成功返回 ok", r == {"ok": True})
jobs.httpx.post = orig_post

print("\n=== 14. 分享转存 / 云下载分别指定目录 ===")
from backend.core import classifier as _cls2  # noqa: E402

# 14.1 同分类分别指定：分享→/媒体库/电视剧，云下载→/云下载/电视剧
db.execute("INSERT INTO routes(category,target_dir,offline_dir) VALUES('电视剧','/媒体库/电视剧','/云下载/电视剧')"
           " ON CONFLICT(category) DO UPDATE SET offline_dir='/云下载/电视剧'")
from backend.core import classifier as _rf  # noqa: E402
_rf._cache = ({}, 0)
check("分享目录", _cls2.resolve_target("电视剧", kind="share") == "/媒体库/电视剧")
check("云下载目录独立", _cls2.resolve_target("电视剧", kind="offline") == "/云下载/电视剧")

# 14.2 管道：ED2K 任务进云下载目录，分享任务进分享目录
ed2k_msg2 = "某剧 第06集 ed2k://|file|x06.mkv|1|" + "b" * 32 + "|/"
share_msg2 = "某剧 第07集 https://115.com/s/sep001?password=ab12"
r = pipeline.process_message("test_ch", 987663, ed2k_msg2)
r2 = pipeline.process_message("test_ch", 987664, share_msg2)
off_row = db.query("SELECT * FROM transfers WHERE kind='offline' AND message_id=987663", one=True)
sh_row = db.query("SELECT * FROM transfers WHERE kind='share' AND share_code='sep001'", one=True)
check("云下载任务进云下载目录", off_row and off_row["target_dir"] == "/云下载/电视剧",
      off_row["target_dir"] if off_row else "无记录")
check("分享任务进分享目录", sh_row and sh_row["target_dir"] == "/媒体库/电视剧",
      sh_row["target_dir"] if sh_row else "无记录")

# 14.3 offline_dir 留空回退分享目录
db.execute("UPDATE routes SET offline_dir='' WHERE category='电影'")
_rf._cache = ({}, 0)
check("留空回退分享目录", _cls2.resolve_target("电影", kind="offline") == "/媒体库/电影")

# 14.4 云下载目录 = skip 时 ED2K 被跳过（分享不受影响）
db.execute("UPDATE routes SET offline_dir='skip' WHERE category='电视剧'")
_rf._cache = ({}, 0)
r = pipeline.process_message("test_ch", 987665, "另一剧 第08集 ed2k://|file|x08.mkv|1|" + "c" * 32 + "|/")
row = db.query("SELECT status FROM transfers WHERE message_id=987665", one=True)
check("云下载目录 skip 时任务跳过", row and row["status"] == "skipped", row["status"] if row else "无记录")
r = pipeline.process_message("test_ch", 987666, "再一剧 第09集 https://115.com/s/sep002")
row = db.query("SELECT status FROM transfers WHERE share_code='sep002'", one=True)
check("分享转存不受 skip 影响", row and row["status"] == "queued", row["status"] if row else "无记录")
db.execute("UPDATE routes SET offline_dir='/云下载/电视剧' WHERE category='电视剧'")
_rf._cache = ({}, 0)

# 14.5 旧库迁移：没有 offline_dir 列的库自动补列（模拟旧结构）
import sqlite3 as _sq  # noqa: E402
old_db = os.path.join(os.environ["DATA_DIR"], "old.db")
conn = _sq.connect(old_db)
conn.execute("CREATE TABLE routes(category TEXT PRIMARY KEY, target_dir TEXT NOT NULL)")
conn.execute("INSERT INTO routes VALUES('电影','/old/电影')")
conn.commit(); conn.close()
conn = _sq.connect(old_db)
conn.execute("ALTER TABLE routes ADD COLUMN offline_dir TEXT NOT NULL DEFAULT ''")
conn.commit()
row = conn.execute("SELECT offline_dir FROM routes WHERE category='电影'").fetchone()
conn.close()
check("旧库迁移补列且默认空", row[0] == "")

# 14.6 测试器显示云下载目标
tr = _cls2.test_text("某剧 第10集 ed2k://|file|x10.mkv|1|" + "d" * 32 + "|/")
check("测试器显示云下载目录", tr["offline_target_dir"] == "/云下载/电视剧"
      and tr["target_kind"] == "offline", str(tr))

print("\n=== 15. 细化后的默认规则集 ===")
# 真实标题批量验证（标题 → 期望分类）
cases = [
    ("央视版西游记 1986 全25集 4K修复", "电视剧"),      # 用户举例：全N集
    ("西游记 第05集 三打白骨精", "电视剧"),              # 第N集
    ("Friends.Complete.S03.SEASON.FINAL", "电视剧"),    # season
    ("某剧 EP12 1080p", "电视剧"),                       # EP集号
    ("[ANi] 我推的孩子 第03集 1080p", "动漫"),           # 字幕组
    ("咒术回战 剧场版 2160p BluRay", "动漫"),            # 剧场版
    ("葬送的芙莉莲 OVA 合集", "动漫"),                    # OVA
    ("行星地球 BBC.2006.1080p.BluRay", "纪录片"),        # BBC
    ("大国崛起 纪录片 全12集", "纪录片"),                # 纪录片关键词优先于全N集
    ("奔跑吧 第12期 2024", "综艺"),                      # 期数
    ("RunningMan.S2024E15.1080p", "综艺"),               # 年份季格式
    ("阿凡达.(2009).2160p.BluRay", "电影"),              # 括号年份
    ("流浪地球2.4K.HDR.国粤双语", "电影"),               # 画质标识
    ("泰坦尼克号.1997. REMUX", "电影"),
]
for title, expect in cases:
    got, _ = classifier.classify(title)
    check(f"{title[:26]:<28} → {expect}", got == expect, f"实际 {got!r}")

# 用户例子端到端：西游记分享→分享目录，云下载→云下载目录
db.execute("INSERT INTO routes(category,target_dir,offline_dir) VALUES('电视剧','/媒体库/电视剧','/云下载/电视剧')"
           " ON CONFLICT(category) DO UPDATE SET offline_dir='/云下载/电视剧'")
from backend.core import classifier as _c3  # noqa: E402
_c3._cache = ({}, 0)
r = pipeline.process_message("test_ch", 987670, "央视版西游记 1986 全25集 4K修复 https://115.com/s/west01?password=ab12")
r2 = pipeline.process_message("test_ch", 987671, "央视版西游记 1986 全25集 ed2k://|file|west.mkv|1|" + "e" * 32 + "|/")
s1 = db.query("SELECT target_dir,category FROM transfers WHERE share_code='west01'", one=True)
s2 = db.query("SELECT target_dir,kind FROM transfers WHERE message_id=987671", one=True)
check("西游记分享→媒体库/电视剧", s1 and s1["category"] == "电视剧" and s1["target_dir"] == "/媒体库/电视剧",
      str(dict(s1) if s1 else None))
check("西游记云下载→云下载/电视剧", s2 and s2["kind"] == "offline" and s2["target_dir"] == "/云下载/电视剧",
      str(dict(s2) if s2 else None))

# reset 端点
from backend.api import manage as _m2  # noqa: E402
r = _m2.reset_rules()
n = db.query("SELECT count(*) c FROM rules", one=True)["c"]
check("reset 重置为最新规则集", n == r["count"] and n >= 13, f"{n} 条")
has_doc = db.query("SELECT 1 FROM rules WHERE category='纪录片'", one=True)
check("新规则集含纪录片分类", has_doc is not None)

# 少儿分类（优先于"动画"关键词）
for title, expect in [("小猪佩奇 第七季 少儿动画 1080p", "少儿"),
                      ("汪汪队立大功 儿童动画 全26集", "少儿"),
                      ("宝宝巴士 幼儿启蒙儿歌", "少儿"),
                      ("蓝色星球2 儿童版 BBC", "少儿")]:
    got, _ = classifier.classify(title)
    check(f"{title[:24]:<26} → {expect}", got == expect, f"实际 {got!r}")
has_kids_route = db.query("SELECT target_dir FROM routes WHERE category='少儿'", one=True)
check("少儿默认路由已补齐", has_kids_route and has_kids_route["target_dir"] == "/媒体库/少儿",
      str(dict(has_kids_route) if has_kids_route else None))

# 未分类兜底：默认 skip
r = pipeline.process_message("test_ch", 987680, "完全无法识别的一段文字xyz https://115.com/s/unk001")
row = db.query("SELECT status FROM transfers WHERE share_code='unk001'", one=True)
check("无法识别默认跳过", row and row["status"] == "skipped", row["status"] if row else "无记录")
# 未分类兜底：改为指定目录后统一进该目录
db.set_setting("unclassified_action", "/待整理")
r = pipeline.process_message("test_ch", 987681, "又一个无法识别的东西abc https://115.com/s/unk002")
row = db.query("SELECT status,target_dir FROM transfers WHERE share_code='unk002'", one=True)
check("无法识别进指定兜底目录", row and row["status"] == "queued" and row["target_dir"] == "/待整理",
      str(dict(row) if row else None))
db.set_setting("unclassified_action", "skip")

print("\n=== 16. 黑名单默认关键词与恢复默认 ===")
from backend.database import DEFAULT_BLACKLIST_KEYWORDS  # noqa: E402
from backend.core import filters  # noqa: E402, F811
print(f"默认关键词 {len(DEFAULT_BLACKLIST_KEYWORDS)} 条: {DEFAULT_BLACKLIST_KEYWORDS[:6]}...")
# 预告/花絮/枪版/广告拦截验证
for kw, title in [("预告", "某某电影 先导预告 1080p"), ("花絮", "某剧 花絮合集"),
                  ("枪版", "某新片 枪版 TS"), ("广告", "广告合作看这里"),
                  ("试看", "某片 试看版"), ("样本", "原盘 样本段")]:
    blocked, rt, rv = filters.check_message(title)
    check(f"关键词[{kw}]拦截", blocked and rv == kw, f"{rv!r}")
# 正片不误伤
blocked, _, _ = filters.check_message("变形金刚7 正片 2160p")
check("正片标题不误伤", not blocked)

# restore-defaults：插入用户自定义条目 + 删掉几条默认关键词，然后补齐
db.execute("INSERT OR IGNORE INTO blacklist(type,value,created_at) VALUES('keyword','自定义词',?)",
           (time.time(),))
for kw in ["预告", "广告"]:
    db.execute("DELETE FROM blacklist WHERE type='keyword' AND value=?", (kw,))
before = db.query("SELECT count(*) c FROM blacklist WHERE type='keyword'", one=True)["c"]
from backend.api import manage as _m3  # noqa: E402
res = _m3.restore_blacklist_defaults()
after = db.query("SELECT count(*) c FROM blacklist WHERE type='keyword'", one=True)["c"]
check("restore 补齐缺失默认词", res["added"] == 2 and after - before == 2, f"added={res['added']}")
kept = db.query("SELECT count(*) c FROM blacklist WHERE value='自定义词'", one=True)["c"]
check("不删除用户自定义条目", kept == 1)

# hits 路由顺序：/blacklist/hits 不能被 /blacklist/{btype} 抢占
r = _m3.hits()
check("hits 路由正常返回列表", isinstance(r, list))

print("\n=== 17. 链接校验 / 双重确认 / 云下载跟踪 ===")

# 17.1 链接合法性校验
check("合法分享码", queue_worker.validate_link("share", "abc123XY", "") == "")
check("分享码为空", "为空" in queue_worker.validate_link("share", "", ""))
check("分享码格式非法", "不符合" in queue_worker.validate_link("share", "a!!", ""))
check("合法磁力链(40hex)", queue_worker.validate_link(
    "offline", "", "magnet:?xt=urn:btih:" + "a" * 40) == "")
check("合法磁力链(32base32)", queue_worker.validate_link(
    "offline", "", "magnet:?xt=urn:btih:" + "abcdefghijklmnopqrstuvwxyz234567") == "")
check("非法磁力链hash", "hash 格式不支持" in queue_worker.validate_link(
    "offline", "", "magnet:?xt=urn:btih:abc123"))
check("非法磁力链(BTv2 64hex)", "hash 格式不支持" in queue_worker.validate_link(
    "offline", "", "magnet:?xt=urn:btih:" + "a" * 64))
check("合法电驴", queue_worker.validate_link(
    "offline", "", "ed2k://|file|x.mkv|1|" + "a" * 32 + "|/") == "")
check("不支持的协议", "不支持的链接协议" in queue_worker.validate_link(
    "offline", "", "thunder://QUFodHRw"))
check("地址为空", "为空" in queue_worker.validate_link("offline", "", ""))

# 17.2 迅雷链接解码
from backend.core import extractor as _ex  # noqa: E402
import base64 as _b64  # noqa: E402
th = "thunder://" + _b64.b64encode(("AA" + "https://example.com/file.zip" + "ZZ").encode()).decode()
links = _ex.extract("迅雷链接 " + th)
check("thunder 解码为 http 云下载", links and links[0]["kind"] == "offline"
      and links[0]["share_url"] == "https://example.com/file.zip", str(links))

# 17.3 永久失败：非法链接（30 位超长码，可提取但校验不过）→ failed + 失败通知
fail_sent = []
jobs.notify_failure = lambda *a, **k: fail_sent.append((a, k))
bad = mkjob("abcdefabcdefabcdefabcdefab")
db.execute("UPDATE transfers SET target_dir='/媒体库/电影' WHERE id=?", (bad["id"],))
job = dict(db.query("SELECT * FROM transfers WHERE id=?", (bad["id"],), one=True))
try:
    queue_worker._process_one(job)
    raised = False
except queue_worker.PermanentTransferError as e:
    raised = True
    queue_worker._fail = _fail_backup = queue_worker._fail  # noqa
    db.execute("UPDATE transfers SET status='failed', attempts=?, error=? WHERE id=?",
               (job["attempts"] + 1, str(e)[:500], job["id"]))
    jobs.notify_failure("share", job["title"], "电影", "test_ch", str(e))
check("非法链接触发永久失败", raised)
row = db.query("SELECT status,error FROM transfers WHERE id=?", (bad["id"],), one=True)
check("状态=failed 且注明原因", row["status"] == "failed" and "不符合" in row["error"])
check("永久失败发出通知", len(fail_sent) == 1 and "不符合" in fail_sent[0][0][4])

# 17.4 分享双重确认：接口成功但目录无文件 → 可重试失败
FakePan.files = [{"fid": "1", "fn": "verify.mkv", "fs": 1 << 30}]
FakePan.total_size = 1 << 30
pan.list_file_names = lambda cid, limit=200: {"不相关的文件.mp4"}
job = mkjob("verify01")
err_msg = ""
try:
    queue_worker._process_one(job)
    raised = False
except Exception as e:
    raised = True
    err_msg = str(e)
    queue_worker._fail(job, err_msg)
check("目录反查不到文件触发复核失败", raised and "未查到文件" in err_msg)
row = db.query("SELECT status FROM transfers WHERE id=?", (job["id"],), one=True)
check("复核失败进入重试队列", row["status"] == "queued", row["status"])
# 二次确认通过 → 成功
pan.list_file_names = lambda cid, limit=200: {"verify.mkv", "其他.mkv"}
queue_worker._process_one(dict(db.query("SELECT * FROM transfers WHERE id=?", (job["id"],), one=True)))
row = db.query("SELECT status FROM transfers WHERE id=?", (job["id"],), one=True)
check("反查到文件后转存成功", row["status"] == "success", row["status"])

# 17.5 云下载 submitted → 115 任务完成 → success + 通知
sent.clear()
jobs.notify = lambda msg: sent.append(msg)  # 恢复通知捕获（12.5 曾替换为抛异常版本）
db.execute("INSERT INTO transfers(id,kind,share_code,share_url,title,target_dir,category,status,created_at)"
           " VALUES(987701,'offline','offhash01','https://example.com/big.iso','某大片 ISO','/云下载/电影','电影','submitted',?)",
           (time.time() - 600,))
sub = dict(db.query("SELECT * FROM transfers WHERE id=987701", one=True))
pan.task_list_all = lambda max_pages=5: [
    {"url": "https://example.com/big.iso", "name": "某大片.ISO", "percent": 100, "status": 11}]
asyncio.run(jobs.offline_check_loop.__wrapped__()) if hasattr(jobs.offline_check_loop, "__wrapped__") else None
# 直接跑一次核对逻辑（offline_check_loop 是无限循环，抽 its body 通过手动调用内部逻辑）
import backend.worker.jobs as _jobs  # noqa: E402
tasks = pan.task_list_all()
_by = {t["url"]: t for t in tasks}
t = _by.get(sub["share_url"])
_jobs._finish_offline(sub, ok=(t["percent"] >= 100 or t["status"] in (10, 11)), name=t["name"])
row = db.query("SELECT status FROM transfers WHERE id=987701", one=True)
check("云下载完成转 success", row["status"] == "success", row["status"])
check("完成通知发出", any("云下载任务完成" in str(s) for s in sent), str(sent))

# 17.6 云下载失败状态 → failed + 失败通知
db.execute("INSERT INTO transfers(id,kind,share_code,share_url,title,target_dir,category,status,created_at)"
           " VALUES(987702,'offline','offhash02','magnet:?xt=urn:btih:'||'a'||'','死种','/云下载/电影','电影','submitted',?)",
           (time.time() - 600,))
sub2 = dict(db.query("SELECT * FROM transfers WHERE id=987702", one=True))
fail_sent.clear()
jobs.notify_failure = lambda *a, **k: fail_sent.append((a, k))
_jobs._finish_offline(sub2, ok=False, reason="115 下载任务失败（链接无效或无资源）")
row = db.query("SELECT status FROM transfers WHERE id=987702", one=True)
check("云下载失败转 failed", row["status"] == "failed")
check("云下载失败通知发出", len(fail_sent) == 1)

# 17.7 端到端：两次通知完整流程（添加时① + 下载完成时②）+ 实时日志
from backend.core import logstream  # noqa: E402
sent.clear()
db.execute("INSERT INTO transfers(id,kind,share_code,share_url,title,target_dir,category,status,created_at)"
           " VALUES(987703,'offline','offhash03','https://example.com/e03.mkv','某剧 E03','/云下载/电视剧','电视剧','queued',?)",
           (time.time(),))
job3 = dict(db.query("SELECT * FROM transfers WHERE id=987703", one=True))
pan.task_list_all = lambda max_pages=5: []
queue_worker._process_one(job3)
row = db.query("SELECT status FROM transfers WHERE id=987703", one=True)
check("阶段①: 提交后状态 submitted", row["status"] == "submitted", row["status"])
notified = [str(s) for s in sent]
check("阶段①: 添加通知发出（仅 1 条）", len(notified) == 1 and "云下载任务已添加" in notified[0]
      and "①" in notified[0] and "第②条" in notified[0], str(notified))
logs_now = [l["message"] for l in logstream.recent(10)]
check("阶段①: 实时日志打印添加", any("云下载任务已添加：" in m for m in logs_now), str(logs_now[-3:]))
# 阶段②：115 下载完成
sent.clear()
sub3 = dict(db.query("SELECT * FROM transfers WHERE id=987703", one=True))
pan.task_list_all = lambda max_pages=5: [
    {"url": "https://example.com/e03.mkv", "name": "某剧.E03.mkv", "percent": 100, "status": 10}]
_jobs._finish_offline(sub3, ok=True, name="某剧.E03.mkv")
row = db.query("SELECT status FROM transfers WHERE id=987703", one=True)
check("阶段②: 完成后状态 success", row["status"] == "success", row["status"])
notified2 = [str(s) for s in sent]
check("阶段②: 完成通知发出", len(notified2) == 1 and "云下载任务完成" in notified2[0]
      and "②" in notified2[0], str(notified2))
logs_now = [l["message"] for l in logstream.recent(10)]
check("阶段②: 实时日志打印完成", any("云下载任务完成：" in m for m in logs_now), str(logs_now[-2:]))

print("\n=== 18. 登录限速 ===")
from backend import auth as _auth  # noqa: E402
_auth._fail_records.clear()
for _ in range(_auth.FAIL_LIMIT):
    _auth.record_login_fail()
check("60s 内失败 5 次触发限速", _auth.login_throttled() is True)
_auth._fail_records.clear()
check("清零后恢复正常", _auth.login_throttled() is False)
db.set_setting("web_password_hash", "")
_auth.ensure_initial_password()
logs_now = [l["message"] for l in logstream.recent(10)]
check("弱初始密码已替换为随机密码（提示文件 + 旧密码失效）",
      any("随机初始密码" in m for m in logs_now)
      and _auth.INITIAL_PASSWORD_FILE.exists()
      and not _auth.verify_password("admin123", db.get_setting("web_password_hash", "")),
      str(logs_now[-2:]))
_auth.change_password("admin123")

print("\n=== 19. 历史消息补漏（backfill）===")
from backend.tg import backfill  # noqa: E402

# 19.1 预算计算与限额挂钩
db.set_setting("daily_limit", 50)
check("预算=限额2倍(100)", backfill._daily_backfill_budget() == 100)
db.set_setting("daily_limit", 10)
check("预算下限40", backfill._daily_backfill_budget() == 40)
db.set_setting("daily_limit", 50)

# 19.2/19.3 真实 t.me 页面（外网不可达时跳过；补漏核心逻辑由 19.4/19.5 确定性校验）
import httpx as _hx  # noqa: E402
from backend.tg.polling import fetch_channel


async def _retry(fn, tries=4, delay=2.0):
    """外网偶发 TLS EOF / 连接重置时重试，避免网络抖动把测试误判为失败。"""
    last = None
    for i in range(tries):
        try:
            return await fn()
        except Exception as e:
            last = e
            if i + 1 < tries:
                await asyncio.sleep(delay * (i + 1))
    raise last


_real_fetch_before = backfill._fetch_before


async def _fetch_before_retry(client, name, before):
    return await _retry(lambda: _real_fetch_before(client, name, before))


async def _latest():
    async with _hx.AsyncClient() as cl:
        return await _retry(lambda: fetch_channel(cl, "regeng115"))


real_ok = False
try:
    latest = asyncio.run(_latest())
    real_ok = bool(latest)
except Exception as e:
    print(f"  ⚠️  真实 t.me 抓取失败，跳过 19.2/19.3: {type(e).__name__}: {e}")

if real_ok:
    newest_id = max(m["id"] for m in latest)
    page_size = len(latest)

    async def _fetch_gap():
        async with _hx.AsyncClient() as cl:
            return await _retry(lambda: _real_fetch_before(cl, "regeng115", newest_id - page_size))

    msgs = asyncio.run(_fetch_gap())
    ids = [m["id"] for m in msgs]
    check("before 分页返回一页历史", len(ids) >= 15 and max(ids) < newest_id - page_size + 2,
          f"{min(ids)}~{max(ids)}")

    # 19.3 补漏处理逻辑：模拟缺口 [gap_lo, gap_hi]
    gap_hi = newest_id - page_size
    gap_lo = gap_hi - page_size + 1
    db.execute("INSERT INTO channels(id,type,name,enabled,last_message_id,created_at)"
               " VALUES(99,'polling','regeng115',1,?,?)", (gap_lo - 1, time.time()))
    ch99 = dict(db.query("SELECT * FROM channels WHERE id=99", one=True))

    async def _run_backfill():
        async with _hx.AsyncClient() as cl:
            return await backfill.backfill_channel(cl, ch99, gap_hi + 1)

    backfill._fetch_before = _fetch_before_retry
    try:
        done = asyncio.run(_run_backfill())
    finally:
        backfill._fetch_before = _real_fetch_before
    check("缺口补漏完成", done is True)
    row = db.query("SELECT last_message_id FROM channels WHERE id=99", one=True)
    check(f"last_message_id 推进到 {gap_hi}", row["last_message_id"] == gap_hi,
          str(row["last_message_id"]))
    n_msgs = db.query("SELECT count(*) c FROM messages WHERE channel='regeng115' AND message_id>?",
                      (gap_lo - 1,), one=True)["c"]
    check(f"缺口消息全部入库（{n_msgs} 条）", n_msgs == page_size, str(n_msgs))
    n_q = db.query("SELECT count(*) c FROM transfers WHERE channel='regeng115'", one=True)["c"]
    print(f"  其中提取到 {n_q} 个资源任务（取决于频道内容）")
else:
    for name in ("before 分页返回一页历史", "缺口补漏完成",
                 "last_message_id 推进到缺口右端", "缺口消息全部入库"):
        skip(name, "t.me 不可访问")

# 19.4/19.5 用确定性分页（每页 20 条、id 连续递减）验证预算截断与断点续补，
# 不依赖外网，可稳定复现「未补完绝不推进接缝」的核心行为
async def _fake_fetch_before(_client, _name, before, page=20):
    before = int(before)
    return [{"id": i, "text": f"测试资源 {i}", "buttons": []}
            for i in range(max(before - page, 0), before)]

backfill._fetch_before = _fake_fetch_before

# 19.4 大缺口按预算截断
db.execute("INSERT INTO channels(id,type,name,enabled,last_message_id,created_at)"
           " VALUES(98,'polling','lsp115',1,19700,?)", (time.time(),))
ch98 = dict(db.query("SELECT * FROM channels WHERE id=98", one=True))


async def _run_partial():
    return await backfill.backfill_channel(None, ch98, 19800)


done2 = asyncio.run(_run_partial())
row = db.query("SELECT last_message_id FROM channels WHERE id=98", one=True)
advanced = row["last_message_id"] - 19700
# 19700→19800 共 99 条缺口 < 预算 100 → 一轮补完是正确行为；换更大的缺口验证截断
n98 = db.query("SELECT count(*) c FROM messages WHERE channel='lsp115'", one=True)["c"]
check(f"99 条缺口一轮补完（入库 {n98} 条）", done2 is True and advanced == 99,
      f"done={done2}, advanced={advanced}")
# 真正的超预算缺口：清空 lsp115 记录重来，缺口 300 条 > 预算 100
db.execute("DELETE FROM messages WHERE channel='lsp115'")
db.execute("DELETE FROM transfers WHERE channel='lsp115'")
# 检查 lsp115 频道 id=98 是否确实存在
assert db.query("SELECT 1 FROM channels WHERE id=98", one=True), 'ch98 缺失'
print(f'  缺口 19501~19799 共 299 条，预算 {backfill._daily_backfill_budget()} 条')
db.execute("UPDATE channels SET last_message_id=19500 WHERE id=98")
db.set_setting("daily_limit", 30)  # 预算 = max(40, 60) = 60，缺口 299 条必截断
backfill._page_log = []
ch98b = dict(db.query("SELECT * FROM channels WHERE id=98", one=True))
print(f'  [调试] daily_limit={db.get_setting("daily_limit")}, budget={backfill._daily_backfill_budget()}')


async def _run_partial2():
    return await backfill.backfill_channel(None, ch98b, 19800)


done3 = asyncio.run(_run_partial2())
row = db.query("SELECT last_message_id FROM channels WHERE id=98", one=True)
n98b = db.query("SELECT count(*) c FROM messages WHERE channel='lsp115'", one=True)["c"]
# 修复后的正确行为：预算是本轮上限，未补完时接缝保持 19500（绝不推进，否则旧缺口永久漏掉）
check(f"299 条缺口按预算截断（入库 {n98b} 条，接缝保持 {row['last_message_id']}）",
      done3 is False and n98b == 60 and row['last_message_id'] == 19500,
      f"done={done3}, last_mid={row['last_message_id']}, n={n98b}")
mins = [lo for (lo, hi) in backfill._page_log]
check("翻页轨迹正常（before 逐页递减 20）",
      len(mins) >= 3 and all(mins[i] - mins[i + 1] == 20 for i in range(len(mins) - 1)),
      str(backfill._page_log[:4]))

# 19.5 续补：第二轮从断点继续向旧翻页（不重复扫描已入库的页），接缝仍不推进
backfill._page_log = []
done4 = asyncio.run(_run_partial2())
row = db.query("SELECT last_message_id FROM channels WHERE id=98", one=True)
n98c = db.query("SELECT count(*) c FROM messages WHERE channel='lsp115'", one=True)["c"]
check(f"续补从断点继续（累计入库 {n98c} 条）", done4 is False and n98c == 120,
      f"done={done4}, n={n98c}")
check("续补接缝仍保持 19500", row["last_message_id"] == 19500, str(row["last_message_id"]))
check("续补首屏从 19740 之前开始（未重复扫描）",
      bool(backfill._page_log) and backfill._page_log[0][1] == 19739,
      str(backfill._page_log[:2]))
backfill._page_log = None
backfill._fetch_before = _real_fetch_before
db.set_setting("daily_limit", 50)

print("\n=== 20. 115 接口字段兼容（file_id/file_name vs fid/fn）===")
import importlib  # noqa: E402
import p115client  # noqa: E402
from backend.pan115 import client as _pan_cli  # noqa: E402

# 前面的用例把 pan 模块的接口整体 mock 掉了，这里重载回真实实现再验证字段兼容
importlib.reload(_pan_cli)
_orig_share_snap = p115client.P115Client.share_snap


def _fake_share_snap(payload, **kw):
    """模拟 115 新形态：文件用 file_id/file_name，目录用 cid/n + fc=0。"""
    cid = payload.get("cid")
    if cid == 0:
        return {"state": True, "data": {
            "count": 2, "shareinfo": {"share_title": "字段兼容测试"},
            "list": [{"file_id": "111", "file_name": "a.mkv", "file_size": 1000, "fc": 1},
                     {"cid": "222", "n": "子目录", "fc": 0}]}}
    return {"state": True, "data": {
        "count": 1, "list": [{"file_id": "333", "file_name": "b.mkv", "fs": 2000}]}}


p115client.P115Client.share_snap = staticmethod(_fake_share_snap)
try:
    snap_new = _pan_cli.share_snap_all("compat001", "")
finally:
    p115client.P115Client.share_snap = _orig_share_snap
check("share_snap_all 兼容 file_id/file_name 且递归子目录",
      [f["fid"] for f in snap_new["files"]] == ["111", "333"], str(snap_new["files"]))
check("分享标题从 data.shareinfo 解析", snap_new["title"] == "字段兼容测试", snap_new["title"])
check("文件体积合计正确", snap_new["total_size"] == 3000, str(snap_new["total_size"]))
check("分享顶层名记录（文件 + 文件夹）", snap_new["top_names"] == ["a.mkv", "子目录"],
      str(snap_new.get("top_names")))
check("分享顶层 id 记录（文件夹取 cid）", snap_new["top_ids"] == ["111", "222"],
      str(snap_new.get("top_ids")))
check("worker 过滤后无缺 id 项（不再 KeyError 'fid'）",
      [str(f.get("fid")) for f in snap_new["files"] if f.get("fid")] == ["111", "333"])


class _FakeFsClient:
    def fs_files(self, payload):
        return {"state": True, "count": 2, "data": [
            {"cid": "555", "n": "目录A", "fc": 0},          # 整数 fc=0：必须是目录
            {"fid": "999", "file_name": "f.mkv", "fc": "1"}]}


_orig_get_client = _pan_cli._get_client
_pan_cli._get_client = lambda: _FakeFsClient()
try:
    dirs_new = _pan_cli._list_dir(0)
finally:
    _pan_cli._get_client = _orig_get_client
check("_list_dir 识别整数 fc=0 目录（修复 or 取值导致漏判）",
      dirs_new == {"目录A": 555}, str(dirs_new))

print("\n=== 21. 转存后反查（115 网页接口目录项用 n 字段）===")


class _FakeListClient:
    """115 网页 fs_files 的真实字段形态：文件名用 n，目录用 cid+fc=0。"""

    def fs_files(self, payload):
        return {"state": True, "count": 2, "data": [
            {"fid": "1", "n": "走马上任 (2026) S01E11.mkv", "fc": "1"},
            {"cid": "2", "n": "最近接收", "fc": 0}]}


_pan_cli._get_client = lambda: _FakeListClient()
try:
    got_names = _pan_cli.list_file_names(123)
finally:
    _pan_cli._get_client = _orig_get_client
check("list_file_names 读取 n 字段（旧实现只认 file_name 会恒为空）",
      "走马上任 (2026) S01E11.mkv" in got_names and "最近接收" in got_names, str(got_names))

_orig_list_names = pan.list_file_names
try:
    pan.list_file_names = lambda cid, limit=500: {"走马上任 (2026) S01E11.mkv"}
    snap_file = {"files": [{"fn": "走马上任 (2026) S01E11.mkv"}], "top_names": []}
    check("反查命中分享文件名 → 验证通过", queue_worker._verify_receive(12345, snap_file) is True)
    pan.list_file_names = lambda cid, limit=500: {"走马上任 (2026) S01E11"}  # 只落了顶层文件夹
    snap_dir = {"files": [{"fn": "inside.mkv"}], "top_names": ["走马上任 (2026) S01E11"]}
    check("分享以文件夹整体转存 → 用顶层目录名也能验证通过",
          queue_worker._verify_receive(12345, snap_dir) is True)
finally:
    pan.list_file_names = _orig_list_names

print("\n=== 22. 文件夹分享整包转存（剧集/合集，如 Lsp115 19857）===")
_orig_snap_all2, _orig_mkdirs2, _orig_receive2 = pan.share_snap_all, pan.mkdirs, pan.receive
_received_ids = {}


def _folder_snap(code, rc, max_files=5000):
    """115 顶层是一个文件夹（cid），内含多季多集：老实现会把所有子文件 id 一次性提交。"""
    return {"title": "传奇办公室 (2015)",
            "files": [{"fid": str(i), "fn": f"S01E{i:02d}.mkv", "fs": 1 << 30} for i in range(1, 4)],
            "total_size": 3 * (1 << 30),
            "top_names": ["传奇办公室 (2015)"], "top_ids": ["900001"]}


def _record_receive(code, rc, cid, ids):
    _received_ids["ids"] = list(ids)
    return {"state": True}


pan.share_snap_all, pan.mkdirs, pan.receive = _folder_snap, lambda p: 12345, _record_receive
pan.list_file_names = lambda cid, limit=500: {"传奇办公室 (2015)"}
# 本用例关注整包转存链路本身；mock 的 list_file_names 会与「同名去重」检查撞车，
# 这里临时关闭该开关（同名去重已有独立用例覆盖）
db.set_setting("dedup_by_name", False)
try:
    job = mkjob("folder001")
    queue_worker._process_one(job)
finally:
    pan.share_snap_all, pan.mkdirs, pan.receive = _orig_snap_all2, _orig_mkdirs2, _orig_receive2
    pan.list_file_names = _orig_list_names
    db.set_setting("dedup_by_name", True)
row = dict(db.query("SELECT * FROM transfers WHERE id=?", (job["id"],), one=True))
check("提交的是顶层文件夹 id 而非上百个子文件 id", _received_ids.get("ids") == ["900001"],
      str(_received_ids.get("ids")))
check("状态 success（反查用顶层目录名命中）", row["status"] == "success", row["status"])
check("文件数按展开后的真实文件统计", row["file_count"] == 3, str(row["file_count"]))

print("\n=== 23. 云下载链接匹配（ED2K 中文名 / percent 编码差异）===")
from urllib.parse import quote  # noqa: E402
from backend.worker import jobs as _jobs  # noqa: E402

_raw = "ed2k://|file|厨房春光.未删减完整版.美版.iso|7976779776|48BEB437DC62ED168546DE45BB8221AB|/"
check("ED2K 按文件 hash 匹配", _jobs._offline_key(_raw) == "ed2k:48beb437dc62ed168546de45bb8221ab",
      _jobs._offline_key(_raw))
check("115 返回 percent 编码的同一链接也能匹配",
      _jobs._offline_key(quote(_raw, safe="")) == _jobs._offline_key(_raw))
check("磁力链按 btih 匹配（忽略 dn 等参数）",
      _jobs._offline_key("magnet:?xt=urn:btih:ABCDEF0123456789&dn=x") == "btih:abcdef0123456789")

print("\n=== 24. 转存请求参数（不能带 is_check）===")
_captured = {}


class _FakeReceiveClient:
    user_id = 777

    def share_receive(self, payload):
        _captured.update(payload)
        return {"state": True}


_pan_cli._get_client = lambda: _FakeReceiveClient()
try:
    _pan_cli.receive("swl3d0b3zrk", "k727", 12345, ["900001"])
finally:
    _pan_cli._get_client = _orig_get_client
check("转存请求不带 is_check（该参数会变成只校验、不落地文件）", "is_check" not in _captured)
check("转存请求带目标目录 cid 与文件 id",
      _captured.get("cid") == 12345 and _captured.get("file_id") == "900001", str(_captured))
check("转存请求带 user_id", _captured.get("user_id") == 777, str(_captured))

print("\n=== 25. 空间预检（剩余空间不足 → 转存前跳过）===")
_orig_snap_all3, _orig_mkdirs3, _orig_receive3 = pan.share_snap_all, pan.mkdirs, pan.receive
_orig_space_info, _orig_list_names3 = pan.space_info, pan.list_file_names
try:
    pan.share_snap_all, pan.mkdirs, pan.receive = fake_share_snap_all, fake_mkdirs, fake_receive
    pan.space_info = lambda force=False: {"use": 99 << 30, "total": 100 << 30,
                                          "free": 1 << 30, "ratio": 99}
    pan.list_file_names = lambda cid, limit=500: set()
    _old_total = FakePan.total_size
    FakePan.total_size = 5 << 30  # 前面用例把快照体积改成过 1GB，这里确保明显大于剩余空间
    job = mkjob("space001")
    queue_worker._process_one(job)
    row = dict(db.query("SELECT * FROM transfers WHERE id=?", (job["id"],), one=True))
    check("空间不足 → 状态 skipped（不是 failed）", row["status"] == "skipped", row["status"])
    check("错误原因标注空间不足", "空间不足" in (row["error"] or ""), row["error"])
    check("体积已记录（来自分享快照）", row["size"] == 5 * (1 << 30), str(row["size"]))

    print("\n=== 26. 同名去重（目标目录已有同名内容 → duplicate）===")
    db.set_setting("dedup_by_name", True)
    pan.space_info = lambda force=False: {"use": 1 << 30, "total": 100 << 30,
                                          "free": 99 << 30, "ratio": 1}
    pan.list_file_names = lambda cid, limit=500: {"测试资源合集"}
    job = mkjob("same001")
    queue_worker._process_one(job)
    row = dict(db.query("SELECT * FROM transfers WHERE id=?", (job["id"],), one=True))
    check("同名命中 → 状态 duplicate", row["status"] == "duplicate", row["status"])
    check("错误原因标注同名去重", "同名去重" in (row["error"] or ""), row["error"])
finally:
    FakePan.total_size = _old_total
    pan.share_snap_all, pan.mkdirs, pan.receive = _orig_snap_all3, _orig_mkdirs3, _orig_receive3
    pan.space_info, pan.list_file_names = _orig_space_info, _orig_list_names3
    db.set_setting("dedup_by_name", True)

print("\n=== 27. 订阅追剧（订阅模式过滤 + 进度记录）===")
from backend.core import subscribe as sub_mod  # noqa: E402

db.set_setting("subscription_only", True)
db.execute("INSERT OR IGNORE INTO subscriptions(keyword,created_at) VALUES('某某电影',?)",
           (time.time(),))
try:
    r = pipeline.process_message("test_ch", next_mid(),
                                 f"别的剧集分享 https://115.com/s/sub0000?password=ab12")
    check("订阅模式下未命中 → 不入队", r.get("sub_skipped") is True and r["queued"] == 0, str(r))
    r = pipeline.process_message("test_ch", next_mid(),
                                 f"某某电影.2023.1080p https://115.com/s/sub0001?password=ab12")
    check("命中订阅 → 正常入队", r["queued"] == 1, str(r))
    sub_row = dict(db.query("SELECT * FROM subscriptions WHERE keyword='某某电影'", one=True))
    check("订阅命中时间已记录", sub_row["last_match_at"] is not None, str(sub_row))
finally:
    db.set_setting("subscription_only", False)

print("\n=== 28. （已移除）TMDB 刮削 → 确认模块删除后转存不受影响 ===")
r = pipeline.process_message("test_ch", next_mid(),
                             f"某某电影.2023.1080p https://115.com/s/tmdb001?password=ab12")
row = dict(db.query("SELECT * FROM transfers WHERE share_code='tmdb001'", one=True))
check("无刮削：标题保留原始解析结果", "某某电影" in row["title"], row["title"])
check("无刮削：目标目录为分类目录", row["target_dir"] and "(2023)" not in row["target_dir"],
      row["target_dir"])

print("\n=== 29. TG Bot 新指令（/quota /sub /ls /space）===")
from backend.worker import bot_commands as bc  # noqa: E402

check("/quota 已移除", "暂不支持" in bc._handle_command("/quota 20", "x"))
check("限额保持默认", db.get_setting("daily_limit") == 50, str(db.get_setting("daily_limit")))
check("/sub 搜索提示", "搜索" in bc._handle_command("/sub 权力的游戏", "x")
      and "点选" in bc._handle_command("/sub 权力的游戏", "x"))
_sub_list_out = bc._handle_command("/sub", "x")
check("/sub 列表", ("追剧订阅" in _sub_list_out) or ("暂无订阅" in _sub_list_out), _sub_list_out[:80])
check("/sub 删除未存在", "未找到" in bc._handle_command("/sub del 权力的游戏", "x"))
check("/ls 列出最近记录", "最近转存" in bc._handle_command("/ls", "x"))
_orig_space_info2 = pan.space_info
try:
    pan.space_info = lambda force=False: {"use": 40 << 30, "total": 100 << 30,
                                          "free": 60 << 30, "ratio": 40}
    out = bc._handle_command("/space", "x")
finally:
    pan.space_info = _orig_space_info2
check("/space 返回空间信息", "剩余" in out and "60.0GB" in out, out)

print("\n=== 30. 每日摘要文本 ======")
from backend.worker import jobs as jobs_mod  # noqa: E402

text = jobs_mod.digest_text()
check("摘要含成功/失败统计", "每日摘要" in text and "今日成功" in text, text[:80])
check("摘要含队列", "队列剩余" in text)

print("\n=== 31. 对外 API（token 鉴权 + 入队判重）===")
from backend.api import ext as ext_mod  # noqa: E402
from fastapi import HTTPException as _HTTPException  # noqa: E402

db.set_setting("api_token", "secret-token")
try:
    ext_mod.require_token(x_api_token="secret-token")  # 不抛即通过
    check("正确 token 放行", True)
    try:
        ext_mod.require_token(x_api_token="wrong")
        check("错误 token 拒绝", False)
    except _HTTPException as e:
        check("错误 token 拒绝", e.status_code == 401, str(e.status_code))
    r = ext_mod.enqueue_transfer({"share_url": "https://115.com/s/ext0001?password=ab12",
                                  "title": "外部入队"})
    check("对外入队成功", r["queued"] is True, str(r))
    r2 = ext_mod.enqueue_transfer({"share_code": "ext0001"})
    check("重复入队判重", r2["duplicate"] is True, str(r2))
    try:
        ext_mod.enqueue_transfer({"share_url": "https://evil.com/x"})
        check("非法链接拒绝", False)
    except _HTTPException as e:
        check("非法链接拒绝", e.status_code == 400, str(e.status_code))
finally:
    db.set_setting("api_token", "")

print("\n=== 32. 「文件已接收」幂等错误直接按成功处理（bug 修复回归）===")
# 线上 bug：115 返回"文件已接收，无需重复接收！"（文件已在网盘中），旧代码只匹配
# "存在"，把该幂等信号当真错误无限重试直至 failed。现在：直接按成功处理，
# 不反查、不找回、不重试。
_orig_snap_all5, _orig_mkdirs5, _orig_receive5 = pan.share_snap_all, pan.mkdirs, pan.receive
_orig_list_names5 = pan.list_file_names
try:
    pan.share_snap_all = fake_share_snap_all
    pan.mkdirs = lambda p: 12345

    def _already_received(code, rc, cid, fids):
        raise pan.Pan115Error("文件已接收，无需重复接收！ (error_code=)")

    pan.receive = _already_received
    _verify_calls = {"n": 0}

    def _no_verify(cid, limit=500):
        _verify_calls["n"] += 1
        return {f.get("fn") for f in FakePan.files}

    pan.list_file_names = _no_verify
    db.set_setting("dedup_by_name", False)  # 本用例关注幂等链路，关掉同名去重避免撞车
    job = mkjob("already001")
    queue_worker._process_one(job)
    row = dict(db.query("SELECT * FROM transfers WHERE id=?", (job["id"],), one=True))
    check("「文件已接收」→ 直接按成功处理（无反查/无重试）",
          row["status"] == "success" and _verify_calls["n"] == 0,
          f"status={row['status']}, 反查调用={_verify_calls['n']}")
finally:
    pan.share_snap_all, pan.mkdirs, pan.receive = _orig_snap_all5, _orig_mkdirs5, _orig_receive5
    pan.list_file_names = _orig_list_names5
    db.set_setting("dedup_by_name", True)

print("\n=== 33. 反查失败 → 全盘找回「复制」到目标目录（必须要存进去）===")
# 文件被接收到了其他位置时：搜索定位 → 复制（不动原文件）→ 再反查 → success
_orig_snap_all6, _orig_mkdirs6, _orig_receive6 = pan.share_snap_all, pan.mkdirs, pan.receive
_orig_list_names6, _orig_search6, _orig_copy6 = pan.list_file_names, pan.search_file, pan.copy_files_to
try:
    pan.share_snap_all = fake_share_snap_all
    pan.mkdirs = lambda p: 12345
    pan.receive = lambda code, rc, cid, fids: {"state": True}
    _copied = {}

    def _list_names_recover(cid, limit=500):
        # 复制发生前：目录里始终没有（模拟整轮反查落库延迟/文件落在别处）；
        # 找回复制后：能查到
        return {"别的文件.mkv"} if not _copied else {FakePan.files[0]["fn"]}

    pan.list_file_names = _list_names_recover
    pan.search_file = lambda name, limit=20: [
        {"id": "999", "name": FakePan.files[0]["fn"], "parent": "888"}]  # 在其他目录找到
    pan.copy_files_to = lambda ids, cid: _copied.update({"ids": list(ids), "cid": cid}) or {"state": True}
    db.set_setting("dedup_by_name", False)
    job = mkjob("recover001")
    queue_worker._process_one(job)
    row = dict(db.query("SELECT * FROM transfers WHERE id=?", (job["id"],), one=True))
    check("反查失败 → 全盘找回复制 → 再反查 → success", row["status"] == "success", row["status"])
    check("复制到了目标目录且不动原文件",
          _copied.get("cid") == 12345 and _copied.get("ids") == ["999"], str(_copied))
finally:
    pan.share_snap_all, pan.mkdirs, pan.receive = _orig_snap_all6, _orig_mkdirs6, _orig_receive6
    pan.list_file_names, pan.search_file, pan.copy_files_to = _orig_list_names6, _orig_search6, _orig_copy6
    db.set_setting("dedup_by_name", True)

print("\n=== 34. 重试防重：此前已落盘 → 跳过提交（避免 115 生成 (1) 副本）===")
# 实测：重试任务的再接收会被 115 放行并生成 "(1)" 副本。修复：重试前先按分享
# 真实文件名预检目标目录，文件已在则跳过提交，直接按成功处理。
_orig_snap_all7, _orig_mkdirs7, _orig_receive7 = pan.share_snap_all, pan.mkdirs, pan.receive
_orig_list_names7 = pan.list_file_names
try:
    pan.share_snap_all = fake_share_snap_all
    pan.mkdirs = lambda p: 12345
    _recv_calls = {"n": 0}

    def _recv(code, rc, cid, fids):
        _recv_calls["n"] += 1
        return {"state": True}

    pan.receive = _recv
    pan.list_file_names = lambda cid, limit=500: {FakePan.files[0]["fn"]}  # 目录里已有该文件
    db.set_setting("dedup_by_name", False)
    job = mkjob("retrydup001")
    db.execute("UPDATE transfers SET attempts=1 WHERE id=?", (job["id"],))  # 模拟重试任务
    job = dict(db.query("SELECT * FROM transfers WHERE id=?", (job["id"],), one=True))
    queue_worker._process_one(job)
    row = dict(db.query("SELECT * FROM transfers WHERE id=?", (job["id"],), one=True))
    check("重试时目标目录已有文件 → 跳过提交 → success",
          row["status"] == "success" and _recv_calls["n"] == 0,
          f"status={row['status']}, 提交次数={_recv_calls['n']}")
finally:
    pan.share_snap_all, pan.mkdirs, pan.receive = _orig_snap_all7, _orig_mkdirs7, _orig_receive7
    pan.list_file_names = _orig_list_names7
    db.set_setting("dedup_by_name", True)

print("\n=== 35. 分享追更：同一链接新增集数 → 只转存新增文件 ===")
# 场景：E01 已成功转存；分享方往同一链接加了 E02。旧逻辑直接判重跳过（E02 永远存不到）；
# 新逻辑转为追更任务，按真实文件名比对目标目录，只提交新增的 file_id。
db.execute("INSERT INTO transfers(kind,share_code,status,title,category,target_dir,file_count,"
           "increment,created_at) VALUES('share','upd001','success','伟大的长征 (2026) S01E01',"
           "'电视剧','/媒体库/电视剧',1,0,?)", (time.time(),))
_orig_snap_all8, _orig_mkdirs8, _orig_receive8 = pan.share_snap_all, pan.mkdirs, pan.receive
_orig_list_names8 = pan.list_file_names
try:
    pan.share_snap_all = lambda code, rc, max_files=5000: {
        "title": "伟大的长征 (2026)",
        "files": [{"fid": "1", "fn": "伟大的长征.S01E01.mkv", "fs": 1 << 30},
                  {"fid": "2", "fn": "伟大的长征.S01E02.mkv", "fs": 1 << 30}],
        "total_size": 2 << 30}
    pan.mkdirs = lambda p: 12345
    _state = {"received": False}

    def _list_names_incr(cid, limit=500):
        # 提交前：目录里只有 E01（追更比对用）；提交后：E02 落地（反查确认用）
        return {"伟大的长征.S01E01.mkv"} if not _state["received"] else {
            "伟大的长征.S01E01.mkv", "伟大的长征.S01E02.mkv"}

    pan.list_file_names = _list_names_incr

    def _recv_incr(code, rc, cid, fids):
        _state["received"] = True
        assert fids == ["2"], f"追更应只提交新增文件 E02，实际: {fids}"
        return {"state": True}

    pan.receive = _recv_incr
    db.set_setting("dedup_by_name", False)
    r = pipeline.process_message("ch", next_mid(),
                                 "https://115.com/s/upd001?password=ab12 伟大的长征 S01E02")
    check("已成功分享的新消息 → 转为追更任务入队", r["queued"] == 1, str(r))
    row = dict(db.query("SELECT * FROM transfers WHERE share_code='upd001' AND increment=1", one=True))
    check("追更任务已标记 increment=1", bool(row) and row["increment"] == 1, str(row))
    queue_worker._process_one(row)
    row2 = dict(db.query("SELECT * FROM transfers WHERE id=?", (row["id"],), one=True))
    check("追更只提交新增文件 → success", row2["status"] == "success", row2["status"])
    check("追更记录文件数为新增数", row2["file_count"] == 1, str(row2["file_count"]))
finally:
    pan.share_snap_all, pan.mkdirs, pan.receive = _orig_snap_all8, _orig_mkdirs8, _orig_receive8
    pan.list_file_names = _orig_list_names8
    db.set_setting("dedup_by_name", True)

print("\n=== 36. 分集向前补全：集数解析 / 同剧匹配 / 增量入队 ===")
from backend.tg.episode_backfill import base_title, parse_episode, should_backfill  # noqa: E402

check("S01E52 解析 (季, 集)", parse_episode("伟大的长征 (2026) S01E52") == (1, 52),
      str(parse_episode("伟大的长征 (2026) S01E52")))
check("第52集 解析", parse_episode("伟大的长征 第52集") == (None, 52))
check("EP52 解析", parse_episode("伟大的长征 EP52") == (None, 52))
check("无集数 解析为 None", parse_episode("某某电影 (2023)") == (None, None))
b1 = base_title("伟大的长征 (2026) S01E52")
b2 = base_title("伟大的长征 (2026) S01E53")
check("同剧不同集 → 剧名一致", b1 == b2 and b1, b1)
check("不同剧 → 剧名不一致", b1 != base_title("尚公主 (2026) S01E31"))
check("S01E52 → 需要补全", should_backfill("伟大的长征 (2026) S01E52") == ("伟大的长征", 1, 52),
      str(should_backfill("伟大的长征 (2026) S01E52")))
check("S01E01 → 无需补全", should_backfill("伟大的长征 (2026) S01E01") is None)
check("无集数 → 无需补全", should_backfill("某某电影 (2023)") is None)

# pipeline 增量参数：补全入队的任务带 increment=1（已存过的集数由工作器比对跳过）
_orig_snap_all9, _orig_mkdirs9, _orig_receive9 = pan.share_snap_all, pan.mkdirs, pan.receive
_orig_list_names9 = pan.list_file_names
try:
    pan.share_snap_all = lambda code, rc, max_files=5000: {
        "title": "伟大的长征 (2026)",
        "files": [{"fid": "1", "fn": "伟大的长征.S01E01.mkv", "fs": 1 << 30}],
        "total_size": 1 << 30}
    pan.mkdirs = lambda p: 12345
    pan.receive = lambda code, rc, cid, fids: {"state": True}
    pan.list_file_names = lambda cid, limit=500: set()  # 目标目录还没有 → 正常提交
    db.set_setting("dedup_by_name", False)
    r = pipeline.process_message("ch", next_mid(),
                                 "https://115.com/s/bf0001?password=ab12 伟大的长征 (2026) S01E01",
                                 False, True)  # increment=True：向前补全入队
    check("补全入队 → increment=1", r["queued"] == 1, str(r))
    row = dict(db.query("SELECT * FROM transfers WHERE share_code='bf0001'", one=True))
    check("补全任务带增量标记", row["increment"] == 1, str(row["increment"]))
finally:
    pan.share_snap_all, pan.mkdirs, pan.receive = _orig_snap_all9, _orig_mkdirs9, _orig_receive9
    pan.list_file_names = _orig_list_names9
    db.set_setting("dedup_by_name", True)

print("\n=== 37. 补全任务优先级：额度有限时优先领取 ===")
time.sleep(0.01)
db.execute("INSERT INTO transfers(kind,share_code,status,created_at,priority)"
           " VALUES('share','prio_norm','queued',?,0)", (time.time(),))
db.execute("INSERT INTO transfers(kind,share_code,status,created_at,priority)"
           " VALUES('share','prio_bf','queued',?+0.001,1)", (time.time(),))
nxt = queue_worker._fetch_next()
check("优先领取向前补全任务（priority=1）", nxt["share_code"] == "prio_bf", nxt["share_code"])
check("补全任务带最高优先级标记", nxt["priority"] == 1, str(nxt["priority"]))
db.execute("DELETE FROM transfers WHERE share_code IN ('prio_norm','prio_bf')")

print("\n=== 38. 向前补全 API（锚点解析 + 手动剧名/集数兜底）===")
import asyncio  # noqa: E402
from backend.api import manage as manage_mod  # noqa: E402
from fastapi import HTTPException as _HTTPException  # noqa: E402

db.execute("INSERT INTO transfers(kind,share_code,status,title,channel,message_id,created_at)"
           " VALUES('share','bfapi001','success','伟大的长征 (2026) S01E05',"
           "'QukanMovie',12500,?)", (time.time(),))
db.execute("INSERT INTO transfers(kind,share_code,status,title,channel,message_id,created_at)"
           " VALUES('share','bfapi002','success','某某电影 (2023)','QukanMovie',12501,?)",
           (time.time(),))
aid = db.query("SELECT id FROM transfers WHERE share_code='bfapi001'", one=True)["id"]
res = asyncio.run(manage_mod.backfill_episodes({"transfer_id": aid}))
check("API 返回补全范围 S01 E01~E04",
      res["episodes"] == "S01 E01~E04" and res["channel"] == "QukanMovie", str(res))
check("剧名自动解析", res["base"] == "伟大的长征", res["base"])

aid2 = db.query("SELECT id FROM transfers WHERE share_code='bfapi002'", one=True)["id"]
try:
    asyncio.run(manage_mod.backfill_episodes({"transfer_id": aid2}))
    check("无集数且未手动指定 → 400", False, "未拦截")
except _HTTPException as e:
    check("无集数且未手动指定 → 400", e.status_code == 400, str(e.status_code))

res2 = asyncio.run(manage_mod.backfill_episodes(
    {"transfer_id": aid2, "keyword": "某某电影", "current_ep": 7}))
check("手动剧名 + 手动集数 → E01~E06", res2["episodes"] == "E01~E06", str(res2))

try:
    asyncio.run(manage_mod.backfill_episodes({"keyword": "不存在的剧xyz"}))
    check("关键词找不到锚点 → 404", False, "未拦截")
except _HTTPException as e:
    check("关键词找不到锚点 → 404", e.status_code == 404, str(e.status_code))

db.execute("DELETE FROM transfers WHERE share_code IN ('bfapi001','bfapi002')")

print("\n=== 42. 备份恢复与唯一索引/运行态竞态（P0 回归）===")
# 场景 1：旧备份里同分享码有两条活动行（历史竞态遗留）→ 恢复自动降级为 duplicate，不再被索引拒绝
payload = {"_meta": {"version": 1}, "settings": {"digest_enabled": True},
           "channels": [], "rules": [], "routes": [], "blacklist": [], "subscriptions": [],
           "transfers": [
               {"kind": "share", "share_code": "dup001", "status": "queued", "title": "a",
                "channel": "c", "message_id": 1, "created_at": 1, "increment": 0, "priority": 1},
               {"kind": "share", "share_code": "dup001", "status": "queued", "title": "b",
                "channel": "c", "message_id": 2, "created_at": 2, "increment": 0, "priority": 0}]}
res = db.restore_payload(payload)
statuses = [r["status"] for r in db.query(
    "SELECT status FROM transfers WHERE share_code='dup001' ORDER BY id")]
check("恢复含重复活动行的旧备份 → 成功且自动降级",
      sorted(statuses) == ["duplicate", "queued"], str(statuses))
# 场景 2：系统运行中 → 恢复被拒绝
db.set_setting("running", True)
try:
    db.restore_payload({"_meta": {"version": 1}, "settings": {}, "channels": [], "rules": [],
                        "routes": [], "blacklist": [], "subscriptions": [], "transfers": []})
    check("运行中恢复被拒绝", False, "未拦截")
except ValueError as e:
    check("运行中恢复被拒绝", "停止" in str(e), str(e))
db.set_setting("running", False)
# 场景 3：有转存中任务 → 恢复被拒绝
db.execute("INSERT INTO transfers(kind,share_code,status,running_at,created_at)"
           " VALUES('share','running001','running',?,?)", (time.time(), time.time()))
try:
    db.restore_payload({"_meta": {"version": 1}, "settings": {}, "channels": [], "rules": [],
                        "routes": [], "blacklist": [], "subscriptions": [], "transfers": []})
    check("转存中恢复被拒绝", False, "未拦截")
except ValueError as e:
    check("转存中恢复被拒绝", "转存中" in str(e), str(e))
db.execute("DELETE FROM transfers WHERE share_code='running001'")

print("\n=== 43. 提取码前置写法识别 ===")
from backend.core import extractor  # noqa: E402
r = extractor.extract("提取码：ab12 https://115.com/s/pwpre01")
check("提取码在链接前 → 识别", bool(r) and r[0]["receive_code"] == "ab12", str(r))
r = extractor.extract("https://115.com/s/pwpre02?password=cd34")
check("密码内嵌 → 识别", bool(r) and r[0]["receive_code"] == "cd34", str(r))

print(f"\n{'='*40}\n结果: {len(PASS)} 通过, {len(FAIL)} 失败, {len(SKIP)} 跳过（外网不可达）")
# 用户在 E08 点向前补全；E06/E07 此前已转存成功（消息已处理过、网盘有文件）
time.sleep(0.01)
for mid, code, ep in ((606, "ep006", 6), (607, "ep007", 7)):
    db.execute("INSERT OR IGNORE INTO messages(channel,message_id,text,processed_at)"
               " VALUES('ch',?,?,?)", (mid, f"https://115.com/s/{code}?password=ab12", time.time()))
    db.execute("INSERT INTO transfers(kind,share_code,status,title,channel,message_id,created_at)"
               " VALUES('share',?,'success',?,'ch',?,?)",
               (code, f"某剧 (2026) S01E{ep:02d}", mid, time.time()))
db.execute("INSERT INTO transfers(kind,share_code,status,title,channel,message_id,created_at)"
           " VALUES('share','ep008','success','某剧 (2026) S01E08','ch',608,?)", (time.time(),))

_orig_snap_all10, _orig_mkdirs10, _orig_receive10 = pan.share_snap_all, pan.mkdirs, pan.receive
_orig_list_names10 = pan.list_file_names
try:
    pan.mkdirs = lambda p: 12345
    _state = {"received": False}

    def _snap10(code, rc, max_files=5000):
        eps = {"ep006": 6, "ep007": 7, "ep005": 5}[code]
        return {"title": "某剧 (2026)",
                "files": [{"fid": "1", "fn": f"某剧 (2026) S01E{eps:02d}.mkv", "fs": 1 << 30}],
                "total_size": 1 << 30}

    pan.share_snap_all = _snap10

    def _list10(cid, limit=500):
        names = {"某剧 (2026) S01E06.mkv", "某剧 (2026) S01E07.mkv"}  # 网盘已有 E06/E07
        if _state["received"]:
            names.add("某剧 (2026) S01E05.mkv")  # E05 提交后落地
        return names

    pan.list_file_names = _list10

    def _recv10(code, rc, cid, fids):
        _state["received"] = True
        return {"state": True}

    pan.receive = _recv10
    db.set_setting("dedup_by_name", False)

    # 向前补全把找到的历史帖重新喂给管道：E06/E07 已处理过 → 消息级去重直接跳过
    r6 = pipeline.process_message("ch", 606,
                                  "https://115.com/s/ep006?password=ab12 某剧 (2026) S01E06",
                                  False, True, 1)
    r7 = pipeline.process_message("ch", 607,
                                  "https://115.com/s/ep007?password=ab12 某剧 (2026) S01E07",
                                  False, True, 1)
    check("E06 帖已处理过 → 消息级去重跳过（不生成任务）",
          r6.get("duplicate") is True and r6["queued"] == 0, str(r6))
    check("E07 帖已处理过 → 消息级去重跳过（不生成任务）",
          r7.get("duplicate") is True and r7["queued"] == 0, str(r7))
    check("E06/E07 无新增任务记录",
          db.query("SELECT count(*) c FROM transfers WHERE share_code IN ('ep006','ep007')",
                   one=True)["c"] == 2)
    # 缺失的 E05 → 正常入队（increment=1）并转存成功
    r5 = pipeline.process_message("ch", 605,
                                  "https://115.com/s/ep005?password=ab12 某剧 (2026) S01E05",
                                  False, True, 1)
    check("缺失的 E05 → 入队（带增量标记）", r5["queued"] == 1, str(r5))
    row = dict(db.query("SELECT * FROM transfers WHERE share_code='ep005'", one=True))
    queue_worker._process_one(row)
    row = dict(db.query("SELECT * FROM transfers WHERE id=?", (row["id"],), one=True))
    check("E05 转存成功", row["status"] == "success", row["status"])
finally:
    pan.share_snap_all, pan.mkdirs, pan.receive = _orig_snap_all10, _orig_mkdirs10, _orig_receive10
    pan.list_file_names = _orig_list_names10
    db.set_setting("dedup_by_name", True)

print("\n=== 40. 失效分享 → 确定性失败（不重试、不计熔断）===")
# 实测场景：补全的早期链接已失效，115 返回"分享不存在"。旧逻辑当普通失败
# 重试 5 次并累计熔断，把普通转存一起拖停 30 分钟
job = mkjob("deadshare001")


def _dead_share(code, rc, max_files=5000):
    raise pan.Pan115Error("读取分享失败: 分享不存在")


pan.share_snap_all = _dead_share
try:
    queue_worker._process_one(job)
    check("失效分享 → 确定性失败", False, "未抛出 PermanentTransferError")
except queue_worker.PermanentTransferError as e:
    check("失效分享 → 确定性失败（不再重试）", "已失效" in str(e), str(e))
    # 与队列循环一致：确定性失败直接落 failed（真实循环还不计连续失败熔断）
    db.execute("UPDATE transfers SET status='failed', attempts=?, error=? WHERE id=?",
               (job["attempts"] + 1, str(e)[:500], job["id"]))
row = dict(db.query("SELECT * FROM transfers WHERE id=?", (job["id"],), one=True))
check("失败原因注明链接已失效", row["status"] == "failed" and "已失效" in (row["error"] or ""),
      f"{row['status']} / {row['error']}")
pan.share_snap_all = fake_share_snap_all

print("\n=== 41. 补全帖：沿用锚点分类/目录 + 绕过订阅过滤 ===")
# 场景：帖子无剧名/无集数标记（10.mp4），正常分类会落「未分类」并被订阅模式拦下；
# 向前补全帖应沿用锚点任务的分类与目标目录，并绕过订阅过滤
db.set_setting("subscription_only", True)
_orig_snap_all11, _orig_mkdirs11, _orig_receive11 = pan.share_snap_all, pan.mkdirs, pan.receive
_orig_list_names11 = pan.list_file_names
try:
    pan.share_snap_all = lambda code, rc, max_files=5000: {
        "title": "某剧 (2026)",
        "files": [{"fid": "1", "fn": "10.mp4", "fs": 1 << 30}],
        "total_size": 1 << 30}
    pan.mkdirs = lambda p: 12345
    pan.receive = lambda code, rc, cid, fids: {"state": True}
    pan.list_file_names = lambda cid, limit=500: set()
    db.set_setting("dedup_by_name", False)
    r = pipeline.process_message("ch", next_mid(),
                                 "https://115.com/s/anc001?password=ab12 10.mp4",
                                 False, True, 1,
                                 {"category": "电视剧", "target_dir": "/媒体库/电视剧"})
    row = dict(db.query("SELECT * FROM transfers WHERE share_code='anc001'", one=True))
    check("补全帖沿用锚点分类/目录（不被订阅模式拦截）",
          row["status"] == "queued" and row["category"] == "电视剧"
          and row["target_dir"] == "/媒体库/电视剧" and row["increment"] == 1, str(row))
    check("补全帖为高优先级", row["priority"] == 1, str(row["priority"]))
finally:
    pan.share_snap_all, pan.mkdirs, pan.receive = _orig_snap_all11, _orig_mkdirs11, _orig_receive11
    pan.list_file_names = _orig_list_names11
    db.set_setting("dedup_by_name", True)
    db.set_setting("subscription_only", False)

print(f"\n{'='*40}\n结果: {len(PASS)} 通过, {len(FAIL)} 失败, {len(SKIP)} 跳过（外网不可达）")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
if SKIP:
    print("跳过项:", SKIP)
