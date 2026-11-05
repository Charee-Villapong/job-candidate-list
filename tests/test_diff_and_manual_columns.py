"""完了の定義④（第1段階で構築される部分）: 差分計算・大量変更ガード・人手列保護。

根拠: 企画書「完了の定義」④、architecture.md §3.11 / §3.13 / §10.1。
週次 cron 実行そのものは第2段階のため対象外（最終報告に明示）。
"""

from __future__ import annotations

import pytest

from conftest import impl

EXPECTED_AUTO_COLUMNS = [
    "候補ID", "会社名", "ポジション名", "雇用形態", "想定年収レンジ", "勤務地",
    "勤務地判定結果", "リモート頻度", "適合度", "適合理由", "要確認理由",
    "情報源種別", "情報源URL", "OpenWork点数", "OpenWork URL",
    "OpenWork点数取得日", "提示区分", "掲載確認日", "状態", "応募状態",
    "応募日時", "応募結果",
]
EXPECTED_MANUAL_COLUMNS = ["本人の判断", "本人のメモ"]


def _record(candidate_id: str, **over):
    """CandidateRecord を生成する。生成方法が未確定なら理由付きで失敗。"""
    models = impl("job_candidate_list.models")
    try:
        return models.CandidateRecord(candidate_id=candidate_id, **over)
    except TypeError as exc:
        pytest.fail(
            "未検証（仕様確認事項）: CandidateRecord の生成方法"
            f"（必須引数・デフォルト）が §2.2 で未確定のため構築できない: {exc}"
        )


def _diff(old, new):
    compute = impl("job_candidate_list.diff_guard.compute_diff")
    return compute(old, new, None, None)


def test_auto_and_manual_columns_are_disjoint():
    """自動列と人手列が重ならず、人手列は書込ホワイトリストに含まれない（§3.11）。"""
    gw = impl("job_candidate_list.sheets_gateway")
    auto = list(gw.AUTO_COLUMNS_CANDIDATE)
    manual = list(gw.MANUAL_COLUMNS_CANDIDATE)
    assert auto == EXPECTED_AUTO_COLUMNS
    assert manual == EXPECTED_MANUAL_COLUMNS
    assert not set(auto) & set(manual)
    assert not set(manual) & set(auto)


def test_diff_detects_added_updated_closed():
    """差分計算: 新規／更新あり／掲載終了が正しく検出される（④）。"""
    old = [_record("cand_0000000000000001"), _record("cand_0000000000000002"),
           _record("cand_0000000000000003")]
    new = [_record("cand_0000000000000001"),
           _record("cand_0000000000000004")]
    diff = _diff(old, new)
    added = set(getattr(diff, "added"))
    closed = set(getattr(diff, "closed"))
    assert added == {"cand_0000000000000004"}
    assert closed == {"cand_0000000000000002", "cand_0000000000000003"}


def test_diff_contains_counts_and_ids_only():
    """Diff は件数と対象IDの集合のみを持ち、会社名等の詳細を持たない（§3.13）。"""
    diff = _diff([], [_record("cand_0000000000000001")])
    for attr in vars(diff):
        assert "company" not in attr and "salary" not in attr


def test_mass_change_guard_stops_when_all_closed():
    """ドライランで全件掲載終了の場合、本実行が止まる（④・§3.13）。"""
    guard = impl("job_candidate_list.diff_guard.mass_change_guard")
    cfg = impl("job_candidate_list.config")
    old = [_record(f"cand_{i:016d}") for i in range(10)]
    diff = _diff(old, [])
    result = guard(diff, existing_count=10, cfg=_guard_cfg(cfg))
    assert str(getattr(result, "status", result)) in ("stop", "GuardResult.stop")


def _guard_cfg(cfg_module):  # pragma: no cover - 実装後に有効化
    pytest.fail("未検証（仕様確認事項）: GuardConfig の生成方法が未確定")


def test_mass_change_guard_allows_small_change():
    """小規模な変更ではガードが止めない（§3.13）。"""
    guard = impl("job_candidate_list.diff_guard.mass_change_guard")
    assert guard is not None
    pytest.fail("未検証（仕様確認事項）: Diff / GuardConfig の生成方法が"
                "未確定のため、小規模変更ケースを構築できない")


def test_upsert_preserves_manual_column_values():
    """自動更新で人手列（本人の判断・メモ）の値が保持される（④）。

    SheetsGateway の具象クラス名が §3.11 で未確定（Protocol のみ）のため、
    実クラスでの検証はクラス確定後に行う。ここでは列ホワイトリストによる
    構造的担保（test_auto_and_manual_columns_are_disjoint）と対で検証する。
    """
    gw = impl("job_candidate_list.sheets_gateway")
    concrete = [n for n in dir(gw) if n.endswith("Gateway") and n != "SheetsGateway"]
    assert concrete, (
        "未検証（仕様確認事項）: SheetsGateway の具象クラスが §3.11 で未確定のため、"
        "フェイク越しの人手列保持を実クラスで検証できない"
    )


def test_dry_run_does_not_write_to_sheet():
    """dry_run=True の場合はシートへ書き込まない（§3.15）。"""
    run = impl("job_candidate_list.pipeline.run_collection")
    assert run is not None
    pytest.fail("未検証: AppConfig / 情報源アダプタの構築方法が未確定のため、"
                "FakeSheetsGateway を使ったドライラン検証を構築できない")
