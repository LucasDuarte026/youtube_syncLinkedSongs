"""Stage 1: Extraction and Sanitization of Liked Songs + Read-Only Seed Playlists Reference."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from config import (
    AUTO_PLAYLIST_TAG,
    RAW_LIKES_PATH,
    SEED_PLAYLISTS_PATH,
    SEED_SAMPLE_TRACKS_LIMIT,
    SYNC_STATE_PATH,
    resolve_ytmusic_client,
)
from src.models import (
    SeedPlaylistRef,
    SeedPlaylistsBundle,
    SeedTrackSample,
    SyncState,
    TrackItem,
    parse_duration_seconds,
)

logger = logging.getLogger("ytmusic_taxonomist.ingestor")

# Built-in YouTube Music system playlist IDs to exclude from seed style references
SYSTEM_PLAYLIST_IDS = {"LM", "SE", "RDPN"}


def _format_artists(artists_raw: Any) -> str:
    """Concatenate artist names from ytmusicapi track artist list."""
    if isinstance(artists_raw, str) and artists_raw.strip():
        return artists_raw.strip()
    if isinstance(artists_raw, list):
        names = [
            str(a.get("name", "")).strip()
            for a in artists_raw
            if isinstance(a, dict) and a.get("name")
        ]
        if names:
            return ", ".join(names)
    return "Desconhecido"


def sanitize_track(raw_track: Dict[str, Any]) -> Optional[TrackItem]:
    """Extract and normalize only essential metadata from a raw ytmusicapi track dict."""
    if not isinstance(raw_track, dict):
        return None
    video_id = raw_track.get("videoId")
    if not video_id or not str(video_id).strip():
        return None

    title = str(raw_track.get("title") or "Sem Título").strip()
    artists = _format_artists(raw_track.get("artists"))

    album_raw = raw_track.get("album")
    album_name: Optional[str] = None
    if isinstance(album_raw, dict):
        album_name = album_raw.get("name")
    elif isinstance(album_raw, str):
        album_name = album_raw

    duration_secs = parse_duration_seconds(
        raw_track.get("duration_seconds"),
        raw_track.get("duration"),
    )
    video_type = str(raw_track.get("videoType") or "UNKNOWN").strip()

    return TrackItem(
        videoId=str(video_id).strip(),
        title=title,
        artists=artists,
        album=album_name,
        duration_seconds=duration_secs,
        videoType=video_type,
    )


def extract_liked_songs(
    yt: Any,
    output_path: Path = RAW_LIKES_PATH,
) -> List[TrackItem]:
    """Fetch 100% of Liked Songs (`limit=None`), sanitize, deduplicate by videoId, and persist JSON."""
    logger.info("Extraindo músicas curtidas via yt.get_liked_songs(limit=None)...")
    response = yt.get_liked_songs(limit=None)
    raw_tracks = response.get("tracks", []) if isinstance(response, dict) else []

    sanitized: List[TrackItem] = []
    seen_ids: Set[str] = set()

    for raw in raw_tracks:
        item = sanitize_track(raw)
        if item and item.videoId not in seen_ids:
            seen_ids.add(item.videoId)
            sanitized.append(item)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps([t.model_dump() for t in sanitized], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Salvas %d faixas únicas em %s", len(sanitized), output_path)
    return sanitized


def extract_seed_playlists_reference(
    yt: Any,
    output_path: Path = SEED_PLAYLISTS_PATH,
    sample_limit: int = SEED_SAMPLE_TRACKS_LIMIT,
    sync_state_path: Path = SYNC_STATE_PATH,
) -> SeedPlaylistsBundle:
    """Inspect existing library playlists as a Read-Only Reference and mark their IDs as protected."""
    logger.info("Inspecionando playlists existentes na conta (Read-Only Reference)...")
    managed_ids: Set[str] = set()
    if sync_state_path.is_file():
        try:
            state = SyncState.model_validate_json(sync_state_path.read_text(encoding="utf-8"))
            for rec in state.managed_playlists.values():
                managed_ids.add(rec.playlistId)
        except Exception as err:
            logger.warning("Não foi possível ler sync_state.json existente: %s", err)

    library_playlists = yt.get_library_playlists(limit=None) or []
    protected_ids: List[str] = []
    seed_refs: List[SeedPlaylistRef] = []

    for pl in library_playlists:
        pl_id = str(pl.get("playlistId") or "").strip()
        pl_title = str(pl.get("title") or "").strip()
        if not pl_id or pl_id in SYSTEM_PLAYLIST_IDS or pl_id in managed_ids:
            continue

        try:
            details = yt.get_playlist(pl_id, limit=sample_limit) or {}
        except Exception as err:
            logger.warning("Falha ao inspecionar playlist '%s' (%s): %s", pl_title, pl_id, err)
            protected_ids.append(pl_id)
            continue

        desc = str(details.get("description") or pl.get("description") or "").strip()
        # Skip playlists created by our own pipeline
        if AUTO_PLAYLIST_TAG in desc:
            continue

        protected_ids.append(pl_id)
        raw_samples = details.get("tracks") or []
        samples: List[SeedTrackSample] = []
        for tr in raw_samples[:sample_limit]:
            if isinstance(tr, dict) and tr.get("title"):
                samples.append(
                    SeedTrackSample(
                        title=str(tr.get("title")).strip(),
                        artists=_format_artists(tr.get("artists")),
                    )
                )

        count_raw = details.get("trackCount") or pl.get("count") or len(raw_samples)
        try:
            track_count = int(str(count_raw).replace(",", "").replace(".", "").strip())
        except ValueError:
            track_count = len(raw_samples)

        seed_refs.append(
            SeedPlaylistRef(
                playlistId=pl_id,
                title=pl_title,
                description=desc,
                track_count=track_count,
                sample_tracks=samples,
            )
        )

    bundle = SeedPlaylistsBundle(
        protected_playlist_ids=protected_ids,
        seed_playlists=seed_refs,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(bundle.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "Referência de sementes salva em %s (%d playlists protegidas)",
        output_path,
        len(protected_ids),
    )
    return bundle


def run_stage1(
    auth_path: Optional[Path | str] = None,
    raw_likes_path: Path = RAW_LIKES_PATH,
    seed_playlists_path: Path = SEED_PLAYLISTS_PATH,
) -> Dict[str, Any]:
    """Execute full Stage 1 ingestion (Liked Songs + Seed Playlists Read-Only Reference)."""
    yt = resolve_ytmusic_client(auth_path=auth_path)
    tracks = extract_liked_songs(yt, output_path=raw_likes_path)
    seeds = extract_seed_playlists_reference(yt, output_path=seed_playlists_path)
    return {
        "tracks_count": len(tracks),
        "seed_playlists_count": len(seeds.seed_playlists),
        "protected_playlist_ids": seeds.protected_playlist_ids,
    }
