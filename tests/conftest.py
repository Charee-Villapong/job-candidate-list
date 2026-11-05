"""受け入れテスト共通フィクスチャ。

設計方針:
- 実装（src/job_candidate_list/）はまだ存在しない前提。公開インターフェース
  （docs/architecture.md §3 と docs/architecture-addendum.md の確定仕様）への
  アクセスはすべて `impl()` 経由とし、未実装の場合は ImportError ではなく
  「どの公開インターフェースが未実装か」が分かるメッセージでテストを
  失敗させる（pytest の収集は通す）。失敗理由は全テストで
  「未実装: <対象> …」の形に統一する。
- ネットワーク・実 Sheets には依存しない。Sheets / OpenWork は
  Protocol 境界に対するフェイク（本ファイルの Fake* クラス）を使う。
- 設定フィクスチャは architecture.md §4 のキー構造を模した辞書で保持する。
  特に勤務地対応表はテスト専用の小さなもの（架空の市区町村）を使い、
  実在の本人の対応表とは切り離す。
- オブジェクト生成は追補の確定インターフェース（AppConfig.from_dicts、
  CandidateRecord は candidate_id のみ必須、LocationWhitelist.from_dict 等）を
  使う共通ヘルパー（build_*）に寄せ、呼び出し形の調整箇所を 1 か所にする。
"""

from __future__ import annotations

import datetime as dt
import importlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "storage-spec" / "samples"
PLACEMENT_TABLE = REPO_ROOT / "storage-spec" / "配置表.md"
ARCHITECTURE_MD = REPO_ROOT / "docs" / "architecture.md"


# ---------------------------------------------------------------------------
# 実装へのアクセス（未実装なら理由付きで失敗させる）
# ---------------------------------------------------------------------------
def impl(dotted: str):
    """`job_candidate_list.<module>[.<attr>...]` を取得する。

    存在しない場合は、対象が未実装であることを明示して失敗させる。
    モジュール自体を指すパス（例: `job_candidate_list.errors`）も扱える。
    """
    parts = dotted.split(".")
    last_error: Exception | None = None
    for cut in range(len(parts), 1, -1):
        module_name = ".".join(parts[:cut])
        try:
            obj = importlib.import_module(module_name)
        except ImportError as exc:  # pragma: no cover - 未実装期間の経路
            last_error = exc
            continue
        for attr in parts[cut:]:
            if not hasattr(obj, attr):
                pytest.fail(
                    f"未実装: {dotted} が存在しない"
                    f"（{module_name} に {attr} が無い）。"
                    "architecture.md §3／追補の公開インターフェース"
                    "仕様どおりでない。"
                )
            obj = getattr(obj, attr)
        return obj
    pytest.fail(
        f"未実装: {dotted} を import できない（{last_error}）。"
        "architecture.md §3／追補の公開インターフェースが"
        "まだ実装されていない。"
    )


def enum_value(value):
    """StrEnum でも素の文字列でも、値そのものを文字列として取り出す。"""
    return str(getattr(value, "value", value))


def impl_module(module: str):
    try:
        return importlib.import_module(f"job_candidate_list.{module}")
    except ImportError as exc:
        pytest.fail(
            f"未実装: job_candidate_list.{module} が存在しない（{exc}）。"
            "architecture.md §1.2 のモジュール構成どおりでない。"
        )


# ---------------------------------------------------------------------------
# サンプル読み込み
# ---------------------------------------------------------------------------
def load_sample(sample_id: str) -> str:
    matches = sorted(SAMPLES_DIR.glob(f"{sample_id}_*.md"))
    assert len(matches) == 1, f"{sample_id} のサンプルファイルが一意でない: {matches}"
    return matches[0].read_text(encoding="utf-8")


def sample_field(text: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}: (.+)$", text, flags=re.MULTILINE)
    assert m, f"サンプルに項目「{key}」が無い"
    return m.group(1).strip()


