"""Greenhouse Job Board API アダプタ（公式公開 API・認証不要の GET のみ）。

補正: 会社名は API の company_name／ローカル設定の対応表から解決し、
本文（HTML・エンティティエスケープ済み）をテキスト化して判定セクション
とリモート原文に渡す。雇用形態はメタデータから、年収は本文中の
日本円表記から抽出する。
"""

from __future__ import annotations

from ..models import SourceType
from ..registry import SourceEntry
from .base import (
    AtsBoardAdapter,
    extract_jpy_salary,
    fetch_json,
    html_to_text,
    make_posting,
    mapping_jobs,
    resolve_company_name,
    unpack_salary,
)


def _metadata_value(job: dict, name: str) -> str | None:
    for item in job.get("metadata") or []:
        if not isinstance(item, dict) or item.get("name") != name:
            continue
        value = item.get("value")
        if isinstance(value, list):
            value = value[0] if value else None
        if value is not None and str(value).strip():
            return str(value)
    return None


def _board_url(board: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs" "?content=true"


class GreenhouseAdapter(AtsBoardAdapter):
    def fetch(self, source: SourceEntry) -> list:
        postings = []
        for board, job in self._iter_board_jobs(
            fetch_json, _board_url, mapping_jobs("jobs")
        ):
            description = html_to_text(str(job.get("content", "") or ""))
            salary_min, salary_max, salary_raw = unpack_salary(
                extract_jpy_salary(description)
            )
            postings.append(
                make_posting(
                    source,
                    SourceType.PUBLIC,
                    str(job.get("absolute_url", "")),
                    resolve_company_name(
                        board,
                        self._company_names,
                        api_company_name=job.get("company_name"),
                    ),
                    str(job.get("title", "")),
                    description,
                    (job.get("location") or {}).get("name"),
                    _metadata_value(job, "Employment Type"),
                    salary_min_jpy=salary_min,
                    salary_max_jpy=salary_max,
                    salary_raw=salary_raw,
                    remote_raw=description or None,
                )
            )
        return postings
