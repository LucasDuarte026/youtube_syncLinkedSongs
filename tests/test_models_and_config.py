"""Unit tests for config.py and src/models.py (Task 1)."""

from pathlib import Path
import pytest


def test_parse_duration_seconds_variants():
    from src.models import parse_duration_seconds

    assert parse_duration_seconds(215, "3:35") == 215
    assert parse_duration_seconds(None, "3:35") == 215
    assert parse_duration_seconds(None, "1:02:05") == 3725
    assert parse_duration_seconds(None, None) == 0


def test_normalize_playlist_name_enforces_macro_subgenre():
    from src.models import normalize_playlist_name

    # Bare macro-genres are prohibited and must be expanded
    assert normalize_playlist_name("Funk") == "Funk - Geral & Variados"
    assert normalize_playlist_name("Rock") == "Rock - Geral & Variados"
    assert normalize_playlist_name("Eletrônica") == "Eletrônica - Geral & Variados"
    # Proper format is preserved with normalized spacing
    assert normalize_playlist_name("  Funk  -  Mandelão ") == "Funk - Mandelão"


def test_playlist_plan_coverage_and_reverse_lookup():
    from src.models import PlaylistPlan

    plan = PlaylistPlan(
        playlists={
            "Funk - Mandelão": ["v1", "v2"],
            "MPB - Clássica & Bossa": ["v2", "v3"],
        }
    )
    assert plan.all_allocated_video_ids() == {"v1", "v2", "v3"}
    assert plan.missing_video_ids({"v1", "v2", "v3", "v4"}) == {"v4"}


def test_resolve_ytmusic_client_raises_helpful_error_when_auth_missing(tmp_path: Path):
    from config import resolve_ytmusic_client

    with pytest.raises(FileNotFoundError) as exc_info:
        resolve_ytmusic_client(
            auth_path=tmp_path / "nonexistent_auth.json",
            search_dir=tmp_path,
        )
    msg = str(exc_info.value)
    assert "ytmusicapi browser" in msg
    assert "ytmusicapi oauth" in msg
