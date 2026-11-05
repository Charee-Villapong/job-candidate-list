"""Lever Postings API アダプタ（公式公開 API・公開状態の求人のみ）。"""

from __future__ import annotations

from ..models import SourceType
from ..registry import SourceEntry
from .base import fetch_json, make_posting


class LeverAdapter:
    def __init__(self, boards: tuple[str, ...], timeout_sec: int = 30) -> None:
        self._boards = boards
        self._timeout = timeout_sec

    def fetch(self, source: SourceEntry) -> list:
        postings = []
        for board in self._boards:
            data = fetch_json(
                f"https://api.lever.co/v0/postings/{board}?mode=json",
                self._timeout,
            )
            jobs = data if isinstance(data, list) else []
            for job in jobs:
                categories = job.get("categories") or {}
                postings.append(
                    make_posting(
                        source,
                        SourceType.PUBLIC,
                        str(job.get("hostedUrl", "")),
                        board,
                        str(job.get("text", "")),
                        str(job.get("descriptionPlain", "")),
                        categories.get("location"),
                        categories.get("commitment"),
                    )
                )
        return postings
