"""Stage 3: Corregedoria QA Auditor (Deterministic Layer A + Semantic LLM Layer B + Interactive HITL)."""

from __future__ import annotations

from difflib import SequenceMatcher
import json
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config import (
    AUDITED_PLAN_PATH,
    FOLDER_DOUBT,
    FOLDER_LONG_MUSICAL,
    FOLDER_NON_MUSIC_PODCASTS,
    FOLDER_NON_MUSIC_RANDOM,
    INITIAL_PLAN_PATH,
    LONG_MEDIA_THRESHOLD_SECONDS,
    RAW_LIKES_PATH,
)
from src.classifier_agent import apply_track_classification_rules
from src.llm_adapter import StructuredLLMClient
from src.models import (
    AuditReport,
    BatchClassificationResponse,
    CorregedoriaReviewResponse,
    PlaylistPlan,
    TrackItem,
    UserPromptAdjustmentAction,
    canonical_comparison_key,
    normalize_playlist_name,
)

logger = logging.getLogger("ytmusic_taxonomist.corregedoria")
console = Console()


def _has_diacritics(text: str) -> bool:
    return any(ord(c) > 127 for c in text)


def _choose_preferred_title(title_a: str, title_b: str) -> str:
    """Prefer the playlist name with proper Portuguese accents/diacritics."""
    if _has_diacritics(title_a) and not _has_diacritics(title_b):
        return title_a
    if _has_diacritics(title_b) and not _has_diacritics(title_a):
        return title_b
    return title_a


def _is_non_music_folder(folder_name: str) -> bool:
    norm = normalize_playlist_name(folder_name).lower()
    return norm.startswith("não-música") or norm.startswith("nao-musica")


