# YouTube Music Multi-Agent Taxonomist & Sync Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a 4-stage checkpointed Python CLI pipeline (`ytmusicapi` + Maestro `core.llm` NVIDIA cascade) that extracts 1,000+ YouTube Music liked songs and read-only seed playlists, classifies them into granular Portuguese subgenres, audits 100% coverage and semantic quality via Corregedoria + HITL, and syncs approved playlists idempotently to YouTube Music for ArchiveTune.

**Architecture:** A modular 4-stage pipeline with JSON state persistence in `./data/` (`raw_likes.json`, `seed_playlists_reference.json`, `taxonomy_catalog.json`, `initial_plan.json`, `audited_plan.json`, `sync_state.json`). LLM calls delegate directly to Maestro's centralized `core.llm` NVIDIA cascade through a Pydantic structured-output adapter (`src/llm_adapter.py`) with exponential backoff.

**Tech Stack:** Python 3.10+, `ytmusicapi`, `pydantic`, `rich`, `requests`, `pytest`, Maestro `core.llm` (NVIDIA NIM 4-tier cascade + `AgyClient` indoor fallback).

**Spec:** `/home/luska/Documents/projects/Maestro/projects/youtubeMusic_editor/docs/superpowers/specs/2026-11-07-ytmusic-taxonomist-design.md`

## Global Constraints

- Execute all Python and test commands strictly via `/home/luska/Documents/projects/Maestro/.venv/bin/python` and `/home/luska/Documents/projects/Maestro/.venv/bin/pytest`.
- All playlist names must be 100% in Portuguese following `[Macro] - [Subgênero/Vibe]` (prohibited bare macro-genres like `Funk`, `Rock`, `Eletrônica`).
- Never modify pre-existing user playlists (`protected_playlist_ids` captured in Stage 1); only create/update playlists tracked in `data/sync_state.json` and tagged with `[Maestro Auto-Taxonomy]`.
- Videos with `duration_seconds >= 720` (12+ minutes) that are musical (DJ sets, live shows, full albums) must be routed to `Especial - DJ Sets, Lives & Álbuns Completos`.
- Non-music items (podcasts, interviews, cuts, tutorials, gameplays) must be isolated in `Não-Música - Podcasts, Entrevistas & Cortes` or `Não-Música - Vídeos Aleatórios & Tutoriais`.
- Low-confidence tracks must be placed in `Dúvida - Sem Categoria Definida` (100% coverage invariant: `set(raw_likes_ids) == set(allocated_ids)`).
- Stage 4 chunks `yt.add_playlist_items(playlistId, videoIds, duplicates=False)` to at most 50 `videoIds` per call with `time.sleep(1.5)` between chunks.

## Review Focus

- `get_liked_songs` returns tracks with missing `videoId`, null `album`, or duration formatted only as `"15:30"` when `duration_seconds` is missing: Ingestor must parse `"MM:SS"` / `"HH:MM:SS"` fallback into `duration_seconds` and skip entries without `videoId`.
- LLM returns markdown-fenced JSON (` ```json ... ``` `) or `<think>...</think>` blocks from reasoning models (`deepseek-r1`/`muse-glimmer`): `llm_adapter.py` must strip reasoning tags and fences before Pydantic validation.
- LLM batch drops 1 or 2 `videoIds` out of 45 in a batch response: Stage 2 and Stage 3 Layer A must deterministically detect missing `videoIds` and either retry them or assign them so 100% coverage is preserved.
- Pre-existing manual playlist on YouTube Music has the exact same title (e.g., `Funk - Mandelão`) as a generated category: Stage 4 must check `protected_playlist_ids` and create a new pipeline-owned playlist rather than modifying the user's seed playlist.
- `add_playlist_items(..., duplicates=False)` fails on a 50-item chunk because 1 video was deleted/region-locked on YouTube: Stage 4 must catch the chunk exception and fall back to individual item insertion, logging failed `videoId`s without crashing.

---

### Task 1: Project Configuration & Pydantic Data Models (`config.py`, `src/models.py`)

**Files:**
- Create: `requirements.txt`
- Create: `config.py`
- Create: `src/__init__.py`
- Create: `src/models.py`
- Test: `tests/test_models_and_config.py`

**Interfaces:**
- Produces:
  - `config.DATA_DIR`, `config.RAW_LIKES_PATH`, `config.SEED_PLAYLISTS_PATH`, `config.TAXONOMY_CATALOG_PATH`, `config.INITIAL_PLAN_PATH`, `config.AUDITED_PLAN_PATH`, `config.SYNC_STATE_PATH`
  - `config.resolve_ytmusic_client(auth_path: Optional[Path] = None) -> YTMusic`
  - `src.models.TrackItem`, `src.models.SeedPlaylistRef`, `src.models.TaxonomyCategory`, `src.models.TaxonomyCatalogResponse`, `src.models.TrackClassification`, `src.models.BatchClassificationResponse`, `src.models.PlaylistPlan`, `src.models.CorregedoriaMove`, `src.models.CorregedoriaReviewResponse`, `src.models.SyncState`

