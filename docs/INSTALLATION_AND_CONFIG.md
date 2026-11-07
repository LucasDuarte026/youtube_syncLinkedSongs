# Installation & Configuration Guide

## 1. Prerequisites

- **Operating System:** Linux (Ubuntu/Debian tested) or macOS.
- **Python Version:** Python 3.10 or higher.
- **Virtual Environment:** An isolated Python virtual environment (`.venv`) is strictly recommended.

---

## 2. Dependency Installation

From the project root:

```bash
# Activate existing virtual environment or create a new one
python3 -m venv .venv
source .venv/bin/activate

# Install required dependencies
pip install -r requirements.txt
```

### Dependencies Overview
- `ytmusicapi>=1.8.0`: Unofficial YouTube Music API client for browsing, library extraction, and playlist operations.
- `pydantic>=2.0.0`: High-performance data validation and structured schema contracts.
- `rich>=13.0.0`: Terminal formatting, tables, panels, and progress displays.
- `requests>=2.32.0`: Resilient HTTP networking.
- `pytest>=7.4.0`: Automated test suite execution.

---

## 3. YouTube Music Authentication Setup

The pipeline requires authenticated access to read your library and liked songs. Two authentication methods are supported:

### Method A: Browser Headers via cURL (Fastest & Recommended)

Modern browsers support copying network requests directly as cURL commands:

1. Open [music.youtube.com](https://music.youtube.com) in Chrome/Chromium and log into your account.
2. Open DevTools (**F12**), navigate to the **Network** tab, and filter by `browse`.
3. Locate any `POST` request to `youtubei/v1/browse` (e.g., `browse?prettyPrint=false`).
4. Right-click the request and select **Copy ➔ Copy as cURL**.
5. Run the authentication bootstrap script:
   ```bash
   python import_auth.py
   ```
6. Paste the copied cURL command into the terminal and press **Ctrl+D**.
7. The script extracts the necessary cookies (`-b`) and authorization headers, writing a validated `browser.json` file.

### Method B: Native ytmusicapi Setup

Alternatively, you can invoke `ytmusicapi` directly:

```bash
# Using browser request headers copied from DevTools
ytmusicapi browser --file browser.json

# Or using OAuth flow
ytmusicapi oauth --file oauth.json
```

---

## 4. Configuration Options (`config.py`)

All core constants and operational thresholds can be configured via environment variables or modified directly in `config.py`:

| Parameter | Environment Variable | Default Value | Description |
| :--- | :--- | :---: | :--- |
| **Batch Size** | `YTMUSIC_BATCH_SIZE` | `45` | Number of tracks evaluated per LLM classification batch in Stage 2. |
| **Long Media Threshold** | `YTMUSIC_LONG_MEDIA_SECONDS` | `720` (12 min) | Duration cutoff beyond which musical videos are routed to `Especial - DJ Sets...`. |
| **Sync Chunk Size** | `SYNC_CHUNK_SIZE` | `50` | Maximum track IDs added per YouTube Music API call in Stage 4. |
| **Sync Sleep Delay** | `SYNC_SLEEP_SECONDS` | `1.5` | Sleep interval (in seconds) between chunk additions to avoid HTTP 429 rate limits. |
| **Seed Sample Limit** | `SEED_SAMPLE_TRACKS_LIMIT` | `10` | Maximum sample tracks extracted per pre-existing user playlist for style context. |
| **LLM Model** | `YTMUSIC_LLM_MODEL` | `gemini-3.8-flash-medium` | Target model identifier passed to the local Antigravity CLI client. |

---

## 5. Security & Isolation Note

- Authentication files (`browser.json`, `oauth.json`, `*.token`) are ignored by `.gitignore` and should **never** be committed to version control.
- See `docs/SECURITY_AUDIT.md` for credential handling protocols and verification steps.
