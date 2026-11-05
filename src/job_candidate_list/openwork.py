"""OpenWork 突合・点数付与（§3.14／§6.7、追補 A7）。

ゲートが無効な場合は provider を一切呼ばず空欄を返す。取得は
正規化会社名単位で週 1 回までとし、キャッシュは呼び出し側の責務とする
（本関数は 1 候補 1 回の付与のみを行う）。再取得に失敗した場合は
``previous`` の前回値を保持し、取得日は前回のままにする。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol

from .config import OpenworkConfig
from .errors import OpenworkBlocked
from .models import CandidateRecord, LinkCell

__all__ = [
    "OpenworkBlocked",
    "OpenworkCompanyRef",
    "OpenworkProvider",
    "OpenworkResult",
    "attach_openwork",
]


@dataclass(frozen=True)
class OpenworkCompanyRef:
    company_norm: str
    score: float
    url: str
    location_text: str | None = None
    industry_text: str | None = None


@dataclass(frozen=True)
class OpenworkResult:
    score: float | None
    url: LinkCell | None
    fetched_on: dt.date | None
    rule_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_ids", tuple(self.rule_ids))
        if self.url is not None and not isinstance(self.url, LinkCell):
            object.__setattr__(self, "url", LinkCell(*self.url))


class OpenworkProvider(Protocol):
    def search_company(self, company_norm: str) -> list[OpenworkCompanyRef]:
        """外部へ送るのは正規化した会社名のみ。"""
        ...


def _failure(rule_id: str, previous: OpenworkResult | None) -> OpenworkResult:
    if previous is not None and (
        previous.score is not None
        or previous.url is not None
        or previous.fetched_on is not None
    ):
        return OpenworkResult(
            score=previous.score,
            url=previous.url,
            fetched_on=previous.fetched_on,
            rule_ids=(rule_id,),
        )
    return OpenworkResult(score=None, url=None, fetched_on=None, rule_ids=(rule_id,))


def _location_compatible(ref: OpenworkCompanyRef, candidate: CandidateRecord) -> bool:
    if not ref.location_text:
        return True
    candidate_location = candidate.location_text
    if not candidate_location or candidate_location == "不明":
        return True
    return (
        ref.location_text == candidate_location
        or ref.location_text in candidate_location
        or candidate_location in ref.location_text
    )


def attach_openwork(
    candidate: CandidateRecord,
    provider: OpenworkProvider,
    gate_enabled: bool,
    cfg: OpenworkConfig,
    previous: OpenworkResult | None = None,
) -> OpenworkResult:
    if not gate_enabled:
        if previous is not None and (
            previous.score is not None
            or previous.url is not None
            or previous.fetched_on is not None
        ):
            return OpenworkResult(
                score=previous.score,
                url=previous.url,
                fetched_on=previous.fetched_on,
                rule_ids=("OW-DISABLED",),
            )
        return OpenworkResult(
            score=None, url=None, fetched_on=None, rule_ids=("OW-DISABLED",)
        )
    try:
        refs = provider.search_company(candidate.company_norm)
    except OpenworkBlocked:
        raise
    except Exception:
        return _failure("OW-UNAVAILABLE", previous)
    if not refs:
        return _failure("OW-NOTFOUND", previous)
    matched = [
        ref
        for ref in refs
        if ref.company_norm == candidate.company_norm
        and _location_compatible(ref, candidate)
    ]
    if len(matched) != 1:
        return _failure("OW-NOMATCH", previous)
    ref = matched[0]
    return OpenworkResult(
        score=ref.score,
        url=LinkCell("OpenWork", ref.url),
        fetched_on=dt.date.today(),
        rule_ids=(),
    )
