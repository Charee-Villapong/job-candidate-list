"""職種名（タイトル）ベースのハード除外（HARD-TITLE）の仕様テスト。

仕様:
- 求人の職種名に、設定 `hard.title_exclusion_keywords` のキーワードが
  含まれれば、対象領域の語（データ／AI 等）の有無にかかわらず
  ハード除外になる。
- 照合範囲は職種名のみ。業務内容・会社概要など他の場所にキーワードが
  偶然現れても、このルールでは発火しない。
- プロジェクトマネージャー／PM／アーキテクト等の本来の対象職種は
  この除外で落とさない（「PM」を含むタイトル一般を落とさない）。
- キーワードは設定側が正本で、設定で置き換え・空にもできる。
  HardConfig の既定値は他のキーワードリストと同様に空。
"""

from __future__ import annotations

import pytest
from conftest import build_raw_posting, impl

PDM_KEYWORDS = [
    "プロダクトマネージャー",
    "PdM",
    "プロダクトマネジメント",
    "プロダクトオーナー",
]


def _hits(position_raw, keywords=PDM_KEYWORDS, **over):
    judge = impl("job_candidate_list.judge_hard.judge_hard_exclusions")
    hard_config = impl("job_candidate_list.config.HardConfig")
    cfg = hard_config.from_dict({"title_exclusion_keywords": list(keywords)})
    posting = build_raw_posting(position_raw=position_raw, **over)
    return judge(posting, cfg)


def _rule_ids(position_raw, keywords=PDM_KEYWORDS, **over):
    return [h.rule_id for h in _hits(position_raw, keywords, **over)]


def test_pdm_title_excluded_even_with_domain_words():
    """領域語（データ／AI）を含む PdM 求人でも HARD-TITLE で落ちること。

    build_raw_posting の既定セクションには「データ活用」「AI活用支援」
    等の対象領域の語が含まれる。従来の HARD-DOMAIN は領域語があると
    発火しないため、このケースで落ちるのは HARD-TITLE のみになる。
    """
    rule_ids = _rule_ids("プロダクトマネージャー")
    assert "HARD-TITLE" in rule_ids
    assert "HARD-DOMAIN" not in rule_ids


@pytest.mark.parametrize(
    "title",
    [
        "プロダクトマネージャー",
        "シニアプロダクトマネージャー",
        "PdM",
        "PdM（プロダクト責任者）",
        "プロダクトマネジメント担当",
        "プロダクトオーナー",
    ],
)
def test_each_title_keyword_fires(title):
    assert "HARD-TITLE" in _rule_ids(title)


@pytest.mark.parametrize(
    "title",
    [
        "プロジェクトマネージャー",
        "プロジェクトマネージャー（データ活用推進）",
        "PM",
        "シニアPM",
        "PM／プロジェクトリーダー",
        "ソリューションアーキテクト",
        "データアーキテクト",
        "開発マネージャー",
    ],
)
def test_target_titles_not_excluded(title):
    """本来の対象職種（プロジェクト側 PM・マネジメント・アーキテクト）は落とさない。"""
    assert _rule_ids(title) == []


def test_keyword_in_body_section_only_does_not_fire():
    """業務内容にだけキーワードがあっても HARD-TITLE は発火しない。"""
    rule_ids = _rule_ids(
        "プロジェクトマネージャー",
        sections={
            "業務内容": (
                "プロダクトマネージャーと連携し、データ活用推進の"
                "企画立案と関係者調整を主担当として担います。"
            ),
            "会社概要": "データ分析基盤を提供する事業を行っています。",
        },
    )
    assert "HARD-TITLE" not in rule_ids


def test_keyword_in_company_overview_only_does_not_fire():
    """会社概要にだけキーワードがあっても HARD-TITLE は発火しない。"""
    rule_ids = _rule_ids(
        "プロジェクトマネージャー",
        company_overview_raw=(
            "プロダクトマネージャーを擁する、データ分析基盤の会社です。"
        ),
    )
    assert "HARD-TITLE" not in rule_ids


def test_hit_carries_config_ref():
    """除外ログから直すべき設定が追えること（config_ref）。"""
    hits = _hits("プロダクトマネージャー")
    title_hits = [h for h in hits if h.rule_id == "HARD-TITLE"]
    assert title_hits
    assert title_hits[0].config_ref == "hard.title_exclusion_keywords"


def test_keywords_are_configurable():
    """キーワードは設定で置き換えられる（コード直書きでない）こと。"""
    assert "HARD-TITLE" in _rule_ids(
        "カスタム対象外職種", keywords=["カスタム対象外職種"]
    )
    # 置き換えた設定では PdM タイトルは（このルールでは）落ちない
    assert "HARD-TITLE" not in _rule_ids(
        "プロダクトマネージャー", keywords=["カスタム対象外職種"]
    )


def test_empty_keywords_never_fire():
    assert _rule_ids("プロダクトマネージャー", keywords=[]) == []


def test_default_config_has_empty_title_keywords():
    """HardConfig の既定値は他のキーワードリストと同様に空。"""
    hard_config = impl("job_candidate_list.config.HardConfig")
    assert hard_config.from_dict({}).title_exclusion_keywords == ()
