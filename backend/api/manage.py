"""主业务路由：登录/向导/仪表盘/频道/规则/黑名单/转存记录/设置/日志。"""
import re
import time

from fastapi import APIRouter, Depends, Header, HTTPException

from .. import auth, config, database as db
from ..core import classifier, filters, logstream, pipeline
from ..pan115 import client as pan
from ..worker import jobs
from .deps import require_user

router = APIRouter(prefix="/api")


# ---------- auth ----------
def _web_username() -> str:
    """当前登录用户名：优先 DB（网页可改），回退环境变量初始值。"""
    return str(db.get_setting("web_username") or config.WEB_USERNAME or "admin")


@router.post("/auth/login")
def login(body: dict):
    username = str(body.get("username", ""))[:64]
    password = str(body.get("password", ""))
    if len(password) > 128:
        auth.record_login_fail()
        raise HTTPException(400, "密码过长")
    if auth.login_throttled():
        logstream.warn("system", "登录请求被限速（连续失败过多）")
        raise HTTPException(429, "失败次数过多，请 1 分钟后再试")
    stored = db.get_setting("web_password_hash", "")
    if username != _web_username() or not auth.verify_password(password, stored):
        auth.record_login_fail()
        logstream.warn("system", "网页登录失败（用户名或密码错误）")
        raise HTTPException(401, "用户名或密码错误")
    logstream.info("system", "网页登录成功")
    return {"token": auth.make_token(username)}


@router.get("/auth/me")
def me(_=Depends(require_user)):
    return {"username": _web_username()}


# 显式列出允许字符（不用 \w：Python 的 \w 会放行西里尔文/韩文等所有 Unicode 文字，与前端规则不一致）
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_@\.\-\u4e00-\u9fa5]{2,32}$")


@router.post("/auth/password")
def change_pwd(body: dict, _=Depends(require_user)):
    """修改用户名和/或密码（都需验证旧密码）。body: {old, new?, username?}"""
    old = body.get("old", "")
    if not auth.verify_password(old, db.get_setting("web_password_hash", "")):
        auth.record_login_fail()  # 旧密码连续错误同样触发限速，防止已登录会话被暴力猜密码
        raise HTTPException(400, "旧密码错误")
    if auth.login_throttled():
        raise HTTPException(429, "失败次数过多，请 1 分钟后再试")
    username = str(body.get("username") or "").strip()
    new = str(body.get("new") or "")
    if not username and not new:
        raise HTTPException(400, "请至少填写新用户名或新密码其中一项")
    changed = []
    if username and username != _web_username():
        if not _USERNAME_RE.fullmatch(username):
            raise HTTPException(400, "用户名需为 2-32 位，仅限中英文、数字、@ . _ -")
        db.set_setting("web_username", username)
        changed.append(f"用户名 → {username}")
    if new:
        if len(new) < 8:
            raise HTTPException(400, "新密码至少 8 位")
        if len(new) > 128:
            raise HTTPException(400, "新密码最长 128 位")
        auth.change_password(new)
        changed.append("密码")
    logstream.info("system", "管理员账号已在网页修改（" + "、".join(changed) + "）")
    # 轮换 JWT 签名密钥：所有旧 token（含可能被窃取的）立即失效，本会话换取新 token 无感续期
    auth.rotate_secret()
    return {"ok": True, "username": _web_username(),
            "token": auth.make_token(_web_username())}


# ---------- wizard ----------
@router.get("/wizard/status")
def wizard_status(_=Depends(require_user)):
    return {
        "completed": bool(db.get_setting("setup_completed", False)),
        "cookie_ok": db.get_setting("cookie_ok", None),
        "userbot_ok": db.get_setting("userbot_ok", None),
        "channels": db.query("SELECT count(*) c FROM channels", one=True)["c"],
        "tg_mode": db.get_setting("tg_mode", "polling"),
    }


@router.post("/wizard/complete")
def wizard_complete(_=Depends(require_user)):
    db.set_setting("setup_completed", True)
    logstream.info("wizard", "初始化向导完成，等待用户点击「开始运行」")
    return {"ok": True}


def _readiness(strict_115: bool) -> dict:
    """启动就绪检查：115 登录 + 启用中频道 +（纯 Userbot 频道时）Userbot 在线。"""
    st = pan.check_login(strict_115)  # strict=True 强制校验真实登录态（绕过缓存）
    rows = db.query("SELECT type, enabled FROM channels")
    enabled = [dict(r) for r in rows if r["enabled"]]
    polling = [r for r in enabled if r["type"] == "polling"]
    userbots = [r for r in enabled if r["type"] == "userbot"]
    userbot_ok = bool(db.get_setting("userbot_ok", False))
    # 全部是 Userbot 频道（无网页轮询兜底）时，必须 Userbot 在线才能收到消息
    userbot_needed = bool(userbots) and not polling
    reasons = []
    if not st.get("ok"):
        reasons.append("115 未登录或 Cookie 失效（请扫码登录或导入 Cookie）")
    if not enabled:
        reasons.append("尚未添加启用中的频道")
    if userbot_needed and not userbot_ok:
        reasons.append("监控的频道均为 Userbot 类型，但 Userbot 未连接")
    return {
        "ok_115": bool(st.get("ok")),
        "channels": len(enabled),
        "polling": len(polling),
        "userbot": len(userbots),
        "userbot_ok": userbot_ok,
        "userbot_needed": userbot_needed,
        "ready": not reasons,
        "reasons": reasons,
    }


@router.get("/wizard/readiness")
def wizard_readiness(_=Depends(require_user)):
    """向导完成页的就绪清单（复用登录缓存，不打额外 115 请求）。"""
    return _readiness(strict_115=False)


@router.post("/system/start")
def system_start(_=Depends(require_user)):
    """开始运行：启动监听与转存。服务端强制就绪检查（防止前端被绕过）。"""
    r = _readiness(strict_115=True)
    if not r["ready"]:
        raise HTTPException(400, "无法开始运行：" + "；".join(r["reasons"]))
    db.set_setting("running", True)
    db.set_setting("paused", False)
    db.set_setting("auto_stop_reason", "")  # 清除上一次自动停止的原因
    logstream.info("system", "▶ 系统开始运行：监控 TG 频道并自动转存")
    return {"ok": True}


@router.post("/system/stop")
def system_stop(_=Depends(require_user)):
    """停止运行：暂停监听与转存（队列与配置保留）。"""
    db.set_setting("running", False)
    db.set_setting("auto_stop_reason", "")  # 手动停止时清除自动停止标记
    logstream.warn("system", "⏸ 系统已停止运行（配置与队列保留，可随时再次启动）")
    return {"ok": True}


@router.get("/system/state")
def system_state(_=Depends(require_user)):
    """运行状态 + 完整就绪检查（供顶栏「开始运行」按钮门控）。

    复用登录缓存（不增加 115 请求压力）；前端按钮据此灰置，服务端 /system/start 仍会强制校验兜底。
    """
    r = _readiness(strict_115=False)
    return {
        "running": bool(db.get_setting("running", False)),
        "setup_completed": bool(db.get_setting("setup_completed", False)),
        "paused": bool(db.get_setting("paused", False)),
        "auto_stop_reason": str(db.get_setting("auto_stop_reason", "") or ""),
        **r,
    }


