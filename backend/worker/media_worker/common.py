"""媒体整理与刮削公共基础设施：异常、配置读取、防风控节流、历史转存提示与过滤规则。"""
from __future__ import annotations

import asyncio
import json
import random
import time

from ... import database as db
from ...core import logstream, transfer_gate
from ...core.media_parser import ParsedMedia, parse_media_filename
from ...pan115.client import normalize_title

def _mw():
    """延迟获取 media_worker 包门面，避免循环导入并保证测试 patch 透明生效。"""
    from .. import media_worker
    return media_worker


STATUS_ZH = {
    "queued": "排队执行中",
    "running": "正在处理",
    "need_confirm": "待确认",
    "staged": "已选待提交",
    "done": "已完成",
    "skipped": "已跳过",
    "failed": "执行失败",
    "ignored": "已忽略",
}


class MediaCancelled(Exception):
    """收到顶栏停止指令，安全中止后续队列条目。"""


class _RiskCircuitBreak(Exception):
    """连续多次触发 115 风控征兆时安全熔断本批写入。"""


def _is_risk_error(err: Exception | str) -> bool:
    """判断异常文本是否命中 115 风控限制关键词。"""
    s = str(err or "").lower()
    if not s:
        return False
    kws = getattr(_mw().client, "_RISK_KEYWORDS", ("频繁", "风控", "验证码", "IP登录异常", "too many", "limit", "405"))
    return any(str(k).lower() in s for k in kws)


def _get_lock(name: str) -> asyncio.Lock:
    lk = _mw()._TASK_LOCKS.get(name)
    if lk is None:
        lk = asyncio.Lock()
        _mw()._TASK_LOCKS[name] = lk
    return lk


def is_system_running() -> bool:
    return str(db.get_setting("running", False)).lower() == "true"


def get_media_settings() -> dict:
    """读取整理刮削相关全部设置项。"""
    keys = (
        "tmdb_api_key",
        "media_append_tmdb_tag",
        "media_organize_template_movie",
        "media_organize_template_tv",
        "media_conflict_policy",
        "media_move_subtitles",
        "media_skip_extras",
        "media_extra_min_mb",
        "media_clean_empty_dirs",
        "media_scrape_confidence_threshold",
        "media_scrape_margin_threshold",
        "media_scrape_write_local_nfo",
        "media_scrape_download_poster",
        "media_scrape_wait_seconds",
        "media_local_meta_dir",
        "media_op_delay_min",
        "media_op_delay_max",
        "media_organize_schedule_enabled",
        "media_scrape_schedule_enabled",
        "media_organize_schedule_type",
        "media_organize_schedule_hours",
        "media_organize_fixed_times",
    )
    out = {}
    for k in keys:
        out[k] = db.get_setting(k, db.DEFAULT_SETTINGS.get(k))
    ft_raw = out.get("media_organize_fixed_times")
    if isinstance(ft_raw, str):
        try:
            out["media_organize_fixed_times"] = json.loads(ft_raw)
        except Exception:
            out["media_organize_fixed_times"] = ["06:00", "12:00", "18:00"]
    elif not isinstance(ft_raw, list):
        out["media_organize_fixed_times"] = ["06:00", "12:00", "18:00"]
    return out


def _check_not_cancelled():
    if transfer_gate.is_cancel_requested() or not is_system_running():
        raise MediaCancelled("系统已停止运行，整理/刮削队列安全中断")


async def _pacing_sleep(settings: dict) -> None:
    """防风控随机退避延时，并按步长感知取消信号。"""
    _check_not_cancelled()
    try:
        dmin = max(0.2, float(settings.get("media_op_delay_min", 0.8)))
        dmax = max(dmin, float(settings.get("media_op_delay_max", 2.0)))
    except (TypeError, ValueError):
        dmin, dmax = 0.8, 2.0
    wait = random.uniform(dmin, dmax) * max(1.0, _mw().client.pace_factor())
    end = time.monotonic() + wait
    while time.monotonic() < end:
        _check_not_cancelled()
        await asyncio.sleep(min(0.4, max(0.05, end - time.monotonic())))


async def _interruptible_batch_rest(
    settings: dict,
    op_counter: int,
    log_mod: str,
    every: int = 15,
    rest_sec: float = 12.0,
) -> None:
    """每连续写入 N 次进行一次可中断长休眠，降低批量操作触发 115 风控的概率。"""
    if op_counter <= 0 or op_counter % every != 0:
        return
    try:
        cfg_max = float(settings.get("media_op_delay_max", 2.0) or 0)
    except (TypeError, ValueError):
        cfg_max = 2.0
    # 测试环境配置极小延迟时跳过长休眠，保持单测快速执行
    if cfg_max < 0.2:
        return
    logstream.info(log_mod, f"☕ 已连续完成 {op_counter} 次 115 写操作，礼貌休眠 {rest_sec:.0f}s 避风控...")
    end = time.monotonic() + rest_sec
    while time.monotonic() < end:
        _check_not_cancelled()
        await asyncio.sleep(min(0.4, max(0.05, end - time.monotonic())))