def run_deterministic_audit(
    plan: PlaylistPlan,
    raw_tracks: List[TrackItem],
    llm: Optional[StructuredLLMClient] = None,
) -> Tuple[PlaylistPlan, AuditReport]:
    """Layer A: Deterministic Python QA (fuzzy/accent merge, duration guard, 100% coverage & orphan recovery)."""
    report = AuditReport(total_raw_tracks=len(raw_tracks))
    track_by_id: Dict[str, TrackItem] = {t.videoId: t for t in raw_tracks}

    # 1. Normalize names and merge duplicate/near-identical playlist names (accent/case/typo)
    merged_plan = PlaylistPlan()
    canonical_map: Dict[str, str] = {}

    for raw_pl_name, video_ids in plan.playlists.items():
        norm_name = normalize_playlist_name(raw_pl_name)
        ckey = canonical_comparison_key(norm_name)

        matched_key: Optional[str] = None
        if ckey in canonical_map:
            matched_key = ckey
        else:
            # Check fuzzy similarity (> 0.91) within the same macro-genre prefix
            macro_prefix = ckey.split(" ")[0] if ckey else ""
            for existing_ckey in canonical_map:
                if existing_ckey.startswith(macro_prefix):
                    ratio = SequenceMatcher(None, ckey, existing_ckey).ratio()
                    if ratio >= 0.91:
                        matched_key = existing_ckey
                        break

        if matched_key is not None:
            existing_title = canonical_map[matched_key]
            preferred_title = _choose_preferred_title(existing_title, norm_name)
            if preferred_title != existing_title and existing_title in merged_plan.playlists:
                existing_vids = merged_plan.playlists.pop(existing_title)
                for v in existing_vids:
                    merged_plan.add_track(preferred_title, v)
                canonical_map[matched_key] = preferred_title
            if norm_name != canonical_map[matched_key]:
                report.merged_playlists.append(f"{raw_pl_name} -> {canonical_map[matched_key]}")
            target_title = canonical_map[matched_key]
        else:
            canonical_map[ckey] = norm_name
            target_title = norm_name

        for vid in video_ids:
            if vid in track_by_id:
                merged_plan.add_track(target_title, vid)

    # 2. Enforce long musical media isolation (>= LONG_MEDIA_THRESHOLD_SECONDS)
    for pl_name in list(merged_plan.playlists.keys()):
        if pl_name == FOLDER_LONG_MUSICAL or _is_non_music_folder(pl_name):
            continue
        for vid in list(merged_plan.playlists.get(pl_name, [])):
            tr = track_by_id.get(vid)
            if tr and tr.duration_seconds >= LONG_MEDIA_THRESHOLD_SECONDS:
                merged_plan.remove_track(pl_name, vid)
                merged_plan.add_track(FOLDER_LONG_MUSICAL, vid)
                report.long_tracks_isolated += 1

    # 3. 100% Coverage Verification & Orphan Reprocessing
    expected_ids: Set[str] = set(track_by_id.keys())
    missing_ids = merged_plan.missing_video_ids(expected_ids)

    if missing_ids:
        report.orphans_recovered = len(missing_ids)
        orphan_tracks = [track_by_id[vid] for vid in sorted(missing_ids)]
        logger.warning(
            "[Corregedoria Camada A] Detectados %d videoIds órfãos! Forçando reprocessamento...",
            len(orphan_tracks),
        )
        if llm is not None:
            try:
                existing_folders = sorted(merged_plan.playlists.keys())
                orphan_payload = [
                    {
                        "videoId": t.videoId,
                        "title": t.title,
                        "artists": t.artists,
                        "duration_seconds": t.duration_seconds,
                        "videoType": t.videoType,
                    }
                    for t in orphan_tracks
                ]
                prompt = (
                    "Classifique obrigatoriamente os seguintes videoIds órfãos em pastas no padrão "
                    f"`[Macro] - [Subgênero/Vibe]`. Pastas já existentes: {existing_folders}\n\n"
                    f"Faixas órfãs:\n{json.dumps(orphan_payload, ensure_ascii=False, indent=2)}"
                )
                resp = llm.generate_structured(prompt=prompt, response_model=BatchClassificationResponse)
                for cls_item in resp.classifications:
                    tr_obj = track_by_id.get(cls_item.videoId)
                    if tr_obj:
                        folders = apply_track_classification_rules(
                            tr_obj,
                            cls_item.playlists,
                            cls_item.confidence,
                        )
                        for f in folders:
                            merged_plan.add_track(f, tr_obj.videoId)
            except Exception as err:
                logger.error("Falha ao reprocessar órfãos via LLM: %s. Alocando em '%s'.", err, FOLDER_DOUBT)

        # Final deterministic safety net: guarantee 100% mathematical coverage
        still_missing = merged_plan.missing_video_ids(expected_ids)
        for vid in sorted(still_missing):
            tr_obj = track_by_id[vid]
            if tr_obj.duration_seconds >= LONG_MEDIA_THRESHOLD_SECONDS:
                merged_plan.add_track(FOLDER_LONG_MUSICAL, vid)
            else:
                merged_plan.add_track(FOLDER_DOUBT, vid)

    allocated_after = merged_plan.all_allocated_video_ids()
    report.total_allocated_tracks = len(allocated_after)
    report.coverage_percent = (
        (len(allocated_after) / len(expected_ids) * 100.0) if expected_ids else 100.0
    )
    return merged_plan, report


