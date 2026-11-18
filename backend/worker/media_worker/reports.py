"""目录整理与媒体刮削 Telegram 汇总汇报排版模块。"""
from __future__ import annotations

import posixpath

from ...pan115.client import normalize_title


def _format_media_detail_lines(done_items: list[dict], max_groups: int = 6) -> list[str]:
    """按「作品/剧集」聚合已整理或已刮削条目，生成适合纯文本 TG 通知的精简明细列表。"""
    if not done_items:
        return []
    groups: dict[tuple, dict] = {}
    for it in done_items:
        mtype = str(it.get("media_type") or "movie")
        title = str(it.get("title") or "").strip() or "未命名媒体"
        year = str(it.get("year") or "").strip()
        tmdb_id = it.get("tmdb_id")
        norm = normalize_title(title, fuzzy_t2s=True) or title.lower()
        key = (mtype, norm, year, str(tmdb_id or ""))
        g = groups.get(key)
        dpath = str(it.get("dest_path") or "").rstrip("/")
        dname = str(it.get("dest_name") or "").strip()
        item_paths = [str(p).rstrip("/") for p in (it.get("dest_paths") or []) if str(p).strip()]
        if dpath and dpath not in item_paths:
            item_paths.insert(0, dpath)
        item_cnt = max(1, int(it.get("count") or 1))
        sub_cnt = int(it.get("sub_count") or 0)
        has_nfo = bool(it.get("has_nfo"))
        has_poster = bool(it.get("has_poster"))
        if g is None:
            groups[key] = {
                "media_type": mtype,
                "title": title,
                "year": year,
                "tmdb_id": tmdb_id,
                "count": item_cnt,
                "sub_count": sub_cnt,
                "is_bdmv": bool(it.get("is_bdmv")),
                "has_nfo": has_nfo,
                "has_poster": has_poster,
                "first_path": dpath,
                "first_name": dname,
                "paths": item_paths,
            }
        else:
            g["count"] += item_cnt
            g["sub_count"] += sub_cnt
            g["has_nfo"] = g["has_nfo"] or has_nfo
            g["has_poster"] = g["has_poster"] or has_poster
            for p in item_paths:
                if p and p not in g["paths"]:
                    g["paths"].append(p)

    group_list = list(groups.values())
    lines: list[str] = []
    for idx, g in enumerate(group_list[:max_groups], 1):
        icon = "📺" if g["media_type"] == "tv" else "🎬"
        yr_part = f" ({g['year']})" if g["year"] else ""
        tmdb_part = f" [tmdbid={g['tmdb_id']}]" if g["tmdb_id"] else ""
        unit = "集" if g["media_type"] == "tv" else ("套" if g["is_bdmv"] else "部")
        sub_part = f"，含 {g['sub_count']} 个字幕" if g["sub_count"] > 0 else ""
        meta_tags = []
        if g["has_nfo"]:
            meta_tags.append("NFO")
        if g["has_poster"]:
            meta_tags.append("海报")
        meta_part = f"｜{' + '.join(meta_tags)}" if meta_tags else ""

        paths = g["paths"]
        if g["media_type"] == "tv" or g["is_bdmv"] or g["count"] > 1:
            if len(paths) > 1:
                try:
                    common = posixpath.commonpath(paths)
                    target_disp = (common.rstrip("/") + "/") if common and common != "/" else (paths[0] + "/")
                except Exception:
                    target_disp = (paths[0] + "/") if paths else "/"
            else:
                target_disp = (paths[0] + "/") if paths else "/"
        else:
            fp = g["first_path"]
            fn = g["first_name"]
            target_disp = f"{fp}/{fn}" if fp and fn else (fp or fn or "/")

        lines.append(f"{idx}. {icon} {g['title']}{yr_part}{tmdb_part}（{g['count']} {unit}{sub_part}{meta_part}）")
        lines.append(f"   ↳ {target_disp}")

    if len(group_list) > max_groups:
        rem_groups = group_list[max_groups:]
        rem_count = sum(g["count"] for g in rem_groups)
        lines.append(f"… 另有 {len(rem_groups)} 部作品（共 {rem_count} 项）已就位，详情见网页任务记录")

    return lines


_format_organize_detail_lines = _format_media_detail_lines


