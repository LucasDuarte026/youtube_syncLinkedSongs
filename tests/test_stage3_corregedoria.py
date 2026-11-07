"""Unit tests for Stage 3 Corregedoria QA Auditor & Interactive HITL (Task 5)."""

from unittest.mock import MagicMock

from config import (
    FOLDER_DOUBT,
    FOLDER_LONG_MUSICAL,
    FOLDER_NON_MUSIC_RANDOM,
)
from src.models import (
    BatchClassificationResponse,
    CorregedoriaMove,
    CorregedoriaReviewResponse,
    PlaylistPlan,
    TrackClassification,
    TrackItem,
    UserPromptAdjustmentAction,
)


def test_deterministic_audit_merges_fuzzy_names_recovers_orphans_and_isolates_long_tracks():
    from src.corregedoria_agent import run_deterministic_audit

    raw_tracks = [
        TrackItem(videoId="v1", title="Mandelão 1", artists="DJ K", duration_seconds=150),
        TrackItem(videoId="v2", title="Mandelão 2", artists="DJ Arana", duration_seconds=165),
        TrackItem(videoId="v3", title="Show Completo 2 Horas", artists="Banda X", duration_seconds=7200),
        TrackItem(videoId="v4_orphan", title="Música Esquecida", artists="Tim Maia", duration_seconds=210),
    ]

    # Initial plan has duplicate accent/no-accent folders, a 2-hour track in a normal folder, and misses v4_orphan
    initial_plan = PlaylistPlan(
        playlists={
            "Funk - Mandelão": ["v1"],
            "Funk - Mandelao": ["v2"],
            "Rock - Nacional & BR": ["v3"],
        }
    )

    mock_llm = MagicMock()
    mock_llm.generate_structured.return_value = BatchClassificationResponse(
        classifications=[
            TrackClassification(
                videoId="v4_orphan",
                playlists=["MPB - Soul & Groove Nacional"],
                confidence=0.95,
            )
        ]
    )

    audited_plan, report = run_deterministic_audit(initial_plan, raw_tracks, llm=mock_llm)

    # 1. Coverage must be 100%
    assert audited_plan.all_allocated_video_ids() == {"v1", "v2", "v3", "v4_orphan"}
    assert report.coverage_percent == 100.0
    assert report.orphans_recovered == 1

    # 2. "Funk - Mandelao" must be merged into "Funk - Mandelão"
    assert "Funk - Mandelao" not in audited_plan.playlists
    assert set(audited_plan.playlists["Funk - Mandelão"]) == {"v1", "v2"}

    # 3. Long track v3 (7200s) must be moved out of "Rock - Nacional & BR" into FOLDER_LONG_MUSICAL
    assert "v3" in audited_plan.playlists.get(FOLDER_LONG_MUSICAL, [])
    assert "v3" not in audited_plan.playlists.get("Rock - Nacional & BR", [])

    # 4. Orphan v4_orphan was recovered via LLM
    assert "v4_orphan" in audited_plan.playlists.get("MPB - Soul & Groove Nacional", [])


def test_semantic_audit_relocates_intruders_and_rescues_doubt():
    from src.corregedoria_agent import run_semantic_audit

    raw_tracks = [
        TrackItem(videoId="v_rock_in_funk", title="Enter Sandman", artists="Metallica", duration_seconds=330),
        TrackItem(videoId="v_gameplay", title="Gameplay GTA VI Trailer Breakdown", artists="IGN", duration_seconds=480, videoType="MUSIC_VIDEO_TYPE_UGC"),
        TrackItem(videoId="v_doubt", title="Chega de Saudade", artists="João Gilberto", duration_seconds=120),
    ]
    plan = PlaylistPlan(
        playlists={
            "Funk - Mandelão": ["v_rock_in_funk"],
            "Rock - Clássico & Hard Rock": ["v_gameplay"],
            FOLDER_DOUBT: ["v_doubt"],
        }
    )

    mock_llm = MagicMock()
    mock_llm.generate_structured.return_value = CorregedoriaReviewResponse(
        moves=[
            CorregedoriaMove(
                videoId="v_rock_in_funk",
                from_playlist="Funk - Mandelão",
                to_playlist="Rock - Clássico & Hard Rock",
                reason="Metallica não é Funk",
            ),
            CorregedoriaMove(
                videoId="v_gameplay",
                from_playlist="Rock - Clássico & Hard Rock",
                to_playlist=FOLDER_NON_MUSIC_RANDOM,
                reason="Vídeo de gameplay não é música",
            ),
            CorregedoriaMove(
                videoId="v_doubt",
                from_playlist=FOLDER_DOUBT,
                to_playlist="MPB - Clássica & Bossa",
                reason="Clássico de Bossa Nova identificado",
            ),
        ]
    )

    updated_plan, report = run_semantic_audit(plan, raw_tracks, llm=mock_llm)
    assert "v_rock_in_funk" in updated_plan.playlists.get("Rock - Clássico & Hard Rock", [])
    assert "v_rock_in_funk" not in updated_plan.playlists.get("Funk - Mandelão", [])
    assert "v_gameplay" in updated_plan.playlists.get(FOLDER_NON_MUSIC_RANDOM, [])
    assert "v_doubt" in updated_plan.playlists.get("MPB - Clássica & Bossa", [])
    assert FOLDER_DOUBT not in updated_plan.playlists
    assert report.doubt_tracks_rescued == 1
    assert report.semantic_moves_applied == 3


def test_apply_user_prompt_adjustment_merges_and_moves_tracks():
    from src.corregedoria_agent import apply_user_prompt_adjustment

    raw_tracks = [
        TrackItem(videoId="v1", title="Song 1", artists="Artist 1", duration_seconds=180),
        TrackItem(videoId="v2", title="Song 2", artists="Artist 2", duration_seconds=200),
    ]
    plan = PlaylistPlan(
        playlists={
            "Rock - Alternativo": ["v1"],
            "Rock - Indie": ["v2"],
        }
    )

    mock_llm = MagicMock()
    mock_llm.generate_structured.return_value = UserPromptAdjustmentAction(
        merge_playlists={
            "Rock - Alternativo": "Rock - Indie & Alternativo",
            "Rock - Indie": "Rock - Indie & Alternativo",
        },
        summary="Unificadas as pastas de Rock Indie e Alternativo.",
    )

    new_plan = apply_user_prompt_adjustment(
        plan,
        raw_tracks,
        instruction="Junte a pasta Rock - Alternativo com Rock - Indie em Rock - Indie & Alternativo",
        llm=mock_llm,
    )
    assert set(new_plan.playlists.get("Rock - Indie & Alternativo", [])) == {"v1", "v2"}
    assert "Rock - Alternativo" not in new_plan.playlists
    assert "Rock - Indie" not in new_plan.playlists
