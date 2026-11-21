"""转存队列门闸机制、链接校验、空间预检、同名去重与洗版归档。"""
from __future__ import annotations

import json
import random
import re
import time

from .. import database as db
from ..core import logstream
from ..pan115 import client as pan
from . import jobs


def _qw():
    """延迟获取 queue_worker 门面，避免循环导入并确保单测 monkeypatch 生效。"""
    from . import queue_worker
    return queue_worker


# ---- 空间门闸参数（空间告急属于账号级环境问题：挂起队列，而不是把任务逐条判失败）----
SPACE_GUARD_RATIO = 90.0          # 使用率 ≥90% 挂起队列（与网页空间条变红同阈值）
SPACE_GUARD_RESUME_RATIO = 88.0   # 回落到 <88% 才恢复（2% 回差，防止在阈值附近反复暂停/恢复）
SPACE_GUARD_MIN_FREE = 10 << 30   # 剩余 <10GB 同样视为告急（任何高清单集都存不下）
SPACE_PROBE_TTL = 300             # 空间门禁探测本地缓存秒数（优先读取内存缓存，避免空闲循环打 115）
_SPACE_PROBE = {"ts": 0.0, "blocked": False, "reason": "", "ratio": 0.0, "free": 0}
_SPACE_MARKERS = ("空间不足", "存储空间不足", "空间已满", "容量不足",
                  "not enough space", "insufficient storage", "no space left", "storage full")


class PermanentTransferError(Exception):
    """确定性失败（如链接格式不支持），不重试，直接失败并通知。"""


# 分享/链接已死亡的 115 错误特征：确定性失败，重试无意义也不计入熔断。
# 覆盖 snap（读取分享）与 receive（转存提交）两个阶段可能返回的文案
_DEAD_SHARE_MARKERS = ("链接已失效", "链接已过期", "链接不存在",
                       "分享不存在", "分享已取消", "分享已删除", "分享已过期",
                       "分享已屏蔽", "分享已被取消", "分享链接已失效")

# 需要访问码/提取码但拿不到（消息里没给或给错）的 115 错误特征：
# 同一链接重试结果完全一致，属于确定性失败，直接判失败，不占用 5 次重试额度
_NO_RETRY_SHARE_MARKERS = ("请输入访问码", "访问码错误", "提取码错误",
                           "访问码不正确", "提取码不正确")


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
    if url.startswith("magnet:?"):
        # xt 参数不一定在最前（如 magnet:?dn=xx&xt=urn:btih:…），全文检索 hash
        m = re.search(r"xt=urn:btih:([0-9A-Za-z]+)", url, re.IGNORECASE)
        if m and (re.fullmatch(r"[0-9a-fA-F]{40}", m.group(1))
                  or re.fullmatch(r"[A-Za-z2-7]{32}", m.group(1))):
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


def _is_space_error(err: str) -> bool:
    """判断错误是否为 115 空间不足（账号级环境问题，重试必然继续失败）。"""
    s = (err or "").lower()
    return any(k in s for k in _SPACE_MARKERS)


def _space_gate() -> tuple[bool, str]:
    """空间门闸：115 使用率过高时挂起整个队列（任务保持 queued，不判失败、不计重试）。

    带 2% 回差（90% 暂停 / 88% 恢复），避免在临界点反复暂停与恢复；
    探测失败沿用上次状态，不因 115 短暂异常让队列停摆。
    """
    if not bool(db.get_setting("space_guard_enabled", True)):
        # 用户关掉门闸：顺带清掉挂起标记，避免顶栏残留「空间不足」
        if db.get_setting("space_blocked", False):
            db.set_setting("space_blocked", False)
            db.set_setting("space_blocked_reason", "")
        return False, ""
    now = time.time()
    if now - _SPACE_PROBE["ts"] < SPACE_PROBE_TTL:
        return _SPACE_PROBE["blocked"], _SPACE_PROBE["reason"]
    blocked, reason, ratio, free = False, "", 0.0, 0
    try:
        sp = pan.space_info()
        total = int(sp.get("total") or 0)
        use = int(sp.get("use") or 0)
        free = int(sp.get("free") or 0)
        if total > 0:
            ratio = use * 100.0 / total
            if bool(db.get_setting("space_blocked", False)):
                # 已挂起：只有同时低于恢复线且剩余空间充足才解除
                blocked = not (ratio < SPACE_GUARD_RESUME_RATIO
                               and free >= SPACE_GUARD_MIN_FREE)
            else:
                blocked = ratio >= SPACE_GUARD_RATIO or 0 < free < SPACE_GUARD_MIN_FREE
            if blocked:
                reason = f"已用 {ratio:.1f}%，剩余 {free / (1 << 30):.2f} GB"
    except Exception:
        _SPACE_PROBE["ts"] = now
        return (bool(db.get_setting("space_blocked", False)),
                str(db.get_setting("space_blocked_reason", "") or ""))
    _SPACE_PROBE.update(ts=now, blocked=blocked, reason=reason, ratio=ratio, free=free)
    return blocked, reason


