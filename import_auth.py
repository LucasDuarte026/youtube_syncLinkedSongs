#!/usr/bin/env python3
"""Helper script to generate browser.json from either .secrets/browse.token, stdin, or raw cURL."""

import json
import logging
import platform
import re
import shlex
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent
SECRETS_DIR = PROJECT_ROOT / ".secrets"
TOKEN_FILE = SECRETS_DIR / "browse.token"
OUTPUT_FILE = PROJECT_ROOT / "browser.json"

logger = logging.getLogger("ytmusic_auth")


def extract_header_lines_from_curl_or_text(raw_text: str) -> str:
    """Extract header lines from a cURL command or raw header text, handling -b/--cookie."""
    if "curl " in raw_text or "-H " in raw_text or "--header " in raw_text or "-b " in raw_text:
        try:
            tokens = shlex.split(raw_text)
            lines = []
            for i, token in enumerate(tokens):
                if token in ("-H", "--header") and i + 1 < len(tokens):
                    lines.append(tokens[i + 1])
                elif token in ("-b", "--cookie") and i + 1 < len(tokens):
                    lines.append(f"cookie: {tokens[i + 1]}")
            if lines:
                return "\n".join(lines)
        except Exception as err:
            logger.debug("shlex fallback: %s", err)
            # Fallback regex if unclosed quotes or strange escapes
            lines = []
            for m in re.finditer(r"(?:-H|--header)\s+['\"]([^'\"]+)['\"]", raw_text):
                lines.append(m.group(1))
            cookie_match = re.search(r"(?:-b|--cookie)\s+['\"]([^'\"]+)['\"]", raw_text)
            if cookie_match:
                lines.append(f"cookie: {cookie_match.group(1)}")
            if lines:
                return "\n".join(lines)

    return raw_text


def process_auth_source(source_text: str, output_path: Path = OUTPUT_FILE) -> bool:
    """Process raw cURL or header text and write compliant browser.json without leaking secrets."""
    from ytmusicapi.auth.browser import setup_browser

    header_lines = extract_header_lines_from_curl_or_text(source_text)
    try:
        setup_browser(filepath=str(output_path), headers_raw=header_lines)
        return True
    except Exception as err:
        logger.error("Erro ao converter headers para %s: %s", output_path.name, err)
        return False


def load_from_token_file(token_path: Path = TOKEN_FILE, output_path: Path = OUTPUT_FILE) -> bool:
    """Read credentials securely from file on disk and generate browser.json."""
    if not token_path.is_file():
        return False
    raw_content = token_path.read_text(encoding="utf-8").strip()
    if not raw_content:
        return False
    return process_auth_source(raw_content, output_path=output_path)


def main():
    # 1. Prefer reading from .secrets/browse.token if present
    if TOKEN_FILE.is_file():
        success = load_from_token_file(TOKEN_FILE, OUTPUT_FILE)
        if success:
            print(f"[OK] Autenticação gerada com sucesso em {OUTPUT_FILE.name} a partir de .secrets/browse.token!")
            return
        else:
            print("[AVISO] Falha ao processar .secrets/browse.token. Tentando entrada manual...")

    # 2. Interactive fallback via stdin if not running headless
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw and process_auth_source(raw, OUTPUT_FILE):
            print(f"[OK] Arquivo '{OUTPUT_FILE.name}' criado com sucesso a partir de stdin!")
            return
        print("[ERRO] Stdin vazio ou inválido.")
        sys.exit(1)

    eof = "Ctrl+D" if platform.system() != "Windows" else "Ctrl+Z e Enter"
    print(f"Cole os headers ou o comando 'Copy as cURL' e pressione {eof} para finalizar:\n")

    raw = sys.stdin.read().strip()
    if not raw:
        print("Erro: Nenhum conteúdo foi colado.")
        sys.exit(1)

    if process_auth_source(raw, OUTPUT_FILE):
        print(f"\n[OK] Arquivo '{OUTPUT_FILE.name}' criado com sucesso!")
    else:
        print("\n[ERRO] Não foi possível validar os headers fornecidos.")
        sys.exit(1)


if __name__ == "__main__":
    main()
