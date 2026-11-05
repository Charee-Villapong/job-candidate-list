"""英語必須判定（必須／非必須／不明／言及なし）（§3.7／§6.5）。

判定の優先順位は §3.7 の確定どおり（必須優先）。根拠は設定
``english.target_sections`` のセクションのみとする。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import EnglishConfig
from .models import EnglishOutcome

_SCORE_RE = re.compile(r"(TOEIC|TOEFL|IELTS|英検)")


@dataclass(frozen=True)
class EnglishResult:
    outcome: EnglishOutcome
    rule_ids: tuple[str, ...]
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, EnglishOutcome):
            object.__setattr__(self, "outcome", EnglishOutcome(self.outcome))
        object.__setattr__(self, "rule_ids", tuple(self.rule_ids))
        object.__setattr__(self, "reasons", tuple(self.reasons))


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"[。\n]", text) if s.strip()]


def _has_required_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    for phrase in phrases:
        if phrase not in text:
            continue
        if "英語" in phrase:
            return True
        # 「必須です」等の一般表現は、英語に言及する文の中にある場合のみ
        # 英語の必須明記として扱う。
        if any(phrase in s and "英語" in s for s in _sentences(text)):
            return True
    return False


def _has_welcome_link(text: str, phrases: tuple[str, ...]) -> bool:
    for sentence in _sentences(text):
        if "英語" in sentence and any(p in sentence for p in phrases):
            return True
    return False


def judge_english(sections: dict[str, str], cfg: EnglishConfig) -> EnglishResult:
    target_texts = {
        name: sections[name] for name in cfg.target_sections if name in sections
    }
    target_joined = "\n".join(target_texts.values())
    other_texts = {
        name: text for name, text in sections.items() if name not in target_texts
    }

    # 1. 対象セクション内の必須明記（必須優先）。
    for text in target_texts.values():
        if _has_required_phrase(text, cfg.required_phrases):
            return EnglishResult(
                outcome=EnglishOutcome.REQUIRED,
                rule_ids=("ENG-HARD",),
                reasons=("対象セクション内に英語必須の明記がある",),
            )
    # 2. 必須系セクション内のレベル表現のみ。
    for name, text in sections.items():
        is_mandatory = name in cfg.mandatory_sections or "必須" in name
        if is_mandatory and "英語" in text:
            if any(p in text for p in cfg.level_phrases):
                return EnglishResult(
                    outcome=EnglishOutcome.REQUIRED,
                    rule_ids=("ENG-HARD",),
                    reasons=("必須系セクション内に英語のレベル表現がある",),
                )
    has_mention = "英語" in target_joined or bool(_SCORE_RE.search(target_joined))
    # 3. 数値スコアの結びつき。
    if _SCORE_RE.search(target_joined):
        if _has_welcome_link(target_joined, cfg.welcome_phrases):
            return EnglishResult(
                outcome=EnglishOutcome.NOT_REQUIRED,
                rule_ids=("ENG-WELCOME-OK",),
                reasons=("英語の数値目安が歓迎表現と結びついている",),
            )
        return EnglishResult(
            outcome=EnglishOutcome.UNKNOWN,
            rule_ids=("ENG-UNKNOWN",),
            reasons=("英語の数値が単独で出現し必須性が読み取れない",),
        )
    # 4. 歓迎表現に結びつく記載のみ。
    if _has_welcome_link(target_joined, cfg.welcome_phrases):
        return EnglishResult(
            outcome=EnglishOutcome.NOT_REQUIRED,
            rule_ids=("ENG-WELCOME-OK",),
            reasons=("英語は歓迎表現に結びつく記載のみである",),
        )
    # 5. 言及はあるが必須性が読み取れない。
    if has_mention:
        return EnglishResult(
            outcome=EnglishOutcome.UNKNOWN,
            rule_ids=("ENG-UNKNOWN",),
            reasons=("英語への言及はあるが必須性が読み取れない",),
        )
    # 6. 対象セクション外にのみ必須明記がある。
    for text in other_texts.values():
        if _has_required_phrase(text, cfg.required_phrases):
            return EnglishResult(
                outcome=EnglishOutcome.UNKNOWN,
                rule_ids=("ENG-UNKNOWN", "ENG-OUT-SECTION"),
                reasons=("英語必須の明記が対象セクション外にのみある",),
            )
    if any("英語" in text for text in other_texts.values()):
        return EnglishResult(
            outcome=EnglishOutcome.UNKNOWN,
            rule_ids=("ENG-UNKNOWN",),
            reasons=("英語への言及が対象セクション外にのみある",),
        )
    # 7. 言及自体がない。
    return EnglishResult(
        outcome=EnglishOutcome.NO_MENTION,
        rule_ids=(),
        reasons=("英語への言及がない",),
    )
