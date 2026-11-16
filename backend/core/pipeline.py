"""消息处理管道：去重 → 黑名单 → 提取链接 → 分类 → 订阅过滤 → 生成转存任务入队。"""
import sqlite3
import time

from .. import database as db
from ..core import classifier, episode, extractor, filters, logstream, quality, subscribe

# 进程内提示标记：每频道只提示一次"无链接"说明，避免刷屏
_runtime_flags: dict[str, bool] = {}


def process_message(channel: str, message_id: int, text: str,
                    bypass: bool = False, increment: bool = False,
                    priority: int = 0, anchor: dict | None = None,
                    force_category: str = "") -> dict:
    """处理一条 TG 消息，返回处理摘要（供测试与调试）。

    bypass=True 时跳过消息级黑名单；increment=True 时生成的转存任务带增量标记
    （只转存目标目录里没有的文件，已存过的不重复）；priority>0 的任务在队列中
    优先领取（向前补全用：额度有限时优先补全剧集）；
    anchor={category, target_dir} 时（向前补全的历史分集帖），沿用锚点任务的分类
    与目标目录（同剧不同帖标题格式不一，各自分类会漂移），并绕过订阅过滤。
    force_category 仅供「管理员直发链接」使用：指令头后写了分类名时直接采用；
    默认空串，分类仍由标题规则决定，行为与旧版一致。
    """
    result = {"channel": channel, "message_id": message_id, "links": 0, "queued": 0,
              "duplicates": 0, "blocked": False, "skipped": 0}

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
                f"[{channel}] 该频道首条消息未发现可转存的资源链接（115 分享/磁力/电驴/迅雷），"
                "此类消息仅记录不转存；若该频道应包含资源链接，"
                "请到「账号管理」的「消息预览」核对消息原文")
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
    #    向前补全的历史分集帖（anchor）沿用锚点任务的分类与目标目录：
    #    同剧不同帖的标题格式不一，各自分类会漂移到不同目录或被「未分类=跳过」拦下
    anchor_cat = str((anchor or {}).get("category") or "")
    anchor_dir = str((anchor or {}).get("target_dir") or "")
    if anchor_cat:
        category, matched = anchor_cat, "锚点沿用"
    elif force_category:
        category, matched = force_category, "直发分类"
    else:
        category, matched = classifier.classify(text or "")
    display_title = classifier.headline(text or "") or (text or "")[:300]

    # 4.5 订阅追剧：命中订阅 → 记录进度；该频道开了「仅订阅」→ 未命中的消息不转存
    # 判断优先级：频道自己的 sub_only 开关 > 全局 subscription_only 设置（频道不存在时兜底）
    # 向前补全帖（anchor）不受订阅过滤拦截——用户显式要求补全该剧，跳过与意图冲突
    sub_hits = subscribe.match_all(f"{display_title}\n{text or ''}")
    ch_row = db.query("SELECT sub_only FROM channels WHERE name=?", (channel,), one=True)
    sub_only = bool(ch_row["sub_only"]) if ch_row else subscribe.subscription_only()
    if sub_only and not sub_hits and anchor is None:
        key = f"_sub_skip_logged_{channel}"
        if not _runtime_flags.get(key):
            _runtime_flags[key] = True
            logstream.info("pipeline",
                           f"[{channel}] 该频道已开启「仅订阅」模式：此消息未命中任何订阅关键词，"
                           "仅记录不转存（在「账号管理」或「订阅追剧」页调整）")
        result["sub_skipped"] = True
        return result

    # 4.6 订阅质量策略（默认关闭）：最低画质门槛 + 同订阅同集择优。
    # 画质只从标题/正文识别；识别不出（score=0）一律放行，绝不因「没写画质」误拦。
    quality_on = bool(db.get_setting("quality_policy_enabled", False))
    min_quality = ""
    if sub_hits:
        for s in sub_hits:
            q = str(s.get("min_quality") or "").strip()
            if q:
                min_quality = q
                break
    if not min_quality:
        min_quality = str(db.get_setting("quality_min_resolution", "") or "").strip()
    q_info = quality.parse(f"{display_title}\n{text or ''}")
    q_season, q_ep, q_kind = episode.parse(display_title)
    if q_ep is None:
        q_season, q_ep, q_kind = episode.parse((text or "")[:200])
    q_ep_key = episode.canonical(q_season, q_ep, q_kind) if q_ep is not None else ""
    # 仅在「唯一命中订阅」且识别出集标识时做同集比较：
    # 同一部剧的不同集画质相同很常见，按订阅整体比较会把后续集数全部误判为「已有同画质」
    q_sub_id = int(sub_hits[0]["id"]) if len(sub_hits) == 1 else 0
    wash_on = bool(db.get_setting("wash_enabled", False)) and bool(
        str(db.get_setting("wash_archive_dir", "") or "").strip())

    for link in links:
        code = link["share_code"]
        kind = link.get("kind", "share")

        # 5.5 追更：此前已成功的分享取回其分类与目标目录——同剧不同帖的标题格式
        # 不一，各自分类会漂移到不同目录，导致增量比对失真（整包重存）。
        # 该取值须在判重之前完成：判重留痕也要用到正确的分类与目录
        prev_row = None
        if kind == "share" and db.get_setting("share_update_enabled", True):
            r = db.query(
                "SELECT category, target_dir FROM transfers WHERE share_code=? AND kind='share'"
                " AND status='success' ORDER BY id DESC LIMIT 1", (code,), one=True)
            if r:
                prev_row = dict(r)
        cat_i = anchor_cat or (str((prev_row or {}).get("category") or "") if prev_row else "") \
            or category
        if anchor_dir:
            base_dir = anchor_dir
        elif prev_row and str((prev_row or {}).get("target_dir") or ""):
            base_dir = str(prev_row["target_dir"])
        else:
            base_dir = classifier.resolve_target(cat_i, kind=kind) if cat_i else ""
            if not cat_i:
                action = db.get_setting("unclassified_action", "skip")
                base_dir = "" if action == "skip" else str(action)
        target = (classifier.resolve_target(cat_i, kind="offline") or base_dir) \
            if kind == "offline" else base_dir
        prev_done = prev_row is not None

        # 5. 分享码去重：
        #    - 活动任务（排队/转存中/已提交）→ 判重留痕，便于追溯来源频道与时间
        #    - 此前已成功的分享 → 转为「追更」任务（上面已取回其分类/目录）
        row = db.query(
            "SELECT id, status, channel FROM transfers WHERE share_code=?"
            " AND status IN ('queued','running','submitted') ORDER BY id LIMIT 1",
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
                 channel, message_id, cat_i, target, "duplicate", err,
                 1 if bypass else 0, time.time()),
            )
            result["duplicates"] += 1
            logstream.info("pipeline", f"[{channel}] 跳过重复链接 {code}：{err}")
            continue

        # 5.2 质量策略：最低画质门槛 + 同订阅同集择优（默认关闭）。
        # 追更/补全任务不参与：它们按真实文件名增量比对，再判画质会误拦同一分享的新集
        skip_reason = ""
        wash_from = 0
        if quality_on and kind == "share" and not prev_done and not increment:
            if not quality.min_resolution_allowed(q_info, min_quality):
                skip_reason = (f"画质低于最低要求（识别为 {q_info['resolution']}，"
                               f"要求 ≥ {min_quality}）")
            elif q_sub_id and q_ep_key and q_info["score"] > 0:
                old = db.query(
                    "SELECT id, status, quality_score, quality_tag FROM transfers"
                    " WHERE sub_id=? AND episode_key=? AND kind='share'"
                    " AND status IN ('queued','running','submitted','success')"
                    " AND quality_score>0 ORDER BY quality_score DESC, id DESC LIMIT 1",
                    (q_sub_id, q_ep_key), one=True)
                if old:
                    old_tag = f"#{old['id']} {old['quality_tag'] or str(old['quality_score']) + ' 分'}"
                    if old["quality_score"] >= q_info["score"]:
                        skip_reason = f"已有同等或更高画质（{old_tag}），按订阅质量策略跳过"
                    elif old["status"] == "success" and wash_on:
                        wash_from = int(old["id"])
                    elif old["status"] == "success":
                        skip_reason = (f"已有较低画质版本（{old_tag}），"
                                       "可在设置开启「洗版升级」后自动升级")
                    elif old["status"] == "queued":
                        db.execute(
                            "UPDATE transfers SET status='skipped', error=?"
                            " WHERE id=? AND status='queued'",
                            (f"已被更高画质资源替代（{q_info['tag'] or '新版'}，"
                             f"来源 @{channel.lstrip('@')}）", old["id"]))
                        logstream.info("pipeline",
                                       f"[{channel}] 队列任务 #{old['id']} 被更高画质资源替代")

        flag = 0 if wash_from else (1 if (increment or prev_done) else 0)
        status = "skipped" if skip_reason else ("queued" if target else "skipped")
        try:
            db.execute(
                "INSERT INTO transfers(kind,share_code,receive_code,share_url,title,channel,message_id,"
                "category,target_dir,status,error,bypass_filters,increment,priority,"
                "sub_id,episode_key,quality_score,quality_tag,wash,wash_from,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (kind, code, link["receive_code"], link["share_url"], display_title,
                 channel, message_id, cat_i, target, status, skip_reason,
                 1 if bypass else 0, flag, priority,
                 q_sub_id if kind == "share" else 0, q_ep_key if kind == "share" else "",
                 q_info["score"] if kind == "share" else 0,
                 q_info["tag"] if kind == "share" else "",
                 1 if wash_from else 0, wash_from, time.time()),
            )
        except sqlite3.IntegrityError:
            # 并发竞态兜底：部分唯一索引保证同分享码同时只有一个活动任务，
            # 查询与插入之间的毫秒级窗口被其他来源（轮询/Userbot/补漏）抢先时在此拦截。
            # 先确认确为"同码活动任务"冲突，其他完整性错误原样抛出，不伪装成判重
            if not db.query(
                "SELECT 1 FROM transfers WHERE share_code=? AND status IN"
                " ('queued','running','submitted') LIMIT 1", (code,), one=True):
                raise
            err = "重复链接：已存在同分享码的活动任务（并发入队被拦截）"
            db.execute(
                "INSERT INTO transfers(kind,share_code,receive_code,share_url,title,channel,message_id,"
                "category,target_dir,status,error,bypass_filters,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (kind, code, link["receive_code"], link["share_url"], display_title,
                 channel, message_id, cat_i, target, "duplicate", err,
                 1 if bypass else 0, time.time()),
            )
            result["duplicates"] += 1
            logstream.info("pipeline", f"[{channel}] 并发竞态：跳过重复链接 {code}（已并入队任务）")
            continue
        if status == "queued":
            result["queued"] += 1
            label = "洗版升级" if wash_from else (
                "追更转存" if prev_done else ("补全转存" if increment else "入队转存"))
            suffix = "（将只转存目标目录里没有的新文件）" if flag else ""
            if wash_from:
                suffix = f"（洗版：成功后旧版 #{wash_from} 移入归档目录）"
            logstream.info("pipeline",
                           f"[{channel}] {label}: {code} → {cat_i or '未分类'} → {target}{suffix}")
        else:
            reason = skip_reason or (
                "未分类，且动作设为「跳过」" if not cat_i else f"「{cat_i}」的目录设为「跳过」")
            result["skipped"] += 1
            logstream.info("pipeline", f"[{channel}] 跳过 {code}（{reason}）")

    # 4.7 订阅进度：命中即更新「最近命中」；只有本次真实处理（入队/重复记录）才推进
    # 追更进度并推送通知，避免被跳过的内容把进度推到前面导致后续漏报
    if sub_hits:
        _record_sub_progress(sub_hits, display_title, text or "", channel,
                             processed=bool(result["queued"] or result["duplicates"]))

    # 统计口径：仅计入真实处理过的消息（入队或含链接的跳过），重复消息不重复计数
    if result["queued"] or result["links"]:
        db.execute("UPDATE channels SET msg_count=msg_count+1 WHERE name=?", (channel,))
    return result


