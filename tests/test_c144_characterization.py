"""C-144（C-066 残件: employment_allowed を肯定形で配線する）のテスト。

テスト係が実装コード（src/）を見ず、仕様（本人の決定 2026-11-07:
「肯定形を正にして配線する（未記載は『要確認』）」）と公開入口
（evaluate.evaluate_posting、docs/architecture.md §3.16）の現行
入出力の実測だけから書いた。

仕様（判定規則）:
1. 求人の雇用形態が profile.employment_allowed（肯定形リスト）に
   明示されている場合のみ素通りする（雇用形態を理由に要確認に
   倒れない）。
2. 雇用形態が未記載（読み取れない = employment_raw is None。
   §3.8 が HARD-EMPTYPE を発火させない条件として定義する形）の
   場合は、ハード除外ではなく「要確認」に倒す。
3. 雇用形態が明示されているが肯定形リストに無い形態の場合も、
   ハード除外ではなく「要確認」に倒す。
4. 境界: 明示された既知の非正規形態（hard.non_regular_keywords に
   一致するもの）は、従来どおりハード除外のまま変わらない。
   否定形は補助として残る。
5. 設定駆動であること: employment_allowed の中身を変えれば判定が
   変わる（リストに無い形態をリストへ加えると素通りするようになり、
   リストから外すと要確認に倒る）。

前提（実測で確認）: conftest.build_raw_posting の既定求人は、雇用
形態以外の全条件が適合側に倒れている（現行の evaluate_posting で
適合度「高」・kind=ok、要確認理由は「要確認に倒す条件は検出され
なかった」）。したがって本ファイルでは employment_raw と
employment_allowed だけを変え、結果の差は雇用形態の条件だけに
帰着する形にしてある。

本ファイルは 2 部構成:

1. 仕様テスト（test_spec_*）: 規則 2・3・5。現行実装では
   employment_allowed が判定に効いておらず、未記載・リスト外の
   雇用形態が素通りする（kind=ok）ため、**実装前は red になるのが
   正しい**。C-144 の実施で green になるべきテストであり、red の
   まま放置したり現行挙動に合わせて書き換えたりしてはならない。
2. 特性テスト（test_characterization_*）: 規則 1・4。実施前後とも
   green でなければならない安全網であり、現行挙動が「正しい」
   ことの証明ではない。

新しい要確認のルール ID の名前は仕様で確定していないため、仕様
テストは特定の新 ID 名には依存しない。その代わり、architecture.md
§3.9／§5 の「要確認理由は理由文＋ルール ID で必ず残す」という
確定仕様に基づき、要確認理由（fitness_result.check_reasons）の
文面に「雇用形態」が現れることを雇用形態由来の要確認の証跡として
検証する（既存の理由文も要因を名指しする形: 例 HARD-EMPTYPE の
理由「雇用形態が正社員でないことが明確である」）。

規則 3 の「リスト外の形態」の例には「嘱託社員」を使う。これは
フィクスチャの肯定形リスト（["正社員"]）にも、否定形キーワード
既定値（judge フィクスチャ hard.non_regular_keywords =
["契約社員", "派遣", "業務委託", "アルバイト"]）のいずれにも
含まれず、部分文字列としても一致しない表記である（テスト内で
前提として assert する）。設定の既定値は仕様の一部として参照した。

実シート・実設定ファイル（config/local/）には触れない。設定は
conftest のフィクスチャ辞書の deepcopy 変更のみで組み立てる。
"""

from __future__ import annotations

import copy

import pytest
from conftest import (
    build_app_config,
    build_location_whitelist,
    build_raw_posting,
    enum_value,
    impl,
)

# フィクスチャの肯定形リスト（profile_config_dict の既定値）。
_ALLOWED_DEFAULT = ["正社員"]

# 規則 3 の「リスト外の形態」の例（肯定形リストにも否定形
# キーワード既定値にも入っていない表記。docstring 参照）。
_OUT_OF_LIST_FORM = "嘱託社員"


def _evaluate(
    profile_config_dict,
    judge_config_dict,
    location_whitelist_dict,
    employment_raw,
    allowed=None,
):
    """雇用形態と（必要なら）肯定形リストだけを変えて単体判定する。"""
    profile = copy.deepcopy(profile_config_dict)
    if allowed is not None:
        profile["employment_allowed"] = list(allowed)
    cfg = build_app_config(profile, judge_config_dict)
    whitelist = build_location_whitelist(location_whitelist_dict)
    evaluate = impl("job_candidate_list.evaluate.evaluate_posting")
    posting = build_raw_posting(employment_raw=employment_raw)
    return evaluate(posting, cfg, whitelist)