def run_semantic_audit(
    plan: PlaylistPlan,
    raw_tracks: List[TrackItem],
    llm: StructuredLLMClient,
    report: Optional[AuditReport] = None,
) -> Tuple[PlaylistPlan, AuditReport]:
    """Layer B: LLM Semantic QA (Funk isolation, Non-Music false positives, and Doubt folder rescue)."""
    audit_report = report or AuditReport(
        total_raw_tracks=len(raw_tracks),
        total_allocated_tracks=len(plan.all_allocated_video_ids()),
    )
    track_by_id: Dict[str, TrackItem] = {t.videoId: t for t in raw_tracks}

    # Group playlists into compact chunks of at most 5 playlists to guarantee small payloads (<15 KB)
    all_playlist_names = sorted(plan.playlists.keys())
    chunk_size = 5
    playlist_chunks = [
        all_playlist_names[i : i + chunk_size]
        for i in range(0, len(all_playlist_names), chunk_size)
    ]

    logger.info("Executando Auditoria Semântica (Camada B) em %d blocos...", len(playlist_chunks))

    for chunk_idx, pl_names in enumerate(playlist_chunks):
        inspection_snapshot: Dict[str, List[Dict[str, object]]] = {}
        for pl_name in pl_names:
            vids = plan.playlists.get(pl_name, [])
            sample_vids = vids if len(vids) <= 25 else vids[:25]
            inspection_snapshot[pl_name] = [
                {
                    "videoId": vid,
                    "title": track_by_id[vid].title,
                    "artists": track_by_id[vid].artists,
                    "duration_seconds": track_by_id[vid].duration_seconds,
                    "videoType": track_by_id[vid].videoType,
                }
                for vid in sample_vids
                if vid in track_by_id
            ]

        prompt = f"""Atue como Corregedor Musical e Auditor de QA implacável sobre o bloco de playlists abaixo ({chunk_idx + 1}/{len(playlist_chunks)}).

### Missões Obrigatórias da Corregedoria:
1. **Caça a Intrusos em Funk (`Funk - *`)**: Verifique se há músicas que NÃO são Funk infiltradas em pastas `Funk - *`, ou se há músicas de Funk vazadas em pastas de Pop, Hip-Hop ou Eletrônica.
2. **Caça a Falsos Positivos de Música**: Verifique se algum vídeo de gameplay, review de tecnologia, tutorial, aula, entrevista ou podcast (`MUSIC_VIDEO_TYPE_UGC`) passou batido em pastas musicais e mova para `{FOLDER_NON_MUSIC_PODCASTS}` ou `{FOLDER_NON_MUSIC_RANDOM}`.
3. **Resgate da Pasta `{FOLDER_DOUBT}`**: Analise os artistas e títulos dentro de `{FOLDER_DOUBT}` e tente inferir o subgênero real no padrão `[Macro] - [Subgênero/Vibe]` quando houver pista suficiente.

Retorne apenas as movimentações necessárias em `moves`. Se todas as faixas deste bloco estiverem corretas, retorne `moves: []`.

### Snapshot deste Bloco de Playlists:
{json.dumps(inspection_snapshot, ensure_ascii=False, indent=2)}
"""

        try:
            review = llm.generate_structured(
                prompt=prompt,
                response_model=CorregedoriaReviewResponse,
                temperature=0.1,
            )
            for move in review.moves:
                if move.videoId not in track_by_id:
                    continue
                target_folder = normalize_playlist_name(move.to_playlist)
                tr = track_by_id[move.videoId]
                if tr.duration_seconds >= LONG_MEDIA_THRESHOLD_SECONDS and not _is_non_music_folder(target_folder):
                    target_folder = FOLDER_LONG_MUSICAL

                if move.from_playlist in plan.playlists and move.videoId in plan.playlists[move.from_playlist]:
                    plan.remove_track(move.from_playlist, move.videoId)
                    if move.from_playlist == FOLDER_DOUBT and target_folder != FOLDER_DOUBT:
                        audit_report.doubt_tracks_rescued += 1

                plan.add_track(target_folder, move.videoId)
                audit_report.semantic_moves_applied += 1
        except Exception as err:
            logger.warning("Bloco %d da auditoria semântica falhou (%s). Mantendo alocações originais.", chunk_idx + 1, err)

    # Ensure no track became orphaned after semantic moves
    for vid in plan.missing_video_ids(set(track_by_id.keys())):
        plan.add_track(FOLDER_DOUBT, vid)

    return plan, audit_report


