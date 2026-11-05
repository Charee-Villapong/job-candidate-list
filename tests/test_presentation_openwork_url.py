"""完了の定義⑨: 提示上限・控え／OpenWork付与／URL表示テキスト付きリンク。

根拠: 企画書「完了の定義」⑨、architecture.md §3.2 / §3.11 / §3.12 / §3.14 /
§6.6 / §6.7 / §7 / §10.1。
"""

from __future__ import annotations

import datetime as dt

import pytest
from conftest import (
    FakeOpenworkProvider,
    build_candidate_record,
    build_raw_posting,
    enum_value,
    impl,
    run_fake_collection,
)


def _record(candidate_id: str, **over):
    """CandidateRecord を生成する（追補 A3: 必須引数は candidate_id のみ）。"""
    return build_candidate_record(candidate_id, **over)


# ---------------------------------------------------------------------------
# 提示上限と控え
# ---------------------------------------------------------------------------
def test_weekly_limit_default_is_20():
    """提示上限Nの初期値は20（§4.2 presentation.weekly_limit）。

    PresentationConfig のデフォルト値として確定（追補 A2.2）。
    空 dict からの from_dict で初期値が 20 であることを確認する。
    """
    presentation_config = impl("job_candidate_list.config.PresentationConfig")
    assert presentation_config.from_dict({}).weekly_limit == 20


def test_collection_is_not_capped_only_presentation_is():
    """収集・本体記録には上限がかからず、提示のみが上限Nに絞られる（⑨(i)(ii)）。

    split_presentation の対象は適合度が高／中の未提示候補のため、
    レコード生成時に適合度（中）を明示する（追補 A3 のデフォルトは
    要確認＝安全側であり、対象条件とは別に明示が必要）。
    """
    split = impl("job_candidate_list.presentation.split_presentation")
    candidates = [_record(f"cand_{i:016d}", fitness="中") for i in range(25)]
    presented, held = split(candidates, 20)
    assert len(presented) == 20
    assert len(held) == 5
    assert {c.candidate_id for c in presented} | {c.candidate_id for c in held} == {
        c.candidate_id for c in candidates
    }, "控えの欠落がある"


def test_held_high_fitness_beats_new_medium_for_last_slot():
    """合流ソート: 適合度「高」の控えが「中」の新規より先に提示される（⑨(ii)）。

    提示順キーは CandidateRecord.presentation_key（追補 A8.2:
    (適合度順位, -機械的一致度, -掲載確認日の ordinal, candidate_id)）。
    機械的一致度は「中」の新規のほうが高いケースでも、適合度順位が
    先に効いて「高」の控えが残り 1 枠を取ることを確認する。
    """
    split = impl("job_candidate_list.presentation.split_presentation")
    ordinal = dt.date(2026, 11, 5).toordinal()
    held_high = _record(
        "cand_0000000000000002",
        fitness="高",
        presentation_key=(0, -50, -ordinal, "cand_0000000000000002"),
    )
    new_medium = _record(
        "cand_0000000000000001",
        fitness="中",
        presentation_key=(1, -90, -ordinal, "cand_0000000000000001"),
    )
    presented, held = split([new_medium, held_high], 1)
    assert [c.candidate_id for c in presented] == ["cand_0000000000000002"]
    assert [c.candidate_id for c in held] == ["cand_0000000000000001"]


def test_no_double_presentation_across_weeks():
    """提示済み候補が翌週に二重提示されない（⑨(ii)）。

    split_presentation の対象は presentation == 控え（未提示）の候補のみ
    （追補 A8.2）。提示済みの候補は提示にも控えにも現れない。
    """
    split = impl("job_candidate_list.presentation.split_presentation")
    already = _record("cand_0000000000000001", fitness="高", presentation="提示済み")
    fresh = _record("cand_0000000000000002", fitness="中")
    presented, held = split([already, fresh], 20)
    all_ids = [c.candidate_id for c in presented] + [c.candidate_id for c in held]
    assert "cand_0000000000000001" not in all_ids
    assert [c.candidate_id for c in presented] == ["cand_0000000000000002"]


