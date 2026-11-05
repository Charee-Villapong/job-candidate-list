"""Ashby Job Postings API アダプタ（公式公開 API・掲載中のみ）。

補正: 会社名はローカル設定の対応表（無ければボード識別子）、本文全体を
リモート判定の原文（remote_raw）とし、報酬サマリ・本文から日本円の
年収レンジを抽出する（外貨サマリは円として扱わない）。
"""

from __future__ import annotations

from typing import Mapping

from ..models import SourceType
from ..registry import SourceEntry
from .base import (
    extract_jpy_salary,
    fetch_json,
    make_posting,
    resolve_company_name,
)


class AshbyAdapter:
    def __init__(
        self,
        boards: tuple[str, ...],
        timeout_sec: int = 30,
        company_names: Mapping[str, str] | None = None,
    ) -> None:
        self._boards = boards
        self._timeout = timeout_sec
        self._company_names = company_names

    def fetch(self, source: SourceEntry) -> list:
        postings = []
        for board in self._boards:
            data = fetch_json(
                "https://api.ashbyhq.com/posting-api/job-board/"
                f"{board}?includeCompensation=true",
                self._timeout,
            )
            jobs = data.get("jobs", []) if isinstance(data, dict) else []
            for job in jobs:
                if job.get("isListed") is False:
                    continue
                description = str(job.get("descriptionPlain", "") or "")
                location = job.get("location")
                secondary = [
                    str(item.get("location"))
                    for item in job.get("secondaryLocations") or []
                    if isinstance(item, dict) and item.get("location")
                ]
                location_text = location
                if secondary:
                    location_text = " / ".join(
                        part for part in [location, *secondary] if part
                    )
                compensation = job.get("compensation") or {}
                salary_text = " ".join(
                    str(compensation.get(key) or "")
                    for key in (
                        "compensationTierSummary",
                        "scrapeableCompensationSalarySummary",
                    )
                ).strip()
                salary = extract_jpy_salary(salary_text) or extract_jpy_salary(
                    description
                )
                salary_min = salary_max = None
                salary_raw = None
                if salary is not None:
                    salary_min, salary_max, salary_raw = salary
                elif salary_text:
                    # 外貨等のサマリは原文のまま表示テキストとしてのみ渡す
                    # （円の min/max は付けない）。
                    salary_raw = salary_text
                workplace = job.get("workplaceType")
                remote_raw = description
                if workplace:
                    remote_raw = f"{description}\nworkplaceType: {workplace}"
                postings.append(
                    make_posting(
                        source,
                        SourceType.PUBLIC,
                        str(job.get("jobUrl", "")),
                        resolve_company_name(board, self._company_names),
                        str(job.get("title", "")),
                        description,
                        location_text,
                        job.get("employmentType"),
                        salary_min_jpy=salary_min,
                        salary_max_jpy=salary_max,
                        salary_raw=salary_raw,
                        remote_raw=remote_raw or None,
                    )
                )
        return postings
