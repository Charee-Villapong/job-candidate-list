"""設定ファイル（YAML）の読込と検証（architecture.md §3.1／§4、追補 A2）。

判定に効く値は全て設定側に置き、本モジュールが唯一の入口になる。
各サブ設定は ``from_dict`` を持ち、未知キー・必須キー欠落・型不一致は
``ConfigError``（キー名をメッセージに含める）とする。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigError
from .parsing import load_yaml_raw

_REMOTE_DIVISIONS = (
    "フルリモート（出社なし）",
    "出社週1日以下",
    "出社週2〜3日",
    "出社週4日以上",
    "出社必須（フル出社）",
    "不明",
)


def _check_unknown(data: Mapping[str, Any], allowed: set[str], owner: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"{owner} に未知のキーがあります: {sorted(unknown)}")


def _req(data: Mapping[str, Any], key: str, owner: str) -> Any:
    if key not in data:
        raise ConfigError(f"{owner} に必須キーがありません: {key}")
    return data[key]


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


@dataclass(frozen=True)
class ProfileConfig:
    profile_version: int
    target_domains: tuple[str, ...]
    target_role_labels: tuple[str, ...]
    salary_target_jpy: int
    employment_allowed: tuple[str, ...]
    remote_preference: str | None = None
    location_condition_enabled: bool = True
    location_hard_exclude: bool = False
    search_queries: tuple[str, ...] = ()
    last_updated: dt.date | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProfileConfig":
        owner = "profile"
        _check_unknown(
            data,
            {
                "profile_version",
                "target_domains",
                "target_role_labels",
                "salary_target_jpy",
                "employment_allowed",
                "remote_preference",
                "location_condition_enabled",
                "location_hard_exclude",
                "search_queries",
                "last_updated",
            },
            owner,
        )
        remote_preference = data.get("remote_preference")
        if remote_preference is not None:
            remote_preference = _as_str(remote_preference, "remote_preference")
        return cls(
            profile_version=_as_int(
                _req(data, "profile_version", owner), "profile_version"
            ),
            target_domains=_as_str_tuple(
                _req(data, "target_domains", owner), "target_domains"
            ),
            target_role_labels=_as_str_tuple(
                _req(data, "target_role_labels", owner), "target_role_labels"
            ),
            salary_target_jpy=_as_int(
                _req(data, "salary_target_jpy", owner), "salary_target_jpy"
            ),
            employment_allowed=_as_str_tuple(
                _req(data, "employment_allowed", owner), "employment_allowed"
            ),
            remote_preference=remote_preference,
            location_condition_enabled=_as_bool(
                data.get("location_condition_enabled", True),
                "location_condition_enabled",
            ),
            location_hard_exclude=_as_bool(
                data.get("location_hard_exclude", False), "location_hard_exclude"
            ),
            search_queries=_as_str_tuple(
                data.get("search_queries", ()), "search_queries"
            ),
            last_updated=_as_date(data.get("last_updated"), "last_updated"),
        )


@dataclass(frozen=True)
class NormalizeConfig:
    tracking_params: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NormalizeConfig":
        _check_unknown(data, {"tracking_params"}, "normalize")
        return cls(
            tracking_params=_as_str_tuple(
                data.get("tracking_params", ()), "tracking_params"
            )
        )


@dataclass(frozen=True)
class DutyConfig:
    target_sections: tuple[str, ...]
    upstream_keywords: tuple[str, ...]
    impl_keywords: tuple[str, ...]
    title_assist_keywords: tuple[str, ...] = ()
    primary_markers: tuple[str, ...] = ()
    mixed_handling: str = "check"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DutyConfig":
        owner = "duty"
        _check_unknown(
            data,
            {
                "target_sections",
                "upstream_keywords",
                "impl_keywords",
                "title_assist_keywords",
                "primary_markers",
                "mixed_handling",
            },
            owner,
        )
        mixed = _as_str(data.get("mixed_handling", "check"), "mixed_handling")
        if mixed != "check":
            raise ConfigError("設定キーの値が範囲外です: mixed_handling")
        return cls(
            target_sections=_as_str_tuple(
                _req(data, "target_sections", owner), "target_sections"
            ),
            upstream_keywords=_as_str_tuple(
                _req(data, "upstream_keywords", owner), "upstream_keywords"
            ),
            impl_keywords=_as_str_tuple(
                _req(data, "impl_keywords", owner), "impl_keywords"
            ),
            title_assist_keywords=_as_str_tuple(
                data.get("title_assist_keywords", ()), "title_assist_keywords"
            ),
            primary_markers=_as_str_tuple(
                data.get("primary_markers", ()), "primary_markers"
            ),
            mixed_handling=mixed,
        )


@dataclass(frozen=True)
class LocationConfig:
    target_sections: tuple[str, ...]
    whitelist_file: Path | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LocationConfig":
        owner = "location"
        _check_unknown(data, {"target_sections", "whitelist_file"}, owner)
        return cls(
            target_sections=_as_str_tuple(
                _req(data, "target_sections", owner), "target_sections"
            ),
            whitelist_file=_as_path(data.get("whitelist_file"), "whitelist_file"),
        )


@dataclass(frozen=True)
class RemoteRule:
    pattern: str
    division: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RemoteRule":
        owner = "remote.rules"
        _check_unknown(data, {"pattern", "division"}, owner)
        division = _as_str(_req(data, "division", owner), "division")
        if division not in _REMOTE_DIVISIONS:
            raise ConfigError("設定キーの値が範囲外です: division")
        return cls(
            pattern=_as_str(_req(data, "pattern", owner), "pattern"),
            division=division,
        )


@dataclass(frozen=True)
class RemoteConfig:
    rules: tuple[RemoteRule, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RemoteConfig":
        owner = "remote"
        _check_unknown(data, {"rules"}, owner)
        raw_rules = _req(data, "rules", owner)
        if not isinstance(raw_rules, (list, tuple)):
            raise ConfigError("設定キーの型が不正です: rules")
        rules = []
        for item in raw_rules:
            if isinstance(item, RemoteRule):
                if item.division not in _REMOTE_DIVISIONS:
                    raise ConfigError("設定キーの値が範囲外です: division")
                rules.append(item)
            elif isinstance(item, Mapping):
                rules.append(RemoteRule.from_dict(item))
            else:
                raise ConfigError("設定キーの型が不正です: rules")
        return cls(rules=tuple(rules))


@dataclass(frozen=True)
class EnglishConfig:
    target_sections: tuple[str, ...]
    mandatory_sections: tuple[str, ...] = ()
    required_phrases: tuple[str, ...] = ()
    welcome_phrases: tuple[str, ...] = ()
    level_phrases: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EnglishConfig":
        owner = "english"
        _check_unknown(
            data,
            {
                "target_sections",
                "mandatory_sections",
                "required_phrases",
                "welcome_phrases",
                "level_phrases",
            },
            owner,
        )
        return cls(
            target_sections=_as_str_tuple(
                _req(data, "target_sections", owner), "target_sections"
            ),
            mandatory_sections=_as_str_tuple(
                data.get("mandatory_sections", ()), "mandatory_sections"
            ),
            required_phrases=_as_str_tuple(
                data.get("required_phrases", ()), "required_phrases"
            ),
            welcome_phrases=_as_str_tuple(
                data.get("welcome_phrases", ()), "welcome_phrases"
            ),
            level_phrases=_as_str_tuple(data.get("level_phrases", ()), "level_phrases"),
        )


@dataclass(frozen=True)
class HardConfig:
    non_regular_keywords: tuple[str, ...] = ()
    out_of_domain_keywords: tuple[str, ...] = ()
    business_form_keywords: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HardConfig":
        owner = "hard"
        _check_unknown(
            data,
            {
                "non_regular_keywords",
                "out_of_domain_keywords",
                "business_form_keywords",
            },
            owner,
        )
        return cls(
            non_regular_keywords=_as_str_tuple(
                data.get("non_regular_keywords", ()), "non_regular_keywords"
            ),
            out_of_domain_keywords=_as_str_tuple(
                data.get("out_of_domain_keywords", ()), "out_of_domain_keywords"
            ),
            business_form_keywords=_as_str_tuple(
                data.get("business_form_keywords", ()), "business_form_keywords"
            ),
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

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FitnessConfig":
        owner = "fitness"
        _check_unknown(
            data,
            {
                "salary_check_threshold_jpy",
                "high_threshold",
                "domain_match_points",
                "salary_points",
                "condition_points",
                "location_points",
                "salary_target_jpy",
                "target_domains",
            },
            owner,
        )
        salary_target = data.get("salary_target_jpy")
        if salary_target is not None:
            salary_target = _as_int(salary_target, "salary_target_jpy")
        return cls(
            salary_check_threshold_jpy=_as_int(
                _req(data, "salary_check_threshold_jpy", owner),
                "salary_check_threshold_jpy",
            ),
            high_threshold=_as_int(
                _req(data, "high_threshold", owner), "high_threshold"
            ),
            domain_match_points=_as_int(
                data.get("domain_match_points", 0), "domain_match_points"
            ),
            salary_points=_as_int(data.get("salary_points", 0), "salary_points"),
            condition_points=_as_int(
                data.get("condition_points", 0), "condition_points"
            ),
            location_points=_as_int(data.get("location_points", 0), "location_points"),
            salary_target_jpy=salary_target,
            target_domains=_as_str_tuple(
                data.get("target_domains", ()), "target_domains"
            ),
        )


@dataclass(frozen=True)
class PresentationConfig:
    weekly_limit: int = 20

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PresentationConfig":
        _check_unknown(data, {"weekly_limit"}, "presentation")
        return cls(weekly_limit=_as_int(data.get("weekly_limit", 20), "weekly_limit"))


@dataclass(frozen=True)
class ValidityConfig:
    listing_valid_days: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ValidityConfig":
        _check_unknown(data, {"listing_valid_days"}, "validity")
        return cls(
            listing_valid_days=_as_int(
                _req(data, "listing_valid_days", "validity"), "listing_valid_days"
            )
        )


@dataclass(frozen=True)
class CollectionConfig:
    request_interval_sec: int
    per_source_max_items: int
    timeout_sec: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CollectionConfig":
        owner = "collection"
        _check_unknown(
            data,
            {"request_interval_sec", "per_source_max_items", "timeout_sec"},
            owner,
        )
        return cls(
            request_interval_sec=_as_int(
                _req(data, "request_interval_sec", owner), "request_interval_sec"
            ),
            per_source_max_items=_as_int(
                _req(data, "per_source_max_items", owner), "per_source_max_items"
            ),
            timeout_sec=_as_int(_req(data, "timeout_sec", owner), "timeout_sec"),
        )


@dataclass(frozen=True)
class GuardConfig:
    max_close_ratio: float
    max_total_change_ratio: float

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GuardConfig":
        owner = "guard"
        _check_unknown(data, {"max_close_ratio", "max_total_change_ratio"}, owner)
        return cls(
            max_close_ratio=_as_float(
                _req(data, "max_close_ratio", owner), "max_close_ratio"
            ),
            max_total_change_ratio=_as_float(
                _req(data, "max_total_change_ratio", owner),
                "max_total_change_ratio",
            ),
        )


@dataclass(frozen=True)
class OpenworkConfig:
    weekly_once_per_company: bool = True

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OpenworkConfig":
        _check_unknown(data, {"weekly_once_per_company"}, "openwork")
        return cls(
            weekly_once_per_company=_as_bool(
                data.get("weekly_once_per_company", True),
                "weekly_once_per_company",
            )
        )


@dataclass(frozen=True)
class AutoApplyConfig:
    enabled: bool = False
    weekly_limit: int = 3
    result_unknown_hold_runs: int = 2

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AutoApplyConfig":
        owner = "auto_apply"
        _check_unknown(
            data, {"enabled", "weekly_limit", "result_unknown_hold_runs"}, owner
        )
        return cls(
            enabled=_as_bool(data.get("enabled", False), "enabled"),
            weekly_limit=_as_int(data.get("weekly_limit", 3), "weekly_limit"),
            result_unknown_hold_runs=_as_int(
                data.get("result_unknown_hold_runs", 2),
                "result_unknown_hold_runs",
            ),
        )


@dataclass(frozen=True)
class SheetNamesConfig:
    candidates: str = "候補リスト"
    registry: str = "情報源レジストリ"
    exclusions: str = "除外ログ"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SheetNamesConfig":
        _check_unknown(data, {"candidates", "registry", "exclusions"}, "sheet_names")
        return cls(
            candidates=_as_str(data.get("candidates", "候補リスト"), "candidates"),
            registry=_as_str(data.get("registry", "情報源レジストリ"), "registry"),
            exclusions=_as_str(data.get("exclusions", "除外ログ"), "exclusions"),
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
        owner = "local"
        _check_unknown(
            data,
            {
                "spreadsheet_id",
                "sheet_names",
                "banned_words_file",
                "ats_boards",
                "profile_file",
                "judge_file",
            },
            owner,
        )
        sheet_names_raw = data.get("sheet_names")
        if sheet_names_raw is None:
            sheet_names = SheetNamesConfig()
        elif isinstance(sheet_names_raw, SheetNamesConfig):
            sheet_names = sheet_names_raw
        elif isinstance(sheet_names_raw, Mapping):
            sheet_names = SheetNamesConfig.from_dict(sheet_names_raw)
        else:
            raise ConfigError("設定キーの型が不正です: sheet_names")
        boards_raw = data.get("ats_boards") or {}
        if not isinstance(boards_raw, Mapping):
            raise ConfigError("設定キーの型が不正です: ats_boards")
        boards = {str(k): _as_str_tuple(v, "ats_boards") for k, v in boards_raw.items()}
        return cls(
            spreadsheet_id=_as_str(data.get("spreadsheet_id", ""), "spreadsheet_id"),
            sheet_names=sheet_names,
            banned_words_file=_as_path(
                data.get("banned_words_file"), "banned_words_file"
            ),
            ats_boards=boards,
            profile_file=_as_path(data.get("profile_file"), "profile_file"),
            judge_file=_as_path(data.get("judge_file"), "judge_file"),
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