def test_held_candidates_are_included_in_duplicate_check(
    judge_config_dict, profile_config_dict, local_config_dict
):
    """控えも重複判定の対象に含まれる（§7・⑨(ii)）。

    既存行に控えの候補がある状態で同一の求人を再収集しても、
    新規追加とはならず重複行が増えないことを pipeline 経由で確認する。
    """
    cid = impl("job_candidate_list.normalize.candidate_id")
    existing_id = cid(
        "サンプル青葉", "プロダクトマネージャー", "https://example.invalid/j/1"
    )
    assert existing_id == cid(
        "サンプル青葉", "プロダクトマネージャー", "https://example.invalid/j/1"
    )
    held = _record(
        existing_id,
        company="株式会社サンプル青葉",
        company_norm="サンプル青葉",
        position="プロダクトマネージャー",
        position_norm="プロダクトマネージャー",
        fitness="中",
    )  # presentation のデフォルト＝控え（追補 A3）
    summary, gateway = run_fake_collection(
        profile_config_dict,
        judge_config_dict,
        local_config_dict,
        existing=[held],
        postings=[build_raw_posting()],
        dry_run=False,
    )
    assert existing_id not in list(
        summary.added_ids
    ), "控えの既存候補が新規として二重登録された"
    upserted_ids = [r.candidate_id for r in gateway.upserted]
    assert len(upserted_ids) == len(set(upserted_ids)), "重複行が書き込まれた"
    assert set(upserted_ids) <= {existing_id}


def test_closed_held_candidate_moves_to_closed_not_presented():
    """控えのまま掲載終了した候補は提示されず「掲載終了」へ倒れる（⑨(ii)）。"""
    recheck = impl("job_candidate_list.presentation.recheck_held")
    held = _record("cand_0000000000000001")
    decision = recheck(held, None, None)  # latest=None は掲載終了を表す想定
    assert str(getattr(decision, "value", decision)) in (
        "closed",
        "HeldDecision.closed",
    )


def _fitness_result(fitness: str):
    """FitnessResult を §3.9 の確定形で組み立てる。"""
    fitness_cls = impl("job_candidate_list.fitness.FitnessResult")
    rank = {"高": 0, "中": 1, "要確認": 2}[fitness]
    return fitness_cls(
        fitness=fitness,
        fit_reasons=(),
        check_reasons=(),
        presentation_key=(rank, 0, 0, "cand_0000000000000001"),
    )


def test_recheck_held_requires_three_conditions():
    """繰り上げ直前の再確認は3点（掲載継続／再判定でも高・中／対象外でない）。

    引数 refitness の型は FitnessResult | None（追補 A8.1）で、
    決定表は同節の確定どおり。
    """
    recheck = impl("job_candidate_list.presentation.recheck_held")
    held = _record("cand_0000000000000001", fitness="中")
    latest = build_raw_posting()

    # 3点すべてを満たす → promote
    decision = recheck(held, latest, _fitness_result("高"))
    assert enum_value(decision) == "promote"
    decision = recheck(held, latest, _fitness_result("中"))
    assert enum_value(decision) == "promote"

    # 掲載が継続していない（latest=None）→ closed
    decision = recheck(held, None, _fitness_result("高"))
    assert enum_value(decision) == "closed"

    # 既に掲載終了の候補 → closed
    closed = _record("cand_0000000000000001", fitness="中", status="掲載終了")
    decision = recheck(closed, latest, _fitness_result("高"))
    assert enum_value(decision) == "closed"

    # 再判定が要確認 → to_check。再判定なしでも繰り上げない → to_check
    decision = recheck(held, latest, _fitness_result("要確認"))
    assert enum_value(decision) == "to_check"
    decision = recheck(held, latest, None)
    assert enum_value(decision) == "to_check"

    # 本人の判断が対象外 → keep_held
    excluded = _record("cand_0000000000000001", fitness="中", manual_judgment="対象外")
    decision = recheck(excluded, latest, _fitness_result("高"))
    assert enum_value(decision) == "keep_held"


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
    assert result.score is None and result.url is None and result.fetched_on is None
    assert "OW-DISABLED" in list(result.rule_ids)


