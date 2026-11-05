"""完了の定義⑤: 禁止ワードによる実行停止・通知文の形式。

根拠: 企画書「完了の定義」⑤、architecture.md §3.3 / §3.15 / §12 手順4・13。
通知文そのものの生成インターフェースは §3 に定義がないため、
形式検証は仕様確認事項として扱う（最終報告に記載）。
"""

from __future__ import annotations

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
    forbidden = [a for a in annotations
                 if any(k in a for k in ("company", "position", "salary"))]
    assert not forbidden, f"RunSummary に詳細フィールドがある: {forbidden}"


def test_notification_text_format_has_no_details():
    """通知文に会社名等の詳細が含まれない形式であること（⑤）。

    §3 に通知文生成の公開インターフェースが定義されていないため、
    インターフェース確定後に検証する（仕様確認事項）。
    """
    pytest.fail(
        "未検証（仕様確認事項）: architecture.md §3 には通知文を生成する"
        "公開インターフェースが定義されていない（RunSummary は件数・ID のみと"
        "定められているが、通知文への変換責務の所在が未確定）。"
        "通知は件数とリストへの参照のみで構成されることの検証は、"
        "生成インターフェースの確定後に行う。"
    )