- [ ] **Step 1: Write the failing test**
  Test `TrackItem` duration parsing, macro-genre validation helper `normalize_playlist_name(name: str) -> str`, `PlaylistPlan` coverage calculation, and `resolve_ytmusic_client` raising `FileNotFoundError` with clear CLI instructions when neither `browser.json` nor `oauth.json` exists.

- [ ] **Step 2: Run test to verify it fails**
  Run: `/home/luska/Documents/projects/Maestro/.venv/bin/pytest tests/test_models_and_config.py -v`

- [ ] **Step 3: Implement `config.py` and `src/models.py`**

- [ ] **Step 4: Run test to verify it passes**
  Run: `/home/luska/Documents/projects/Maestro/.venv/bin/pytest tests/test_models_and_config.py -v`

---

### Task 2: Decoupled Maestro NVIDIA LLM Structured Adapter (`src/llm_adapter.py`)

**Files:**
- Create: `src/llm_adapter.py`
- Test: `tests/test_llm_adapter.py`

**Interfaces:**
- Consumes: `core.llm.invoke_chat`, `core.llm.invoke_outdoor_cascade`
- Produces:
  - `StructuredLLMClient.generate_structured(prompt: str, response_model: Type[T], system_prompt: Optional[str] = None, temperature: float = 0.2, max_tokens: int = 4096) -> T`
  - `extract_json_payload(raw_text: str) -> Dict[str, Any]`

- [ ] **Step 1: Write the failing test**
  Test `extract_json_payload` with `<think>...</think>` blocks and markdown fences, test `generate_structured` validating a Pydantic model via `core.llm` cascade, and test exponential backoff retry when the first attempt returns malformed JSON.

- [ ] **Step 2: Run test to verify it fails**
  Run: `/home/luska/Documents/projects/Maestro/.venv/bin/pytest tests/test_llm_adapter.py -v`

- [ ] **Step 3: Implement `src/llm_adapter.py`**

- [ ] **Step 4: Run test to verify it passes**
  Run: `/home/luska/Documents/projects/Maestro/.venv/bin/pytest tests/test_llm_adapter.py -v`

---

### Task 3: Stage 1 — Ingestor & Read-Only Seed Playlists Extractor (`src/ingestor.py`, `01_ingestor.py`)

**Files:**
- Create: `src/ingestor.py`
- Create: `01_ingestor.py`
- Test: `tests/test_stage1_ingestor.py`

**Interfaces:**
- Consumes: `config.resolve_ytmusic_client`, `src.models.TrackItem`, `src.models.SeedPlaylistRef`
- Produces:
  - `sanitize_track(raw_track: Dict[str, Any]) -> Optional[TrackItem]`
  - `extract_liked_songs(yt: Any, output_path: Path) -> List[TrackItem]`
  - `extract_seed_playlists_reference(yt: Any, output_path: Path, sample_limit: int = 10) -> Dict[str, Any]`

- [ ] **Step 1: Write the failing test**
  Test `sanitize_track` normalizing `videoId`, concatenating `artists`, extracting `album`, computing `duration_seconds` from fallback `"MM:SS"`, preserving `videoType`, and test `extract_seed_playlists_reference` saving `protected_playlist_ids` and up to 10 sample tracks per existing playlist while ignoring pipeline-created playlists (`[Maestro Auto-Taxonomy]`).

- [ ] **Step 2: Run test to verify it fails**
  Run: `/home/luska/Documents/projects/Maestro/.venv/bin/pytest tests/test_stage1_ingestor.py -v`

- [ ] **Step 3: Implement `src/ingestor.py` and `01_ingestor.py`**

- [ ] **Step 4: Run test to verify it passes**
  Run: `/home/luska/Documents/projects/Maestro/.venv/bin/pytest tests/test_stage1_ingestor.py -v`

---

### Task 4: Stage 2 — Two-Pass Taxonomist & Batch Classifier (`src/classifier_agent.py`, `02_classifier_agent.py`)

**Files:**
- Create: `src/classifier_agent.py`
- Create: `02_classifier_agent.py`
- Test: `tests/test_stage2_classifier.py`

**Interfaces:**
- Consumes: `src.llm_adapter.StructuredLLMClient`, `src.models.*`
- Produces:
  - `discover_taxonomy_catalog(tracks: List[TrackItem], seed_refs: List[SeedPlaylistRef], llm: StructuredLLMClient) -> List[TaxonomyCategory]`
  - `classify_tracks_pipeline(raw_likes_path: Path, seed_ref_path: Path, initial_plan_path: Path, catalog_path: Path, llm: StructuredLLMClient, batch_size: int = 45, full_reclassify: bool = False) -> PlaylistPlan`

- [ ] **Step 1: Write the failing test**
  Test Pass 2A taxonomy discovery, Pass 2B batch classification (40-50 tracks per batch), deterministic routing of long musical tracks (`>= 720s`) to `Especial - DJ Sets, Lives & Álbuns Completos`, routing of missing batch `videoIds` to `Dúvida - Sem Categoria Definida` if omitted by LLM, and incremental Delta Sync skipping already classified `videoIds` unless `full_reclassify=True`.