def apply_user_prompt_adjustment(
    plan: PlaylistPlan,
    raw_tracks: List[TrackItem],
    instruction: str,
    llm: StructuredLLMClient,
) -> PlaylistPlan:
    """Interpret and execute a natural-language adjustment requested by the user in the HITL menu."""
    track_by_id: Dict[str, TrackItem] = {t.videoId: t for t in raw_tracks}
    folder_summary = {
        pl_name: [
            {"videoId": vid, "artists": track_by_id[vid].artists, "title": track_by_id[vid].title}
            for vid in vids[:3]
            if vid in track_by_id
        ]
        for pl_name, vids in sorted(plan.playlists.items())
    }

    prompt = f"""O usuário solicitou o seguinte ajuste manual sobre o plano de playlists:
"{instruction}"

### Playlists Atuais (com amostra de faixas):
{json.dumps(folder_summary, ensure_ascii=False, indent=2)}

Traduza o pedido do usuário em:
- `merge_playlists`: dicionário ` {{"Nome Antigo": "Nome Novo no padrão [Macro] - [Subgênero]"}} ` para juntar ou renomear pastas inteiras.
- `track_moves`: movimentações individuais de `videoId` de uma pasta para outra (ex: para dividir uma pasta ou mover um artista).
"""
    action = llm.generate_structured(
        prompt=prompt,
        response_model=UserPromptAdjustmentAction,
        temperature=0.1,
    )

    # 1. Apply playlist merges/renames
    for src_name, dst_name in action.merge_playlists.items():
        if src_name in plan.playlists:
            vids = plan.playlists.pop(src_name)
            norm_dst = normalize_playlist_name(dst_name)
            for v in vids:
                plan.add_track(norm_dst, v)

    # 2. Apply delete_playlists_to_doubt
    for del_name in action.delete_playlists_to_doubt:
        if del_name in plan.playlists:
            vids = plan.playlists.pop(del_name)
            for v in vids:
                plan.add_track(FOLDER_DOUBT, v)

    # 3. Apply specific track moves
    for mv in action.track_moves:
        if mv.videoId in track_by_id:
            if mv.from_playlist in plan.playlists:
                plan.remove_track(mv.from_playlist, mv.videoId)
            plan.add_track(normalize_playlist_name(mv.to_playlist), mv.videoId)

    # Guarantee 100% coverage remains intact
    for vid in plan.missing_video_ids(set(track_by_id.keys())):
        plan.add_track(FOLDER_DOUBT, vid)

    return plan


def render_corregedoria_table(
    plan: PlaylistPlan,
    raw_tracks: List[TrackItem],
    report: AuditReport,
    target_console: Optional[Console] = None,
) -> None:
    """Display the formatted HITL summary table in the terminal."""
    out = target_console or console
    track_by_id: Dict[str, TrackItem] = {t.videoId: t for t in raw_tracks}

    out.print(
        Panel.fit(
            f"[bold green]Cobertura Total:[/bold green] {report.total_allocated_tracks}/{report.total_raw_tracks} "
            f"({report.coverage_percent:.1f}%) | "
            f"[bold yellow]Órfãos Recuperados:[/bold yellow] {report.orphans_recovered} | "
            f"[bold cyan]Merges:[/bold cyan] {len(report.merged_playlists)} | "
            f"[bold magenta]Correções Semânticas:[/bold magenta] {report.semantic_moves_applied} | "
            f"[bold blue]Resgates de Dúvida:[/bold blue] {report.doubt_tracks_rescued}",
            title="🛡️ Relatório da Corregedoria (QA Auditor)",
        )
    )

    table = Table(title="Plano de Playlists Auditado (Ordem Alfabética — ArchiveTune)", show_lines=True)
    table.add_column("Nome da Playlist", style="bold cyan", no_wrap=False)
    table.add_column("Qtd de Faixas", justify="right", style="bold green")
    table.add_column("3 Exemplos de Artistas / Faixas", style="white")

    for pl_name in sorted(plan.playlists.keys()):
        vids = plan.playlists[pl_name]
        examples: List[str] = []
        for vid in vids[:3]:
            tr = track_by_id.get(vid)
            if tr:
                examples.append(f"{tr.artists} - {tr.title}")
            else:
                examples.append(vid)
        table.add_row(pl_name, str(len(vids)), " | ".join(examples))

    out.print(table)


