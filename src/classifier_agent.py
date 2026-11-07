"""Stage 2: Two-Pass Taxonomist & Batch Classifier Agent (`data/initial_plan.json`)."""

from __future__ import annotations

from collections import Counter
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

from config import (
    BATCH_SIZE,
    FOLDER_DOUBT,
    FOLDER_LONG_MUSICAL,
    FOLDER_NON_MUSIC_PODCASTS,
    FOLDER_NON_MUSIC_RANDOM,
    INITIAL_PLAN_PATH,
    LONG_MEDIA_THRESHOLD_SECONDS,
    RAW_LIKES_PATH,
    SEED_PLAYLISTS_PATH,
    TAXONOMY_CATALOG_PATH,
)
from src.llm_adapter import StructuredLLMClient
from src.models import (
    BatchClassificationResponse,
    PlaylistPlan,
    SeedPlaylistRef,
    SeedPlaylistsBundle,
    TaxonomyCatalogResponse,
    TaxonomyCategory,
    TrackItem,
    canonical_comparison_key,
    normalize_playlist_name,
)

logger = logging.getLogger("ytmusic_taxonomist.classifier")

MANDATORY_SPECIAL_CATEGORIES = [
    TaxonomyCategory(
        name=FOLDER_LONG_MUSICAL,
        description="DJ Sets longos, shows ao vivo completos, full albums ou vídeos musicais com 12+ minutos (>=720s).",
    ),
    TaxonomyCategory(
        name=FOLDER_NON_MUSIC_PODCASTS,
        description="Podcasts, entrevistas, mesas-redondas e cortes falados.",
    ),
    TaxonomyCategory(
        name=FOLDER_NON_MUSIC_RANDOM,
        description="Vídeos aleatórios do YouTube, tutoriais, reviews de tecnologia, gameplays e aulas.",
    ),
    TaxonomyCategory(
        name=FOLDER_DOUBT,
        description="Faixas com metadados obscuros ou baixa confiança onde não foi possível determinar o subgênero.",
    ),
]


def _is_non_music_folder(folder_name: str) -> bool:
    norm = normalize_playlist_name(folder_name).lower()
    return norm.startswith("não-música") or norm.startswith("nao-musica")


def discover_taxonomy_catalog(
    tracks: List[TrackItem],
    seed_refs: List[SeedPlaylistRef],
    llm: StructuredLLMClient,
) -> List[TaxonomyCategory]:
    """Pass 2A: Analyze global artist/title profile + Read-Only seed playlists to propose the initial taxonomy."""
    artist_counts = Counter(t.artists for t in tracks if t.artists and t.artists != "Desconhecido")
    top_artists = [f"{artist} ({count})" for artist, count in artist_counts.most_common(120)]
    sample_titles = [f"{t.artists} - {t.title}" for t in tracks[:80]]

    seed_summary_lines: List[str] = []
    for s in seed_refs:
        examples = ", ".join(f"{st.artists} - {st.title}" for st in s.sample_tracks[:5])
        seed_summary_lines.append(f"- '{s.title}' ({s.track_count} faixas): ex: [{examples}]")

    seed_section = (
        "\n".join(seed_summary_lines)
        if seed_summary_lines
        else "Nenhuma playlist semente pré-existente informada."
    )

    prompt = f"""Analise o perfil global da biblioteca do usuário ({len(tracks)} músicas curtidas) para projetar a árvore inicial de playlists granulares em Português para o ArchiveTune.

### 1. Playlists Atuais do Usuário (APENAS REFERÊNCIA DE ESTILO — NÃO REUTILIZE NOMES SEM PADRONIZAR):
{seed_section}

### 2. Artistas Mais Frequentes nas Músicas Curtidas:
{', '.join(top_artists)}

### 3. Amostra de Faixas Curtidas:
{chr(10).join(sample_titles)}

### Regras Obrigatórias para o Catálogo de Subgêneros:
1. **Nomenclatura 100% em Português no formato `[Macro] - [Subgênero/Vibe]`**:
   Exemplos de referência:
   - `Funk - Mandelão`
   - `Funk - Festa & 150 BPM`
   - `Funk - Consciente & Antigo`
   - `Funk - Brega Funk & Melody`
   - `Rock - Clássico & Hard Rock`
   - `Rock - Indie & Alternativo`
   - `Rock - Nacional & BR`
   - `MPB - Acústico & Voz e Violão`
   - `MPB - Clássica & Bossa`
   - `Hip-Hop - Boom Bap & Rap Nacional`
   - `Hip-Hop - Trap & Grime`
   - `Reggae - Roots & Dub`
   - `Reggae - Nacional & Surf Music`
   - `Eletrônica - Festival EDM & Progressive House`
   - `Eletrônica - French House & Synth`
   - `Eletrônica - Gaming, NCS & Monstercat`
2. **Proibido Macro-Gêneros Puros**: Nunca proponha apenas `Funk`, `Rock` ou `Eletrônica`.
3. Crie entre 12 e 35 categorias granulares que reflitam os estilos reais presentes nos artistas acima.
"""

    response = llm.generate_structured(
        prompt=prompt,
        response_model=TaxonomyCatalogResponse,
        temperature=0.2,
    )

    seen_keys: Set[str] = set()
    catalog: List[TaxonomyCategory] = []

    for cat in list(response.categories) + MANDATORY_SPECIAL_CATEGORIES:
        norm_name = normalize_playlist_name(cat.name)
        key = canonical_comparison_key(norm_name)
        if key not in seen_keys:
            seen_keys.add(key)
            catalog.append(TaxonomyCategory(name=norm_name, description=cat.description))

    return catalog


