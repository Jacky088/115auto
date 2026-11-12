"""转存队列工作器：消费 transfers 表中 status='queued' 的任务。

可靠性设计：
- 提交前校验链接是否符合 115 支持范围（不符合 → 直接失败 + 日志 + TG 通知，不重试）
- 分享转存：API 成功后再反查目标目录确认文件真实到达（双重确认）
- 云下载：提交成功只标记"已提交"，由后台任务跟踪 115 真实下载结果后二次通知
- 风控控制：随机间隔、每日限额、指数退避重试、连续失败熔断
"""
import asyncio
import random
import re
import time

from .. import database as db
from ..core import classifier, filters, logstream, transfer_gate
from ..pan115 import client as pan
from . import jobs

MAX_ATTEMPTS = 5


class PermanentTransferError(Exception):
    """确定性失败（如链接格式不支持），不重试，直接失败并通知。"""


# 分享/链接已死亡的 115 错误特征：确定性失败，重试无意义也不计入熔断。
# 覆盖 snap（读取分享）与 receive（转存提交）两个阶段可能返回的文案
_DEAD_SHARE_MARKERS = ("链接已失效", "链接已过期", "链接不存在",
                       "分享不存在", "分享已取消", "分享已删除", "分享已过期",
                       "分享已屏蔽", "分享已被取消", "分享链接已失效")


def validate_link(kind: str, share_code: str, share_url: str) -> str:
    """校验链接是否符合 115 支持范围，返回错误原因；合法返回空串。"""
    if kind == "share":
        code = (share_code or "").strip()
        if not code:
            return "分享码为空，链接无效"
        if not re.fullmatch(r"[0-9A-Za-z]{5,24}", code):
            return f"分享码格式不符合 115 规范（{code[:24]}），无法转存"
        return ""
    # 云下载
    url = (share_url or "").strip()
    if not url:
        return "云下载地址为空"
    if url.startswith("magnet:?xt=urn:btih:"):
        h = url.split("btih:", 1)[1].split("&", 1)[0]
        if re.fullmatch(r"[0-9a-fA-F]{40}", h) or re.fullmatch(r"[A-Za-z2-7]{32}", h):
            return ""
        return "磁力链 hash 格式不支持（115 仅支持 BTv1：40 位十六进制或 32 位 Base32），无法云下载"
    if url.startswith("ed2k://|file|"):
        parts = url.split("|")
        if len(parts) >= 5 and re.fullmatch(r"[0-9A-Fa-f]{32}", parts[4]):
            return ""
        return "电驴链接格式不完整（缺少有效的文件 hash），无法云下载"
    if url.lower().startswith(("http://", "https://", "ftp://")):
        return ""
    return "不支持的链接协议（115 云下载仅支持 HTTP/FTP/磁力/电驴），无法云下载"


def _num_setting(key: str, default: float) -> float:
    """读取数值型设置，非法/负数时回退默认值（防止脏配置导致工作器崩溃）。"""
    try:
        v = float(db.get_setting(key, default))
    except (TypeError, ValueError):
        return float(default)
    return v if v >= 0 else float(default)


def _interval() -> float:
    lo = _num_setting("transfer_interval_min", 30)
    hi = _num_setting("transfer_interval_max", 90)
    lo, hi = max(lo, 1.0), max(hi, 1.0)  # 至少 1 秒，避免忙轮询
    # 115 风控自适应：出现风控征兆错误时按档位放大间隔（1→2→3→5→8 倍）
    return random.uniform(lo, max(hi, lo)) * pan.pace_factor()


def _space_preflight(total_size: int, title: str = "") -> bool:
    """空间预检：115 剩余空间不足时跳过任务并告警（转存前拦截，而不是失败后被动发现）。

    返回 True = 空间足够（或无法判定，放行）；False = 明显不足，任务打 skipped。
    """
    if total_size <= 0:
        return True
    try:
        sp = pan.space_info()
    except Exception:
        return True  # 容量接口失败不阻塞转存
    free = sp.get("free", 0)
    if free and total_size > free:
        jobs.notify_failure("share", title, "", "", "",
                            retry_info=f"💡 115 剩余空间不足（剩余 {_fmt_gb(free)}，"
                                       f"需要 {_fmt_gb(total_size)}），任务已标记为「跳过」，"
                                       "清理空间后可手动重试")
        return False
    return True


def _fmt_gb(b: int) -> str:
    return f"{b / (1 << 30):.2f}GB"


def _norm_entry_name(s: str) -> str:
    """目录条目归一化：去空白 + 小写 + 去常见媒体扩展名，用于精确同名比对。

    不用 startswith 前缀判断：「进击的巨人」与「进击的巨人 最终季」是两部作品，
    前缀匹配会把续作误判为已存在并静默跳过。
    """
    n = _norm_filename(s)
    return re.sub(r"\.(mkv|mp4|avi|ts|m2ts|rmvb|mov|flv|wmv|iso|srt|ass|ssa|zip|rar|7z)$", "", n)


_dir_cache: dict[int, tuple[float, set[str]]] = {}


