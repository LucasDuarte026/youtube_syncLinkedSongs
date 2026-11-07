"""Unit tests for Stage 1 Ingestor (`src/ingestor.py` & `01_ingestor.py`) (Task 3)."""

import json
from pathlib import Path
from unittest.mock import MagicMock


def test_sanitize_track_normalizes_all_fields_and_fallback_duration():
    from src.ingestor import sanitize_track

    raw = {
        "videoId": "abc123xyz",
        "title": "Baile de Favela",
        "artists": [{"name": "MC João"}, {"name": "DJ R7"}],
        "album": {"name": "Single 2015", "id": "MPREb_1"},
        "duration": "3:10",
        "duration_seconds": None,
        "videoType": "MUSIC_VIDEO_TYPE_OMV",
    }
    item = sanitize_track(raw)
    assert item is not None
    assert item.videoId == "abc123xyz"
    assert item.title == "Baile de Favela"
    assert item.artists == "MC João, DJ R7"
    assert item.album == "Single 2015"
    assert item.duration_seconds == 190
    assert item.videoType == "MUSIC_VIDEO_TYPE_OMV"


def test_sanitize_track_skips_missing_video_id():
    from src.ingestor import sanitize_track

    assert sanitize_track({"title": "No ID Track"}) is None


def test_extract_liked_songs_and_seed_playlists_reference(tmp_path: Path):
    from config import AUTO_PLAYLIST_TAG
    from src.ingestor import extract_liked_songs, extract_seed_playlists_reference

    fake_yt = MagicMock()
    fake_yt.get_liked_songs.return_value = {
        "tracks": [
            {
                "videoId": "v1",
                "title": "Song One",
                "artists": [{"name": "Artist A"}],
                "album": {"name": "Album A"},
                "duration_seconds": 200,
                "videoType": "MUSIC_VIDEO_TYPE_ATV",
            },
            {
                "videoId": "v1",  # duplicate should be deduplicated
                "title": "Song One",
                "artists": [{"name": "Artist A"}],
                "duration_seconds": 200,
            },
            {
                "videoId": "v2",
                "title": "Podcast Episode #42",
                "artists": [{"name": "Flow"}],
                "duration": "1:30:00",
                "videoType": "MUSIC_VIDEO_TYPE_UGC",
            },
        ]
    }

    raw_likes_file = tmp_path / "raw_likes.json"
    tracks = extract_liked_songs(fake_yt, output_path=raw_likes_file)
    assert len(tracks) == 2
    assert raw_likes_file.is_file()
    persisted = json.loads(raw_likes_file.read_text(encoding="utf-8"))
    assert len(persisted) == 2
    assert persisted[1]["duration_seconds"] == 5400

    # Test seed playlists extraction (Read-Only Reference + protected IDs)
    fake_yt.get_library_playlists.return_value = [
        {"playlistId": "LM", "title": "Liked Music"},
        {"playlistId": "PL_SEED_1", "title": "Meu Funk Antigo", "count": "15"},
        {"playlistId": "PL_AUTO_1", "title": "Funk - Mandelão", "count": "20"},
    ]

    def fake_get_playlist(playlist_id, limit=10):
        if playlist_id == "PL_SEED_1":
            return {
                "id": "PL_SEED_1",
                "title": "Meu Funk Antigo",
                "description": "Minhas favoritas das antigas",
                "trackCount": 15,
                "tracks": [
                    {"title": "Rap da Felicidade", "artists": [{"name": "Cidinho e Doca"}]},
                    {"title": "Nosso Sonho", "artists": [{"name": "Claudinho & Buchecha"}]},
                ],
            }
        if playlist_id == "PL_AUTO_1":
            return {
                "id": "PL_AUTO_1",
                "title": "Funk - Mandelão",
                "description": f"Gerada automaticamente {AUTO_PLAYLIST_TAG}",
                "trackCount": 20,
                "tracks": [],
            }
        return {}

    fake_yt.get_playlist.side_effect = fake_get_playlist

    seed_file = tmp_path / "seed_playlists_reference.json"
    bundle = extract_seed_playlists_reference(fake_yt, output_path=seed_file, sample_limit=10)
    assert "PL_SEED_1" in bundle.protected_playlist_ids
    assert "PL_AUTO_1" not in bundle.protected_playlist_ids
    assert len(bundle.seed_playlists) == 1
    assert bundle.seed_playlists[0].title == "Meu Funk Antigo"
    assert len(bundle.seed_playlists[0].sample_tracks) == 2