# ---------- dashboard ----------
@router.get("/dashboard/stats")
def dashboard(_=Depends(require_user)):
    from datetime import datetime

    today_quota = db.quota_today()
    # 以服务器本地时区的 0 点为今日起点（与 quota_today 的 date.today() 保持一致）
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    rows = db.query(
        "SELECT status, count(*) c FROM transfers WHERE created_at>=? GROUP BY status", (today_start,))
    by_status = {r["status"]: r["c"] for r in rows}
    queued = db.query("SELECT count(*) c FROM transfers WHERE status='queued'", one=True)["c"]
    channels = db.query("SELECT count(*) c FROM channels WHERE enabled=1", one=True)["c"]
    return {
        "today": by_status,
        "today_success": by_status.get("success", 0),
        "today_failed": by_status.get("failed", 0) + by_status.get("blocked", 0),
        "quota": today_quota,
        "daily_limit": db.get_setting("daily_limit", 50),
        "queue": queued,
        "channels": channels,
        "cookie_ok": db.get_setting("cookie_ok", None),
        "userbot_ok": db.get_setting("userbot_ok", None),
    }


@router.get("/dashboard/recent")
def recent_transfers(_=Depends(require_user)):
    return [dict(r) for r in db.query(
        "SELECT id,title,category,target_dir,size,status,created_at,channel FROM transfers"
        " ORDER BY id DESC LIMIT 20")]


# ---------- channels ----------
@router.get("/channels")
def list_channels(_=Depends(require_user)):
    """频道列表 + 健康信息：连续失败轮数（poll_fail_*）、最近抓取时间、消息量。"""
    items = []
    for r in db.query("SELECT * FROM channels ORDER BY id DESC"):
        item = dict(r)
        try:
            item["fail_count"] = int(db.get_setting(f"poll_fail_{r['id']}", 0) or 0)
        except (TypeError, ValueError):
            item["fail_count"] = 0
        items.append(item)
    return items


@router.post("/channels")
async def add_channel(body: dict, _=Depends(require_user)):
    name = (body.get("name") or "").strip()
    ctype = body.get("type", "polling")
    # 允许粘贴完整链接
    m = re.search(r"t\.me/(?:s/)?@?([0-9A-Za-z_]+)", name)
    if m:
        name = m.group(1)
    name = name.lstrip("@")
    if not name:
        raise HTTPException(400, "频道名不能为空")
    if len(name) > 64:
        raise HTTPException(400, "频道名过长（最多 64 字符）")
    if ctype not in ("polling", "userbot"):
        raise HTTPException(400, "type 必须是 polling 或 userbot")
    if db.query("SELECT 1 FROM channels WHERE type=? AND name=?", (ctype, name), one=True):
        raise HTTPException(400, "该频道已存在")
    # 频道数量上限：防止误操作/脚本灌入海量频道拖垮轮询与 115 风控
    n = db.query("SELECT count(*) c FROM channels", one=True)["c"]
    if n >= 50:
        raise HTTPException(400, "监控频道已达上限 50 个，请先清理不需要的频道")

    # 添加前校验：确认该频道当前确实可被对应方式访问（轮询→t.me/s 可打开；Userbot→账号可解析）
    from ..tg import polling as tg_polling, userbot as tg_userbot
    res = await (tg_userbot.check_channel(name) if ctype == "userbot"
                 else tg_polling.check_channel(name))
    if not res.get("ok"):
        raise HTTPException(400, f"无法添加 @{name}：{res.get('err') or '频道不可访问，请核对后重试'}")
    title = str(res.get("title") or "")

    # 新频道继承全局订阅模式设置（之后可在「频道管理」按频道独立调整）
    from ..core import subscribe as _submod
    sub_only = 1 if _submod.subscription_only() else 0
    db.execute("INSERT INTO channels(type,name,sub_only,created_at) VALUES(?,?,?,?)",
               (ctype, name, sub_only, time.time()))
    logstream.info("channels",
                   f"添加监控频道 @{name}（{title}，接入方式：{ctype}，已通过访问校验）")
    return {"ok": True, "name": name, "title": title}


@router.put("/channels/{ch_id}")
def update_channel(ch_id: int, body: dict, _=Depends(require_user)):
    if "type" in body:
        new_type = body["type"]
        if new_type not in ("polling", "userbot"):
            raise HTTPException(400, "type 必须是 polling 或 userbot")
        ch = db.query("SELECT name, type FROM channels WHERE id=?", (ch_id,), one=True)
        if not ch:
            raise HTTPException(404, "频道不存在")
        if ch["type"] != new_type:
            db.execute("UPDATE channels SET type=? WHERE id=?", (new_type, ch_id))
            db.set_setting(f"poll_fail_{ch_id}", 0)  # 类型切换后失败计数重新累计
            label = {"polling": "网页轮询", "userbot": "Userbot"}
            logstream.info("channels",
                           f"频道 @{ch['name']} 接入方式已变更：{label.get(ch['type'], ch['type'])}"
                           f" → {label[new_type]}")
    if "enabled" in body:
        ch = db.query("SELECT name FROM channels WHERE id=?", (ch_id,), one=True)
        db.execute("UPDATE channels SET enabled=? WHERE id=?", (1 if body["enabled"] else 0, ch_id))
        if ch:
            state = "启用" if body["enabled"] else "停用"
            logstream.info("channels", f"已{state}监控频道 @{ch['name']}")
    if "sub_only" in body:
        ch = db.query("SELECT name FROM channels WHERE id=?", (ch_id,), one=True)
        if not ch:
            raise HTTPException(404, "频道不存在")
        db.execute("UPDATE channels SET sub_only=? WHERE id=?", (1 if body["sub_only"] else 0, ch_id))
        logstream.info("channels",
                       f"频道 @{ch['name']} 订阅模式已{'开启（仅转存命中订阅的内容）' if body['sub_only'] else '关闭（全盘转存）'}")
    return {"ok": True}


@router.delete("/channels/{ch_id}")
def delete_channel(ch_id: int, _=Depends(require_user)):
    """删除频道：连同该频道的消息 ID 记忆一起清除。

    重新添加同名频道时 messages 已为空、游标从 0 开始，等同新频道重新抓取；
    转存记录保留（已成功的分享码仍会命中去重，不会重复转存）。
    """
    ch = db.query("SELECT name FROM channels WHERE id=?", (ch_id,), one=True)
    if not ch:
        return {"ok": True, "cleared": 0}
    name = ch["name"]
    n = db.execute("DELETE FROM messages WHERE channel=?", (name,))
    db.execute("DELETE FROM channels WHERE id=?", (ch_id,))
    db.set_setting(f"poll_fail_{ch_id}", 0)  # 清理失败计数，避免残留影响健康判定
    try:
        from ..tg import backfill as _backfill
        _backfill._backfill_state.pop(ch_id, None)
    except Exception:
        pass
    logstream.info("channels",
                   f"删除监控频道 @{name}：已清除 {n} 条消息 ID 记忆（重新添加将按新频道处理）")
    return {"ok": True, "cleared": n}


