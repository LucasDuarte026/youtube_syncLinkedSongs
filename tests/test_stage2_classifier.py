"""Unit tests for Stage 2 Two-Pass Taxonomist & Batch Classifier (Task 4)."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from config import (
    FOLDER_DOUBT,
    FOLDER_LONG_MUSICAL,
    FOLDER_NON_MUSIC_PODCASTS,
)
from src.models import (
    BatchClassificationResponse,
    PlaylistPlan,
    SeedPlaylistRef,
    SeedPlaylistsBundle,
    SeedTrackSample,
    TaxonomyCatalogResponse,
    TaxonomyCategory,
    TrackClassification,
    TrackItem,
)


def test_discover_taxonomy_catalog_uses_seed_inspiration_and_normalizes():
    from src.classifier_agent import discover_taxonomy_catalog

    tracks = [
        TrackItem(videoId="v1", title="Automotivo Bibi", artists="Bibi Babydoll", duration_seconds=150),
        TrackItem(videoId="v2", title="Tempo Perdido", artists="Legião Urbana", duration_seconds=300),
    ]
    seeds = [
        SeedPlaylistRef(
            playlistId="PL1",
            title="Funk Bruxaria",
            sample_tracks=[SeedTrackSample(title="Montagem Anos 2000", artists="DJ Arana")],
        )
    ]

    mock_llm = MagicMock()
    mock_llm.generate_structured.return_value = TaxonomyCatalogResponse(
        categories=[
            TaxonomyCategory(name="Funk - Mandelão", description="Batida automotiva e bruxaria"),
            TaxonomyCategory(name="Rock - Nacional & BR", description="Rock brasileiro clássico"),
            TaxonomyCategory(name="Eletrônica", description="Bare macro genre should normalize"),
        ]
    )

    catalog = discover_taxonomy_catalog(tracks, seeds, mock_llm)
    names = [c.name for c in catalog]
    assert "Funk - Mandelão" in names
    assert "Rock - Nacional & BR" in names
    assert "Eletrônica - Geral & Variados" in names


def test_classify_tracks_pipeline_enforces_long_media_doubt_and_delta_sync(tmp_path: Path):
    from src.classifier_agent import classify_tracks_pipeline

    raw_likes_path = tmp_path / "raw_likes.json"
    seed_ref_path = tmp_path / "seed_playlists_reference.json"
    initial_plan_path = tmp_path / "initial_plan.json"
    catalog_path = tmp_path / "taxonomy_catalog.json"

    tracks = [
        TrackItem(videoId="v_funk", title="Mega Mandelão", artists="DJ K", duration_seconds=160, videoType="MUSIC_VIDEO_TYPE_ATV"),
        TrackItem(videoId="v_set", title="Boiler Room Set 2025", artists="Vintage Culture", duration_seconds=3600, videoType="MUSIC_VIDEO_TYPE_UGC"),
        TrackItem(videoId="v_podcast", title="Cortes do Flow - Entrevista", artists="Flow Podcast", duration_seconds=1200, videoType="MUSIC_VIDEO_TYPE_UGC"),
        TrackItem(videoId="v_omitted", title="Unknown Track", artists="Mystery", duration_seconds=180, videoType="MUSIC_VIDEO_TYPE_UGC"),
    ]
    raw_likes_path.write_text(
        json.dumps([t.model_dump() for t in tracks], ensure_ascii=False),
        encoding="utf-8",
    )
    seed_ref_path.write_text(
        json.dumps(SeedPlaylistsBundle().model_dump(), ensure_ascii=False),
        encoding="utf-8",
    )

    mock_llm = MagicMock()

    def side_effect(prompt, response_model, **kwargs):
        if response_model is TaxonomyCatalogResponse:
            return TaxonomyCatalogResponse(
                categories=[
                    TaxonomyCategory(name="Funk - Mandelão", description="Funk automotivo"),
                    TaxonomyCategory(name="Eletrônica - Festival EDM & Progressive House", description="EDM"),
                ]
            )
        if response_model is BatchClassificationResponse:
            # Notice v_omitted is intentionally left out by LLM to test deterministic fallback to FOLDER_DOUBT,
            # and v_set (3600s) is classified by LLM as Eletrônica to test deterministic routing to FOLDER_LONG_MUSICAL
            return BatchClassificationResponse(
                classifications=[
                    TrackClassification(videoId="v_funk", playlists=["Funk - Mandelão"], confidence=0.95),
                    TrackClassification(videoId="v_set", playlists=["Eletrônica - Festival EDM & Progressive House"], confidence=0.90),
                    TrackClassification(videoId="v_podcast", playlists=[FOLDER_NON_MUSIC_PODCASTS], confidence=0.98),
                ]
            )
        raise AssertionError(f"Unexpected model: {response_model}")

    mock_llm.generate_structured.side_effect = side_effect

    plan = classify_tracks_pipeline(
        raw_likes_path=raw_likes_path,
        seed_ref_path=seed_ref_path,
        initial_plan_path=initial_plan_path,
        catalog_path=catalog_path,
        llm=mock_llm,
        batch_size=45,
        full_reclassify=False,
    )

    assert "v_funk" in plan.playlists.get("Funk - Mandelão", [])
    # Long musical video (>= 720s) must be isolated in FOLDER_LONG_MUSICAL
    assert "v_set" in plan.playlists.get(FOLDER_LONG_MUSICAL, [])
    assert "v_set" not in plan.playlists.get("Eletrônica - Festival EDM & Progressive House", [])
    # Non-music podcast stays in Non-Music folder even if long
    assert "v_podcast" in plan.playlists.get(FOLDER_NON_MUSIC_PODCASTS, [])
    # Omitted videoId must be caught and placed in Doubt folder
    assert "v_omitted" in plan.playlists.get(FOLDER_DOUBT, [])

    # Delta Sync check: running again with full_reclassify=False should make 0 new batch LLM calls
    mock_llm.generate_structured.reset_mock()
    plan_second = classify_tracks_pipeline(
        raw_likes_path=raw_likes_path,
        seed_ref_path=seed_ref_path,
        initial_plan_path=initial_plan_path,
        catalog_path=catalog_path,
        llm=mock_llm,
        batch_size=45,
        full_reclassify=False,
    )
    assert mock_llm.generate_structured.call_count == 0
    assert plan_second.all_allocated_video_ids() == {"v_funk", "v_set", "v_podcast", "v_omitted"}
