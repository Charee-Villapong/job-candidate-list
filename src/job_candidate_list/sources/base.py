"""情報源アダプタの共通インターフェースと ATS 共通の取得補助。

取得は標準ライブラリ urllib のみで行い、取得エラー時は
``SourceFetchError`` を送出する（pipeline は当該情報源をスキップし、
リトライしない）。読み取れない項目は推測で埋めず None とする。
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.request
from typing import Protocol

from ..errors import SourceFetchError
from ..models import RawPosting, SourceType
from ..registry import SourceEntry


class SourceAdapter(Protocol):
    def fetch(self, source: SourceEntry) -> list[RawPosting]:
        """ボード参照から求人を取得する。エラー時は SourceFetchError。"""
        ...


def fetch_json(url: str, timeout_sec: int = 30) -> object:
    request = urllib.request.Request(
        url, headers={"User-Agent": "job-candidate-list/0.1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            body = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceFetchError(f"取得に失敗しました: {type(exc).__name__}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise SourceFetchError("取得結果の形式が不正です: JSON") from exc


def make_posting(
    source: SourceEntry,
    source_type: SourceType,
    raw_url: str,
    company_raw: str,
    position_raw: str,
    description: str = "",
    location_raw: str | None = None,
    employment_raw: str | None = None,
) -> RawPosting:
    sections: dict[str, str] = {}
    if description:
        sections["業務内容"] = description
    if location_raw:
        sections["勤務地"] = location_raw
    return RawPosting(
        source_id=source.source_id,
        source_type=source_type,
        raw_url=raw_url,
        company_raw=company_raw,
        position_raw=position_raw,
        employment_raw=employment_raw,
        location_raw=location_raw,
        sections=sections,
        company_overview_raw=None,
        fetched_on=dt.date.today(),
    )
