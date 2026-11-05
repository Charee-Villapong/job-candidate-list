"""情報源レジストリの読取・スキーマ検証・フェイルクローズ判定（§3.10／§8）。

レジストリの正は Sheets の「情報源レジストリ」タブであり、実装側の
設定ファイルに判定値を置かない。閉じた値の範囲外は例外にせず保持し、
``validate_registry`` が当該情報源のみのフェイルクローズとして返す。
全体停止はヘッダ不一致の ``RegistrySchemaError`` のみ。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from .errors import RegistrySchemaError

OPENWORK_SOURCE_ID = "OpenWork"

REGISTRY_HEADER: tuple[str, ...] = (
    "情報源名",
    "URLパターン",
    "ログイン要否",
    "資格情報の有無",
    "実測結果",
    "自動取得可否",
    "リスク評価結果と判定根拠",
    "費用条件確認",
    "自動応募可否",
    "送信確定方法",
    "対象化の最終判断",
    "備考",
)

LOGIN_VALUES = ("不要", "必須")
CREDENTIAL_VALUES = ("Secure Vault保存済み", "未保存", "不要（ログイン不要のため）")
FETCH_VALUES = ("可", "条件付き可（手動・低頻度の範囲のみ）", "不可", "未確認")
COST_VALUES = ("確認済み（無料または既存契約範囲内）", "有料条件あり", "未確認")
AUTO_APPLY_VALUES = ("可", "不可")
SEND_METHOD_VALUES = (
    "完了画面の文言",
    "確認メールの到着",
    "マイページの応募履歴表示",
    "対象外（企業評価の補助情報源）",
    "対象外（自動応募は第3段階で確定）",
)
CONFIRMED_SEND_METHODS = (
    "完了画面の文言",
    "確認メールの到着",
    "マイページの応募履歴表示",
)
DECISION_VALUES = ("対象", "対象（手動収集のみ）", "対象外", "対象外（実測待ち）")
TARGET_DECISIONS = ("対象", "対象（手動収集のみ）")
MEASURED_RESULTS = ("成功", "失敗", "未実施", "対象外（ログイン不要）")
MEASURED_METHODS = (
    "公開ページ取得",
    "公式公開API",
    "Secure Vault経由",
    "Secure Vault経由（予定含む）",
)
RISK_LEVELS = ("低", "中", "高", "未評価")


@dataclass(frozen=True)
class MeasuredResult:
    result: str
    method: str
    checked_on: dt.date | None


@dataclass(frozen=True)
class RiskAssessment:
    level: str
    basis: str
    checked_on: dt.date | None


@dataclass(frozen=True)
class SourceEntry:
    source_id: str
    url_pattern: str
    login_requirement: str
    credential_status: str
    measured_text: str
    measured: MeasuredResult | None
    fetch_availability: str
    risk_text: str
    risk: RiskAssessment | None
    cost_condition: str
    auto_apply: str
    send_method: str
    final_decision: str
    note: str
    row_index: int
    is_duplicate_url: bool = False


@dataclass(frozen=True)
class Registry:
    header: tuple[str, ...]
    sources: tuple[SourceEntry, ...]

    def find(self, source_id: str) -> SourceEntry | None:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        return None


class IssueKind(StrEnum):
    SCHEMA = "schema"
    SOURCE_BLOCKED = "source_blocked"
    MAJOR_CHANGE = "major_change"


@dataclass(frozen=True)
class RegistryIssue:
    source_id: str | None
    kind: IssueKind
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, IssueKind):
            object.__setattr__(self, "kind", IssueKind(self.kind))


def _parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        return None


def _parse_measured(text: str) -> MeasuredResult | None:
    fields: dict[str, str] = {}
    for part in text.split(";"):
        if "=" not in part:
            return None
        key, value = part.split("=", 1)
        fields[key.strip()] = value.strip()
    if not {"結果", "取得方法", "確認日"} <= set(fields):
        return None
    checked = _parse_date(fields["確認日"])
    if checked is None:
        return None
    return MeasuredResult(
        result=fields["結果"], method=fields["取得方法"], checked_on=checked
    )


def _parse_risk(text: str) -> RiskAssessment | None:
    match = re.match(
        r"リスク=([^;]+);\s*根拠=(.*);\s*確認日=(\d{4}-\d{2}-\d{2})\s*$", text
    )
    if not match:
        return None
    checked = _parse_date(match.group(3))
    if checked is None:
        return None
    return RiskAssessment(
        level=match.group(1).strip(), basis=match.group(2).strip(), checked_on=checked
    )


def parse_registry(rows: Sequence[Sequence[str]]) -> Registry:
    if not rows:
        raise RegistrySchemaError("レジストリが空です（ヘッダ行がありません）")
    header = tuple(str(c) for c in rows[0])
    if header != REGISTRY_HEADER:
        raise RegistrySchemaError("レジストリの列構成がスキーマと一致しません")
    url_counts: dict[str, int] = {}
    raw_rows: list[list[str]] = []
    for raw in rows[1:]:
        cells = [str(c) for c in raw]
        cells += [""] * (len(REGISTRY_HEADER) - len(cells))
        raw_rows.append(cells[: len(REGISTRY_HEADER)])
        url_counts[cells[1]] = url_counts.get(cells[1], 0) + 1
    sources = []
    for index, cells in enumerate(raw_rows):
        sources.append(
            SourceEntry(
                source_id=cells[0],
                url_pattern=cells[1],
                login_requirement=cells[2],
                credential_status=cells[3],
                measured_text=cells[4],
                measured=_parse_measured(cells[4]),
                fetch_availability=cells[5],
                risk_text=cells[6],
                risk=_parse_risk(cells[6]),
                cost_condition=cells[7],
                auto_apply=cells[8],
                send_method=cells[9],
                final_decision=cells[10],
                note=cells[11],
                row_index=index,
                is_duplicate_url=url_counts.get(cells[1], 0) > 1 and cells[1] != "",
            )
        )
    return Registry(header=header, sources=tuple(sources))


def _closed_value_issues(source: SourceEntry) -> list[RegistryIssue]:
    issues: list[RegistryIssue] = []

    def add(detail: str) -> None:
        issues.append(
            RegistryIssue(
                source_id=source.source_id,
                kind=IssueKind.SOURCE_BLOCKED,
                detail=detail,
            )
        )

    if source.login_requirement not in LOGIN_VALUES:
        add("ログイン要否が閉じた値の範囲外です")
    if source.credential_status not in CREDENTIAL_VALUES:
        add("資格情報の有無が閉じた値の範囲外です")
    if source.fetch_availability not in FETCH_VALUES:
        add("自動取得可否が閉じた値の範囲外です")
    if source.cost_condition not in COST_VALUES:
        add("費用条件確認が閉じた値の範囲外です")
    if source.auto_apply not in AUTO_APPLY_VALUES:
        add("自動応募可否が閉じた値の範囲外です")
    if source.send_method not in SEND_METHOD_VALUES:
        add("送信確定方法の値が閉じた値の範囲外です")
    if source.final_decision not in DECISION_VALUES:
        add("対象化の最終判断が閉じた値の範囲外です")
    if source.measured is None:
        add("実測結果の形式が不正です")
    elif source.measured.result not in MEASURED_RESULTS:
        add("実測結果の結果区分が閉じた値の範囲外です")
    elif source.measured.method not in MEASURED_METHODS:
        add("実測結果の取得方法が閉じた値の範囲外です")
    if source.risk is None:
        add("リスク評価の形式が不正です")
    elif source.risk.level not in RISK_LEVELS:
        add("リスク区分が閉じた値の範囲外です")
    return issues


def validate_registry(reg: Registry) -> list[RegistryIssue]:
    issues: list[RegistryIssue] = []
    url_counts: dict[str, int] = {}
    for source in reg.sources:
        if source.url_pattern:
            url_counts[source.url_pattern] = url_counts.get(source.url_pattern, 0) + 1
    for source in reg.sources:
        issues.extend(_closed_value_issues(source))
        if source.url_pattern and url_counts.get(source.url_pattern, 0) > 1:
            issues.append(
                RegistryIssue(
                    source_id=source.source_id,
                    kind=IssueKind.SOURCE_BLOCKED,
                    detail="URLパターンが重複しています",
                )
            )
        if (
            source.auto_apply == "可"
            and source.send_method not in CONFIRMED_SEND_METHODS
        ):
            issues.append(
                RegistryIssue(
                    source_id=source.source_id,
                    kind=IssueKind.SOURCE_BLOCKED,
                    detail="自動応募可否が可だが送信確定方法が未確定です",
                )
            )
    return issues


def is_collectable(source: SourceEntry) -> bool:
    """フェイルクローズ判定（§8.4）。対象として収集できる場合のみ True。"""
    if source.final_decision not in TARGET_DECISIONS:
        return False
    if _closed_value_issues(source):
        return False
    if source.is_duplicate_url:
        return False
    if source.auto_apply == "可" and source.send_method not in CONFIRMED_SEND_METHODS:
        return False
    if source.login_requirement == "必須":
        if source.credential_status != "Secure Vault保存済み":
            return False
        if source.measured is None or source.measured.result != "成功":
            return False
    if source.cost_condition != "確認済み（無料または既存契約範囲内）":
        return False
    if source.fetch_availability in ("不可", "未確認"):
        return False
    if source.fetch_availability == "条件付き可（手動・低頻度の範囲のみ）":
        if source.final_decision != "対象（手動収集のみ）":
            return False
    if source.risk is None or source.risk.level in ("高", "未評価"):
        return False
    return True


def openwork_gate(reg: Registry) -> bool:
    entry = reg.find(OPENWORK_SOURCE_ID)
    if entry is None:
        return False
    if entry.fetch_availability not in (
        "可",
        "条件付き可（手動・低頻度の範囲のみ）",
    ):
        return False
    if entry.final_decision not in TARGET_DECISIONS:
        return False
    blocked = [
        issue
        for issue in validate_registry(reg)
        if issue.source_id == OPENWORK_SOURCE_ID
        and issue.kind == IssueKind.SOURCE_BLOCKED
    ]
    if blocked:
        return False
    return is_collectable(entry)