def _build_organize_tg_report(
    *,
    outcome: str,
    trigger: str,
    batch_id: str,
    elapsed_sec: int,
    rule_cnt: int,
    total_candidates: int,
    done_cnt: int,
    skip_cnt: int,
    fail_cnt: int,
    removed_dirs: int,
    done_items: list[dict],
    scan_stats: dict | None = None,
    fail_items: list[tuple[str, str]],
    error_msg: str = "",
    system_running: bool = True,
) -> str | None:
    """构造「目录整理」纯文本 TG 通知；定时任务 0 移动 0 失败时静默返回 None。"""
    if trigger != "manual" and outcome == "done" and done_cnt == 0 and fail_cnt == 0:
        return None

    trig_zh = "手动执行" if trigger == "manual" else "定时计划执行"
    dur_zh = f"（耗时 {max(0, int(elapsed_sec))}s）"
    resume_line = (
        "▶️ 主程序转存与云下载已恢复运行"
        if system_running
        else "ℹ️ 系统当前处于停止状态，未执行部分保留原状"
    )

    if outcome == "risk_aborted":
        head = "🛑 115 目录整理触发风控熔断"
    elif outcome == "cancelled":
        head = "⏸ 115 目录整理已安全中断"
    elif outcome == "error":
        head = "❌ 115 目录整理执行异常"
    elif fail_cnt > 0 and done_cnt > 0:
        head = "⚠️ 115 目录整理部分完成（含异常）"
    elif fail_cnt > 0:
        head = "❌ 115 目录整理执行失败"
    elif done_cnt == 0:
        head = "📁 115 目录整理完成（无需变动）"
    else:
        head = "📁 115 目录整理完成"

    if outcome == "done" and done_cnt == 0 and fail_cnt == 0:
        scope_txt = (
            "暂无已启用的整理规则"
            if rule_cnt == 0
            else f"{rule_cnt} 条规则，共检查 {total_candidates} 项（均已符合命名规范或已跳过）"
        )
        return "\n".join([
            head,
            "",
            f"• 触发方式：{trig_zh}{dur_zh}",
            f"• 扫描范围：{scope_txt}",
            resume_line,
        ])

    scan_filter_parts = []
    for key, label in (("non_video", "非视频"), ("media_type_skip", "类型互斥"),
                       ("extra_skip", "花絮/广告/微小体积"), ("dedup_seen", "其他规则已认领")):
        v = int((scan_stats or {}).get(key) or 0)
        if v > 0:
            scan_filter_parts.append(f"{label} {v}")

    lines = [
        head,
        "",
        f"• 触发方式：{trig_zh}{dur_zh}",
        f"• 整理统计：成功 {done_cnt} 项｜跳过 {skip_cnt} 项｜失败 {fail_cnt} 项",
    ]
    if scan_filter_parts:
        lines.append(f"• 扫描过滤：{'｜'.join(scan_filter_parts)}（不计入整理统计）")
    if removed_dirs > 0:
        lines.append(f"• 空目录清理：已清理 {removed_dirs} 个搬空目录")
    if error_msg and outcome in ("risk_aborted", "cancelled", "error"):
        lines.append(f"• 中止原因：{str(error_msg)[:180]}")

    detail_lines = _format_media_detail_lines(done_items, max_groups=6)
    if detail_lines:
        lines.append("")
        lines.append("整理明细：")
        lines.extend(detail_lines)

    if fail_items:
        lines.append("")
        lines.append("失败摘要（仅显示前 3 条）：")
        for fname, err_txt in fail_items[:3]:
            lines.append(f"• {fname}：{str(err_txt or '未知错误')[:120]}")
        if len(fail_items) > 3:
            lines.append(f"• … 另有 {len(fail_items) - 3} 项失败，详情见网页任务记录")

    lines.append("")
    if done_cnt > 0:
        undo_note = "（已完成部分仍可一键撤销）" if (outcome != "done" or fail_cnt > 0) else ""
        lines.append(f"📌 批次编号：{batch_id}{undo_note}")
    lines.append(resume_line)
    if done_cnt > 0:
        lines.append("💡 如需回退，可在网页「操作账本」按批次一键撤销")

    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3850].rstrip() + "\n…（内容较长已截断，完整记录请查看网页任务列表）"
    return text


