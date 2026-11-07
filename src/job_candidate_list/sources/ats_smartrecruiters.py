"""SmartRecruiters Posting API アダプタ（公式公開 API・公開求人のみ）。

補正: 会社名は API の会社名／ローカル設定の対応表から解決し、勤務地は
市区町村だけでなく地域・国まで含めた文字列で渡す。雇用形態が
構造化データとして返る場合はそれを使う。
"""

from __future__ import annotations

from ..models import SourceType
from ..registry import SourceEntry
from .base import (
    AtsBoardAdapter,
    fetch_json,
    make_posting,
    mapping_jobs,
    resolve_company_name,
)


def _board_url(board: str) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/{board}/postings"


class SmartRecruitersAdapter(AtsBoardAdapter):
    def fetch(self, source: SourceEntry) -> list:
        postings = []
        for board, job in self._iter_board_jobs(
            fetch_json, _board_url, mapping_jobs("content")
        ):
            location = job.get("location") or {}
            location_text = ", ".join(
                str(location[key])
                for key in ("city", "region", "country")
                if location.get(key)
            )
            employment = job.get("typeOfEmployment")
            if isinstance(employment, dict):
                employment = employment.get("label") or employment.get("id")
            postings.append(
                make_posting(
                    source,
                    SourceType.PUBLIC,
                    f"https://jobs.smartrecruiters.com/{board}/{job.get('id', '')}",
                    resolve_company_name(
                        board,
                        self._company_names,
                        api_company_name=(job.get("company") or {}).get("name"),
                    ),
                    str(job.get("name", "")),
                    "",
                    location_text or None,
                    str(employment) if employment else None,
                )
            )
        return postings
