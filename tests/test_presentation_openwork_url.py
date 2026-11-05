"""完了の定義⑨: 提示上限・控え／OpenWork付与／URL表示テキスト付きリンク。

根拠: 企画書「完了の定義」⑨、architecture.md §3.2 / §3.11 / §3.12 / §3.14 /
§6.6 / §6.7 / §7 / §10.1。
"""

from __future__ import annotations

import datetime as dt

import pytest

from conftest import FakeOpenworkProvider, impl


def _record(candidate_id: str, **over):
    models = impl("job_candidate_list.models")
    try:
        return models.CandidateRecord(candidate_id=candidate_id, **over)
    except TypeError as exc:
        pytest.fail(
            "未検証（仕様確認事項）: CandidateRecord の生成方法"
            f"（必須引数・デフォルト）が §2.2 で未確定のため構築できない: {exc}"
        )


# ---------------------------------------------------------------------------
# 提示上限と控え
# ---------------------------------------------------------------------------
def test_weekly_limit_default_is_20():
    """提示上限Nの初期値は20（§4.2 presentation.weekly_limit）。"""
    load = impl("job_candidate_list.config.load_config")
    assert load is not None
    pytest.fail("未検証（仕様確認事項）: 設定テンプレートの実ファイルと"
                "AppConfig のフィールド構造が未確定のため、初期値20を"
                "読み取って確認できない")


def test_collection_is_not_capped_only_presentation_is():
    """収集・本体記録には上限がかからず、提示のみが上限Nに絞られる（⑨(i)(ii)）。"""
    split = impl("job_candidate_list.presentation.split_presentation")
    candidates = [_record(f"cand_{i:016d}") for i in range(25)]
    presented, held = split(candidates, 20)
    assert len(presented) == 20
    assert len(held) == 5
    assert {c.candidate_id for c in presented} | {c.candidate_id for c in held} \
        == {c.candidate_id for c in candidates}, "控えの欠落がある"


def test_held_high_fitness_beats_new_medium_for_last_slot():
    """合流ソート: 適合度「高」の控えが「中」の新規より先に提示される（⑨(ii)）。"""
    split = impl("job_candidate_list.presentation.split_presentation")
    assert split is not None
    pytest.fail("未検証（仕様確認事項）: CandidateRecord に適合度・提示順キー"
                "（§3.9 presentation_key）を持たせる方法が未確定のため、"
                "「高の控え vs 中の新規」のケースを構築できない")


def test_no_double_presentation_across_weeks():
    """提示済み候補が翌週に二重提示されない（⑨(ii)）。"""
    split = impl("job_candidate_list.presentation.split_presentation")
    assert split is not None
    pytest.fail("未検証（仕様確認事項）: CandidateRecord の提示区分フィールドと"
                "split_presentation の対象条件（未提示候補のみ）の実装前提が"
                "未確定のため、二重提示ゼロを構築して確認できない")


def test_held_candidates_are_included_in_duplicate_check():
    """控えも重複判定の対象に含まれる（§7・⑨(ii)）。"""
    cid = impl("job_candidate_list.normalize.candidate_id")
    assert cid("サンプル青葉", "PM", "https://example.invalid/j/1") == \
        cid("サンプル青葉", "PM", "https://example.invalid/j/1")
    pytest.fail("未検証（仕様確認事項）: 行レベルの重複排除を行う公開入口が"
                "§3 で未確定のため、控えを含む重複判定を実挙動で確認できない")


def test_closed_held_candidate_moves_to_closed_not_presented():
    """控えのまま掲載終了した候補は提示されず「掲載終了」へ倒れる（⑨(ii)）。"""
    recheck = impl("job_candidate_list.presentation.recheck_held")
    held = _record("cand_0000000000000001")
    decision = recheck(held, None, None)  # latest=None は掲載終了を表す想定
    assert str(getattr(decision, "value", decision)) in ("closed", "HeldDecision.closed")


def test_recheck_held_requires_three_conditions():
    """繰り上げ直前の再確認は3点（掲載継続／再判定でも高・中／対象外でない）。"""
    recheck = impl("job_candidate_list.presentation.recheck_held")
    assert recheck is not None
    pytest.fail("未検証（仕様確認事項）: recheck_held の引数 refitness の型"
                "（Fitness か FitnessResult か）が §3.12 で一意に定まらず、"
                "3条件のケースを構築できない")


