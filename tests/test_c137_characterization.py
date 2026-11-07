"""C-137 特性テスト: sheets_gateway の CLI 経路（削除前の現状固定）。

前提（本人の決定）: GoogleApiSheetsBackend のサービス経路を削除し、
CLI 一本に確定する。本ファイルは削除前に「CLI 経路の挙動は一切
変わらない」ことを固定するための特性テストであり、現行コードで
全件 pass する（現状固定）。

根拠: 調査レポート job-candidate-list-C137-usage-check.md
（本番生成は cli.py の `_gateway` による
`GoogleApiSheetsBackend(None, spreadsheet_id)` の 1 箇所のみで、
read_rows／read_links は常に CLI 経路、write_rows は現行でも常に
CLI 経路）、architecture.md §3.11、test_sheets_backend_cli.py 冒頭の
フェイク CLI 前提（gws CLI の公開仕様）。

既存テストとの重複回避（担当範囲の切り分け）:
- test_sheets_backend_cli.py の 4 件は、候補 1 件の往復（upsert→
  read_candidates、情報源 URL のみ）、タブ独立性（混入の有無）、
  候補リストタブ 1 行・2 リンク列での read_links、複数候補の一括
  upsert を担当する。本ファイルはそれらで固定されていない範囲
  （レジストリ読取の内容そのもの、除外ログタブでの read_links、
  OpenWork URL を含む候補の読み戻し、backend.write_rows の直接
  書込の CLI 呼び出し形・空書込、append_exclusions の直列化形、
  本番生成形での全操作の CLI 完結）だけを固定する。
- test_c068_characterization.py §4 のサービス経路 4 件は本ファイル
  では扱わない（削除時に仕様変更として別途扱う対象）。

フェイクは test_sheets_backend_cli.py の FakeGwsCli（subprocess.run
差し替え）を再利用し、実 Sheets・実データには触れない。
"""

from __future__ import annotations

import json

from conftest import impl, registry_rows
from test_sheets_backend_cli import (
    SPREADSHEET_ID,
    TAB_CANDIDATES,
    TAB_EXCLUSIONS,
    TAB_REGISTRY,
    _default_tabs,
    _exclusion,
    _install_fake,
)

CLI_PATH = "/opt/hatch/bin/hatch_gws_cli"


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------
def _make_backend_gateway(monkeypatch, initial_tabs=None):
    fake = _install_fake(monkeypatch, initial_tabs)
    gw = impl("job_candidate_list.sheets_gateway")
    # 本番生成形（cli.py の _gateway と同じく service=None）
    backend = gw.GoogleApiSheetsBackend(None, SPREADSHEET_ID)
    gateway = gw.GoogleSheetsGateway(backend)
    return backend, gateway, fake


def _params_of(cmd) -> dict:
    """values get／append 呼び出しの --params JSON を dict で返す。"""
    return json.loads(cmd[cmd.index("--params") + 1])


def _body_of(cmd) -> dict:
    """values append 呼び出しの --json JSON を dict で返す。"""
    return json.loads(cmd[cmd.index("--json") + 1])


def _call_summary(cmd):
    """CLI 呼び出しを (種別, 対象タブ) に要約する。

    get の場合は valueRenderOption も含める（FORMULA 読みの固定用）。
    """
    assert cmd[0] == CLI_PATH, f"CLI パスが想定と異なる: {cmd!r}"
    assert cmd[1] == "sheets"
    if cmd[2] == "+read":
        return ("+read", cmd[cmd.index("--range") + 1])
    if cmd[2:5] == ["spreadsheets", "values", "get"]:
        params = _params_of(cmd)
        return ("get", params.get("range"), params.get("valueRenderOption"))
    if cmd[2:5] == ["spreadsheets", "values", "append"]:
        return ("append", _params_of(cmd).get("range"))
    raise AssertionError(f"想定外の CLI 呼び出し形式: {cmd!r}")


# ---------------------------------------------------------------------------
# 1. CLI 経路の読取
# ---------------------------------------------------------------------------
def test_cli_read_rows_and_registry_rows_return_seeded_rows_verbatim(monkeypatch):
    """レジストリタブの read_rows／read_registry_rows が種付け行を
    そのまま返すこと（既存 CLI テストはレジストリの行数しか見て
    おらず、内容と gateway 経由の読取は未固定）。

    あわせて、読取が `sheets +read --range <タブ>` の CLI 呼び出し
    1 回で完結することを固定する。
    """
    expected = registry_rows("サンプル情報源A") + [registry_rows("サンプル情報源B")[1]]
    tabs = _default_tabs()
    tabs[TAB_REGISTRY] = [list(row) for row in expected]
    backend, gateway, fake = _make_backend_gateway(monkeypatch, tabs)

    rows = backend.read_rows(TAB_REGISTRY)
    assert rows == expected
    assert fake.calls == [
        [
            CLI_PATH,
            "sheets",
            "+read",
            "--spreadsheet",
            SPREADSHEET_ID,
            "--range",
            TAB_REGISTRY,
        ]
    ]

    # gateway 経由（cli.py の validate-registry が使う形）でも同一の行
    assert gateway.read_registry_rows() == expected


