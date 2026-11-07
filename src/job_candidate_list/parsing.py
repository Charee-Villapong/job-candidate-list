"""複数モジュールで共有する小さなパース部品。

各モジュールに重複していた同一実装をここへ寄せる。失敗時の扱い
（None をどう解釈するか等の方針）は呼び出し側に残し、本モジュールは
現行の変換挙動だけをそのまま提供する。
"""

from __future__ import annotations

import datetime as dt
import re


def split_clauses(text: str) -> list[str]:
    """テキストを「。」と改行で文節に分割し、空白のみの節を除く。"""
    return [c for c in re.split(r"[。\n]", text) if c.strip()]


def parse_iso_date(value: str) -> dt.date | None:
    """ISO 形式（YYYY-MM-DD）の日付文字列を date にする。それ以外は None。"""
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        return None