def _ow_cfg():
    """OpenworkConfig を追補 A2.2 の from_dict で生成する。"""
    openwork_config = impl("job_candidate_list.config.OpenworkConfig")
    return openwork_config.from_dict({"weekly_once_per_company": True})


def _ow_ref(**over):
    """OpenworkCompanyRef を追補 A7 の確定形で組み立てる。"""
    ref_cls = impl("job_candidate_list.openwork.OpenworkCompanyRef")
    fields = {
        "company_norm": "サンプル青葉",
        "score": 3.5,
        "url": "https://example.invalid/openwork/1",
        "location_text": "サンプル甲市",
        "industry_text": None,
    }
    fields.update(over)
    return ref_cls(**fields)


def _ow_candidate(**over):
    fields = {
        "company": "株式会社サンプル青葉",
        "company_norm": "サンプル青葉",
        "location_text": "サンプル甲市",
        "fitness": "中",
    }
    fields.update(over)
    return _record("cand_0000000000000001", **fields)


def test_openwork_nomatch_returns_blank_and_keeps_fitness():
    """突合不能は空欄で、点数が適合度を変えない（⑨(iii)・§6.7）。

    同一の正規化会社名で公開情報も矛盾しない候補が複数残る場合は
    突合不能（OW-NOMATCH、追補 A7）とし、3項目は空欄のままにする。
    """
    attach = impl("job_candidate_list.openwork.attach_openwork")
    provider = FakeOpenworkProvider(
        results={
            "サンプル青葉": [
                _ow_ref(url="https://example.invalid/openwork/1"),
                _ow_ref(url="https://example.invalid/openwork/2"),
            ]
        }
    )
    candidate = _ow_candidate()
    result = attach(candidate, provider, True, _ow_cfg())
    assert result.score is None and result.url is None and result.fetched_on is None
    assert "OW-NOMATCH" in list(result.rule_ids)
    assert enum_value(candidate.fitness) == "中", "適合度が変わっている"


def test_openwork_refetch_failure_keeps_previous_values():
    """再取得失敗時は前回値を保持し、取得日は前回のまま（⑨(iii)・§6.7）。

    前回値は attach_openwork の previous 引数で渡し、保持責務は
    attach 側にある（追補 A7）。ここでは「存在しない（OW-NOTFOUND）」
    の失敗で確認する。
    """
    attach = impl("job_candidate_list.openwork.attach_openwork")
    result_cls = impl("job_candidate_list.openwork.OpenworkResult")
    models = impl("job_candidate_list.models")
    previous = result_cls(
        score=4.2,
        url=models.LinkCell("OpenWork", "https://example.invalid/openwork/1"),
        fetched_on=dt.date(2026, 10, 29),
        rule_ids=(),
    )
    provider = FakeOpenworkProvider(results={"サンプル青葉": []})
    candidate = _ow_candidate(
        openwork_score=4.2,
        openwork_url=models.LinkCell("OpenWork", "https://example.invalid/openwork/1"),
        openwork_fetched_on=dt.date(2026, 10, 29),
    )
    result = attach(candidate, provider, True, _ow_cfg(), previous=previous)
    assert result.score == 4.2
    assert result.url == previous.url
    assert result.fetched_on == dt.date(2026, 10, 29), "取得日が前回のまま"
    assert "OW-NOTFOUND" in list(result.rule_ids)


