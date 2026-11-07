#!/usr/bin/env python3
"""CLI Entrypoint for Stage 3: Corregedoria QA Auditor & HITL Approval."""

from __future__ import annotations

import argparse
import logging
from rich.console import Console

from config import AUDITED_PLAN_PATH, INITIAL_PLAN_PATH, RAW_LIKES_PATH
from src.corregedoria_agent import run_stage3_corregedoria

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Estágio 3: Executa a Corregedoria (Camada Determinística + Camada Semântica LLM + Menu HITL)."
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Executa a auditoria e exporta audited_plan.json sem abrir o menu interativo.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    console.print("[bold cyan]▶ Estágio 3: Agente de Corregedoria / Auditoria QA[/bold cyan]")

    plan, report, decision = run_stage3_corregedoria(
        raw_likes_path=RAW_LIKES_PATH,
        initial_plan_path=INITIAL_PLAN_PATH,
        audited_plan_path=AUDITED_PLAN_PATH,
        interactive=not args.non_interactive,
    )
    console.print(
        f"[bold green]✔ Auditoria finalizada ({decision})![/bold green] "
        f"Plano salvo em [bold]{AUDITED_PLAN_PATH}[/bold] ({len(plan.playlists)} playlists, "
        f"cobertura de {report.coverage_percent:.1f}%)."
    )


if __name__ == "__main__":
    main()
