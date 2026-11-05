"""Ashby Job Postings API アダプタ（公式公開 API・掲載中のみ）。"""

from __future__ import annotations

from ..models import SourceType
from ..registry import SourceEntry
from .base import fetch_json, make_posting


class AshbyAdapter:
    def __init__(self, boards: tuple[str, ...], timeout_sec: int = 30) -> None:
        self._boards = boards
        self._timeout = timeout_sec

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
                postings.append(
                    make_posting(
                        source,
                        SourceType.PUBLIC,
                        str(job.get("jobUrl", "")),
                        board,
                        str(job.get("title", "")),
                        str(job.get("descriptionPlain", "")),
                        job.get("location"),
                        job.get("employmentType"),
                    )
                )
        return postings
