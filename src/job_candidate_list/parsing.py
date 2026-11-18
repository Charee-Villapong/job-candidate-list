"""複数モジュールで共有する小さなパース部品。

各モジュールに重複していた同一実装をここへ寄せる。失敗時の扱い
（None をどう解釈するか等の方針）は呼び出し側に残し、本モジュールは
現行の変換挙動だけをそのまま提供する。
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path


def split_clauses(text: str) -> list[str]:
    """テキストを「。」と改行で文節に分割し、空白のみの節を除く。"""
    return [c for c in re.split(r"[。\n]", text) if c.strip()]


def load_yaml_raw(path: Path) -> object:
    """YAML ファイルを読み、生の Python オブジェクトを返す低レベル処理。

    例外（ImportError／OSError／YAML 構文エラー）はそのまま送出する。
    エラーをどう扱うかは呼び出し側ローダの方針に従う（C-062 では
    方針を統一しない: ConfigError で止める／一部そのまま伝播させる／
    空として継続する、の現行3通りを各ローダ側で維持する）。
    """
    import yaml

    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def parse_iso_date(value: str) -> dt.date | None:
    """ISO 形式（YYYY-MM-DD）の日付文字列を date にする。それ以外は None。"""
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        return None


_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
)


def parse_iso_datetime(value: str) -> dt.datetime | None:
    """日時文字列を datetime にする。解釈できなければ None。

    ISO 形式（``isoformat()`` の出力。区切りは ``T`` でも空白でもよい）に
    加え、シート上で使われる ``YYYY-MM-DD HH:MM`` 形式（スラッシュ区切り
    を含む）も受け付ける。Sheets の表示整形で区切りや秒の有無が変わっても
    読み戻せるようにするため。
    """
    if not value:
        return None
    text = value.strip()
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in _DATETIME_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
