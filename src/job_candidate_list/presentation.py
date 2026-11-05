"""提示上限 N・控え管理、提示順ソート、繰り上げ再確認（§3.12、追補 A8）。"""

from __future__ import annotations

from dataclasses import replace
from enum import StrEnum
from typing import Sequence

from .fitness import FitnessResult
from .models import CandidateRecord, CandidateStatus, Fitness, Presentation

_FITNESS_RANK = {Fitness.HIGH: 0, Fitness.MEDIUM: 1, Fitness.CHECK: 2}


class HeldDecision(StrEnum):
    PROMOTE = "promote"
    KEEP_HELD = "keep_held"
    CLOSED = "closed"
    TO_CHECK = "to_check"


def presentation_sort_key(
    candidate: CandidateRecord,
) -> tuple[int, int, int, str]:
    if candidate.presentation_key is not None:
        key = tuple(candidate.presentation_key)
        return (int(key[0]), int(key[1]), int(key[2]), str(key[3]))
    rank = _FITNESS_RANK[Fitness(candidate.fitness)]
    third = (
        -candidate.confirmed_on.toordinal() if candidate.confirmed_on is not None else 0
    )
    return (rank, 0, third, candidate.candidate_id)


def split_presentation(
    candidates: Sequence[CandidateRecord], limit: int
) -> tuple[list[CandidateRecord], list[CandidateRecord]]:
    eligible = [
        c
        for c in candidates
        if Fitness(c.fitness) in (Fitness.HIGH, Fitness.MEDIUM)
        and CandidateStatus(c.status) != CandidateStatus.CLOSED
        and c.manual_judgment != "対象外"
        and Presentation(c.presentation) == Presentation.HELD
    ]
    ordered = sorted(eligible, key=presentation_sort_key)
    presented = [
        replace(c, presentation=Presentation.PRESENTED) for c in ordered[:limit]
    ]
    held = list(ordered[limit:])
    return presented, held


def recheck_held(
    candidate: CandidateRecord,
    latest,
    refitness: FitnessResult | None,
) -> HeldDecision:
    if latest is None:
        return HeldDecision.CLOSED
    if CandidateStatus(candidate.status) == CandidateStatus.CLOSED:
        return HeldDecision.CLOSED
    if refitness is None:
        return HeldDecision.TO_CHECK
    if candidate.manual_judgment == "対象外":
        return HeldDecision.KEEP_HELD
    if Fitness(refitness.fitness) == Fitness.CHECK:
        return HeldDecision.TO_CHECK
    return HeldDecision.PROMOTE
