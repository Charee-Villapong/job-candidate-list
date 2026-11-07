"""C-063（候補5・候補 ID の再計算方法の不一致）のテスト。

テスト係が実装コード（src/）を見ず、仕様ドキュメント
（docs/architecture.md §3.2／§3.9／追補 A8.2、config/templates/
judge.example.yaml）と既存テスト（特に C-068 の特性テスト
tests/test_c068_characterization.py §6）が使う公開インターフェース、
および現行コードの入出力の実測だけから書いた。

背景（現行挙動・C-068 で固定済み）:
- レコードの candidate_id は pipeline 側で、設定
  `normalize.tracking_params` を使って URL を正規化してから計算される。
- 一方、提示順キー（CandidateRecord.presentation_key、追補 A8.2:
  (適合度順位, -機械的一致度, -掲載確認日の ordinal, candidate_id)）の
  第 4 要素に埋め込まれる ID は、fitness 側が normalize_url を
  「既定のトラッキング除去リスト」で呼んで再計算するため、設定の
  tracking_params が既定リストと異なる場合に両 ID が食い違う。
  実測で確認した既定リストは judge.example.yaml の 8 件
  （utm_source／utm_medium／utm_campaign／utm_term／utm_content／
  ref／fbclid／gclid）と一致する。

本人の決定（C-063）: 「設定を使う側に統一（提示順キーも設定で計算する）」。
つまり実施後は、提示順キーの ID も設定の tracking_params で
計算されなければならない。本ファイルは 2 部構成:

1. 仕様テスト（test_spec_*）: 決定を受けた正しい挙動を assert する。
   **現行コードでは食い違いがあるため、実装前は red になるのが正しい。**
   C-063 の実施で green になるべきテストであり、red のまま放置したり
   現行挙動に合わせて書き換えたりしてはならない。
2. 特性テスト（test_characterization_*）: 設定が既定リストのままでは
   提示順キー・candidate_id・提示順が現行と完全に同一であることを
   固定する。実施前後とも green でなければならない安全網であり、
   現行挙動が「正しい」ことの証明ではない。

観測はすべて pipeline 経由（conftest.run_fake_collection）で行う。
assign_fitness 等の内部シグネチャは C-063 の実施で変わりうるため、
統合された公開挙動（upsert される CandidateRecord）だけを見る。
実シート・実レジストリ・実設定ファイルには触れない（境界は
conftest のフェイク、設定はフィクスチャ辞書の deepcopy 変更のみ）。

C-068 側への影響（所見）: 実施後は
tests/test_c068_characterization.py の
test_candidate_id_diverges_between_record_and_presentation_key
（「食い違い」を現行挙動として固定したテスト）は前提が崩れて落ちる。
それは検知として正しく、当該テストは本ファイルの仕様テストに合わせて
「一致する」形へ更新する仕様変更が要る（本ファイルでは変更しない）。
同ファイルの test_candidate_id_agrees_when_no_custom_tracking_param は
実施後もそのまま green のはずである。
"""

from __future__ import annotations

import copy
import datetime as dt

import pytest
from conftest import build_raw_posting, impl, run_fake_collection

# build_raw_posting の既定求人（株式会社サンプル青葉／
# プロダクトマネージャー）の正規化名。既存テストと同じ前提。
_COMPANY_NORM = "サンプル青葉"
_POSITION_NORM = "プロダクトマネージャー"

# 設定テンプレート（config/templates/judge.example.yaml）の
# normalize.tracking_params。これが fitness 側の現行の
# 「既定のトラッキング除去リスト」と実測で一致する。
_TEMPLATE_DEFAULT_TRACKING_PARAMS = [
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "fbclid",
    "gclid",
]

# conftest の judge_config_dict が持つ tracking_params（テスト用に
# 縮小された設定値であり、テンプレート既定値そのものではない）。
_FIXTURE_TRACKING_PARAMS = ["utm_source", "utm_medium", "ref", "fbclid"]


def _expected_candidate_id(url_norm: str) -> str:
    """§3.2 の公開関数で、設定どおりに正規化済みの URL から ID を作る。"""
    cid = impl("job_candidate_list.normalize.candidate_id")
    return cid(_COMPANY_NORM, _POSITION_NORM, url_norm)


def _judge_with_tracking(judge_config_dict, tracking_params):
    judge = copy.deepcopy(judge_config_dict)
    judge["normalize"]["tracking_params"] = list(tracking_params)
    return judge


def _collect_one(profile_config_dict, judge_data, raw_url):
    posting = build_raw_posting(raw_url=raw_url)
    summary, gateway = run_fake_collection(
        profile_config_dict, judge_data, postings=[posting]
    )
    assert len(gateway.upserted) == 1
    record = gateway.upserted[0]
    assert summary.added_ids == (record.candidate_id,)
    assert record.presentation_key is not None
    return record


