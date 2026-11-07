"""Unit tests for Stage 4 Idempotent YouTube Music Sync Executor (Task 6)."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from src.models import PlaylistPlan, SeedPlaylistsBundle


def test_sync_executor_protects_seed_playlists_chunks_by_50_and_isolates_failed_videos(tmp_path: Path):
    from src.sync_executor import execute_youtube_sync

    audited_plan_path = tmp_path / "audited_plan.json"
    sync_state_path = tmp_path / "sync_state.json"
    seed_ref_path = tmp_path / "seed_playlists_reference.json"

    # 60 tracks in "Funk - Mandelão" (tests chunking into 50 + 10)
    funk_vids = [f"v_{i}" for i in range(60)]
    # 2 tracks in "Rock - Indie & Alternativo" where "v_deleted" will fail on YouTube
    rock_vids = ["v_good", "v_deleted"]

    plan = PlaylistPlan(
        playlists={
            "Funk - Mandelão": funk_vids,
            "Rock - Indie & Alternativo": rock_vids,
        }
    )
    audited_plan_path.write_text(json.dumps(plan.model_dump()), encoding="utf-8")

    # Pre-existing manual playlist has the SAME title "Funk - Mandelão" and ID "PL_SEED_PROTECTED"
    seed_bundle = SeedPlaylistsBundle(protected_playlist_ids=["PL_SEED_PROTECTED"])
    seed_ref_path.write_text(json.dumps(seed_bundle.model_dump()), encoding="utf-8")

    fake_yt = MagicMock()
    fake_yt.get_library_playlists.return_value = [
        {"playlistId": "PL_SEED_PROTECTED", "title": "Funk - Mandelão"},
    ]

    created_playlists = []

    def fake_create_playlist(title, description, privacy_status="PRIVATE"):
        new_id = f"PL_NEW_{len(created_playlists) + 1}"
        created_playlists.append((new_id, title, description, privacy_status))
        return new_id

    fake_yt.create_playlist.side_effect = fake_create_playlist

    added_batches = []

    def fake_add_playlist_items(playlistId, videoIds, duplicates=False):
        assert playlistId != "PL_SEED_PROTECTED", "MUST NEVER touch protected seed playlist!"
        if "v_deleted" in videoIds and len(videoIds) > 1:
            raise RuntimeError("HTTP 400: Video v_deleted is unavailable")
        if videoIds == ["v_deleted"]:
            raise RuntimeError("HTTP 400: Video v_deleted is deleted")
        added_batches.append((playlistId, list(videoIds), duplicates))
        return {"status": "STATUS_SUCCEEDED"}

    fake_yt.add_playlist_items.side_effect = fake_add_playlist_items

    state = execute_youtube_sync(
        yt=fake_yt,
        audited_plan_path=audited_plan_path,
        sync_state_path=sync_state_path,
        seed_ref_path=seed_ref_path,
        chunk_size=50,
        sleep_seconds=0.0,
    )

    # 1. Protected playlist PL_SEED_PROTECTED was untouched; 2 new playlists were created
    assert len(created_playlists) == 2
    funk_record = state.managed_playlists["Funk - Mandelão"]
    assert funk_record.playlistId == "PL_NEW_1"
    assert len(funk_record.synced_video_ids) == 60

    # 2. 60 videos in Funk were split into a chunk of 50 and a chunk of 10
    funk_batches = [b[1] for b in added_batches if b[0] == "PL_NEW_1"]
    assert [len(b) for b in funk_batches] == [50, 10]

    # 3. In Rock, batch failed due to v_deleted, fell back to individual items, synced v_good and logged v_deleted
    rock_record = state.managed_playlists["Rock - Indie & Alternativo"]
    assert rock_record.synced_video_ids == ["v_good"]
    assert rock_record.failed_video_ids == ["v_deleted"]

    # 4. Idempotent Delta Sync: running again with the same plan makes 0 new create_playlist or add_playlist_items calls
    fake_yt.create_playlist.reset_mock()
    fake_yt.add_playlist_items.reset_mock()
    execute_youtube_sync(
        yt=fake_yt,
        audited_plan_path=audited_plan_path,
        sync_state_path=sync_state_path,
        seed_ref_path=seed_ref_path,
        chunk_size=50,
        sleep_seconds=0.0,
    )
    assert fake_yt.create_playlist.call_count == 0
    assert fake_yt.add_playlist_items.call_count == 0
