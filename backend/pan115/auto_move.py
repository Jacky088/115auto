"""115 定时文件整理（自动归档匹配、标题归一化、季目录解析与同名歧义预检）。"""
import os
import re
import time

from .. import database as db
from ..core import logstream
from ..core.episode import extract_episode_keys, parse as parse_episode


def _pan():
    """延迟获取 client 模块引用，避免循环导入且保证测试 patch 透明生效。"""
    from . import client
    return client


from .auto_move_utils import (
    _AD_BRACKET_RE,
    _AUTO_MOVE_SCAN_MAX_DIRS,
    _AUTO_MOVE_SKIP_SCAN_DIRS,
    _CHINESE_CHAR_RE,
    _CN_NUM_MAP,
    _DIR_MATCH_MIN_SIZE,
    _DUP_SUFFIX_RE,
    _EXTRA_KEYWORDS,
    _GENERIC_CN_TERMS,
    _GENERIC_LIBRARY_SKIP_FOLDERS,
    _MEDIA_EXTENSIONS,
    _ROMAN_NUMS,
    _should_skip_auto_move_dir,
    _strip_ad_brackets,
    collect_library_keyword_conflicts,
    extract_chinese_subject,
    extract_target_folder_name,
    normalize_title,
    parse_season_number,
    strip_duplicate_suffix,
)


