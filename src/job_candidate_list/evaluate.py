"""求人 1 件を単体で判定する公開入口（追補 A1）。

Sheets・ネットワークには一切触れない純粋関数のみで構成する。
検証用サンプルの単体検証と、パイプラインの判定段の共通実装の両方から使う。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from enum import StrEnum

from .config import AppConfig
from .fitness import FitnessResult, assign_fitness
from .judge_duty import DutyResult, judge_main_duty
from .judge_english import EnglishResult, judge_english
from .judge_hard import ExclusionHit, judge_hard_exclusions
from .judge_location import LocationResult, judge_location
from .judge_remote import RemoteResult, normalize_remote_frequency
from .models import (
    DutyOutcome,
    EnglishOutcome,
    Fitness,
    LocationWhitelist,
    RawPosting,
    SourceType,
)

SAMPLE_SOURCE_ID = "検証用サンプル"

_REQUIRED_HEADERS = ("会社名", "ポジション名", "雇用形態", "情報源URL", "勤務地")
_SALARY_RE = re.compile(r"(\d+(?:\.\d+)?)万円")


def _parse_salary_range_jpy(salary_raw: str) -> tuple[int | None, int | None]:
    """年収表記中の「万円」を円へ換算し、(最小, 最大) を返す。

    該当が1件だけなら最小＝最大、無ければ (None, None)。
    逆順表記でも小さい方を最小に正規化する（追補 A1.1）。
    アダプタ側（sources/）の年収抽出とは正規表現自体が異なる別系統
    であり、そちらは C-062 の対象外として統合しない。
    """
    amounts = [int(float(v) * 10000) for v in _SALARY_RE.findall(salary_raw)]
    if len(amounts) >= 2:
        return min(amounts), max(amounts)
    if len(amounts) == 1:
        return amounts[0], amounts[0]
    return None, None


class EvaluationKind(StrEnum):
    HARD = "hard"
    CHECK = "check"
    OK = "ok"


@dataclass(frozen=True)
class EvaluationResult:
    posting: RawPosting
    hard_excluded: bool
    exclusions: tuple[ExclusionHit, ...]
    duty: DutyResult
    english: EnglishResult
    remote: RemoteResult
    location: LocationResult
    fitness_result: FitnessResult | None
    rule_ids: tuple[str, ...]

    @property
    def kind(self) -> EvaluationKind:
        if self.hard_excluded:
            return EvaluationKind.HARD
        if (
            self.fitness_result is not None
            and Fitness(self.fitness_result.fitness) == Fitness.CHECK
        ):
            return EvaluationKind.CHECK
        return EvaluationKind.OK


def parse_sample_text(text: str, *, fetched_on: dt.date | None = None) -> RawPosting:
    headers: dict[str, str] = {}
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buffer).strip()
            current = line[3:].strip()
            buffer = []
            continue
        if current is not None:
            buffer.append(line)
            continue
        match = re.match(r"^([^:#][^:]*):\s*(.+)$", line)
        if match:
            headers[match.group(1).strip()] = match.group(2).strip()
    if current is not None:
        sections[current] = "\n".join(buffer).strip()

    for required in _REQUIRED_HEADERS:
        if required not in headers or not headers[required]:
            raise ValueError(f"必須ヘッダ項目が欠けています: {required}")

    salary_raw = headers.get("想定年収")
    salary_min: int | None = None
    salary_max: int | None = None
    if salary_raw:
        salary_min, salary_max = _parse_salary_range_jpy(salary_raw)

    remote_raw: str | None = headers.get("リモート頻度（原文）")
    if remote_raw is not None and remote_raw.startswith("記載なし"):
        remote_raw = None

    return RawPosting(
        source_id=SAMPLE_SOURCE_ID,
        source_type=SourceType(headers.get("情報源種別", "公開求人")),
        raw_url=headers["情報源URL"],
        company_raw=headers["会社名"],
        position_raw=headers["ポジション名"],
        employment_raw=headers["雇用形態"],
        salary_min_jpy=salary_min,
        salary_max_jpy=salary_max,
        salary_raw=salary_raw,
        location_raw=headers["勤務地"],
        remote_raw=remote_raw,
        sections=sections,
        company_overview_raw=sections.get("会社概要"),
        fetched_on=fetched_on if fetched_on is not None else dt.date.today(),
    )


def evaluate_posting(
    posting: RawPosting, cfg: AppConfig, whitelist: LocationWhitelist
) -> EvaluationResult:
    remote = normalize_remote_frequency(posting.remote_raw, cfg.remote)
    location = judge_location(posting, remote, whitelist, cfg.location)
    duty = judge_main_duty(posting.sections, cfg.duty)
    english = judge_english(posting.sections, cfg.english)
    hard_hits = judge_hard_exclusions(posting, cfg.hard)

    exclusions: list[ExclusionHit] = list(hard_hits)
    if duty.outcome == DutyOutcome.IMPL:
        exclusions.append(
            ExclusionHit(
                rule_id="JOB-IMPL-HARD",
                reason="／".join(duty.reasons) or "主務が実装等のため除外",
                config_ref="duty.impl_keywords",
            )
        )
    if english.outcome == EnglishOutcome.REQUIRED:
        exclusions.append(
            ExclusionHit(
                rule_id="ENG-HARD",
                reason="／".join(english.reasons) or "英語必須のため除外",
                config_ref="english.required_phrases",
            )
        )
    hard_excluded = len(exclusions) > 0

    fitness_result: FitnessResult | None = None
    if not hard_excluded:
        fitness_result = assign_fitness(
            posting, duty, location, remote, english, cfg.fitness
        )

    ordered: list[str] = []
    sources: list = [
        list(duty.rule_ids),
        list(english.rule_ids),
        [hit.rule_id for hit in hard_hits],
        list(remote.rule_ids),
        list(location.rule_ids),
    ]
    if fitness_result is not None:
        fitness_ids: list[str] = []
        for reason in (*fitness_result.fit_reasons, *fitness_result.check_reasons):
            fitness_ids.extend(reason.rule_ids)
        sources.append(fitness_ids)
    for group in sources:
        for rule_id in group:
            if rule_id not in ordered:
                ordered.append(rule_id)

    return EvaluationResult(
        posting=posting,
        hard_excluded=hard_excluded,
        exclusions=tuple(exclusions),
        duty=duty,
        english=english,
        remote=remote,
        location=location,
        fitness_result=fitness_result,
        rule_ids=tuple(ordered),
    )


def evaluate_sample_text(
    text: str,
    cfg: AppConfig,
    whitelist: LocationWhitelist,
    *,
    fetched_on: dt.date | None = None,
) -> EvaluationResult:
    return evaluate_posting(
        parse_sample_text(text, fetched_on=fetched_on), cfg, whitelist
    )