def _assert_passes_without_employment_check(result):
    """素通り（規則 1）の共通検証: ハード除外でも要確認でもなく、
    雇用形態が要確認理由に現れていないことまで含める。"""
    assert result.hard_excluded is False
    assert enum_value(result.kind) == "ok"
    assert result.fitness_result is not None
    assert enum_value(result.fitness_result.fitness) in ("高", "中")
    assert result.exclusions == ()
    assert "HARD-EMPTYPE" not in result.rule_ids
    # 雇用形態が理由で要確認になっていないこと（要確認理由の文面に
    # 雇用形態が現れない）
    assert all(
        "雇用形態" not in reason.text for reason in result.fitness_result.check_reasons
    )


def _assert_check_for_employment(result):
    """雇用形態を理由とする要確認（規則 2・3）の共通検証:
    ハード除外ではなく、適合度「要確認」に倒れ、要確認理由に
    雇用形態が記録されていること。"""
    assert result.hard_excluded is False
    assert enum_value(result.kind) == "check"
    assert result.fitness_result is not None
    assert enum_value(result.fitness_result.fitness) == "要確認"
    # ハード除外ではない（除外ヒットは無く、HARD-EMPTYPE も発火しない）
    assert result.exclusions == ()
    assert "HARD-EMPTYPE" not in result.rule_ids
    # 要確認の理由が雇用形態であることが理由文に残っていること
    assert any(
        "雇用形態" in reason.text for reason in result.fitness_result.check_reasons
    ), (
        "要確認理由に雇用形態への言及が無い: "
        f"{result.fitness_result.check_reasons!r}"
    )


# ---------------------------------------------------------------------------
# 1. 仕様テスト（規則 2・3・5。C-144 実装前は red が正）
# ---------------------------------------------------------------------------
def test_spec_missing_employment_is_check_not_hard_nor_ok(
    profile_config_dict, judge_config_dict, location_whitelist_dict
):
    """仕様テスト（規則 2・実装前は red が正）: 雇用形態が未記載
    （employment_raw is None）の求人は、ハード除外されず、雇用形態を
    理由に適合度「要確認」に倒れること。

    現行実装では未記載でも素通りする（kind=ok・適合度「高」）ため
    red になる。対照として、同じ求人で雇用形態だけ「正社員」に
    変えると素通りすることもあわせて確認し、結果を分けるのが
    雇用形態の条件だけであることを担保する。
    """
    baseline = _evaluate(
        profile_config_dict,
        judge_config_dict,
        location_whitelist_dict,
        "正社員",
    )
    _assert_passes_without_employment_check(baseline)

    result = _evaluate(
        profile_config_dict,
        judge_config_dict,
        location_whitelist_dict,
        None,
    )
    _assert_check_for_employment(result)


def test_spec_out_of_list_form_is_check_not_hard_nor_ok(
    profile_config_dict, judge_config_dict, location_whitelist_dict
):
    """仕様テスト（規則 3・実装前は red が正）: 雇用形態が明示されて
    いるが肯定形リストに無い形態（例: 嘱託社員）の求人は、ハード
    除外されず、雇用形態を理由に適合度「要確認」に倒れること。

    現行実装ではリスト外の形態も素通りする（kind=ok）ため red に
    なる。前提として、例示形態が肯定形リストにも否定形キーワード
    既定値にも一致しないことを先に固定する（一致すれば規則 4 の
    ハード除外側の話になり、本テストの対象でなくなるため）。
    """
    # 前提: 例示形態は肯定形リストに無く、否定形キーワードの
    # いずれとも（部分文字列としても）一致しない。
    assert _OUT_OF_LIST_FORM not in profile_config_dict["employment_allowed"]
    for keyword in judge_config_dict["hard"]["non_regular_keywords"]:
        assert keyword not in _OUT_OF_LIST_FORM

    result = _evaluate(
        profile_config_dict,
        judge_config_dict,
        location_whitelist_dict,
        _OUT_OF_LIST_FORM,
    )
    _assert_check_for_employment(result)


def test_spec_adding_form_to_allowed_makes_it_pass(
    profile_config_dict, judge_config_dict, location_whitelist_dict
):
    """仕様テスト（規則 5・実装前は red が正）: 設定駆動であること
    （追加側）。肯定形リストに無い形態の求人は要確認に倒れるが、
    その形態を employment_allowed へ加えた設定では、同じ求人が
    素通りするようになること。

    現行実装ではリストに無い形態も最初から素通りするため、
    「リスト無しでは要確認」の assert で red になる。
    """
    without = _evaluate(
        profile_config_dict,
        judge_config_dict,
        location_whitelist_dict,
        _OUT_OF_LIST_FORM,
        allowed=_ALLOWED_DEFAULT,
    )
    _assert_check_for_employment(without)

    with_added = _evaluate(
        profile_config_dict,
        judge_config_dict,
        location_whitelist_dict,
        _OUT_OF_LIST_FORM,
        allowed=_ALLOWED_DEFAULT + [_OUT_OF_LIST_FORM],
    )
    _assert_passes_without_employment_check(with_added)