def move_matched_files_for_rule(source_dir: str, target_dir: str,
                                keyword: str | None = None,
                                scan_subdirs: bool = True,
                                mode: str = "library",
                                season_folder: bool = True,
                                fuzzy_t2s: bool = True,
                                dedup_by_episode: bool | None = None,
                                max_depth: int = 2,
                                scan_cache: dict | None = None,
                                dry_run: bool = False,
                                moved_out: list | None = None,
                                rule_media_type: str = "auto",
                                ambiguous_kws: set | frozenset | None = None,
                                match_by_dir: bool = False,
                                allow_english: bool = False,
                                skip_stats_out: dict | None = None,
                                include_ids: set[str] | None = None) -> tuple:
    """按映射规则排队移动满足条件的文件（做好 115 严格风控）：

    模式支持：
    - library（总库智能分流模式）：目标目录作为媒体总库（如 /电视剧），自动识别库内所有一级剧集子文件夹
      （如 折月亮 (2026)、繁花 (2023)），根据文件名智能归入对应剧目文件夹；若剧目下有季子目录（如 Season 1/第一季），
      且开启了 season_folder，自动分流入季目录。
    - exact（一对一精确匹配模式）：目标目录作为具体剧目目录（如 /电视剧/折月亮 (2026)），仅移动匹配此剧名的文件。
    仅接受以上两个值；规则的 "default" 由调用方（jobs.auto_move）归一化为全局模式后传入，
    其他未知值兜底按 library 处理。

    返回值按模式区分：
    - 正常模式（dry_run=False）：(移动数, 样例文件名列表)，与历史版本一致；
      若传入 moved_out 列表，成功移动的每个文件明细 dict 会追加其中
      （id/name/from_cid/from_path/dest_cid/dest_path/drama/season/size），供上层落整理账本；
      若传入 skip_stats_out dict，各层跳过计数会写入其中
      （ep=按季集防重 / movie=电影库守卫 / ambiguous=同名歧义 / junk=垃圾过滤 / dedup=同名同内容查重），
      供上层在完成通知中向用户解释「为什么有些文件没有动」。
    - dry_run=True：不做任何移动，返回 (计划数, 计划 dict)——dict 含
      items（与 moved_out 同结构的明细列表，另含 matched 命中词）/ skip_ep / skip_ep_samples /
      skip_movie / skip_ambiguous 及各自样例。
      注意 mkdirs 仍会确保源/目标目录存在（均为用户配置的既有目录，幂等）。
      跳过统计中的防重计数为"若执行将会跳过"的预演结果。

    媒体类型守卫（rule_media_type）与同名歧义拦截（ambiguous_kws）：
    - rule_media_type='movie'：跳过一切带集数标识的文件（S01E01/E01/第1集/1话）——
      电影库不收剧集，重名场景下剧集留给 tv/auto 规则认领，与规则顺序无关
    - ambiguous_kws：跨规则同名预检得到的剧名关键词集合（出现在 ≥2 个总库的目录名）。
      命中这些关键词且**无集数标识**的裸文件名不自动处理（两库同名无法判别，留给用户手动判断）
    - 两者均默认关闭（'auto' / None），行为与历史版本一致

    云下载目录模式（match_by_dir=True，配合规则上的 dir_match 开关）：
    中文标题只出现在「下载任务目录名」上、目录内文件多为纯英文名的场景。匹配对象从
    文件名换成任务子目录名（剥除含 www/http 的【…】广告括号段后做两级匹配），目录内
    文件继承目录的归属；集数标识取「文件名或目录名任一」。三重垃圾过滤防广告文件混入：
    文件名含 www./http、视频体积 < 10MB、扩展名白名单；源目录根部散落的文件不参与；
    字幕等非视频扩展不跟移（留给整理刮削配对）。仅推荐在云下载目录的规则上开启。

    防风控与安全原则：
    1. 只移动文件，文件夹绝不移动（is_dir == False），忽略 BDMV/花絮/字幕独立文件夹
    2. 文件名必须包含中文（_CHINESE_CHAR_RE.search(name)）
    3. 媒体扩展名白名单（_MEDIA_EXTENSIONS），过滤广告与非媒体文件
    4. 排除预告/花絮/sample 等非正片文件
    5. 源目录穿透默认最多 2 层（兼容 剧名/Season 目录），带总目录配额上限与周期缓存复用
    6. 分批 fs_move（每批最多 30 个），批次间休眠 0.5s * pace_factor()
    7. 支持按季集防重（同剧目标已存在同季同集视频时跳过）及同名/副本防撞
    8. 捕获 115 异常并检测风控关键词，一旦命中记录风控状态并抛出
    """
    if mode == "exact":
        kw = (keyword or extract_target_folder_name(target_dir)).strip()
        if not kw:
            raise ValueError(f"目标目录「{target_dir}」未包含有效文件夹名称，无法提取匹配关键词，不予执行")

    if dedup_by_episode is None:
        try:
            dedup_ep = bool(db.get_setting("auto_move_dedup_by_episode", True))
        except Exception:
            dedup_ep = True
    else:
        dedup_ep = bool(dedup_by_episode)

    # 跳过统计（提前初始化：统一出口 _result 在任何早退路径都要能带出计数）
    ep_skipped_cnt = 0
    ep_skipped_samples: list[str] = []
    movie_skip_cnt = 0
    movie_skip_samples: list[str] = []
    ambiguous_cnt = 0
    ambiguous_samples: list[str] = []
    plan_items: list[dict] = []
    dir_junk_cnt = 0
    dir_junk_samples: list[str] = []
    dedup_skip_cnt = 0

    def _result(count, items):
        """统一出口：dry_run 返回 (计划数, 统计 dict)，正常模式返回 (移动数, 样例名)。"""
        if dry_run:
            return count, {
                "items": plan_items,
                "skip_ep": ep_skipped_cnt,
                "skip_ep_samples": list(ep_skipped_samples)[:5],
                "skip_movie": movie_skip_cnt,
                "skip_movie_samples": list(movie_skip_samples)[:5],
                "skip_ambiguous": ambiguous_cnt,
                "skip_ambiguous_samples": list(ambiguous_samples)[:5],
                "skip_junk": dir_junk_cnt,
                "skip_junk_samples": list(dir_junk_samples)[:5],
                "skip_dedup": dedup_skip_cnt,
            }
        return count, items

    target_cid = _pan().mkdirs(target_dir)
    if target_cid is None:
        raise RuntimeError(f"无法获取或创建目标目录: {target_dir}")

    source_cid = _pan().mkdirs(source_dir)
    if source_cid is None:
        raise RuntimeError(f"无法获取源目录: {source_dir}")

    if mode == "exact" and source_cid == target_cid:
        logstream.info("auto_move", f"精确模式下源目录与目标目录相同「{target_dir}」，无需移动")
        return _result(0, [])

    pace = _pan().pace_factor()
    client = _pan()._get_client()
    entries_cache: dict[int, list[dict]] = scan_cache.setdefault("dir_entries", {}) if scan_cache is not None else {}

    def cached_list_entries(cid: int, limit: int = 2000, sleep_sec: float = 0.0) -> list[dict]:
        if cid in entries_cache:
            return entries_cache[cid]
        if sleep_sec > 0 and pace > 0:
            time.sleep(sleep_sec * pace)
        res = _pan().list_entries(cid, limit=limit)
        if scan_cache is not None:
            entries_cache[cid] = res
        return res

    drama_targets: list[dict] = []

    if mode == "library":
        # 总库智能分流模式：列出目标总库下的一级子文件夹构建剧目映射
        root_targets = cached_list_entries(target_cid, limit=2000, sleep_sec=0.2)
        sub_dramas = [e for e in root_targets if e.get("is_dir")]
        for sd in sub_dramas:
            sd_name = sd.get("name", "").strip()
            if not sd_name or sd_name.lower() in _GENERIC_LIBRARY_SKIP_FOLDERS:
                continue
            if parse_season_number(sd_name) is not None:
                continue  # 排除裸露的季目录
            if not allow_english and not _CHINESE_CHAR_RE.search(sd_name):
                continue  # 默认必须包含中文汉字；开启 allow_english 后允许英文剧集/电影目录
            sd_kw = extract_target_folder_name(sd_name)
            sd_norm_kw = normalize_title(sd_kw, fuzzy_t2s=fuzzy_t2s)
            if len(sd_norm_kw) < 2:
                continue  # 剧名关键词过短跳过，避免单字严重误匹配
            sd_cn = extract_chinese_subject(sd_kw)
            sd_norm_cn = normalize_title(sd_cn, fuzzy_t2s=fuzzy_t2s) if sd_cn else ""
            drama_targets.append({
                "cid": int(str(sd["id"])),
                "name": sd_name,
                "kw": sd_kw,
                "norm_kw": sd_norm_kw,
                "norm_cn_kw": sd_norm_cn,
            })
        # 按关键词长度降序排序，长剧名优先匹配（如「流浪地球2」优先于「流浪地球」）
        drama_targets.sort(key=lambda x: len(x["norm_kw"]), reverse=True)
        if not drama_targets:
            logstream.info("auto_move", f"总库「{target_dir}」下暂未发现包含中文的剧集子文件夹，跳过本次分流")
            return _result(0, [])
    else:
        # 一对一精确匹配模式
        norm_kw = normalize_title(kw, fuzzy_t2s=fuzzy_t2s)
        cn_subj = extract_chinese_subject(kw)
        norm_cn_subj = normalize_title(cn_subj, fuzzy_t2s=fuzzy_t2s) if cn_subj else ""
        drama_targets = [{
            "cid": target_cid,
            "name": os.path.basename(target_dir.rstrip("/")),
            "kw": kw,
            "norm_kw": norm_kw,
            "norm_cn_kw": norm_cn_subj,
        }]

    # 1. 受控 BFS 扫描源目录（默认最多穿透 2 层：源目录 -> 剧名目录 -> Season X，并设总目录请求配额防风控）
    effective_depth = 0 if not scan_subdirs else max(1, min(int(max_depth or 2), 3))
    candidate_files: list[dict] = []
    # 队列元素: (cid, 深度, 紧邻父目录名, 顶级任务目录名)——顶级名供云下载目录模式按目录名匹配
    scan_queue: list[tuple[int, int, str, str]] = [(source_cid, 0, "", "")]
    visited_scan_cids: set[int] = set()
    scanned_subdirs = 0

    while scan_queue:
        cur_cid, cur_depth, cur_folder_name, cur_top_name = scan_queue.pop(0)
        if cur_cid in visited_scan_cids:
            continue
        visited_scan_cids.add(cur_cid)
        try:
            cur_entries = cached_list_entries(
                cur_cid,
                limit=2000 if cur_depth == 0 else 1000,
                sleep_sec=0.2 if cur_depth == 0 else 0.25,
            )
        except Exception as e:
            if cur_depth == 0 or _pan()._hits_risk_keyword(str(e).lower()):
                raise
            logstream.warn("auto_move", f"跳过不可读子目录 {cur_folder_name or cur_cid}: {e}")
            continue

        # BDMV / VIDEO_TS 原盘目录保护：不拆散枚举内部流文件
        if cur_depth > 0:
            dir_names_low = {
                str(x.get("name") or "").strip().lower()
                for x in cur_entries if x.get("is_dir")
            }
            if dir_names_low & {"bdmv", "certificate", "video_ts"}:
                continue

        for e in cur_entries:
            if not e.get("is_dir"):
                candidate_files.append({
                    **e,
                    "_parent_cid": cur_cid,
                    "_parent_folder_name": cur_folder_name,
                    "_top_folder_name": cur_top_name,
                })
            elif cur_depth < effective_depth and scanned_subdirs < _pan()._AUTO_MOVE_SCAN_MAX_DIRS:
                dname = str(e.get("name") or "").strip()
                if _should_skip_auto_move_dir(dname):
                    continue
                try:
                    sub_cid = int(str(e["id"]))
                except (TypeError, ValueError):
                    continue
                if sub_cid in visited_scan_cids:
                    continue
                scanned_subdirs += 1
                # 深度 1 的目录自己就是"顶级任务目录"；更深层继承顶级名
                scan_queue.append((sub_cid, cur_depth + 1, dname,
                                   dname if cur_depth == 0 else cur_top_name))

    if not candidate_files:
        return _result(0, [])

    # 季子目录与目标文件查重缓存（原始文件名 + 剥离副本后缀规范名 + 按季集防重集合）
    drama_season_cache: dict[int, dict[int, int]] = {}
    drama_season_names: dict[int, dict[int, str]] = {}
    dest_file_cache: dict[int, set[str]] = {}
    dest_canon_cache: dict[int, set[str]] = {}
    dir_ep_cache: dict[tuple[int, str], set[tuple[int, int, str, str]]] = {}

    def get_dest_file_names(cid: int) -> tuple[set[str], set[str]]:
        if cid not in dest_file_cache:
            try:
                raw_names = _pan().list_file_names(cid, limit=2000)
                canon_names = {strip_duplicate_suffix(n)[0] for n in raw_names}
                dest_file_cache[cid] = raw_names
                dest_canon_cache[cid] = canon_names
            except Exception as e:
                if _pan()._hits_risk_keyword(str(e).lower()):
                    raise
                logstream.warn("auto_move", f"列举目标目录 {cid} 文件名失败，暂跳过缓存: {e}")
                return set(), set()
        return dest_file_cache[cid], dest_canon_cache[cid]

    dest_sha_cache: dict[int, set[str]] = {}

    def get_dest_sha1s(cid: int) -> set[str]:
        """目标目录已有文件的 sha1 集合（仅云下载目录模式使用）。

        整理刮削可能已按模板重命名了目标库里的同内容文件——名字对不上但内容相同，
        按 sha1 内容指纹比对才能避免重复归档。拉取失败按空集处理（放行，与名称查重同口径）。
        """
        if cid not in dest_sha_cache:
            try:
                dest_sha_cache[cid] = {
                    str(e.get("sha1") or "")
                    for e in _pan().list_entries(cid, limit=2000) if e.get("sha1")
                }
            except Exception as e:
                if _pan()._hits_risk_keyword(str(e).lower()):
                    raise
                logstream.warn("auto_move", f"列举目标目录 {cid} SHA1 失败，暂跳过缓存: {e}")
                return set()
        return dest_sha_cache[cid]

    def get_dir_episode_keys(cid: int, folder_hint: str = "") -> set[tuple[int, int, str, str]]:
        ckey = (cid, folder_hint)
        if ckey not in dir_ep_cache:
            raw_names, _ = get_dest_file_names(cid)
            eps: set[tuple[int, int, str, str]] = set()
            for rname in raw_names:
                if any(extra in rname.lower() for extra in _EXTRA_KEYWORDS):
                    continue
                for k in extract_episode_keys(rname, folder_hint=folder_hint):
                    eps.add(k)
            dir_ep_cache[ckey] = eps
        return dir_ep_cache[ckey]

    def get_season_subdirs(drama_cid: int) -> dict[int, int]:
        if drama_cid not in drama_season_cache:
            mapping: dict[int, int] = {}
            names_map: dict[int, str] = {}
            try:
                entries = cached_list_entries(drama_cid, limit=100, sleep_sec=0.2)
                for item in entries:
                    if item.get("is_dir"):
                        sname = item.get("name", "")
                        s_idx = parse_season_number(sname)
                        if s_idx is not None:
                            mapping[s_idx] = int(str(item["id"]))
                            names_map[s_idx] = sname
            except Exception as e:
                if _pan()._hits_risk_keyword(str(e).lower()):
                    raise
                logstream.warn("auto_move", f"列举剧集季目录 {drama_cid} 失败: {e}")
                return {}
            drama_season_cache[drama_cid] = mapping
            drama_season_names[drama_cid] = names_map
        return drama_season_cache[drama_cid]

    seen_ids: set[str] = set()
    # 聚类去重：开启按季集防重且为带集号的视频时按 (drama_cid, ep_keys) 聚类择优，否则按 (final_dest_cid, clean_name)
    candidates_by_group: dict[tuple, list[dict]] = {}

    # 云下载目录模式预计算：按顶级任务目录名分组，剥广告括号后做目录级两级匹配
    dir_ctx: dict[str, dict] = {}
    if match_by_dir:
        dir_groups: dict[str, list[dict]] = {}
        for f in candidate_files:
            top = str(f.get("_top_folder_name") or "")
            if top:
                dir_groups.setdefault(top, []).append(f)
        for top_name, group in dir_groups.items():
            cleaned = _strip_ad_brackets(top_name)
            has_marker = parse_episode(cleaned)[1] is not None
            if not has_marker:
                has_marker = any(
                    parse_episode(str(x.get("name") or ""))[1] is not None for x in group)
            dir_norm = normalize_title(cleaned, fuzzy_t2s=fuzzy_t2s)
            md = None
            for dt in drama_targets:
                if dt["norm_kw"] in dir_norm:
                    md = dt
                    break
            if md is None:
                cn_subject = extract_chinese_subject(cleaned)
                if cn_subject:
                    ncs = normalize_title(cn_subject, fuzzy_t2s=fuzzy_t2s)
                    for dt in drama_targets:
                        if dt.get("norm_cn_kw") and len(dt["norm_cn_kw"]) >= 2 \
                                and dt["norm_cn_kw"] in ncs:
                            md = dt
                            break
            dir_ctx[top_name] = {"drama": md, "has_marker": has_marker, "cleaned": cleaned}

    for f in candidate_files:
        fid = str(f.get("id"))
        fname = f.get("name", "")
        if not fid or not fname or fid in seen_ids:
            continue
        # 云下载目录模式：身份由顶级任务目录名承载（目录未命中规则的目录直接整组跳过）
        dctx = None
        if match_by_dir:
            dctx = dir_ctx.get(str(f.get("_top_folder_name") or ""))
            if dctx is None or dctx["drama"] is None:
                continue

        # 扩展名白名单校验
        ext = os.path.splitext(fname)[1].lower()
        if ext not in _MEDIA_EXTENSIONS:
            continue
        # 中文硬指标：默认必须含有汉字（纯数字、纯英文坚决不移动）；
        # 云下载目录或开启 allow_english 后放开文件名中文要求
        if not match_by_dir and not allow_english and not _CHINESE_CHAR_RE.search(fname):
            continue
        # 预告/花絮非正片过滤
        fname_lower = fname.lower()
        if any(extra in fname_lower for extra in _EXTRA_KEYWORDS):
            continue

        # 媒体类型守卫 / 同名歧义判别都需要集数标识信息（按需解析，正则开销极低）
        ep_marker = False
        if rule_media_type == "movie" or ambiguous_kws or match_by_dir:
            ep_marker = parse_episode(fname)[1] is not None
            if match_by_dir and dctx["has_marker"]:
                ep_marker = True  # 文件名或任务目录名任一携带集数标识即视为剧集
        # 电影库规则：跳过一切带集数标识的文件（剧集交给 tv/auto 规则认领，与规则顺序无关）
        if rule_media_type == "movie" and ep_marker:
            movie_skip_cnt += 1
            if len(movie_skip_samples) < 5:
                movie_skip_samples.append(fname)
            continue
        # 云下载垃圾过滤：广告域名 / 微小体积（真媒体为 GB 级，防广告视频混入媒体库）
        if match_by_dir:
            fsize = int(f.get("size", f.get("s", 0)) or 0)
            if re.search(r"www\.|http", fname_lower) or 0 < fsize < _DIR_MATCH_MIN_SIZE:
                dir_junk_cnt += 1
                if len(dir_junk_samples) < 5:
                    dir_junk_samples.append(fname)
                continue

        # 软化标题比对（支持中英文混合双层匹配）；云下载模式直接继承目录级匹配结果
        if match_by_dir:
            matched_drama = dctx["drama"]
        else:
            norm_name = normalize_title(fname, fuzzy_t2s=fuzzy_t2s)
            matched_drama = None
            # 第一轮：优先精确匹配完整体关键词（如 doctorx白色黑手党时代 命中）
            for dt in drama_targets:
                if dt["norm_kw"] in norm_name:
                    matched_drama = dt
                    break
            # 第二轮：若完整体未命中，回落比对纯中文主体（如发布组仅写 白色黑手党时代 时命中）
            if not matched_drama:
                for dt in drama_targets:
                    if dt.get("norm_cn_kw") and len(dt["norm_cn_kw"]) >= 2 and dt["norm_cn_kw"] in norm_name:
                        matched_drama = dt
                        break
        if not matched_drama:
            continue

        # 同名歧义拦截：命中的剧名关键词同时出现在 ≥2 个总库，且文件无集数标识
        # （如「斗罗大陆.mkv」在电影库和电视剧库都有同名目录）→ 不自动处理，留源目录交用户手动判断
        if ambiguous_kws and not ep_marker and matched_drama["norm_kw"] in ambiguous_kws:
            ambiguous_cnt += 1
            if len(ambiguous_samples) < 5:
                ambiguous_samples.append(fname)
            continue

        # 目标 cid 确定：若开启按季归档且剧目下有季子目录，按季落入季目录
        drama_cid = matched_drama["cid"]
        final_dest_cid = drama_cid
        seasons_map: dict[int, int] = {}
        season_name = ""
        if season_folder:
            s_num, ep_num, _ = parse_episode(fname)
            if s_num is None and ep_num is not None:
                s_num = parse_season_number(f.get("_parent_folder_name", ""))
                if s_num is None:
                    s_num = 1  # 电视剧单集默认第 1 季
            if s_num is not None:
                seasons_map = get_season_subdirs(drama_cid)
                if s_num in seasons_map:
                    final_dest_cid = seasons_map[s_num]
                    season_name = drama_season_names.get(drama_cid, {}).get(
                        s_num, f"Season {s_num}")

        # 若文件已经在目标目录，无需移动
        if f.get("_parent_cid") == final_dest_cid:
            continue

        # 剥离 (1)、(2)、（1）、（2）等重复转存后缀，提取基准规范名与副本编号
        clean_name, dup_idx = strip_duplicate_suffix(fname)

        # 目标目录文件名查重：目标库已有原始文件或任一历史副本时，源目录全部副本均跳过
        dest_raw_names, dest_canon_names = get_dest_file_names(final_dest_cid)
        if fname in dest_raw_names or clean_name in dest_raw_names or clean_name in dest_canon_names:
            dedup_skip_cnt += 1
            continue

        # 云下载目录模式内容级查重：目标库的同内容文件可能已被整理刮削按模板重命名，
        # 文件名对不上但 sha1 相同 → 同一部影片/同一集，跳过防止重复归档
        if match_by_dir:
            src_sha = str(f.get("sha1") or "")
            if src_sha and src_sha in get_dest_sha1s(final_dest_cid):
                dedup_skip_cnt += 1
                continue

        # 按季集防重：同剧目标（剧集根目录 + 对应季目录）已有相同季集视频时跳过不同命名版本
        ep_keys = extract_episode_keys(fname, folder_hint=f.get("_parent_folder_name", "")) if dedup_ep else []
        if dedup_ep and ep_keys:
            existing_eps = set(get_dir_episode_keys(drama_cid, ""))
            if final_dest_cid != drama_cid:
                s_hint = drama_season_names.get(drama_cid, {}).get(ep_keys[0][0], f"Season {ep_keys[0][0]}")
                existing_eps |= get_dir_episode_keys(final_dest_cid, s_hint)
            elif season_folder:
                for ek_s in {k[0] for k in ep_keys}:
                    if ek_s in seasons_map:
                        s_hint = drama_season_names.get(drama_cid, {}).get(ek_s, f"Season {ek_s}")
                        existing_eps |= get_dir_episode_keys(seasons_map[ek_s], s_hint)
            if all(k in existing_eps for k in ep_keys):
                ep_skipped_cnt += 1
                if len(ep_skipped_samples) < 5:
                    ep_skipped_samples.append(fname)
                continue

        seen_ids.add(fid)
        if dedup_ep and ep_keys:
            group_key = ("ep", drama_cid, tuple(ep_keys))
        else:
            group_key = ("name", final_dest_cid, clean_name)

        candidates_by_group.setdefault(group_key, []).append({
            "id": fid,
            "name": fname,
            "clean_name": clean_name,
            "dup_idx": dup_idx,
            "size": int(f.get("size", f.get("s", 0)) or 0),
            "parent_cid": f.get("_parent_cid"),
            "from_folder": f.get("_parent_folder_name", ""),
            "dest_cid": final_dest_cid,
            "drama_cid": drama_cid,
            "drama_name": matched_drama["name"],
            "matched": matched_drama["kw"],
            "season_name": season_name if final_dest_cid != drama_cid else "",
            "ep_keys": ep_keys,
        })

    if not candidates_by_group:
        if ep_skipped_cnt > 0:
            logstream.info(
                "auto_move",
                f"按季集防重跳过 {ep_skipped_cnt} 个同季同集视频（目标已存在）：{'、'.join(ep_skipped_samples[:3])}",
            )
        return _result(0, [])

    # 择优提取：每组仅移动一份文件！
    # 优先级：dup_idx 升序（0 原版最优先，其次 (1)、(2)）；同编号时大文件优先（如 2160p 优于 1080p）
    files_to_move: list[dict] = []
    batch_eps_added: dict[int, set[tuple[int, int, str, str]]] = {}
    for gkey, items in candidates_by_group.items():
        items.sort(key=lambda x: (x["dup_idx"], -x["size"]))
        best = items[0]
        if len(items) > 1 and gkey[0] == "ep":
            ep_skipped_cnt += len(items) - 1
            for extra_it in items[1:]:
                if len(ep_skipped_samples) < 5:
                    ep_skipped_samples.append(extra_it["name"])
        if gkey[0] == "ep" and best["ep_keys"]:
            d_added = batch_eps_added.setdefault(best["drama_cid"], set())
            if all(k in d_added for k in best["ep_keys"]):
                ep_skipped_cnt += 1
                if len(ep_skipped_samples) < 5:
                    ep_skipped_samples.append(best["name"])
                continue
            d_added.update(best["ep_keys"])
        files_to_move.append(best)
        dest_raw_names, dest_canon_names = get_dest_file_names(best["dest_cid"])
        dest_raw_names.add(best["name"])
        dest_canon_names.add(best["clean_name"])

    # 两段式预览确认：仅执行用户在预览中明确勾选的条目，
    # 预览后新出现的文件本次一律不移动（include_ids 为 None 时保持旧的全量执行口径）
    if include_ids is not None:
        files_to_move = [x for x in files_to_move if str(x["id"]) in include_ids]

    if ep_skipped_cnt > 0:
        logstream.info(
            "auto_move",
            f"按季集防重跳过 {ep_skipped_cnt} 个同季同集视频（目标或本批已存在）：{'、'.join(ep_skipped_samples[:3])}",
        )

    # 按目标目录分组移动（dry_run 在此返回计划，不做任何 115 写操作）
    src_root = (source_dir or "").rstrip("/")
    if not dry_run:
        if movie_skip_cnt > 0:
            logstream.info(
                "auto_move",
                f"电影库规则跳过 {movie_skip_cnt} 个带集数标识的文件（剧集交给剧集规则认领）："
                f"{'、'.join(movie_skip_samples[:3])}",
            )
        if ambiguous_cnt > 0:
            logstream.info(
                "auto_move",
                f"同名歧义跳过 {ambiguous_cnt} 个文件（多库同名且无集数标识，留源目录交用户手动判断）："
                f"{'、'.join(ambiguous_samples[:3])}",
            )
        if dir_junk_cnt > 0:
            logstream.info(
                "auto_move",
                f"云下载垃圾过滤跳过 {dir_junk_cnt} 个文件（广告域名/微小体积）："
                f"{'、'.join(dir_junk_samples[:3])}",
            )
    if dry_run:
        plan_items = []
        for x in files_to_move:
            from_folder = str(x.get("from_folder") or "")
            season_name = str(x.get("season_name") or "")
            plan_items.append({
                "id": x["id"],
                "name": x["name"],
                "from_cid": x["parent_cid"],
                "from_path": f"{src_root}/{from_folder}" if from_folder else (src_root or "/"),
                "dest_cid": x["dest_cid"],
                "dest_path": (target_dir or "").rstrip("/") + "/" + str(x["drama_name"])
                             + (f"/{season_name}" if season_name else ""),
                "drama": x["drama_name"],
                "season": season_name,
                "matched": x.get("matched", ""),
                "size": int(x.get("size") or 0),
            })
        return _result(len(plan_items), [])

    by_dest: dict[int, list[dict]] = {}
    for item in files_to_move:
        by_dest.setdefault(item["dest_cid"], []).append(item)

    moved_count = 0
    sample_names: list[str] = []

    for dest_cid, items in by_dest.items():
        for i in range(0, len(items), 30):
            batch = items[i:i + 30]
            ids = [x["id"] for x in batch if x.get("id")]
            if not ids:
                continue
            time.sleep(0.5 * _pan().pace_factor())
            try:
                resp = _pan().pan_call(client.fs_move, ids, pid=dest_cid)
                if not resp.get("state"):
                    msg = resp.get("error", "") or resp.get("msg", "") or str(resp)[:200]
                    _pan().note_115_error(msg)
                    raise _pan().Pan115Error(f"移动文件被 115 拒绝: {msg}")
                moved_count += len(ids)
                sample_names.extend([x["name"] for x in batch][:2])
                _pan().note_115_ok()
                # 移动成功才落明细：供上层写整理账本（撤销依据），失败批次不产生记录
                if moved_out is not None:
                    for x in batch:
                        from_folder = str(x.get("from_folder") or "")
                        season_name = str(x.get("season_name") or "")
                        moved_out.append({
                            "id": x["id"],
                            "name": x["name"],
                            "from_cid": x["parent_cid"],
                            "from_path": f"{src_root}/{from_folder}" if from_folder else (src_root or "/"),
                            "dest_cid": x["dest_cid"],
                            "dest_path": (target_dir or "").rstrip("/") + "/" + str(x["drama_name"])
                                         + (f"/{season_name}" if season_name else ""),
                            "drama": x["drama_name"],
                            "season": season_name,
                            "size": int(x.get("size") or 0),
                        })
                if scan_cache is not None:
                    entries_cache.pop(dest_cid, None)
                    for x in batch:
                        if x.get("parent_cid") is not None:
                            entries_cache.pop(int(x["parent_cid"]), None)
            except Exception as e:
                _pan().note_115_error(str(e))
                raise

    if skip_stats_out is not None:
        skip_stats_out.update({
            "ep": ep_skipped_cnt,
            "movie": movie_skip_cnt,
            "ambiguous": ambiguous_cnt,
            "junk": dir_junk_cnt,
            "dedup": dedup_skip_cnt,
        })
    return _result(moved_count, sample_names[:5])


__all__ = [
    "_AD_BRACKET_RE",
    "_AUTO_MOVE_SCAN_MAX_DIRS",
    "_AUTO_MOVE_SKIP_SCAN_DIRS",
    "_CHINESE_CHAR_RE",
    "_CN_NUM_MAP",
    "_DIR_MATCH_MIN_SIZE",
    "_DUP_SUFFIX_RE",
    "_EXTRA_KEYWORDS",
    "_GENERIC_CN_TERMS",
    "_GENERIC_LIBRARY_SKIP_FOLDERS",
    "_MEDIA_EXTENSIONS",
    "_ROMAN_NUMS",
    "_should_skip_auto_move_dir",
    "_strip_ad_brackets",
    "collect_library_keyword_conflicts",
    "extract_chinese_subject",
    "extract_target_folder_name",
    "move_matched_files_for_rule",
    "normalize_title",
    "parse_season_number",
    "strip_duplicate_suffix",
]

