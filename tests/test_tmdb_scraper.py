"""TMDB 多阶段刮削打分、信心门禁与本地 NFO/海报零上传生成测试集。"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_tmdb_"))

from backend import config, database as db
from backend.core.media_parser import parse_media_filename
from backend.core import tmdb_scraper


def _sample_tv_detail(tmdb_id=215403, name="狂飙", orig="狂飙", first_yr="2023-01-14"):
    return {
        "id": tmdb_id,
        "name": name,
        "original_name": orig,
        "first_air_date": first_yr,
        "overview": "京海市扫黑除恶故事。",
        "vote_average": 8.5,
        "vote_count": 320,
        "popularity": 45.0,
        "poster_path": "/abc123.jpg",
        "backdrop_path": "/bg456.jpg",
        "status": "Ended",
        "genres": [{"name": "剧情"}, {"name": "犯罪"}],
        "seasons": [
            {"season_number": 1, "name": "第 1 季", "air_date": "2023-01-14", "episode_count": 39, "overview": "第一季"}
        ],
        "credits": {"cast": [{"name": "张译", "character": "安欣"}]},
        "alternative_titles": {"results": [{"iso_3166_1": "US", "title": "The Knockout"}]},
        "translations": {"translations": []},
    }


def test_p0_explicit_tmdb_id_hits_100_and_auto_locks():
    db.init()
    db.execute("DELETE FROM tmdb_cache")
    pm = parse_media_filename("狂飙.S01E01.[tmdbid=215403].mkv")
    det = _sample_tv_detail()
    with patch.object(tmdb_scraper, "get_tmdb_detail", return_value=det):
        scored = tmdb_scraper.recall_and_score(pm, api_key="fake_key", type_hint="tv")
    assert len(scored) == 1
    assert scored[0]["tmdb_id"] == 215403
    assert scored[0]["score"] == 100.0
    decision, _ = tmdb_scraper.decide_match(scored, conf_threshold=85, margin_threshold=12)
    assert decision == "auto"


def test_ambiguous_candidates_trigger_need_confirm_by_margin_or_score():
    db.init()
    db.execute("DELETE FROM tmdb_cache")
    pm = parse_media_filename("沙丘.1080p.mkv")  # 无年份的同名重拍片
    d_new = {
        "id": 438631, "title": "沙丘", "original_title": "Dune",
        "release_date": "2021-10-22", "overview": "新版", "vote_average": 7.8,
        "vote_count": 9000, "popularity": 90.0, "poster_path": "/p1.jpg",
    }
    d_old = {
        "id": 841, "title": "沙丘", "original_title": "Dune",
        "release_date": "1984-12-14", "overview": "老版", "vote_average": 6.2,
        "vote_count": 2900, "popularity": 30.0, "poster_path": "/p2.jpg",
    }
    s1 = tmdb_scraper.score_candidate(d_new, "movie", pm)
    s2 = tmdb_scraper.score_candidate(d_old, "movie", pm)
    assert abs(s1["score"] - s2["score"]) < 12.0
    decision, _ = tmdb_scraper.decide_match([s1, s2], conf_threshold=85, margin_threshold=12)
    assert decision == "need_confirm"

    # 一旦文件带了年份 2021，则 2021 版精准锁定而 1984 版因年份差异被重度扣分
    pm_with_yr = parse_media_filename("沙丘.2021.1080p.mkv")
    y1 = tmdb_scraper.score_candidate(d_new, "movie", pm_with_yr)
    y2 = tmdb_scraper.score_candidate(d_old, "movie", pm_with_yr)
    assert y1["score"] >= 85.0
    assert (y1["score"] - y2["score"]) >= 30.0
    decision_yr, _ = tmdb_scraper.decide_match([y1, y2], conf_threshold=85, margin_threshold=12)
    assert decision_yr == "auto"


def test_compute_local_meta_dir_prevents_traversal_and_writes_nfo_locally():
    with tempfile.TemporaryDirectory() as td:
        meta_dir = tmdb_scraper.compute_local_meta_dir("../../evil/狂飙 (2023) [tmdbid=215403]", custom_root=td)
        assert meta_dir.resolve().is_relative_to(Path(td).resolve())

        det = _sample_tv_detail()
        with patch.object(tmdb_scraper, "get_tv_season_episodes", return_value=[
            {"id": 9001, "episode_number": 1, "name": "第一集 序幕", "air_date": "2023-01-14", "overview": "故事开端", "vote_average": 8.2}
        ]), patch.object(tmdb_scraper, "tmdb_download_image", return_value=b"\xff\xd8\xff\xe0FAKEJPEG"):
            res = tmdb_scraper.save_local_metadata(
                meta_dir, det, kind="tv",
                season_nums=[1],
                episode_items=[(1, 1, "狂飙 - S01E01")],
                api_key="fake_key",
                write_nfo=True,
                download_poster=True,
            )

        assert (meta_dir / "tvshow.nfo").exists()
        assert (meta_dir / "Season 01" / "season.nfo").exists()
        assert (meta_dir / "Season 01" / "狂飙 - S01E01.nfo").exists()
        assert (meta_dir / "poster.jpg").read_bytes().startswith(b"\xff\xd8\xff")
        assert (meta_dir / "fanart.jpg").read_bytes().startswith(b"\xff\xd8\xff")
        assert "<tmdbid>215403</tmdbid>" in (meta_dir / "tvshow.nfo").read_text(encoding="utf-8")
        assert len(res["files"]) == 5


def test_remap_absolute_episode_and_cumulative_season_scoring():
    detail_two_seasons = {
        "id": 99001,
        "name": "葬送的芙莉莲",
        "original_name": "葬送のフリーレン",
        "first_air_date": "2023-09-29",
        "overview": "魔法使之旅",
        "vote_average": 9.0,
        "vote_count": 500,
        "popularity": 80.0,
        "poster_path": "/frieren.jpg",
        "seasons": [
            {"season_number": 0, "episode_count": 2},
            {"season_number": 1, "episode_count": 12},
            {"season_number": 2, "episode_count": 12},
        ],
    }

    # 绝对集号跨季重映射（15 -> S02E03；13-14 -> S02E01-E02）
    assert tmdb_scraper.remap_absolute_episode(None, 15, None, detail_two_seasons) == (2, 3, None)
    assert tmdb_scraper.remap_absolute_episode(1, 13, 14, detail_two_seasons) == (2, 1, 2)
    assert tmdb_scraper.remap_absolute_episode(None, 5, None, detail_two_seasons) == (1, 5, None)
    # 超过累计总集数时不强行拆分
    assert tmdb_scraper.remap_absolute_episode(None, 30, None, detail_two_seasons) == (1, 30, None)
    # 已显式高于第1季时保持原季集
    assert tmdb_scraper.remap_absolute_episode(2, 15, None, detail_two_seasons) == (2, 15, None)

    # 物理结构对账：第1季12集 + 第2季12集 = 24集，本地绝对集号到24集时不应被扣 -10 分
    pm = parse_media_filename("葬送的芙莉莲 - 24.mkv")
    scored = tmdb_scraper.score_candidate(detail_two_seasons, "tv", pm, ep_stats={"max_ep": 24, "video_count": 24})
    assert scored["score"] >= 80.0
    assert any("多季累计集数吻合" in r for r in scored["reasons"])
