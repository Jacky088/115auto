"""消息处理管道：去重 → 黑名单 → 提取链接 → 分类 → 生成转存任务入队。"""
import time

from .. import database as db
from ..core import classifier, extractor, filters, logstream

# 进程内提示标记：每频道只提示一次"无链接"说明，避免刷屏
_runtime_flags: dict[str, bool] = {}


def process_message(channel: str, message_id: int, text: str, bypass: bool = False) -> dict:
    """处理一条 TG 消息，返回处理摘要（供测试与调试）。bypass=True 时跳过消息级黑名单。"""
    result = {"channel": channel, "message_id": message_id, "links": 0, "queued": 0,
              "duplicates": 0, "blocked": False}

    # 1. 消息去重
    cur = db.execute(
        "INSERT OR IGNORE INTO messages(channel,message_id,text,processed_at) VALUES(?,?,?,?)",
        (channel, message_id, (text or "")[:2000], time.time()),
    )
    if cur == 0:
        result["duplicate"] = True
        return result

    # 2. 频道/关键词黑名单
    if not bypass:
        blocked, rule_type, rule_value = filters.check_message(text or "", channel=channel)
        if blocked:
            filters.record_hit(channel, message_id, text, "", rule_type, rule_value, "message")
            logstream.info("pipeline", f"[{channel}] 消息被黑名单拦截（{rule_type}:{rule_value}）")
            result["blocked"] = True
            return result

    # 3. 提取资源链接
    links = extractor.extract(text or "")
    result["links"] = len(links)
    if not links:
        # 可观测性：首条消息说明"为什么没有转存"（其余同类消息不再刷屏）
        key = f"_no_link_logged_{channel}"
        if not _runtime_flags.get(key):
            _runtime_flags[key] = True
            logstream.info(
                "pipeline",
                f"[{channel}] 首批消息未发现可转存的资源链接（115 分享/磁力/电驴/迅雷）——"
                "此类消息会正常记录但不转存；若该频道应包含资源链接，请到「频道管理→消息预览」核对消息原文")
    if not links:
        return result

    # 3.5 功能开关过滤：关闭的类型不入队
    share_on = bool(db.get_setting("enable_share_transfer", True))
    offline_on = bool(db.get_setting("enable_offline_download", True))

    def _enabled(link):
        return offline_on if link.get("kind", "share") == "offline" else share_on

    disabled = [l for l in links if not _enabled(l)]
    if disabled:
        kinds = "云下载" if disabled[0].get("kind") == "offline" else "分享转存"
        logstream.info("pipeline", f"[{channel}] {kinds}功能已关闭，跳过 {len(disabled)} 条链接")
        result["disabled"] = len(disabled)
        links = [l for l in links if _enabled(l)]
        if not links:
            return result

    # 4. 分类（优先采用频道消息自带标签），名称取频道消息首个有效行
    category, matched = classifier.classify(text or "")
    display_title = classifier.headline(text or "") or (text or "")[:300]

    for link in links:
        code = link["share_code"]
        kind = link.get("kind", "share")

        # 按链接类型路由目录（分享/云下载可分别指定）
        target = classifier.resolve_target(category, kind=kind) if category else ""
        if not category:
            action = db.get_setting("unclassified_action", "skip")
            target = "" if action == "skip" else str(action)

        # 5. 分享码去重：已排队/处理中/已成功的不重复入队；
        #    被跳过的重复链接也落一条 duplicate 记录，便于追溯来源频道与时间
        row = db.query(
            "SELECT id, status, channel FROM transfers WHERE share_code=?"
            " AND status IN ('queued','running','submitted','success') ORDER BY id LIMIT 1",
            (code,), one=True,
        )
        if row:
            err = (f"重复链接：已存在记录 #{row['id']}"
                   f"（{row['status']}，来源 @{row['channel'] or '-'}）")
            db.execute(
                "INSERT INTO transfers(kind,share_code,receive_code,share_url,title,channel,message_id,"
                "category,target_dir,status,error,bypass_filters,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (kind, code, link["receive_code"], link["share_url"], display_title,
                 channel, message_id, category, target, "duplicate", err,
                 1 if bypass else 0, time.time()),
            )
            result["duplicates"] += 1
            logstream.info("pipeline", f"[{channel}] 跳过重复链接 {code}：{err}")
            continue

        status = "queued" if target else "skipped"
        db.execute(
            "INSERT INTO transfers(kind,share_code,receive_code,share_url,title,channel,message_id,"
            "category,target_dir,status,bypass_filters,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (kind, code, link["receive_code"], link["share_url"], display_title,
             channel, message_id, category, target, status, 1 if bypass else 0, time.time()),
        )
        if status == "queued":
            result["queued"] += 1
            logstream.info("pipeline",
                           f"[{channel}] 入队转存: {code} → {category or '未分类'} → {target}")
        else:
            reason = "未分类且动作为 skip" if not category else f"{category} 的此类型目录为 skip"
            logstream.info("pipeline", f"[{channel}] 跳过 {code}（{reason}）")

    # 统计口径：仅计入真实处理过的消息（入队或含链接的跳过），重复消息不重复计数
    if result["queued"] or result["links"]:
        db.execute("UPDATE channels SET msg_count=msg_count+1 WHERE name=?", (channel,))
    return result