# ---------------------------------------------------------------------------
# OpenWork 付与
# ---------------------------------------------------------------------------
def test_openwork_gate_disabled_never_calls_provider():
    """ゲート不可の場合は取得を試みず全件空欄（⑨(iii)・§3.14）。"""
    attach = impl("job_candidate_list.openwork.attach_openwork")
    provider = FakeOpenworkProvider()
    candidate = _record("cand_0000000000000001")
    result = attach(candidate, provider, False, _ow_cfg())
    assert provider.calls == [], "ゲート無効なのに provider が呼ばれた"
    assert result.score is None and result.url is None \
        and result.fetched_on is None
    assert "OW-DISABLED" in list(result.rule_ids)


def _ow_cfg():  # pragma: no cover - 実装後に有効化
    pytest.fail("未検証（仕様確認事項）: OpenworkConfig の生成方法が未確定")


def test_openwork_nomatch_returns_blank_and_keeps_fitness():
    """突合不能は空欄で、点数が適合度を変えない（⑨(iii)・§6.7）。"""
    attach = impl("job_candidate_list.openwork.attach_openwork")
    assert attach is not None
    pytest.fail("未検証（仕様確認事項）: OpenworkCompanyRef の構造が §3.14 で"
                "未確定のため、複数候補が残る突合不能ケースを構築できない")


def test_openwork_refetch_failure_keeps_previous_values():
    """再取得失敗時は前回値を保持し、取得日は前回のまま（⑨(iii)・§6.7）。"""
    attach = impl("job_candidate_list.openwork.attach_openwork")
    assert attach is not None
    pytest.fail("未検証（仕様確認事項）: attach_openwork の引数に前回値"
                "（既存の OpenWork 3項目）を渡す欄がなく、保持責務が"
                "pipeline 側か attach 側か §3.14 で未確定のため検証できない")


def test_openwork_success_records_fetched_date():
    """取得できた全件で取得日が記録される（⑨(iii)）。"""
    attach = impl("job_candidate_list.openwork.attach_openwork")
    assert attach is not None
    pytest.fail("未検証（仕様確認事項）: OpenworkCompanyRef の構造が未確定のため、"
                "突合成功ケースを構築できない")


def test_openwork_blocked_raises_and_stops_attachment():
    """403/429 等では OpenworkBlocked が送出され付与が停止する（§3.14）。"""
    openwork = impl("job_candidate_list.openwork")
    assert hasattr(openwork, "OpenworkBlocked") or hasattr(
        impl("job_candidate_list.errors"), "OpenworkBlocked"
    ), "OpenworkBlocked の置き場（openwork.py か errors か）が仕様どおりでない"


# ---------------------------------------------------------------------------
# URL の表示テキスト付きリンク
# ---------------------------------------------------------------------------
def test_link_cell_display_texts_are_fixed():
    """情報源URLの表示テキストは「求人ページ」、OpenWorkは「OpenWork」固定（§3.11/§10.1）。"""
    gw = impl("job_candidate_list.sheets_gateway")
    link = gw.LinkCell(display_text="求人ページ", url="https://example.invalid/j/1")
    assert link.display_text == "求人ページ"
    assert link.url == "https://example.invalid/j/1"


def test_url_change_fallback_keeps_candidate_id_and_apply_state():
    """URL変更時もフォールバック突合で候補ID不変・状態引き継ぎ（⑨(iv)・§7）。"""
    key = impl("job_candidate_list.normalize.fallback_match_key")
    assert key("サンプル青葉", "PM") == key("サンプル青葉", "PM")
    pytest.fail("未検証（仕様確認事項）: 既存行との突合・引き継ぎを行う"
                "公開入口が §3 で未確定（§7 は手順のみ）のため、候補ID不変と"
                "応募希望マーク・応募状態の引き継ぎを実挙動で確認できない")


def test_presentation_limit_change_is_reflected_from_config(judge_config_dict):
    """提示上限の設定変更がコード無変更で提示件数に反映される（⑨(i)・①）。"""
    assert judge_config_dict["presentation"]["weekly_limit"] == 20
    changed = dict(judge_config_dict["presentation"], weekly_limit=5)
    assert changed["weekly_limit"] == 5
    split = impl("job_candidate_list.presentation.split_presentation")
    assert split is not None
    pytest.fail("未検証: CandidateRecord の生成方法が未確定のため、"
                "上限5での振り分け件数を確認できない")
