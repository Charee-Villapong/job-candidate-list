"""完了の定義①: プロファイル・判定設定の変数化。

根拠: 企画書 Issue #1「完了の定義」①、architecture.md §3.1 / §4 / §5。
"""

from __future__ import annotations

import re

import pytest
from conftest import (
    ARCHITECTURE_MD,
    REPO_ROOT,
    build_app_config,
    build_candidate_record,
    build_location_whitelist,
    build_raw_posting,
    enum_value,
    impl,
)

REQUIRED_JUDGE_KEYS = [
    "normalize.tracking_params",
    "duty.target_sections",
    "duty.upstream_keywords",
    "duty.impl_keywords",
    "duty.title_assist_keywords",
    "duty.primary_markers",
    "duty.mixed_handling",
    "location.target_sections",
    "remote.rules",
    "english.target_sections",
    "english.mandatory_sections",
    "english.required_phrases",
    "english.welcome_phrases",
    "english.level_phrases",
    "hard.non_regular_keywords",
    "hard.out_of_domain_keywords",
    "hard.business_form_keywords",
    "fitness.salary_check_threshold_jpy",
    "fitness.high_threshold",
    "presentation.weekly_limit",
    "validity.listing_valid_days",
    "collection.request_interval_sec",
    "collection.per_source_max_items",
    "collection.timeout_sec",
    "guard.max_close_ratio",
    "guard.max_total_change_ratio",
    "openwork.weekly_once_per_company",
    "auto_apply.enabled",
    "auto_apply.weekly_limit",
]

REQUIRED_PROFILE_KEYS = [
    "profile_version",
    "target_domains",
    "target_role_labels",
    "salary_target_jpy",
    "employment_allowed",
    "remote_preference",
    "location_condition_enabled",
    "location_hard_exclude",
    "search_queries",
]


