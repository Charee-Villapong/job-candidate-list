"""勤務地判定（対応表ホワイトリストによるローカル判定）（§3.6、追補 A9）。

外部 API・経路計算は使わない。照合順序は追補 A9 の確定どおり:
対応表内の市区町村名が勤務地系セクションに現れる → 適合／現れないが
市区町村らしい表記が抽出できる → 不適合／どちらでもない → 判定不能。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import LocationConfig
from .errors import ConfigError
from .judge_remote import RemoteResult
from .models import LocationDivision, LocationWhitelist, RawPosting, RemoteDivision
from .parsing import load_yaml_raw

__all__ = [
    "LocationResult",
    "LocationWhitelist",
    "judge_location",
    "load_whitelist",
]

# 市区町村そのものを指す一般語は、市区町村名の抽出対象から除く。
_GENERIC_PLACE_WORDS = ("市区町村", "市内", "市区", "地区", "町内", "村内")
_MUNICIPALITY_RE = re.compile(r"[ァ-ヶー一-龥]{2,}?(?:市|区|町|村)")


@dataclass(frozen=True)
class LocationResult:
    division: LocationDivision
    rule_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    matched_municipality: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.division, LocationDivision):
            object.__setattr__(self, "division", LocationDivision(self.division))
        object.__setattr__(self, "rule_ids", tuple(self.rule_ids))
        object.__setattr__(self, "reasons", tuple(self.reasons))


def load_whitelist(path: Path) -> LocationWhitelist:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"勤務地対応表が存在しません: {path.name}")
    # 読込の低レベル処理は parsing.load_yaml_raw に共通化し、エラー方針
    # （ファイルなし・構文不正は ConfigError、構造不正は from_dict の
    # 例外がそのまま伝播する現行挙動）は本ローダ側で維持する。
    try:
        data = load_yaml_raw(path)
    except ImportError as exc:  # pragma: no cover
        raise ConfigError("YAML 読込ライブラリが利用できません") from exc
    except Exception as exc:
        raise ConfigError(f"勤務地対応表の形式が不正です: {path.name}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"勤務地対応表の形式が不正です: {path.name}")
    return LocationWhitelist.from_dict(data)


def _extract_municipalities(text: str, known: set[str]) -> list[str]:
    residual = text
    for name in known:
        residual = residual.replace(name, " ")
    for word in _GENERIC_PLACE_WORDS:
        residual = residual.replace(word, " ")
    return _MUNICIPALITY_RE.findall(residual)


def judge_location(
    posting: RawPosting,
    remote: RemoteResult,
    whitelist: LocationWhitelist,
    cfg: LocationConfig,
) -> LocationResult:
    if remote.division == RemoteDivision.FULL_REMOTE:
        return LocationResult(
            division=LocationDivision.FULLREMOTE_EXEMPT,
            rule_ids=("LOC-FULLREMOTE-EXEMPT",),
            reasons=("出社が一切発生しないため勤務地条件の対象外",),
            matched_municipality=None,
        )
    if remote.division == RemoteDivision.UNKNOWN:
        return LocationResult(
            division=LocationDivision.UNKNOWN,
            rule_ids=("LOC-UNKNOWN", "REMOTE-UNKNOWN"),
            reasons=("リモート頻度が不明のため勤務地も判定不能として扱う",),
            matched_municipality=None,
        )
    text = "\n".join(
        posting.sections[name]
        for name in cfg.target_sections
        if name in posting.sections
    )
    if not text.strip():
        return LocationResult(
            division=LocationDivision.UNKNOWN,
            rule_ids=("LOC-UNKNOWN",),
            reasons=("勤務地系セクションの記載が無い",),
            matched_municipality=None,
        )
    if "転勤" in text or "全国勤務" in text:
        return LocationResult(
            division=LocationDivision.UNKNOWN,
            rule_ids=("LOC-UNKNOWN",),
            reasons=("転勤あり・全国勤務のため主たる勤務地を特定できない",),
            matched_municipality=None,
        )
    if "本社所在地" in text and "勤務地は" not in text:
        return LocationResult(
            division=LocationDivision.UNKNOWN,
            rule_ids=("LOC-UNKNOWN",),
            reasons=("本社所在地のみの記載は勤務地判定の根拠にしない",),
            matched_municipality=None,
        )
    whitelist_names = [e.municipality for e in whitelist.municipalities]
    hits = [name for name in whitelist_names if name in text]
    others = _extract_municipalities(text, set(whitelist_names))
    if hits and not others:
        return LocationResult(
            division=LocationDivision.FIT,
            rule_ids=("LOC-OK",),
            reasons=("勤務地が対応表の範囲内である",),
            matched_municipality=hits[0],
        )
    if hits and others:
        return LocationResult(
            division=LocationDivision.UNKNOWN,
            rule_ids=("LOC-UNKNOWN",),
            reasons=("複数の勤務地があり主たる勤務地を特定できない",),
            matched_municipality=None,
        )
    if others:
        return LocationResult(
            division=LocationDivision.UNFIT,
            rule_ids=("LOC-OUT",),
            reasons=("勤務地が対応表の範囲外であることが明確である",),
            matched_municipality=None,
        )
    return LocationResult(
        division=LocationDivision.UNKNOWN,
        rule_ids=("LOC-UNKNOWN",),
        reasons=("勤務地の市区町村を抽出できない",),
        matched_municipality=None,
    )