# ---------------------------------------------------------------------------
# 1. 仕様テスト（C-063 の決定を受けたもの。実装前は red が正）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tracking_params,raw_url,expected_url_norm",
    [
        # (a) 追加: テンプレート既定に無いパラメータ（session）を
        # 設定で除去対象に加えた構成。レコード ID では除去される。
        (
            _FIXTURE_TRACKING_PARAMS + ["session"],
            "https://example.invalid/j/1?session=abc&utm_source=x",
            "https://example.invalid/j/1",
        ),
        # (b) 入替: C-068 が食い違いを固定したのと同じ構成
        # （tracking_params = ["utm_source", "session"]）。
        (
            ["utm_source", "session"],
            "https://example.invalid/j/1?session=abc&utm_source=x",
            "https://example.invalid/j/1",
        ),
        # (c) 縮小: 設定では utm_campaign を除去対象から外した構成。
        # レコード ID では utm_campaign が残るのが設定どおりの挙動で、
        # 提示順キーもそれに一致しなければならない（既定リストで
        # 勝手に除去してはならない）。
        (
            ["utm_source"],
            "https://example.invalid/j/1?utm_campaign=x&utm_source=y",
            "https://example.invalid/j/1?utm_campaign=x",
        ),
        # (d) 空: 設定が空なら何も除去しない。レコード ID は
        # utm_source を含んだまま計算され、提示順キーも同じになる。
        (
            [],
            "https://example.invalid/j/1?utm_source=x",
            "https://example.invalid/j/1?utm_source=x",
        ),
    ],
)
def test_spec_presentation_key_id_matches_candidate_id_with_custom_tracking_params(
    profile_config_dict, judge_config_dict, tracking_params, raw_url, expected_url_norm
):
    """仕様テスト（C-063・実装前は red が正）: 設定の tracking_params を
    既定リストから変えた構成では、提示順キーに埋め込まれる ID も
    設定で計算され、実際の candidate_id と一致すること。

    レコード側の candidate_id が設定どおりであること（expected_url_norm
    から §3.2 の公開関数で計算した ID と一致）は現行でも満たされる
    はずで、落ちるのは提示順キー側の一致 assert のみであること。
    """
    judge = _judge_with_tracking(judge_config_dict, tracking_params)
    record = _collect_one(profile_config_dict, judge, raw_url)
    # pipeline 側（設定を使う側）の ID は設定どおり
    assert record.candidate_id == _expected_candidate_id(expected_url_norm)
    # 決定: 提示順キー側も設定で計算し、両 ID が一致する
    assert record.presentation_key[3] == record.candidate_id


def test_spec_all_presentation_key_ids_match_in_batch_with_custom_tracking_params(
    profile_config_dict, judge_config_dict
):
    """仕様テスト（C-063・実装前は red が正）: 設定を変えた構成で
    複数件を収集しても、全件で提示順キーの ID が candidate_id と
    一致すること（1 件だけの偶然の一致でないこと）。"""
    judge = _judge_with_tracking(
        judge_config_dict, _FIXTURE_TRACKING_PARAMS + ["session"]
    )
    postings = [
        build_raw_posting(raw_url="https://example.invalid/j/1?session=a"),
        build_raw_posting(raw_url="https://example.invalid/j/2?session=b"),
        build_raw_posting(raw_url="https://example.invalid/j/3?session=c"),
    ]
    summary, gateway = run_fake_collection(
        profile_config_dict, judge, postings=postings
    )
    assert len(gateway.upserted) == 3
    assert len(summary.added_ids) == 3
    for record in gateway.upserted:
        assert record.presentation_key is not None
        assert record.presentation_key[3] == record.candidate_id


def test_spec_presentation_order_uses_candidate_id_as_tiebreak_with_custom_params(
    profile_config_dict, judge_config_dict
):
    """仕様テスト（C-063・実装前は red が正）: 提示順キーの他の要素
    （順位・一致度・日付）が同点の 2 件では、並びは実際の
    candidate_id の昇順で決まること。

    実測した現行挙動では、埋め込み ID（session を除去しない側）で
    並ぶため順序が逆転する。実施後は candidate_id 昇順
    （cand_2e89… → cand_8d76…）になるはずである。
    ハッシュ値は §3.2 の生成規則（sha256 先頭 16 桁）で決まる
    固定値で、実測で確認した。
    """
    judge = _judge_with_tracking(
        judge_config_dict, _FIXTURE_TRACKING_PARAMS + ["session"]
    )
    postings = [
        build_raw_posting(raw_url="https://example.invalid/j/1?session=s0"),
        build_raw_posting(raw_url="https://example.invalid/j/2?session=t0"),
    ]
    _, gateway = run_fake_collection(profile_config_dict, judge, postings=postings)
    assert len(gateway.upserted) == 2
    # 前提: 2 件とも ID 以外のキー要素は同点（タイブレーカーは ID のみ）
    assert len({r.presentation_key[:3] for r in gateway.upserted}) == 1
    sort_key = impl("job_candidate_list.presentation.presentation_sort_key")
    ordered = [r.candidate_id for r in sorted(gateway.upserted, key=sort_key)]
    by_candidate_id = [
        r.candidate_id
        for r in sorted(
            gateway.upserted, key=lambda r: (r.presentation_key[:3], r.candidate_id)
        )
    ]
    assert ordered == by_candidate_id
    assert ordered == ["cand_2e8984d880461d2d", "cand_8d767a50e2445546"]