def _get_dir_names_cached(cid: int) -> set[str]:
    ttl = max(0, int(float(db.get_setting("dedup_cache_ttl_sec", 90) or 0)))
    now = time.time()
    if ttl > 0 and cid in _dir_cache:
        ts, cached = _dir_cache[cid]
        if now - ts < ttl:
            return cached
    names = pan.list_file_names(cid, limit=5000)
    if ttl > 0:
        _dir_cache[cid] = (now, names)
    return names


def _add_to_dir_cache(cid: int, name: str):
    if cid in _dir_cache:
        _dir_cache[cid][1].add(name)


def _invalidate_dir_cache(cid: int):
    """转存落盘后主动失效该目录缓存，防并发或连续转存时的假阴性脏读。"""
    _dir_cache.pop(cid, None)


def _dedup_by_name(cid: int, title: str, snap: dict = None) -> str:
    """同名去重：目标目录已存在同名内容时返回原因，否则空串。

    不仅比对任务标题，还深层比对分享内的真实文件/文件夹名，
    彻底避免散装单文件（如 1.mp4）直接入库触发 115 自动重命名 (1) 冲突。
    """
    if not db.get_setting("dedup_by_name", True):
        return ""
    if not cid:
        return ""
    try:
        names = _get_dir_names_cached(cid) or []
    except Exception:
        names = []  # 目录为空或检索失败时，仍继续核对 organized_records 账本防重

    names_raw = {_norm_filename(n): n for n in names}
    names_base = {_norm_entry_name(n): n for n in names if _norm_entry_name(n)}

    # 1. 标题比对
    title_raw = _norm_filename(title)
    if title_raw in names_raw:
        matched = names_raw[title_raw]
        return f"目标目录已存在「{matched}」（同名去重），如需强制转存请在「系统设置」关闭「同名去重」后重试"
    title_base = _norm_entry_name(title)
    if title_base and title_base in names_base:
        matched = names_base[title_base]
        return f"目标目录已存在「{matched}」（同名去重），如需强制转存请在「系统设置」关闭「同名去重」后重试"

    # 2. 分享内容深层条目与单文件名比对
    if snap and isinstance(snap, dict):
        # 2.1 顶层条目名比对（如合集/剧集文件夹名）
        for top in (snap.get("top_names") or []):
            if not top:
                continue
            top_raw = _norm_filename(top)
            if top_raw in names_raw:
                return f"目标目录已存在「{names_raw[top_raw]}」（同名去重），如需强制转存请在「系统设置」关闭「同名去重」后重试"
            top_base = _norm_entry_name(top)
            if top_base and top_base in names_base:
                return f"目标目录已存在「{names_base[top_base]}」（同名去重），如需强制转存请在「系统设置」关闭「同名去重」后重试"

        # 2.2 散装文件真实文件名比对（对齐 1.mp4 等具体单文件）
        files = [f for f in (snap.get("files") or []) if f.get("fn")]
        if files:
            for f in files[:20]:
                fn = f.get("fn")
                if not fn:
                    continue
                fn_raw = _norm_filename(fn)
                if fn_raw in names_raw:
                    return f"目标目录已存在「{names_raw[fn_raw]}」（同名去重），如需强制转存请在「系统设置」关闭「同名去重」后重试"
                fn_base = _norm_entry_name(fn)
                if fn_base and fn_base in names_base:
                    if len(fn_base) > 3 or not fn_base.isdigit():
                        return f"目标目录已存在「{names_base[fn_base]}」（同名去重），如需强制转存请在「系统设置」关闭「同名去重」后重试"
                if fn_base and len(fn_base) >= 5 and not re.fullmatch(r"(?:s?\d+)?(?:e|ep)?\d+", fn_base, re.I):
                    try:
                        rec_rows = db.query(
                            "SELECT orig_name, dest_name FROM organized_records WHERE undone=0 ORDER BY id DESC LIMIT 800"
                        )
                        for rr in rec_rows:
                            if _norm_entry_name(rr["orig_name"]) == fn_base:
                                return f"曾转存并整理为「{rr['dest_name']}」（账本去重），如需强制转存请关闭「同名去重」后重试"
                    except Exception:
                        pass

    return ""