def sample_section(text: str, heading: str) -> str:
    m = re.search(
        rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert m, f"サンプルにセクション「{heading}」が無い"
    return m.group(1).strip()


@pytest.fixture(scope="session")
def samples_dir() -> Path:
    return SAMPLES_DIR


# ---------------------------------------------------------------------------
# 設定フィクスチャ（architecture.md §4 のキー構造を模す。値は全て架空・一般化）
# ---------------------------------------------------------------------------
@pytest.fixture()
def judge_config_dict() -> dict:
    return {
        "normalize": {"tracking_params": ["utm_source", "utm_medium", "ref", "fbclid"]},
        "duty": {
            "target_sections": ["業務内容", "担当業務"],
            "upstream_keywords": [
                "企画立案",
                "推進",
                "方向付け",
                "関係者調整",
                "意思決定",
                "システム全体の設計",
                "アーキテクチャ設計",
                "設計方針の策定",
                "技術選定",
                "標準化",
            ],
            "impl_keywords": [
                "要件定義後の設計",
                "詳細設計",
                "実装",
                "コーディング",
                "開発",
                "テスト",
                "運用保守",
                "障害対応",
            ],
            "title_assist_keywords": ["マネージャー", "エンジニア", "アーキテクト"],
            "primary_markers": ["主担当", "主務", "メイン業務"],
            "mixed_handling": "check",
        },
        "location": {
            "target_sections": ["勤務地", "勤務先", "就業場所", "アクセス"],
        },
        "remote": {
            "rules": [
                {"pattern": "フルリモート|出社なし", "division": "フルリモート（出社なし）"},
                {"pattern": "週3日リモート|週2日出社", "division": "出社週2〜3日"},
                {"pattern": "週2〜3日", "division": "出社週2〜3日"},
                {"pattern": "月1回出社|週1日以下", "division": "出社週1日以下"},
                {"pattern": "週4日", "division": "出社週4日以上"},
                {"pattern": "フル出社|出社必須", "division": "出社必須（フル出社）"},
            ]
        },
        "english": {
            "target_sections": ["応募資格", "必須スキル", "求める人物像", "業務内容"],
            "mandatory_sections": ["必須スキル", "必須要件"],
            "required_phrases": ["英語必須", "英語が必須", "必須です"],
            "welcome_phrases": ["歓迎", "尚可", "活かせる", "読み書き程度"],
            "level_phrases": ["ビジネスレベル", "日常会話レベル", "ネイティブレベル"],
        },
        "hard": {
            "non_regular_keywords": ["契約社員", "派遣", "業務委託", "アルバイト"],
            "out_of_domain_keywords": ["飲食店", "配送ドライバー", "介護"],
            "business_form_keywords": [
                "システムインテグレーション事業",
                "常駐",
                "コンサルティングを提供",
                "SI事業",
            ],
        },
        "fitness": {
            "salary_check_threshold_jpy": 4_000_000,
            "high_threshold": 70,
            "domain_match_points": 30,
            "salary_points": 20,
            "condition_points": 10,
            "location_points": 10,
        },
        "presentation": {"weekly_limit": 20},
        "validity": {"listing_valid_days": 14},
        "collection": {
            "request_interval_sec": 5,
            "per_source_max_items": 50,
            "timeout_sec": 30,
        },
        "guard": {"max_close_ratio": 0.5, "max_total_change_ratio": 1.0},
        "openwork": {"weekly_once_per_company": True},
        "auto_apply": {
            "enabled": False,
            "weekly_limit": 3,
            "result_unknown_hold_runs": 2,
        },
    }


@pytest.fixture()
def profile_config_dict() -> dict:
    return {
        "profile_version": 1,
        "target_domains": ["データ", "AI"],
        "target_role_labels": ["PM", "マネジメント", "企画・推進", "アーキテクト"],
        "salary_target_jpy": 8_000_000,
        "employment_allowed": ["正社員"],
        "remote_preference": None,
        "location_condition_enabled": True,
        "location_hard_exclude": False,
        "search_queries": ["データ PM", "AI 企画 推進"],
    }


@pytest.fixture()
def local_config_dict() -> dict:
    """接続・実値系設定のフィクスチャ（値は全て架空・一般化）。"""
    return {
        "spreadsheet_id": "sample-spreadsheet",
        "sheet_names": {
            "candidates": "候補リスト",
            "registry": "情報源レジストリ",
            "exclusions": "除外ログ",
        },
    }


@pytest.fixture()
def location_whitelist_dict() -> dict:
    """テスト専用の小さな勤務地対応表（架空。本人の実対応表とは無関係）。"""
    return {
        "version": 1,
        "municipalities": [
            {"prefecture": "サンプル県", "municipality": "サンプル甲市"},
            {"prefecture": "サンプル県", "municipality": "サンプル区"},
        ],
        "notes": "テスト用フィクスチャ。サンプル甲市・サンプル区が対応表内、"
        "サンプル乙市・サンプル遠方市は対応表外とする。",
    }


# ---------------------------------------------------------------------------
# Protocol 境界のフェイク
# ---------------------------------------------------------------------------
class FakeSheetsGateway:
    """SheetsGateway Protocol（§3.11）に対するインメモリフェイク。

    書込は自動列のみに限定されることを検証できるよう、upsert された
    レコードと、タブごとの行データをそのまま保持する。
    """

    def __init__(self, candidates=None, registry_rows=None):
        self._candidates = list(candidates or [])
        self._registry_rows = registry_rows or []
        self.upserted: list = []
        self.exclusions: list = []

    def read_tab(self, tab_name: str):
        return []

    def read_candidates(self):
        return list(self._candidates)

    def upsert_candidates(self, records):
        self.upserted.extend(records)

    def append_exclusions(self, rows):
        self.exclusions.extend(rows)

    def read_registry_rows(self):
        return self._registry_rows


class FakeOpenworkProvider:
    """OpenworkProvider Protocol（§3.14）に対するフェイク。呼出回数を数える。"""

    def __init__(self, results=None):
        self._results = results or {}
        self.calls: list[str] = []

    def search_company(self, company_norm: str):
        self.calls.append(company_norm)
        return self._results.get(company_norm, [])


class FakeSourceAdapter:
    """SourceAdapter Protocol（追補付録）に対するフェイク。

    `fetch(source)` には SourceEntry が渡る想定だが、引数は記録するだけで
    解釈せず、固定の RawPosting 一覧を返す。
    """

    def __init__(self, postings=None):
        self._postings = list(postings or [])
        self.calls: list = []

    def fetch(self, source=None):
        self.calls.append(source)
        return list(self._postings)


# ---------------------------------------------------------------------------
# レジストリ行フィクスチャ（§8.1 の列構成。値は全て架空）
# ---------------------------------------------------------------------------
REGISTRY_HEADER = [
    "情報源名", "URLパターン", "ログイン要否", "資格情報の有無", "実測結果",
    "自動取得可否", "リスク評価結果と判定根拠", "費用条件確認", "自動応募可否",
    "送信確定方法", "対象化の最終判断", "備考",
]

_REGISTRY_MEASURED_OK = (
    "結果=対象外（ログイン不要）; 取得方法=公式公開API; 確認日=2026-11-05"
)
_REGISTRY_RISK_LOW = (
    "リスク=低; 根拠=公式ドキュメントで公開求人の取得が明記されている; "
    "確認日=2026-11-05"
)


def registry_rows(source_id: str = "サンプル情報源A") -> list[list[str]]:
    """収集可能な情報源 1 件分（ヘッダ＋データ行）を返す。"""
    return [
        list(REGISTRY_HEADER),
        [
            source_id,
            "https://example.invalid/board-a/*",
            "不要",
            "不要（ログイン不要のため）",
            _REGISTRY_MEASURED_OK,
            "可",
            _REGISTRY_RISK_LOW,
            "確認済み（無料または既存契約範囲内）",
            "不可",
            "対象外（自動応募は第3段階で確定）",
            "対象",
            "",
        ],
    ]


# ---------------------------------------------------------------------------
# 確定インターフェースによるオブジェクト生成ヘルパー（追補 A1〜A3）
# ---------------------------------------------------------------------------
def build_app_config(profile_data: dict, judge_data: dict, local_data=None):
    """AppConfig.from_dicts（追補 A2.2）で設定オブジェクトを組み立てる。"""
    app_config = impl("job_candidate_list.config.AppConfig")
    if local_data is None:
        local_data = {
            "spreadsheet_id": "sample-spreadsheet",
            "sheet_names": {
                "candidates": "候補リスト",
                "registry": "情報源レジストリ",
                "exclusions": "除外ログ",
            },
        }
    return app_config.from_dicts(profile_data, judge_data, local_data)


def build_candidate_record(candidate_id: str, **over):
    """CandidateRecord を生成する（追補 A3: 必須引数は candidate_id のみ）。"""
    models = impl("job_candidate_list.models")
    return models.CandidateRecord(candidate_id=candidate_id, **over)


def build_raw_posting(**over):
    """RawPosting を生成する。デフォルトはハード除外に触れない上流職の求人。"""
    models = impl("job_candidate_list.models")
    source_type = models.SourceType("公開求人")
    defaults = {
        "source_id": "サンプル情報源A",
        "source_type": source_type,
        "raw_url": "https://example.invalid/j/1",
        "company_raw": "株式会社サンプル青葉",
        "position_raw": "プロダクトマネージャー",
        "employment_raw": "正社員",
        "salary_min_jpy": 6_000_000,
        "salary_max_jpy": 8_000_000,
        "salary_raw": "年収 600万円〜800万円",
        "location_raw": "サンプル甲市",
        "remote_raw": "出社週2〜3日",
        "sections": {
            "業務内容": (
                "データ活用推進の企画立案を主担当として担います。"
                "関係者調整を行いながらプロジェクトの方向付けを行い、"
                "施策の推進と意思決定の支援を行います。"
            ),
            "応募資格": "データ領域での業務経験。関係者との調整経験。",
            "勤務地": "勤務地はサンプル甲市です。",
            "会社概要": (
                "データ分析基盤とAI活用支援のプロダクトを提供する"
                "事業を行っています。"
            ),
        },
        "company_overview_raw": (
            "データ分析基盤とAI活用支援のプロダクトを提供する事業を行っています。"
        ),
        "fetched_on": dt.date(2026, 11, 5),
    }
    defaults.update(over)
    return models.RawPosting(**defaults)


def build_location_whitelist(data: dict):
    """LocationWhitelist.from_dict（追補 A2.3）で対応表オブジェクトを作る。"""
    models = impl("job_candidate_list.models")
    return models.LocationWhitelist.from_dict(data)


def run_fake_collection(profile_data, judge_data, local_data=None, *,
                        existing=(), postings=(), dry_run=False):
    """pipeline.run_collection をフェイク境界で 1 回実行する。

    戻り値は (RunSummary, FakeSheetsGateway)。情報源は
    「サンプル情報源A」1 件（レジストリ行・取得結果ともフィクスチャ）。
    """
    cfg = build_app_config(profile_data, judge_data, local_data)
    gateway = FakeSheetsGateway(
        candidates=list(existing), registry_rows=registry_rows()
    )
    adapter = FakeSourceAdapter(postings)
    run = impl("job_candidate_list.pipeline.run_collection")
    summary = run(
        cfg, gateway, {"サンプル情報源A": adapter}, None, dry_run
    )
    return summary, gateway
