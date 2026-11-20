"""tools/reconcile_tabs.py の独立テスト（Issue #87）。

仕様の正本:
~/workspace/goals/ai-organization/hidden_files/issue-87/definition-of-done.md
（GitHub Issue Charee-Villapong/ai-organization #87）。

テスト係が仕様だけから書いたテストで、実装コードは含まない。実装がまだ無い
期間は全テストが「未実装: tools/reconcile_tabs.py が存在しない」で失敗する
のが正しい（pytest の収集は通す）。

流儀は tests/test_bookkeep.py に合わせる:
- tools/*.py を importlib.util.spec_from_file_location で読み込む。
- 実シートには一切触らない。シート読み取りはフェイクで差し替える。
- フィクスチャの会社名は架空のものだけを使う。

行データの形（仕様のインターフェース契約）:
- ヘッダー名をキーとする辞書。行番号は "_row" キー。
- 候補リスト行のグレー状態は "_gray" キー（True=グレー、False=非グレー、
  None=読み取り不能）。
- reconcile に渡す config は {"rules": <会社名正規化ルール>} とする。
  rules は tools/reconcile_tabs_config.json のトップレベル "rules" と同じ形。
- 対象タブ名は実シートのタブ名（選考結果／応募済み企業一覧／候補リスト）を使う。

before / after の厳密な文言は仕様で固定されていないため、その文言自体は
断定せず、存在・非空・一覧文言との一致など仕様から導ける性質だけを検証する。
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

_RECONCILE_PATH = Path(__file__).resolve().parents[1] / "tools" / "reconcile_tabs.py"
_CONFIG_PATH = _RECONCILE_PATH.with_name("reconcile_tabs_config.json")

if _RECONCILE_PATH.exists():
    _spec = importlib.util.spec_from_file_location("reconcile_tabs", _RECONCILE_PATH)
    reconcile_tabs = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(reconcile_tabs)
else:  # pragma: no cover - 未実装期間の経路
    reconcile_tabs = None


@pytest.fixture(autouse=True)
def _require_implementation():
    if reconcile_tabs is None:
        pytest.fail(
            "未実装: tools/reconcile_tabs.py が存在しない。"
            "Issue #87 の実装がまだ無いためテストは失敗するのが正しい。"
        )
    for name in ("normalize_company", "normalize_date", "reconcile"):
        if not hasattr(reconcile_tabs, name):
            pytest.fail(
                f"未実装: tools/reconcile_tabs.py に {name} が存在しない。"
                "完了の定義のインターフェース契約どおりでない。"
            )


# ---------------------------------------------------------------------------
# 正規化ルールと設定のフィクスチャ（仕様の完了の定義 4 由来）
# ---------------------------------------------------------------------------
RULES = {
    "remove_whitespace": True,
    "normalize_width": True,
    "remove_corporate_forms": ["株式会社", "(株)", "有限会社", "(有)"],
}

CONFIG = {"rules": RULES}


# ---------------------------------------------------------------------------
# 行データの構築ヘルパー（列名は完了の定義の実シート列構成どおり）
# ---------------------------------------------------------------------------
def _results_row(company, role, date, result, row):
    return {
        "会社名": company,
        "職種": role,
        "応募日": date,
        "選考結果": result,
        "_row": row,
    }


def _list_row(company, date, result_text, row):
    return {"会社名": company, "応募日": date, "結果": result_text, "_row": row}


def _candidate_row(company, position, status, row, gray, applied_at="2026-11-10 10:00"):
    return {
        "会社名": company,
        "ポジション名": position,
        "状態": status,
        "応募状態": "応募済み",
        "応募日時": applied_at,
        "_row": row,
        "_gray": gray,
    }


def _by_kind(result, kind):
    return [d for d in result["diffs"] if d["kind"] == kind]


def _matching_fixture():
    """全タブが一致する最小フィクスチャ（差分0件の基準）。"""
    results = [
        _results_row(
            "株式会社テスト商事", "プロジェクトマネージャー", "2026-11-13", "選考中", 2
        )
    ]
    listed = [_list_row("テスト商事", "2026/11/13", "選考中", 2)]
    candidates = [
        _candidate_row(
            "テスト商事",
            "プロジェクトマネージャー",
            "選考中",
            5,
            True,
            applied_at="2026-11-13 21:45",
        )
    ]
    return results, listed, candidates


# ---------------------------------------------------------------------------
# normalize_company
# ---------------------------------------------------------------------------
def test_normalize_company_removes_kabushiki_kaisha():
    assert (
        reconcile_tabs.normalize_company("株式会社テスト商事", RULES)
        == reconcile_tabs.normalize_company("テスト商事", RULES)
        == "テスト商事"
    )
    assert (
        reconcile_tabs.normalize_company("テスト商事 株式会社", RULES) == "テスト商事"
    )


def test_normalize_company_removes_kabu_abbreviation():
    assert reconcile_tabs.normalize_company("テスト商事（株）", RULES) == "テスト商事"
    assert reconcile_tabs.normalize_company("テスト商事(株)", RULES) == "テスト商事"


def test_normalize_company_removes_spaces():
    assert reconcile_tabs.normalize_company("テスト 商事", RULES) == "テスト商事"
    assert reconcile_tabs.normalize_company("テスト　商事", RULES) == "テスト商事"
    assert (
        reconcile_tabs.normalize_company(" 株式会社テスト商事 ", RULES) == "テスト商事"
    )


def test_normalize_company_unifies_full_half_width():
    assert (
        reconcile_tabs.normalize_company("テスト商事ＡＢＣ", RULES) == "テスト商事ABC"
    )
    assert reconcile_tabs.normalize_company("架空工業１２３", RULES) == "架空工業123"


def test_normalize_company_same_name_different_company_not_matched():
    assert reconcile_tabs.normalize_company(
        "テスト商事", RULES
    ) != reconcile_tabs.normalize_company("テスト商事ホールディングス", RULES)
    assert reconcile_tabs.normalize_company(
        "架空工業", RULES
    ) != reconcile_tabs.normalize_company("架空工業所", RULES)


def test_normalize_company_rules_are_not_hardcoded():
    """ルールが空なら法人格表記は除去されない（設定ファイル由来であること）。"""
    assert (
        reconcile_tabs.normalize_company("株式会社テスト商事", {})
        == "株式会社テスト商事"
    )


def test_normalize_company_only_listed_corporate_forms_are_removed():
    rules = {"remove_corporate_forms": ["株式会社"]}
    assert reconcile_tabs.normalize_company("テスト商事(株)", rules) == "テスト商事(株)"
    assert reconcile_tabs.normalize_company("株式会社テスト商事", rules) == "テスト商事"


def test_normalize_company_empty_name():
    assert reconcile_tabs.normalize_company("", RULES) == ""


# ---------------------------------------------------------------------------
# normalize_date
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026-11-13", "2026-11-13"),
        ("2026/11/13", "2026-11-13"),
        ("2026-11-13 21:45", "2026-11-13"),
        ("2026-11-14 1:31", "2026-11-14"),
        ("2026-11-13 21:45:00", "2026-11-13"),
    ],
)
def test_normalize_date_accepts_hyphen_slash_and_datetime(text, expected):
    assert reconcile_tabs.normalize_date(text) == expected


@pytest.mark.parametrize("text", ["", "   ", None])
def test_normalize_date_blank_returns_none(text):
    assert reconcile_tabs.normalize_date(text) is None


@pytest.mark.parametrize("text", ["不明", "abc", "2026-13-40", "2026/11/31"])
def test_normalize_date_unparseable_returns_none(text):
    assert reconcile_tabs.normalize_date(text) is None


# ---------------------------------------------------------------------------
# reconcile (1) missing_in_list / (3) missing_in_results
# ---------------------------------------------------------------------------
def test_reconcile_missing_in_list():
    results = [
        _results_row(
            "株式会社テスト商事", "プロジェクトマネージャー", "2026-11-13", "選考中", 2
        )
    ]
    result = reconcile_tabs.reconcile(results, [], [], CONFIG)
    assert len(result["diffs"]) == 1
    diff = result["diffs"][0]
    assert diff["kind"] == "missing_in_list"
    assert diff["company"] == "テスト商事"
    assert diff["applied_date"] == "2026-11-13"
    assert diff["target"] == {"tab": "選考結果", "row": 2}
    assert diff["before"] is None
    assert diff["after"]
    assert isinstance(diff["note"], str)


def test_reconcile_missing_in_results():
    listed = [_list_row("架空工業", "2026/11/10", "選考中", 3)]
    result = reconcile_tabs.reconcile([], listed, [], CONFIG)
    assert len(result["diffs"]) == 1
    diff = result["diffs"][0]
    assert diff["kind"] == "missing_in_results"
    assert diff["company"] == "架空工業"
    assert diff["applied_date"] == "2026-11-10"
    assert diff["target"] == {"tab": "応募済み企業一覧", "row": 3}


def test_reconcile_matching_rows_produce_no_diffs():
    """会社名の表記ゆれ・日付の区切り違い・時刻付きは同一とみなす。"""
    results, listed, candidates = _matching_fixture()
    result = reconcile_tabs.reconcile(results, listed, candidates, CONFIG)
    assert result["diffs"] == []
    assert result["needs_review"] == []


def test_reconcile_blank_dates_match_each_other():
    results = [_results_row("テスト商事", "プロジェクトマネージャー", "", "選考中", 2)]
    listed = [_list_row("テスト商事", "", "選考中", 2)]
    result = reconcile_tabs.reconcile(results, listed, [], CONFIG)
    assert result["diffs"] == []


def test_reconcile_blank_date_does_not_match_dated_row():
    results = [_results_row("テスト商事", "プロジェクトマネージャー", "", "選考中", 2)]
    listed = [_list_row("テスト商事", "2026-11-13", "選考中", 2)]
    result = reconcile_tabs.reconcile(results, listed, [], CONFIG)
    assert _by_kind(result, "missing_in_list")


# ---------------------------------------------------------------------------
# reconcile (2) result_mismatch
# ---------------------------------------------------------------------------
def _count_fixture(list_text):
    results = [
        _results_row("架空工業", "プロジェクトマネージャー", "2026-11-10", "選考中", 2),
        _results_row("架空工業", "ITコンサルタント", "2026-11-10", "選考中", 3),
        _results_row("架空工業", "データアナリスト", "2026-11-10", "選考中", 4),
        _results_row("架空工業", "アーキテクト", "2026-11-10", "募集終了", 5),
    ]
    listed = [_list_row("架空工業", "2026-11-10", list_text, 2)]
    return results, listed


def test_reconcile_result_mismatch_count_differs():
    results, listed = _count_fixture("選考中（2件）、募集終了（1件）")
    result = reconcile_tabs.reconcile(results, listed, [], CONFIG)
    assert len(result["diffs"]) == 1
    diff = result["diffs"][0]
    assert diff["kind"] == "result_mismatch"
    assert diff["company"] == "架空工業"
    assert diff["target"] == {"tab": "応募済み企業一覧", "row": 2}
    assert diff["before"] == "選考中（2件）、募集終了（1件）"
    assert diff["after"] and "選考中（3件）" in diff["after"]


def test_reconcile_result_match_counted_summary():
    results, listed = _count_fixture("選考中（3件）、募集終了（1件）")
    result = reconcile_tabs.reconcile(results, listed, [], CONFIG)
    assert result["diffs"] == []


def test_reconcile_result_mismatch_single_phrase():
    results = [
        _results_row(
            "サンプル物産",
            "プロジェクトマネージャー",
            "2026-11-11",
            "書類選考で不合格",
            2,
        )
    ]
    listed = [_list_row("サンプル物産", "2026-11-11", "選考中", 2)]
    result = reconcile_tabs.reconcile(results, listed, [], CONFIG)
    assert len(result["diffs"]) == 1
    assert result["diffs"][0]["kind"] == "result_mismatch"
    assert result["diffs"][0]["before"] == "選考中"


def test_reconcile_result_match_single_phrase():
    results = [
        _results_row(
            "サンプル物産",
            "プロジェクトマネージャー",
            "2026-11-11",
            "書類選考で不合格",
            2,
        )
    ]
    listed = [_list_row("サンプル物産", "2026-11-11", "書類選考で不合格", 2)]
    result = reconcile_tabs.reconcile(results, listed, [], CONFIG)
    assert result["diffs"] == []


def test_reconcile_unparseable_list_result_goes_to_needs_review():
    results = [
        _results_row(
            "サンプル物産", "プロジェクトマネージャー", "2026-11-11", "選考中", 2
        )
    ]
    listed = [_list_row("サンプル物産", "2026-11-11", "調整中メモ", 2)]
    result = reconcile_tabs.reconcile(results, listed, [], CONFIG)
    assert result["diffs"] == []
    assert len(result["needs_review"]) == 1
    review = result["needs_review"][0]
    assert review["kind"] == "result_mismatch"
    assert review["reason"]
    assert result["false_positive_count"] == 1


# ---------------------------------------------------------------------------
# reconcile (4) not_grayed
# ---------------------------------------------------------------------------
def _gray_fixture(gray, status="選考中"):
    results = [
        _results_row(
            "サンプル物産", "プロジェクトマネージャー", "2026-11-10", "選考中", 2
        )
    ]
    listed = [_list_row("サンプル物産", "2026-11-10", "選考中", 2)]
    candidates = [
        _candidate_row("サンプル物産", "プロジェクトマネージャー", status, 5, gray)
    ]
    return results, listed, candidates


def test_reconcile_not_grayed():
    results, listed, candidates = _gray_fixture(False)
    result = reconcile_tabs.reconcile(results, listed, candidates, CONFIG)
    assert len(result["diffs"]) == 1
    diff = result["diffs"][0]
    assert diff["kind"] == "not_grayed"
    assert diff["company"] == "サンプル物産"
    assert diff["role"] == "プロジェクトマネージャー"
    assert diff["target"] == {"tab": "候補リスト", "row": 5}
    assert diff["after"]


def test_reconcile_grayed_row_has_no_diff():
    results, listed, candidates = _gray_fixture(True)
    result = reconcile_tabs.reconcile(results, listed, candidates, CONFIG)
    assert result["diffs"] == []


def test_reconcile_closed_row_not_grayed_has_no_diff():
    results, listed, candidates = _gray_fixture(False, status="終了")
    result = reconcile_tabs.reconcile(results, listed, candidates, CONFIG)
    assert result["diffs"] == []


def test_reconcile_unreadable_gray_goes_to_needs_review():
    results, listed, candidates = _gray_fixture(None)
    result = reconcile_tabs.reconcile(results, listed, candidates, CONFIG)
    assert result["diffs"] == []
    assert len(result["needs_review"]) == 1
    review = result["needs_review"][0]
    assert review["kind"] == "not_grayed"
    assert review["reason"]
    assert any(word in review["reason"] for word in ("グレー", "背景", "読み取り"))
    assert result["false_positive_count"] == 1


# ---------------------------------------------------------------------------
# 同名同日の複数行（職種での切り分け）
# ---------------------------------------------------------------------------
def _same_name_same_date_fixture(candidate_position, candidate_gray):
    results = [
        _results_row(
            "テスト商事", "プロジェクトマネージャー", "2026-11-13", "選考中", 2
        ),
        _results_row(
            "テスト商事", "ITコンサルタント", "2026-11-13", "書類選考で不合格", 3
        ),
    ]
    listed = [
        _list_row(
            "テスト商事", "2026-11-13", "選考中（1件）、書類選考で不合格（1件）", 2
        )
    ]
    candidates = [
        _candidate_row(
            "テスト商事",
            "プロジェクトマネージャー",
            "選考中",
            10,
            True,
            applied_at="2026-11-13 09:00",
        ),
        _candidate_row(
            "テスト商事",
            "ITコンサルタント",
            "選考中",
            11,
            True,
            applied_at="2026-11-13 09:00",
        ),
    ]
    candidates.append(
        _candidate_row(
            "テスト商事",
            candidate_position,
            "選考中",
            12,
            candidate_gray,
            applied_at="2026-11-13 10:00",
        )
    )
    return results, listed, candidates


def test_reconcile_candidate_role_disambiguation():
    """職種で一意に定まる候補リスト行は自動判定する。"""
    results, listed, candidates = _same_name_same_date_fixture(
        "プロジェクトマネージャー", False
    )
    result = reconcile_tabs.reconcile(results, listed, candidates, CONFIG)
    not_grayed = _by_kind(result, "not_grayed")
    assert len(not_grayed) == 1
    assert not_grayed[0]["role"] == "プロジェクトマネージャー"
    assert not_grayed[0]["target"] == {"tab": "候補リスト", "row": 12}
    assert result["needs_review"] == []


def test_reconcile_ambiguous_role_goes_to_needs_review():
    """職種がどの選考結果行とも突き合わない場合は自動判定しない。"""
    results, listed, candidates = _same_name_same_date_fixture("未定の職種", False)
    result = reconcile_tabs.reconcile(results, listed, candidates, CONFIG)
    assert _by_kind(result, "not_grayed") == []
    assert len(result["needs_review"]) == 1
    assert result["needs_review"][0]["kind"] == "not_grayed"
    assert result["needs_review"][0]["reason"]


# ---------------------------------------------------------------------------
# 出力の形（diffs / needs_review / summary / false_positive_count）
# ---------------------------------------------------------------------------
def test_reconcile_diff_shape_has_target_before_after():
    results = [
        _results_row(
            "テスト商事", "プロジェクトマネージャー", "2026-11-13", "選考中", 2
        ),
        _results_row("架空工業", "プロジェクトマネージャー", "2026-11-10", "選考中", 3),
        _results_row("架空工業", "ITコンサルタント", "2026-11-10", "選考中", 4),
    ]
    listed = [
        _list_row("架空工業", "2026-11-10", "選考中（1件）", 2),
        _list_row("サンプル物産", "2026-11-11", "選考中", 3),
    ]
    result = reconcile_tabs.reconcile(results, listed, [], CONFIG)
    kinds = {d["kind"] for d in result["diffs"]}
    assert kinds == {"missing_in_list", "result_mismatch", "missing_in_results"}
    for diff in result["diffs"]:
        assert {
            "kind",
            "company",
            "applied_date",
            "role",
            "target",
            "before",
            "after",
            "note",
        } <= set(diff)
        assert set(diff["target"]) == {"tab", "row"}
        assert diff["target"]["tab"] in ("選考結果", "応募済み企業一覧", "候補リスト")
        assert isinstance(diff["target"]["row"], int)
        assert diff["applied_date"] is None or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", diff["applied_date"]
        )
        assert isinstance(diff["note"], str)
    for review in result["needs_review"]:
        assert review["reason"]


def test_reconcile_zero_diffs_summary_contains_no_diff():
    results, listed, candidates = _matching_fixture()
    result = reconcile_tabs.reconcile(results, listed, candidates, CONFIG)
    assert result["diffs"] == []
    assert result["needs_review"] == []
    assert result["false_positive_count"] == 0
    assert "差分なし" in result["summary"]


def test_reconcile_false_positive_count_equals_needs_review_len():
    results = [
        _results_row(
            "テスト商事", "プロジェクトマネージャー", "2026-11-13", "選考中", 2
        ),
        _results_row(
            "サンプル物産", "プロジェクトマネージャー", "2026-11-11", "選考中", 3
        ),
    ]
    listed = [_list_row("サンプル物産", "2026-11-11", "調整中メモ", 2)]
    result = reconcile_tabs.reconcile(results, listed, [], CONFIG)
    assert len(result["diffs"]) == 1
    assert result["diffs"][0]["kind"] == "missing_in_list"
    assert len(result["needs_review"]) == 1
    assert result["false_positive_count"] == len(result["needs_review"]) == 1


def test_reconcile_generated_at_is_jst_iso():
    results, listed, candidates = _matching_fixture()
    result = reconcile_tabs.reconcile(results, listed, candidates, CONFIG)
    from datetime import datetime

    generated = datetime.fromisoformat(result["generated_at"])
    assert generated.tzinfo is not None
    assert generated.utcoffset() == timedelta(hours=9)


# ---------------------------------------------------------------------------
# 読み取り専用・純粋性
# ---------------------------------------------------------------------------
def test_reconcile_makes_no_subprocess_calls(monkeypatch):
    """reconcile は純粋関数で、シート読み取りの subprocess を呼ばない。"""

    def _forbidden_run(*args, **kwargs):
        raise AssertionError("reconcile が subprocess.run を呼んだ")

    def _forbidden_popen(*args, **kwargs):
        raise AssertionError("reconcile が subprocess.Popen を呼んだ")

    monkeypatch.setattr(subprocess, "run", _forbidden_run)
    monkeypatch.setattr(subprocess, "Popen", _forbidden_popen)
    results, listed, candidates = _matching_fixture()
    result = reconcile_tabs.reconcile(results, listed, candidates, CONFIG)
    assert result["diffs"] == []


def test_reconcile_does_not_mutate_inputs():
    results, listed, candidates = _matching_fixture()
    snapshot = (
        copy.deepcopy(results),
        copy.deepcopy(listed),
        copy.deepcopy(candidates),
    )
    reconcile_tabs.reconcile(results, listed, candidates, CONFIG)
    assert (results, listed, candidates) == snapshot


# ---------------------------------------------------------------------------
# CLI と書き込み経路の不在（公開インターフェースの範囲で）
# ---------------------------------------------------------------------------
def test_cli_help_lists_config_and_out_without_write_options(capsys):
    if not hasattr(reconcile_tabs, "main"):
        pytest.fail(
            "未実装: tools/reconcile_tabs.py に main が存在しない。"
            "完了の定義の CLI（--config / --out）がまだ実装されていない。"
        )
    with pytest.raises(SystemExit) as excinfo:
        reconcile_tabs.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--config" in out
    assert "--out" in out
    flags = set(re.findall(r"--[a-z][a-z-]*", out))
    assert flags <= {"--config", "--out", "--help"}


def test_source_contains_no_sheet_write_path():
    """最善努力の確認: Sheets の書き込み呼び出し形がソースに無いこと。

    主眼は公開インターフェースの振る舞い（上記の subprocess 不使用テスト）
    に置き、こちらは書き込み経路の混入を防ぐ補助的なガードである。
    """
    source = _RECONCILE_PATH.read_text(encoding="utf-8")
    assert "batchUpdate" not in source
    assert re.search(r"""["']values["']\s*,\s*["']update["']""", source) is None


