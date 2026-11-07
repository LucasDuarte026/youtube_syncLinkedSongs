"""Pydantic data models, taxonomy normalization helpers, and checkpoint contracts."""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Set
from pydantic import BaseModel, Field

from config import (
    FOLDER_DOUBT,
    FOLDER_LONG_MUSICAL,
    FOLDER_NON_MUSIC_PODCASTS,
    FOLDER_NON_MUSIC_RANDOM,
)


def parse_duration_seconds(duration_seconds: Optional[int], duration_str: Optional[str]) -> int:
    """Return duration in seconds from integer field or fallback 'MM:SS' / 'HH:MM:SS' string."""
    if isinstance(duration_seconds, int) and duration_seconds > 0:
        return duration_seconds
    if not duration_str or not isinstance(duration_str, str):
        return 0
    parts = [p.strip() for p in duration_str.strip().split(":")]
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        return 0
    return 0


def normalize_playlist_name(name: str) -> str:
    """Enforce '[Macro] - [Subgênero/Vibe]' formatting and forbid bare macro-genres."""
    if not name or not name.strip():
        return FOLDER_DOUBT

    cleaned = re.sub(r"\s+", " ", name.strip())
    # Standardize dash separator
    for sep in (" — ", " – ", " - ", "—", "–"):
        if sep in cleaned:
            parts = [p.strip() for p in cleaned.split(sep, 1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                return f"{parts[0]} - {parts[1]}"

    if "-" in cleaned:
        parts = [p.strip() for p in cleaned.split("-", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            return f"{parts[0]} - {parts[1]}"

    # Bare macro-genre detected -> expand automatically
    return f"{cleaned} - Geral & Variados"


def canonical_comparison_key(name: str) -> str:
    """Return an accent-insensitive, lowercase, alphanumeric slug for deduplicating playlist names."""
    normalized = normalize_playlist_name(name)
    nfkd = unicodedata.normalize("NFKD", normalized)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", ascii_str.lower()).strip()


class TrackItem(BaseModel):
    """Sanitized track metadata extracted in Stage 1 (`data/raw_likes.json`)."""

    videoId: str = Field(description="Primary YouTube video identifier")
    title: str = Field(description="Track or video title")
    artists: str = Field(default="Desconhecido", description="Concatenated artist names")
    album: Optional[str] = Field(default=None, description="Album title if available")
    duration_seconds: int = Field(default=0, description="Duration in seconds")
    videoType: str = Field(default="UNKNOWN", description="YouTube Music videoType enum")


class SeedTrackSample(BaseModel):
    """Minimal sample track from a user's pre-existing playlist."""

    title: str
    artists: str


class SeedPlaylistRef(BaseModel):
    """Read-only reference of a user's pre-existing library playlist."""

    playlistId: str
    title: str
    description: str = ""
    track_count: int = 0
    sample_tracks: List[SeedTrackSample] = Field(default_factory=list)


class SeedPlaylistsBundle(BaseModel):
    """Container persisted to `data/seed_playlists_reference.json`."""

    protected_playlist_ids: List[str] = Field(default_factory=list)
    seed_playlists: List[SeedPlaylistRef] = Field(default_factory=list)


class TaxonomyCategory(BaseModel):
    """A single granular subgenre folder in the taxonomy catalog."""

    name: str = Field(description="Full Portuguese name in '[Macro] - [Subgênero/Vibe]' format")
    description: str = Field(default="", description="Sonic vibe and inclusion rules")


class TaxonomyCatalogResponse(BaseModel):
    """Structured LLM output for Pass 2A (Global Taxonomy Discovery)."""

    categories: List[TaxonomyCategory] = Field(
        description="Granular Portuguese subgenre playlists following '[Macro] - [Subgênero/Vibe]'"
    )


class TrackClassification(BaseModel):
    """Classification of a single videoId into one or more subgenre playlists."""

    videoId: str = Field(description="Exact videoId from the input batch")
    playlists: List[str] = Field(
        description="One or more Portuguese subgenre playlist names ('[Macro] - [Subgênero]')"
    )
    confidence: float = Field(default=0.9, ge=0.0, le=1.0, description="Classification confidence 0.0-1.0")


class BatchClassificationResponse(BaseModel):
    """Structured LLM output for Pass 2B (Batch Track Allocation)."""

    classifications: List[TrackClassification] = Field(
        description="Classification decision for every videoId in the batch"
    )


class PlaylistPlan(BaseModel):
    """State checkpoint mapping playlist names to lists of unique videoIds."""

    playlists: Dict[str, List[str]] = Field(default_factory=dict)

    def add_track(self, playlist_name: str, video_id: str) -> None:
        """Add a videoId to a normalized playlist without duplicates."""
        norm_name = normalize_playlist_name(playlist_name)
        bucket = self.playlists.setdefault(norm_name, [])
        if video_id not in bucket:
            bucket.append(video_id)

    def remove_track(self, playlist_name: str, video_id: str) -> None:
        """Remove a videoId from a playlist and prune empty playlists."""
        if playlist_name in self.playlists:
            self.playlists[playlist_name] = [v for v in self.playlists[playlist_name] if v != video_id]
            if not self.playlists[playlist_name]:
                del self.playlists[playlist_name]

    def all_allocated_video_ids(self) -> Set[str]:
        """Return the set of all unique videoIds present across all playlists."""
        allocated: Set[str] = set()
        for vids in self.playlists.values():
            allocated.update(vids)
        return allocated

    def missing_video_ids(self, expected_video_ids: Set[str]) -> Set[str]:
        """Return any expected videoIds that are not allocated to at least one playlist."""
        return expected_video_ids - self.all_allocated_video_ids()


class CorregedoriaMove(BaseModel):
    """A single corrective action decided by the Stage 3 Semantic Auditor."""

    videoId: str = Field(description="The videoId being moved or rescued")
    from_playlist: str = Field(description="Current playlist where the track is misplaced")
    to_playlist: str = Field(description="Target corrective playlist in '[Macro] - [Subgênero]' format")
    reason: str = Field(default="", description="Brief explanation of the correction")


class CorregedoriaReviewResponse(BaseModel):
    """Structured LLM output for Stage 3 Layer B (Semantic QA & Dúvida Rescue)."""

    moves: List[CorregedoriaMove] = Field(default_factory=list)


class UserPromptAdjustmentAction(BaseModel):
    """Structured LLM output for interpreting interactive user prompt adjustments in Stage 3."""

    merge_playlists: Dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of source_playlist_name -> target_playlist_name to merge/rename",
    )
    delete_playlists_to_doubt: List[str] = Field(
        default_factory=list,
        description="Playlists whose tracks should be moved to Doubt or redistributed",
    )
    track_moves: List[CorregedoriaMove] = Field(
        default_factory=list,
        description="Specific videoId moves between old or newly split playlists",
    )
    summary: str = Field(default="", description="Summary of changes applied")


class AuditReport(BaseModel):
    """Summary metrics produced by Stage 3 Corregedoria."""

    total_raw_tracks: int = 0
    total_allocated_tracks: int = 0
    coverage_percent: float = 100.0
    orphans_recovered: int = 0
    merged_playlists: List[str] = Field(default_factory=list)
    long_tracks_isolated: int = 0
    semantic_moves_applied: int = 0
    doubt_tracks_rescued: int = 0


class SyncedPlaylistRecord(BaseModel):
    """State of a single pipeline-managed playlist on YouTube Music."""

    playlistId: str
    title: str
    synced_video_ids: List[str] = Field(default_factory=list)
    failed_video_ids: List[str] = Field(default_factory=list)


class SyncState(BaseModel):
    """Persistent state saved to `data/sync_state.json` for idempotent Delta Sync."""

    protected_playlist_ids: List[str] = Field(default_factory=list)
    managed_playlists: Dict[str, SyncedPlaylistRecord] = Field(default_factory=dict)