@router.post("/channels/{ch_id}/reset")
def reset_channel(ch_id: int, _=Depends(require_user)):
    """重置频道：清除消息 ID 记忆与抓取游标，回到初始状态重新获取数据。

    - 删除 messages 表中该频道的去重记录（否则旧消息仍会被判重跳过）
    - last_message_id 归零：轮询下一轮把最新一页当作新消息处理（不触发历史补漏）
    - 清空失败计数与内存中的补漏游标，避免继续从旧断点接续
    - 转存记录保留：已成功的分享码仍会命中去重，不会重复转存
    """
    ch = db.query("SELECT * FROM channels WHERE id=?", (ch_id,), one=True)
    if not ch:
        raise HTTPException(404, "频道不存在")
    n = db.execute("DELETE FROM messages WHERE channel=?", (ch["name"],))
    db.execute("UPDATE channels SET last_message_id=0, last_fetch_at=NULL, msg_count=0 WHERE id=?",
               (ch_id,))
    db.set_setting(f"poll_fail_{ch_id}", 0)
    try:
        from ..tg import backfill as _backfill
        _backfill._backfill_state.pop(ch_id, None)  # 清掉续补游标，防止从旧断点继续
    except Exception:
        pass
    logstream.info("channels",
                   f"重置监控频道 @{ch['name']}：清除 {n} 条消息 ID 记录，抓取游标归零，"
                   "下一轮从最新消息重新开始")
    return {"ok": True, "cleared": n}


@router.get("/channels/{ch_id}/messages")
def channel_messages(ch_id: int, _=Depends(require_user)):
    ch = db.query("SELECT * FROM channels WHERE id=?", (ch_id,), one=True)
    if not ch:
        raise HTTPException(404, "频道不存在")
    rows = db.query(
        "SELECT m.*, (SELECT status FROM transfers t WHERE t.channel=m.channel AND t.message_id=m.message_id"
        " LIMIT 1) transfer_status FROM messages m WHERE m.channel=? ORDER BY m.message_id DESC LIMIT 50",
        (ch["name"],))
    return [dict(r) for r in rows]


@router.post("/channels/{ch_id}/messages/{message_id}/transfer")
def transfer_message(ch_id: int, message_id: int, _=Depends(require_user)):
    """手动转存：把消息预览里的某条消息重新走一遍处理管道。

    - 清除该消息的去重记忆后重放（否则会被消息级去重直接跳过）
    - bypass=True：跳过消息级黑名单（用户明确点了按钮）；分享内容黑名单仍生效
    - 链接重复仍按 duplicate 留痕，不会重复转存
    """
    ch = db.query("SELECT * FROM channels WHERE id=?", (ch_id,), one=True)
    if not ch:
        raise HTTPException(404, "频道不存在")
    msg = db.query("SELECT * FROM messages WHERE channel=? AND message_id=?",
                   (ch["name"], message_id), one=True)
    if not msg:
        raise HTTPException(404, "消息不存在（可能已被清理）")
    db.execute("DELETE FROM messages WHERE channel=? AND message_id=?", (ch["name"], message_id))
    result = pipeline.process_message(ch["name"], message_id, msg["text"] or "", bypass=True)
    logstream.info("channels",
                   f"手动转存消息 #{message_id}（@{ch['name']}）：入队 {result.get('queued', 0)}，"
                   f"重复 {result.get('duplicates', 0)}")
    return {"ok": True, "result": result}


# ---------- subscriptions ----------
@router.get("/subscriptions")
def list_subscriptions(_=Depends(require_user)):
    return {"items": [dict(r) for r in db.query("SELECT * FROM subscriptions ORDER BY id DESC")],
            "subscription_only": bool(db.get_setting("subscription_only", False))}


@router.post("/subscriptions")
def add_subscription(body: dict, _=Depends(require_user)):
    kw = (body.get("keyword") or "").strip()
    if not kw:
        raise HTTPException(400, "关键词不能为空")
    if len(kw) > 100:
        raise HTTPException(400, "关键词最长 100 字符")
    if db.query("SELECT 1 FROM subscriptions WHERE keyword=?", (kw,), one=True):
        raise HTTPException(400, f"订阅「{kw}」已存在")
    db.execute("INSERT INTO subscriptions(keyword,note,created_at) VALUES(?,?,?)",
               (kw, (body.get("note") or "")[:200], time.time()))
    logstream.info("subscribe", f"添加追剧订阅: {kw}")
    return {"ok": True}


@router.put("/subscriptions/{sub_id}")
def update_subscription(sub_id: int, body: dict, _=Depends(require_user)):
    sub = db.query("SELECT id FROM subscriptions WHERE id=?", (sub_id,), one=True)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    if "enabled" in body:
        db.execute("UPDATE subscriptions SET enabled=? WHERE id=?",
                   (1 if body["enabled"] else 0, sub_id))
    if "keyword" in body:
        kw = (str(body["keyword"]) or "").strip()
        if not kw:
            raise HTTPException(400, "关键词不能为空")
        dup = db.query("SELECT id FROM subscriptions WHERE keyword=? AND id<>?", (kw, sub_id), one=True)
        if dup:
            raise HTTPException(400, f"订阅「{kw}」已存在")
        db.execute("UPDATE subscriptions SET keyword=? WHERE id=?", (kw[:100], sub_id))
    # 进度重置：清除追更记录，下次命中重新通知
    if body.get("reset_progress"):
        db.execute("UPDATE subscriptions SET last_ep='', last_title='', last_match_at=NULL WHERE id=?",
                   (sub_id,))
    return {"ok": True}


@router.delete("/subscriptions/{sub_id}")
def delete_subscription(sub_id: int, _=Depends(require_user)):
    row = db.query("SELECT keyword FROM subscriptions WHERE id=?", (sub_id,), one=True)
    db.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))
    if row:
        logstream.info("subscribe", f"删除追剧订阅: {row['keyword']}")
    return {"ok": True}


# ---------- tmdb 选剧搜索（订阅页） ----------
_TMDB_IMG_BASE = "https://image.tmdb.org/t/p"


def _tmdb_lang() -> str:
    return str(db.get_setting("tmdb_language", "zh-CN") or "zh-CN") or "zh-CN"


def _tmdb_get(path: str, params: dict, timeout: float = 10):
    """带代理请求 TMDB，返回响应对象；调用方自行处理异常。"""
    import httpx
    proxy = None
    try:
        proxy = jobs._tg_proxy()
    except Exception:
        pass
    return httpx.get(f"https://api.themoviedb.org/3{path}",
                     params=params, proxy=proxy, timeout=timeout)


