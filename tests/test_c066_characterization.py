"""C-066（JCL-08）特性テスト: 「効かない設定」10件の現行挙動の固定。

処遇表（調査係レポート job-candidate-list-C066-dispositions.md）に基づく。
前提は「挙動不変」: 削除2件（profile_version /
location_condition_enabled）と残置8件のどちらについても、収集・判定・
提示の挙動は現行と同一でなければならない。本ファイルは実装を見ずに、
公開インターフェース（evaluate_sample_text / judge_main_duty /
run_collection / attach_openwork / load_config / AppConfig.from_dicts /
validate_config）の入出力だけから現行挙動を固定する。

固定する観点:
1. 設定の無効性: 対象10件の値を変えても判定・収集の出力が同一である
   こと。例外として、employment_allowed（空チェック）と
   validity.listing_valid_days（1以上チェック）は validate_config の
   検証結果だけが変わる現行挙動もあわせて固定する（処遇表の所見どおり、
   検証のみ・収集経路では呼ばれない）。
2. 設定読込: 同梱テンプレート（config/templates/*.example.yaml）と
   フィクスチャ設定が読み込め、残置8件の実効値が現行どおりであること。
3. 後方互換: 削除2件のキーを含む設定辞書は現行ではエラーにならず
   値が読み取れること。一方、セクション内の未知キーに対しては現行は
   厳格（ConfigError）であり、その現状も固定する。削除実装の際に
   フィールドだけを消すと、削除2件を含む既存ローカル設定は
   「未知のキー」で読込エラーになる点に注意（所見として報告済み）。

既存テストとの対応（重複回避）:
- キーの存在自体は test_config_and_rules.py の REQUIRED_*_KEYS が固定
  済みのため、本ファイルでは「値の実効性・無効性」だけを見る。
- クラス単位 from_dict の未知キーエラーは
  test_c068_characterization.py が固定済みのため、本ファイルでは
  AppConfig.from_dicts / load_config という上位の読込経路で見る。
- 有効な設定変更（提示上限・年収閾値・上流キーワード・対応表）で
  挙動が変わる側は test_config_and_rules.py が固定済み。
"""

from __future__ import annotations

import copy
import datetime as dt

import pytest
import yaml
from conftest import (
    REPO_ROOT,
    FakeOpenworkProvider,
    build_app_config,
    build_candidate_record,
    build_location_whitelist,
    build_raw_posting,
    impl,
    load_sample,
    run_fake_collection,
)

FIXED_FETCHED_ON = dt.date(2026, 11, 5)


def _evaluate(sample_id, cfg, whitelist):
    evaluate = impl("job_candidate_list.evaluate.evaluate_sample_text")
    return evaluate(load_sample(sample_id), cfg, whitelist, fetched_on=FIXED_FETCHED_ON)


# ---------------------------------------------------------------------------
# 1. 設定の無効性（profile 側6件）: 値を変えても一括判定の結果が同一
# ---------------------------------------------------------------------------
# 各変種は「もし効いていれば結果が変わるはず」の値を選んでいる:
# - profile_version: 版数に連動する分岐があれば変わるはずの大きな値
# - target_role_labels: サンプルの肩書き（プロダクトマネージャー）を
#   含まないラベル群。ラベル照合があれば落ちるはず
# - employment_allowed: サンプルの雇用形態は「正社員」。許可リストが
#   効けば ["契約社員"] では除外されるはず
# - remote_preference: サンプルのリモート原文は「出社週2〜3日」。
#   希望との適合条件化があれば適合度が変わるはず
# - location_condition_enabled: False で勤務地条件が外れるはず
# - location_hard_exclude: True で勤務地不適合（SMP-028）がハード
#   除外になるはず
_PROFILE_VARIANTS = [
    ("profile_version", 999),
    ("target_role_labels", ["全く別の肩書き例"]),
    ("employment_allowed", ["契約社員"]),
    ("remote_preference", "フルリモート希望"),
    ("location_condition_enabled", False),
    ("location_hard_exclude", True),
]


