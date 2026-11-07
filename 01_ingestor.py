#!/usr/bin/env python3
"""CLI Entrypoint for Stage 1: Liked Songs & Seed Playlists Ingestor."""

from __future__ import annotations

import argparse
import logging
from rich.console import Console

from config import RAW_LIKES_PATH, SEED_PLAYLISTS_PATH
from src.ingestor import run_stage1

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Estágio 1: Extrai Liked Songs (raw_likes.json) e referência Read-Only das playlists atuais."
    )
    parser.add_argument("--auth", type=str, default=None, help="Caminho para browser.json ou oauth.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    console.print("[bold cyan]▶ Estágio 1: Extração e Sanitização (YouTube Music)[/bold cyan]")

    summary = run_stage1(auth_path=args.auth)
    console.print(
        f"[bold green]✔ Extração concluída![/bold green] "
        f"{summary['tracks_count']} músicas salvas em [bold]{RAW_LIKES_PATH}[/bold] e "
        f"{summary['seed_playlists_count']} playlists semente protegidas registradas em [bold]{SEED_PLAYLISTS_PATH}[/bold]."
    )


if __name__ == "__main__":
    main()
