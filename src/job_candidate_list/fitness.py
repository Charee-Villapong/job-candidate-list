"""適合度付け（高／中／要確認）と提示順スコア算出（§3.9／§6.6）。

決定規則は §3.9 の順序どおりに評価する。前提として、ハード除外
ヒット済みの候補は本関数に渡さない（pipeline が先に除外へ回す）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import FitnessConfig, HardConfig
from .judge_duty import DutyResult
from .judge_english import EnglishResult
from .judge_hard import (
    is_clear_business_form,
    matched_business_form_keywords,
)
from .judge_location import LocationResult
from .judge_remote import RemoteResult
from .models import (
    DutyOutcome,
    EnglishOutcome,
    Fitness,
    LocationDivision,
    RawPosting,
    Reason,
    RemoteDivision,
)
from .normalize import (
    candidate_id,
    normalize_company,
    normalize_position,
    normalize_url,
)

_FITNESS_RANK = {Fitness.HIGH: 0, Fitness.MEDIUM: 1, Fitness.CHECK: 2}


@dataclass(frozen=True)
class FitnessResult:
    fitness: Fitness
    fit_reasons: tuple[Reason, ...]
    check_reasons: tuple[Reason, ...]
    presentation_key: tuple[int, int, int, str]

    def __post_init__(self) -> None:
        if not isinstance(self.fitness, Fitness):
            object.__setattr__(self, "fitness", Fitness(self.fitness))
        object.__setattr__(self, "fit_reasons", tuple(self.fit_reasons))
        object.__setattr__(self, "check_reasons", tuple(self.check_reasons))
        object.__setattr__(self, "presentation_key", tuple(self.presentation_key))


def _is_bizform_ambiguous(posting: RawPosting) -> bool:
    """事業形態が曖昧か（弱い一致のみで断定できない状態）。

    判定キーワード群は設定由来が原則だが、適合度付けのシグネチャには
    HardConfig が含まれないため、一般化した弱キーワードで補助判定する。
    ハード除外側（judge_hard）が明確と判断した候補はここへ来ない。
    """
    text = posting.company_overview_raw or ""
    weak_words = ("常駐", "コンサルティング", "受託", "SI")
    if not any(word in text for word in weak_words):
        return False
    cfg = HardConfig(business_form_keywords=weak_words)
    matched = matched_business_form_keywords(posting, cfg)
    return not is_clear_business_form(matched)


def assign_fitness(
    posting: RawPosting,
    duty: DutyResult,
    location: LocationResult,
    remote: RemoteResult,
    english: EnglishResult,
    cfg: FitnessConfig,
) -> FitnessResult:
    fit_reasons: list[Reason] = []
    check_reasons: list[Reason] = []
    needs_check = False

    if duty.outcome == DutyOutcome.UPSTREAM:
        fit_reasons.append(
            Reason("主務が上流業務として確認できた", ("JOB-UPSTREAM-OK",))
        )
    # 1. 主務不明・混在
    if duty.outcome in (DutyOutcome.UNKNOWN, DutyOutcome.MIXED):
        needs_check = True
        check_reasons.append(
            Reason(
                "主務が不明または混在のため確認が必要",
                tuple(duty.rule_ids),
            )
        )
    # 2. 英語不明
    if english.outcome == EnglishOutcome.UNKNOWN:
        needs_check = True
        check_reasons.append(
            Reason("英語要件の必須性が読み取れない", tuple(english.rule_ids))
        )
    elif english.outcome == EnglishOutcome.NOT_REQUIRED:
        fit_reasons.append(
            Reason("英語は必須でないことが確認できた", tuple(english.rule_ids))
        )
    # 3. リモート不明（固定）
    if remote.division == RemoteDivision.UNKNOWN:
        needs_check = True
        check_reasons.append(Reason("リモート頻度が不明である", ("REMOTE-UNKNOWN",)))
    # 4. 勤務地不適合・判定不能
    if location.division in (
        LocationDivision.UNFIT,
        LocationDivision.UNKNOWN,
    ):
        needs_check = True
        check_reasons.append(
            Reason(
                "勤務地条件の確認が必要である",
                tuple(location.rule_ids),
            )
        )
    elif location.division == LocationDivision.FIT:
        fit_reasons.append(Reason("勤務地が対応表の範囲内である", ("LOC-OK",)))
    elif location.division == LocationDivision.FULLREMOTE_EXEMPT:
        fit_reasons.append(
            Reason(
                "フルリモートのため勤務地条件の対象外である",
                ("LOC-FULLREMOTE-EXEMPT",),
            )
        )
    # 5. 年収
    salary_known = posting.salary_max_jpy is not None
    if salary_known and posting.salary_max_jpy < cfg.salary_check_threshold_jpy:
        needs_check = True
        check_reasons.append(Reason("年収上限が目安を大幅に下回る", ("SAL-BELOW",)))
    elif salary_known:
        fit_reasons.append(Reason("年収レンジが目安と大きく乖離しない", ("SAL-OK",)))
    else:
        check_reasons.append(Reason("公開情報に年収の記載がない", ("SAL-UNKNOWN",)))
    # 6. 事業形態が曖昧
    if _is_bizform_ambiguous(posting):
        needs_check = True
        check_reasons.append(Reason("事業形態が判断できない", ("BIZFORM-UNKNOWN",)))

    # 機械的一致度（§6.6）。
    score = 0
    searchable = "\n".join(
        part
        for part in (
            posting.position_raw,
            posting.company_overview_raw,
            *posting.sections.values(),
        )
        if part
    )
    if cfg.target_domains and any(d in searchable for d in cfg.target_domains):
        score += cfg.domain_match_points
    if salary_known:
        target = cfg.salary_target_jpy
        if (
            target is None
            or (
                posting.salary_min_jpy is not None
                and posting.salary_min_jpy <= target <= posting.salary_max_jpy
            )
            or (target is not None and posting.salary_max_jpy >= target)
        ):
            score += cfg.salary_points
    if remote.division != RemoteDivision.UNKNOWN:
        score += cfg.condition_points
    if location.division == LocationDivision.FIT:
        score += cfg.location_points

    # 7. 高／中の付与は上流主務の場合のみ。
    if needs_check or duty.outcome != DutyOutcome.UPSTREAM:
        fitness = Fitness.CHECK
    elif score >= cfg.high_threshold:
        fitness = Fitness.HIGH
    else:
        fitness = Fitness.MEDIUM

    if not check_reasons:
        check_reasons.append(Reason("要確認に倒す条件は検出されなかった", ()))
    if not fit_reasons:
        fit_reasons.append(Reason("適合を裏付ける観点は検出されなかった", ()))

    cid = candidate_id(
        normalize_company(posting.company_raw),
        normalize_position(posting.position_raw),
        normalize_url(posting.raw_url),
    )
    presentation_key = (
        _FITNESS_RANK[fitness],
        -score,
        -posting.fetched_on.toordinal(),
        cid,
    )
    return FitnessResult(
        fitness=fitness,
        fit_reasons=tuple(fit_reasons),
        check_reasons=tuple(check_reasons),
        presentation_key=presentation_key,
    )