def test_cli_read_links_on_exclusions_tab_maps_only_formula_cells(monkeypatch):
    """除外ログタブの read_links が HYPERLINK 数式セルの位置だけを
    (行, 列) → 実 URL で返し、プレーンな URL 文字列のセルは含めない
    こと（既存 CLI テストの read_links は候補リストタブのみ）。

    表示読み（read_rows）では数式セルが表示テキストに、プレーンな
    セルは文字列のままになることもあわせて固定する。
    """
    header = list(_default_tabs()[TAB_EXCLUSIONS][0])
    url_col = header.index("情報源URL")
    rows = [
        header,
        [
            "excl_0000000000000001",
            "2026-11-05",
            "A社",
            "PM",
            "正社員",
            "公開求人",
            '=HYPERLINK("https://example.invalid/e/1", "求人ページ")',
            "理由1",
            "R1",
            "cfg1",
        ],
        [
            "excl_0000000000000002",
            "2026-11-05",
            "B社",
            "PM",
            "正社員",
            "公開求人",
            "https://example.invalid/e/2-plain",
            "理由2",
            "R2",
            "cfg2",
        ],
        [
            "excl_0000000000000003",
            "2026-11-05",
            "C社",
            "PM",
            "正社員",
            "公開求人",
            '=HYPERLINK("https://example.invalid/e/3", "求人ページ")',
            "理由3",
            "R3",
            "cfg3",
        ],
    ]
    tabs = _default_tabs()
    tabs[TAB_EXCLUSIONS] = rows
    backend, _gateway, _fake = _make_backend_gateway(monkeypatch, tabs)

    assert backend.read_links(TAB_EXCLUSIONS) == {
        (1, url_col): "https://example.invalid/e/1",
        (3, url_col): "https://example.invalid/e/3",
    }

    displayed = backend.read_rows(TAB_EXCLUSIONS)
    assert displayed[1][url_col] == "求人ページ"
    assert displayed[2][url_col] == "https://example.invalid/e/2-plain"
    assert displayed[3][url_col] == "求人ページ"


def test_cli_read_candidates_restores_both_link_columns_and_openwork(monkeypatch):
    """FORMULA 表示の =HYPERLINK(...) を含む CLI 応答から、候補の
    情報源URL と OpenWork URL の両リンク列と、OpenWork点数／取得日
    が読み戻せること（既存 CLI テストの往復は情報源URL のみで、
    OpenWork 列の読み戻しは未固定）。

    書込経路とは独立に検証するため、フェイク CLI のストアへ
    HYPERLINK 数式セルを直接種付けする。
    """
    gw = impl("job_candidate_list.sheets_gateway")
    auto = list(gw.AUTO_COLUMNS_CANDIDATE)
    header = auto + list(gw.MANUAL_COLUMNS_CANDIDATE)
    row = [""] * len(header)
    row[0] = "cand_0000000000000042"
    row[1] = "株式会社サンプル青葉"
    row[2] = "プロダクトマネージャー"
    row[auto.index("雇用形態")] = "正社員"
    row[auto.index("情報源種別")] = "公開求人"
    row[auto.index("情報源URL")] = (
        '=HYPERLINK("https://example.invalid/jobs/42", "求人ページ")'
    )
    row[auto.index("OpenWork点数")] = "4.2"
    row[auto.index("OpenWork URL")] = (
        '=HYPERLINK("https://example.invalid/openwork/7", "OpenWork")'
    )
    row[auto.index("OpenWork点数取得日")] = "2026-11-01"
    row[auto.index("掲載確認日")] = "2026-11-05"
    row[auto.index("状態")] = "新規"
    tabs = _default_tabs()
    tabs[TAB_CANDIDATES] = [header, row]
    _backend, gateway, _fake = _make_backend_gateway(monkeypatch, tabs)

    restored = gateway.read_candidates()
    assert len(restored) == 1
    got = restored[0]
    assert got.candidate_id == "cand_0000000000000042"
    assert got.source_url.display_text == "求人ページ"
    assert got.source_url.url == "https://example.invalid/jobs/42"
    assert got.openwork_url is not None
    assert got.openwork_url.display_text == "OpenWork"
    assert got.openwork_url.url == "https://example.invalid/openwork/7"
    assert got.openwork_score == 4.2
    assert str(got.openwork_fetched_on) == "2026-11-01"
    assert str(got.confirmed_on) == "2026-11-05"


