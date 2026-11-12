"""媒体文件名解析、三层上下文继承、配套字幕匹配与规范格式化测试集。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_mp_"))

from backend.core.media_parser import (
    format_media_target,
    match_subtitle_for_video,
    parse_media_context,
    parse_media_filename,
    resolution_rank,
    sanitize_filename,
)


def test_numeric_movie_title_and_tech_tag_isolation():
    # 技术标签中的 2160/1920/265/10bit/60fps 不应误当成集数或年份；数字片名 1917 应被保护
    pm = parse_media_filename("1917.2019.2160p.UHD.BluRay.x265.10bit.HDR.TrueHD.Atmos-FRDS.mkv")
    assert pm.title == "1917"
    assert pm.year == "2019"
    assert pm.resolution == "2160p"
    assert pm.video_codec.lower().replace(".", "") in ("x265",)
    assert pm.media_type == "movie"
    assert pm.episode is None

    bare = parse_media_filename("1917.1080p.WEB-DL.mkv")
    assert bare.title == "1917"
    assert bare.year == ""
    assert bare.resolution == "1080p"


def test_bilingual_title_splitting_and_short_prefix_protection():
    pm = parse_media_filename("[LoliHouse] 葬送的芙莉莲 Sousou no Frieren - 05 [WebRip 1080p HEVC-10bit].mkv")
    assert pm.title == "葬送的芙莉莲"
    assert pm.alt_title == "Sousou no Frieren"
    assert pm.episode == 5
    assert pm.season == 1
    assert pm.media_type == "tv"

    xmen = parse_media_filename("X战警：逆转未来.2014.1080p.BluRay.mp4")
    assert "X战警" in xmen.title
    assert xmen.alt_title == ""
    assert xmen.year == "2014"


def test_tv_ranges_specials_extras_and_parts():
    rng = parse_media_filename("狂飙.The.Knockout.2023.S01E01-E02.2160p.WEB-DL.mkv")
    assert rng.media_type == "tv"
    assert rng.season == 1
    assert rng.episode == 1
    assert rng.episode_end == 2
    assert rng.year == "2023"

    sp = parse_media_filename("进击的巨人.OVA.SP02.1080p.mkv")
    assert sp.is_special is True
    assert sp.season == 0
    assert sp.episode == 2

    extra = parse_media_filename("Sand.Dune.2024.Trailer.1080p.mp4")
    assert extra.is_extra is True

    pt = parse_media_filename("指环王1.2001.CD2.1080p.BluRay.mkv")
    assert pt.part == "part2"
    assert pt.year == "2001"


def test_three_tier_context_inheritance_and_tmdbid():
    # 纯数字集文件从多层父目录继承片名、年份、季号与显式 tmdb_id，并跳过通用分类文件夹
    pm = parse_media_context(
        "05.mp4",
        parent_dirs=["我的媒体库", "剧集", "狂飙 (2023) [tmdbid=215403]", "Season 02"],
        is_dir=False,
    )
    assert pm.title == "狂飙"
    assert pm.year == "2023"
    assert pm.tmdb_id == 215403
    assert pm.media_type == "tv"
    assert pm.season == 2
    assert pm.episode == 5

    # 数据库提示兜底
    pm2 = parse_media_context(
        "E08.mkv",
        parent_dirs=["下载临时目录"],
        db_hint={"title": "三体 (2023)", "kind": "tv", "tmdb_id": 123456},
    )
    assert pm2.title == "三体"
    assert pm2.year == "2023"
    assert pm2.tmdb_id == 123456
    assert pm2.media_type == "tv"
    assert pm2.episode == 8


def test_subtitle_matching_and_format_media_target():
    subs = [
        "狂飙.S01E01.chs&eng.ass",
        "狂飙.S01E01.cht.srt",
        "狂飙.S01E02.chs.ass",
        "readme.txt",
    ]
    matched = match_subtitle_for_video("狂飙.S01E01.2160p.WEB-DL.mkv", subs)
    names_langs = dict(matched)
    assert "狂飙.S01E01.chs&eng.ass" in names_langs
    assert "狂飙.S01E01.cht.srt" in names_langs
    assert "狂飙.S01E02.chs.ass" not in names_langs
    assert names_langs["狂飙.S01E01.cht.srt"] == ".cht"

    pm = parse_media_filename("狂飙.2023.S01E01-E02.2160p.mkv")
    settings = {
        "media_append_tmdb_tag": True,
        "media_organize_template_tv": "{title} ({year}){tmdb_tag}/Season {season_00}/{title} - S{season_00}E{episode_00}{part_tag}{ext}",
        "media_organize_template_movie": "{title} ({year}){tmdb_tag}/{title} ({year}){part_tag}{res_tag}{ext}",
    }
    rel_dir, fname = format_media_target(pm, settings, tmdb_meta={"tmdb_id": 215403, "title": "狂飙", "year": "2023", "media_type": "tv"})
    assert rel_dir == "狂飙 (2023) [tmdbid=215403]/Season 01"
    assert fname == "狂飙 - S01E01-E02.mkv"

    # 不开启 tmdb_tag 时不附加方括号
    rel2, _ = format_media_target(pm, {**settings, "media_append_tmdb_tag": False}, tmdb_meta={"tmdb_id": 215403, "title": "狂飙", "year": "2023", "media_type": "tv"})
    assert rel2 == "狂飙 (2023)/Season 01"

    assert sanitize_filename('Spider:Man/No?Way*Home') == 'Spider：Man No Way Home'
    assert resolution_rank("2160p") > resolution_rank("1080p") > resolution_rank("720p")