@pytest.mark.parametrize("key,value", _PROFILE_VARIANTS)
@pytest.mark.parametrize("sample_id", ["SMP-022", "SMP-028"])
def test_profile_setting_does_not_change_evaluation(
    key,
    value,
    sample_id,
    profile_config_dict,
    judge_config_dict,
    location_whitelist_dict,
):
    """現行挙動: profile の対象キーを変えても EvaluationResult は同一。

    SMP-022 は ok（勤務地適合）、SMP-028 は check（勤務地不適合）の
    代表サンプル。結果全体（種別・ルールID・理由・適合度・提示順キー）
    の等価で比較する。
    """
    whitelist = build_location_whitelist(location_whitelist_dict)
    base_cfg = build_app_config(profile_config_dict, judge_config_dict)
    base = _evaluate(sample_id, base_cfg, whitelist)

    changed_profile = copy.deepcopy(profile_config_dict)
    changed_profile[key] = value
    changed_cfg = build_app_config(changed_profile, judge_config_dict)
    changed = _evaluate(sample_id, changed_cfg, whitelist)

    assert changed == base, f"{key}={value!r} で判定結果が変わった"


# ---------------------------------------------------------------------------
# 1. 設定の無効性（judge 側: duty.title_assist_keywords）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("variant", [[], ["サンプル未使用語"]])
def test_title_assist_keywords_do_not_change_duty_result(judge_config_dict, variant):
    """現行挙動: 肩書き補助キーワードを変えても主務判定は同一。

    本文には基準設定の補助キーワード（マネージャー／アーキテクト）が
    実際に現れる文面を使う。仕様 §3.5 の「補助情報として理由に残す」
    が未実装である現行挙動として、理由文まで含めて同一であることを
    固定する（結果全体の等価で比較）。
    """
    judge = impl("job_candidate_list.judge_duty.judge_main_duty")
    duty_config = impl("job_candidate_list.config.DutyConfig")
    sections = {
        "業務内容": (
            "マネージャーとしてデータ活用の企画立案を主担当として担います。"
            "アーキテクトとも連携します。"
        )
    }
    base = judge(sections, duty_config.from_dict(judge_config_dict["duty"]))
    changed = judge(
        sections,
        duty_config.from_dict(
            dict(judge_config_dict["duty"], title_assist_keywords=variant)
        ),
    )
    assert changed == base


# ---------------------------------------------------------------------------
# 1. 設定の無効性（judge 側: validity.listing_valid_days）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("days", [1, 9999])
def test_listing_valid_days_does_not_change_collection_closing(
    days, profile_config_dict, judge_config_dict
):
    """現行挙動: 掲載確認の有効日数を変えても掲載終了の判定は同一。

    前日に掲載確認した既存候補が今回の収集に無ければ、日数が
    9999（十分に大きい）でも即・掲載終了になる（日数方式ではなく
    「収集に現れなければ終了」の即時方式が現行挙動）。
    """
    recent = dt.date.today() - dt.timedelta(days=1)
    existing = [build_candidate_record("cand_0000000000000001", confirmed_on=recent)]
    postings = [build_raw_posting()]

    base_summary, _ = run_fake_collection(
        profile_config_dict, judge_config_dict, existing=existing, postings=postings
    )
    judge = copy.deepcopy(judge_config_dict)
    judge["validity"]["listing_valid_days"] = days
    summary, _ = run_fake_collection(
        profile_config_dict, judge, existing=existing, postings=postings
    )

    assert summary == base_summary
    assert summary.closed_ids == ("cand_0000000000000001",)


# ---------------------------------------------------------------------------
# 1. 設定の無効性（judge 側: collection.request_interval_sec）
# ---------------------------------------------------------------------------
def test_request_interval_sec_does_not_change_collection(
    profile_config_dict, judge_config_dict
):
    """現行挙動: アクセス間隔を変えても収集の出力は同一。

    間隔の執行（待機処理）自体が無いため、サマリ・書込候補・除外
    ログのいずれも変わらない。所要時間は見ない（現行に待機が無い
    ことは出力同一性で固定し、時間計測は不安定なため使わない）。
    """
    postings = [
        build_raw_posting(),
        build_raw_posting(raw_url="https://example.invalid/j/2"),
    ]
    base_summary, base_gateway = run_fake_collection(
        profile_config_dict, judge_config_dict, postings=postings
    )
    judge = copy.deepcopy(judge_config_dict)
    judge["collection"]["request_interval_sec"] = 999
    summary, gateway = run_fake_collection(
        profile_config_dict, judge, postings=postings
    )

    assert summary == base_summary
    assert gateway.upserted == base_gateway.upserted
    assert gateway.exclusions == base_gateway.exclusions