def _process_one(job: dict):
    """同步执行一次转存（跑在线程池）。"""
    code, rc = job["share_code"], job["receive_code"]
    target = job["target_dir"]
    kind = job.get("kind", "share")

    # 0. 链接合法性：不符合 115 支持范围 → 确定性失败
    err = validate_link(kind, code, job["share_url"])
    if err:
        raise PermanentTransferError(err)

    # ---- 云下载任务（ED2K/磁力/HTTP）----
    if kind == "offline":
        cid = pan.mkdirs(target)
        if not cid:
            raise PermanentTransferError(f"目标目录无效: {target}")
        # 重试防重：上次提交可能已被 115 受理（响应超时/丢失），再提交会生成双任务
        if job.get("attempts", 0):
            try:
                key = jobs._offline_key(job["share_url"])
                tasks = pan.task_list_all()
                if any(jobs._offline_key(t.get("url") or "") == key for t in tasks):
                    logstream.info("transfer", "云下载任务已存在于 115（此前提交已受理），跳过重复提交")
                    changed = db.execute("UPDATE transfers SET status='submitted', finished_at=NULL"
                                         " WHERE id=? AND status='running'", (job["id"],))
                    if changed == 0:
                        return
                    display = classifier.headline(job["title"]) or job["title"][:80]
                    jobs.notify_transfer(display, job.get("category", ""), job.get("channel", ""),
                                         target, kind="offline")
                    return
            except Exception:
                pass  # 查询失败按原流程重新提交
        resp = pan.offline_download(job["share_url"], cid)
        # 提交成功 ≠ 下载完成：标记 submitted，由 offline_check_loop 跟踪真实结果
        changed = db.execute("UPDATE transfers SET status='submitted', finished_at=NULL"
                             " WHERE id=? AND status='running'", (job["id"],))
        if changed == 0:
            return
        # 若 115 接口返回了 info_hash，写入 receive_code 方便后续精准核对跟踪
        ih = str((resp or {}).get("info_hash") or "").strip()
        if ih:
            db.execute("UPDATE transfers SET receive_code=? WHERE id=?", (ih, job["id"]))
        display = classifier.headline(job["title"]) or job["title"][:80]
        logstream.info("transfer",
                       f"☁️ 云下载任务已添加：{display[:60]} → {target}（下载完成后推送完成通知）")
        jobs.notify_transfer(display, job.get("category", ""), job.get("channel", ""),
                             target, kind="offline")
        return

    # ---- 分享转存任务 ----
    try:
        snap = pan.share_snap_all(code, rc)
    except pan.Pan115Error as e:
        # 分享已被取消/删除/不存在：确定性失败——重试无意义，也不计入连续失败熔断
        #（否则一批失效补全链接会把普通转存一起拖停 30 分钟）
        if any(m in str(e) for m in _DEAD_SHARE_MARKERS):
            raise PermanentTransferError(f"分享链接已失效（{str(e)[:120]}）") from e
        raise

    # 1.45 超大分享截断透明化：超过 5000 文件的部分不会转存，任务与日志必须留痕
    if snap.get("truncated"):
        db.execute("UPDATE transfers SET error=? WHERE id=? AND status='running'",
                   ("分享文件数超过 5000 上限，仅转存前 5000 个文件", job["id"]))
        logstream.warn("transfer", f"分享 {code} 文件数超过 5000 上限，仅转存前 5000 个文件")
    # 名称优先取频道消息里的标题（已可能是 TMDB 官方名），分享内的文件夹名仅兜底
    title = (classifier.headline(job["title"]) or snap["title"] or job["title"][:100] or code)

    # 1.5 分享内没有任何文件（空目录/链接空壳）：无法转存，直接跳过而不是反复重试
    if not snap["files"]:
        reason = "分享内没有可转存的文件（可能为空目录或分享内容已失效）"
        db.execute("UPDATE transfers SET status='skipped', title=?, error=?"
                   " WHERE id=? AND status='running'", (title, reason, job["id"]))
        logstream.warn("transfer", f"分享 {code} 跳过：{reason}")
        return

    # 1.55 增量追更：同一分享此前已成功转存，分享方常往同一链接里持续加更集数
    #（今天 E01、明天 E01-E03）。本次按真实文件名与目标目录比对，只转存新增的文件，
    # 已存过的集数不重复提交（实测 115 对重复提交会生成 "(1)" 副本）。
    incr = bool(job.get("increment"))
    if incr and target:
        tops = [t for t in (snap.get("top_names") or []) if t]
        # 单顶层文件夹的分集分享：此前整包转存落在 target/<文件夹名>/ 下，
        # 新集数也应存回同一文件夹，保持目录结构一致
        if len(tops) == 1 and len(snap["files"]) > len(tops):
            target = f"{target.rstrip('/')}/{tops[0]}"
        existing: set[str] = set()
        try:
            dest_cid = pan.mkdirs(target)
            existing = pan.list_file_names(dest_cid)
        except Exception as e:
            logstream.warn("transfer", f"追更预检目标目录失败（本次全量提交，宁多勿漏）: {e}")
        existing_n = {_norm_filename(n) for n in existing}
        try:
            org_recs = db.query("SELECT orig_name, dest_name FROM organized_records WHERE undone=0 ORDER BY id DESC LIMIT 800")
            for rr in org_recs:
                on_n = _norm_filename(rr["orig_name"])
                dn_n = _norm_filename(rr["dest_name"])
                if on_n and len(on_n) >= 5 and not re.fullmatch(r"(?:s?\d+)?(?:e|ep)?\d+(?:\.[a-z0-9]+)?", on_n, re.I):
                    existing_n.add(on_n)
                if dn_n and len(dn_n) >= 5:
                    existing_n.add(dn_n)
        except Exception:
            pass
        new_files = [f for f in snap["files"]
                     if f.get("fn") and f.get("fid")
                     and _norm_filename(f["fn"]) not in existing_n]
        new_size = sum(int(f.get("fs") or 0) for f in new_files)
        if not new_files:
            db.execute("UPDATE transfers SET status='skipped', title=?, size=0, error=?"
                       " WHERE id=? AND status='running'",
                       (title, "追更检查：分享无新增文件（已存过的集数不重复转存）", job["id"]))
            logstream.info("transfer", f"分享 {code} 追更检查完成：无新增文件，跳过")
            return
        total_count = len(snap["files"])
        # 用「仅新增文件」替换快照：后续空间预检/黑名单/提交/反查都只针对新增部分
        snap["files"] = new_files
        snap["total_size"] = new_size
        snap["top_names"] = []
        snap["top_ids"] = []
        logstream.info("transfer",
                       f"分享 {code} 追更检查：分享共 {total_count} 个文件，"
                       f"检测到新增 {len(new_files)} 个（{new_size / (1 << 30):.2f} GB）")

    # 1.6 空间预检：115 剩余空间不足时跳过（转存前拦截）
    if not _space_preflight(snap["total_size"], title):
        db.execute("UPDATE transfers SET status='skipped', title=?, size=?, file_count=?, "
                   "error=? WHERE id=? AND status='running'",
                   (title, snap["total_size"], len(snap["files"]),
                    f"115 剩余空间不足（需要 {_fmt_gb(snap['total_size'])}）", job["id"]))
        logstream.warn("transfer",
                       f"分享 {code} 跳过：115 剩余空间不足"
                       f"（需要 {_fmt_gb(snap['total_size'])}），任务已标记为「跳过」")
        return

    # 1.7 同名去重（可关）：目标目录已有同名内容时不重复占空间
    # 目录在这里解析一次（cid 供去重与转存两用），解析失败留给第 3 步正式报错走重试
    cid = 0
    if target:
        try:
            cid = pan.mkdirs(target)
        except Exception:
            cid = 0
    dedup_reason = "" if incr else _dedup_by_name(cid, title, snap)
    # 追更任务不做同名去重：增量分支已按真实文件名比对过，标题前缀匹配反而可能误拦
    if dedup_reason:
        db.execute("UPDATE transfers SET status='duplicate', title=?, size=?, error=?"
                   " WHERE id=?",
                   (title, snap["total_size"], dedup_reason, job["id"]))
        logstream.info("transfer", f"分享 {code} 跳过：{dedup_reason}")
        return

    # 2. 内容黑名单：文件名 + 体积（放行任务跳过）
    if not job.get("bypass_filters"):
        blocked, rule_type, rule_value = filters.check_files(
            [f.get("fn", "") for f in snap["files"]], snap["total_size"])
        if blocked:
            filters.record_hit(job["channel"], job["message_id"], title, code,
                               rule_type, rule_value, "share_info")
            db.execute("UPDATE transfers SET status='blocked', title=?, size=?, file_count=?, "
                       "error=? WHERE id=?",
                       (title, snap["total_size"], len(snap["files"]),
                        f"{rule_type}:{rule_value}", job["id"]))
            logstream.warn("transfer", f"分享 {code} 被内容黑名单拦截（{rule_type}:{rule_value}）")
            return

    # 3. 确保目标目录存在（上面解析成功则直接复用）
    if not cid:
        cid = pan.mkdirs(target)
    if not cid:
        raise RuntimeError(f"目标目录无效: {target}")

    # 4. 转存（115 对重复文件会报"已存在"，同样视为已在目标目录）
    # 分享文件字段可能因接口形态不同而缺失（fid/file_id），统一过滤后再提交，
    # 避免旧的直取 f["fid"] 在字段不匹配时抛出 KeyError（历史线上报错 'fid'）。
    file_total = len(snap["files"])
    flat_ids = [str(f.get("fid")) for f in snap["files"] if f.get("fid")]
    # 优先按分享顶层条目整包转存：分享是文件夹（剧集/合集）时保留目录结构，
    # 与 115 网页的“转存”一致；逐个子文件 id 提交在大量文件时（如 5 季 100 集、
    # 316GB 的剧集分享）容易被 115 拒绝或只转存一部分。
    top_ids = [str(i) for i in (snap.get("top_ids") or []) if i]
    file_ids = top_ids or flat_ids
    if not file_ids:
        raise RuntimeError("分享内文件缺少有效的文件 id（115 接口返回字段异常），需要重试")
    # 3.8 重试防重：此前尝试可能已实际落盘（接口成功但反查超时/落库延迟）。
    # 按分享的真实文件名预检目标目录，命中则跳过提交——实测 115 对重试任务的
    # 再接收会放行并生成 "(1)" 副本，浪费空间；文件已在即视为成功，不再提交。
    skip_receive = False
    if job.get("attempts", 0):
        try:
            existing = pan.list_file_names(cid)
        except Exception as e:
            # 修复漏洞 1：网络抖动导致无法读取目录确认状态时，绝不盲目二次提交导致生成 (1) 副本；
            # 抛出重试异常进入退避，等待网络恢复后再安全校验
            logstream.warn("transfer", f"重试预检目标目录网络异常（稍后重试，避免盲目提交产生 (1) 副本）: {e}")
            raise RuntimeError(f"重试预检目标目录网络异常（防重复转存拦截）: {e}")
        existing_n = {_norm_filename(n) for n in existing}
        pre = [f.get("fn") for f in snap["files"][:10] if f.get("fn")]
        pre += (snap.get("top_names") or [])[:10]
        pre = [n for n in dict.fromkeys(pre) if n]
        if pre and any(_norm_filename(n) in existing_n for n in pre):
            skip_receive = True
            logstream.info("transfer",
                           f"分享 {code} 目标目录已存在本次分享的文件（此前尝试已落盘），跳过重复提交")

    confirmed = skip_receive  # 预检命中 = 已确认文件就在目标目录
    received_before = False
    if not skip_receive:
        try:
            pan.receive(code, rc, cid, file_ids)
        except pan.Pan115Error as e:
            msg = str(e)
            if any(m in msg for m in _DEAD_SHARE_MARKERS):
                # 提交阶段也可能报链接失效/过期：确定性失败，不重试不计熔断
                raise PermanentTransferError(f"分享链接已失效（{msg[:120]}）") from e
            if "已接收" in msg:
                # 115 幂等信号："文件已接收，无需重复接收"——本账号此前已接收过该分享，
                # 文件已在网盘中。直接按成功处理：不反查、不找回、不重试。
                received_before = True
                confirmed = True
                logstream.info("transfer", f"分享 {code} 115 提示此前已接收，文件已在网盘中，按成功处理")
            elif "存在" in msg:
                # "文件已存在"：目标目录已有同名文件 → 走反查确认
                logstream.info("transfer", f"分享 {code} 文件已存在于目标目录，视为已转存")
            elif top_ids and flat_ids and set(flat_ids) != set(top_ids):
                # 极少数接口形态不接受目录 id：退回逐文件提交
                logstream.warn("transfer",
                               f"分享 {code} 顶层整包转存被拒（{msg[:120]}），改为逐文件提交")
                pan.receive(code, rc, cid, flat_ids)
            else:
                raise

    # 5. 双重确认：反查目标目录，确认文件真实到达（防接口误报）。
    # 已确认文件在目标目录的（重试预检命中 / 已接收信号）除外。
    if not confirmed and not _verify_receive(cid, snap):
        # 「必须要存进去」兜底：文件可能被接收到了网盘其他位置（如早前用其他方式
        # 转存过同一分享、或分类调整过）。全盘搜索后用「复制」找回到目标目录——
        # 不动原文件，多一份也无妨。
        _recover_misplaced(cid, snap)
        if not _verify_receive(cid, snap):
            raise RuntimeError("转存接口已返回成功，但反查目标目录未查到文件（多为 115 落库延迟，"
                               "已尝试全盘找回，将自动重试）；多次重试仍失败时，"
                               "请到 115 网页搜索文件名或检查回收站")

    changed = db.execute("UPDATE transfers SET status='success', title=?, size=?, file_count=?, "
                         "finished_at=?, running_at=NULL, next_retry_at=NULL"
                         " WHERE id=? AND status='running'",
                         (title, snap["total_size"], file_total, time.time(), job["id"]))
    if changed == 0:
        # 看门狗已把本任务重新排队（或已被人工终止）：旧线程迟到返回不能再改终态，
        # 更不能二次计额度/二次通知，否则会与新一轮执行重复转存、重复扣额度。
        logstream.warn("transfer",
                       f"任务 #{job['id']} 已不在 running 状态，忽略本次迟到成功回写（防重复计额）")
        return
    _invalidate_dir_cache(cid)
    db.quota_incr()
    pan.note_115_ok()  # 完整成功：风控退避逐级恢复
    logstream.info("transfer",
                   f"转存成功: {title[:50]} → {target}（{file_total} 个文件, "
                   f"{snap['total_size'] / (1 << 30):.2f} GB）")
    jobs.notify_transfer(title, job.get("category", ""), job.get("channel", ""),
                         target, size=snap["total_size"], kind="share",
                         file_count=file_total)
    # 说明：转存后统一通过「整理刮削」队列互斥执行整理重命名，此处不再直接并发调 organize_seasons。


