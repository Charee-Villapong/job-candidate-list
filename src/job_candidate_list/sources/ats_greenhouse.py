"""Greenhouse Job Board API アダプタ（公式公開 API・認証不要の GET のみ）。"""

from __future__ import annotations

from ..models import SourceType
from ..registry import SourceEntry
from .base import fetch_json, make_posting


class GreenhouseAdapter:
    def __init__(self, boards: tuple[str, ...], timeout_sec: int = 30) -> None:
        self._boards = boards
        self._timeout = timeout_sec

    def fetch(self, source: SourceEntry) -> list:
        postings = []
        for board in self._boards:
            data = fetch_json(
                f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
                "?content=true",
                self._timeout,
            )
            jobs = data.get("jobs", []) if isinstance(data, dict) else []
            for job in jobs:
                postings.append(
                    make_posting(
                        source,
                        SourceType.PUBLIC,
                        str(job.get("absolute_url", "")),
                        board,
                        str(job.get("title", "")),
                        str(job.get("content", "")),
                        (job.get("location") or {}).get("name"),
                    )
                )
        return postings