def _build_scrape_tg_report(
    *,
    mode: str,
    outcome: str,
    elapsed_sec: int,
    rule_cnt: int,
    total_candidates: int,
    auto_cnt: int,
    confirm_cnt: int,
    skip_cnt: int,
    fail_cnt: int,
    removed_dirs: int,
    auto_items: list[dict],
    pending_items: list[dict],
    fail_items: list[tuple[str, str]],
    error_msg: str = "",
    system_running: bool = True,
) -> str | None:
    """构造「媒体刮削」纯文本 TG 通知（无交互按钮；所有待确认项引导前往网页提交队列）。"""
    if mode == "commit_only" and outcome == "done" and auto_cnt == 0 and fail_cnt == 0:
        return None

    dur_zh = f"（耗时 {max(0, int(elapsed_sec))}s）"
    resume_line = (
        "▶️ 主程序转存与云下载已恢复运行"
        if system_running
        else "ℹ️ 系统当前处于停止状态，未执行部分保留原状"
    )
    is_commit = mode == "commit_only"
    subj = "刮削提交队列" if is_commit else "媒体刮削"

    if outcome == "risk_aborted":
        head = f"🛑 115 {subj}触发风控熔断"
    elif outcome == "cancelled":
        head = f"⏸ 115 {subj}已安全中断"
    elif outcome == "error":
        head = f"❌ 115 {subj}执行异常"
    elif fail_cnt > 0 and (auto_cnt > 0 or confirm_cnt > 0):
        head = f"⚠️ 115 {subj}部分完成（含异常）"
    elif fail_cnt > 0:
        head = f"❌ 115 {subj}执行失败"
    elif is_commit:
        head = "🚀 115 刮削提交队列执行完毕"
    elif auto_cnt == 0 and confirm_cnt == 0:
        head = "🎬 115 媒体刮削完成（无新增可刮削条目）"
    else:
        head = "🎬 115 媒体刮削本轮完成"

    if not is_commit and outcome == "done" and auto_cnt == 0 and confirm_cnt == 0 and fail_cnt == 0:
        if rule_cnt == 0:
            scope_txt = "暂无已启用的刮削规则"
        elif skip_cnt > 0:
            scope_txt = f"{rule_cnt} 条规则，共检查 {total_candidates} 项（其中 {skip_cnt} 项已在待确认/排队队列中）"
        else:
            scope_txt = f"{rule_cnt} 条规则，共检查 {total_candidates} 项（均已处理或无新媒体）"
        return "\n".join([
            head,
            "",
            f"• 执行耗时：{max(0, int(elapsed_sec))}s",
            f"• 扫描范围：{scope_txt}",
            resume_line,
        ])

    lines = [head, ""]
    if is_commit:
        lines.append(f"• 执行统计：成功落盘 {auto_cnt} 项｜失败 {fail_cnt} 项{dur_zh}")
    else:
        lines.append(
            f"• 刮削统计：自动落盘 {auto_cnt} 项｜跳过待确认 {confirm_cnt} 部｜已忽略/跳过 {skip_cnt} 项｜失败 {fail_cnt} 项{dur_zh}"
        )
    if removed_dirs > 0:
        lines.append(f"• 空目录清理：已清理 {removed_dirs} 个搬空目录")
    if error_msg and outcome in ("risk_aborted", "cancelled", "error"):
        lines.append(f"• 中止原因：{str(error_msg)[:180]}")

    detail_lines = _format_media_detail_lines(auto_items, max_groups=6)
    if detail_lines:
        lines.append("")
        has_local_meta = any(it.get("has_nfo") or it.get("has_poster") for it in auto_items)
        meta_suffix = "（本地已写 NFO / 海报）" if has_local_meta else ""
        sec_title = f"已确认落盘明细{meta_suffix}：" if is_commit else f"已自动匹配落盘{meta_suffix}："
        lines.append(sec_title)
        lines.extend(detail_lines)

    if not is_commit and pending_items:
        lines.append("")
        if len(pending_items) > 6:
            lines.append(f"以下作品未自动锁定，已跳过并加入网页「待确认列表」（共 {len(pending_items)} 部，前 6 部）：")
        else:
            lines.append("以下作品未自动锁定，已跳过并加入网页「待确认列表」：")
        for idx, p in enumerate(pending_items[:6], 1):
            icon = "📺" if p.get("media_type") == "tv" else "🎬"
            disp = str(p.get("title") or p.get("orig_name") or "未命名").strip()
            yr = str(p.get("year") or "").strip()
            yr_part = f" ({yr})" if yr and yr not in disp else ""
            ep_cnt = int(p.get("ep_count") or 1)
            ep_part = f"（共 {ep_cnt} 集）" if ep_cnt > 1 else ""
            top1 = p.get("top1")
            if top1:
                t1_title = str(top1.get("title") or "?")
                t1_yr = f"({top1['year']})" if top1.get("year") else ""
                t1_sc = float(top1.get("score") or 0)
                hint = f"最高候选 {t1_title}{t1_yr} · {t1_sc:.0f}分"
            else:
                hint = "未匹配到高分候选，需网页手动搜片"
            lines.append(f"{idx}. {icon} {disp}{yr_part}{ep_part}｜{hint}")
        if len(pending_items) > 6:
            lines.append(f"… 另有 {len(pending_items) - 6} 部待确认作品，请打开网页查看")
        lines.append("👉 请前往网页「整理刮削 → 待确认列表」点选候选后点击「提交队列」")

    if fail_items:
        lines.append("")
        lines.append("失败摘要（仅显示前 3 条）：")
        for fname, err_txt in fail_items[:3]:
            lines.append(f"• {fname}：{str(err_txt or '未知错误')[:120]}")
        if len(fail_items) > 3:
            lines.append(f"• … 另有 {len(fail_items) - 3} 项失败，详情见网页任务记录")

    lines.append("")
    lines.append(resume_line)

    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3850].rstrip() + "\n…（内容较长已截断，完整记录请查看网页任务列表）"
    return text