def _tmdb_item(it: dict, kind: str) -> dict | None:
    """把 TMDB 的 tv/movie 条目统一成订阅页卡片数据。"""
    name = it.get("name") or it.get("title") or ""
    if not name:
        return None
    date = it.get("first_air_date") or it.get("release_date") or ""
    poster_path = it.get("poster_path") or ""
    return {
        "id": it.get("id"), "kind": kind, "name": name,
        "original": it.get("original_name") or it.get("original_title") or "",
        "year": date[:4] if re.match(r"^(19|20)\d{2}", date) else "",
        "date": date,
        "overview": (it.get("overview") or "").strip()[:200],
        "rating": round(float(it.get("vote_average") or 0), 1),
        "poster": f"{_TMDB_IMG_BASE}/w200{poster_path}" if poster_path else "",
        "poster_path": poster_path,
        "popularity": float(it.get("popularity") or 0),
    }


# TV 详情（季列表）缓存：季播剧的最新季基本不变，进程内缓存即可
_TV_SEASON_CACHE: dict[int, tuple[int, str] | None] = {}


def _latest_season_of(tv_id, api_key: str, lang: str) -> tuple[int, str] | None:
    """取剧集「最新一季」的季号与播出日期（季号≥1 且有 air_date 的最大季）。

    季播剧（如已经出到 S04 的长寿剧）在列表接口里只有 1999 年的首播日期，
    对追剧没有参考意义；这里用详情接口的 seasons 覆盖为最新季的数据。
    """
    try:
        tv_id = int(tv_id)
    except (TypeError, ValueError):
        return None
    if tv_id in _TV_SEASON_CACHE:
        return _TV_SEASON_CACHE[tv_id]
    best = None
    try:
        resp = _tmdb_get(f"/tv/{tv_id}", {"api_key": api_key, "language": lang})
        if resp.status_code == 200:
            for s in (resp.json().get("seasons") or []):
                try:
                    sn = int(s.get("season_number") or 0)
                except (TypeError, ValueError):
                    continue
                ad = str(s.get("air_date") or "").strip()
                if sn >= 1 and ad and (best is None or sn > best[0]):
                    best = (sn, ad)
    except Exception:
        pass
    _TV_SEASON_CACHE[tv_id] = best
    return best


def _enrich_tv_latest_season(items: list[dict], api_key: str, lang: str) -> list[dict]:
    """把 TV 条目的播出时间替换为最新季的，并附上季号标签（S04）。"""
    tv_items = [it for it in items if it.get("kind") == "tv"]
    if not tv_items:
        return items
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        infos = list(ex.map(lambda it: _latest_season_of(it.get("id"), api_key, lang),
                            tv_items))
    for it, info in zip(tv_items, infos):
        if info:
            it["date"] = info[1]
            it["season"] = f"S{info[0]:02d}"
    return items


@router.get("/tmdb/discover")
def tmdb_discover(kind: str = "on_air", page: int = 1, _=Depends(require_user)):
    """追剧选剧分类浏览：按「追剧日历」视角组织，电影与剧集各自对齐正确概念。

    kind:
      on_air  — 📺 正在播出：剧集=TMDB /tv/on_the_air（未来 7 天内有更新、在播）；
                电影=/movie/now_playing（正在影院热映）——电影没有「播出」概念
      upcoming— 📅 即将上线：电影=/movie/upcoming（已定档待映）；
                剧集=discover 首播在今天~未来 90 天内的新剧/新一季，按开播日期升序（日历感）
      anime   — 🎌 动漫新番（动画类目 + 最近 45 天内开播/更新的剧）
      doc     — 🌍 纪录片（纪录片类目 + 最近 90 天内开播/更新的剧）
    """
    import datetime

    api_key = str(db.get_setting("tmdb_api_key", "") or "").strip()
    if not api_key:
        raise HTTPException(400, "请先填写并保存 TMDB API Key")
    page = min(max(page, 1), 500)
    today = datetime.date.today()
    lang = _tmdb_lang()

    items: list[dict] = []
    total_pages = 1

    def _collect(path: str, params: dict, k: str) -> None:
        nonlocal total_pages
        try:
            resp = _tmdb_get(path, params)
        except Exception as e:
            logstream.warn("system", f"TMDB {path} 请求失败: {logstream.exc(e)}")
            return
        if resp.status_code != 200:
            logstream.warn("system", f"TMDB {path} 返回 {resp.status_code}: {str(resp.text)[:120]}")
            return
        data = resp.json()
        total_pages = max(total_pages, min(int(data.get("total_pages") or 1), 500))
        items.extend(x for x in (_tmdb_item(it, k)
                                 for it in (data.get("results") or [])) if x)

    if kind == "on_air":
        # 剧集：在更剧；电影：正在热映（两个世界的同一档位，保证类型筛选双方都有数据）
        _collect("/tv/on_the_air", {"api_key": api_key, "language": lang,
                                    "page": page}, "tv")
        _collect("/movie/now_playing", {"api_key": api_key, "language": lang,
                                        "page": page}, "movie")
    elif kind == "upcoming":
        # 电影：已定档待映；剧集：已定档新剧/新一季（今天~90 天内开播）
        _collect("/movie/upcoming", {"api_key": api_key, "language": lang,
                                     "page": page}, "movie")
        _collect("/discover/tv", {"api_key": api_key, "language": lang, "page": page,
                                  "sort_by": "first_air_date.asc", "include_adult": "false",
                                  "first_air_date.gte": today.isoformat(),
                                  "first_air_date.lte": (today + datetime.timedelta(days=90)).isoformat()},
                 "tv")
    elif kind == "anime":
        _collect("/discover/tv", {"api_key": api_key, "language": lang, "page": page,
                                  "sort_by": "popularity.desc", "include_adult": "false",
                                  "with_genres": "16",
                                  "air_date.gte": (today - datetime.timedelta(days=45)).isoformat()},
                 "tv")
    elif kind == "doc":
        _collect("/discover/tv", {"api_key": api_key, "language": lang, "page": page,
                                  "sort_by": "popularity.desc", "include_adult": "false",
                                  "with_genres": "99",
                                  "air_date.gte": (today - datetime.timedelta(days=90)).isoformat()},
                 "tv")
    else:
        raise HTTPException(400, f"未知分类: {kind}")
    if not items:
        raise HTTPException(502, "TMDB 拉取失败，请检查 API Key 或网络/代理设置")
    if kind == "upcoming":
        # 即将上线按开播/上映日期升序：最接近今天的排最前，符合追剧日历视角
        items.sort(key=lambda x: x.get("date") or "9999-12-31")
    else:
        items.sort(key=lambda x: -x["popularity"])
    # TV 条目改用最新季的播出时间 + 季号标签（季播剧首播日期没有参考意义）
    items = _enrich_tv_latest_season(items, api_key, lang)
    # 合并类目含电影+剧集两个来源，返回双份单页（各 20 条），保证类型筛选双方都有数据
    return {"items": items[:40], "page": page, "total_pages": total_pages}


