"""リモート頻度の出社日数基準への正規化（§3.4、追補 A11）。

変換は設定 ``remote.rules``（正規表現 → 区分）の順序評価のみで行い、
同義表記はパターンの選択肢で吸収する。日数の数値計算は行わない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import RemoteConfig
from .models import RemoteDivision


@dataclass(frozen=True)
class RemoteResult:
    division: RemoteDivision
    rule_ids: tuple[str, ...]
    raw_found: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.division, RemoteDivision):
            object.__setattr__(self, "division", RemoteDivision(self.division))
        object.__setattr__(self, "rule_ids", tuple(self.rule_ids))


def normalize_remote_frequency(raw: str | None, cfg: RemoteConfig) -> RemoteResult:
    if raw is None or not raw.strip():
        return RemoteResult(
            division=RemoteDivision.UNKNOWN,
            rule_ids=("REMOTE-UNKNOWN",),
            raw_found=None,
        )
    for rule in cfg.rules:
        match = re.search(rule.pattern, raw)
        if match:
            return RemoteResult(
                division=RemoteDivision(rule.division),
                rule_ids=("REMOTE-NORMALIZED",),
                raw_found=match.group(0),
            )
    return RemoteResult(
        division=RemoteDivision.UNKNOWN,
        rule_ids=("REMOTE-UNKNOWN",),
        raw_found=None,
    )
