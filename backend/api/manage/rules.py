"""分类匹配规则、目录映射路由与黑名单管理。"""
import time

from fastapi import APIRouter, Depends, HTTPException

from ... import database as db
from ...core import classifier, filters, logstream, patguard, pipeline
from ..deps import require_user
from ._common import _clean_dir_action

router = APIRouter()


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
    cleaned_rules = None
    cleaned_routes = None

    if "rules" in body:
        rules = body.get("rules") or []
        if not isinstance(rules, list):
            raise HTTPException(400, "rules 必须是数组")
        cleaned_rules = []
        for i, r in enumerate(rules):
            if not isinstance(r, dict):
                raise HTTPException(400, f"第 {i + 1} 条规则格式不正确")
            pattern = str(r.get("pattern") or "").strip()
            category = str(r.get("category") or "").strip()
            if not pattern or not category:
                raise HTTPException(400, f"第 {i + 1} 条规则的正则和分类不能为空")
            err = patguard.validate_pattern(pattern)
            if err:
                raise HTTPException(400, f"第 {i + 1} 条规则：{err}")
            try:
                priority = int(r.get("priority", i + 1))
            except (TypeError, ValueError):
                raise HTTPException(400, f"第 {i + 1} 条规则的优先级必须是数字")
            if priority < 1:
                raise HTTPException(400, f"第 {i + 1} 条规则的优先级必须是大于等于 1 的整数")
            note = str(r.get("note") or "").strip()[:120]
            cleaned_rules.append((pattern, category, priority, 1 if r.get("enabled", 1) else 0, note))

    if "routes" in body:
        routes = body.get("routes") or []
        if not isinstance(routes, list):
            raise HTTPException(400, "routes 必须是数组")
        cleaned_routes = []
        categories = set()
        for i, r in enumerate(routes):
            if not isinstance(r, dict):
                raise HTTPException(400, f"第 {i + 1} 个分类映射格式不正确")
            category = str(r.get("category") or "").strip()
            target = _clean_dir_action(r.get("target_dir") or "")
            raw_off = str(r.get("offline_dir") or "").strip()
            offline = _clean_dir_action(raw_off) if raw_off else ""
            if not category:
                raise HTTPException(400, f"第 {i + 1} 个分类映射的分类名不能为空")
            if not target:
                raise HTTPException(400, f"分类「{category}」的分享转存目录不能为空（可填 skip）")
            categories.add(category)
            cleaned_routes.append((category, target, offline))
        saved_routes = len(categories)

    if cleaned_rules is not None or cleaned_routes is not None:
        # 全部校验通过后同一事务原子提交，防止 rules 成功但 routes 校验失败导致半套状态
        with db.transaction() as con:
            if cleaned_rules is not None:
                con.execute("DELETE FROM rules")
                con.executemany("INSERT INTO rules(pattern,category,priority,enabled,note) VALUES(?,?,?,?,?)",
                                cleaned_rules)
                saved_rules = len(cleaned_rules)
            if cleaned_routes is not None:
                con.execute("DELETE FROM routes")
                con.executemany("INSERT INTO routes(category,target_dir,offline_dir) VALUES(?,?,?)",
                                cleaned_routes)

    if "unclassified_action" in body:
        db.set_setting("unclassified_action", _clean_dir_action(body["unclassified_action"]))

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
    from ...database import DEFAULT_RULES, DEFAULT_ROUTES
    with db.transaction() as con:
        con.execute("DELETE FROM rules")
        con.executemany("INSERT INTO rules(pattern,category,priority) VALUES(?,?,?)",
                        [(pat, cat, pri) for pat, cat, pri in DEFAULT_RULES])
    for cat, d in DEFAULT_ROUTES:
        db.execute("INSERT OR IGNORE INTO routes(category,target_dir,offline_dir) VALUES(?,?,'')", (cat, d))
    classifier.invalidate()
    logstream.info("rules", f"已恢复默认规则（{len(DEFAULT_RULES)} 条），缺失的默认分类目录已补齐")
    return {"ok": True, "count": len(DEFAULT_RULES)}