@router.get("/tmdb/search")
def tmdb_search(query: str = "", kind: str = "all", page: int = 1, _=Depends(require_user)):
    """按名称搜索 TMDB 影视（tv/movie），返回海报、年份、简介等供订阅选剧。"""
    api_key = str(db.get_setting("tmdb_api_key", "") or "").strip()
    if not api_key:
        raise HTTPException(400, "请先在上方填写并保存 TMDB API Key")
    query = query.strip()
    if len(query) < 2:
        raise HTTPException(400, "搜索词至少 2 个字符")
    page = page if page >= 1 else 1
    kinds = ("tv", "movie") if kind not in ("tv", "movie") else (kind,)
    items = []
    for k in kinds:
        try:
            resp = _tmdb_get(f"/search/{k}", {"api_key": api_key, "query": query,
                                              "language": _tmdb_lang(), "page": page})
            if resp.status_code != 200:
                continue
            items += [x for x in (_tmdb_item(it, k) for it in (resp.json().get("results") or [])) if x]
        except Exception:
            continue  # 网络异常时跳过该类型，另一个类型可能仍可用
    items.sort(key=lambda x: -x["popularity"])
    # TV 条目同样标注最新季（季号 + 最新季播出时间），与分类浏览口径一致
    items = _enrich_tv_latest_season(items, api_key, _tmdb_lang())
    return {"items": items[:24]}


@router.get("/tmdb/poster")
def tmdb_poster(path: str = "", token: str = "",
                authorization: str = Header(default="")):
    """后端代理 TMDB 海报图片（大陆无法直连 image.tmdb.org，走 TG 代理）。

    浏览器 <img> 标签无法带 Authorization 头，因此额外允许 ?token= 鉴权。
    """
    from fastapi.responses import Response
    user = ""
    if authorization.startswith("Bearer "):
        user = auth.check_token(authorization[7:]) or ""
    if not user and token:
        user = auth.check_token(token) or ""
    if not user:
        raise HTTPException(401, "登录已过期，请重新登录")
    if not re.match(r"^/[A-Za-z0-9._-]+(\.jpg|\.png|\.jpeg|\.webp)$", path):
        raise HTTPException(400, "海报路径不合法")
    proxy = None
    try:
        proxy = jobs._tg_proxy()
    except Exception:
        pass
    try:
        import httpx
        resp = httpx.get(f"{_TMDB_IMG_BASE}/w200{path}", proxy=proxy, timeout=15)
        if resp.status_code != 200:
            raise HTTPException(404, "海报获取失败")
        return Response(content=resp.content, media_type=resp.headers.get(
            "content-type", "image/jpeg"),
            headers={"Cache-Control": "public, max-age=604800"})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"海报获取失败: {str(e)[:120]}")


# ---------- version ----------
@router.get("/version")
def version(_=Depends(require_user)):
    from .. import config
    return {"version": config.APP_VERSION}


# ---------- rules & routes ----------
@router.get("/rules")
def get_rules(_=Depends(require_user)):
    return {
        "rules": [dict(r) for r in db.query("SELECT * FROM rules ORDER BY priority, id")],
        "routes": [dict(r) for r in db.query("SELECT * FROM routes ORDER BY category")],
        "unclassified_action": db.get_setting("unclassified_action", "skip"),
    }


@router.put("/rules")
def save_rules(body: dict, _=Depends(require_user)):
    """保存规则与目录映射。

    - 只有请求里带 `rules` 时才替换规则（前端「目录映射」保存只传 routes，不能清空规则）
    - 带 `routes` 时按提交的完整列表同步：未提交的分类会被删除（支持删除分类映射）
    """
    saved_rules = saved_routes = None

    if "rules" in body:
        rules = body.get("rules") or []
        if not isinstance(rules, list):
            raise HTTPException(400, "rules 必须是数组")
        cleaned = []
        for i, r in enumerate(rules):
            if not isinstance(r, dict):
                raise HTTPException(400, f"第 {i + 1} 条规则格式不正确")
            pattern = str(r.get("pattern") or "").strip()
            category = str(r.get("category") or "").strip()
            if not pattern or not category:
                raise HTTPException(400, f"第 {i + 1} 条规则的正则和分类不能为空")
            try:
                re.compile(pattern)
            except re.error as e:
                raise HTTPException(400, f"第 {i + 1} 条规则的正则无效（{pattern}）: {e}")
            try:
                priority = int(r.get("priority", i + 1))
            except (TypeError, ValueError):
                raise HTTPException(400, f"第 {i + 1} 条规则的优先级必须是数字")
            cleaned.append((pattern, category, priority, 1 if r.get("enabled", 1) else 0))
        db.execute("DELETE FROM rules")
        for item in cleaned:
            db.execute("INSERT INTO rules(pattern,category,priority,enabled) VALUES(?,?,?,?)", item)
        saved_rules = len(cleaned)

    if "routes" in body:
        routes = body.get("routes") or []
        if not isinstance(routes, list):
            raise HTTPException(400, "routes 必须是数组")
        categories = set()
        for i, r in enumerate(routes):
            if not isinstance(r, dict):
                raise HTTPException(400, f"第 {i + 1} 个分类映射格式不正确")
            category = str(r.get("category") or "").strip()
            target = str(r.get("target_dir") or "").strip()
            offline = str(r.get("offline_dir") or "").strip()
            if not category:
                raise HTTPException(400, f"第 {i + 1} 个分类映射的分类名不能为空")
            if not target:
                raise HTTPException(400, f"分类「{category}」的分享转存目录不能为空（可填 skip）")
            categories.add(category)
            db.execute(
                "INSERT INTO routes(category,target_dir,offline_dir) VALUES(?,?,?)"
                " ON CONFLICT(category) DO UPDATE SET target_dir=excluded.target_dir,"
                " offline_dir=excluded.offline_dir",
                (category, target, offline))
        # 全量同步：删除本次未提交的分类映射
        for row in db.query("SELECT category FROM routes"):
            if row["category"] not in categories:
                db.execute("DELETE FROM routes WHERE category=?", (row["category"],))
        saved_routes = len(categories)

    if "unclassified_action" in body:
        db.set_setting("unclassified_action", body["unclassified_action"])

    classifier.invalidate()
    parts = []
    if saved_rules is not None:
        parts.append(f"{saved_rules} 条规则")
    if saved_routes is not None:
        parts.append(f"{saved_routes} 个分类映射")
    logstream.info("rules", f"分类规则/目录映射已更新（{'、'.join(parts) or '无变更'}）")
    return {"ok": True}


@router.post("/rules/reset")
def reset_rules(_=Depends(require_user)):
    """重置为最新内置规则集；缺失的默认分类目录映射自动补齐（不覆盖已有映射）。"""
    from ..database import DEFAULT_RULES, DEFAULT_ROUTES
    db.execute("DELETE FROM rules")
    for pat, cat, pri in DEFAULT_RULES:
        db.execute("INSERT INTO rules(pattern,category,priority) VALUES(?,?,?)", (pat, cat, pri))
    for cat, d in DEFAULT_ROUTES:
        db.execute("INSERT OR IGNORE INTO routes(category,target_dir,offline_dir) VALUES(?,?,'')", (cat, d))
    classifier.invalidate()
    logstream.info("rules", f"已恢复默认规则（{len(DEFAULT_RULES)} 条），缺失的默认分类目录已补齐")
    return {"ok": True, "count": len(DEFAULT_RULES)}


@router.post("/rules/test")
def test_rule(body: dict, _=Depends(require_user)):
    # 截断超长输入：规则测试是同步正则执行，超大文本有拖慢接口的风险
    text = str(body.get("text", ""))[:2000]
    return classifier.test_text(text)


