"""完了の定義③: 検証用求人サンプル39件の期待結果。

根拠: 企画書「完了の定義」③、architecture.md §11.2。
期待結果は本ファイル（テスト側）にのみ持ち、サンプル実体には書かない。

期待結果の表現:
- ("hard", {ルールID,...}) : ハード除外され除外ログ行き。本体混入0。
- ("check", {ルールID,...}): ハード除外されず適合度「要確認」。週次提示外。
- ("ok", {ルールID,...})   : ハード除外されない（適合度は高/中/要確認のいずれか
                             になりうるが、少なくとも記載ルールIDが発火する）。

注: §3 には「サンプルテキスト→RawPosting 化＋一括判定」の公開入口が定義されて
いないため、エンドツーエンドの判定検証は入口の確定後に有効化する
（仕様確認事項として最終報告に記載）。サンプル実体の完全性検証は
実装なしで今すぐ検証できる。
"""

from __future__ import annotations

import pytest

from conftest import SAMPLES_DIR, load_sample, sample_field

# sample_id -> (分類コード, 期待種別, 期待ルールID集合)
EXPECTED: dict[str, tuple[str, str, frozenset]] = {
    # 基本カテゴリ
    "SMP-001": ("basic_emptype", "hard", frozenset({"HARD-EMPTYPE"})),
    "SMP-002": ("basic_emptype", "hard", frozenset({"HARD-EMPTYPE"})),
    "SMP-003": ("basic_emptype", "hard", frozenset({"HARD-EMPTYPE"})),
    "SMP-004": ("basic_domain", "hard", frozenset({"HARD-DOMAIN"})),
    "SMP-005": ("basic_domain", "hard", frozenset({"HARD-DOMAIN"})),
    "SMP-006": ("basic_domain", "hard", frozenset({"HARD-DOMAIN"})),
    "SMP-007": ("basic_bizform", "hard", frozenset({"HARD-BIZFORM"})),
    "SMP-008": ("basic_bizform", "hard", frozenset({"HARD-BIZFORM"})),
    "SMP-009": ("basic_bizform", "hard", frozenset({"HARD-BIZFORM"})),
    # (a) 主務が明確に実装等
    "SMP-010": ("a_impl", "hard", frozenset({"JOB-IMPL-HARD"})),
    "SMP-011": ("a_impl", "hard", frozenset({"JOB-IMPL-HARD"})),
    "SMP-012": ("a_impl", "hard", frozenset({"JOB-IMPL-HARD"})),
    "SMP-013": ("a_impl", "hard", frozenset({"JOB-IMPL-HARD"})),
    "SMP-014": ("a_impl", "hard", frozenset({"JOB-IMPL-HARD"})),
    # (b) 肩書きと主務の不一致
    "SMP-015": ("b_mismatch", "hard", frozenset({"JOB-IMPL-HARD"})),
    "SMP-016": ("b_mismatch", "hard", frozenset({"JOB-IMPL-HARD"})),
    "SMP-017": ("b_mismatch", "ok", frozenset({"JOB-UPSTREAM-OK"})),
    "SMP-018": ("b_mismatch", "ok", frozenset({"JOB-UPSTREAM-OK"})),
    # (c) 主務不明または混在
    "SMP-019": ("c_unknown_mixed", "check", frozenset({"JOB-UNKNOWN"})),
    "SMP-020": ("c_unknown_mixed", "check", frozenset({"JOB-MIXED"})),
    "SMP-021": ("c_unknown_mixed", "check",
                frozenset({"JOB-UNKNOWN", "JOB-DESIGN-ONLY"})),
    # (d) 主務が明確な上流職
    "SMP-022": ("d_upstream", "ok", frozenset({"JOB-UPSTREAM-OK"})),
    "SMP-023": ("d_upstream", "ok", frozenset({"JOB-UPSTREAM-OK"})),
    "SMP-024": ("d_upstream", "ok", frozenset({"JOB-UPSTREAM-OK"})),
    # (e) アーキテクト境界
    "SMP-025": ("e_architect", "ok", frozenset({"JOB-UPSTREAM-OK"})),
    "SMP-026": ("e_architect", "hard", frozenset({"JOB-IMPL-HARD"})),
    # (f) 勤務地・リモート境界（勤務地の内/外はテスト側フィクスチャ対応表で決定）
    "SMP-027": ("f_location_remote", "ok", frozenset({"LOC-OK"})),
    "SMP-028": ("f_location_remote", "check", frozenset({"LOC-OUT"})),
    "SMP-029": ("f_location_remote", "ok",
                frozenset({"LOC-FULLREMOTE-EXEMPT"})),
    "SMP-030": ("f_location_remote", "check", frozenset({"LOC-UNKNOWN"})),
    "SMP-031": ("f_location_remote", "ok",
                frozenset({"REMOTE-NORMALIZED"})),
    "SMP-032": ("f_location_remote", "check",
                frozenset({"REMOTE-UNKNOWN", "LOC-UNKNOWN"})),
    # (g) 英語要件境界
    "SMP-033": ("g_english", "hard", frozenset({"ENG-HARD"})),
    "SMP-034": ("g_english", "ok", frozenset({"ENG-WELCOME-OK"})),
    "SMP-035": ("g_english", "check", frozenset({"ENG-UNKNOWN"})),
    "SMP-036": ("g_english", "hard", frozenset({"ENG-HARD"})),
    "SMP-037": ("g_english", "hard", frozenset({"ENG-HARD"})),
    "SMP-038": ("g_english", "check", frozenset({"ENG-UNKNOWN"})),
    "SMP-039": ("g_english", "check",
                frozenset({"ENG-UNKNOWN", "ENG-OUT-SECTION"})),
}