# ---------------------------------------------------------------------------
# 2. CLI 経路の書込
# ---------------------------------------------------------------------------
def test_cli_write_rows_single_append_call_with_exact_payload_and_range(
    monkeypatch,
):
    """backend.write_rows の直接書込が、対象タブを指定した
    values append の CLI 呼び出し 1 回で、渡した行列そのものを
    USER_ENTERED で送ること（既存 CLI テストは gateway 経由の
    upsert／append の結果しか見ておらず、直接書込の呼び出し形と
    対象範囲は未固定）。

    書込結果は既存行（ヘッダ）の後ろへ順に追記され、他タブは
    変わらないこともあわせて固定する。
    """
    backend, _gateway, fake = _make_backend_gateway(monkeypatch)
    rows = [
        ["新規情報源A", "https://example.invalid/a/*", "不要"],
        ["新規情報源B", "https://example.invalid/b/*", "不要"],
    ]
    backend.write_rows(TAB_REGISTRY, rows)

    assert len(fake.calls) == 1, f"CLI 呼び出しは 1 回のはず: {fake.calls!r}"
    cmd = fake.calls[0]
    assert _call_summary(cmd) == ("append", TAB_REGISTRY)
    assert _params_of(cmd) == {
        "spreadsheetId": SPREADSHEET_ID,
        "range": TAB_REGISTRY,
        "valueInputOption": "USER_ENTERED",
    }
    assert _body_of(cmd) == {"values": rows}

    stored = backend.read_rows(TAB_REGISTRY)
    assert stored[1:] == rows
    assert len(backend.read_rows(TAB_CANDIDATES)) == 1  # ヘッダのみ
    assert len(backend.read_rows(TAB_EXCLUSIONS)) == 1  # ヘッダのみ


def test_cli_write_rows_empty_rows_makes_no_cli_call(monkeypatch):
    """現行挙動: 空の行列を write_rows に渡しても CLI は呼ばれず、
    タブの内容も変わらない。"""
    backend, _gateway, fake = _make_backend_gateway(monkeypatch)
    before = backend.read_rows(TAB_CANDIDATES)
    fake.calls.clear()

    backend.write_rows(TAB_CANDIDATES, [])

    assert fake.calls == []
    assert backend.read_rows(TAB_CANDIDATES) == before


def test_cli_append_exclusions_serializes_row_as_single_append(monkeypatch):
    """gateway.append_exclusions が除外行を、除外ログタブへの
    values append 1 回で、現行の直列化形（日付は ISO 文字列、
    情報源URL は =HYPERLINK(...) 数式文字列）で送ること
    （既存 CLI テストは混入の有無のみで、直列化形は未固定）。

    書込後の表示読み／リンク読みでも形が保たれることも固定する。
    """
    backend, gateway, fake = _make_backend_gateway(monkeypatch)
    gateway.append_exclusions([_exclusion()])

    assert len(fake.calls) == 1, f"CLI 呼び出しは 1 回のはず: {fake.calls!r}"
    cmd = fake.calls[0]
    assert _call_summary(cmd) == ("append", TAB_EXCLUSIONS)
    assert _params_of(cmd)["valueInputOption"] == "USER_ENTERED"
    assert _body_of(cmd) == {
        "values": [
            [
                "excl_0000000000000001",
                "2026-11-05",
                "株式会社サンプル乙",
                "ソフトウェアエンジニア",
                "契約社員",
                "公開求人",
                '=HYPERLINK("https://example.invalid/jobs/99", "求人ページ")',
                "雇用形態が正社員でない",
                "HARD-EMPTYPE",
                "hard.non_regular_keywords",
            ]
        ]
    }

    url_col = list(_default_tabs()[TAB_EXCLUSIONS][0]).index("情報源URL")
    displayed = backend.read_rows(TAB_EXCLUSIONS)
    assert displayed[1][url_col] == "求人ページ"
    assert backend.read_links(TAB_EXCLUSIONS) == {
        (1, url_col): "https://example.invalid/jobs/99"
    }


# ---------------------------------------------------------------------------
# 3. バックエンド確定後の形（本番生成形で全て CLI 経路で完結すること）
# ---------------------------------------------------------------------------
def test_none_backend_full_flow_completes_entirely_via_cli(monkeypatch):
    """本番生成形（service=None）で、読取・リンク読取・候補読取・
    書込・レジストリ読取の一連が、すべて hatch_gws_cli の呼び出し
    だけで完結し、その種別と対象タブの並びが現行どおりであること。

    サービス経路削除後もこの並びが変わらないことが、C-137 の
    完了の定義（CLI 経路の挙動は一切変わらない）の核になる。
    """
    backend, gateway, fake = _make_backend_gateway(monkeypatch)

    assert backend.read_rows(TAB_CANDIDATES)[0][0] == "候補ID"
    assert backend.read_links(TAB_CANDIDATES) == {}
    assert gateway.read_candidates() == []  # ヘッダのみ → 候補なし
    backend.write_rows(TAB_REGISTRY, [["追記行", "x"]])
    registry = gateway.read_registry_rows()

    assert registry[1] == ["追記行", "x"]
    assert fake.calls, "CLI が一度も呼ばれていない"
    assert all(cmd[0] == CLI_PATH for cmd in fake.calls)
    assert [_call_summary(cmd) for cmd in fake.calls] == [
        ("+read", TAB_CANDIDATES),
        ("get", TAB_CANDIDATES, "FORMULA"),
        ("+read", TAB_CANDIDATES),
        ("get", TAB_CANDIDATES, "FORMULA"),
        ("append", TAB_REGISTRY),
        ("+read", TAB_REGISTRY),
    ]
    # subprocess 差し替え以外に外部境界へ出ていないことの確認として、
    # フェイクが記録した呼び出しが上記の 6 回ちょうどであること
    assert len(fake.calls) == 6
