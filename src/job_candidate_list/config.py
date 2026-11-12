"""設定ファイル（YAML）の読込と検証（architecture.md §3.1／§4、追補 A2）。

判定に効く値は全て設定側に置き、本モジュールが唯一の入口になる。
各サブ設定は ``from_dict`` を持ち、未知キー・必須キー欠落・型不一致は
``ConfigError``（キー名をメッセージに含める）とする。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Mapping

from .errors import ConfigError
from .models import RemoteDivision
from .parsing import load_yaml_raw

# リモート区分の閉じた集合は models.RemoteDivision が正本。
# ここでは検証用にその値のタプルを導出するだけにする（二重管理の解消）。
_REMOTE_DIVISIONS = tuple(str(d) for d in RemoteDivision)

# 判定キーワードの既定値（C-064）。従来 judge_hard／fitness のコード内に
# 直書きされていた値と完全に同一であり、設定ファイルでキーを省略した
# 場合はこの既定値が従来どおりの判定を再現する。正本はここ（設定側）とし、
# 判定モジュール側に同値のリテラルを置かない。
DEFAULT_TARGET_DOMAIN_KEYWORDS: tuple[str, ...] = ("データ", "AI", "機械学習")
DEFAULT_STRONG_BUSINESS_FORM_KEYWORDS: tuple[str, ...] = (
    "システムインテグレーション事業",
    "SI事業",
)
DEFAULT_WEAK_BUSINESS_FORM_KEYWORDS: tuple[str, ...] = (
    "常駐",
    "コンサルティング",
    "受託",
    "SI",
)


def _check_unknown(data: Mapping[str, Any], allowed: set[str], owner: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"{owner} に未知のキーがあります: {sorted(unknown)}")


def _as_str_tuple(value: Any, key: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ConfigError(f"設定キーの型が不正です: {key}")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise ConfigError(f"設定キーの型が不正です: {key}")
        result.append(item)
    return tuple(result)


def _as_int(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"設定キーの型が不正です: {key}")
    return value


def _as_float(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"設定キーの型が不正です: {key}")
    return float(value)


def _as_bool(value: Any, key: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"設定キーの型が不正です: {key}")
    return value


def _as_str(value: Any, key: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"設定キーの型が不正です: {key}")
    return value


def _as_path(value: Any, key: str) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        return value
    if isinstance(value, str):
        return Path(value)
    raise ConfigError(f"設定キーの型が不正です: {key}")


def _as_date(value: Any, key: str) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(f"設定キーの型が不正です: {key}") from exc
    raise ConfigError(f"設定キーの型が不正です: {key}")


def _as_opt_str(value: Any, key: str) -> str | None:
    if value is None:
        return None
    return _as_str(value, key)


def _as_opt_int(value: Any, key: str) -> int | None:
    if value is None:
        return None
    return _as_int(value, key)


def _as_mixed_handling(value: Any, key: str) -> str:
    result = _as_str(value, key)
    if result != "check":
        raise ConfigError(f"設定キーの値が範囲外です: {key}")
    return result


def _as_remote_division(value: Any, key: str) -> str:
    result = _as_str(value, key)
    if result not in _REMOTE_DIVISIONS:
        raise ConfigError(f"設定キーの値が範囲外です: {key}")
    return result


def _as_rules(value: Any, key: str) -> tuple["RemoteRule", ...]:
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"設定キーの型が不正です: {key}")
    rules = []
    for item in value:
        if isinstance(item, RemoteRule):
            if item.division not in _REMOTE_DIVISIONS:
                raise ConfigError("設定キーの値が範囲外です: division")
            rules.append(item)
        elif isinstance(item, Mapping):
            rules.append(RemoteRule.from_dict(item))
        else:
            raise ConfigError(f"設定キーの型が不正です: {key}")
    return tuple(rules)


def _as_sheet_names(value: Any, key: str) -> "SheetNamesConfig":
    if value is None:
        return SheetNamesConfig()
    if isinstance(value, SheetNamesConfig):
        return value
    if isinstance(value, Mapping):
        return SheetNamesConfig.from_dict(value)
    raise ConfigError(f"設定キーの型が不正です: {key}")


def _as_ats_boards(value: Any, key: str) -> dict[str, tuple[str, ...]]:
    raw = value or {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"設定キーの型が不正です: {key}")
    return {str(k): _as_str_tuple(v, key) for k, v in raw.items()}


def _build_from_dict(
    cls: type,
    data: Mapping[str, Any],
    owner: str,
    converters: Mapping[str, Callable[[Any, str], Any]],
) -> Any:
    """``from_dict`` の定型（未知キー検査 → 変換 → 構築）を共通化する。

    変換は ``converters`` の定義順に行う（各クラスの従来の変換順を
    維持する）。キーが無い場合は dataclass のフィールド既定値
    （default／default_factory）を変換して使う。既定値の無い必須キーの
    欠落は ConfigError（キー名をメッセージに含める）とする。
    """
    _check_unknown(data, set(converters), owner)
    field_map = {f.name: f for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, convert in converters.items():
        if key in data:
            raw = data[key]
        else:
            f = field_map[key]
            if f.default is not MISSING:
                raw = f.default
            elif f.default_factory is not MISSING:
                raw = f.default_factory()
            else:
                raise ConfigError(f"{owner} に必須キーがありません: {key}")
        kwargs[key] = convert(raw, key)
    return cls(**kwargs)


# C-066（JCL-08）で削除した profile のキー。既存のローカル設定に
# 残っている場合に未知キーエラーで落とさないよう、読込時に読み捨てる
# （後方互換。値そのものはどこからも参照しない）。
_REMOVED_PROFILE_KEYS = frozenset(
    {"profile_version", "location_condition_enabled", "target_role_labels"}
)


@dataclass(frozen=True)
class ProfileConfig:
    target_domains: tuple[str, ...]
    salary_target_jpy: int
    employment_allowed: tuple[str, ...]
    # 未実装（予約）: 希望頻度が指定された場合のみ適合条件化する。
    # 現行は値を入れても判定は変わらない。
    remote_preference: str | None = None
    # 未実装（予約）: 本人指定時に勤務地条件のハード除外化を実装する
    # 切替の受け口。現行は True でもハード除外にならない。
    location_hard_exclude: bool = False
    search_queries: tuple[str, ...] = ()
    last_updated: dt.date | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProfileConfig":
        # 削除済みキーは未知キー検査の前に落とす（_REMOVED_PROFILE_KEYS）。
        data = {
            key: value
            for key, value in data.items()
            if key not in _REMOVED_PROFILE_KEYS
        }
        # remote_preference は従来どおり他の必須キーより先に変換する。
        return _build_from_dict(
            cls,
            data,
            "profile",
            {
                "remote_preference": _as_opt_str,
                "target_domains": _as_str_tuple,
                "salary_target_jpy": _as_int,
                "employment_allowed": _as_str_tuple,
                "location_hard_exclude": _as_bool,
                "search_queries": _as_str_tuple,
                "last_updated": _as_date,
            },
        )


@dataclass(frozen=True)
class NormalizeConfig:
    tracking_params: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NormalizeConfig":
        return _build_from_dict(
            cls, data, "normalize", {"tracking_params": _as_str_tuple}
        )


@dataclass(frozen=True)
class DutyConfig:
    target_sections: tuple[str, ...]
    upstream_keywords: tuple[str, ...]
    impl_keywords: tuple[str, ...]
    # 未実装: 仕様では主務判定の理由へ補助情報として残す定義だが、
    # その反映は未実装（現行は判定結果・理由に現れない）。
    title_assist_keywords: tuple[str, ...] = ()
    primary_markers: tuple[str, ...] = ()
    mixed_handling: str = "check"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DutyConfig":
        # mixed_handling は従来どおり他の必須キーより先に変換・検証する。
        return _build_from_dict(
            cls,
            data,
            "duty",
            {
                "mixed_handling": _as_mixed_handling,
                "target_sections": _as_str_tuple,
                "upstream_keywords": _as_str_tuple,
                "impl_keywords": _as_str_tuple,
                "title_assist_keywords": _as_str_tuple,
                "primary_markers": _as_str_tuple,
            },
        )


@dataclass(frozen=True)
class LocationConfig:
    target_sections: tuple[str, ...]
    whitelist_file: Path | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LocationConfig":
        return _build_from_dict(
            cls,
            data,
            "location",
            {"target_sections": _as_str_tuple, "whitelist_file": _as_path},
        )


@dataclass(frozen=True)
class RemoteRule:
    pattern: str
    division: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RemoteRule":
        # division は従来どおり pattern より先に変換・検証する。
        return _build_from_dict(
            cls,
            data,
            "remote.rules",
            {"division": _as_remote_division, "pattern": _as_str},
        )


@dataclass(frozen=True)
class RemoteConfig:
    rules: tuple[RemoteRule, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RemoteConfig":
        return _build_from_dict(cls, data, "remote", {"rules": _as_rules})


@dataclass(frozen=True)
class EnglishConfig:
    target_sections: tuple[str, ...]
    mandatory_sections: tuple[str, ...] = ()
    required_phrases: tuple[str, ...] = ()
    welcome_phrases: tuple[str, ...] = ()
    level_phrases: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EnglishConfig":
        return _build_from_dict(
            cls,
            data,
            "english",
            {
                "target_sections": _as_str_tuple,
                "mandatory_sections": _as_str_tuple,
                "required_phrases": _as_str_tuple,
                "welcome_phrases": _as_str_tuple,
                "level_phrases": _as_str_tuple,
            },
        )


@dataclass(frozen=True)
class HardConfig:
    non_regular_keywords: tuple[str, ...] = ()
    out_of_domain_keywords: tuple[str, ...] = ()
    business_form_keywords: tuple[str, ...] = ()
    # 職種名（タイトル）ベースの除外キーワード。タイトルに一致すれば
    # 対象領域の語の有無にかかわらずハード除外する（judge_hard の
    # HARD-TITLE）。他のキーワードリストと同様、既定は空で
    # 実値は設定ファイル側が持つ。
    title_exclusion_keywords: tuple[str, ...] = ()
    # 対象領域の判定に使うキーワード（従来 judge_hard に直書き。C-064）。
    target_domain_keywords: tuple[str, ...] = DEFAULT_TARGET_DOMAIN_KEYWORDS
    # 事業形態を単独でも明確とみなす強キーワード（同上）。
    strong_business_form_keywords: tuple[str, ...] = (
        DEFAULT_STRONG_BUSINESS_FORM_KEYWORDS
    )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HardConfig":
        return _build_from_dict(
            cls,
            data,
            "hard",
            {
                "non_regular_keywords": _as_str_tuple,
                "out_of_domain_keywords": _as_str_tuple,
                "business_form_keywords": _as_str_tuple,
                "title_exclusion_keywords": _as_str_tuple,
                "target_domain_keywords": _as_str_tuple,
                "strong_business_form_keywords": _as_str_tuple,
            },
        )


@dataclass(frozen=True)
class FitnessConfig:
    salary_check_threshold_jpy: int
    high_threshold: int
    domain_match_points: int = 0
    salary_points: int = 0
    condition_points: int = 0
    location_points: int = 0
    salary_target_jpy: int | None = None
    target_domains: tuple[str, ...] = ()
    # 事業形態の曖昧判定（BIZFORM-UNKNOWN）に使う弱キーワード
    # （従来 fitness に直書き。C-064）。
    business_form_weak_keywords: tuple[str, ...] = DEFAULT_WEAK_BUSINESS_FORM_KEYWORDS

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FitnessConfig":
        # salary_target_jpy は従来どおり他の必須キーより先に変換する。
        return _build_from_dict(
            cls,
            data,
            "fitness",
            {
                "salary_target_jpy": _as_opt_int,
                "salary_check_threshold_jpy": _as_int,
                "high_threshold": _as_int,
                "domain_match_points": _as_int,
                "salary_points": _as_int,
                "condition_points": _as_int,
                "location_points": _as_int,
                "target_domains": _as_str_tuple,
                "business_form_weak_keywords": _as_str_tuple,
            },
        )


@dataclass(frozen=True)
class PresentationConfig:
    weekly_limit: int = 20

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PresentationConfig":
        return _build_from_dict(cls, data, "presentation", {"weekly_limit": _as_int})


@dataclass(frozen=True)
class ValidityConfig:
    listing_valid_days: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ValidityConfig":
        return _build_from_dict(cls, data, "validity", {"listing_valid_days": _as_int})


@dataclass(frozen=True)
class CollectionConfig:
    # 未実装: 仕様の確定条件だが、間隔の執行（待機処理）は未実装。
    # 現行は値を変えても取得間隔は変わらない。
    request_interval_sec: int
    per_source_max_items: int
    timeout_sec: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CollectionConfig":
        return _build_from_dict(
            cls,
            data,
            "collection",
            {
                "request_interval_sec": _as_int,
                "per_source_max_items": _as_int,
                "timeout_sec": _as_int,
            },
        )


@dataclass(frozen=True)
class GuardConfig:
    max_close_ratio: float
    max_total_change_ratio: float

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GuardConfig":
        return _build_from_dict(
            cls,
            data,
            "guard",
            {
                "max_close_ratio": _as_float,
                "max_total_change_ratio": _as_float,
            },
        )


@dataclass(frozen=True)
class OpenworkConfig:
    # 未実装（予約）: 仕様の確定条件（同一会社は週1回まで）に対応する
    # 予約で、第2段階で実装する。False の意味は未定義。
    weekly_once_per_company: bool = True

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OpenworkConfig":
        return _build_from_dict(
            cls, data, "openwork", {"weekly_once_per_company": _as_bool}
        )


@dataclass(frozen=True)
class AutoApplyConfig:
    enabled: bool = False
    weekly_limit: int = 3
    result_unknown_hold_runs: int = 2

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AutoApplyConfig":
        return _build_from_dict(
            cls,
            data,
            "auto_apply",
            {
                "enabled": _as_bool,
                "weekly_limit": _as_int,
                "result_unknown_hold_runs": _as_int,
            },
        )


@dataclass(frozen=True)
class SheetNamesConfig:
    candidates: str = "候補リスト"
    registry: str = "情報源レジストリ"
    exclusions: str = "除外ログ"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SheetNamesConfig":
        # 既定値はフィールド定義（クラス既定値）に一本化されており、
        # _build_from_dict がそちらを参照する（リテラルの二重管理を避ける）。
        return _build_from_dict(
            cls,
            data,
            "sheet_names",
            {
                "candidates": _as_str,
                "registry": _as_str,
                "exclusions": _as_str,
            },
        )


@dataclass(frozen=True)
class LocalConfig:
    spreadsheet_id: str = ""
    sheet_names: SheetNamesConfig = field(default_factory=SheetNamesConfig)
    banned_words_file: Path | None = None
    ats_boards: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    profile_file: Path | None = None
    judge_file: Path | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LocalConfig":
        # sheet_names／ats_boards は従来どおり他のキーより先に変換する。
        return _build_from_dict(
            cls,
            data,
            "local",
            {
                "sheet_names": _as_sheet_names,
                "ats_boards": _as_ats_boards,
                "spreadsheet_id": _as_str,
                "banned_words_file": _as_path,
                "profile_file": _as_path,
                "judge_file": _as_path,
            },
        )


@dataclass(frozen=True)
class AppConfig:
    profile: ProfileConfig
    normalize: NormalizeConfig
    duty: DutyConfig
    location: LocationConfig
    remote: RemoteConfig
    english: EnglishConfig
    hard: HardConfig
    fitness: FitnessConfig
    presentation: PresentationConfig
    validity: ValidityConfig
    collection: CollectionConfig
    guard: GuardConfig
    openwork: OpenworkConfig
    auto_apply: AutoApplyConfig
    local: LocalConfig

    @classmethod
    def from_dicts(
        cls,
        profile_data: Mapping[str, Any],
        judge_data: Mapping[str, Any],
        local_data: Mapping[str, Any],
    ) -> "AppConfig":
        profile = ProfileConfig.from_dict(profile_data)

        def section(name: str) -> Mapping[str, Any]:
            value = judge_data.get(name)
            if value is None:
                raise ConfigError(f"judge に必須キーがありません: {name}")
            if not isinstance(value, Mapping):
                raise ConfigError(f"設定キーの型が不正です: {name}")
            return value

        def optional_section(name: str) -> Mapping[str, Any]:
            value = judge_data.get(name)
            if value is None:
                return {}
            if not isinstance(value, Mapping):
                raise ConfigError(f"設定キーの型が不正です: {name}")
            return value

        fitness = FitnessConfig.from_dict(section("fitness"))
        # 適合度付けに必要なプロファイル由来の値を補う（追補 A2.2）。
        fill: dict[str, Any] = {}
        if fitness.salary_target_jpy is None:
            fill["salary_target_jpy"] = profile.salary_target_jpy
        if not fitness.target_domains:
            fill["target_domains"] = profile.target_domains
        if fill:
            from dataclasses import replace

            fitness = replace(fitness, **fill)
        return cls(
            profile=profile,
            normalize=NormalizeConfig.from_dict(optional_section("normalize")),
            duty=DutyConfig.from_dict(section("duty")),
            location=LocationConfig.from_dict(section("location")),
            remote=RemoteConfig.from_dict(section("remote")),
            english=EnglishConfig.from_dict(section("english")),
            hard=HardConfig.from_dict(optional_section("hard")),
            fitness=fitness,
            presentation=PresentationConfig.from_dict(optional_section("presentation")),
            validity=ValidityConfig.from_dict(section("validity")),
            collection=CollectionConfig.from_dict(section("collection")),
            guard=GuardConfig.from_dict(section("guard")),
            openwork=OpenworkConfig.from_dict(optional_section("openwork")),
            auto_apply=AutoApplyConfig.from_dict(optional_section("auto_apply")),
            local=LocalConfig.from_dict(local_data),
        )


def _load_yaml(path: Path) -> Mapping[str, Any]:
    # 読込の低レベル処理は parsing.load_yaml_raw に共通化し、エラー方針
    # （全て ConfigError に寄せるフェイルクローズ）は本ローダ側で維持する。
    try:
        data = load_yaml_raw(path)
    except ImportError as exc:  # pragma: no cover
        raise ConfigError("YAML 読込ライブラリが利用できません") from exc
    except OSError as exc:
        raise ConfigError(f"設定ファイルが読み取れません: {path.name}") from exc
    except Exception as exc:
        raise ConfigError(f"設定ファイルの形式が不正です: {path.name}") from exc
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise ConfigError(f"設定ファイルの形式が不正です: {path.name}")
    return data


def load_config(profile_path: Path, judge_path: Path, local_path: Path) -> AppConfig:
    """3 ファイルを読み、検証して AppConfig を組み立てる（§3.1）。

    実値ファイルが無い場合も ConfigError とし、テンプレートを実値として
    代用しない。
    """
    for path in (profile_path, judge_path, local_path):
        if not Path(path).is_file():
            raise ConfigError(f"設定ファイルが存在しません: {Path(path).name}")
    profile_data = _load_yaml(Path(profile_path))
    judge_data = _load_yaml(Path(judge_path))
    local_data = _load_yaml(Path(local_path))
    return AppConfig.from_dicts(profile_data, judge_data, local_data)


def validate_config(cfg: AppConfig) -> list[str]:
    """検証のみを行う（例外を投げない）。正常時は空リスト（§3.1）。"""
    problems: list[str] = []
    if cfg.presentation.weekly_limit <= 0:
        problems.append("presentation.weekly_limit は 1 以上が必要です")
    if cfg.profile.salary_target_jpy <= 0:
        problems.append("profile.salary_target_jpy は正の値が必要です")
    if not cfg.profile.employment_allowed:
        problems.append("profile.employment_allowed が空です")
    if cfg.validity.listing_valid_days <= 0:
        problems.append("validity.listing_valid_days は 1 以上が必要です")
    if cfg.collection.per_source_max_items <= 0:
        problems.append("collection.per_source_max_items は 1 以上が必要です")
    if not 0.0 <= cfg.guard.max_close_ratio <= 1.0:
        problems.append("guard.max_close_ratio は 0〜1 の範囲が必要です")
    if cfg.guard.max_total_change_ratio < 0.0:
        problems.append("guard.max_total_change_ratio は 0 以上が必要です")
    if cfg.duty.mixed_handling != "check":
        problems.append("duty.mixed_handling は check のみ対応します")
    return problems
