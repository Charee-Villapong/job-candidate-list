"""その他ハード除外（雇用形態／領域外／事業形態）（§3.8）。

事業形態は公開情報から明確に判断できる場合のみヒットさせる。
曖昧な場合（キーワードが弱く単独で現れる場合）は本関数では返さず、
適合度付け側で BIZFORM-UNKNOWN（要確認）として扱う。
"""

from __future__ import annotations

from typing import NamedTuple

from .config import DEFAULT_STRONG_BUSINESS_FORM_KEYWORDS, HardConfig
from .models import RawPosting

# 判定キーワードは全て設定側（HardConfig）が正本（C-064）。
# 対象領域キーワードは cfg.target_domain_keywords、事業形態の強キーワード
# （単独でも明確とみなす）は cfg.strong_business_form_keywords を使う。
# 既定値は config.DEFAULT_* と従来の直書き値で完全に同一である。


class ExclusionHit(NamedTuple):
    rule_id: str
    reason: str
    config_ref: str


def overview_text(posting: RawPosting) -> str:
    """会社概要系テキスト（原文＋会社概要セクション）の組み立て。

    事業形態の判定対象はこの範囲に限る（業務内容セクションは含めない）。
    """
    return "\n".join(
        part
        for part in (
            posting.company_overview_raw,
            posting.sections.get("会社概要"),
        )
        if part
    )


def searchable_text(posting: RawPosting) -> str:
    """求人全体の検索対象テキスト（ポジション名＋会社概要原文＋全セクション）。"""
    return "\n".join(
        part
        for part in (
            posting.position_raw,
            posting.company_overview_raw,
            *posting.sections.values(),
        )
        if part
    )


def matched_business_form_keywords(
    posting: RawPosting, cfg: HardConfig
) -> tuple[str, ...]:
    """会社概要等の公開情報に一致した事業形態キーワードを返す。"""
    text = overview_text(posting)
    return tuple(kw for kw in cfg.business_form_keywords if kw in text)


def is_clear_business_form(
    matched: tuple[str, ...],
    strong_keywords: tuple[str, ...] | None = None,
) -> bool:
    """事業形態が明確と判断できるか（複数一致または強キーワード）。

    ``strong_keywords`` を省略した場合は設定の既定値
    （config.DEFAULT_STRONG_BUSINESS_FORM_KEYWORDS）を使う。
    """
    if len(matched) >= 2:
        return True
    strong = (
        DEFAULT_STRONG_BUSINESS_FORM_KEYWORDS
        if strong_keywords is None
        else strong_keywords
    )
    return any(kw in strong for kw in matched)


def judge_hard_exclusions(posting: RawPosting, cfg: HardConfig) -> list[ExclusionHit]:
    hits: list[ExclusionHit] = []
    # 雇用形態: 読み取れない場合は発火させない（不明を除外に倒さない）。
    if posting.employment_raw is not None:
        if any(kw in posting.employment_raw for kw in cfg.non_regular_keywords):
            hits.append(
                ExclusionHit(
                    rule_id="HARD-EMPTYPE",
                    reason="雇用形態が正社員でないことが明確である",
                    config_ref="hard.non_regular_keywords",
                )
            )
    # 領域外: 対象領域の語が無く、領域外キーワードに一致する場合のみ。
    searchable = searchable_text(posting)
    has_domain = any(word in searchable for word in cfg.target_domain_keywords)
    if not has_domain and any(kw in searchable for kw in cfg.out_of_domain_keywords):
        hits.append(
            ExclusionHit(
                rule_id="HARD-DOMAIN",
                reason="対象領域から明らかに外れる職種である",
                config_ref="hard.out_of_domain_keywords",
            )
        )
    # 事業形態: 明確に判断できる場合のみ。
    matched_biz = matched_business_form_keywords(posting, cfg)
    if is_clear_business_form(matched_biz, cfg.strong_business_form_keywords):
        hits.append(
            ExclusionHit(
                rule_id="HARD-BIZFORM",
                reason="事業形態が SIer・コンサル寄りと公開情報から明確である",
                config_ref="hard.business_form_keywords",
            )
        )
    return hits
