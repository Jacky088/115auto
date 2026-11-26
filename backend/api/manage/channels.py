"""Telegram 监控频道管理、消息预览与手动单条转存路由。"""
import re
import time

from fastapi import APIRouter, Depends, HTTPException

from ... import database as db
from ...core import logstream, pipeline
from ..deps import require_user

router = APIRouter()


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
    # 频道名字符集白名单：名字会内插进 t.me/s/<name> 抓取 URL，含 ?/#/空白 的名字
    # 会污染翻页参数并造成展示与实际抓取不一致
    if not re.fullmatch(r"[0-9A-Za-z_]{1,64}", name):
        raise HTTPException(400, "频道名仅支持字母、数字与下划线（1-64 位）")
    if ctype not in ("polling", "userbot"):
        raise HTTPException(400, "type 必须是 polling 或 userbot")
    if db.query("SELECT 1 FROM channels WHERE name=? COLLATE NOCASE", (name,), one=True):
        # 大小写不敏感查重：@Foo 与 @foo 是同一频道，双链路接入会导致消息重复处理
        raise HTTPException(400, "该频道已存在（同一频道不能同时用轮询与 Userbot 接入，避免重复处理）")
    # 频道数量上限：防止误操作/脚本灌入海量频道拖垮轮询与 115 风控
    n = db.query("SELECT count(*) c FROM channels", one=True)["c"]
    if n >= 50:
        raise HTTPException(400, "监控频道已达上限 50 个，请先清理不需要的频道")

    # 添加前校验（统一入口）：确认该频道当前确实可被对应方式访问
    # （轮询→t.me/s 可打开；Userbot→账号可解析）
    from ...tg.validate import check_channel_access
    res = await check_channel_access(name, ctype)
    if not res.get("ok"):
        raise HTTPException(400, f"无法添加 @{name}：{res.get('err') or '频道不可访问，请核对后重试'}")
    title = str(res.get("title") or "")

    # 订阅模式：网页弹窗必选（与 Bot /add 的 on|off 一致）；未携带 sub_only 的调用方
    # （如初始化向导批量添加）回退继承全局订阅模式设置
    from ...core import subscribe as _submod
    if body.get("sub_only") is None:
        sub_only = 1 if _submod.subscription_only() else 0
    else:
        sub_only = 1 if body.get("sub_only") in (True, 1, "1", "true", "on") else 0
    db.execute("INSERT INTO channels(type,name,sub_only,created_at) VALUES(?,?,?,?)",
               (ctype, name, sub_only, time.time()))
    mode_label = "网页轮询" if ctype == "polling" else "Userbot"
    sub_label = "仅订阅" if sub_only else "全盘转存"
    logstream.info("channels",
                   f"添加监控频道 @{name}（{title}，接入方式：{mode_label}，"
                   f"订阅模式：{sub_label}，已通过访问校验）")
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
        if not ch:
            raise HTTPException(404, "频道不存在")
        raw_en = body["enabled"]
        is_en = 0 if (isinstance(raw_en, str) and raw_en.strip().lower() in ("0", "false", "no", "")) else (1 if raw_en else 0)
        if not is_en:
            # 停用频道时，下级「仅订阅」联动关闭并归零
            db.execute("UPDATE channels SET enabled=0, sub_only=0 WHERE id=?", (ch_id,))
        else:
            db.execute("UPDATE channels SET enabled=1 WHERE id=?", (ch_id,))
        state = "启用" if is_en else "停用"
        logstream.info("channels", f"已{state}监控频道 @{ch['name']}" + ("（订阅模式已联动关闭）" if not is_en else ""))
    if "sub_only" in body:
        ch = db.query("SELECT name FROM channels WHERE id=?", (ch_id,), one=True)
        if not ch:
            raise HTTPException(404, "频道不存在")
        raw_sub = body["sub_only"]
        is_sub = False if (isinstance(raw_sub, str) and raw_sub.strip().lower() in ("0", "false", "no", "")) else bool(raw_sub)
        db.execute("UPDATE channels SET sub_only=? WHERE id=?", (1 if is_sub else 0, ch_id))
        logstream.info("channels",
                       f"频道 @{ch['name']} 订阅模式已{'开启（仅转存命中订阅关键词的内容）' if is_sub else '关闭（全盘转存）'}")
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
    db.execute("DELETE FROM settings WHERE key=?", (f"poll_fail_{ch_id}",))  # 连计数行一起清理，防残留累积
    try:
        from ...tg import backfill as _backfill
        _backfill._backfill_state.pop(ch_id, None)
    except Exception:
        pass
    logstream.info("channels",
                   f"删除监控频道 @{name}：已清除 {n} 条消息去重记录（重新添加将按新频道处理）")
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
        from ...tg import backfill as _backfill
        _backfill._backfill_state.pop(ch_id, None)  # 清掉续补游标，防止从旧断点继续
    except Exception:
        pass
    logstream.info("channels",
                   f"重置监控频道 @{ch['name']}：清除 {n} 条消息去重记录，抓取游标归零，"
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
