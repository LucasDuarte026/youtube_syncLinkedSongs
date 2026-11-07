"""Global configuration, file paths, constants, and YTMusic authentication loader."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

# Ensure Maestro root is importable for centralized core.llm access
PROJECT_ROOT = Path(__file__).resolve().parent
MAESTRO_ROOT = PROJECT_ROOT.parent.parent
if str(MAESTRO_ROOT) not in sys.path:
    sys.path.insert(0, str(MAESTRO_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Checkpoint file paths
RAW_LIKES_PATH = DATA_DIR / "raw_likes.json"
SEED_PLAYLISTS_PATH = DATA_DIR / "seed_playlists_reference.json"
TAXONOMY_CATALOG_PATH = DATA_DIR / "taxonomy_catalog.json"
INITIAL_PLAN_PATH = DATA_DIR / "initial_plan.json"
AUDITED_PLAN_PATH = DATA_DIR / "audited_plan.json"
SYNC_STATE_PATH = DATA_DIR / "sync_state.json"

# Business Rule Constants
BATCH_SIZE: int = int(os.environ.get("YTMUSIC_BATCH_SIZE", "45"))
LONG_MEDIA_THRESHOLD_SECONDS: int = int(os.environ.get("YTMUSIC_LONG_MEDIA_SECONDS", "720"))  # 12 minutes
SYNC_CHUNK_SIZE: int = 50
SYNC_SLEEP_SECONDS: float = 1.5
SEED_SAMPLE_TRACKS_LIMIT: int = 10

# Canonical Special Folders (100% Portuguese)
FOLDER_DOUBT = "Dúvida - Sem Categoria Definida"
FOLDER_LONG_MUSICAL = "Especial - DJ Sets, Lives & Álbuns Completos"
FOLDER_NON_MUSIC_PODCASTS = "Não-Música - Podcasts, Entrevistas & Cortes"
FOLDER_NON_MUSIC_RANDOM = "Não-Música - Vídeos Aleatórios & Tutoriais"

# Signature embedded in descriptions of playlists created by this pipeline
AUTO_PLAYLIST_TAG = "[Maestro Auto-Taxonomy]"

AUTH_SETUP_INSTRUCTIONS = """
================================================================================
[ERRO DE AUTENTICAÇÃO] Arquivo de credenciais do YouTube Music não encontrado!
================================================================================
Para autenticar sua conta no ytmusicapi, execute UM dos comandos abaixo no
diretório do projeto (/home/luska/Documents/projects/Maestro/projects/youtubeMusic_editor):

Opção 1 — Autenticação via Browser Headers (Recomendado):
  1. Abra https://music.youtube.com logado na sua conta e abra o DevTools (F12 -> Network).
  2. Filtre por 'browse', clique com botão direito em uma requisição POST -> Copy -> Copy request headers.
  3. Rode no terminal:
     /home/luska/Documents/projects/Maestro/.venv/bin/ytmusicapi browser --file browser.json
  4. Cole os headers copiados e pressione Ctrl+D.

Opção 2 — Autenticação via OAuth:
  1. Rode no terminal:
     /home/luska/Documents/projects/Maestro/.venv/bin/ytmusicapi oauth --file oauth.json
  2. Siga o link exibido no terminal para autorizar o dispositivo.
================================================================================
""".strip()


def resolve_auth_filepath(
    auth_path: Optional[Path | str] = None,
    search_dir: Optional[Path] = None,
) -> Path:
    """Locate browser.json or oauth.json, or raise FileNotFoundError with setup instructions."""
    base_dir = Path(search_dir) if search_dir else PROJECT_ROOT

    if auth_path:
        candidate = Path(auth_path)
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(
            f"Arquivo de autenticação '{candidate}' não encontrado.\n\n{AUTH_SETUP_INSTRUCTIONS}"
        )

    env_auth = os.environ.get("YTMUSIC_AUTH_FILE")
    if env_auth:
        env_candidate = Path(env_auth)
        if not env_candidate.is_absolute():
            env_candidate = base_dir / env_candidate
        if env_candidate.is_file():
            return env_candidate

    for filename in ("browser.json", "oauth.json", "headers_auth.json"):
        candidate = base_dir / filename
        if candidate.is_file():
            return candidate

    # Check for .secrets/browse.token to bootstrap browser.json automatically
    token_candidate = base_dir / ".secrets" / "browse.token"
    if token_candidate.is_file():
        try:
            from import_auth import load_from_token_file
            target_browser_json = base_dir / "browser.json"
            if load_from_token_file(token_candidate, target_browser_json) and target_browser_json.is_file():
                return target_browser_json
        except Exception:
            pass

    raise FileNotFoundError(AUTH_SETUP_INSTRUCTIONS)


def resolve_ytmusic_client(
    auth_path: Optional[Path | str] = None,
    search_dir: Optional[Path] = None,
) -> Any:
    """Instantiate and return an authenticated YTMusic client."""
    from ytmusicapi import YTMusic

    resolved_file = resolve_auth_filepath(auth_path=auth_path, search_dir=search_dir)
    return YTMusic(str(resolved_file))