def _is_generic_name(name: str) -> bool:
    """判断是否为无特异性短名称或通用单集名（如 1.mp4, 01.mp4, cover.jpg 等）。
    全盘搜索这类名称极易匹配到网盘内其他影视资源导致误复制冲突生成 (1) 副本。"""
    base = re.sub(r"\.[^.]+$", "", name).strip().lower()
    if len(base) <= 3 or base.isdigit():
        return True
    if re.fullmatch(r"(?:ep?|s\d{1,2}e?|第)?\d{1,4}(?:集|话|話)?", base):
        return True
    if base in {"cover", "poster", "folder", "fanart", "sample", "trailer", "thumb", "cd1", "cd2"}:
        return True
    return False


def _recover_misplaced(cid: int, snap: dict) -> int:
    """反查失败后的兜底（「必须要存进去」的最后手段）。

    按文件名全盘搜索本分享的文件：若被接收到了其他目录（早前转存过同一分享、
    手动操作移动等），用「复制」找回到目标目录——不移动、不删除原文件，
    允许重复，只求文件必须出现在目标目录。返回复制的文件数。
    搜索/复制之间留小间隔，与库内其他 115 调用的防风控口径一致。
    """
    # 严格过滤无特征通用文件名（如 1.mp4, 01.mp4），防止全盘搜索抓取到其他目录的同名剧集
    sample = [f.get("fn") for f in snap["files"][:5] if f.get("fn") and not _is_generic_name(f.get("fn"))]
    sample += [n for n in (snap.get("top_names") or [])[:3] if n and not _is_generic_name(n)]
    sample = [n for n in dict.fromkeys(sample) if n][:5]
    copied = 0
    seen_ids: set[str] = set()
    searched = False
    for name in sample:
        if searched:
            time.sleep(0.5)  # 多次搜索之间留间隔，防风控
        searched = True
        try:
            hits = pan.search_file(name)
        except Exception as e:
            logstream.warn("transfer", f"全盘搜索「{name[:40]}」失败，跳过找回: {e}")
            continue
        for h in hits:
            if h["parent"] == str(cid) or h["id"] in seen_ids:
                continue  # 已在目标目录（多为列表延迟）或已处理过
            if copied:
                time.sleep(0.3)  # 多次复制之间留间隔，防风控
            try:
                pan.copy_files_to([h["id"]], cid)
                seen_ids.add(h["id"])
                copied += 1
                logstream.info("transfer",
                               f"在网盘其他位置找到「{h['name'][:50]}」，已复制一份到目标目录")
            except Exception as e:
                logstream.warn("transfer", f"复制「{h['name'][:50]}」到目标目录失败: {e}")
        if copied >= 50:
            break  # 单次找回上限，防异常数据触发海量复制；剩余交给下一轮重试
    return copied


