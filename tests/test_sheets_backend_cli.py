"""M-1（第2段階の前提）: GoogleApiSheetsBackend の CLI 経路の読み書き往復。

根拠: docs/引き継ぎ.md「既知の課題 M-1」、architecture.md §3.11／§10.1、
追補 A5。本ファイルは実装（sheets_gateway.py）を読まず、公開仕様だけから書く。

対象は `GoogleApiSheetsBackend(spreadsheet_id)` の CLI 経路
（cli.py の `_gateway` が使う形。実 Sheets アクセスは
`/opt/hatch/bin/hatch_gws_cli sheets ...` を subprocess で呼ぶ）。

フェイク CLI の前提（gws CLI の --help と公開仕様で確認済みの振る舞い）:
- `sheets +read --spreadsheet ID --range TAB`
  → `{"values": [[...]]}`。値は表示形式（HYPERLINK セルは表示テキストのみ）。
- `sheets +append --spreadsheet ID --values 'a,b'`
  → カンマ区切り文字列を 1 行として追加。
  `--json-values '[["a"]]'` → JSON 行列を追加。
  **+append にはタブ／範囲の指定手段がない**（先頭タブへの追加になる）。
  未知のオプション（例: `--range`）は実 CLI と同様に検証エラー
  （終了コード 3、stdout に error JSON）で返す。
- `sheets spreadsheets values append
    --params '{"spreadsheetId":..,"range":..,"valueInputOption":"USER_ENTERED"}'
    --json '{"values":[[..]]}'`
  → 範囲（タブ）を指定して追加できる。USER_ENTERED のとき
  `=HYPERLINK("url","text")` 形式の文字列は数式セルとして保持される
  （明示的に RAW が指定された場合は文字列のまま保持する。
  valueInputOption の指定自体が無い場合は USER_ENTERED として扱う）。
- `sheets spreadsheets values get
    --params '{"spreadsheetId":..,"range":..,"valueRenderOption":"FORMULA"}'`
  → 数式（=HYPERLINK(...) を含む）を文字列として取得できる。
  valueRenderOption 省略時は表示形式。
- その他、値の書込系として正規の `values update`／`values batchUpdate`、
  読取系として `values batchGet` も上記と同じ規則でシミュレートする。

フェイクはタブ別のインメモリストアを持ち、HYPERLINK 数式セルは
「数式として保持し、表示読みでは表示テキストを返す」。
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess

import pytest
from conftest import REGISTRY_HEADER, build_candidate_record, impl

SPREADSHEET_ID = "fake-spreadsheet-id"
TAB_CANDIDATES = "候補リスト"
TAB_REGISTRY = "情報源レジストリ"
TAB_EXCLUSIONS = "除外ログ"
TAB_ORDER = [TAB_CANDIDATES, TAB_REGISTRY, TAB_EXCLUSIONS]

EXCLUSION_HEADER = [
    "除外ID",
    "取得日",
    "会社名",
    "ポジション名",
    "雇用形態",
    "情報源種別",
    "情報源URL",
    "除外理由",
    "ルールID",
    "設定参照",
]

_HYPERLINK_RE = re.compile(r'^=HYPERLINK\("([^"]*)",\s*"([^"]*)"\)$')


# ---------------------------------------------------------------------------
# フェイク CLI（文書化された gws CLI の振る舞いのシミュレーション）
# ---------------------------------------------------------------------------
class _Cell:
    """シートの 1 セル。数式セルは数式を保持し、表示は表示テキストになる。"""

    def __init__(self, text: str = "", formula: str | None = None):
        self.text = text
        self.formula = formula

    @classmethod
    def from_value(cls, value, *, user_entered: bool) -> "_Cell":
        text = "" if value is None else str(value)
        if user_entered and text.startswith("="):
            return cls(formula=text)
        return cls(text=text)

    @property
    def display(self) -> str:
        if self.formula is not None:
            m = _HYPERLINK_RE.match(self.formula)
            if m:
                return m.group(2)
            return self.formula
        return self.text

    @property
    def formula_view(self) -> str:
        if self.formula is not None:
            return self.formula
        return self.text


class _CliError(Exception):
    def __init__(self, flag: str):
        super().__init__(flag)
        self.flag = flag


_COMMON_VALUE_OPTS = {
    "--sanitize",
    "--format",
    "--output",
    "-o",
    "--page-limit",
    "--page-delay",
}
_COMMON_BOOL_OPTS = {"--dry-run", "--page-all", "-h", "--help"}


def _col_to_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


class FakeGwsCli:
    """`hatch_gws_cli sheets ...` の subprocess.run 差し替え用フェイク。"""

    def __init__(self, initial_tabs: dict[str, list[list[str]]] | None = None):
        self._tabs: dict[str, list[list[_Cell]]] = {name: [] for name in TAB_ORDER}
        for tab, rows in (initial_tabs or {}).items():
            self._tabs[tab] = [
                [_Cell.from_value(v, user_entered=True) for v in row] for row in rows
            ]
        self.calls: list[list[str]] = []

    # -- subprocess.run の代替 -------------------------------------------
    def run(self, cmd, *args, **kwargs):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        try:
            payload = self._dispatch(cmd)
        except _CliError as exc:
            message = f"error: unexpected argument '{exc.flag}' found"
            stdout = json.dumps(
                {
                    "error": {
                        "code": 400,
                        "message": message,
                        "reason": "validationError",
                    }
                }
            )
            return subprocess.CompletedProcess(
                cmd, 3, stdout=stdout, stderr=f"error[validation]: {message}\n"
            )
        except Exception as exc:  # 想定外の呼び出し形式は CLI エラーとして返す
            stdout = json.dumps(
                {"error": {"code": 400, "message": str(exc), "reason": "fakeCli"}}
            )
            return subprocess.CompletedProcess(
                cmd, 3, stdout=stdout, stderr=f"error: {exc}\n"
            )
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(payload, ensure_ascii=False), stderr=""
        )

    # -- 引数解析 ---------------------------------------------------------
    @staticmethod
    def _parse_options(tokens, value_opts, bool_opts):
        options: dict[str, object] = {}
        i = 0
        while i < len(tokens):
            arg = tokens[i]
            name, eq, inline = arg.partition("=")
            if eq and name in value_opts:
                options[name] = inline
                i += 1
            elif arg in value_opts:
                if i + 1 >= len(tokens):
                    raise _CliError(arg)
                options[arg] = tokens[i + 1]
                i += 2
            elif arg in bool_opts:
                options[arg] = True
                i += 1
            else:
                raise _CliError(arg)
        return options

    def _options(self, tokens, specific_value_opts):
        return self._parse_options(
            tokens, _COMMON_VALUE_OPTS | set(specific_value_opts), _COMMON_BOOL_OPTS
        )

    # -- 範囲（タブ・セル範囲）の解釈 --------------------------------------
    def _split_range(self, range_str: str):
        """'タブ'／'タブ!A1:B2' を (タブ名, セル範囲文字列|None) に分ける。"""
        if not range_str:
            return TAB_ORDER[0], None
        if "!" not in range_str:
            return range_str.strip("'"), None
        tab, _, cells = range_str.partition("!")
        return tab.strip("'"), cells or None

    @staticmethod
    def _slice(rows, cells):
        if not cells:
            return rows
        m = re.match(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$", cells)
        if not m:
            return rows
        col_start = _col_to_index(m.group(1))
        row_start = int(m.group(2)) - 1
        col_end = _col_to_index(m.group(3)) + 1 if m.group(3) else None
        row_end = int(m.group(4)) if m.group(4) else None
        return [row[col_start:col_end] for row in rows[row_start:row_end]]

    def _read_values(self, tab: str, cells, *, formula: bool):
        stored = self._tabs.get(tab, [])
        if formula:
            rows = [[c.formula_view for c in row] for row in stored]
        else:
            rows = [[c.display for c in row] for row in stored]
        return self._slice(rows, cells)

    def _append_rows(self, tab: str, rows, *, user_entered: bool):
        store = self._tabs.setdefault(tab, [])
        for row in rows:
            store.append([_Cell.from_value(v, user_entered=user_entered) for v in row])

    def _update_rows(self, tab: str, cells, rows, *, user_entered: bool):
        store = self._tabs.setdefault(tab, [])
        row_start, col_start = 0, 0
        if cells:
            m = re.match(r"([A-Z]+)(\d+)", cells)
            if m:
                col_start = _col_to_index(m.group(1))
                row_start = int(m.group(2)) - 1
        for offset, row in enumerate(rows):
            r = row_start + offset
            while len(store) <= r:
                store.append([])
            target = store[r]
            values = [_Cell.from_value(v, user_entered=user_entered) for v in row]
            end = col_start + len(values)
            if len(target) < end:
                target.extend(_Cell() for _ in range(end - len(target)))
            target[col_start:end] = values

    @staticmethod
    def _user_entered(params, body=None) -> bool:
        option = None
        if isinstance(params, dict):
            option = params.get("valueInputOption")
        if option is None and isinstance(body, dict):
            option = body.get("valueInputOption")
        if option is None:
            return True
        return str(option).upper() == "USER_ENTERED"

    # -- コマンド振り分け ---------------------------------------------------
    def _dispatch(self, cmd):
        if len(cmd) < 2 or cmd[1] != "sheets":
            raise _CliError(cmd[1] if len(cmd) > 1 else "")
        rest = cmd[2:]
        if rest[:1] == ["+read"]:
            return self._cmd_plus_read(rest[1:])
        if rest[:1] == ["+append"]:
            return self._cmd_plus_append(rest[1:])
        if rest[:2] == ["spreadsheets", "values"]:
            action = rest[2] if len(rest) > 2 else ""
            handler = {
                "get": self._cmd_values_get,
                "append": self._cmd_values_append,
                "update": self._cmd_values_update,
                "batchUpdate": self._cmd_values_batch_update,
                "batchGet": self._cmd_values_batch_get,
            }.get(action)
            if handler is None:
                raise _CliError(action)
            return handler(rest[3:])
        raise _CliError(rest[0] if rest else "")

    def _cmd_plus_read(self, tokens):
        options = self._options(tokens, {"--spreadsheet", "--range"})
        tab, cells = self._split_range(str(options.get("--range", "")))
        # +read は表示形式のみ（HYPERLINK セルは表示テキストのみ見える）
        return {"values": self._read_values(tab, cells, formula=False)}

    def _cmd_plus_append(self, tokens):
        options = self._options(tokens, {"--spreadsheet", "--values", "--json-values"})
        # +append にはタブ指定手段がないため、先頭タブへの追加になる
        target = TAB_ORDER[0]
        if "--json-values" in options:
            rows = json.loads(str(options["--json-values"]))
            self._append_rows(target, rows, user_entered=True)
        elif "--values" in options:
            row = str(options["--values"]).split(",")
            self._append_rows(target, [row], user_entered=True)
        return {"spreadsheetId": SPREADSHEET_ID, "updates": {"updatedRows": 1}}

    def _cmd_values_get(self, tokens):
        options = self._options(tokens, {"--params", "--json"})
        params = json.loads(str(options.get("--params", "{}")))
        tab, cells = self._split_range(str(params.get("range", "")))
        formula = str(params.get("valueRenderOption", "")).upper() == "FORMULA"
        return {
            "spreadsheetId": params.get("spreadsheetId", SPREADSHEET_ID),
            "range": params.get("range", tab),
            "values": self._read_values(tab, cells, formula=formula),
        }

    def _cmd_values_append(self, tokens):
        options = self._options(tokens, {"--params", "--json"})
        params = json.loads(str(options.get("--params", "{}")))
        body = json.loads(str(options.get("--json", "{}")))
        tab, _cells = self._split_range(str(params.get("range", "")))
        user_entered = self._user_entered(params, body)
        self._append_rows(tab, body.get("values", []), user_entered=user_entered)
        return {
            "spreadsheetId": params.get("spreadsheetId", SPREADSHEET_ID),
            "updates": {"updatedRows": len(body.get("values", []))},
        }

    def _cmd_values_update(self, tokens):
        options = self._options(tokens, {"--params", "--json"})
        params = json.loads(str(options.get("--params", "{}")))
        body = json.loads(str(options.get("--json", "{}")))
        tab, cells = self._split_range(str(params.get("range", "")))
        user_entered = self._user_entered(params, body)
        self._update_rows(tab, cells, body.get("values", []), user_entered=user_entered)
        return {"spreadsheetId": params.get("spreadsheetId", SPREADSHEET_ID)}

    def _cmd_values_batch_update(self, tokens):
        options = self._options(tokens, {"--params", "--json"})
        params = json.loads(str(options.get("--params", "{}")))
        body = json.loads(str(options.get("--json", "{}")))
        user_entered = self._user_entered(params, body)
        for value_range in body.get("valueRanges", []):
            tab, cells = self._split_range(str(value_range.get("range", "")))
            self._update_rows(
                tab, cells, value_range.get("values", []), user_entered=user_entered
            )
        return {"spreadsheetId": params.get("spreadsheetId", SPREADSHEET_ID)}

    def _cmd_values_batch_get(self, tokens):
        options = self._options(tokens, {"--params", "--json"})
        params = json.loads(str(options.get("--params", "{}")))
        formula = str(params.get("valueRenderOption", "")).upper() == "FORMULA"
        value_ranges = []
        for range_str in params.get("ranges", []):
            tab, cells = self._split_range(str(range_str))
            value_ranges.append(
                {
                    "range": range_str,
                    "values": self._read_values(tab, cells, formula=formula),
                }
            )
        return {
            "spreadsheetId": params.get("spreadsheetId", SPREADSHEET_ID),
            "valueRanges": value_ranges,
        }


# ---------------------------------------------------------------------------
# フィクスチャ／ヘルパー
# ---------------------------------------------------------------------------
def _default_tabs():
    gw = impl("job_candidate_list.sheets_gateway")
    candidates_header = list(gw.AUTO_COLUMNS_CANDIDATE) + list(
        gw.MANUAL_COLUMNS_CANDIDATE
    )
    return {
        TAB_CANDIDATES: [candidates_header],
        TAB_REGISTRY: [list(REGISTRY_HEADER)],
        TAB_EXCLUSIONS: [list(EXCLUSION_HEADER)],
    }


def _install_fake(monkeypatch, initial_tabs=None) -> FakeGwsCli:
    fake = FakeGwsCli(_default_tabs() if initial_tabs is None else initial_tabs)
    monkeypatch.setattr(subprocess, "run", fake.run)
    return fake


@pytest.fixture()
def cli_env(monkeypatch):
    """フェイク CLI を差し替えた (backend, gateway, fake) を返す。"""
    fake = _install_fake(monkeypatch)
    gw = impl("job_candidate_list.sheets_gateway")
    # spreadsheet_id のみが CLI 経路（cli.py の _gateway と同じ渡し方）
    backend = gw.GoogleApiSheetsBackend(SPREADSHEET_ID)
    gateway = gw.GoogleSheetsGateway(backend)
    return backend, gateway, fake


def _candidate(candidate_id: str, **over):
    models = impl("job_candidate_list.models")
    fields = {
        "company": "株式会社サンプル青葉",
        "position": "プロダクトマネージャー",
        "company_norm": "サンプル青葉",
        "position_norm": "プロダクトマネージャー",
        "employment_type": "正社員",
        "salary_range_text": "年収 600万円〜800万円",
        "location_text": "サンプル甲市",
        "location_result": "勤務地適合",
        "remote_division": "出社週2〜3日",
        "fitness": "中",
        "fit_reasons": (models.Reason("上流主務が確認できた", ("JOB-UPSTREAM-OK",)),),
        "check_reasons": (models.Reason("年収は目安の範囲内", ("SAL-OK",)),),
        "source_type": "公開求人",
        "source_url": models.LinkCell(
            "求人ページ", "https://example.invalid/jobs/1"
        ),
        "presentation": "控え",
        "confirmed_on": dt.date(2026, 11, 5),
        "status": "新規",
        "apply_status": "未応募",
    }
    fields.update(over)
    return build_candidate_record(candidate_id, **fields)


def _exclusion(**over):
    models = impl("job_candidate_list.models")
    fields = {
        "exclusion_id": "excl_0000000000000001",
        "fetched_on": dt.date(2026, 11, 5),
        "company": "株式会社サンプル乙",
        "position": "ソフトウェアエンジニア",
        "employment_type": "契約社員",
        "source_type": "公開求人",
        "source_url": models.LinkCell(
            "求人ページ", "https://example.invalid/jobs/99"
        ),
        "reason": "雇用形態が正社員でない",
        "rule_ids": ("HARD-EMPTYPE",),
        "config_refs": ("hard.non_regular_keywords",),
    }
    fields.update(over)
    return models.ExclusionRow(**fields)


# ---------------------------------------------------------------------------
# 1. 候補レコードの往復（M-1: 書込と候補読取の両方）
# ---------------------------------------------------------------------------
def test_candidate_roundtrip_via_cli(cli_env):
    """Gateway 経由で書いた候補が自動列の内容どおり読み戻せる（§3.11）。

    特に情報源 URL は、表示テキスト付きリンクとして記録され、
    読み戻しで実 URL が復元できなければならない。
    """
    _backend, gateway, _fake = cli_env
    record = _candidate("cand_0000000000000001")
    gateway.upsert_candidates([record])

    restored = gateway.read_candidates()
    assert len(restored) == 1, f"読み戻し件数が一致しない: {len(restored)}"
    got = restored[0]
    assert got.candidate_id == record.candidate_id
    assert got.company == "株式会社サンプル青葉"
    assert got.position == "プロダクトマネージャー"
    assert got.employment_type == "正社員"
    assert got.salary_range_text == "年収 600万円〜800万円"
    assert got.location_text == "サンプル甲市"
    assert str(got.location_result) == "勤務地適合"
    assert str(got.remote_division) == "出社週2〜3日"
    assert str(got.fitness) == "中"
    assert str(got.source_type) == "公開求人"
    assert got.source_url.display_text == "求人ページ"
    assert got.source_url.url == "https://example.invalid/jobs/1", (
        "情報源URLの実URLが復元できない"
        f"（表示テキストのみ等）: {got.source_url.url!r}"
    )
    assert got.confirmed_on == dt.date(2026, 11, 5)
    assert str(got.status) == "新規"


# ---------------------------------------------------------------------------
# 2. タブ独立性
# ---------------------------------------------------------------------------
def test_tabs_are_independent(cli_env):
    """候補リストへの書込と除外ログへの append が互いに混入しない（§3.11）。

    タブを指定できない CLI 形式（+append）で書くと、先頭タブへ
    流れ込む等により他タブが汚染されるため、それも検出できる。
    """
    backend, gateway, _fake = cli_env
    gateway.upsert_candidates([_candidate("cand_0000000000000001")])
    gateway.append_exclusions([_exclusion()])

    candidate_rows = backend.read_rows(TAB_CANDIDATES)
    exclusion_rows = backend.read_rows(TAB_EXCLUSIONS)
    registry_rows = backend.read_rows(TAB_REGISTRY)

    candidate_flat = [cell for row in candidate_rows for cell in row]
    exclusion_flat = [cell for row in exclusion_rows for cell in row]
    assert "cand_0000000000000001" in candidate_flat
    assert "excl_0000000000000001" not in candidate_flat, (
        "除外ログの行が候補リストタブに混入している"
    )
    assert "excl_0000000000000001" in exclusion_flat
    assert "cand_0000000000000001" not in exclusion_flat, (
        "候補リストの行が除外ログタブに混入している"
    )
    # レジストリタブはヘッダのみのまま（どちらの書込でも汚染されない）
    assert len(registry_rows) == 1
    # 候補としての読み戻しも 1 件だけ（除外行が候補として読まれない）
    assert [c.candidate_id for c in gateway.read_candidates()] == [
        "cand_0000000000000001"
    ]


# ---------------------------------------------------------------------------
# 3. read_links（CLI 経路）
# ---------------------------------------------------------------------------
def test_read_links_via_cli_returns_real_urls(monkeypatch):
    """HYPERLINK 数式のあるタブで (行, 列) → 実 URL の辞書が返る（追補 A5）。

    書込経路とは独立に検証するため、フェイク CLI のストアへ
    HYPERLINK 数式セルを直接種付け（seed）する。表示読みでは
    表示テキストしか見えないこと（だからこそリンク読みが要ること）も
    あわせて確認する。
    """
    gw = impl("job_candidate_list.sheets_gateway")
    header = list(gw.AUTO_COLUMNS_CANDIDATE) + list(gw.MANUAL_COLUMNS_CANDIDATE)
    row = [""] * len(header)
    row[0] = "cand_0000000000000042"
    row[1] = "株式会社サンプル青葉"
    row[2] = "プロダクトマネージャー"
    url_col = list(gw.AUTO_COLUMNS_CANDIDATE).index("情報源URL")
    ow_col = list(gw.AUTO_COLUMNS_CANDIDATE).index("OpenWork URL")
    row[url_col] = '=HYPERLINK("https://example.invalid/jobs/42", "求人ページ")'
    row[ow_col] = '=HYPERLINK("https://example.invalid/openwork/7", "OpenWork")'
    tabs = _default_tabs()
    tabs[TAB_CANDIDATES] = [header, row]
    _install_fake(monkeypatch, tabs)

    backend = gw.GoogleApiSheetsBackend(SPREADSHEET_ID)

    # 表示読みではリンク先は見えず、表示テキストのみが返る
    displayed = backend.read_rows(TAB_CANDIDATES)
    assert displayed[1][url_col] == "求人ページ"
    assert displayed[1][ow_col] == "OpenWork"

    links = backend.read_links(TAB_CANDIDATES)
    assert links.get((1, url_col)) == "https://example.invalid/jobs/42", (
        f"情報源URL列の実URLが取れない: {links!r}"
    )
    assert links.get((1, ow_col)) == "https://example.invalid/openwork/7", (
        f"OpenWork URL列の実URLが取れない: {links!r}"
    )
    assert links == {
        (1, url_col): "https://example.invalid/jobs/42",
        (1, ow_col): "https://example.invalid/openwork/7",
    }


# ---------------------------------------------------------------------------
# 4. 複数行の一括書込
# ---------------------------------------------------------------------------
def test_bulk_upsert_writes_all_rows(cli_env):
    """複数候補の一括書込が欠落なく全行反映される（§3.11）。"""
    models = impl("job_candidate_list.models")
    backend, gateway, _fake = cli_env
    records = [
        _candidate(
            f"cand_{i:016d}",
            company=f"株式会社サンプル{i}号",
            position=f"ポジション{i}号",
            source_url=models.LinkCell(
                "求人ページ", f"https://example.invalid/jobs/{i}"
            ),
        )
        for i in (1, 2, 3)
    ]
    gateway.upsert_candidates(records)

    stored_rows = backend.read_rows(TAB_CANDIDATES)
    assert len(stored_rows) == 1 + 3, (
        f"ヘッダ＋3行のはずが {len(stored_rows)} 行しかない"
    )

    restored = gateway.read_candidates()
    assert [c.candidate_id for c in restored] == [r.candidate_id for r in records]
    by_id = {c.candidate_id: c for c in restored}
    for record in records:
        got = by_id[record.candidate_id]
        assert got.company == record.company
        assert got.position == record.position
        assert got.source_url.url == record.source_url.url
        assert str(got.fitness) == str(record.fitness)