# ---------------------------------------------------------------------------
# 1. 設定の無効性（judge 側: openwork.weekly_once_per_company）
# ---------------------------------------------------------------------------
def _ow_ref():
    ref_cls = impl("job_candidate_list.openwork.OpenworkCompanyRef")
    return ref_cls(
        company_norm="サンプル青葉",
        score=3.5,
        url="https://example.invalid/openwork/1",
        location_text="サンプル甲市",
        industry_text=None,
    )


def test_weekly_once_per_company_does_not_change_openwork_attach():
    """現行挙動: 週1回フラグを変えても OpenWork 付与は同一で、
    同一会社への再取得も抑止されない（会社単位の共有・キャッシュが
    呼び出し側にも attach 側にも無い、という現行挙動の固定）。"""
    attach = impl("job_candidate_list.openwork.attach_openwork")
    openwork_config = impl("job_candidate_list.config.OpenworkConfig")
    candidate = build_candidate_record(
        "cand_0000000000000001",
        company="株式会社サンプル青葉",
        company_norm="サンプル青葉",
        location_text="サンプル甲市",
        fitness="中",
    )

    results = {}
    for flag in (True, False):
        provider = FakeOpenworkProvider(results={"サンプル青葉": [_ow_ref()]})
        cfg = openwork_config.from_dict({"weekly_once_per_company": flag})
        first = attach(candidate, provider, True, cfg)
        second = attach(candidate, provider, True, cfg)
        # フラグによらず、同一会社でも呼ぶたびに provider を引く
        assert provider.calls == ["サンプル青葉", "サンプル青葉"]
        results[flag] = (first, second)

    assert results[True] == results[False]


# ---------------------------------------------------------------------------
# 1'. 無効性の例外（現行挙動）: validate_config の検証だけは効く2件
# ---------------------------------------------------------------------------
def test_validate_config_still_checks_employment_allowed_nonempty(
    profile_config_dict, judge_config_dict
):
    """現行挙動: employment_allowed は空だと validate_config が問題を
    返すが、値そのもの（何を許可するか）は検証・判定に影響しない。"""
    validate = impl("job_candidate_list.config.validate_config")

    empty_profile = copy.deepcopy(profile_config_dict)
    empty_profile["employment_allowed"] = []
    problems = validate(build_app_config(empty_profile, judge_config_dict))
    assert "profile.employment_allowed が空です" in problems

    other_profile = copy.deepcopy(profile_config_dict)
    other_profile["employment_allowed"] = ["契約社員"]
    assert validate(build_app_config(other_profile, judge_config_dict)) == []


def test_validate_config_still_checks_listing_valid_days_positive(
    profile_config_dict, judge_config_dict
):
    """現行挙動: listing_valid_days は 1 未満だと validate_config が
    問題を返す（検証のみ。収集・判定への使用は無い）。"""
    validate = impl("job_candidate_list.config.validate_config")
    judge = copy.deepcopy(judge_config_dict)
    judge["validity"]["listing_valid_days"] = 0
    problems = validate(build_app_config(profile_config_dict, judge))
    assert "validity.listing_valid_days は 1 以上が必要です" in problems


# ---------------------------------------------------------------------------
# 2. 設定読込の固定: 残置8件の実効値
# ---------------------------------------------------------------------------
def test_template_files_load_and_retained_values():
    """同梱テンプレート3ファイルが load_config で読み込め、残置8件の
    実効値がテンプレートの現行値どおりであること。"""
    load_config = impl("job_candidate_list.config.load_config")
    templates = REPO_ROOT / "config" / "templates"
    cfg = load_config(
        templates / "profile.example.yaml",
        templates / "judge.example.yaml",
        templates / "local.example.yaml",
    )
    # profile 側（残置4件）
    assert cfg.profile.target_role_labels == ("<対象職種の肩書き例>",)
    assert cfg.profile.employment_allowed == ("<許可する雇用形態>",)
    assert cfg.profile.remote_preference is None
    assert cfg.profile.location_hard_exclude is False
    # judge 側（残置4件）
    assert cfg.duty.title_assist_keywords == (
        "<職種名の補助キーワード（単独では判定を決定しない）>",
    )
    assert cfg.validity.listing_valid_days == 14
    assert cfg.collection.request_interval_sec == 5
    assert cfg.openwork.weekly_once_per_company is True


