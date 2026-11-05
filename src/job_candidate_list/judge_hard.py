"""その他ハード除外（雇用形態／領域外／事業形態）（§3.8）。

事業形態は公開情報から明確に判断できる場合のみヒットさせる。
曖昧な場合（キーワードが弱く単独で現れる場合）は本関数では返さず、
適合度付け側で BIZFORM-UNKNOWN（要確認）として扱う。
"""

from __future__ import annotations

from typing import NamedTuple

from .config import HardConfig
from .models import RawPosting

# 対象領域の判定に使う一般化キーワード。志向プロファイルの対象領域
# （データ／AI 領域）を表す語であり、HardConfig には対象領域側の
# キーワード置き場が無いため、本モジュールで補助的に保持する。
_TARGET_DOMAIN_WORDS: tuple[str, ...] = ("データ", "AI", "機械学習")

# 事業形態の明確な判断に使う強キーワード（単独でも明確とみなす）。
_STRONG_BIZFORM_WORDS: tuple[str, ...] = (
    "システムインテグレーション事業",
    "SI事業",
)


class ExclusionHit(NamedTuple):
    rule_id: str
    reason: str
    config_ref: str


def matched_business_form_keywords(
    posting: RawPosting, cfg: HardConfig
) -> tuple[str, ...]:
    """会社概要等の公開情報に一致した事業形態キーワードを返す。"""
    text = "\n".join(
        part
        for part in (
            posting.company_overview_raw,
            posting.sections.get("会社概要"),
        )
        if part
    )
    return tuple(kw for kw in cfg.business_form_keywords if kw in text)


def is_clear_business_form(
    matched: tuple[str, ...],
) -> bool:
    """事業形態が明確と判断できるか（複数一致または強キーワード）。"""
    if len(matched) >= 2:
        return True
    return any(kw in _STRONG_BIZFORM_WORDS for kw in matched)


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
    searchable = "\n".join(
        part
        for part in (
            posting.position_raw,
            posting.company_overview_raw,
            *posting.sections.values(),
        )
        if part
    )
    has_domain = any(word in searchable for word in _TARGET_DOMAIN_WORDS)
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
    if is_clear_business_form(matched_biz):
        hits.append(
            ExclusionHit(
                rule_id="HARD-BIZFORM",
                reason="事業形態が SIer・コンサル寄りと公開情報から明確である",
                config_ref="hard.business_form_keywords",
            )
        )
    return hits