@router.post("/rules/test")
def test_rule(body: dict, _=Depends(require_user)):
    # 截断超长输入：规则测试是同步正则执行，超大文本有拖慢接口的风险
    text = str(body.get("text", ""))[:2000]
    channel = str(body.get("channel") or "").strip()[:120]
    return classifier.test_text(text, channel=channel or None)


# ---------- blacklist ----------
# 注意：固定路径（hits/restore-defaults）必须注册在 /{btype} 之前，否则会被通配匹配
@router.get("/blacklist/hits")
def hits(_=Depends(require_user)):
    return [dict(r) for r in db.query("SELECT * FROM blacklist_hits ORDER BY id DESC LIMIT 200")]


@router.post("/blacklist/hits/{hit_id}/release")
async def release_hit(hit_id: int, remove_rule: bool = False, _=Depends(require_user)):
    """误杀放行：忽略黑名单重新处理；可选同步从黑名单移除触发拦截的词条。"""
    hit = db.query("SELECT * FROM blacklist_hits WHERE id=?", (hit_id,), one=True)
    if not hit:
        raise HTTPException(404, "记录不存在")
    if remove_rule and hit["rule_type"] in ("keyword", "channel", "file_pattern") and hit["rule_value"]:
        db.execute("DELETE FROM blacklist WHERE type=? AND value=?",
                   (hit["rule_type"], hit["rule_value"]))
    if hit["stage"] == "share_info" and hit["share_code"]:
        # 活动任务唯一：同分享码已有排队/转存中任务时不重复入队（放行记录保留）
        changed = db.execute(
            "UPDATE transfers SET bypass_filters=1, status='queued', attempts=0, error='',"
            " next_retry_at=NULL WHERE share_code=? AND status='blocked'"
            " AND share_code NOT IN (SELECT share_code FROM transfers"
            "                         WHERE status IN ('queued','running','submitted'))",
            (hit["share_code"],))
        db.execute("DELETE FROM blacklist_hits WHERE id=?", (hit_id,))
        if changed:
            return {"ok": True, "result": {"requeued": True}}
    # 消息环节（或无对应任务）：删除消息记录后带 bypass 重走管道
    db.execute("DELETE FROM messages WHERE channel=? AND message_id=?", (hit["channel"], hit["message_id"]))
    db.execute("DELETE FROM blacklist_hits WHERE id=?", (hit_id,))
    result = pipeline.process_message(hit["channel"], hit["message_id"], hit["text"], bypass=True)
    logstream.info("blacklist",
                   f"误拦放行：消息 #{hit['message_id']}（{hit['rule_type']}:{hit['rule_value']}）已忽略黑名单重新处理")
    return {"ok": True, "result": result}


@router.post("/blacklist/restore-defaults")
def restore_blacklist_defaults(_=Depends(require_user)):
    """补齐缺失的默认关键词（不删除、不覆盖用户自定义条目）。"""
    from ...database import DEFAULT_BLACKLIST_KEYWORDS
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
        raise HTTPException(404, "未知黑名单类型")
    return [dict(r) for r in db.query(
        "SELECT * FROM blacklist WHERE type=? ORDER BY id DESC", (btype,))]


@router.post("/blacklist/{btype}")
def add_blacklist(btype: str, body: dict, _=Depends(require_user)):
    if btype not in ("channel", "keyword", "file_pattern"):
        raise HTTPException(404, "未知黑名单类型")
    value = (body.get("value") or "").strip()
    if not value:
        raise HTTPException(400, "内容不能为空")
    lines = [ln.strip() for ln in value.splitlines() if ln.strip()]
    if len(lines) > 200:
        raise HTTPException(400, "单次最多添加 200 条，请分批操作")
    if btype == "file_pattern":  # 文件名正则同样跑在不可信文件名上，保存时做安全校验
        for line in lines:
            err = patguard.validate_pattern(line)
            if err:
                raise HTTPException(400, f"「{line[:60]}」：{err}")
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