def _build_batch_prompt(
    batch: List[TrackItem],
    catalog: List[TaxonomyCategory],
) -> str:
    catalog_lines = [f"- `{c.name}`: {c.description}" for c in catalog]
    tracks_payload = [
        {
            "videoId": t.videoId,
            "title": t.title,
            "artists": t.artists,
            "album": t.album or "",
            "duration_seconds": t.duration_seconds,
            "videoType": t.videoType,
        }
        for t in batch
    ]

    return f"""Classifique CADA UMA das {len(batch)} faixas abaixo em uma ou mais playlists granulares em Português.

### Catálogo de Subgêneros Ativo (Reutilize estas pastas sempre que apropriado, ou crie uma nova `[Macro] - [Subgênero/Vibe]` se surgir um nicho genuinamente novo):
{chr(10).join(catalog_lines)}

### Regras de Negócio Obrigatórias:
1. **Cobertura de 100% do Lote**: Todos os {len(batch)} `videoId` enviados DEVEM aparecer na lista `classifications`.
2. **Nomenclatura 100% em Português (`[Macro] - [Subgênero/Vibe]`)**: Proibido usar nomes genéricos como `Funk`, `Rock`, `Pop` ou `Eletrônica` sem subgênero.
3. **Triagem Rigorosa de Funk**: Nenhuma música de Funk pode vazar para Pop/Eletrônica/Hip-Hop, e nenhuma música não-funk pode cair em `Funk - *`.
4. **Multi-Label Permitido**: Uma música pode ir para 2 playlists se pertencer genuinamente a dois subgêneros (ex: `MPB - ...` e `Reggae - ...`).
5. **Isolamento de Não-Músicas**: Podcasts, entrevistas e cortes vão para `{FOLDER_NON_MUSIC_PODCASTS}`. Tutoriais, gameplays, reviews e vídeos aleatórios (`MUSIC_VIDEO_TYPE_UGC` não-musical) vão para `{FOLDER_NON_MUSIC_RANDOM}`.
6. **Vídeos Musicais Longos (>= 720s / 12+ min)**: DJ sets longos, lives completas ou full albums vão para `{FOLDER_LONG_MUSICAL}`.
7. **Dúvida**: Se os metadados forem obscuros e você tiver baixa confiança, classifique em `{FOLDER_DOUBT}`.

### Lote de Faixas (JSON):
{json.dumps(tracks_payload, ensure_ascii=False, indent=2)}
"""


def apply_track_classification_rules(
    track: TrackItem,
    raw_playlists: List[str],
    confidence: float,
) -> List[str]:
    """Deterministically enforce business rules on a single track's proposed playlists."""
    if not raw_playlists or confidence < 0.45:
        return [FOLDER_DOUBT]

    normalized = [normalize_playlist_name(p) for p in raw_playlists if p and p.strip()]
    if not normalized:
        return [FOLDER_DOUBT]

    # If any chosen folder is Non-Music, keep only Non-Music folders
    non_music = [p for p in normalized if _is_non_music_folder(p)]
    if non_music:
        return non_music

    # Rule: Any musical video >= LONG_MEDIA_THRESHOLD_SECONDS (720s / 12m) goes exclusively to FOLDER_LONG_MUSICAL
    if track.duration_seconds >= LONG_MEDIA_THRESHOLD_SECONDS:
        return [FOLDER_LONG_MUSICAL]

    return normalized