def _fmt_gb(b: int) -> str:
    return f"{b / (1 << 30):.2f}GB"


def _space_preflight(total_size: int, title: str = "") -> bool:
    """空间预检：115 剩余空间不足时跳过任务并告警（转存前拦截，而不是失败后被动发现）。

    返回 True = 空间足够（或无法判定，放行）；False = 明显不足，任务打 skipped。
    多留 2%（最少 1GB）抖动余量：与 115 判定刚好卡满时更容易触发失败。
    """
    if total_size <= 0 or not bool(db.get_setting("space_guard_enabled", True)):
        return True
    try:
        sp = pan.space_info()
    except Exception:
        return True  # 容量接口失败不阻塞转存
    free = int(sp.get("free") or 0)
    if free <= 0:
        return True
    reserve = max(1 << 30, total_size // 50)
    if total_size + reserve > free:
        jobs.notify_failure("share", title, "", "", "",
                            retry_info=f"💡 115 剩余空间不足（剩余 {_fmt_gb(free)}，"
                                       f"需要 {_fmt_gb(total_size)} + 预留 {_fmt_gb(reserve)}），"
                                       "本条已跳过且不重试；清理空间后可重新提交")
        return False
    return True


def _norm_filename(s: str) -> str:
    """文件名比对归一化（去空白 + 小写）：115 入库可能微调文件名，
    精确比对会把已落盘的文件误判成新文件导致重复转存。"""
    return re.sub(r"\s+", "", (s or "")).lower()


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
    cache_dict = getattr(_qw(), "_dir_cache", _dir_cache)
    if ttl > 0 and cid in cache_dict:
        ts, cached = cache_dict[cid]
        if now - ts < ttl:
            return cached
    names = pan.list_file_names(cid, limit=5000)
    if ttl > 0:
        # 防长期运行内存缓慢增长：超限时先清过期项，仍超限则整体重建
        if len(cache_dict) > 512:
            for k in [k for k, (ts, _) in cache_dict.items() if now - ts >= ttl]:
                cache_dict.pop(k, None)
            if len(cache_dict) > 512:
                cache_dict.clear()
        cache_dict[cid] = (now, names)
    return names


def _add_to_dir_cache(cid: int, name: str):
    cache_dict = getattr(_qw(), "_dir_cache", _dir_cache)
    if cid in cache_dict:
        cache_dict[cid][1].add(name)


def _invalidate_dir_cache(cid: int):
    """转存落盘后主动失效该目录缓存，防并发或连续转存时的假阴性脏读。"""
    cache_dict = getattr(_qw(), "_dir_cache", _dir_cache)
    cache_dict.pop(cid, None)


def _assert_still_running(job: dict):
    """看门狗重排守卫：任务已被重新排队/终止时抛错中止旧线程。

    DB 层的 `WHERE status='running'` 守卫只能防止旧线程重复回写与重复计额，
    防不了旧线程从挂死请求中恢复后继续执行真实 115 写操作（重复 receive 会生成
    "(1)" 副本）。在每次真实写操作前用本检查提前退出。
    """
    row = db.query("SELECT status FROM transfers WHERE id=?", (job["id"],), one=True)
    if row is None or row["status"] != "running":
        raise RuntimeError(f"任务 #{job['id']} 已被重新调度（看门狗重排或人工操作），旧线程中止执行")


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
        names = _qw()._get_dir_names_cached(cid) or []
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
                    except Exception as e:
                        logstream.warn("transfer", f"同名去重账本查询失败（本次跳过账本去重）: {e}")

    return ""


def _archive_washed_version(job: dict):
    """洗版收尾：把被替代的旧版顶层条目移入归档目录（不删除，可随时人工恢复）。

    只在新版成功落盘后执行；任何异常只记日志，绝不影响新任务的成功状态。
    找不到旧版定位记录 / 目录已变更时放弃移动，避免跨目录误移用户文件。
    """
    try:
        old = db.query("SELECT id, dest_cid, dest_ids FROM transfers WHERE id=?",
                       (job.get("wash_from"),), one=True)
        if not old:
            return
        archive_dir = str(db.get_setting("wash_archive_dir", "") or "").strip()
        if not archive_dir:
            logstream.warn("transfer", f"洗版完成：未配置旧版归档目录，旧版 #{old['id']} 保留在原处")
            return
        try:
            ids = [str(i) for i in json.loads(old["dest_ids"] or "[]")]
        except (TypeError, ValueError):
            ids = []
        old_cid = int(old["dest_cid"] or 0)
        if not ids or not old_cid:
            logstream.warn("transfer",
                           f"洗版完成：旧版 #{old['id']} 缺少落盘定位记录，未自动移动（请手动清理）")
            return
        # 只移动仍位于原目录的条目：用户已手动挪走/删除的不跨目录误移
        present = {str(e["id"]) for e in pan.list_entries(old_cid, limit=3000)}
        move_ids = [i for i in ids if i in present]
        if not move_ids:
            logstream.info("transfer", f"洗版完成：旧版 #{old['id']} 条目已不在原目录，无需移动")
            return
        arch_cid = pan.mkdirs(archive_dir)
        if not arch_cid:
            logstream.warn("transfer", f"洗版完成：无法创建归档目录 {archive_dir}，旧版保留在原处")
            return
        pan.move_entries(move_ids, arch_cid)
        _qw()._invalidate_dir_cache(old_cid)  # 目录内容已变化，避免后续同名去重读到过期缓存
        db.execute("UPDATE transfers SET error=? WHERE id=? AND (error IS NULL OR error='')",
                   (f"已洗版：旧版本移入 {archive_dir}", old["id"]))
        logstream.info("transfer",
                       f"洗版完成：旧版 #{old['id']} 共 {len(move_ids)} 个条目已移入 {archive_dir}")
    except Exception as e:
        logstream.warn("transfer", f"洗版收尾失败（新版已成功，不影响任务状态）: {e}")


def _verify_receive(cid: int, snap: dict, found: dict | None = None) -> bool:
    """反查目标目录：抽查分享条目是否真实到达。

    115 转存落库有延迟，这里轮询几次再判定；候选名同时包含分享展开后的文件名
    和分享顶层条目名（分享以文件夹整体转存时，目标目录里只有顶层文件夹名）。
    验证接口异常时按 API 成功处理，避免把接口抖动误判成转存失败。
    found 传入字典时回填命中的顶层条目 id（洗版定位旧版用）。
    """
    names = [f.get("fn") for f in snap["files"][:10] if f.get("fn")]
    names += [n for n in (snap.get("top_names") or [])[:10] if n]
    names = list(dict.fromkeys(names))  # 去重：同一文件可能同时出现在文件列表与顶层条目里
    if not names:
        return True
    delays = (2, 5, 8)
    wanted = {_norm_filename(n) for n in names if _norm_filename(n)}
    try:
        for i, delay in enumerate(delays):
            time.sleep(delay)
            got = pan.list_file_names(cid)
            got_norm = {_norm_filename(n) for n in got}
            if any(_norm_filename(n) in got_norm for n in names):
                if found is not None:
                    try:
                        entries = pan.list_entries(cid, limit=2000)
                        found["ids"] = [str(e["id"]) for e in entries
                                        if _norm_filename(e["name"]) in wanted][:200]
                    except Exception as e:
                        logstream.warn("transfer", f"记录落盘条目 id 失败（不影响转存结果）: {e}")
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
        values = [60.0, 300.0, 1800.0]
    if not values:
        values = [60.0, 300.0, 1800.0]
    idx = min(max(0, attempts - 1), len(values) - 1)
    return values[idx]


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