def _load_db_transfer_hints() -> tuple[dict[str, dict], dict[str, dict]]:
    """加载已转存记录与追更订阅作为第三层解析线索。

    返回 `(by_norm_title, by_sub_id)`，供 `parse_media_context` 快速匹配。
    """
    by_title: dict[str, dict] = {}
    try:
        subs = db.query(
            "SELECT keyword, aliases, last_title, kind, tmdb_id "
            "FROM subscriptions WHERE enabled=1 ORDER BY id DESC LIMIT 300"
        )
        for r in subs:
            names: list[str] = []
            kw = str(r["keyword"] or "").strip()
            if kw:
                names.append(kw)
            lt = str(r["last_title"] or "").strip()
            if lt:
                names.append(lt)
            raw_aliases = str(r["aliases"] or "").strip()
            if raw_aliases:
                try:
                    al = json.loads(raw_aliases)
                    if isinstance(al, list):
                        names.extend(str(a).strip() for a in al if str(a or "").strip())
                except Exception as e:
                    logstream.warn("scrape", f"订阅别名解析失败（忽略该条别名）: {e}")
            disp = kw or lt
            for nm in names:
                nt = normalize_title(nm, fuzzy_t2s=True)
                if nt and nt not in by_title:
                    by_title[nt] = {
                        "title": disp,
                        "kind": str(r["kind"] or ""),
                        "tmdb_id": r["tmdb_id"],
                    }
    except Exception as e:
        logstream.warn("scrape", f"订阅索引构建失败，本次刮削的同名去重能力下降: {e}")
    try:
        tr_rows = db.query(
            "SELECT title, category, target_dir FROM transfers "
            "WHERE status='success' AND title != '' ORDER BY id DESC LIMIT 500"
        )
        for r in tr_rows:
            raw_t = str(r["title"] or "").strip()
            cat = str(r["category"] or "")
            pm = parse_media_filename(raw_t, is_dir=True)
            key = normalize_title(pm.title or raw_t, fuzzy_t2s=True)
            if not key or key in by_title:
                continue
            kind = "movie" if "电影" in cat else ("tv" if any(k in cat for k in ("剧", "动漫", "综艺", "番")) else "")
            by_title[key] = {
                "title": pm.title or raw_t,
                "kind": kind,
                "tmdb_id": pm.tmdb_id,
            }
    except Exception as e:
        logstream.warn("scrape", f"历史转存记录索引构建失败，本次刮削的同名去重能力下降: {e}")
    return by_title, {}


def _match_hint_for_parsed(pm: ParsedMedia, parent_dirs: list[str], hints_by_title: dict[str, dict]) -> dict | None:
    if not hints_by_title:
        return None
    for cand in [pm.title, *(list(reversed(parent_dirs or []))[:2])]:
        nc = normalize_title(cand or "", fuzzy_t2s=True)
        if nc and nc in hints_by_title:
            return hints_by_title[nc]
    return None


def _split_rel_and_name(full_target_dir: str, rel_dir: str, fname: str) -> tuple[str, str]:
    base = "/" + (full_target_dir or "").strip("/")
    sub = (rel_dir or "").strip("/")
    dest_dir = f"{base}/{sub}" if sub else base
    return dest_dir, fname


def _should_skip_extra(file_name: str, size_bytes: int, pm: ParsedMedia, settings: dict) -> bool:
    # 广告域名垃圾：文件名内嵌网址（如「【…www.xxx.com】 xxx.mkv」）——不是正片，
    # 无论 media_skip_extras 开关如何都不入库（否则会被解析成一部"广告电影"组织进媒体库）
    low = (file_name or "").lower()
    if "www." in low or "http" in low:
        return True
    if not settings.get("media_skip_extras", True):
        return False
    if pm.is_extra:
        return True
    try:
        min_mb = max(0, int(settings.get("media_extra_min_mb", 50)))
    except (TypeError, ValueError):
        min_mb = 50
    if (
        pm.media_type == "movie"
        and not pm.is_bdmv
        and pm.ext != ".iso"
        and min_mb > 0
        and size_bytes > 0
        and size_bytes < min_mb * 1024 * 1024
    ):
        return True
    return False