def classify_tracks_pipeline(
    raw_likes_path: Path = RAW_LIKES_PATH,
    seed_ref_path: Path = SEED_PLAYLISTS_PATH,
    initial_plan_path: Path = INITIAL_PLAN_PATH,
    catalog_path: Path = TAXONOMY_CATALOG_PATH,
    llm: Optional[StructuredLLMClient] = None,
    batch_size: int = BATCH_SIZE,
    full_reclassify: bool = False,
) -> PlaylistPlan:
    """Execute Stage 2 (Pass 2A Taxonomy Discovery + Pass 2B Batch Allocation with Checkpoint & Delta Sync)."""
    if not raw_likes_path.is_file():
        raise FileNotFoundError(f"Arquivo {raw_likes_path} não encontrado. Rode o Estágio 1 primeiro.")

    client = llm or StructuredLLMClient()
    raw_list = json.loads(raw_likes_path.read_text(encoding="utf-8"))
    tracks: List[TrackItem] = [TrackItem.model_validate(item) for item in raw_list]

    seed_refs: List[SeedPlaylistRef] = []
    if seed_ref_path.is_file():
        bundle = SeedPlaylistsBundle.model_validate_json(seed_ref_path.read_text(encoding="utf-8"))
        seed_refs = bundle.seed_playlists

    # Load or initialize plan (Delta Sync support)
    plan = PlaylistPlan()
    if initial_plan_path.is_file() and not full_reclassify:
        try:
            plan = PlaylistPlan.model_validate_json(initial_plan_path.read_text(encoding="utf-8"))
            logger.info(
                "Checkpoint existente carregado de %s (%d faixas já alocadas).",
                initial_plan_path,
                len(plan.all_allocated_video_ids()),
            )
        except Exception as err:
            logger.warning("Falha ao ler initial_plan.json existente, iniciando novo plano: %s", err)

    allocated_ids = plan.all_allocated_video_ids()
    pending_tracks = [t for t in tracks if t.videoId not in allocated_ids]

    if not pending_tracks:
        logger.info("Todas as %d faixas já estão classificadas no checkpoint (Delta Sync).", len(tracks))
        return plan

    # Pass 2A: Load or discover global taxonomy catalog
    catalog: List[TaxonomyCategory] = []
    if catalog_path.is_file() and not full_reclassify:
        try:
            raw_cat = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog = [TaxonomyCategory.model_validate(c) for c in raw_cat]
        except Exception:
            catalog = []

    if not catalog:
        logger.info("Passada 2A: Descobrindo catálogo global de subgêneros via LLM...")
        catalog = discover_taxonomy_catalog(tracks, seed_refs, client)
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(
            json.dumps([c.model_dump() for c in catalog], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    known_cat_keys = {canonical_comparison_key(c.name): c.name for c in catalog}

    # Pass 2B: Batch Allocation (40-50 tracks per batch) with incremental checkpointing
    total_batches = (len(pending_tracks) + batch_size - 1) // batch_size
    for batch_idx in range(total_batches):
        batch = pending_tracks[batch_idx * batch_size : (batch_idx + 1) * batch_size]
        batch_map: Dict[str, TrackItem] = {t.videoId: t for t in batch}
        logger.info(
            "Passada 2B: Classificando lote %d/%d (%d faixas)...",
            batch_idx + 1,
            total_batches,
            len(batch),
        )

        prompt = _build_batch_prompt(batch, catalog)
        try:
            batch_resp = client.generate_structured(
                prompt=prompt,
                response_model=BatchClassificationResponse,
                temperature=0.15,
            )
            classifications_by_id = {c.videoId: c for c in batch_resp.classifications}
        except Exception as err:
            logger.error("Erro ao classificar lote %d: %s. Alocando lote em '%s'.", batch_idx + 1, err, FOLDER_DOUBT)
            classifications_by_id = {}

        for vid, track_obj in batch_map.items():
            cls_item = classifications_by_id.get(vid)
            if cls_item is None:
                # Deterministic guarantee: never drop an omitted track
                final_folders = apply_track_classification_rules(
                    track_obj,
                    [FOLDER_DOUBT],
                    confidence=0.0,
                )
            else:
                final_folders = apply_track_classification_rules(
                    track_obj,
                    cls_item.playlists,
                    cls_item.confidence,
                )

            for folder in final_folders:
                ckey = canonical_comparison_key(folder)
                canonical_folder = known_cat_keys.get(ckey, normalize_playlist_name(folder))
                if ckey not in known_cat_keys:
                    known_cat_keys[ckey] = canonical_folder
                    catalog.append(
                        TaxonomyCategory(
                            name=canonical_folder,
                            description="Subgênero aberto dinamicamente durante a alocação de lotes.",
                        )
                    )
                plan.add_track(canonical_folder, vid)

        # Save incremental checkpoint after every single batch
        initial_plan_path.parent.mkdir(parents=True, exist_ok=True)
        initial_plan_path.write_text(
            json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        catalog_path.write_text(
            json.dumps([c.model_dump() for c in catalog], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return plan
