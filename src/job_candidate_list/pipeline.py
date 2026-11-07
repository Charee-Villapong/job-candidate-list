"""第1段階の手動実行パイプライン（§3.15／§12、追補 A4）。

RunSummary は件数と候補 ID・情報源名のみを持ち、会社名・年収等の
詳細は含めない。段ごとの失敗時の扱いは §12 に従う（推測で埋めない、
リトライしない、止めるべき段で止めて要確認に倒す）。

`run_collection` は統括のみを行い、段ごとの処理は以下の部品に分ける:
収集（`_collect_postings`）、レコード構築（`_build_records` と
`_build_candidate_record`／`_build_exclusion_row`）、OpenWork 付与の
段内状態（`_OpenworkResolver`）、提示振り分け（`_apply_presentation`）、
書込（`_persist_results`）。
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from .banned_words import assert_no_banned_words, load_banned_words
from .config import AppConfig, OpenworkConfig
from .diff_guard import GuardStatus, compute_diff, has_auto_diff, mass_change_guard
from .errors import OpenworkBlocked
from .evaluate import EvaluationResult, evaluate_posting
from .judge_location import load_whitelist
from .models import (
    JOB_PAGE_DISPLAY_TEXT,
    UNKNOWN_LOCATION_TEXT,
    CandidateRecord,
    CandidateStatus,
    ExclusionRow,
    Fitness,
    LinkCell,
    LocationWhitelist,
    Presentation,
    RawPosting,
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
from .registry import (
    Registry,
    is_collectable,
    openwork_gate,
    parse_registry,
    validate_registry,
)
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


def _exclusion_id(posting: RawPosting, cfg: AppConfig) -> str:
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


def _load_whitelist(cfg: AppConfig) -> LocationWhitelist:
    """勤務地対応表を読む（設定が無ければ空の対応表で判定不能側に倒す）。"""
    if cfg.location.whitelist_file is not None:
        return load_whitelist(cfg.location.whitelist_file)
    return LocationWhitelist(version=1, municipalities=())


def _check_banned_words(cfg: AppConfig) -> None:
    """禁止ワードスキャン（手順4。設定がある場合のみ）。

    ファイルが無ければ load_banned_words が ConfigError を送出し、
    スキャン省略で続行しない。
    """
    if cfg.local.banned_words_file is not None:
        words = load_banned_words(cfg.local.banned_words_file)
        assert_no_banned_words(list(cfg.profile.search_queries), words)


def _collect_postings(
    cfg: AppConfig,
    registry: Registry,
    adapters: Mapping[str, SourceAdapter],
) -> tuple[list[RawPosting], tuple[str, ...], tuple[str, ...]]:
    """収集対象の情報源ごとに取得する（手順5）。

    取得エラー時はその情報源をスキップし、リトライしない。情報源
    ごとの上限件数で切り、候補 ID で重複を除く。
    戻り値は (求人一覧, 収集した情報源名, スキップした情報源名)。
    """
    collectable = [s for s in registry.sources if is_collectable(s)]
    source_names = tuple(s.source_id for s in collectable if s.source_id in adapters)
    skipped: list[str] = []
    postings: list[RawPosting] = []
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
    return postings, source_names, tuple(skipped)


def _index_existing(
    existing: Sequence[CandidateRecord],
) -> tuple[dict[str, CandidateRecord], dict[str, CandidateRecord]]:
    """既存候補を候補 ID と突合キー（会社名＋ポジション名）の両方で引けるようにする。"""
    by_id = {c.candidate_id: c for c in existing}
    by_fallback = {
        fallback_match_key(c.company_norm, c.position_norm): c for c in existing
    }
    return by_id, by_fallback


def _build_exclusion_row(
    posting: RawPosting, cfg: AppConfig, result: EvaluationResult
) -> ExclusionRow:
    """ハード除外ヒットから除外ログ行を組み立てる。"""
    reason_text = "／".join(hit.reason for hit in result.exclusions)
    return ExclusionRow(
        exclusion_id=_exclusion_id(posting, cfg),
        fetched_on=posting.fetched_on,
        company=posting.company_raw,
        position=posting.position_raw,
        employment_type=posting.employment_raw,
        source_type=posting.source_type,
        source_url=LinkCell(JOB_PAGE_DISPLAY_TEXT, posting.raw_url),
        reason=reason_text,
        rule_ids=tuple(dict.fromkeys(hit.rule_id for hit in result.exclusions)),
        config_refs=tuple(dict.fromkeys(hit.config_ref for hit in result.exclusions)),
    )


class _OpenworkResolver:
    """OpenWork 付与の段内状態（ゲート・遮断・無効件数）を束ねる。

    ゲート無効時は取得を試みず候補ごとに無効件数を数え、一度
    OpenworkBlocked が起きたら以降の候補では取得を試みない。
    取得できない場合は既存候補の前回値をそのまま保持する。
    """

    def __init__(
        self,
        *,
        gate_enabled: bool,
        provider: OpenworkProvider | None,
        cfg: OpenworkConfig,
    ) -> None:
        self._gate_enabled = gate_enabled
        self._provider = provider
        self._cfg = cfg
        self._blocked = False
        self.disabled_count = 0

    def resolve(
        self,
        posting: RawPosting,
        record_id: str,
        company_norm: str,
        old: CandidateRecord | None,
    ) -> tuple[float | None, LinkCell | None, dt.date | None]:
        """(点数, URL, 取得日) を返す。既定は既存候補の前回値。"""
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
        if not self._gate_enabled:
            self.disabled_count += 1
        elif not self._blocked:
            probe = CandidateRecord(
                candidate_id=record_id,
                company=posting.company_raw,
                company_norm=company_norm,
                location_text=posting.location_raw or UNKNOWN_LOCATION_TEXT,
            )
            try:
                ow = attach_openwork(
                    probe,
                    self._provider,
                    True,
                    self._cfg,
                    previous=previous,
                )
                ow_score, ow_url, ow_on = ow.score, ow.url, ow.fetched_on
            except OpenworkBlocked:
                self._blocked = True
        return ow_score, ow_url, ow_on


def _build_candidate_record(
    posting: RawPosting,
    result: EvaluationResult,
    old: CandidateRecord | None,
    record_id: str,
    company_norm: str,
    position_norm: str,
    openwork_values: tuple[float | None, LinkCell | None, dt.date | None],
) -> CandidateRecord:
    """判定結果と OpenWork 付与結果から候補レコードを組み立てる。

    既存候補があり自動列の内容が変わっていれば状態を「更新あり」にする
    （判定は diff_guard.has_auto_diff に一本化する）。
    """
    fitness_result = result.fitness_result
    assert fitness_result is not None  # ハード除外でない場合は必ず存在
    ow_score, ow_url, ow_on = openwork_values
    record = CandidateRecord(
        candidate_id=record_id,
        company=posting.company_raw,
        position=posting.position_raw,
        company_norm=company_norm,
        position_norm=position_norm,
        employment_type=posting.employment_raw or "",
        salary_range_text=posting.salary_raw,
        location_text=posting.location_raw or UNKNOWN_LOCATION_TEXT,
        location_result=result.location.division,
        remote_division=result.remote.division,
        fitness=fitness_result.fitness,
        fit_reasons=fitness_result.fit_reasons,
        check_reasons=fitness_result.check_reasons,
        source_type=posting.source_type,
        source_url=LinkCell(JOB_PAGE_DISPLAY_TEXT, posting.raw_url),
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
    if old is not None and has_auto_diff(record, old):
        record = replace(record, status=CandidateStatus.UPDATED)
    return record


def _build_records(
    cfg: AppConfig,
    whitelist: LocationWhitelist,
    postings: Sequence[RawPosting],
    existing_by_id: Mapping[str, CandidateRecord],
    existing_by_fallback: Mapping[str, CandidateRecord],
    resolver: _OpenworkResolver,
) -> tuple[list[CandidateRecord], list[ExclusionRow], list[str]]:
    """求人ごとに判定し、候補レコードと除外ログ行へ振り分ける。

    戻り値は (候補レコード, 除外ログ行, 除外 ID 一覧)。
    """
    current: list[CandidateRecord] = []
    exclusion_rows: list[ExclusionRow] = []
    excluded_ids: list[str] = []
    for posting in postings:
        result = evaluate_posting(posting, cfg, whitelist)
        company_norm = normalize_company(posting.company_raw)
        position_norm = normalize_position(posting.position_raw)
        url_norm = normalize_url(posting.raw_url, cfg.normalize.tracking_params)
        new_id = candidate_id(company_norm, position_norm, url_norm)
        if result.hard_excluded:
            row = _build_exclusion_row(posting, cfg, result)
            excluded_ids.append(row.exclusion_id)
            exclusion_rows.append(row)
            continue

        old = existing_by_id.get(new_id)
        if old is None:
            old = existing_by_fallback.get(
                fallback_match_key(company_norm, position_norm)
            )
        record_id = old.candidate_id if old is not None else new_id
        openwork_values = resolver.resolve(posting, record_id, company_norm, old)
        current.append(
            _build_candidate_record(
                posting,
                result,
                old,
                record_id,
                company_norm,
                position_norm,
                openwork_values,
            )
        )
    return current, exclusion_rows, excluded_ids


def _apply_presentation(
    current: list[CandidateRecord], weekly_limit: int
) -> list[CandidateRecord]:
    """提示・控えの振り分け（手順10）を反映した候補一覧を返す。"""
    presented, _held = split_presentation(current, weekly_limit)
    presented_ids = {c.candidate_id for c in presented}
    if not presented_ids:
        return current
    return [
        (
            replace(c, presentation=Presentation.PRESENTED)
            if c.candidate_id in presented_ids
            else c
        )
        for c in current
    ]


def _persist_results(
    gateway: SheetsGateway,
    *,
    dry_run: bool,
    stopped: bool,
    current: Sequence[CandidateRecord],
    existing: Sequence[CandidateRecord],
    closed_ids: Sequence[str],
    exclusion_rows: Sequence[ExclusionRow],
) -> None:
    """本実行かつガード停止でない場合のみ Sheets へ書く。

    掲載終了の既存候補は状態を「掲載終了」に差し替えてあわせて書く。
    """
    if dry_run or stopped:
        return
    closed_set = set(closed_ids)
    closed_records = [
        replace(c, status=CandidateStatus.CLOSED)
        for c in existing
        if c.candidate_id in closed_set
    ]
    gateway.upsert_candidates([*current, *closed_records])
    if exclusion_rows:
        gateway.append_exclusions(exclusion_rows)


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

    whitelist = _load_whitelist(cfg)
    _check_banned_words(cfg)

    postings, source_names, skipped_source_names = _collect_postings(
        cfg, registry, adapters
    )
    existing_by_id, existing_by_fallback = _index_existing(existing)

    resolver = _OpenworkResolver(
        gate_enabled=openwork_gate(registry) and openwork_provider is not None,
        provider=openwork_provider,
        cfg=cfg.openwork,
    )
    current, exclusion_rows, excluded_ids = _build_records(
        cfg,
        whitelist,
        postings,
        existing_by_id,
        existing_by_fallback,
        resolver,
    )

    # 提示・控えの振り分け（手順10）を差分計算の前に行い、提示予定を差分へ含める。
    current = _apply_presentation(current, cfg.presentation.weekly_limit)

    diff = compute_diff(existing, current, None, registry)
    guard = mass_change_guard(diff, len(existing), cfg.guard)
    stopped = guard.status == GuardStatus.STOP

    _persist_results(
        gateway,
        dry_run=dry_run,
        stopped=stopped,
        current=current,
        existing=existing,
        closed_ids=diff.closed,
        exclusion_rows=exclusion_rows,
    )

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
        openwork_disabled_count=resolver.disabled_count,
        source_names=source_names,
        skipped_source_names=skipped_source_names,
        added_ids=diff.added,
        updated_ids=diff.updated,
        closed_ids=diff.closed,
        excluded_ids=tuple(excluded_ids),
    )
