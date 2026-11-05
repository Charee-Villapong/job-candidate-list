"""第1段階の手動実行パイプライン（§3.15／§12、追補 A4）。

RunSummary は件数と候補 ID・情報源名のみを持ち、会社名・年収等の
詳細は含めない。段ごとの失敗時の扱いは §12 に従う（推測で埋めない、
リトライしない、止めるべき段で止めて要確認に倒す）。
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, replace
from typing import Mapping

from .banned_words import assert_no_banned_words, load_banned_words
from .config import AppConfig
from .diff_guard import GuardStatus, compute_diff, mass_change_guard
from .errors import OpenworkBlocked
from .evaluate import evaluate_posting
from .judge_location import load_whitelist
from .models import (
    CandidateRecord,
    CandidateStatus,
    ExclusionRow,
    Fitness,
    LinkCell,
    LocationWhitelist,
    Presentation,
)
from .normalize import (
    candidate_id,
    fallback_match_key,
    normalize_company,
    normalize_position,
    normalize_url,
)
from .openwork import OpenworkProvider, OpenworkResult, attach_openwork
from .presentation import split_presentation
from .registry import is_collectable, openwork_gate, parse_registry, validate_registry
from .sheets_gateway import SheetsGateway
from .sources.base import SourceAdapter


@dataclass(frozen=True)
class RunSummary:
    run_on: dt.date
    dry_run: bool
    stopped_by_guard: bool
    collected_count: int
    added_count: int
    updated_count: int
    closed_count: int
    excluded_count: int
    check_only_count: int
    check_counts_by_reason: tuple[tuple[str, int], ...]
    presented_count: int
    held_new_count: int
    held_promoted_count: int
    held_remaining_count: int
    held_closed_count: int
    openwork_disabled_count: int
    source_names: tuple[str, ...]
    skipped_source_names: tuple[str, ...]
    added_ids: tuple[str, ...]
    updated_ids: tuple[str, ...]
    closed_ids: tuple[str, ...]
    excluded_ids: tuple[str, ...]


def build_notification_text(summary: RunSummary, list_url: str) -> str:
    """通知文を固定テンプレートで生成する（追補 A4.2）。詳細は含めない。"""
    date_text = summary.run_on.isoformat()
    if summary.stopped_by_guard:
        first = f"応募候補リストの更新を停止しました（大量変更ガード・{date_text}）"
    elif summary.dry_run:
        first = f"応募候補リストのドライラン結果（{date_text}）"
    else:
        first = f"応募候補リストを更新しました（{date_text}）"
    lines = [
        first,
        (
            f"新規 {summary.added_count}件／更新 {summary.updated_count}件／"
            f"掲載終了 {summary.closed_count}件／除外 {summary.excluded_count}件／"
            f"要確認 {summary.check_only_count}件"
        ),
        (
            f"提示 {summary.presented_count}件／"
            f"控え 新規{summary.held_new_count}件・"
            f"繰り上げ{summary.held_promoted_count}件・"
            f"残{summary.held_remaining_count}件"
        ),
        f"候補リスト: {list_url}",
    ]
    return "\n".join(lines)


def _exclusion_id(posting, cfg: AppConfig) -> str:
    key = fallback_match_key(
        normalize_company(posting.company_raw),
        normalize_position(posting.position_raw),
    )
    url_norm = normalize_url(posting.raw_url, cfg.normalize.tracking_params)
    digest = hashlib.sha256(f"{key}\n{url_norm}".encode("utf-8")).hexdigest()
    return "excl_" + digest[:16]


def _check_counts(records: list[CandidateRecord]) -> tuple[tuple[str, int], ...]:
    counts = {"主務": 0, "英語": 0, "勤務地・リモート": 0, "その他": 0}
    for record in records:
        if Fitness(record.fitness) != Fitness.CHECK:
            continue
        rule_ids: set[str] = set()
        for reason in record.check_reasons:
            rule_ids.update(reason.rule_ids)
        categorized = False
        if any(r.startswith("JOB-") for r in rule_ids):
            counts["主務"] += 1
            categorized = True
        if any(r.startswith("ENG-") for r in rule_ids):
            counts["英語"] += 1
            categorized = True
        if any(r.startswith("LOC-") or r.startswith("REMOTE-") for r in rule_ids):
            counts["勤務地・リモート"] += 1
            categorized = True
        if not categorized:
            counts["その他"] += 1
    return tuple((name, n) for name, n in counts.items() if n > 0)


def run_collection(
    cfg: AppConfig,
    gateway: SheetsGateway,
    adapters: Mapping[str, SourceAdapter],
    openwork_provider: OpenworkProvider | None,
    dry_run: bool,
) -> RunSummary:
    run_on = dt.date.today()
    # 手順2: レジストリ読取・検証（全体停止条件は例外として送出する）。
    registry = parse_registry(gateway.read_registry_rows())
    validate_registry(registry)
    existing = list(gateway.read_candidates())

    # 勤務地対応表（設定が無ければ空の対応表で判定不能側に倒す）。
    if cfg.location.whitelist_file is not None:
        whitelist = load_whitelist(cfg.location.whitelist_file)
    else:
        whitelist = LocationWhitelist(version=1, municipalities=())

    # 手順4: 禁止ワードスキャン（設定がある場合のみ。ファイルが無ければ
    # load_banned_words が ConfigError を送出し、スキャン省略で続行しない）。
    if cfg.local.banned_words_file is not None:
        words = load_banned_words(cfg.local.banned_words_file)
        assert_no_banned_words(list(cfg.profile.search_queries), words)

    collectable = [s for s in registry.sources if is_collectable(s)]
    source_names = tuple(s.source_id for s in collectable if s.source_id in adapters)
    skipped: list[str] = []
    postings = []
    seen_keys: set[str] = set()
    for source in collectable:
        adapter = adapters.get(source.source_id)
        if adapter is None:
            continue
        try:
            fetched = adapter.fetch(source)
        except Exception:
            skipped.append(source.source_id)
            continue
        for posting in fetched[: cfg.collection.per_source_max_items]:
            dedupe_key = candidate_id(
                normalize_company(posting.company_raw),
                normalize_position(posting.position_raw),
                normalize_url(posting.raw_url, cfg.normalize.tracking_params),
            )
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            postings.append(posting)

    existing_by_id = {c.candidate_id: c for c in existing}
    existing_by_fallback = {
        fallback_match_key(c.company_norm, c.position_norm): c for c in existing
    }

    gate = openwork_gate(registry) and openwork_provider is not None
    openwork_blocked = False
    openwork_disabled_count = 0

    current: list[CandidateRecord] = []
    exclusion_rows: list[ExclusionRow] = []
    excluded_ids: list[str] = []
    matched_existing_ids: set[str] = set()

    for posting in postings:
        result = evaluate_posting(posting, cfg, whitelist)
        company_norm = normalize_company(posting.company_raw)
        position_norm = normalize_position(posting.position_raw)
        url_norm = normalize_url(posting.raw_url, cfg.normalize.tracking_params)
        new_id = candidate_id(company_norm, position_norm, url_norm)
        if result.hard_excluded:
            excl_id = _exclusion_id(posting, cfg)
            excluded_ids.append(excl_id)
            reason_text = "／".join(hit.reason for hit in result.exclusions)
            exclusion_rows.append(
                ExclusionRow(
                    exclusion_id=excl_id,
                    fetched_on=posting.fetched_on,
                    company=posting.company_raw,
                    position=posting.position_raw,
                    employment_type=posting.employment_raw,
                    source_type=posting.source_type,
                    source_url=LinkCell("求人ページ", posting.raw_url),
                    reason=reason_text,
                    rule_ids=tuple(
                        dict.fromkeys(hit.rule_id for hit in result.exclusions)
                    ),
                    config_refs=tuple(
                        dict.fromkeys(hit.config_ref for hit in result.exclusions)
                    ),
                )
            )
            continue

        fitness_result = result.fitness_result
        assert fitness_result is not None  # ハード除外でない場合は必ず存在
        old = existing_by_id.get(new_id)
        if old is None:
            old = existing_by_fallback.get(
                fallback_match_key(company_norm, position_norm)
            )
        record_id = old.candidate_id if old is not None else new_id
        if old is not None:
            matched_existing_ids.add(old.candidate_id)

        # OpenWork 付与（ゲート無効時は取得を試みず空欄）。
        previous: OpenworkResult | None = None
        if old is not None and (
            old.openwork_score is not None or old.openwork_url is not None
        ):
            previous = OpenworkResult(
                score=old.openwork_score,
                url=old.openwork_url,
                fetched_on=old.openwork_fetched_on,
                rule_ids=(),
            )
        ow_score = old.openwork_score if old is not None else None
        ow_url = old.openwork_url if old is not None else None
        ow_on = old.openwork_fetched_on if old is not None else None
        if not gate:
            openwork_disabled_count += 1
        elif not openwork_blocked:
            probe = CandidateRecord(
                candidate_id=record_id,
                company=posting.company_raw,
                company_norm=company_norm,
                location_text=posting.location_raw or "不明",
            )
            try:
                ow = attach_openwork(
                    probe,
                    openwork_provider,
                    True,
                    cfg.openwork,
                    previous=previous,
                )
                ow_score, ow_url, ow_on = ow.score, ow.url, ow.fetched_on
            except OpenworkBlocked:
                openwork_blocked = True

        record = CandidateRecord(
            candidate_id=record_id,
            company=posting.company_raw,
            position=posting.position_raw,
            company_norm=company_norm,
            position_norm=position_norm,
            employment_type=posting.employment_raw or "",
            salary_range_text=posting.salary_raw,
            location_text=posting.location_raw or "不明",
            location_result=result.location.division,
            remote_division=result.remote.division,
            fitness=fitness_result.fitness,
            fit_reasons=fitness_result.fit_reasons,
            check_reasons=fitness_result.check_reasons,
            source_type=posting.source_type,
            source_url=LinkCell("求人ページ", posting.raw_url),
            openwork_score=ow_score,
            openwork_url=ow_url,
            openwork_fetched_on=ow_on,
            presentation=(old.presentation if old is not None else Presentation.HELD),
            presentation_key=fitness_result.presentation_key,
            manual_judgment=old.manual_judgment if old is not None else None,
            confirmed_on=posting.fetched_on,
            status=(CandidateStatus.NEW if old is None else CandidateStatus.CONTINUING),
            apply_status=old.apply_status if old is not None else "未応募",
            applied_at=old.applied_at if old is not None else None,
            apply_result=old.apply_result if old is not None else None,
        )
        if old is not None:
            from .diff_guard import _auto_signature

            if _auto_signature(record) != _auto_signature(old):
                record = replace(record, status=CandidateStatus.UPDATED)
        current.append(record)

    # 提示・控えの振り分け（手順10）を差分計算の前に行い、提示予定を差分へ含める。
    presented, _held = split_presentation(current, cfg.presentation.weekly_limit)
    presented_ids = {c.candidate_id for c in presented}
    if presented_ids:
        current = [
            (
                replace(c, presentation=Presentation.PRESENTED)
                if c.candidate_id in presented_ids
                else c
            )
            for c in current
        ]

    diff = compute_diff(existing, current, None, registry)
    guard = mass_change_guard(diff, len(existing), cfg.guard)
    stopped = guard.status == GuardStatus.STOP

    if not dry_run and not stopped:
        closed_records = [
            replace(c, status=CandidateStatus.CLOSED)
            for c in existing
            if c.candidate_id in set(diff.closed)
        ]
        gateway.upsert_candidates([*current, *closed_records])
        if exclusion_rows:
            gateway.append_exclusions(exclusion_rows)

    return RunSummary(
        run_on=run_on,
        dry_run=dry_run,
        stopped_by_guard=stopped,
        collected_count=len(postings),
        added_count=len(diff.added),
        updated_count=len(diff.updated),
        closed_count=len(diff.closed),
        excluded_count=len(excluded_ids),
        check_only_count=diff.check_only_count,
        check_counts_by_reason=_check_counts(current),
        presented_count=len(diff.presented_planned),
        held_new_count=len(diff.held_new),
        held_promoted_count=len(diff.held_promoted),
        held_remaining_count=len(diff.held_remaining),
        held_closed_count=len(diff.held_closed),
        openwork_disabled_count=openwork_disabled_count,
        source_names=source_names,
        skipped_source_names=tuple(skipped),
        added_ids=diff.added,
        updated_ids=diff.updated,
        closed_ids=diff.closed,
        excluded_ids=tuple(excluded_ids),
    )
