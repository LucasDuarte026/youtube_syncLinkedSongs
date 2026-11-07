"""End-to-end integration tests for main.py orchestrator (Task 7)."""

from pathlib import Path
from unittest.mock import MagicMock

from src.models import (
    BatchClassificationResponse,
    CorregedoriaReviewResponse,
    TaxonomyCatalogResponse,
    TaxonomyCategory,
    TrackClassification,
)


def test_main_orchestrator_end_to_end_with_mocked_yt_and_llm(tmp_path: Path):
    from main import run_pipeline

    fake_yt = MagicMock()
    fake_yt.get_liked_songs.return_value = {
        "tracks": [
            {
                "videoId": "vid_1",
                "title": "Rave de Favela",
                "artists": [{"name": "MC Lan"}, {"name": "Anitta"}],
                "duration_seconds": 175,
                "videoType": "MUSIC_VIDEO_TYPE_OMV",
            },
            {
                "videoId": "vid_2",
                "title": "Construção",
                "artists": [{"name": "Chico Buarque"}],
                "duration_seconds": 380,
                "videoType": "MUSIC_VIDEO_TYPE_ATV",
            },
        ]
    }
    fake_yt.get_library_playlists.return_value = [
        {"playlistId": "PL_SEED_1", "title": "MPB Antiga", "count": "5"},
    ]
    fake_yt.get_playlist.return_value = {
        "id": "PL_SEED_1",
        "title": "MPB Antiga",
        "description": "Minhas favoritas",
        "trackCount": 5,
        "tracks": [{"title": "Águas de Março", "artists": [{"name": "Elis Regina"}]}],
    }
    fake_yt.create_playlist.side_effect = lambda title, description, privacy_status="PRIVATE": f"PL_AUTO_{title[:4]}"
    fake_yt.add_playlist_items.return_value = {"status": "STATUS_SUCCEEDED"}

    mock_llm = MagicMock()

    def side_effect(prompt, response_model, **kwargs):
        if response_model is TaxonomyCatalogResponse:
            return TaxonomyCatalogResponse(
                categories=[
                    TaxonomyCategory(name="Funk - Festa & 150 BPM", description="Funk acelerado"),
                    TaxonomyCategory(name="MPB - Clássica & Bossa", description="MPB tradicional"),
                ]
            )
        if response_model is BatchClassificationResponse:
            return BatchClassificationResponse(
                classifications=[
                    TrackClassification(videoId="vid_1", playlists=["Funk - Festa & 150 BPM"], confidence=0.95),
                    TrackClassification(videoId="vid_2", playlists=["MPB - Clássica & Bossa"], confidence=0.98),
                ]
            )
        if response_model is CorregedoriaReviewResponse:
            return CorregedoriaReviewResponse(moves=[])
        raise AssertionError(f"Unexpected model {response_model}")

    mock_llm.generate_structured.side_effect = side_effect

    result = run_pipeline(
        data_dir=tmp_path,
        yt_client=fake_yt,
        llm_client=mock_llm,
        interactive=False,
        auto_execute_sync=True,
        sleep_seconds=0.0,
    )

    assert (tmp_path / "raw_likes.json").is_file()
    assert (tmp_path / "seed_playlists_reference.json").is_file()
    assert (tmp_path / "taxonomy_catalog.json").is_file()
    assert (tmp_path / "initial_plan.json").is_file()
    assert (tmp_path / "audited_plan.json").is_file()
    assert (tmp_path / "sync_state.json").is_file()
    assert result["coverage_percent"] == 100.0
    assert result["synced_playlists_count"] == 2
