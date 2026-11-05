"""SmartRecruiters Posting API アダプタ（公式公開 API・公開求人のみ）。"""

from __future__ import annotations

from ..models import SourceType
from ..registry import SourceEntry
from .base import fetch_json, make_posting


class SmartRecruitersAdapter:
    def __init__(self, boards: tuple[str, ...], timeout_sec: int = 30) -> None:
        self._boards = boards
        self._timeout = timeout_sec

    def fetch(self, source: SourceEntry) -> list:
        postings = []
        for board in self._boards:
            data = fetch_json(
                f"https://api.smartrecruiters.com/v1/companies/{board}/postings",
                self._timeout,
            )
            content = data.get("content", []) if isinstance(data, dict) else []
            for job in content:
                location = job.get("location") or {}
                city = location.get("city")
                postings.append(
                    make_posting(
                        source,
                        SourceType.PUBLIC,
                        f"https://jobs.smartrecruiters.com/{board}/{job.get('id', '')}",
                        str((job.get("company") or {}).get("name", board)),
                        str(job.get("name", "")),
                        "",
                        city,
                    )
                )
        return postings
