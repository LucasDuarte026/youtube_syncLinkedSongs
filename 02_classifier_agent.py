#!/usr/bin/env python3
"""CLI Entrypoint for Stage 2: Two-Pass Taxonomist & Batch Classifier Agent."""

from __future__ import annotations

import argparse
import logging
from rich.console import Console

from config import BATCH_SIZE, INITIAL_PLAN_PATH, RAW_LIKES_PATH, SEED_PLAYLISTS_PATH, TAXONOMY_CATALOG_PATH
from src.classifier_agent import classify_tracks_pipeline

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Estágio 2: Classifica as músicas de raw_likes.json em subgêneros granulares (initial_plan.json)."
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help="Quantidade de músicas por lote (padrão: 45)",
    )
    parser.add_argument(
        "--full-reclassify",
        action="store_true",
        help="Reclassifica todas as músicas do zero ignorando checkpoints anteriores.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    console.print("[bold cyan]▶ Estágio 2: Agente Taxonomista / Classificador (Passada Dupla)[/bold cyan]")

    plan = classify_tracks_pipeline(
        raw_likes_path=RAW_LIKES_PATH,
        seed_ref_path=SEED_PLAYLISTS_PATH,
        initial_plan_path=INITIAL_PLAN_PATH,
        catalog_path=TAXONOMY_CATALOG_PATH,
        batch_size=args.batch_size,
        full_reclassify=args.full_reclassify,
    )
    console.print(
        f"[bold green]✔ Classificação concluída![/bold green] "
        f"{len(plan.playlists)} playlists geradas cobrindo {len(plan.all_allocated_video_ids())} faixas únicas em [bold]{INITIAL_PLAN_PATH}[/bold]."
    )


if __name__ == "__main__":
    main()