def _verify_receive(cid: int, snap: dict) -> bool:
    """反查目标目录：抽查分享条目是否真实到达。

    115 转存落库有延迟，这里轮询几次再判定；候选名同时包含分享展开后的文件名
    和分享顶层条目名（分享以文件夹整体转存时，目标目录里只有顶层文件夹名）。
    验证接口异常时按 API 成功处理，避免把接口抖动误判成转存失败。
    """
    names = [f.get("fn") for f in snap["files"][:10] if f.get("fn")]
    names += [n for n in (snap.get("top_names") or [])[:10] if n]
    names = list(dict.fromkeys(names))  # 去重：同一文件可能同时出现在文件列表与顶层条目里
    if not names:
        return True
    delays = (2, 4, 8)
    try:
        for i, delay in enumerate(delays):
            time.sleep(delay)
            got = pan.list_file_names(cid)
            if any(n in got for n in names):
                return True
            if i == len(delays) - 1:
                logstream.warn("transfer",
                               f"转存接口已返回成功，但目标目录里暂时还没看到本次分享的文件"
                               f"（多为 115 落库延迟，将自动重试确认，无需处理）；"
                               f"目录现有条目示例: {list(got)[:5] or '（空目录）'}；"
                               f"正在查找的文件名: {names[:3]}")
    except Exception as e:
        logstream.warn("transfer", f"目录反查失败（按 API 成功处理）: {e}")
        return True
    return False