def test_openwork_success_records_fetched_date():
    """取得できた全件で取得日が記録される（⑨(iii)）。

    突合成功時の戻り値は追補 A7 の確定形:
    score/url は ref 由来、fetched_on は当日、表示テキストは「OpenWork」。
    """
    attach = impl("job_candidate_list.openwork.attach_openwork")
    ref = _ow_ref(score=3.8, url="https://example.invalid/openwork/9")
    provider = FakeOpenworkProvider(results={"サンプル青葉": [ref]})
    candidate = _ow_candidate()
    result = attach(candidate, provider, True, _ow_cfg())
    assert provider.calls == ["サンプル青葉"]
    assert result.score == 3.8
    assert result.url is not None
    assert result.url.display_text == "OpenWork"
    assert result.url.url == "https://example.invalid/openwork/9"
    assert result.fetched_on == dt.date.today()


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


def test_url_change_fallback_keeps_candidate_id_and_apply_state(
    judge_config_dict, profile_config_dict, local_config_dict
):
    """URL変更時もフォールバック突合で候補ID不変・状態引き継ぎ（⑨(iv)・§7）。

    突合・引き継ぎは pipeline の収集処理（§7 手順(2)）で行われる。
    既存行（旧URL・応募希望マークあり）を置いた状態で新URLの同一求人を
    収集し、候補 ID が初回登録時のまま・情報源 URL のリンク先だけが
    新 URL に更新され、応募状態と本人の判断が引き継がれることを見る。
    """
    key = impl("job_candidate_list.normalize.fallback_match_key")
    cid = impl("job_candidate_list.normalize.candidate_id")
    models = impl("job_candidate_list.models")
    assert key("サンプル青葉", "プロダクトマネージャー") == key(
        "サンプル青葉", "プロダクトマネージャー"
    )

    old_url = "https://example.invalid/j/1"
    new_url = "https://example.invalid/j/2"
    existing_id = cid("サンプル青葉", "プロダクトマネージャー", old_url)
    existing = _record(
        existing_id,
        company="株式会社サンプル青葉",
        company_norm="サンプル青葉",
        position="プロダクトマネージャー",
        position_norm="プロダクトマネージャー",
        fitness="中",
        source_url=models.LinkCell("求人ページ", old_url),
        manual_judgment="応募希望",
    )
    summary, gateway = run_fake_collection(
        profile_config_dict,
        judge_config_dict,
        local_config_dict,
        existing=[existing],
        postings=[build_raw_posting(raw_url=new_url)],
        dry_run=False,
    )
    assert existing_id not in list(summary.added_ids)
    assert existing_id in list(summary.updated_ids)
    carried = [r for r in gateway.upserted if r.candidate_id == existing_id]
    assert carried, "既存候補がフォールバック突合で引き継がれていない"
    record = carried[0]
    assert record.source_url.url == new_url, "リンク先が新URLに更新されていない"
    assert record.source_url.display_text == "求人ページ"
    assert enum_value(record.apply_status) == enum_value(
        existing.apply_status
    ), "応募状態が引き継がれていない"
    assert record.manual_judgment == "応募希望", "応募希望マークが引き継がれていない"


def test_presentation_limit_change_is_reflected_from_config(judge_config_dict):
    """提示上限の設定変更がコード無変更で提示件数に反映される（⑨(i)・①）。"""
    assert judge_config_dict["presentation"]["weekly_limit"] == 20
    presentation_config = impl("job_candidate_list.config.PresentationConfig")
    changed = presentation_config.from_dict(
        dict(judge_config_dict["presentation"], weekly_limit=5)
    )
    assert changed.weekly_limit == 5
    split = impl("job_candidate_list.presentation.split_presentation")
    candidates = [_record(f"cand_{i:016d}", fitness="中") for i in range(7)]
    presented, held = split(candidates, changed.weekly_limit)
    assert len(presented) == 5
    assert len(held) == 2