# ---------- blacklist ----------
# 注意：固定路径（hits/restore-defaults）必须注册在 /{btype} 之前，否则会被通配匹配
@router.get("/blacklist/hits")
def hits(_=Depends(require_user)):
    return [dict(r) for r in db.query("SELECT * FROM blacklist_hits ORDER BY id DESC LIMIT 200")]


@router.post("/blacklist/hits/{hit_id}/release")
async def release_hit(hit_id: int, _=Depends(require_user)):
    """误杀放行：忽略黑名单重新处理。share_info 环节的命中给对应任务打免检标记重新入队。"""
    hit = db.query("SELECT * FROM blacklist_hits WHERE id=?", (hit_id,), one=True)
    if not hit:
        raise HTTPException(404, "记录不存在")
    if hit["stage"] == "share_info" and hit["share_code"]:
        changed = db.execute(
            "UPDATE transfers SET bypass_filters=1, status='queued', attempts=0, error='',"
            " next_retry_at=NULL WHERE share_code=? AND status='blocked'",
            (hit["share_code"],))
        db.execute("DELETE FROM blacklist_hits WHERE id=?", (hit_id,))
        if changed:
            return {"ok": True, "result": {"requeued": True}}
    # 消息环节（或无对应任务）：删除消息记录后带 bypass 重走管道
    db.execute("DELETE FROM messages WHERE channel=? AND message_id=?", (hit["channel"], hit["message_id"]))
    db.execute("DELETE FROM blacklist_hits WHERE id=?", (hit_id,))
    result = pipeline.process_message(hit["channel"], hit["message_id"], hit["text"], bypass=True)
    logstream.info("blacklist",
                   f"误杀放行: 消息 #{hit['message_id']}（{hit['rule_type']}:{hit['rule_value']}）已忽略黑名单重新处理")
    return {"ok": True, "result": result}


@router.post("/blacklist/restore-defaults")
def restore_blacklist_defaults(_=Depends(require_user)):
    """补齐缺失的默认关键词（不删除、不覆盖用户自定义条目）。"""
    from ..database import DEFAULT_BLACKLIST_KEYWORDS
    added = 0
    for kw in DEFAULT_BLACKLIST_KEYWORDS:
        added += db.execute(
            "INSERT OR IGNORE INTO blacklist(type,value,note,created_at) VALUES('keyword',?,'',?)",
            (kw, time.time()))
    filters.invalidate()
    logstream.info("blacklist", f"已补齐默认关键词黑名单（新增 {added} 条）")
    return {"ok": True, "added": added, "total": len(DEFAULT_BLACKLIST_KEYWORDS)}


@router.get("/blacklist/{btype}")
def get_blacklist(btype: str, _=Depends(require_user)):
    if btype not in ("channel", "keyword", "file_pattern"):
        raise HTTPException(404, "未知类型")
    return [dict(r) for r in db.query(
        "SELECT * FROM blacklist WHERE type=? ORDER BY id DESC", (btype,))]


@router.post("/blacklist/{btype}")
def add_blacklist(btype: str, body: dict, _=Depends(require_user)):
    if btype not in ("channel", "keyword", "file_pattern"):
        raise HTTPException(404, "未知类型")
    value = (body.get("value") or "").strip()
    if not value:
        raise HTTPException(400, "内容不能为空")
    lines = [ln.strip() for ln in value.splitlines() if ln.strip()]
    if len(lines) > 200:
        raise HTTPException(400, "单次最多添加 200 条，请分批操作")
    # 支持批量（每行一条）
    for line in lines:
        db.execute("INSERT OR IGNORE INTO blacklist(type,value,note,created_at) VALUES(?,?,?,?)",
                   (btype, line[:500], (body.get("note") or "")[:200], time.time()))
    label = {"keyword": "关键词", "channel": "频道", "file_pattern": "文件名正则"}[btype]
    filters.invalidate()
    logstream.info("blacklist", f"添加{label}黑名单: {lines[0][:40]}"
                                f"{' 等' if len(lines) > 1 else ''}")
    return {"ok": True}


@router.delete("/blacklist/{btype}/{item_id}")
def del_blacklist(btype: str, item_id: int, _=Depends(require_user)):
    row = db.query("SELECT value FROM blacklist WHERE id=? AND type=?", (item_id, btype), one=True)
    db.execute("DELETE FROM blacklist WHERE id=? AND type=?", (item_id, btype))
    filters.invalidate()
    if row:
        logstream.info("blacklist", f"删除黑名单条目: {row['value'][:40]}")
    return {"ok": True}


# ---------- transfers ----------
def _clamp_page(page: int, size: int, max_size: int) -> tuple[int, int]:
    """分页参数收紧：size 设上限防一次性拖全表（DoS/内存），page 至少为 1。"""
    return max(page, 1), max(1, min(size, max_size))


@router.get("/transfers")
def list_transfers(status: str = "", category: str = "", search: str = "",
                   page: int = 1, size: int = 30, _=Depends(require_user)):
    page, size = _clamp_page(page, size, 200)
    where, params = ["1=1"], []
    if status:
        where.append("status=?"); params.append(status)
    if category:
        where.append("category=?"); params.append(category)
    if search:
        where.append("(title LIKE ? OR share_code LIKE ?)"); params += [f"%{search}%", f"%{search}%"]
    w = " AND ".join(where)
    total = db.query(f"SELECT count(*) c FROM transfers WHERE {w}", tuple(params), one=True)["c"]
    rows = db.query(
        f"SELECT * FROM transfers WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(params + [size, (page - 1) * size]))
    return {"total": total, "items": [dict(r) for r in rows]}


@router.post("/transfers/{t_id}/retry")
def retry_transfer(t_id: int, _=Depends(require_user)):
    row = db.query("SELECT status FROM transfers WHERE id=?", (t_id,), one=True)
    if not row:
        raise HTTPException(404, "记录不存在")
    # 成功/已提交下载/正在转存的任务不能重试，否则会重复转存
    if row["status"] in ("success", "submitted", "running"):
        label = {"success": "已成功", "submitted": "已提交云下载", "running": "正在转存"}[row["status"]]
        raise HTTPException(400, f"任务{label}，不能重复重试")
    db.execute("UPDATE transfers SET status='queued', attempts=0, error='', next_retry_at=NULL WHERE id=?", (t_id,))
    logstream.info("transfer", f"手动重试任务 #{t_id}（原状态: {row['status']}）")
    return {"ok": True}


@router.post("/transfers/retry-failed")
def retry_all_failed(_=Depends(require_user)):
    """批量重试全部失败任务。"""
    n = db.execute("UPDATE transfers SET status='queued', attempts=0, error='', next_retry_at=NULL"
                   " WHERE status='failed'")
    logstream.info("transfer", f"批量重试 {n} 个失败任务")
    return {"ok": True, "count": n}


@router.post("/transfers/batch-delete")
def batch_delete_transfers(body: dict, _=Depends(require_user)):
    """批量删除任务记录（正在转存/已成功的不可删）。"""
    raw = body.get("ids") if isinstance(body, dict) else None
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "请选择要删除的记录")
    ids = []
    for x in raw[:500]:  # 单次上限，避免超大 SQL
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    if not ids:
        raise HTTPException(400, "记录 id 无效")
    ids = list(dict.fromkeys(ids))
    rows = db.query(
        f"SELECT id, status FROM transfers WHERE id IN ({','.join('?' * len(ids))})",
        tuple(ids))
    deletable = [r["id"] for r in rows if r["status"] not in ("running", "success")]
    skipped = len(ids) - len(deletable)  # 含不存在/正在转存/已成功
    n = 0
    if deletable:
        n = db.execute(
            f"DELETE FROM transfers WHERE id IN ({','.join('?' * len(deletable))})",
            tuple(deletable))
        logstream.info("transfer", f"批量删除 {n} 条任务记录（跳过 {skipped} 条不可删除记录）")
    return {"ok": True, "deleted": n, "skipped": skipped}


