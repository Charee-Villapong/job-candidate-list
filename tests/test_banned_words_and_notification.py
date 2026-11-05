"""完了の定義⑤: 禁止ワードによる実行停止・通知文の形式。

根拠: 企画書「完了の定義」⑤、architecture.md §3.3 / §3.15 / §12 手順4・13。
通知文の生成インターフェースは追補 A4 で確定した
`pipeline.build_notification_text(summary, list_url)` と
`pipeline.RunSummary`（件数と候補 ID・情報源名のみ）を使う。
"""

from __future__ import annotations

import datetime as dt

import pytest
from conftest import impl


def test_scan_banned_words_detects_partial_match():
    """禁止ワードは部分一致で検出される（§3.3）。"""
    scan = impl("job_candidate_list.banned_words.scan_banned_words")
    hits = scan(["データ PM 駅名サンプル を含むクエリ"], ["駅名サンプル"])
    assert len(hits) == 1
    assert hits[0].text_index == 0


def test_scan_banned_words_no_hit_returns_empty():
    scan = impl("job_candidate_list.banned_words.scan_banned_words")
    assert scan(["データ PM"], ["駅名サンプル"]) == []


def test_assert_no_banned_words_raises_on_hit():
    """禁止ワードを含むクエリでは実行が止まる（⑤）。"""
    assert_no = impl("job_candidate_list.banned_words.assert_no_banned_words")
    errors = impl("job_candidate_list.errors")
    with pytest.raises(errors.BannedWordHit):
        assert_no(["禁止サンプル語 を含む検索クエリ"], ["禁止サンプル語"])


def test_banned_word_exception_message_contains_only_count():
    """例外メッセージに禁止ワード自体を含めず、件数のみを含める（§3.3）。"""
    assert_no = impl("job_candidate_list.banned_words.assert_no_banned_words")
    errors = impl("job_candidate_list.errors")
    with pytest.raises(errors.BannedWordHit) as excinfo:
        assert_no(["禁止サンプル語 を含むクエリ"], ["禁止サンプル語"])
    assert "禁止サンプル語" not in str(excinfo.value)


def test_load_banned_words_missing_file_is_config_error(tmp_path):
    """禁止ワードファイルが無い場合はスキャン省略で続行せず ConfigError（§3.3）。"""
    load = impl("job_candidate_list.banned_words.load_banned_words")
    errors = impl("job_candidate_list.errors")
    with pytest.raises(errors.ConfigError):
        load(tmp_path / "nonexistent.txt")


def test_run_summary_contains_counts_and_ids_only():
    """実行サマリ（通知の材料）は件数とIDのみで、詳細を含まない（§3.15）。"""
    pipeline = impl("job_candidate_list.pipeline")
    summary = pipeline.RunSummary
    annotations = getattr(summary, "__annotations__", {})
    forbidden = [
        a for a in annotations if any(k in a for k in ("company", "position", "salary"))
    ]
    assert not forbidden, f"RunSummary に詳細フィールドがある: {forbidden}"


def _run_summary(**over):
    """RunSummary を追補 A4.1 の確定フィールドで組み立てる。"""
    pipeline = impl("job_candidate_list.pipeline")
    fields = {
        "run_on": dt.date(2026, 11, 5),
        "dry_run": False,
        "stopped_by_guard": False,
        "collected_count": 6,
        "added_count": 2,
        "updated_count": 1,
        "closed_count": 0,
        "excluded_count": 3,
        "check_only_count": 1,
        "check_counts_by_reason": (("主務", 1),),
        "presented_count": 2,
        "held_new_count": 1,
        "held_promoted_count": 0,
        "held_remaining_count": 1,
        "held_closed_count": 0,
        "openwork_disabled_count": 2,
        "source_names": ("サンプル情報源A",),
        "skipped_source_names": (),
        "added_ids": ("cand_0000000000000001", "cand_0000000000000002"),
        "updated_ids": ("cand_0000000000000003",),
        "closed_ids": (),
        "excluded_ids": ("cand_0000000000000004",),
    }
    fields.update(over)
    return pipeline.RunSummary(**fields)


def test_notification_text_format_has_no_details():
    """通知文に会社名等の詳細が含まれない形式であること（⑤）。

    生成関数は追補 A4.2 の build_notification_text。出力は固定
    テンプレート（件数とリスト参照のみ）どおりであることを確認する。
    """
    build = impl("job_candidate_list.pipeline.build_notification_text")
    text = build(_run_summary(), "https://example.invalid/list")
    expected = (
        "応募候補リストを更新しました（2026-11-05）\n"
        "新規 2件／更新 1件／掲載終了 0件／除外 3件／要確認 1件\n"
        "提示 2件／控え 新規1件・繰り上げ0件・残1件\n"
        "候補リスト: https://example.invalid/list"
    )
    assert text.strip() == expected
    # 詳細（会社名・ポジション名・年収等）が現れていないこと
    assert "株式会社" not in text
    assert "万円" not in text