def test_spec_removing_form_from_allowed_makes_it_check(
    profile_config_dict, judge_config_dict, location_whitelist_dict
):
    """仕様テスト（規則 5・実装前は red が正）: 設定駆動であること
    （除外側）。雇用形態「正社員」の求人は、肯定形リストに「正社員」
    を含む設定では素通りするが、「正社員」を外した設定では雇用形態
    を理由に要確認に倒れること（肯定形リストの中身そのものが判定を
    決め、「正社員」という文字列への固定判定ではないこと）。

    現行実装では employment_allowed の中身によらず素通りするため、
    「外した設定では要確認」の assert で red になる。
    """
    with_form = _evaluate(
        profile_config_dict,
        judge_config_dict,
        location_whitelist_dict,
        "正社員",
        allowed=_ALLOWED_DEFAULT,
    )
    _assert_passes_without_employment_check(with_form)

    without_form = _evaluate(
        profile_config_dict,
        judge_config_dict,
        location_whitelist_dict,
        "正社員",
        allowed=[_OUT_OF_LIST_FORM],
    )
    _assert_check_for_employment(without_form)


# ---------------------------------------------------------------------------
# 2. 特性テスト（規則 1・4。実施前後とも green）
# ---------------------------------------------------------------------------
def test_characterization_allowed_form_passes_without_employment_check(
    profile_config_dict, judge_config_dict, location_whitelist_dict
):
    """特性テスト（規則 1）: 雇用形態が肯定形リストに明示されている
    求人（「正社員」・リスト ["正社員"]）は、雇用形態を理由に要確認
    に倒れず素通りすること（適合度は現行どおり「高」）。

    雇用形態以外の条件は全て適合側に倒れた既定求人であるため、
    ここで要確認に倒れれば原因は雇用形態しかあり得ない。
    C-144 実施前後とも green でなければならない。
    """
    result = _evaluate(
        profile_config_dict,
        judge_config_dict,
        location_whitelist_dict,
        "正社員",
    )
    _assert_passes_without_employment_check(result)
    assert enum_value(result.fitness_result.fitness) == "高"


@pytest.mark.parametrize(
    "form",
    [
        "契約社員",  # 否定形キーワードそのもの
        "派遣社員",  # 否定形キーワード「派遣」を含む表記
        "業務委託",  # 否定形キーワードそのもの
        "アルバイト",  # 否定形キーワードそのもの
    ],
)
def test_characterization_known_non_regular_forms_stay_hard_excluded(
    form, profile_config_dict, judge_config_dict, location_whitelist_dict
):
    """特性テスト（規則 4・境界）: 明示された既知の非正規形態
    （否定形キーワードに一致）は、従来どおり HARD-EMPTYPE で
    ハード除外され、適合度付けは行われないこと。

    肯定形リスト（["正社員"]）に無い形態でも、規則 3 の要確認では
    なくハード除外のままである点が境界になる。C-144 実施前後とも
    green でなければならない。
    """
    result = _evaluate(
        profile_config_dict,
        judge_config_dict,
        location_whitelist_dict,
        form,
    )
    assert result.hard_excluded is True
    assert enum_value(result.kind) == "hard"
    assert result.fitness_result is None
    emptype_hits = [h for h in result.exclusions if h.rule_id == "HARD-EMPTYPE"]
    assert emptype_hits, f"HARD-EMPTYPE が発火していない: {result.exclusions!r}"
    assert emptype_hits[0].config_ref == "hard.non_regular_keywords"
    assert "HARD-EMPTYPE" in result.rule_ids


def test_characterization_non_regular_stays_hard_even_if_listed_in_allowed(
    profile_config_dict, judge_config_dict, location_whitelist_dict
):
    """特性テスト（規則 4・境界の固定）: 否定形キーワードに一致する
    形態は、肯定形リスト側に同名が入っている設定でも、従来どおり
    ハード除外のまま変わらないこと（否定形は補助として残り、
    肯定形の配線でハード除外が要確認に緩まない）。

    現行挙動でもこの形（hard 側の判定が先に立つ）であり、C-144
    実施前後とも green でなければならない。
    """
    result = _evaluate(
        profile_config_dict,
        judge_config_dict,
        location_whitelist_dict,
        "契約社員",
        allowed=["正社員", "契約社員"],
    )
    assert result.hard_excluded is True
    assert enum_value(result.kind) == "hard"
    assert result.fitness_result is None
    assert any(h.rule_id == "HARD-EMPTYPE" for h in result.exclusions)