@router.delete("/transfers/{t_id}")
def cancel_transfer(t_id: int, _=Depends(require_user)):
    row = db.query("SELECT status FROM transfers WHERE id=?", (t_id,), one=True)
    if not row:
        raise HTTPException(404, "记录不存在")
    # 与前端删除按钮保持一致：可删除排队/已提交跟踪/失败/跳过/拦截的记录；
    # 正在转存或已成功的记录不允许删除，避免误删正在使用/已完成的数据。
    if row["status"] in ("running", "success"):
        label = {"running": "正在转存", "success": "已成功"}[row["status"]]
        raise HTTPException(400, f"任务{label}，不能删除")
    db.execute("DELETE FROM transfers WHERE id=?", (t_id,))
    logstream.info("transfer", f"已删除任务记录 #{t_id}（原状态: {row['status']}）")
    return {"ok": True}


# ---------- settings ----------
_INT_SETTINGS = {
    # 轮询/转存间隔下限按风控保守值设定：115 转存类操作过快会触发风控，
    # TG 网页轮询过快也易被限流，因此分别限制在 15/30 秒以上
    "poll_interval_min": (15, 3600),
    "poll_interval_max": (15, 3600),
    "transfer_interval_min": (30, 1800),
    "transfer_interval_max": (30, 1800),
    # 115 单账号每日转存安全上限：1~50（0 不再表示「不限制」）
    "daily_limit": (1, 50),
    "tg_api_id": (0, 2 ** 31 - 1),
    "auto_backup_days": (1, 365),
}
_BOOL_SETTINGS = {"enable_share_transfer", "enable_offline_download",
                  "notify_transfer_success", "schedule_enabled",
                  "subscription_only", "dedup_by_name", "organize_enabled",
                  "digest_enabled", "auto_backup_enabled"}


def _norm_setting(k: str, v):
    """归一化设置值；非法输入抛 400，避免脏配置让后台任务崩溃。"""
    if k == "tg_api_id":
        # Userbot 未配置时该值为空串：Settings 页会原样回传，必须允许空值，
        # 否则「保存设置」会被误判为非法整数而整体失败；非空值仍走下方整数范围校验。
        if v is None or (isinstance(v, str) and not v.strip()):
            return ""
    if k in _BOOL_SETTINGS:
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)
    if k in _INT_SETTINGS:
        lo, hi = _INT_SETTINGS[k]
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{k} 必须是整数")
        return max(lo, min(hi, n))
    if k == "min_size_gb":
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise HTTPException(400, "min_size_gb 必须是数字")
        return max(0.0, min(1000.0, f))
    if k == "retry_backoff":
        if not isinstance(v, (list, tuple)) or not v:
            raise HTTPException(400, "重试退避至少需要填写一个大于 0 的秒数")
        vals = []
        for item in v:
            try:
                n = int(float(item))
            except (TypeError, ValueError):
                raise HTTPException(400, "重试退避只能包含数字（秒）")
            # 重试间隔下限 30 秒（前端只提供预设档位，此处兜底防过密重试触发风控）
            if n < 30:
                raise HTTPException(400, "重试退避的秒数不能小于 30（避免过密重试触发风控）")
            vals.append(min(n, 86400))
        return vals[:10]
    if k in ("schedule_start", "schedule_end", "digest_time"):
        s = str(v).strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s):
            raise HTTPException(400, f"{k} 需为 HH:MM 格式（如 08:30）")
        return s
    if k == "tg_mode":
        s = str(v).strip()
        if s not in ("polling", "userbot", "both"):
            raise HTTPException(400, "tg_mode 仅支持 polling / userbot / both")
        return s
    if k == "unclassified_action":
        return str(v).strip() or "skip"
    if v is None:
        return ""
    return v if isinstance(v, str) else str(v)


@router.get("/settings")
def get_settings(_=Depends(require_user)):
    keys = ["tg_mode", "enable_share_transfer", "enable_offline_download",
            "poll_interval_min", "poll_interval_max", "transfer_interval_min",
            "transfer_interval_max", "daily_limit", "retry_backoff", "min_size_gb",
            "unclassified_action", "notify_token", "notify_chat_id",
            "notify_transfer_success", "proxy",
            "schedule_enabled", "schedule_start", "schedule_end",
            "tg_api_id", "tg_api_hash", "tg_phone",
            "subscription_only", "dedup_by_name", "organize_enabled",
            "tmdb_api_key",
            "bark_url", "pushplus_token", "pushplus_topic", "ntfy_url", "ntfy_token",
            "digest_enabled", "digest_time",
            "auto_backup_enabled", "auto_backup_days",
            "api_token"]
    # 未配置过的键必须回代码默认值而非 null：前端会把 null 当「关」显示，
    # 且保存时 bool(null)=false 落库，把默认开启的功能（如每日摘要）悄悄关掉
    defaults = {"enable_share_transfer": True, "enable_offline_download": True,
                "notify_transfer_success": True, "schedule_enabled": False,
                "schedule_start": "22:00", "schedule_end": "08:00",
                "subscription_only": False, "dedup_by_name": True,
                "organize_enabled": False,
                "digest_enabled": True, "digest_time": "21:00",
                "auto_backup_enabled": False, "auto_backup_days": 7}
    return {k: db.get_setting(k, defaults.get(k)) for k in keys}


@router.put("/settings")
def put_settings(body: dict, _=Depends(require_user)):
    allowed = {"tg_mode", "enable_share_transfer", "enable_offline_download",
               "poll_interval_min", "poll_interval_max", "transfer_interval_min",
               "transfer_interval_max", "daily_limit", "retry_backoff", "min_size_gb",
               "unclassified_action", "notify_token", "notify_chat_id",
               "notify_transfer_success", "proxy",
               "schedule_enabled", "schedule_start", "schedule_end",
               "tg_api_id", "tg_api_hash", "tg_phone",
               "subscription_only", "dedup_by_name", "organize_enabled",
               "tmdb_api_key",
               "bark_url", "pushplus_token", "pushplus_topic", "ntfy_url", "ntfy_token",
               "digest_enabled", "digest_time",
               "auto_backup_enabled", "auto_backup_days",
               "api_token"}
    # 先整体校验再落库：任一字段非法时不写入，避免半新半旧配置。
    # null 表示「前端从未配置过该键」，跳过不写，防止 bool(null)=false 把默认开启的功能关掉
    updates = {k: _norm_setting(k, v) for k, v in body.items()
               if k in allowed and v is not None}
    # 间隔成对提交时保证 min <= max
    for lo_k, hi_k in (("poll_interval_min", "poll_interval_max"),
                       ("transfer_interval_min", "transfer_interval_max")):
        if lo_k in updates and hi_k in updates and updates[lo_k] > updates[hi_k]:
            updates[lo_k], updates[hi_k] = updates[hi_k], updates[lo_k]
    for k, v in updates.items():
        db.set_setting(k, v)
    logstream.info("system", f"系统设置已修改: {', '.join(updates) if updates else '（无变更）'}")
    return {"ok": True}