def _backoff(attempts: int) -> float:
    """按尝试次数取退避秒数；配置为空/非法时使用默认阶梯，避免 IndexError。"""
    raw = db.get_setting("retry_backoff", [60, 300, 1800])
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        values = [float(raw)] if raw > 0 else []
    elif isinstance(raw, (list, tuple)):
        values = []
        for v in raw:
            if isinstance(v, bool):
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if f > 0:
                values.append(f)
    else:
        values = []
    if not values:
        values = [60.0, 300.0, 1800.0]
    return int(values[min(max(attempts, 1) - 1, len(values) - 1)])


def _fail(job: dict, err: str):
    attempts = job["attempts"] + 1
    if attempts >= MAX_ATTEMPTS:
        db.execute("UPDATE transfers SET status='failed', attempts=?, error=? WHERE id=?"
                   " AND status='running'",
                   (attempts, err[:500], job["id"]))
        logstream.error("transfer", f"分享 {job['share_code']} 转存失败（已达最大重试）: {err}")
        kind = job.get("kind", "share")
        jobs.notify_failure(
            kind, (classifier.headline(job.get("title", "")) or job.get("title", "")
                   or job["share_code"]), job.get("category", ""),
            job.get("channel", ""), err[:200],
            retry_info=f"💡 已自动重试 {attempts} 次仍未成功，请检查分享链接是否失效")
    else:
        delay = _backoff(attempts)
        changed = db.execute("UPDATE transfers SET status='queued', attempts=?, error=?, next_retry_at=?"
                             " WHERE id=? AND status='running'",
                             (attempts, err[:500], time.time() + delay, job["id"]))
        if changed == 0:
            return  # 已被看门狗重排/人工终止：不再覆盖当前状态
        logstream.warn("transfer",
                       f"分享 {job['share_code']} 第 {attempts} 次失败，{delay} 秒后重试：{err}")


