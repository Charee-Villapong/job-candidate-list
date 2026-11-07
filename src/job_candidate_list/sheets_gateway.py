"""Google Sheets 入出力（§3.11、追補 A5）。

書き込むのは自動列のみとし、列名のホワイトリストで物理的に制限する。
URL 列は表示テキスト付きリンク（HYPERLINK 形式）で記録し、生 URL を
セル文字列として書かない。

本モジュールの構成（責務ごとに節を分ける）:
- 行⇔レコード変換（`_record_to_row`／`_row_to_record`／
  `_exclusion_to_row` とその補助関数群。I/O を持たない純粋関数）
- `GoogleSheetsGateway`: SheetsGateway Protocol の具象実装。
  backend への委譲と変換の統括のみを行う
- `GoogleApiSheetsBackend`: 実 Sheets アクセス用の低レベル backend

backend の経路は CLI 一本に確定する（C-137。本人の決定により
サービス経路を削除した）:
- read_rows: CLI 経路で表示形式の行を返す
- read_links: CLI 経路で FORMULA 形式からリンクを復元する
- write_rows: CLI 経路で書く
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Mapping, Protocol, Sequence

from .config import SheetNamesConfig
from .models import (
    JOB_PAGE_DISPLAY_TEXT,
    OPENWORK_DISPLAY_TEXT,
    UNKNOWN_LOCATION_TEXT,
    ApplyStatus,
    CandidateRecord,
    CandidateStatus,
    ExclusionRow,
    Fitness,
    LinkCell,
    LocationDivision,
    Presentation,
    Reason,
    RemoteDivision,
    SourceType,
)
from .parsing import parse_iso_date

__all__ = [
    "AUTO_COLUMNS_CANDIDATE",
    "MANUAL_COLUMNS_CANDIDATE",
    "GoogleApiSheetsBackend",
    "GoogleSheetsGateway",
    "LinkCell",
    "SheetsBackend",
    "SheetsGateway",
]

AUTO_COLUMNS_CANDIDATE: tuple[str, ...] = (
    "候補ID",
    "会社名",
    "ポジション名",
    "雇用形態",
    "想定年収レンジ",
    "勤務地",
    "勤務地判定結果",
    "リモート頻度",
    "適合度",
    "適合理由",
    "要確認理由",
    "情報源種別",
    "情報源URL",
    "OpenWork点数",
    "OpenWork URL",
    "OpenWork点数取得日",
    "提示区分",
    "掲載確認日",
    "状態",
    "応募状態",
    "応募日時",
    "応募結果",
)

MANUAL_COLUMNS_CANDIDATE: tuple[str, ...] = ("本人の判断", "本人のメモ")

# シート名の既定値は設定側 SheetNamesConfig の既定値に一本化し、
# ここではそれを引いて dict 化するだけにする。
_DEFAULT_SHEET_NAMES = {
    "candidates": SheetNamesConfig().candidates,
    "registry": SheetNamesConfig().registry,
    "exclusions": SheetNamesConfig().exclusions,
}

_SOURCE_TYPE_DISPLAY = {
    SourceType.PUBLIC: "公開求人",
    SourceType.COMPANY_PAGE: "企業採用ページ",
    SourceType.LOGIN_REQUIRED: "ログイン必須（Secure Vault保存済み）",
    SourceType.SCOUT: "補助：届いたスカウト",
}
_SOURCE_TYPE_FROM_DISPLAY = {v: k for k, v in _SOURCE_TYPE_DISPLAY.items()}
_SOURCE_TYPE_FROM_DISPLAY["ログイン必須"] = SourceType.LOGIN_REQUIRED
_SOURCE_TYPE_FROM_DISPLAY["補助スカウト"] = SourceType.SCOUT

_RULE_ID_RE = re.compile(r"[A-Z][A-Z]+-[A-Z-]+")


# ---------------------------------------------------------------------------
# プロトコル（I/O 境界）
# ---------------------------------------------------------------------------
class SheetsBackend(Protocol):
    def read_rows(self, tab_name: str) -> list[list[str]]: ...

    def read_links(self, tab_name: str) -> dict[tuple[int, int], str]: ...

    def write_rows(self, tab_name: str, rows: Sequence[Sequence[str]]) -> None: ...


class SheetsGateway(Protocol):
    def read_tab(self, tab_name: str) -> list[list[str]]: ...

    def read_candidates(self) -> list[CandidateRecord]: ...

    def upsert_candidates(self, records: Sequence[CandidateRecord]) -> None: ...

    def append_exclusions(self, rows: Sequence[ExclusionRow]) -> None: ...

    def read_registry_rows(self) -> list[list[str]]: ...


# ---------------------------------------------------------------------------
# 行⇔レコード変換（I/O を持たない純粋関数群）
# ---------------------------------------------------------------------------
def _source_type_display(source_type: SourceType) -> str:
    """情報源種別を表示文字列へ変換する（対応が無ければ値そのもの）。"""
    value = SourceType(source_type)
    return _SOURCE_TYPE_DISPLAY.get(value, str(source_type))


def _format_reasons(reasons: Sequence[Reason]) -> str:
    parts = []
    for reason in reasons:
        if reason.rule_ids:
            parts.append(f"{reason.text}（{'、'.join(reason.rule_ids)}）")
        else:
            parts.append(reason.text)
    return "／".join(parts)


def _parse_reasons(text: str) -> tuple[Reason, ...]:
    if not text:
        return ()
    return (Reason(text=text, rule_ids=tuple(_RULE_ID_RE.findall(text))),)


def _hyperlink(link: LinkCell | None) -> str:
    if link is None or not link.url:
        return ""
    url = link.url.replace('"', '""')
    display = link.display_text.replace('"', '""')
    return f'=HYPERLINK("{url}", "{display}")'


def _parse_hyperlink_url(text: str) -> str | None:
    """=HYPERLINK("url", "text") 数式から実 URL を取り出す。

    数式内の文字列リテラルでは引用符が "" と二重化されて
    エスケープされるため、それを " に戻しながら読み取る。
    HYPERLINK 数式でないセルについては None を返す。
    """
    stripped = text.strip()
    match = re.match(r'=HYPERLINK\s*\(\s*"', stripped, re.IGNORECASE)
    if match is None:
        return None
    index = match.end()
    chars: list[str] = []
    while index < len(stripped):
        char = stripped[index]
        if char == '"':
            if index + 1 < len(stripped) and stripped[index + 1] == '"':
                chars.append('"')
                index += 2
                continue
            return "".join(chars)
        chars.append(char)
        index += 1
    return None


def _record_to_row(record: CandidateRecord) -> list[str]:
    return [
        record.candidate_id,
        record.company,
        record.position,
        record.employment_type,
        record.salary_range_text or "",
        record.location_text,
        str(LocationDivision(record.location_result)),
        str(RemoteDivision(record.remote_division)),
        str(record.fitness),
        _format_reasons(record.fit_reasons),
        _format_reasons(record.check_reasons),
        _source_type_display(record.source_type),
        _hyperlink(record.source_url),
        "" if record.openwork_score is None else str(record.openwork_score),
        _hyperlink(record.openwork_url),
        (
            ""
            if record.openwork_fetched_on is None
            else record.openwork_fetched_on.isoformat()
        ),
        str(record.presentation),
        "" if record.confirmed_on is None else record.confirmed_on.isoformat(),
        str(record.status),
        str(record.apply_status),
        "" if record.applied_at is None else record.applied_at.isoformat(),
        record.apply_result or "",
    ]


def _row_to_record(
    row: Sequence[str], links: Mapping[tuple[int, int], str], row_index: int
) -> CandidateRecord:
    cells = list(row) + [""] * (len(AUTO_COLUMNS_CANDIDATE) - len(row))

    def cell(column: str) -> str:
        return cells[AUTO_COLUMNS_CANDIDATE.index(column)]

    def link_cell(column: str, display: str) -> LinkCell:
        idx = AUTO_COLUMNS_CANDIDATE.index(column)
        real_url = links.get((row_index, idx))
        if real_url:
            return LinkCell(display, real_url)
        text = cells[idx]
        if text.startswith("http"):
            return LinkCell(display, text)
        raise ValueError(f"URL 列の実 URL が読み取れません: {column}")

    score_text = cell("OpenWork点数")
    ow_url_text = cell("OpenWork URL")
    return CandidateRecord(
        candidate_id=cell("候補ID"),
        company=cell("会社名"),
        position=cell("ポジション名"),
        employment_type=cell("雇用形態"),
        salary_range_text=cell("想定年収レンジ") or None,
        location_text=cell("勤務地") or UNKNOWN_LOCATION_TEXT,
        location_result=cell("勤務地判定結果") or str(LocationDivision.UNKNOWN),
        remote_division=cell("リモート頻度") or str(RemoteDivision.UNKNOWN),
        fitness=cell("適合度") or str(Fitness.CHECK),
        fit_reasons=_parse_reasons(cell("適合理由")),
        check_reasons=_parse_reasons(cell("要確認理由")),
        source_type=_SOURCE_TYPE_FROM_DISPLAY.get(
            cell("情報源種別"), SourceType.PUBLIC
        ),
        source_url=link_cell("情報源URL", JOB_PAGE_DISPLAY_TEXT),
        openwork_score=float(score_text) if score_text else None,
        openwork_url=(
            link_cell("OpenWork URL", OPENWORK_DISPLAY_TEXT) if ow_url_text else None
        ),
        openwork_fetched_on=parse_iso_date(cell("OpenWork点数取得日")),
        presentation=cell("提示区分") or str(Presentation.HELD),
        confirmed_on=parse_iso_date(cell("掲載確認日")),
        status=cell("状態") or str(CandidateStatus.NEW),
        apply_status=cell("応募状態") or str(ApplyStatus.NOT_APPLIED),
        apply_result=cell("応募結果") or None,
    )


def _exclusion_to_row(row: ExclusionRow) -> list[str]:
    """除外ログ行（§10.3）をセル群へ変換する。"""
    return [
        row.exclusion_id,
        row.fetched_on.isoformat(),
        row.company,
        row.position,
        row.employment_type or "",
        _source_type_display(row.source_type),
        _hyperlink(row.source_url),
        row.reason,
        ",".join(row.rule_ids),
        ",".join(row.config_refs),
    ]


# ---------------------------------------------------------------------------
# Gateway（SheetsGateway Protocol の具象実装。変換と backend 委譲の統括）
# ---------------------------------------------------------------------------
class GoogleSheetsGateway:
    """SheetsGateway Protocol の具象実装（追補 A5）。"""

    def __init__(
        self,
        backend: SheetsBackend,
        sheet_names: Mapping[str, str] | None = None,
    ) -> None:
        self._backend = backend
        self._sheet_names = dict(_DEFAULT_SHEET_NAMES)
        if sheet_names:
            self._sheet_names.update(sheet_names)

    def read_tab(self, tab_name: str) -> list[list[str]]:
        return self._backend.read_rows(tab_name)

    def read_registry_rows(self) -> list[list[str]]:
        return self._backend.read_rows(self._sheet_names["registry"])

    def read_candidates(self) -> list[CandidateRecord]:
        tab = self._sheet_names["candidates"]
        rows = self._backend.read_rows(tab)
        links = self._backend.read_links(tab)
        records: list[CandidateRecord] = []
        for index, row in enumerate(rows):
            if index == 0:
                continue  # ヘッダ行
            if not row or not str(row[0]).strip():
                continue
            records.append(_row_to_record(row, links, index))
        return records

    def upsert_candidates(self, records: Sequence[CandidateRecord]) -> None:
        rows = [_record_to_row(record) for record in records]
        self._backend.write_rows(self._sheet_names["candidates"], rows)

    def append_exclusions(self, rows: Sequence[ExclusionRow]) -> None:
        out = [_exclusion_to_row(row) for row in rows]
        self._backend.write_rows(self._sheet_names["exclusions"], out)


# ---------------------------------------------------------------------------
# Backend（実 Sheets アクセス用の低レベル入出力）
# ---------------------------------------------------------------------------
def _extract_values(data: object) -> list[list[str]]:
    """CLI 応答の ``values`` を文字列の行列として取り出す。"""
    values = data.get("values", []) if isinstance(data, dict) else []
    return [[str(c) for c in row] for row in values]


class GoogleApiSheetsBackend:
    """実 Sheets アクセス用の backend。

    この環境の Sheets アクセス経路である
    ``/opt/hatch/bin/hatch_gws_cli sheets ...`` を呼び出す
    （C-137 で CLI 一本に確定。サービス経路は削除した）。

    - read_rows: CLI 経路で表示形式の行を返す
    - read_links: CLI 経路で FORMULA 形式からリンクを復元する
    - write_rows: CLI 経路で書く
    """

    CLI_PATH = "/opt/hatch/bin/hatch_gws_cli"

    def __init__(self, spreadsheet_id: str) -> None:
        self._spreadsheet_id = spreadsheet_id

    def _run_cli(self, args: list[str]) -> dict:
        completed = subprocess.run(
            [self.CLI_PATH, "sheets", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Sheets アクセスに失敗しました（終了コード {completed.returncode}）"
            )
        try:
            return json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return {}

    def read_rows(self, tab_name: str) -> list[list[str]]:
        return self._read_rows_via_cli(tab_name)

    def _read_rows_via_cli(self, tab_name: str) -> list[list[str]]:
        data = self._run_cli(
            [
                "+read",
                "--spreadsheet",
                self._spreadsheet_id,
                "--range",
                tab_name,
            ]
        )
        return _extract_values(data)

    def read_links(self, tab_name: str) -> dict[tuple[int, int], str]:
        return self._read_links_via_cli(tab_name)

    def _read_links_via_cli(self, tab_name: str) -> dict[tuple[int, int], str]:
        # FORMULA 形式で読み、=HYPERLINK(...) 数式から
        # 実 URL を (行, 列) 辞書に復元する。表示形式の読み取り
        # （read_rows）では表示テキストしか得られないため分離する。
        params = {
            "spreadsheetId": self._spreadsheet_id,
            "range": tab_name,
            "valueRenderOption": "FORMULA",
        }
        data = self._run_cli(
            [
                "spreadsheets",
                "values",
                "get",
                "--params",
                json.dumps(params, ensure_ascii=False),
            ]
        )
        links: dict[tuple[int, int], str] = {}
        for row_index, row in enumerate(_extract_values(data)):
            for col_index, cell in enumerate(row):
                url = _parse_hyperlink_url(cell)
                if url:
                    links[(row_index, col_index)] = url
        return links

    def write_rows(self, tab_name: str, rows: Sequence[Sequence[str]]) -> None:
        if not rows:
            return
        self._write_rows_via_cli(tab_name, rows)

    def _write_rows_via_cli(self, tab_name: str, rows: Sequence[Sequence[str]]) -> None:
        # +append にはタブ（範囲）の指定手段がなく先頭タブへ流れるため、
        # 範囲を指定できる正規形式を使う。USER_ENTERED により
        # =HYPERLINK(...) 文字列は数式セルとして保持される。
        # 複数行は 1 回の append にまとめて欠落なく書く。
        params = {
            "spreadsheetId": self._spreadsheet_id,
            "range": tab_name,
            "valueInputOption": "USER_ENTERED",
        }
        body = {"values": [list(row) for row in rows]}
        self._run_cli(
            [
                "spreadsheets",
                "values",
                "append",
                "--params",
                json.dumps(params, ensure_ascii=False),
                "--json",
                json.dumps(body, ensure_ascii=False),
            ]
        )