@router.post("/settings/proxy/test")
def test_proxy(body: dict = None, _=Depends(require_user)):
    """测试 TG 代理连通性（用输入框当前值，未保存也可直接测）。"""
    body = body or {}
    proxy = body.get("proxy") or db.get_setting("proxy", "")
    ok, detail = jobs.test_proxy(proxy)
    if not ok:
        raise HTTPException(400, detail)
    return {"ok": True, "detail": detail}


@router.post("/settings/notify/test")
def test_notify(body: dict = None, _=Depends(require_user)):
    """发送测试通知。优先用传入的 Token/Chat ID（未保存也可直接测），否则用已保存配置。"""
    body = body or {}
    token = body.get("token") or db.get_setting("notify_token", "")
    chat_id = body.get("chat_id") or db.get_setting("notify_chat_id", "")
    ok, detail = jobs.send_test(token, chat_id)
    if not ok:
        raise HTTPException(400, detail)
    return {"ok": True}


@router.post("/settings/notify/test/{channel}")
def test_channel(channel: str, _=Depends(require_user)):
    """按渠道发送测试通知（bark / pushplus / ntfy），用已保存的配置。

    返回 ok + 说明；失败时说明里含未配置提示或失败原因，前端直接展示。
    """
    ok, detail = jobs.test_channel(channel)
    if not ok:
        raise HTTPException(400, detail)
    return {"ok": True, "detail": detail}


@router.post("/settings/digest/test")
def digest_test(_=Depends(require_user)):
    """立即推送一次每日摘要（内容与定时推送完全一致），用于验证摘要功能与通知渠道。"""
    detail = jobs.notify_detail(jobs.digest_text())
    ok = any(detail.values())
    logstream.info("system", "已手动推送每日摘要（测试）" if ok else
                   "手动推送每日摘要失败：所有通知渠道都未成功（未配置或发送失败）")
    return {"ok": ok, "detail": detail}


# ---------- logs ----------
@router.get("/logs/days")
def log_days(_=Depends(require_user)):
    """最近 7 天按天统计（天、总数、错误数、警告数）。"""
    rows = db.query(
        "SELECT date(created_at,'unixepoch','localtime') d, count(*) c,"
        " sum(CASE WHEN level='error' THEN 1 ELSE 0 END) err,"
        " sum(CASE WHEN level='warn' THEN 1 ELSE 0 END) warn"
        " FROM logs GROUP BY d ORDER BY d DESC LIMIT 7")
    return [dict(r) for r in rows]


@router.get("/logs/modules")
def log_modules(_=Depends(require_user)):
    rows = db.query("SELECT DISTINCT module FROM logs ORDER BY module")
    return [r["module"] for r in rows if r["module"]]


@router.get("/logs")
def get_logs(date: str = "", level: str = "", module: str = "", search: str = "",
             page: int = 1, size: int = 100, _=Depends(require_user)):
    """历史日志查询：按天（服务器本地时区）/级别/模块/关键词筛选，最多回溯 7 天。"""
    from datetime import datetime, timedelta
    page, size = _clamp_page(page, size, 500)
    where, params = ["1=1"], []
    if date:
        try:
            start = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "日期格式应为 YYYY-MM-DD")
        where += ["created_at>=?", "created_at<?"]
        params += [start.timestamp(), (start + timedelta(days=1)).timestamp()]
    else:
        where.append("created_at>=?")
        params.append(time.time() - 7 * 86400)
    if level:
        where.append("level=?"); params.append(level)
    if module:
        where.append("module=?"); params.append(module)
    if search:
        where.append("message LIKE ?"); params.append(f"%{search}%")
    w = " AND ".join(where)
    total = db.query(f"SELECT count(*) c FROM logs WHERE {w}", tuple(params), one=True)["c"]
    rows = db.query(f"SELECT * FROM logs WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?",
                    tuple(params + [size, (page - 1) * size]))
    return {"total": total, "items": [dict(r) for r in rows]}


@router.get("/logs/export")
def export_logs(date: str = "", _=Depends(require_user)):
    """导出某天（默认最近 7 天）日志为文本文件。"""
    from datetime import datetime, timedelta
    from fastapi.responses import PlainTextResponse
    if date:
        try:
            start = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "日期格式应为 YYYY-MM-DD")
        rows = db.query("SELECT * FROM logs WHERE created_at>=? AND created_at<? ORDER BY id",
                        (start.timestamp(), (start + timedelta(days=1)).timestamp()))
        fname = f"logs_{date}.txt"
    else:
        rows = db.query("SELECT * FROM logs WHERE created_at>=? ORDER BY id",
                        (time.time() - 7 * 86400,))
        fname = "logs_7days.txt"
    lines = [f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['created_at']))}"
             f" [{r['level']}] {r['module']} {r['message']}" for r in rows]
    return PlainTextResponse(
        "\n".join(lines),
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ---------- 备份 ----------
@router.post("/backup/restore")
def backup_restore(body: dict, _=Depends(require_user)):
    """从备份 JSON 恢复数据（整体覆盖）。

    body 即备份文件内容（_meta/settings/channels/...）；恢复在事务内完成，
    失败自动回滚；恢复后系统回到停止状态，需手动点「开始运行」。
    """
    stats = db.restore_payload(body)
    logstream.info("system", f"已从备份恢复数据：设置 {stats['settings']} 项、频道 {stats['channels']}、"
                             f"规则 {stats['rules']}、订阅 {stats['subscriptions']}、转存记录 {stats['transfers']}")
    return {"ok": True, "stats": stats}


@router.get("/backup/export")
def backup_export(_=Depends(require_user)):
    """导出全部数据（设置/频道/规则/黑名单/订阅/转存记录）为 JSON 备份文件。

    与 TG 定时自动备份共用 db.backup_payload，保证两者内容一致。
    """
    import json
    from fastapi.responses import Response
    payload = db.backup_payload()
    content = json.dumps(payload, ensure_ascii=False, indent=1)
    fname = f"autosave_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
    logstream.info("system", "已导出数据备份（JSON）")
    return Response(
        content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/backup/import")
async def backup_import(_=Depends(require_user)):
    """备份导入为 Phase 3 预留：当前版本请直接恢复 data 目录。"""
    raise HTTPException(400, "请直接恢复 data 目录完成导入（备份含 cookie/session，更完整）")