CATEGORY_COUNTS = {
    "basic_emptype": 3,
    "basic_domain": 3,
    "basic_bizform": 3,
    "a_impl": 5,
    "b_mismatch": 4,
    "c_unknown_mixed": 3,
    "d_upstream": 3,
    "e_architect": 2,
    "f_location_remote": 6,
    "g_english": 7,
}


def test_sample_total_count_is_39():
    files = sorted(SAMPLES_DIR.glob("SMP-*.md"))
    assert len(files) == 39, f"サンプル総数が39件でない: {len(files)}"


@pytest.mark.parametrize("code,count", sorted(CATEGORY_COUNTS.items()))
def test_category_counts_match_classification_design(code, count):
    """§11.2 の分類設計どおりの件数（重複カウントなし）であること。"""
    actual = [sid for sid, v in EXPECTED.items() if v[0] == code]
    assert len(actual) == count
    files = [f for f in SAMPLES_DIR.glob("SMP-*.md")
             if load_sample(f.name.split("_")[0]) and
             sample_field(load_sample(f.name.split("_")[0]), "分類コード") == code]
    assert len(files) == count


@pytest.mark.parametrize("sample_id", sorted(EXPECTED))
def test_sample_contains_judgement_elements(sample_id):
    """各サンプルに判定に必要な要素が実務的な文面で含まれていること。"""
    text = load_sample(sample_id)
    for key in ("会社名", "ポジション名", "雇用形態", "情報源URL",
                "想定年収", "勤務地", "リモート頻度（原文）"):
        assert sample_field(text, key), f"{sample_id}: {key} が空"
    for heading in ("業務内容", "応募資格", "勤務地", "会社概要"):
        assert f"## {heading}" in text, f"{sample_id}: セクション {heading} が無い"
    company = sample_field(text, "会社名")
    assert company.startswith("株式会社サンプル"), (
        f"{sample_id}: 会社名が架空名の形式でない: {company}")


@pytest.mark.parametrize("sample_id", sorted(EXPECTED))
def test_sample_expected_outcome(sample_id):
    """サンプルを判定系の公開インターフェースに通し期待結果と一致すること。

    現時点では §3 にサンプル→RawPosting 化と一括判定の公開入口がないため、
    入口未確定として失敗させる（仕様確認事項）。
    """
    code, kind, rule_ids = EXPECTED[sample_id]
    text = load_sample(sample_id)
    assert sample_field(text, "分類コード") == code
    pytest.fail(
        f"未検証（仕様確認事項）: {sample_id} の期待結果は {kind} / {sorted(rule_ids)} "
        "だが、architecture.md §3 には求人テキストを RawPosting 化して"
        "ハード除外・適合度まで一括判定する公開入口が定義されていない。"
        "pipeline.run_collection は SheetsGateway と情報源アダプタを前提とするため、"
        "サンプル単体検証の入口確定後に本テストを有効化すること。"
    )


def test_f_samples_use_fixture_municipalities():
    """(f) の勤務地はテストフィクスチャで内/外が決まる架空名であること。"""
    assert "サンプル甲市" in load_sample("SMP-027")  # 対応表内（想定）
    assert "サンプル乙市" in load_sample("SMP-028")  # 対応表外（想定）
    assert "サンプル遠方市" in load_sample("SMP-029")


def test_exclusion_log_traceability_fields_are_specified():
    """除外ログから「どの設定を直せば判定が変わるか」が追えること（③末尾）。

    §3.8 の ExclusionHit は config_ref を含み、§10.3 の除外ログに
    設定参照列がある。実装側の確認は judge_hard の戻り値で行う。
    """
    from conftest import impl

    judge = impl("job_candidate_list.judge_hard.judge_hard_exclusions")
    assert judge is not None
    pytest.fail("未検証: RawPosting / HardConfig の生成方法が未確定のため、"
                "ExclusionHit.config_ref の実値を確認できない")