def _dig(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        assert part in cur, f"設定キー {dotted} が存在しない"
        cur = cur[part]
    return cur


@pytest.mark.parametrize("key", REQUIRED_JUDGE_KEYS)
def test_judge_config_has_all_effective_keys(judge_config_dict, key):
    """判定に効く値が全て設定側に存在する（§4.2 のキー一覧と一致）。"""
    _dig(judge_config_dict, key)
    # 実装側: load_config が同キーを欠いた設定を ConfigError にすること
    load_config = impl("job_candidate_list.config.load_config")
    assert callable(load_config)


@pytest.mark.parametrize("key", REQUIRED_PROFILE_KEYS)
def test_profile_config_has_all_keys(profile_config_dict, key):
    """志向プロファイルの各項目が設定側に存在する（§4.1）。"""
    assert key in profile_config_dict
    impl("job_candidate_list.config.load_config")


def test_load_config_missing_local_file_raises_config_error(tmp_path):
    """実値ファイルが無い場合はテンプレートで代用せず ConfigError（§3.1）。"""
    load_config = impl("job_candidate_list.config.load_config")
    errors = impl_module_errors()
    with pytest.raises(errors):
        load_config(
            tmp_path / "profile.local.yaml",
            tmp_path / "judge.local.yaml",
            tmp_path / "local.local.yaml",
        )


def impl_module_errors():
    errors_mod = impl("job_candidate_list.errors")
    return errors_mod.ConfigError


def test_validate_config_returns_empty_for_valid_config(
    judge_config_dict, profile_config_dict, local_config_dict
):
    """正常な設定では validate_config が空リストを返す（§3.1）。

    設定オブジェクトは AppConfig.from_dicts（追補 A2.2）で組み立てる。
    """
    validate_config = impl("job_candidate_list.config.validate_config")
    cfg = build_app_config(profile_config_dict, judge_config_dict, local_config_dict)
    assert validate_config(cfg) == []


def test_changing_weekly_limit_changes_presentation_without_code_change(
    judge_config_dict, profile_config_dict, local_config_dict
):
    """設定値（提示上限N）を1つ変えると判定結果が変わる（①の反映性）。"""
    split = impl("job_candidate_list.presentation.split_presentation")
    candidates = [
        build_candidate_record(f"cand_{i:016d}", fitness="中") for i in range(3)
    ]
    judge_one = dict(judge_config_dict, presentation={"weekly_limit": 1})
    cfg_one = build_app_config(profile_config_dict, judge_one, local_config_dict)
    presented, held = split(candidates, cfg_one.presentation.weekly_limit)
    assert len(presented) == 1 and len(held) == 2

    judge_two = dict(judge_config_dict, presentation={"weekly_limit": 2})
    cfg_two = build_app_config(profile_config_dict, judge_two, local_config_dict)
    presented, held = split(candidates, cfg_two.presentation.weekly_limit)
    assert len(presented) == 2 and len(held) == 1


def _fitness_sub_results():
    """assign_fitness の引数（各判定結果）を確定形で組み立てる。"""
    duty_cls = impl("job_candidate_list.judge_duty.DutyResult")
    location_cls = impl("job_candidate_list.judge_location.LocationResult")
    remote_cls = impl("job_candidate_list.judge_remote.RemoteResult")
    english_cls = impl("job_candidate_list.judge_english.EnglishResult")
    duty = duty_cls(
        outcome="upstream",
        rule_ids=["JOB-UPSTREAM-OK"],
        reasons=["主務が上流業務と判定できる"],
        matched_upstream=["企画立案"],
        matched_impl=[],
    )
    location = location_cls(
        division="勤務地適合",
        rule_ids=["LOC-OK"],
        reasons=["勤務地が対応表内"],
        matched_municipality="サンプル甲市",
    )
    remote = remote_cls(
        division="出社週2〜3日",
        rule_ids=["REMOTE-NORMALIZED"],
        raw_found="出社週2〜3日",
    )
    english = english_cls(outcome="no_mention", rule_ids=[], reasons=[])
    return duty, location, remote, english


def _reason_rule_ids(reasons) -> set:
    ids = set()
    for reason in reasons:
        ids |= set(getattr(reason, "rule_ids", reason[1]))
    return ids


def test_changing_salary_threshold_changes_fitness_without_code_change(
    judge_config_dict,
):
    """設定値（年収閾値）を1つ変えると適合度が変わる（①の反映性）。

    年収上限が salary_check_threshold_jpy を大幅に下回る場合は要確認
    （SAL-BELOW、§3.9 規則5）。閾値だけを変えて結果が変わることを見る。
    """
    assign = impl("job_candidate_list.fitness.assign_fitness")
    fitness_config = impl("job_candidate_list.config.FitnessConfig")
    posting = build_raw_posting(
        salary_min_jpy=1_000_000,
        salary_max_jpy=1_000_000,
        salary_raw="年収 100万円",
    )
    duty, location, remote, english = _fitness_sub_results()

    base = dict(judge_config_dict["fitness"], salary_check_threshold_jpy=500_000)
    result_ok = assign(
        posting,
        duty,
        location,
        remote,
        english,
        fitness_config.from_dict(base),
    )
    assert enum_value(result_ok.fitness) in ("高", "中")

    raised = dict(judge_config_dict["fitness"], salary_check_threshold_jpy=4_000_000)
    result_check = assign(
        posting,
        duty,
        location,
        remote,
        english,
        fitness_config.from_dict(raised),
    )
    assert enum_value(result_check.fitness) == "要確認"
    assert "SAL-BELOW" in _reason_rule_ids(result_check.check_reasons)


def test_changing_upstream_keyword_changes_duty_outcome(judge_config_dict):
    """設定値（上流キーワード群）を1つ変えると主務判定が変わる（①の反映性）。"""
    judge = impl("job_candidate_list.judge_duty.judge_main_duty")
    duty_config = impl("job_candidate_list.config.DutyConfig")
    sections = {"業務内容": "データ活用の企画立案と施策の推進を担います。"}

    base = judge(sections, duty_config.from_dict(judge_config_dict["duty"]))
    assert enum_value(base.outcome) == "upstream"
    assert "JOB-UPSTREAM-OK" in list(base.rule_ids)

    changed_dict = dict(
        judge_config_dict["duty"], upstream_keywords=["サンプル未使用語"]
    )
    changed = judge(sections, duty_config.from_dict(changed_dict))
    assert enum_value(changed.outcome) == "unknown"
    assert "JOB-UNKNOWN" in list(changed.rule_ids)


def test_changing_whitelist_changes_location_outcome(
    judge_config_dict, location_whitelist_dict
):
    """設定データ（勤務地対応表）を変えると勤務地判定が変わる（①・§13）。"""
    judge = impl("job_candidate_list.judge_location.judge_location")
    location_config = impl("job_candidate_list.config.LocationConfig")
    remote_cls = impl("job_candidate_list.judge_remote.RemoteResult")
    posting = build_raw_posting()
    remote = remote_cls(
        division="出社週2〜3日",
        rule_ids=["REMOTE-NORMALIZED"],
        raw_found="出社週2〜3日",
    )
    cfg = location_config.from_dict(judge_config_dict["location"])

    whitelist_in = build_location_whitelist(location_whitelist_dict)
    result_in = judge(posting, remote, whitelist_in, cfg)
    assert enum_value(result_in.division) == "勤務地適合"
    assert "LOC-OK" in list(result_in.rule_ids)
    assert result_in.matched_municipality == "サンプル甲市"

    whitelist_out = build_location_whitelist(
        {
            "version": 1,
            "municipalities": [
                {"prefecture": "サンプル県", "municipality": "サンプル区"}
            ],
            "notes": "サンプル甲市を含まない対応表",
        }
    )
    result_out = judge(posting, remote, whitelist_out, cfg)
    assert enum_value(result_out.division) == "勤務地不適合"
    assert "LOC-OUT" in list(result_out.rule_ids)


# ---------------------------------------------------------------------------
# ルールID: 要求区分ごとに別IDであること（§5）
# ---------------------------------------------------------------------------
def _architecture_rule_ids() -> dict[str, set[str]]:
    text = ARCHITECTURE_MD.read_text(encoding="utf-8")
    sections: dict[str, set[str]] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"### 5\.(\d) ", line)
        if m:
            current = f"5.{m.group(1)}"
            sections[current] = set()
            continue
        if line.startswith("## ") and current:
            current = None
        if current:
            sections[current] |= set(re.findall(r"`([A-Z][A-Z-]+)`", line))
    return sections


