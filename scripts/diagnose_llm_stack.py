#!/usr/bin/env python3
"""Diagnostic script & health probe for Maestro LLM Stack & Antigravity Models."""

import json
import logging
import sys
import time
from pathlib import Path
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAESTRO_ROOT = PROJECT_ROOT.parent.parent
if str(MAESTRO_ROOT) not in sys.path:
    sys.path.insert(0, str(MAESTRO_ROOT))

from core.llm import (
    AgyClient,
    DEFAULT_BASE_URL,
    DEFAULT_CHAT_COMPLETIONS_URL,
    get_ordered_model_credentials,
)
from rich.console import Console
from rich.table import Table

console = Console()


def audit_nvidia_outdoor_models():
    results = []
    for model_name, api_key, configured_timeout in get_ordered_model_credentials():
        if not api_key:
            results.append({
                "model": model_name,
                "status": "SEM CHAVE",
                "latency": "-",
                "details": "API Key ausente no ambiente",
            })
            continue

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": 'Responda apenas: {"status": "ok"}'}],
            "max_tokens": 50,
            "temperature": 0.1,
        }
        t0 = time.time()
        try:
            res = requests.post(
                DEFAULT_CHAT_COMPLETIONS_URL,
                json=payload,
                headers=headers,
                timeout=15,
            )
            elapsed = round(time.time() - t0, 2)
            if res.status_code == 200:
                msg = res.json().get("choices", [{}])[0].get("message", {})
                content = msg.get("content")
                reasoning = msg.get("reasoning_content")
                if content:
                    status = "ONLINE"
                    details = f"Resposta direta em {elapsed}s"
                elif reasoning:
                    status = "ONLINE (REASONING)"
                    details = f"Thinking model ({len(reasoning)} chars no reasoning_content, timeout curto pode abortar)"
                else:
                    status = "ONLINE (VAZIO)"
                    details = "Resposta 200 mas sem content"
            else:
                status = f"HTTP {res.status_code}"
                details = res.text[:120]
        except requests.exceptions.Timeout:
            elapsed = round(time.time() - t0, 2)
            status = "TIMEOUT"
            details = f"Servidor da NVIDIA não respondeu em 15s"
        except Exception as e:
            elapsed = round(time.time() - t0, 2)
            status = "ERRO"
            details = str(e)[:120]

        results.append({
            "model": model_name,
            "status": status,
            "latency": f"{elapsed}s",
            "details": details,
        })
    return results


def audit_agy_direct_models():
    client = AgyClient()
    models_to_test = ["gemini-3.8-flash-medium", "gemini-flash", "gpt-oss-120b-medium"]
    results = []
    for model in models_to_test:
        t0 = time.time()
        try:
            res = client.query('Responda apenas: {"status": "ok"}', model=model, timeout=25)
            elapsed = round(time.time() - t0, 2)
            if res.exit_code == 0 and res.content:
                status = "ONLINE"
                details = f"Resposta válida em {elapsed}s"
            else:
                status = f"ERRO (code {res.exit_code})"
                details = res.raw_stderr[:120]
        except Exception as e:
            elapsed = round(time.time() - t0, 2)
            status = "ERRO"
            details = str(e)[:120]

        results.append({
            "model": model,
            "status": status,
            "latency": f"{elapsed}s",
            "details": details,
        })
    return results


def main():
    console.print("\n[bold cyan]🔍 DIAGNÓSTICO DA STACK DE IA (NVIDIA OUTDOOR + GEMINI DIRETO)[/bold cyan]\n")

    # 1. NVIDIA Outdoor
    outdoor_results = audit_nvidia_outdoor_models()
    table_outdoor = Table(title="1. Stack Externa NVIDIA NIM (integrate.api.nvidia.com)")
    table_outdoor.add_column("Modelo", style="bold")
    table_outdoor.add_column("Status", justify="center")
    table_outdoor.add_column("Latência", justify="right")
    table_outdoor.add_column("Diagnóstico / Motivo", style="dim")

    for r in outdoor_results:
        color = "green" if "ONLINE" in r["status"] else "red"
        table_outdoor.add_row(r["model"], f"[{color}]{r['status']}[/{color}]", r["latency"], r["details"])
    console.print(table_outdoor)

    # 2. Local / Gemini Direct
    console.print()
    agy_results = audit_agy_direct_models()
    table_agy = Table(title="2. Execução Direta via Antigravity (Recomendado)")
    table_agy.add_column("Modelo", style="bold")
    table_agy.add_column("Status", justify="center")
    table_agy.add_column("Latência", justify="right")
    table_agy.add_column("Diagnóstico", style="dim")

    for r in agy_results:
        color = "green" if r["status"] == "ONLINE" else "red"
        table_agy.add_row(r["model"], f"[{color}]{r['status']}[/{color}]", r["latency"], r["details"])
    console.print(table_agy)


if __name__ == "__main__":
    main()