def _record_sub_progress(subs: list[dict], title: str, text: str,
                         channel: str, processed: bool):
    """记录订阅追更进度；出现新的一集时推送一条「追剧更新」通知。

    一条消息可命中多个订阅（关键词/别名重叠），逐个记录进度，但只推送一次通知，
    避免同一集重复刷屏。进度记录失败只记警告，不影响转存主流程。
    """
    from ..core import episode
    season, ep, kind = episode.parse(title)
    if ep is None:
        season, ep, kind = episode.parse(text[:200])
    display = episode.canonical(season, ep, kind)
    new_subs = []
    for sub in subs:
        try:
            if not processed:
                subscribe.touch_match(sub, title)  # 命中但被跳过：只更新「最近命中」
                continue
            if subscribe.record_progress(sub, title, season, ep, kind):
                new_subs.append(sub)
                logstream.info(
                    "subscribe",
                    f"[{sub['keyword']}] 追剧更新：{(title or '')[:50]}"
                    f"{f'（{display}）' if display else ''}")
        except Exception as e:
            logstream.warn("subscribe", f"记录追剧进度失败（{sub.get('keyword')}）: {logstream.exc(e)}")
    if new_subs:
        try:
            from ..worker import jobs as _jobs
            first = new_subs[0]
            if display:
                lines = ["🎬 追剧更新", "", "📺 " + (title or first.get("keyword", ""))[:80],
                         "🎞 已更新到：" + display,
                         "📡 来源：@" + channel.lstrip("@")]
            else:
                lines = ["🎬 追剧更新", "", "📺 " + (title or first.get("keyword", ""))[:80],
                         "📡 来源：@" + channel.lstrip("@")]
            _jobs.notify("\n".join(lines))
        except Exception as e:
            logstream.warn("subscribe", f"追剧更新通知发送失败: {logstream.exc(e)}")