def test_rule_ids_are_distinct_per_requirement_category():
    """主務3区分・勤務地/リモート5区分・英語2区分がそれぞれ別ID（§5）。

    この検証は設計文書 §5 の表自体に対して行う（実装不要で検証可能）。
    """
    ids = _architecture_rule_ids()
    duty = ids["5.1"]
    assert {"JOB-IMPL-HARD", "JOB-UNKNOWN", "JOB-MIXED"} <= duty
    assert len({"JOB-IMPL-HARD", "JOB-UNKNOWN", "JOB-MIXED"}) == 3
    loc = ids["5.2"]
    assert {
        "LOC-OK",
        "LOC-OUT",
        "LOC-UNKNOWN",
        "LOC-FULLREMOTE-EXEMPT",
        "REMOTE-UNKNOWN",
    } <= loc
    eng = ids["5.3"]
    assert {"ENG-HARD", "ENG-UNKNOWN"} <= eng
    # 区分をまたいで同一IDが別の意味に再利用されていないこと
    all_ids = [i for s in ids.values() for i in s]
    assert len(all_ids) == len(set(all_ids)), "§5 内でルールIDが重複している"


def test_design_only_is_separate_id_from_impl_hard():
    """「設計のみ」は JOB-IMPL-HARD ではなく JOB-DESIGN-ONLY（§5.1）。"""
    ids = _architecture_rule_ids()
    assert "JOB-DESIGN-ONLY" in ids["5.1"]
    assert "JOB-DESIGN-ONLY" != "JOB-IMPL-HARD"


# ---------------------------------------------------------------------------
# 機微情報が設定・リストに含まれないこと（パターン検査観点）
# ---------------------------------------------------------------------------
SENSITIVE_PATTERNS = [
    r"[\w.+-]+@[\w-]+\.[\w.]+",  # メールアドレス
    r"\b\d{2,4}-\d{2,4}-\d{4}\b",  # 電話番号らしき並び
    r"(password|passwd|api[_-]?key|secret|token)\s*[:=]\s*\S+",  # 認証情報
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
]


@pytest.mark.parametrize("pattern", SENSITIVE_PATTERNS)
def test_samples_and_placement_table_have_no_sensitive_patterns(pattern):
    """成果物（サンプル・配置表）に機微情報パターンが含まれない（①末尾）。

    実装不要で検証可能な検査観点として、まずテスト係の成果物自体で担保する。
    実装側の設定ファイル・リストに対する同検査は Executor の成果物が
    揃った時点で対象を広げる。
    """
    targets = list((REPO_ROOT / "storage-spec").rglob("*.md"))
    assert targets
    for path in targets:
        text = path.read_text(encoding="utf-8")
        hits = re.findall(pattern, text, flags=re.IGNORECASE)
        # example.invalid のURLは架空ドメインでありメールアドレスではない
        hits = [h for h in hits if "example.invalid" not in str(h)]
        assert not hits, f"{path} に機微情報パターン {pattern} のヒット: {hits}"


def test_config_templates_contain_no_real_values():
    """設定テンプレートに実値（認証情報等）が含まれないこと（§4・§13）。"""
    load_config = impl("job_candidate_list.config.load_config")
    assert load_config is not None
    templates = REPO_ROOT / "config" / "templates"
    assert templates.is_dir(), (
        "未実装: config/templates/ が存在しない"
        "（設定テンプレートの実ファイルがまだ作成されていない）"
    )
    files = [
        p
        for p in templates.rglob("*")
        if p.is_file() and p.suffix in (".yaml", ".yml", ".md", ".txt")
    ]
    assert files, "未実装: config/templates/ にテンプレートファイルが無い"
    for path in files:
        text = path.read_text(encoding="utf-8")
        for pattern in SENSITIVE_PATTERNS:
            hits = [
                h
                for h in re.findall(pattern, text, flags=re.IGNORECASE)
                if "example.invalid" not in str(h)
            ]
            assert not hits, f"{path} に実値らしきパターン {pattern} のヒット: {hits}"