def _retry_scan():
    """把到期的重试任务重新置为可执行（由 fetch 领取）。"""
    db.execute("UPDATE transfers SET next_retry_at=NULL WHERE status='queued' AND next_retry_at<=?",
               (time.time(),))


def _norm_filename(s: str) -> str:
    """文件名比对归一化（去空白 + 小写）：115 入库可能微调文件名，
    精确比对会把已落盘的文件误判成新文件导致重复转存。"""
    return re.sub(r"\s+", "", (s or "")).lower()


def _fetch_next():
    """领取下一个转存任务：优先级高的先跑（向前补全 priority=1，优先消耗每日额度），
    同级按入队先后（FIFO）。"""
    _retry_scan()
    return db.query(
        "SELECT * FROM transfers WHERE status='queued' AND (next_retry_at IS NULL OR next_retry_at<=?)"
        " ORDER BY priority DESC, id ASC LIMIT 1", (time.time(),), one=True)


def skip_if_disabled(job: dict) -> bool:
    """功能开关检查：任务所属类型被关闭时标记 skipped。返回是否已跳过。"""
    kind = job.get("kind", "share")
    enabled = db.get_setting("enable_offline_download" if kind == "offline"
                             else "enable_share_transfer", True)
    if enabled:
        return False
    label = "云下载功能已关闭" if kind == "offline" else "分享转存功能已关闭"
    db.execute("UPDATE transfers SET status='skipped', error=? WHERE id=? AND status='queued'",
               (label, job["id"]))
    logstream.info("transfer", f"任务 {job['share_code']} 跳过：{label}")
    return True


async def _heartbeat(job_id: int):
    """长任务心跳：每 60 秒刷新 running_at（仅当任务仍为 running），看门狗据此判断是否真卡死。"""
    while True:
        await asyncio.sleep(60)
        await asyncio.to_thread(
            db.execute,
            "UPDATE transfers SET running_at=? WHERE id=? AND status='running'",
            (time.time(), job_id),
        )


def _in_schedule_window() -> bool:
    """定时计划：开启后仅允许时段内转存与云下载（由 transfer_gate 统一判定）。"""
    return transfer_gate.is_in_schedule_window()


def _poll_worker_gates():
    """在线程池中集中读取工作器调度所需的设置与额度状态，避免事件循环争抢 SQLite 锁。"""
    running = bool(db.get_setting("running", False))
    paused = bool(db.get_setting("paused", False))
    sched_on = bool(db.get_setting("schedule_enabled", False))
    in_win = _in_schedule_window()
    jitter_min = int(float(db.get_setting("schedule_jitter_min", 5) or 0))
    limit = max(1, int(_num_setting("daily_limit", 50)))
    quota = db.quota_today() if limit > 0 else 0
    waiting = (
        db.query("SELECT count(*) c FROM transfers WHERE status='queued'", one=True)["c"]
        if (limit > 0 and quota >= limit) else 0
    )
    return running, paused, sched_on, in_win, jitter_min, limit, quota, waiting


def _claim_next_job():
    """扫描重试并发原子抢占下一个可执行任务；已禁用类型则直接跳过并返回哨兵值。"""
    _retry_scan()
    job = _fetch_next()
    if not job:
        return None
    job = dict(job)
    if skip_if_disabled(job):
        return "skipped"
    changed = db.execute(
        "UPDATE transfers SET status='running', running_at=?, error='', next_retry_at=NULL"
        " WHERE id=? AND status='queued'",
        (time.time(), job["id"]),
    )
    if changed == 0:
        return "race"
    job["status"] = "running"
    return job