def test_fixture_config_effective_values_for_retained_keys(
    profile_config_dict, judge_config_dict, local_config_dict
):
    """フィクスチャ設定を AppConfig.from_dicts で読み込んだときの
    残置8件の実効値が現行どおりであること（読込経路はテンプレートの
    ファイル経路と別に、辞書経路でも固定する）。"""
    cfg = build_app_config(profile_config_dict, judge_config_dict, local_config_dict)
    assert cfg.profile.target_role_labels == (
        "PM",
        "マネジメント",
        "企画・推進",
        "アーキテクト",
    )
    assert cfg.profile.employment_allowed == ("正社員",)
    assert cfg.profile.remote_preference is None
    assert cfg.profile.location_hard_exclude is False
    assert cfg.duty.title_assist_keywords == (
        "マネージャー",
        "エンジニア",
        "アーキテクト",
    )
    assert cfg.validity.listing_valid_days == 14
    assert cfg.collection.request_interval_sec == 5
    assert cfg.openwork.weekly_once_per_company is True


# ---------------------------------------------------------------------------
# 3. 後方互換の固定: 削除2件を含む辞書と、未知キーへの現行の厳格さ
# ---------------------------------------------------------------------------
def test_dict_with_keys_to_be_deleted_loads_without_error(
    profile_config_dict, judge_config_dict, local_config_dict
):
    """現行挙動: 削除予定2件（profile_version /
    location_condition_enabled）を含む設定辞書はエラーにならず、
    値がそのまま読み取れる（既存ローカル設定の形）。"""
    profile = copy.deepcopy(profile_config_dict)
    profile["profile_version"] = 7
    profile["location_condition_enabled"] = False
    cfg = build_app_config(profile, judge_config_dict, local_config_dict)
    assert cfg.profile.profile_version == 7
    assert cfg.profile.location_condition_enabled is False


def test_unknown_key_in_profile_dict_is_config_error_at_from_dicts(
    profile_config_dict, judge_config_dict, local_config_dict
):
    """現行挙動（厳格）: profile 辞書内の未知キーは AppConfig.from_dicts
    でも ConfigError。削除2件も、フィールド削除後はこの経路で
    「未知のキー」になる、という現行の寛容さの無さの固定。"""
    app_config = impl("job_candidate_list.config.AppConfig")
    errors = impl("job_candidate_list.errors")
    profile = copy.deepcopy(profile_config_dict)
    profile["__unknown_key__"] = 1
    with pytest.raises(errors.ConfigError) as excinfo:
        app_config.from_dicts(profile, judge_config_dict, local_config_dict)
    assert "__unknown_key__" in str(excinfo.value)


def test_unknown_key_in_profile_yaml_is_config_error_at_load_config(
    tmp_path, profile_config_dict, judge_config_dict, local_config_dict
):
    """現行挙動（厳格）: YAML ファイル経由（load_config）でも、
    profile 内の未知キーは ConfigError（実ファイル読込経路の固定）。
    storage は tmp_path のみを使い、実運用データには触れない。"""
    load_config = impl("job_candidate_list.config.load_config")
    errors = impl("job_candidate_list.errors")
    profile = copy.deepcopy(profile_config_dict)
    profile["__unknown_key__"] = 1
    paths = {}
    for name, data in (
        ("profile.local.yaml", profile),
        ("judge.local.yaml", judge_config_dict),
        ("local.local.yaml", local_config_dict),
    ):
        path = tmp_path / name
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        paths[name] = path
    with pytest.raises(errors.ConfigError) as excinfo:
        load_config(
            paths["profile.local.yaml"],
            paths["judge.local.yaml"],
            paths["local.local.yaml"],
        )
    assert "__unknown_key__" in str(excinfo.value)
