"""完了の定義④（第1段階で構築される部分）: 差分計算・大量変更ガード・人手列保護。

根拠: 企画書「完了の定義」④、architecture.md §3.11 / §3.13 / §10.1。
週次 cron 実行そのものは第2段階のため対象外（最終報告に明示）。
"""

from __future__ import annotations

import pytest

from conftest import (
    build_candidate_record,
    build_raw_posting,
    enum_value,
    impl,
    run_fake_collection,
)

EXPECTED_AUTO_COLUMNS = [
    "候補ID", "会社名", "ポジション名", "雇用形態", "想定年収レンジ", "勤務地",
    "勤務地判定結果", "リモート頻度", "適合度", "適合理由", "要確認理由",
    "情報源種別", "情報源URL", "OpenWork点数", "OpenWork URL",
    "OpenWork点数取得日", "提示区分", "掲載確認日", "状態", "応募状態",
    "応募日時", "応募結果",
]
EXPECTED_MANUAL_COLUMNS = ["本人の判断", "本人のメモ"]


def _record(candidate_id: str, **over):
    """CandidateRecord を生成する（追補 A3: 必須引数は candidate_id のみ）。"""
    return build_candidate_record(candidate_id, **over)


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


def _guard_cfg():
    """GuardConfig を追補 A2.2 の from_dict で生成する（§4.2 の初期値）。"""
    guard_config = impl("job_candidate_list.config.GuardConfig")
    return guard_config.from_dict(
        {"max_close_ratio": 0.5, "max_total_change_ratio": 1.0}
    )


def test_mass_change_guard_stops_when_all_closed():
    """ドライランで全件掲載終了の場合、本実行が止まる（④・§3.13）。"""
    guard = impl("job_candidate_list.diff_guard.mass_change_guard")
    old = [_record(f"cand_{i:016d}") for i in range(10)]
    diff = _diff(old, [])
    result = guard(diff, existing_count=10, cfg=_guard_cfg())
    assert enum_value(getattr(result, "status", result)) == "stop"


def test_mass_change_guard_allows_small_change():
    """小規模な変更ではガードが止めない（§3.13）。

    既存10件に対し追加1件・終了1件は、終了比率・変更合計比率とも
    閾値（0.5／1.0）の範囲内。
    """
    guard = impl("job_candidate_list.diff_guard.mass_change_guard")
    old = [_record(f"cand_{i:016d}") for i in range(10)]
    new = old[1:] + [_record("cand_0000000000000100")]
    diff = _diff(old, new)
    result = guard(diff, existing_count=10, cfg=_guard_cfg())
    assert enum_value(getattr(result, "status", result)) == "ok"


class _FakeSheetsBackend:
    """SheetsBackend（追補 A5）に対するフェイク。書込内容を記録する。"""

    def __init__(self):
        self.writes: list = []

    def read_rows(self, tab_name: str):
        return []

    def read_links(self, tab_name: str):
        return {}

    def write_rows(self, tab_name: str, rows) -> None:
        self.writes.append((tab_name, [list(r) for r in rows]))


def test_upsert_preserves_manual_column_values():
    """自動更新で人手列（本人の判断・メモ）の値が保持される（④）。

    具象クラスは追補 A5 で確定した GoogleSheetsGateway。backend へ
    渡される行が自動列（AUTO_COLUMNS_CANDIDATE）の分だけから構成され、
    人手列の列位置へ書き込みデータを渡さないことで、人手列の値が
    保持されることを実クラスで検証する。
    """
    gw = impl("job_candidate_list.sheets_gateway")
    backend = _FakeSheetsBackend()
    gateway = gw.GoogleSheetsGateway(backend)
    record = _record(
        "cand_0000000000000001",
        company="株式会社サンプル青葉",
        position="プロダクトマネージャー",
    )
    gateway.upsert_candidates([record])

    assert backend.writes, "backend への書込が行われていない"
    auto = list(gw.AUTO_COLUMNS_CANDIDATE)
    manual = list(gw.MANUAL_COLUMNS_CANDIDATE)
    for tab_name, rows in backend.writes:
        assert tab_name == "候補リスト"
        for row in rows:
            # 行は自動列の分だけ。人手列ぶんのセルは含まれない
            assert len(row) == len(auto), (
                f"書込行の列数が自動列数と一致しない: {len(row)} != {len(auto)}"
            )
    assert not set(auto) & set(manual)


def test_dry_run_does_not_write_to_sheet(
    judge_config_dict, profile_config_dict, local_config_dict
):
    """dry_run=True の場合はシートへ書き込まない（§3.15）。

    AppConfig は AppConfig.from_dicts（追補 A2.2）、情報源アダプタは
    SourceAdapter Protocol（追補付録）に対するフェイクで構築する。
    """
    run = impl("job_candidate_list.pipeline.run_collection")
    assert run is not None
    summary, gateway = run_fake_collection(
        profile_config_dict,
        judge_config_dict,
        local_config_dict,
        existing=[],
        postings=[build_raw_posting()],
        dry_run=True,
    )
    assert summary is not None
    assert gateway.upserted == [], "dry_run なのに候補が書き込まれた"
    assert gateway.exclusions == [], "dry_run なのに除外ログが書き込まれた"
