"""データ構造定義（architecture.md §2、追補 A2.3／A3／A5）。

副作用なし。列挙型は全て ``enum.StrEnum`` とし、値は設計で確定した
文字列そのものを使う。文字列で渡された列挙値は ``__post_init__`` で
対応する列挙値へ寄せる（テスト・設定の両方から構築できるようにするため）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, NamedTuple

from .errors import ConfigError


class SourceType(StrEnum):
    PUBLIC = "公開求人"
    COMPANY_PAGE = "企業採用ページ"
    LOGIN_REQUIRED = "ログイン必須"
    SCOUT = "補助スカウト"


class LocationDivision(StrEnum):
    FIT = "勤務地適合"
    UNFIT = "勤務地不適合"
    UNKNOWN = "勤務地判定不能"
    FULLREMOTE_EXEMPT = "フルリモート対象外"


class RemoteDivision(StrEnum):
    FULL_REMOTE = "フルリモート（出社なし）"
    WEEKLY_1_OR_LESS = "出社週1日以下"
    WEEKLY_2_TO_3 = "出社週2〜3日"
    WEEKLY_4_OR_MORE = "出社週4日以上"
    FULL_OFFICE = "出社必須（フル出社）"
    UNKNOWN = "不明"


class Fitness(StrEnum):
    HIGH = "高"
    MEDIUM = "中"
    CHECK = "要確認"


class Presentation(StrEnum):
    PRESENTED = "提示済み"
    HELD = "控え"


class CandidateStatus(StrEnum):
    NEW = "新規"
    CONTINUING = "継続"
    UPDATED = "更新あり"
    CLOSED = "掲載終了"


class ApplyStatus(StrEnum):
    NOT_APPLIED = "未応募"
    SENDING = "送信処理中"
    APPLIED = "応募済み"
    FAILED = "応募失敗"
    UNKNOWN = "結果不明"
    MANUAL_REQUIRED = "手動応募が必要"


class DutyOutcome(StrEnum):
    UPSTREAM = "upstream"
    IMPL = "impl"
    UNKNOWN = "unknown"
    MIXED = "mixed"


class EnglishOutcome(StrEnum):
    REQUIRED = "required"
    NOT_REQUIRED = "not_required"
    UNKNOWN = "unknown"
    NO_MENTION = "no_mention"


# リンク表示テキストと勤務地不明表記の正本（C-065）。従来は models／
# pipeline／openwork／sheets_gateway の複数箇所に同じリテラルが散在し、
# gateway の欠落時既定値も別に直書きされていたため、ここへ一本化する。
JOB_PAGE_DISPLAY_TEXT = "求人ページ"
OPENWORK_DISPLAY_TEXT = "OpenWork"
UNKNOWN_LOCATION_TEXT = "不明"


class Reason(NamedTuple):
    text: str
    rule_ids: tuple[str, ...] = ()


class LinkCell(NamedTuple):
    display_text: str
    url: str


def _coerce(enum_cls, value):
    """文字列を列挙値へ寄せる。すでに列挙値ならそのまま返す。"""
    if isinstance(value, enum_cls):
        return value
    return enum_cls(value)


@dataclass(frozen=True)
class LocationEntry:
    prefecture: str
    municipality: str


@dataclass(frozen=True)
class LocationWhitelist:
    version: int
    municipalities: tuple[LocationEntry, ...]
    notes: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LocationWhitelist":
        if not isinstance(data, Mapping):
            raise ConfigError("勤務地対応表の形式が不正です: municipalities")
        unknown = set(data) - {"version", "municipalities", "notes"}
        if unknown:
            raise ConfigError(f"勤務地対応表に未知のキーがあります: {sorted(unknown)}")
        if "version" not in data:
            raise ConfigError("勤務地対応表に必須キーがありません: version")
        if "municipalities" not in data:
            raise ConfigError("勤務地対応表に必須キーがありません: municipalities")
        entries = []
        for item in data["municipalities"]:
            if not isinstance(item, Mapping):
                raise ConfigError("勤務地対応表の形式が不正です: municipalities")
            entry_unknown = set(item) - {"prefecture", "municipality"}
            if entry_unknown:
                raise ConfigError(
                    f"勤務地対応表に未知のキーがあります: {sorted(entry_unknown)}"
                )
            if "prefecture" not in item or "municipality" not in item:
                raise ConfigError(
                    "勤務地対応表に必須キーがありません: prefecture/municipality"
                )
            entries.append(
                LocationEntry(
                    prefecture=str(item["prefecture"]),
                    municipality=str(item["municipality"]),
                )
            )
        return cls(
            version=int(data["version"]),
            municipalities=tuple(entries),
            notes=str(data.get("notes") or ""),
        )

    def contains(self, municipality: str) -> bool:
        return any(e.municipality == municipality for e in self.municipalities)


@dataclass(frozen=True)
class RawPosting:
    source_id: str
    source_type: SourceType
    raw_url: str
    company_raw: str
    position_raw: str
    employment_raw: str | None = None
    salary_min_jpy: int | None = None
    salary_max_jpy: int | None = None
    salary_raw: str | None = None
    location_raw: str | None = None
    remote_raw: str | None = None
    sections: dict[str, str] = field(default_factory=dict)
    company_overview_raw: str | None = None
    fetched_on: dt.date = field(default_factory=dt.date.today)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_type", _coerce(SourceType, self.source_type))


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    company: str = ""
    position: str = ""
    company_norm: str = ""
    position_norm: str = ""
    employment_type: str = ""
    salary_range_text: str | None = None
    location_text: str = UNKNOWN_LOCATION_TEXT
    location_result: LocationDivision = LocationDivision.UNKNOWN
    remote_division: RemoteDivision = RemoteDivision.UNKNOWN
    fitness: Fitness = Fitness.CHECK
    fit_reasons: tuple[Reason, ...] = ()
    check_reasons: tuple[Reason, ...] = ()
    source_type: SourceType = SourceType.PUBLIC
    source_url: LinkCell = LinkCell(JOB_PAGE_DISPLAY_TEXT, "")
    openwork_score: float | None = None
    openwork_url: LinkCell | None = None
    openwork_fetched_on: dt.date | None = None
    presentation: Presentation = Presentation.HELD
    presentation_key: tuple[int, int, int, str] | None = None
    manual_judgment: str | None = None
    confirmed_on: dt.date | None = None
    status: CandidateStatus = CandidateStatus.NEW
    apply_status: ApplyStatus = ApplyStatus.NOT_APPLIED
    applied_at: dt.datetime | None = None
    apply_result: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "location_result", _coerce(LocationDivision, self.location_result)
        )
        object.__setattr__(
            self, "remote_division", _coerce(RemoteDivision, self.remote_division)
        )
        object.__setattr__(self, "fitness", _coerce(Fitness, self.fitness))
        object.__setattr__(self, "source_type", _coerce(SourceType, self.source_type))
        object.__setattr__(
            self, "presentation", _coerce(Presentation, self.presentation)
        )
        object.__setattr__(self, "status", _coerce(CandidateStatus, self.status))
        object.__setattr__(
            self, "apply_status", _coerce(ApplyStatus, self.apply_status)
        )
        object.__setattr__(
            self, "fit_reasons", tuple(_coerce_reason(r) for r in self.fit_reasons)
        )
        object.__setattr__(
            self,
            "check_reasons",
            tuple(_coerce_reason(r) for r in self.check_reasons),
        )
        if not isinstance(self.source_url, LinkCell):
            object.__setattr__(self, "source_url", LinkCell(*self.source_url))
        if self.openwork_url is not None and not isinstance(
            self.openwork_url, LinkCell
        ):
            object.__setattr__(self, "openwork_url", LinkCell(*self.openwork_url))


def _coerce_reason(value) -> Reason:
    if isinstance(value, Reason):
        return Reason(value.text, tuple(value.rule_ids))
    text, rule_ids = value
    return Reason(text, tuple(rule_ids))


@dataclass(frozen=True)
class ExclusionRow:
    exclusion_id: str
    fetched_on: dt.date
    company: str
    position: str
    employment_type: str | None
    source_type: SourceType
    source_url: LinkCell
    reason: str
    rule_ids: tuple[str, ...]
    config_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_type", _coerce(SourceType, self.source_type))
        object.__setattr__(self, "rule_ids", tuple(self.rule_ids))
        object.__setattr__(self, "config_refs", tuple(self.config_refs))
