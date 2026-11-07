"""主務判定（上流／実装／不明／混在）（§3.5／§6.1）。

根拠は設定 ``duty.target_sections`` のセクションのみ。主担当の明示が
ある場合は、その記述（主担当マーカーを含む文節）で言及されている側の
業務を優先する。肩書きキーワードは単独では outcome を決定しない。
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import DutyConfig
from .models import DutyOutcome
from .parsing import split_clauses


@dataclass(frozen=True)
class DutyResult:
    outcome: DutyOutcome
    rule_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    matched_upstream: tuple[str, ...]
    matched_impl: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, DutyOutcome):
            object.__setattr__(self, "outcome", DutyOutcome(self.outcome))
        for name in ("rule_ids", "reasons", "matched_upstream", "matched_impl"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


def judge_main_duty(sections: dict[str, str], cfg: DutyConfig) -> DutyResult:
    target_text = "\n".join(
        sections[name] for name in cfg.target_sections if name in sections
    )
    matched_upstream = tuple(kw for kw in cfg.upstream_keywords if kw in target_text)
    matched_impl = tuple(kw for kw in cfg.impl_keywords if kw in target_text)

    # 主担当の明示: マーカーを含む文節で言及された側を優先する。
    primary_upstream: list[str] = []
    primary_impl: list[str] = []
    if cfg.primary_markers:
        for clause in split_clauses(target_text):
            if any(marker in clause for marker in cfg.primary_markers):
                primary_upstream.extend(
                    kw for kw in cfg.upstream_keywords if kw in clause
                )
                primary_impl.extend(kw for kw in cfg.impl_keywords if kw in clause)
    if primary_upstream and not primary_impl:
        return DutyResult(
            outcome=DutyOutcome.UPSTREAM,
            rule_ids=("JOB-UPSTREAM-OK",),
            reasons=("主担当の明示により上流業務が主務と判定できる",),
            matched_upstream=matched_upstream,
            matched_impl=matched_impl,
        )
    if primary_impl and not primary_upstream:
        return DutyResult(
            outcome=DutyOutcome.IMPL,
            rule_ids=("JOB-IMPL-HARD",),
            reasons=("主担当の明示により実装等の業務が主務と判定できる",),
            matched_upstream=matched_upstream,
            matched_impl=matched_impl,
        )

    if matched_upstream and matched_impl:
        return DutyResult(
            outcome=DutyOutcome.MIXED,
            rule_ids=("JOB-MIXED",),
            reasons=("上流業務と実装等の業務の両方が主務候補として読み取れる",),
            matched_upstream=matched_upstream,
            matched_impl=matched_impl,
        )
    if matched_upstream:
        return DutyResult(
            outcome=DutyOutcome.UPSTREAM,
            rule_ids=("JOB-UPSTREAM-OK",),
            reasons=("上流業務のキーワードのみが主務として読み取れる",),
            matched_upstream=matched_upstream,
            matched_impl=matched_impl,
        )
    if matched_impl:
        return DutyResult(
            outcome=DutyOutcome.IMPL,
            rule_ids=("JOB-IMPL-HARD",),
            reasons=("実装等の業務のキーワードのみが主務として読み取れる",),
            matched_upstream=matched_upstream,
            matched_impl=matched_impl,
        )
    if "設計" in target_text:
        return DutyResult(
            outcome=DutyOutcome.UNKNOWN,
            rule_ids=("JOB-UNKNOWN", "JOB-DESIGN-ONLY"),
            reasons=("「設計」のみの記載で主務を判別できない",),
            matched_upstream=matched_upstream,
            matched_impl=matched_impl,
        )
    return DutyResult(
        outcome=DutyOutcome.UNKNOWN,
        rule_ids=("JOB-UNKNOWN",),
        reasons=("主務を読み取れるキーワードが対象セクションに無い",),
        matched_upstream=matched_upstream,
        matched_impl=matched_impl,
    )
