"""Stage 4: Idempotent YouTube Music Synchronizer (`data/sync_state.json`) with Seed Protection."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from config import (
    AUDITED_PLAN_PATH,
    AUTO_PLAYLIST_TAG,
    SEED_PLAYLISTS_PATH,
    SYNC_CHUNK_SIZE,
    SYNC_SLEEP_SECONDS,
    SYNC_STATE_PATH,
    resolve_ytmusic_client,
)
from src.models import (
    PlaylistPlan,
    SeedPlaylistsBundle,
    SyncedPlaylistRecord,
    SyncState,
)

logger = logging.getLogger("ytmusic_taxonomist.sync")


def _save_sync_state(state: SyncState, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _discover_remote_auto_playlists(
    yt: Any,
    protected_ids: Set[str],
) -> Dict[str, str]:
    """Return mapping `title -> playlistId` ONLY for non-protected playlists bearing AUTO_PLAYLIST_TAG."""
    remote_map: Dict[str, str] = {}
    try:
        lib_playlists = yt.get_library_playlists(limit=None) or []
    except Exception as err:
        logger.warning("Não foi possível listar playlists da biblioteca: %s", err)
        return remote_map

    for pl in lib_playlists:
        pl_id = str(pl.get("playlistId") or "").strip()
        pl_title = str(pl.get("title") or "").strip()
        if not pl_id or pl_id in protected_ids or pl_id == "LM":
            continue
        desc = str(pl.get("description") or "").strip()
        if AUTO_PLAYLIST_TAG in desc:
            remote_map[pl_title] = pl_id
    return remote_map


def execute_youtube_sync(
    yt: Any,
    audited_plan_path: Path = AUDITED_PLAN_PATH,
    sync_state_path: Path = SYNC_STATE_PATH,
    seed_ref_path: Optional[Path] = SEED_PLAYLISTS_PATH,
    chunk_size: int = SYNC_CHUNK_SIZE,
    sleep_seconds: float = SYNC_SLEEP_SECONDS,
    privacy_status: str = "PRIVATE",
) -> SyncState:
    """Create and populate approved playlists while strictly protecting pre-existing user playlists."""
    if not audited_plan_path.is_file():
        raise FileNotFoundError(
            f"Plano auditado '{audited_plan_path}' não encontrado. Rode o Estágio 3 antes de sincronizar."
        )

    plan = PlaylistPlan.model_validate_json(audited_plan_path.read_text(encoding="utf-8"))

    state = SyncState()
    if sync_state_path.is_file():
        try:
            state = SyncState.model_validate_json(sync_state_path.read_text(encoding="utf-8"))
        except Exception as err:
            logger.warning("Falha ao carregar sync_state.json existente, iniciando estado limpo: %s", err)

    protected_ids: Set[str] = set(state.protected_playlist_ids)
    if seed_ref_path and seed_ref_path.is_file():
        try:
            seed_bundle = SeedPlaylistsBundle.model_validate_json(seed_ref_path.read_text(encoding="utf-8"))
            protected_ids.update(seed_bundle.protected_playlist_ids)
        except Exception as err:
            logger.warning("Não foi possível ler seed_playlists_reference.json: %s", err)

    state.protected_playlist_ids = sorted(protected_ids)
    remote_auto = _discover_remote_auto_playlists(yt, protected_ids)

    for playlist_name in sorted(plan.playlists.keys()):
        video_ids = plan.playlists[playlist_name]
        if not video_ids:
            continue

        record = state.managed_playlists.get(playlist_name)
        if record is None or record.playlistId in protected_ids:
            # Check if a pipeline-created playlist with this title already exists remotely
            existing_auto_id = remote_auto.get(playlist_name)
            if existing_auto_id and existing_auto_id not in protected_ids:
                record = SyncedPlaylistRecord(
                    playlistId=existing_auto_id,
                    title=playlist_name,
                )
            else:
                desc = (
                    f"Subgênero '{playlist_name}' organizado automaticamente para o ArchiveTune "
                    f"{AUTO_PLAYLIST_TAG}"
                )
                logger.info("Criando nova playlist no YouTube Music: '%s'...", playlist_name)
                created_resp = yt.create_playlist(
                    title=playlist_name,
                    description=desc,
                    privacy_status=privacy_status,
                )
                if not isinstance(created_resp, str) or not created_resp.strip():
                    logger.error(
                        "Falha ao criar playlist '%s': resposta inesperada %s",
                        playlist_name,
                        created_resp,
                    )
                    continue
                record = SyncedPlaylistRecord(
                    playlistId=created_resp.strip(),
                    title=playlist_name,
                )
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)

            state.managed_playlists[playlist_name] = record
            _save_sync_state(state, sync_state_path)

        # Safety check: never touch a protected playlist ID under any circumstance
        if record.playlistId in protected_ids:
            raise RuntimeError(
                f"ABORTADO: Tentativa bloqueada de escrever na playlist semente protegida {record.playlistId}!"
            )

        already_synced = set(record.synced_video_ids)
        already_failed = set(record.failed_video_ids)
        pending_ids = [vid for vid in video_ids if vid not in already_synced and vid not in already_failed]

        if not pending_ids:
            logger.info("Playlist '%s' já está 100%% sincronizada (%d faixas).", playlist_name, len(already_synced))
            continue

        for i in range(0, len(pending_ids), chunk_size):
            chunk = pending_ids[i : i + chunk_size]
            try:
                logger.info(
                    "Adicionando lote de %d faixas em '%s' (%s)...",
                    len(chunk),
                    playlist_name,
                    record.playlistId,
                )
                res = yt.add_playlist_items(record.playlistId, chunk, duplicates=False)
                if isinstance(res, dict) and res.get("status") == "STATUS_FAILED":
                    raise RuntimeError(f"STATUS_FAILED returned by YouTube Music: {res}")
                for vid in chunk:
                    if vid not in record.synced_video_ids:
                        record.synced_video_ids.append(vid)
            except Exception as batch_err:
                logger.warning(
                    "Lote de %d faixas falhou em '%s' (%s). Fazendo fallback item a item...",
                    len(chunk),
                    playlist_name,
                    batch_err,
                )
                for vid in chunk:
                    try:
                        single_res = yt.add_playlist_items(record.playlistId, [vid], duplicates=False)
                        if isinstance(single_res, dict) and single_res.get("status") == "STATUS_FAILED":
                            raise RuntimeError(str(single_res))
                        if vid not in record.synced_video_ids:
                            record.synced_video_ids.append(vid)
                    except Exception as item_err:
                        logger.error(
                            "Faixa '%s' falhou individualmente em '%s' (deletada ou bloqueada): %s",
                            vid,
                            playlist_name,
                            item_err,
                        )
                        if vid not in record.failed_video_ids:
                            record.failed_video_ids.append(vid)
                    if sleep_seconds > 0:
                        time.sleep(min(sleep_seconds, 0.5))

            _save_sync_state(state, sync_state_path)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    return state


def run_stage4(
    auth_path: Optional[Path | str] = None,
    audited_plan_path: Path = AUDITED_PLAN_PATH,
    sync_state_path: Path = SYNC_STATE_PATH,
    seed_ref_path: Path = SEED_PLAYLISTS_PATH,
) -> SyncState:
    """Instantiate YTMusic client and execute Stage 4 synchronization."""
    yt = resolve_ytmusic_client(auth_path=auth_path)
    return execute_youtube_sync(
        yt=yt,
        audited_plan_path=audited_plan_path,
        sync_state_path=sync_state_path,
        seed_ref_path=seed_ref_path,
    )
