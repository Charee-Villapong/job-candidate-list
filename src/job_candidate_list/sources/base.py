"""情報源アダプタの共通インターフェースと ATS 共通の取得補助。

取得は標準ライブラリ urllib のみで行い、取得エラー時は
``SourceFetchError`` を送出する（pipeline は当該情報源をスキップし、
リトライしない）。読み取れない項目は推測で埋めず None とする。

補正で追加した共通処理:

- 会社名の解決: ボード識別子 → 公開されている正式な会社名の対応を
  ローカル設定 ``config/local/company_names.local.yaml``（gitignore 対象）
  で持てるようにし、対応がある場合はそちらを候補の会社名に使う。
  対応が無いボードは従来どおりボード識別子を使う。
- 年収の抽出: 本文テキスト等から日本円表記（万円／¥／円）の年収レンジ
  だけを抽出する。外貨表記は円換算せず、年収（円）としては扱わない。
- HTML のテキスト化: エンティティを戻しタグを除いた本文を渡す。
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Protocol

from ..errors import SourceFetchError
from ..models import RawPosting, SourceType
from ..parsing import load_yaml_raw
from ..registry import SourceEntry

# ボード識別子 → 正式な会社名の対応表（実値）の既定の置き場。
# 実値ファイルは gitignore 対象で、無ければ対応なしとして扱う。
DEFAULT_COMPANY_NAMES_PATH = Path("config/local/company_names.local.yaml")

# HTTP 取得の既定値（C-065）。従来は fetch_json とアダプタ側に同じ値が
# 個別に直書きされていた。実行時のタイムアウトは設定側
# （collection.timeout_sec）が正本で、呼び出し側がそちらを渡す。
# ここは引数省略時の既定値としてのみ使う。
DEFAULT_TIMEOUT_SEC = 30
USER_AGENT = "job-candidate-list/0.1"

# 年収抽出の調整値（C-065）。値は従来の直書きと完全に同一。
SALARY_CONTEXT_WINDOW_CHARS = 60  # 「年収」の近傍として切り取る文字数
MIN_SALARY_JPY = 100_000  # ¥表記を年収とみなす下限額（円）
JPY_PER_MAN_YEN = 10_000  # 万円 → 円の換算係数


class SourceAdapter(Protocol):
    def fetch(self, source: SourceEntry) -> list[RawPosting]:
        """ボード参照から求人を取得する。エラー時は SourceFetchError。"""
        ...


def fetch_json(url: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            body = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceFetchError(f"取得に失敗しました: {type(exc).__name__}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise SourceFetchError("取得結果の形式が不正です: JSON") from exc


def mapping_jobs(key: str) -> Callable[[object], Any]:
    """dict 形式ペイロードから ``key`` の求人リストを取り出す関数を返す。

    ペイロードが dict でない場合は空リストとする。キーの値が None の
    場合はそのまま返し、反復時に TypeError が伝播する現行挙動を保つ。
    """

    def extract(data: object) -> Any:
        if isinstance(data, Mapping):
            return data.get(key, [])
        return []

    return extract


def list_jobs(data: object) -> Any:
    """list 形式ペイロードをそのまま求人リストとして返す（他は空）。"""
    return data if isinstance(data, list) else []


class AtsBoardAdapter:
    """ATS アダプタ 4 本共通の骨格（ボード走査 → fetch_json → 求人反復）。

    ``fetch_json`` は本モジュールのものを直接は使わず、各アダプタの
    モジュール名前空間にあるものを引数で受け取る（テストがアダプタ
    モジュール側の名前を差し替えて取得を切り分けられるようにするため）。
    求人 1 件ごとの変換（make_posting の引数組み立て）は各アダプタ側に残す。
    """

    def __init__(
        self,
        boards: tuple[str, ...],
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        company_names: Mapping[str, str] | None = None,
    ) -> None:
        self._boards = boards
        self._timeout = timeout_sec
        self._company_names = company_names

    def _iter_board_jobs(
        self,
        fetch_json_fn: Callable[[str, int], object],
        board_url: Callable[[str], str],
        payload_jobs: Callable[[object], Iterable[Any]],
    ) -> Iterator[tuple[str, Any]]:
        for board in self._boards:
            data = fetch_json_fn(board_url(board), self._timeout)
            for job in payload_jobs(data):
                yield board, job


def load_company_names(
    path: Path | None = None,
) -> dict[str, str]:
    """ボード識別子 → 正式な会社名の対応表をローカル設定から読む。

    形式は ``{ボード識別子: 会社名}`` のマッピング、または
    ``company_names:`` 配下に同マッピングを置く形のどちらでもよい。
    ファイルが無い・読めない場合は対応なし（空）として扱い、
    呼び出し側は従来どおりボード識別子へフォールバックする。
    """
    target = Path(path) if path is not None else DEFAULT_COMPANY_NAMES_PATH
    if not target.is_file():
        return {}
    # 読込の低レベル処理は parsing.load_yaml_raw に共通化し、エラー方針
    # （読めない・壊れている場合は全て空 dict で継続するフェイル
    # オープン）は本ローダ側で維持する。
    try:
        data = load_yaml_raw(target)
    except Exception:
        return {}
    if not isinstance(data, Mapping):
        return {}
    inner = data.get("company_names")
    mapping = inner if isinstance(inner, Mapping) else data
    return {
        str(board): str(name)
        for board, name in mapping.items()
        if isinstance(name, str) and name.strip()
    }


def resolve_company_name(
    board: str,
    company_names: Mapping[str, str] | None = None,
    api_company_name: str | None = None,
) -> str:
    """候補の会社名を決める。

    優先順位: ローカル設定の対応表 → API が返す会社名 → ボード識別子。
    ``company_names`` が None の場合は既定の置き場から読み込む。
    """
    names = load_company_names() if company_names is None else company_names
    if board in names:
        return names[board]
    if api_company_name and api_company_name.strip():
        return api_company_name.strip()
    return board


_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(raw: str) -> str:
    """HTML（エンティティエスケープ済み含む）をプレーンテキスト化する。"""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = _TAG_RE.sub("\n", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


_RANGE_SEP = r"(?:〜|～|~|-|–|—)"
_MANEN_RANGE_RE = re.compile(
    rf"(\d[\d,]*(?:\.\d+)?)\s*万(?:円)?\s*{_RANGE_SEP}\s*"
    rf"(\d[\d,]*(?:\.\d+)?)\s*万円"
)
_MANEN_SINGLE_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*万円")
_YEN_RANGE_RE = re.compile(rf"[¥￥]\s*(\d[\d,]+)\s*{_RANGE_SEP}\s*[¥￥]?\s*(\d[\d,]+)")
_YEN_SINGLE_RE = re.compile(r"[¥￥]\s*(\d[\d,]+)")


def _to_man_yen(value: str) -> int:
    return int(float(value.replace(",", "")) * JPY_PER_MAN_YEN)


def _salary_from_match_range(low: int, high: int, raw: str) -> tuple[int, int, str]:
    return (min(low, high), max(low, high), raw.strip())


def extract_jpy_salary(text: str | None) -> tuple[int, int, str] | None:
    """テキストから日本円の年収レンジを抽出する（円換算はしない）。

    「年収」の近傍を優先して探し、無ければテキスト全体から探す。
    万円表記・¥表記のみを対象とし、外貨表記は対象外とする。
    見つからなければ None（推測で埋めない）。
    """
    if not text:
        return None
    windows: list[str] = []
    for match in re.finditer("年収", text):
        windows.append(
            text[match.start() : match.start() + SALARY_CONTEXT_WINDOW_CHARS]
        )
    for window in [*windows, text]:
        found = _MANEN_RANGE_RE.search(window)
        if found:
            return _salary_from_match_range(
                _to_man_yen(found.group(1)),
                _to_man_yen(found.group(2)),
                found.group(0),
            )
        found = _YEN_RANGE_RE.search(window)
        if found:
            low = int(found.group(1).replace(",", ""))
            high = int(found.group(2).replace(",", ""))
            if low >= MIN_SALARY_JPY and high >= MIN_SALARY_JPY:
                return _salary_from_match_range(low, high, found.group(0))
        found = _MANEN_SINGLE_RE.search(window)
        if found:
            value = _to_man_yen(found.group(1))
            return (value, value, found.group(0).strip())
        found = _YEN_SINGLE_RE.search(window)
        if found:
            value = int(found.group(1).replace(",", ""))
            if value >= MIN_SALARY_JPY:
                return (value, value, found.group(0).strip())
    return None


def format_salary_raw(salary_min_jpy: int, salary_max_jpy: int) -> str:
    """年収レンジの表示テキスト（万円で割り切れる場合は万円表記）。"""
    if salary_min_jpy % JPY_PER_MAN_YEN == 0 and salary_max_jpy % JPY_PER_MAN_YEN == 0:
        low = salary_min_jpy // JPY_PER_MAN_YEN
        high = salary_max_jpy // JPY_PER_MAN_YEN
        if low == high:
            return f"{low:,}万円"
        return f"{low:,}万円〜{high:,}万円"
    return f"{salary_min_jpy:,}円〜{salary_max_jpy:,}円"


def unpack_salary(
    salary: tuple[int, int, str] | None,
) -> tuple[int | None, int | None, str | None]:
    """年収の三つ組を (min, max, raw) に展開する。抽出なしは全て None。"""
    if salary is None:
        return None, None, None
    return salary


def make_posting(
    source: SourceEntry,
    source_type: SourceType,
    raw_url: str,
    company_raw: str,
    position_raw: str,
    description: str = "",
    location_raw: str | None = None,
    employment_raw: str | None = None,
    salary_min_jpy: int | None = None,
    salary_max_jpy: int | None = None,
    salary_raw: str | None = None,
    remote_raw: str | None = None,
    company_overview_raw: str | None = None,
    extra_sections: Mapping[str, str] | None = None,
) -> RawPosting:
    sections: dict[str, str] = {}
    if description:
        sections["業務内容"] = description
    if location_raw:
        sections["勤務地"] = location_raw
    for name, text in (extra_sections or {}).items():
        if not text:
            continue
        if name in sections:
            sections[name] = sections[name] + "\n" + text
        else:
            sections[name] = text
    return RawPosting(
        source_id=source.source_id,
        source_type=source_type,
        raw_url=raw_url,
        company_raw=company_raw,
        position_raw=position_raw,
        employment_raw=employment_raw,
        salary_min_jpy=salary_min_jpy,
        salary_max_jpy=salary_max_jpy,
        salary_raw=salary_raw,
        location_raw=location_raw,
        remote_raw=remote_raw,
        sections=sections,
        company_overview_raw=company_overview_raw,
        fetched_on=dt.date.today(),
    )
