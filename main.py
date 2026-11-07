#!/usr/bin/env python3
"""Main Orchestrator for the YouTube Music Multi-Agent Taxonomist & Sync Pipeline."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from rich.console import Console

from config import (
    BATCH_SIZE,
    DATA_DIR,
    SYNC_SLEEP_SECONDS,
    resolve_ytmusic_client,
)
from src.classifier_agent import classify_tracks_pipeline
from src.corregedoria_agent import run_stage3_corregedoria
from src.ingestor import extract_liked_songs, extract_seed_playlists_reference
from src.llm_adapter import StructuredLLMClient
from src.sync_executor import execute_youtube_sync

console = Console()


def run_pipeline(
    data_dir: Path = DATA_DIR,
    auth_path: Optional[Path | str] = None,
    yt_client: Optional[Any] = None,
    llm_client: Optional[StructuredLLMClient] = None,
    stage: str = "all",
    batch_size: int = BATCH_SIZE,
    full_reclassify: bool = False,
    interactive: bool = True,
    auto_execute_sync: bool = False,
    sleep_seconds: float = SYNC_SLEEP_SECONDS,
) -> Dict[str, Any]:
    """Execute the 4-stage YouTube Music Taxonomist & Sync pipeline with JSON checkpoints."""
    data_dir.mkdir(parents=True, exist_ok=True)
    raw_likes_path = data_dir / "raw_likes.json"
    seed_ref_path = data_dir / "seed_playlists_reference.json"
    catalog_path = data_dir / "taxonomy_catalog.json"
    initial_plan_path = data_dir / "initial_plan.json"
    audited_plan_path = data_dir / "audited_plan.json"
    sync_state_path = data_dir / "sync_state.json"

    llm = llm_client or StructuredLLMClient()
    result_summary: Dict[str, Any] = {}

    # Stage 1: Ingestion + Read-Only Seed Reference
    if stage in ("all", "1"):
        console.print("\n[bold cyan]=== ESTÁGIO 1: Extração de Liked Songs & Playlists Semente (Read-Only) ===[/bold cyan]")
        yt = yt_client or resolve_ytmusic_client(auth_path=auth_path)
        tracks = extract_liked_songs(yt, output_path=raw_likes_path)
        seeds = extract_seed_playlists_reference(
            yt,
            output_path=seed_ref_path,
            sync_state_path=sync_state_path,
        )
        result_summary["tracks_extracted"] = len(tracks)
        result_summary["protected_seed_playlists"] = len(seeds.protected_playlist_ids)

    # Stage 2: Two-Pass Taxonomist & Batch Classifier
    if stage in ("all", "2"):
        console.print("\n[bold cyan]=== ESTÁGIO 2: Agente Taxonomista / Classificador (Passada Dupla) ===[/bold cyan]")
        plan = classify_tracks_pipeline(
            raw_likes_path=raw_likes_path,
            seed_ref_path=seed_ref_path,
            initial_plan_path=initial_plan_path,
            catalog_path=catalog_path,
            llm=llm,
            batch_size=batch_size,
            full_reclassify=full_reclassify,
        )
        result_summary["initial_playlists_count"] = len(plan.playlists)

    # Stage 3: Corregedoria QA Auditor (Layer A + Layer B + HITL)
    decision = "dry_run"
    if stage in ("all", "3"):
        console.print("\n[bold cyan]=== ESTÁGIO 3: Agente de Corregedoria / Auditoria QA ===[/bold cyan]")
        audited_plan, report, decision = run_stage3_corregedoria(
            raw_likes_path=raw_likes_path,
            initial_plan_path=initial_plan_path,
            audited_plan_path=audited_plan_path,
            llm=llm,
            interactive=interactive,
        )
        result_summary["audited_playlists_count"] = len(audited_plan.playlists)
        result_summary["coverage_percent"] = report.coverage_percent
        result_summary["corregedoria_decision"] = decision

    # Stage 4: Idempotent YouTube Music Synchronizer
    should_sync = (stage == "4" and auto_execute_sync) or (
        stage == "all" and (decision == "approve_and_sync" or auto_execute_sync)
    )
    if should_sync:
        console.print("\n[bold cyan]=== ESTÁGIO 4: Sincronizador YouTube Music (Idempotente) ===[/bold cyan]")
        yt = yt_client or resolve_ytmusic_client(auth_path=auth_path)
        sync_state = execute_youtube_sync(
            yt=yt,
            audited_plan_path=audited_plan_path,
            sync_state_path=sync_state_path,
            seed_ref_path=seed_ref_path,
            sleep_seconds=sleep_seconds,
        )
        result_summary["synced_playlists_count"] = len(sync_state.managed_playlists)
    else:
        result_summary["synced_playlists_count"] = 0

    return result_summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Orquestrador do YouTube Music Multi-Agent Taxonomist & Sync Pipeline (ArchiveTune)."
    )
    parser.add_argument(
        "--stage",
        choices=["all", "1", "2", "3", "4"],
        default="all",
        help="Executa todos os estágios ('all') ou um estágio específico (1, 2, 3, 4).",
    )
    parser.add_argument("--auth", type=str, default=None, help="Caminho para browser.json ou oauth.json.")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Tamanho do lote no Estágio 2 (padrão: 45).")
    parser.add_argument(
        "--full-reclassify",
        action="store_true",
        help="Reclassifica 100%% das músicas ignorando checkpoints anteriores.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Autoriza a execução do Estágio 4 (Sincronização com YouTube Music).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Roda sem menu interativo e exporta audited_plan.json sem sincronizar.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run_pipeline(
        auth_path=args.auth,
        stage=args.stage,
        batch_size=args.batch_size,
        full_reclassify=args.full_reclassify,
        interactive=not args.dry_run,
        auto_execute_sync=args.execute,
    )


if __name__ == "__main__":
    main()