- [ ] **Step 2: Run test to verify it fails**
  Run: `/home/luska/Documents/projects/Maestro/.venv/bin/pytest tests/test_stage2_classifier.py -v`

- [ ] **Step 3: Implement `src/classifier_agent.py` and `02_classifier_agent.py`**

- [ ] **Step 4: Run test to verify it passes**
  Run: `/home/luska/Documents/projects/Maestro/.venv/bin/pytest tests/test_stage2_classifier.py -v`

---

### Task 5: Stage 3 — Corregedoria QA Auditor & Interactive HITL CLI (`src/corregedoria_agent.py`, `03_corregedoria_agent.py`)

**Files:**
- Create: `src/corregedoria_agent.py`
- Create: `03_corregedoria_agent.py`
- Test: `tests/test_stage3_corregedoria.py`

**Interfaces:**
- Consumes: `src.llm_adapter.StructuredLLMClient`, `src.models.*`
- Produces:
  - `run_deterministic_audit(plan: PlaylistPlan, raw_tracks: List[TrackItem], llm: Optional[StructuredLLMClient] = None) -> Tuple[PlaylistPlan, AuditReport]`
  - `run_semantic_audit(plan: PlaylistPlan, raw_tracks: List[TrackItem], llm: StructuredLLMClient) -> Tuple[PlaylistPlan, List[str]]`
  - `apply_user_prompt_adjustment(plan: PlaylistPlan, raw_tracks: List[TrackItem], instruction: str, llm: StructuredLLMClient) -> PlaylistPlan`

- [ ] **Step 1: Write the failing test**
  Test Layer A: 100% coverage check & orphan recovery, accent/case/typo playlist deduplication (`Funk - Mandelao` + `funk - mandelão` -> `Funk - Mandelão`), bare macro-genre normalization (`Funk` -> `Funk - Geral & Variados`), long musical track enforcement (`>= 720s` -> `Especial - DJ Sets, Lives & Álbuns Completos`). Test Layer B: semantic intruder relocation (non-funk out of `Funk - *`, non-music out of music folders, `Dúvida` rescue). Test interactive prompt adjustment (`apply_user_prompt_adjustment`).

- [ ] **Step 2: Run test to verify it fails**
  Run: `/home/luska/Documents/projects/Maestro/.venv/bin/pytest tests/test_stage3_corregedoria.py -v`

- [ ] **Step 3: Implement `src/corregedoria_agent.py` and `03_corregedoria_agent.py`**

- [ ] **Step 4: Run test to verify it passes**
  Run: `/home/luska/Documents/projects/Maestro/.venv/bin/pytest tests/test_stage3_corregedoria.py -v`

---

### Task 6: Stage 4 — Idempotent YouTube Music Sync Executor (`src/sync_executor.py`, `04_sync_executor.py`)

**Files:**
- Create: `src/sync_executor.py`
- Create: `04_sync_executor.py`
- Test: `tests/test_stage4_sync.py`

**Interfaces:**
- Consumes: `config.resolve_ytmusic_client`, `src.models.PlaylistPlan`, `src.models.SyncState`
- Produces:
  - `execute_youtube_sync(yt: Any, audited_plan_path: Path, sync_state_path: Path, seed_ref_path: Optional[Path] = None, chunk_size: int = 50, sleep_seconds: float = 1.5) -> SyncState`

- [ ] **Step 1: Write the failing test**
  Test that pre-existing playlists in `protected_playlist_ids` are NEVER modified even when their title matches a planned playlist; test idempotent reuse of pipeline-owned playlists via `sync_state.json`; test 50-item chunking with `sleep_seconds`; and test per-item fallback when a batch of 50 fails due to one invalid/deleted `videoId`.

- [ ] **Step 2: Run test to verify it fails**
  Run: `/home/luska/Documents/projects/Maestro/.venv/bin/pytest tests/test_stage4_sync.py -v`

- [ ] **Step 3: Implement `src/sync_executor.py` and `04_sync_executor.py`**

- [ ] **Step 4: Run test to verify it passes**
  Run: `/home/luska/Documents/projects/Maestro/.venv/bin/pytest tests/test_stage4_sync.py -v`

---

### Task 7: Main Pipeline Orchestrator & Documentation (`main.py`, `README.md`)

**Files:**
- Create: `main.py`
- Create: `README.md`
- Test: `tests/test_main_pipeline.py`

**Interfaces:**
- Consumes: Stages 1–4 entrypoints
- Produces: `main.py` CLI orchestrator supporting `--stage {all,1,2,3,4}`, `--full-reclassify`, `--execute`, `--dry-run`, `--auth`

- [ ] **Step 1: Write the failing test**
  Test `main.py` dry-run and full pipeline orchestration across stages 1–4 with mocked `YTMusic` and `StructuredLLMClient`.

- [ ] **Step 2: Run test to verify it fails**
  Run: `/home/luska/Documents/projects/Maestro/.venv/bin/pytest tests/test_main_pipeline.py -v`

- [ ] **Step 3: Implement `main.py` and `README.md`**

- [ ] **Step 4: Run full test suite to verify 100% pass rate**
  Run: `/home/luska/Documents/projects/Maestro/.venv/bin/pytest -v`
