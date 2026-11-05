"""ドライラン差分・大量変更ガード・レジストリ重大変更の検出（§3.13）。

Diff は件数と対象 ID の集合のみを持ち、会社名等の詳細は持たない。
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Sequence

from .config import GuardConfig
from .models import CandidateRecord, Fitness, Presentation
from .registry import Registry

# 比較対象外のフィールド（人手列スナップショットと提示順の内部キー）。
_NON_AUTO_FIELDS = {"manual_judgment", "presentation_key"}


@dataclass(frozen=True)
class Diff:
    added: tuple[str, ...]
    updated: tuple[str, ...]
    closed: tuple[str, ...]
    presented_planned: tuple[str, ...]
    held_new: tuple[str, ...]
    held_promoted: tuple[str, ...]
    held_remaining: tuple[str, ...]
    held_closed: tuple[str, ...]
    check_only_count: int
    registry_changes: tuple[str, ...]
    auto_apply_stop: bool = False


class GuardStatus(StrEnum):
    OK = "ok"
    STOP = "stop"


@dataclass(frozen=True)
class GuardResult:
    status: GuardStatus
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, GuardStatus):
            object.__setattr__(self, "status", GuardStatus(self.status))
        object.__setattr__(self, "reasons", tuple(self.reasons))


def _auto_signature(record: CandidateRecord) -> tuple:
    values = []
    for f in fields(record):
        if f.name in _NON_AUTO_FIELDS:
            continue
        values.append(getattr(record, f.name))
    return tuple(values)


def _registry_changes(
    old_registry: Registry | None, new_registry: Registry | None
) -> tuple[tuple[str, ...], bool]:
    if old_registry is None or new_registry is None:
        return (), False
    changes: list[str] = []
    stop = False
    old_by_id = {s.source_id: s for s in old_registry.sources}
    new_by_id = {s.source_id: s for s in new_registry.sources}
    for source_id in new_by_id:
        if source_id not in old_by_id:
            changes.append(f"情報源追加: {source_id}")
            stop = True
    for source_id in old_by_id:
        if source_id not in new_by_id:
            changes.append(f"情報源削除: {source_id}")
    for source_id, new_source in new_by_id.items():
        old_source = old_by_id.get(source_id)
        if old_source is None:
            continue
        if old_source.auto_apply != new_source.auto_apply:
            changes.append(f"自動応募可否変更: {source_id}")
            stop = True
        if old_source.send_method != new_source.send_method:
            changes.append(f"送信確定方法変更: {source_id}")
            stop = True
    return tuple(changes), stop


def compute_diff(
    old: Sequence[CandidateRecord],
    new: Sequence[CandidateRecord],
    old_registry: Registry | None,
    new_registry: Registry | None,
) -> Diff:
    old_by_id = {c.candidate_id: c for c in old}
    new_by_id = {c.candidate_id: c for c in new}
    added = tuple(c.candidate_id for c in new if c.candidate_id not in old_by_id)
    closed = tuple(c.candidate_id for c in old if c.candidate_id not in new_by_id)
    updated = tuple(
        c.candidate_id
        for c in new
        if c.candidate_id in old_by_id
        and _auto_signature(c) != _auto_signature(old_by_id[c.candidate_id])
    )
    presented_planned = tuple(
        c.candidate_id
        for c in new
        if Presentation(c.presentation) == Presentation.PRESENTED
    )
    held_new = tuple(
        cid
        for cid in added
        if Presentation(new_by_id[cid].presentation) == Presentation.HELD
    )
    held_promoted = tuple(
        c.candidate_id
        for c in new
        if c.candidate_id in old_by_id
        and Presentation(old_by_id[c.candidate_id].presentation) == Presentation.HELD
        and Presentation(c.presentation) == Presentation.PRESENTED
    )
    held_remaining = tuple(
        c.candidate_id
        for c in new
        if c.candidate_id in old_by_id
        and Presentation(c.presentation) == Presentation.HELD
    )
    held_closed = tuple(
        cid
        for cid in closed
        if Presentation(old_by_id[cid].presentation) == Presentation.HELD
    )
    check_only_count = sum(1 for c in new if Fitness(c.fitness) == Fitness.CHECK)
    registry_changes, auto_stop = _registry_changes(old_registry, new_registry)
    return Diff(
        added=added,
        updated=updated,
        closed=closed,
        presented_planned=presented_planned,
        held_new=held_new,
        held_promoted=held_promoted,
        held_remaining=held_remaining,
        held_closed=held_closed,
        check_only_count=check_only_count,
        registry_changes=registry_changes,
        auto_apply_stop=auto_stop,
    )


def mass_change_guard(diff: Diff, existing_count: int, cfg: GuardConfig) -> GuardResult:
    if existing_count <= 0:
        return GuardResult(status=GuardStatus.OK, reasons=())
    reasons: list[str] = []
    if len(diff.closed) / existing_count > cfg.max_close_ratio:
        reasons.append("掲載終了比率が上限を超えています")
    total_change = len(diff.added) + len(diff.updated) + len(diff.closed)
    if total_change / existing_count > cfg.max_total_change_ratio:
        reasons.append("変更合計比率が上限を超えています")
    if reasons:
        return GuardResult(status=GuardStatus.STOP, reasons=tuple(reasons))
    return GuardResult(status=GuardStatus.OK, reasons=())