# ---------------------------------------------------------------------------
# 設定ファイル（完了の定義 4: ルールは設定ファイルに変数として列挙）
# ---------------------------------------------------------------------------
def test_config_file_contains_required_normalize_rules():
    if not _CONFIG_PATH.exists():
        pytest.fail(
            "未実装: tools/reconcile_tabs_config.json が存在しない。"
            "完了の定義 4 の設定ファイルがまだ無い。"
        )
    data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert isinstance(data.get("rules"), dict)
    rules = data["rules"]
    dumped = json.dumps(rules, ensure_ascii=False)
    assert "株式会社" in dumped
    assert "(株)" in dumped or "（株）" in dumped
    keys = " ".join(str(key) for key in rules).lower()
    assert "space" in keys or "空白" in keys or "whitespace" in keys
    assert "width" in keys or "全角" in keys or "半角" in keys


def test_normalize_company_with_real_config_file():
    if not _CONFIG_PATH.exists():
        pytest.fail(
            "未実装: tools/reconcile_tabs_config.json が存在しない。"
            "完了の定義 4 の設定ファイルがまだ無い。"
        )
    data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    rules = data["rules"]
    assert reconcile_tabs.normalize_company(
        "株式会社テスト商事", rules
    ) == reconcile_tabs.normalize_company("テスト商事", rules)