# ---------------------------------------------------------------------------
# 2. 特性テスト（実施前後とも green。設定が既定のままでは現行と同一）
# ---------------------------------------------------------------------------
# 現行実測の提示順キー前缀: 既定求人は適合度「要確認」（順位 2）、
# 機械的一致度 60（負値 -60）、掲載確認日は fetched_on フィクスチャ
# 2026-11-05 の ordinal の負値。C-063 は ID の計算方法だけを変える
# ため、この前缀は実施前後で変わらないはずである。
_EXPECTED_KEY_PREFIX = (2, -60, -dt.date(2026, 11, 5).toordinal())

# 既定構成での提示順（実測で固定）: ID 以外が同点のため ID 昇順。
_EXPECTED_DEFAULT_ORDER = [
    "cand_2e8984d880461d2d",
    "cand_8d767a50e2445546",
    "cand_eded93da49b46732",
]


def _assert_default_records(records):
    """既定構成の 3 件について ID・キー・提示順が現行どおりであること。"""
    assert [r.candidate_id for r in records] == [
        "cand_8d767a50e2445546",  # /j/1（トラッキング除去後）
        "cand_2e8984d880461d2d",  # /j/2（同上）
        "cand_eded93da49b46732",  # /j/3（パラメータなし）
    ]
    for record in records:
        assert record.presentation_key == (
            *_EXPECTED_KEY_PREFIX,
            record.candidate_id,
        )
    sort_key = impl("job_candidate_list.presentation.presentation_sort_key")
    ordered = [r.candidate_id for r in sorted(records, key=sort_key)]
    assert ordered == _EXPECTED_DEFAULT_ORDER


def test_characterization_template_default_tracking_params_exact_keys_and_order(
    profile_config_dict, judge_config_dict
):
    """特性テスト: tracking_params が設定テンプレートの既定リスト
    （8 件）のままでは、candidate_id・提示順キー・提示順のすべてが
    現行と完全に同一であること（C-063 実施前後とも green）。"""
    judge = _judge_with_tracking(judge_config_dict, _TEMPLATE_DEFAULT_TRACKING_PARAMS)
    postings = [
        build_raw_posting(raw_url="https://example.invalid/j/1?utm_source=x"),
        build_raw_posting(raw_url="https://example.invalid/j/2?utm_campaign=y&gclid=z"),
        build_raw_posting(raw_url="https://example.invalid/j/3"),
    ]
    _, gateway = run_fake_collection(profile_config_dict, judge, postings=postings)
    assert len(gateway.upserted) == 3
    _assert_default_records(gateway.upserted)


def test_characterization_fixture_default_config_exact_keys_and_order(
    profile_config_dict, judge_config_dict
):
    """特性テスト: conftest の judge フィクスチャを無変更で使い、
    URL のパラメータが既定リストと設定の両方に含まれる範囲では、
    candidate_id・提示順キー・提示順が現行と完全に同一であること
    （C-068 の対照テストと同じ範囲。実施前後とも green）。"""
    postings = [
        build_raw_posting(raw_url="https://example.invalid/j/1?utm_source=x"),
        build_raw_posting(raw_url="https://example.invalid/j/2?ref=y"),
        build_raw_posting(raw_url="https://example.invalid/j/3"),
    ]
    _, gateway = run_fake_collection(
        profile_config_dict, judge_config_dict, postings=postings
    )
    assert len(gateway.upserted) == 3
    _assert_default_records(gateway.upserted)


def test_characterization_template_default_strips_all_default_params_at_once(
    profile_config_dict, judge_config_dict
):
    """特性テスト: 既定リストの 8 パラメータを全て含む URL でも、
    既定構成では全て除去された ID になり、提示順キーもそれと
    一致する現行挙動が変わらないこと（実施前後とも green）。"""
    judge = _judge_with_tracking(judge_config_dict, _TEMPLATE_DEFAULT_TRACKING_PARAMS)
    raw_url = (
        "https://example.invalid/j/1?utm_source=a&utm_medium=b"
        "&utm_campaign=c&utm_term=d&utm_content=e&ref=f&fbclid=g&gclid=h"
    )
    record = _collect_one(profile_config_dict, judge, raw_url)
    assert record.candidate_id == _expected_candidate_id("https://example.invalid/j/1")
    assert record.candidate_id == "cand_8d767a50e2445546"
    assert record.presentation_key == (
        *_EXPECTED_KEY_PREFIX,
        "cand_8d767a50e2445546",
    )
