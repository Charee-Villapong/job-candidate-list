"""完了の定義③: 検証用求人サンプル39件の期待結果。

根拠: 企画書「完了の定義」③、architecture.md §11.2。
期待結果は本ファイル（テスト側）にのみ持ち、サンプル実体には書かない。

期待結果の表現:
- ("hard", {ルールID,...}) : ハード除外され除外ログ行き。本体混入0。
- ("check", {ルールID,...}): ハード除外されず適合度「要確認」。週次提示外。
- ("ok", {ルールID,...})   : ハード除外されない（適合度は高/中/要確認のいずれか
                             になりうるが、少なくとも記載ルールIDが発火する）。

注: 判定入口は追補 A1 で確定した `evaluate.evaluate_sample_text`
（サンプルテキスト→RawPosting 化＋一括判定、純粋関数）を使う。
戻り値 EvaluationResult の種別（kind）・発火ルール ID・適合度で検証する。
"""

from __future__ import annotations

import pytest
from conftest import (
    SAMPLES_DIR,
    build_app_config,
    build_location_whitelist,
    enum_value,
    load_sample,
    sample_field,
)

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
    "SMP-021": (
        "c_unknown_mixed",
        "check",
        frozenset({"JOB-UNKNOWN", "JOB-DESIGN-ONLY"}),
    ),
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
    "SMP-029": ("f_location_remote", "ok", frozenset({"LOC-FULLREMOTE-EXEMPT"})),
    "SMP-030": ("f_location_remote", "check", frozenset({"LOC-UNKNOWN"})),
    "SMP-031": ("f_location_remote", "ok", frozenset({"REMOTE-NORMALIZED"})),
    "SMP-032": (
        "f_location_remote",
        "check",
        frozenset({"REMOTE-UNKNOWN", "LOC-UNKNOWN"}),
    ),
    # (g) 英語要件境界
    "SMP-033": ("g_english", "hard", frozenset({"ENG-HARD"})),
    "SMP-034": ("g_english", "ok", frozenset({"ENG-WELCOME-OK"})),
    "SMP-035": ("g_english", "check", frozenset({"ENG-UNKNOWN"})),
    "SMP-036": ("g_english", "hard", frozenset({"ENG-HARD"})),
    "SMP-037": ("g_english", "hard", frozenset({"ENG-HARD"})),
    "SMP-038": ("g_english", "check", frozenset({"ENG-UNKNOWN"})),
    "SMP-039": ("g_english", "check", frozenset({"ENG-UNKNOWN", "ENG-OUT-SECTION"})),
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
    files = [
        f
        for f in SAMPLES_DIR.glob("SMP-*.md")
        if load_sample(f.name.split("_")[0])
        and sample_field(load_sample(f.name.split("_")[0]), "分類コード") == code
    ]
    assert len(files) == count


@pytest.mark.parametrize("sample_id", sorted(EXPECTED))
def test_sample_contains_judgement_elements(sample_id):
    """各サンプルに判定に必要な要素が実務的な文面で含まれていること。"""
    text = load_sample(sample_id)
    for key in (
        "会社名",
        "ポジション名",
        "雇用形態",
        "情報源URL",
        "想定年収",
        "勤務地",
        "リモート頻度（原文）",
    ):
        assert sample_field(text, key), f"{sample_id}: {key} が空"
    for heading in ("業務内容", "応募資格", "勤務地", "会社概要"):
        assert f"## {heading}" in text, f"{sample_id}: セクション {heading} が無い"
    company = sample_field(text, "会社名")
    assert company.startswith(
        "株式会社サンプル"
    ), f"{sample_id}: 会社名が架空名の形式でない: {company}"


@pytest.mark.parametrize("sample_id", sorted(EXPECTED))
def test_sample_expected_outcome(
    sample_id,
    judge_config_dict,
    profile_config_dict,
    local_config_dict,
    location_whitelist_dict,
):
    """サンプルを判定系の公開インターフェースに通し期待結果と一致すること。

    判定入口は追補 A1 の evaluate_sample_text。期待種別は
    EvaluationResult.kind（hard／check／ok、追補 A1.2）と対応させる。
    """
    from conftest import impl

    code, kind, rule_ids = EXPECTED[sample_id]
    text = load_sample(sample_id)
    assert sample_field(text, "分類コード") == code
    evaluate = impl("job_candidate_list.evaluate.evaluate_sample_text")
    cfg = build_app_config(profile_config_dict, judge_config_dict, local_config_dict)
    whitelist = build_location_whitelist(location_whitelist_dict)
    result = evaluate(text, cfg, whitelist)

    assert enum_value(result.kind) == kind, (
        f"{sample_id}: 種別が期待 {kind} と異なる "
        f"(rule_ids={list(result.rule_ids)})"
    )
    assert result.hard_excluded is (kind == "hard")
    assert rule_ids <= set(result.rule_ids), (
        f"{sample_id}: 期待ルール {sorted(rule_ids)} が発火していない "
        f"(実際: {list(result.rule_ids)})"
    )
    if kind == "hard":
        # ハード除外時は適合度付けを行わない（追補 A1.3 手順6）
        assert result.fitness_result is None
    elif kind == "check":
        assert result.fitness_result is not None
        assert enum_value(result.fitness_result.fitness) == "要確認"
    else:  # ok
        assert result.fitness_result is not None
        assert enum_value(result.fitness_result.fitness) in ("高", "中")


def test_f_samples_use_fixture_municipalities():
    """(f) の勤務地はテストフィクスチャで内/外が決まる架空名であること。"""
    assert "サンプル甲市" in load_sample("SMP-027")  # 対応表内（想定）
    assert "サンプル乙市" in load_sample("SMP-028")  # 対応表外（想定）
    assert "サンプル遠方市" in load_sample("SMP-029")


def test_exclusion_log_traceability_fields_are_specified(judge_config_dict):
    """除外ログから「どの設定を直せば判定が変わるか」が追えること（③末尾）。

    §3.8 の ExclusionHit は config_ref を含み、§10.3 の除外ログに
    設定参照列がある。実装側の確認は judge_hard の戻り値で行う。
    HardConfig は追補 A2.2 の from_dict で生成する。
    """
    from conftest import build_raw_posting, impl

    judge = impl("job_candidate_list.judge_hard.judge_hard_exclusions")
    hard_config = impl("job_candidate_list.config.HardConfig")
    posting = build_raw_posting(employment_raw="契約社員")
    hits = judge(posting, hard_config.from_dict(judge_config_dict["hard"]))
    emptype_hits = [h for h in hits if h.rule_id == "HARD-EMPTYPE"]
    assert emptype_hits, f"HARD-EMPTYPE が発火していない: {hits}"
    # どの設定を直せば判定が変わるかが config_ref で追えること
    assert emptype_hits[0].config_ref == "hard.non_regular_keywords"
    assert all(h.config_ref.startswith("hard.") for h in hits)
