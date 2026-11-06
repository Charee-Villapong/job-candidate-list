"""Google Sheets 入出力（§3.11、追補 A5）。

書き込むのは自動列のみとし、列名のホワイトリストで物理的に制限する。
URL 列は表示テキスト付きリンク（HYPERLINK 形式）で記録し、生 URL を
セル文字列として書かない。
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from typing import Mapping, Protocol, Sequence

from .models import (
    CandidateRecord,
    ExclusionRow,
    LinkCell,
    LocationDivision,
    Reason,
    RemoteDivision,
    SourceType,
)

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

_DEFAULT_SHEET_NAMES = {
    "candidates": "候補リスト",
    "registry": "情報源レジストリ",
    "exclusions": "除外ログ",
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


def _parse_date(value: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
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
        _SOURCE_TYPE_DISPLAY.get(
            SourceType(record.source_type), str(record.source_type)
        ),
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
        location_text=cell("勤務地") or "不明",
        location_result=cell("勤務地判定結果") or "勤務地判定不能",
        remote_division=cell("リモート頻度") or "不明",
        fitness=cell("適合度") or "要確認",
        fit_reasons=_parse_reasons(cell("適合理由")),
        check_reasons=_parse_reasons(cell("要確認理由")),
        source_type=_SOURCE_TYPE_FROM_DISPLAY.get(
            cell("情報源種別"), SourceType.PUBLIC
        ),
        source_url=link_cell("情報源URL", "求人ページ"),
        openwork_score=float(score_text) if score_text else None,
        openwork_url=(link_cell("OpenWork URL", "OpenWork") if ow_url_text else None),
        openwork_fetched_on=_parse_date(cell("OpenWork点数取得日")),
        presentation=cell("提示区分") or "控え",
        confirmed_on=_parse_date(cell("掲載確認日")),
        status=cell("状態") or "新規",
        apply_status=cell("応募状態") or "未応募",
        apply_result=cell("応募結果") or None,
    )


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
        out: list[list[str]] = []
        for row in rows:
            out.append(
                [
                    row.exclusion_id,
                    row.fetched_on.isoformat(),
                    row.company,
                    row.position,
                    row.employment_type or "",
                    _SOURCE_TYPE_DISPLAY.get(
                        SourceType(row.source_type), str(row.source_type)
                    ),
                    _hyperlink(row.source_url),
                    row.reason,
                    ",".join(row.rule_ids),
                    ",".join(row.config_refs),
                ]
            )
        self._backend.write_rows(self._sheet_names["exclusions"], out)


class GoogleApiSheetsBackend:
    """実 Sheets アクセス用の backend。

    この環境の Sheets アクセス経路である
    ``/opt/hatch/bin/hatch_gws_cli sheets ...`` を呼び出す。
    ``service`` に Google Sheets API クライアント風のオブジェクト
    （``spreadsheets()`` を持つもの）が渡された場合はそちらを使う。
    """

    CLI_PATH = "/opt/hatch/bin/hatch_gws_cli"

    def __init__(self, service: object, spreadsheet_id: str) -> None:
        self._service = service
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
        if self._service is not None and hasattr(self._service, "spreadsheets"):
            result = (
                self._service.spreadsheets()
                .values()
                .get(spreadsheetId=self._spreadsheet_id, range=tab_name)
                .execute()
            )
            return [[str(c) for c in row] for row in result.get("values", [])]
        data = self._run_cli(
            [
                "+read",
                "--spreadsheet",
                self._spreadsheet_id,
                "--range",
                tab_name,
            ]
        )
        values = data.get("values", []) if isinstance(data, dict) else []
        return [[str(c) for c in row] for row in values]

    def read_links(self, tab_name: str) -> dict[tuple[int, int], str]:
        if self._service is not None and hasattr(self._service, "spreadsheets"):
            # Google API クライアント経路の従来の振る舞いを変えない。
            return {}
        # CLI 経路では FORMULA 形式で読み、=HYPERLINK(...) 数式から
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
        values = data.get("values", []) if isinstance(data, dict) else []
        links: dict[tuple[int, int], str] = {}
        for row_index, row in enumerate(values):
            for col_index, cell in enumerate(row):
                url = _parse_hyperlink_url(str(cell))
                if url:
                    links[(row_index, col_index)] = url
        return links

    def write_rows(self, tab_name: str, rows: Sequence[Sequence[str]]) -> None:
        if not rows:
            return
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