def run_stage3_corregedoria(
    raw_likes_path: Path = RAW_LIKES_PATH,
    initial_plan_path: Path = INITIAL_PLAN_PATH,
    audited_plan_path: Path = AUDITED_PLAN_PATH,
    llm: Optional[StructuredLLMClient] = None,
    interactive: bool = True,
    input_fn: Callable[[str], str] = input,
) -> Tuple[PlaylistPlan, AuditReport, str]:
    """Execute full Stage 3 Corregedoria (Layer A + Layer B + HITL menu) and return `(plan, report, decision)`."""
    if not raw_likes_path.is_file():
        raise FileNotFoundError(f"Arquivo {raw_likes_path} não encontrado.")
    if not initial_plan_path.is_file():
        raise FileNotFoundError(f"Arquivo {initial_plan_path} não encontrado. Rode o Estágio 2 primeiro.")

    client = llm or StructuredLLMClient()
    raw_tracks = [
        TrackItem.model_validate(item)
        for item in json.loads(raw_likes_path.read_text(encoding="utf-8"))
    ]
    initial_plan = PlaylistPlan.model_validate_json(initial_plan_path.read_text(encoding="utf-8"))

    # Load existing audited plan checkpoint if already present
    if audited_plan_path.is_file():
        logger.info("Plano auditado existente carregado de %s.", audited_plan_path)
        plan = PlaylistPlan.model_validate_json(audited_plan_path.read_text(encoding="utf-8"))
        report = AuditReport(
            total_raw_tracks=len(raw_tracks),
            total_allocated_tracks=len(plan.all_allocated_video_ids()),
            coverage_percent=(len(plan.all_allocated_video_ids()) / len(raw_tracks) * 100.0) if raw_tracks else 100.0,
        )
    else:
        # Layer A: Deterministic QA
        plan, report = run_deterministic_audit(initial_plan, raw_tracks, llm=client)
        # Layer B: Semantic LLM QA
        plan, report = run_semantic_audit(plan, raw_tracks, llm=client, report=report)

        # Save audited plan checkpoint
        audited_plan_path.parent.mkdir(parents=True, exist_ok=True)
        audited_plan_path.write_text(
            json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if not interactive:
        render_corregedoria_table(plan, raw_tracks, report)
        return plan, report, "dry_run"

    while True:
        render_corregedoria_table(plan, raw_tracks, report)
        console.print("\n[bold]Menu de Aprovação Humana (Corregedoria HITL):[/bold]")
        console.print("  [bold green][1][/bold green] Aprovar e Sincronizar com YouTube Music")
        console.print(
            "  [bold yellow][2][/bold yellow] Pedir ajuste à Corregedoria via prompt (ex: 'Junte a pasta X com a Y', 'Divida a pasta Z')"
        )
        console.print("  [bold cyan][3][/bold cyan] Exportar plano final e sair (Dry-Run)")

        choice = input_fn("\nEscolha uma opção [1/2/3]: ").strip()
        if choice == "1":
            audited_plan_path.write_text(
                json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return plan, report, "approve_and_sync"
        if choice == "2":
            instruction = input_fn("Digite sua instrução para a Corregedoria: ").strip()
            if instruction:
                plan = apply_user_prompt_adjustment(plan, raw_tracks, instruction, llm=client)
                plan, report = run_deterministic_audit(plan, raw_tracks, llm=client)
                audited_plan_path.write_text(
                    json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        elif choice == "3":
            audited_plan_path.write_text(
                json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return plan, report, "dry_run"
        else:
            console.print("[red]Opção inválida. Digite 1, 2 ou 3.[/red]")
