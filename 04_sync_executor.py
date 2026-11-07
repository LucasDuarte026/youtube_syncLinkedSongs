#!/usr/bin/env python3
"""CLI Entrypoint for Stage 4: Idempotent YouTube Music Synchronizer."""

from __future__ import annotations

import argparse
import logging
import sys
from rich.console import Console

from config import AUDITED_PLAN_PATH, SEED_PLAYLISTS_PATH, SYNC_STATE_PATH
from src.sync_executor import run_stage4

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Estágio 4: Sincroniza audited_plan.json com a conta do YouTube Music de forma idempotente."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Confirma explicitamente a criação e população das playlists no YouTube Music.",
    )
    parser.add_argument("--auth", type=str, default=None, help="Caminho para browser.json ou oauth.json")
    args = parser.parse_args()

    if not args.execute:
        console.print(
            "[bold yellow]⚠ Modo de segurança:[/bold yellow] O Estágio 4 altera sua biblioteca do YouTube Music.\n"
            "Passe a flag [bold cyan]--execute[/bold cyan] (ou aprove pelo menu interativo do Estágio 3 / main.py) para executar."
        )
        sys.exit(0)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    console.print("[bold cyan]▶ Estágio 4: Sincronizador YouTube Music (Idempotente + Proteção de Sementes)[/bold cyan]")

    state = run_stage4(
        auth_path=args.auth,
        audited_plan_path=AUDITED_PLAN_PATH,
        sync_state_path=SYNC_STATE_PATH,
        seed_ref_path=SEED_PLAYLISTS_PATH,
    )
    total_synced = sum(len(r.synced_video_ids) for r in state.managed_playlists.values())
    total_failed = sum(len(r.failed_video_ids) for r in state.managed_playlists.values())
    console.print(
        f"[bold green]✔ Sincronização concluída![/bold green] "
        f"{len(state.managed_playlists)} playlists gerenciadas, {total_synced} inserções concluídas, "
        f"{total_failed} faixas indisponíveis ignoradas. Estado salvo em [bold]{SYNC_STATE_PATH}[/bold]."
    )


if __name__ == "__main__":
    main()