async def queue_worker():
    logstream.info("transfer", "转存工作器启动（等待「开始运行」）")
    consecutive_fails = 0
    consecutive_success = 0
    schedule_logged = False
    pause_logged = False
    stopped_logged = False
    last_schedule_active = False
    while True:
        try:
            # 互斥门闸：若文件整理正在运行，暂停转存并在此挂起等待整理完成
            await transfer_gate.wait_transfer_worker_allowed()

            running, paused, sched_on, in_win, jitter_min, limit, quota, waiting = \
                await asyncio.to_thread(_poll_worker_gates)

            # 总开关：用户未点击「开始运行」前不消费队列
            if not running:
                if not stopped_logged:
                    logstream.info("transfer", "系统未启动（向导完成后点击网页顶栏「开始运行」）")
                    stopped_logged = True
                await asyncio.sleep(5)
                continue
            stopped_logged = False

            # 全局暂停（TG /pause 或设置）
            if paused:
                if not pause_logged:
                    logstream.warn("transfer", "转存已暂停（TG 发送 /resume 恢复）")
                    pause_logged = True
                await asyncio.sleep(10)
                continue
            pause_logged = False

            # 定时计划窗口检查
            if not in_win:
                last_schedule_active = False
                if not schedule_logged:
                    logstream.info("transfer", "当前不在允许转存时段，新任务暂不入队"
                                               "（可在设置页调整或关闭定时计划）")
                    schedule_logged = True
                await asyncio.sleep(60)
                continue
            schedule_logged = False

            # 从「时段外」刚进入「时段内」时触发拟人化开工随机扰动（防整点脚本特征）
            if sched_on and not last_schedule_active:
                last_schedule_active = True
                if jitter_min > 0:
                    jitter_sec = random.randint(15, max(15, jitter_min * 60))
                    logstream.info("transfer", f"⏰ 进入允许转存时段，开启拟人化开工随机扰动（等待 {jitter_sec} 秒后开始处理队列）...")
                    await asyncio.sleep(jitter_sec)
            elif not sched_on:
                last_schedule_active = False

            # 旧库可能存过 0（旧的「不限制」语义）：现约定限额最小 1，兜底夹紧
            if limit > 0 and quota >= limit:
                logstream.warn("transfer",
                               f"已达每日限额 {limit}（分享+云下载共享额度），剩余 {waiting} 个任务保留在队列，"
                               "明日 0 点额度重置后自动接续转存")
                await asyncio.sleep(600)
                continue

            # 领取任务前再次确认互斥门闸
            await transfer_gate.wait_transfer_worker_allowed()
            claimed = await asyncio.to_thread(_claim_next_job)
            if claimed is None:
                await asyncio.sleep(5)
                continue
            if claimed == "skipped":
                await asyncio.sleep(1)
                continue
            if claimed == "race":
                await asyncio.sleep(0.5)
                continue
            job = claimed

            # cookie 检查（不消耗重试次数：长时间失效不应把排队任务打成 failed）
            st = await pan.run_pan_thread(pan.check_login)
            if not st.get("ok"):
                err = f"115 未登录或 Cookie 失效：{st.get('err', '')}"
                await asyncio.to_thread(
                    db.execute,
                    "UPDATE transfers SET status='queued', error=?, next_retry_at=?"
                    " WHERE id=? AND status='running'",
                    (err[:500], time.time() + 60, job["id"]),
                )
                logstream.warn("transfer",
                               f"115 Cookie 失效，任务 #{job['id']} 暂缓 60 秒（不计入重试次数）")
                await asyncio.sleep(60)
                continue

            # 长任务心跳：转存在线程池中运行且无法取消，心跳确保 running_at 持续刷新，
            # 避免看门狗把仍在正常推进的大分享误判为卡死并重排（进而重复转存）
            hb = asyncio.create_task(_heartbeat(job["id"]))
            transfer_gate.mark_transfer_active(True)
            try:
                await pan.run_pan_thread(_process_one, job)
                consecutive_fails = 0
                consecutive_success += 1

                # 拟人化连存小憩机制
                def _batch_rest_cfg():
                    en = bool(db.get_setting("batch_rest_enabled", True))
                    cnt = max(1, int(float(db.get_setting("batch_rest_count", 3) or 3)))
                    min_m = max(1, int(float(db.get_setting("batch_rest_min_min", 2) or 2)))
                    max_m = max(min_m, int(float(db.get_setting("batch_rest_max_min", 5) or 5)))
                    return en, cnt, min_m, max_m

                rest_on, rest_count, min_m, max_m = await asyncio.to_thread(_batch_rest_cfg)
                if rest_on:
                    if consecutive_success >= rest_count:
                        consecutive_success = 0
                        rest_sec = random.randint(min_m * 60, max_m * 60)
                        logstream.info("transfer", f"☕ 已连续完成 {rest_count} 笔转存，触发拟人化小憩（休息 {rest_sec // 60} 分 {rest_sec % 60} 秒后继续）...")
                        await asyncio.sleep(rest_sec)
            except PermanentTransferError as e:
                # 确定性失败（格式不支持/地址无效等）：不重试，立即失败并通知
                await asyncio.to_thread(
                    db.execute,
                    "UPDATE transfers SET status='failed', attempts=?, error=? WHERE id=?"
                    " AND status='running'",
                    (job["attempts"] + 1, str(e)[:500], job["id"]),
                )
                logstream.error("transfer", f"任务永久失败: {str(e)}")
                pan.note_115_error(str(e))
                await asyncio.to_thread(
                    jobs.notify_failure, job.get("kind", "share"),
                    classifier.headline(job.get("title", "")) or job.get("title", ""),
                    job.get("category", ""), job.get("channel", ""),
                    str(e), retry_info="💡 该链接无法被 115 处理，不会自动重试")
                consecutive_fails = 0  # 任务自身问题不计入熔断
            except Exception as e:
                consecutive_fails += 1
                consecutive_success = 0
                pan.note_115_error(str(e))
                await asyncio.to_thread(_fail, job, str(e))
                if consecutive_fails >= 5:
                    logstream.error("transfer", "连续失败 5 次，暂停转存 30 分钟")
                    await asyncio.sleep(1800)
                    consecutive_fails = 0
            finally:
                transfer_gate.mark_transfer_active(False)
                hb.cancel()

            await asyncio.sleep(await asyncio.to_thread(_interval))
        except Exception as e:
            logstream.error("transfer", f"工作器异常: {logstream.exc(e)}")
            await asyncio.sleep(30)
