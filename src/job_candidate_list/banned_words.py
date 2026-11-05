"""禁止ワードスキャン（§3.3）。

ヒット箇所の周辺文は返さない。例外メッセージには件数のみを含め、
禁止ワード自体をログ・例外に残さない。
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple, Sequence

from .errors import BannedWordHit, ConfigError


class BannedHit(NamedTuple):
    text_index: int
    word: str


def load_banned_words(path: Path) -> list[str]:
    """禁止ワードリストを読む。ファイルが無い場合は ConfigError。"""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"禁止ワードファイルが存在しません: {path.name}")
    words: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        word = line.strip()
        if word and not word.startswith("#"):
            words.append(word)
    return words


def scan_banned_words(texts: Sequence[str], words: Sequence[str]) -> list[BannedHit]:
    hits: list[BannedHit] = []
    for index, text in enumerate(texts):
        for word in words:
            if word and word in text:
                hits.append(BannedHit(text_index=index, word=word))
    return hits


def assert_no_banned_words(texts: Sequence[str], words: Sequence[str]) -> None:
    hits = scan_banned_words(texts, words)
    if hits:
        raise BannedWordHit(f"禁止ワードが {len(hits)} 件ヒットしました")
