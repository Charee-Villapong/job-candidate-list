# 追補設計（architecture-addendum）― 応募候補リスト作成・第1段階

- 位置づけ: 本書は `docs/architecture.md`（以下「本体」）に対する追補であり、テスト係から
  上がった仕様確認事項 11 件について、公開インターフェースの具体化を確定するものである。
- **優先関係: 本書と本体が矛盾する場合は、本書（追補）が優先する（追補を正とする）。**
  本体は書き換えない。企画書（組織リポジトリ Issue #1）と本書が矛盾する場合は企画書が優先し、
  本書を改訂する（本体冒頭の位置づけと同じ）。
- 本書の範囲: インターフェースの具体化のみ。企画書の完了の定義・判定仕様そのものは変更しない。
  判定仕様の変更に当たると判断した点は本書では確定せず、別途「本人／PM確認事項」として分離する。
- 記法: 型は Python の型注釈で書く。列挙型は全て `enum.StrEnum` とし、値は本体で確定した
  文字列そのものを使う（`str(値) == "値の文字列"` が成り立つことをテスト前提とする）。
  データ構造は原則 `dataclass(frozen=True)` とし、可変のリストではなく `tuple` で保持する
  （本体 §2.2 が `list` と書いた箇所は、本書の `tuple` 表記に読み替える）。
- 日付: 2026-11-05。

---

## A1. サンプル検証用の判定入口（本体 §3 への追加 §3.16、§11.2、§12 手順7）

本体 §3 に欠けていた「求人 1 件を単体で判定する公開入口」を、新規モジュール
`src/job_candidate_list/evaluate.py` に確定する。Sheets・ネットワークには一切触れない
純粋関数のみで構成し、検証用サンプル（storage-spec/samples/）の単体検証と、
パイプライン §12 手順7 の判定段の共通実装の両方から使う。

```python
# evaluate.py
SAMPLE_SOURCE_ID: str = "検証用サンプル"   # サンプル由来 RawPosting.source_id の固定値

def parse_sample_text(text: str, *, fetched_on: date | None = None) -> RawPosting: ...
def evaluate_posting(posting: RawPosting, cfg: AppConfig,
                     whitelist: LocationWhitelist) -> EvaluationResult: ...
def evaluate_sample_text(text: str, cfg: AppConfig, whitelist: LocationWhitelist,
                         *, fetched_on: date | None = None) -> EvaluationResult: ...
    # evaluate_sample_text(t, cfg, wl) == evaluate_posting(parse_sample_text(t), cfg, wl)
```

### A1.1 `parse_sample_text` の変換規則（サンプル形式 → `RawPosting`）

サンプル形式は storage-spec/samples/ の実形式（ヘッダ項目＋`## 見出し`セクション）で固定する。

| サンプルの項目 | `RawPosting` フィールド | 変換規則 |
|---|---|---|
| 会社名 | `company_raw` | 原文のまま |
| ポジション名 | `position_raw` | 原文のまま |
| 雇用形態 | `employment_raw` | 原文のまま |
| 情報源種別 | `source_type` | 値文字列を `SourceType`（本体 §2.1 の閉じた集合）として解釈する |
| 情報源URL | `raw_url` | 原文のまま（表示テキストではない） |
| 想定年収 | `salary_raw` / `salary_min_jpy` / `salary_max_jpy` | 原文を `salary_raw` に保持。`(\d+(?:\.\d+)?)万円` の出現が 2 件なら小さい方を min・大きい方を max（円換算）、1 件なら min == max == その値、0 件なら両方 `None` |
| 勤務地（ヘッダ） | `location_raw` | 原文のまま（勤務地判定の根拠には使わない。判定根拠はセクション側のみ＝本体 §3.6） |
| リモート頻度（原文） | `remote_raw` | 原文のまま。ただし値が `記載なし` で始まる場合は「記載なし」の宣言として `None` に変換する |
| `## 見出し` セクション群 | `sections` | 見出し文字列 → 本文（前後の空白を除去）の dict。全セクションを保持する |
| `## 会社概要` | `company_overview_raw` | `sections["会社概要"]` と同値。無い場合は `None` |
| （固定） | `source_id` | `SAMPLE_SOURCE_ID`（`"検証用サンプル"`） |
| 引数 | `fetched_on` | 引数で受け取る。`None` の場合は `date.today()` |

- 必須ヘッダ項目（会社名／ポジション名／雇用形態／情報源URL／勤務地）が欠けた場合は
  `ValueError`（欠けた項目名をメッセージに含める）を送出する。推測で補わない。
- 分類コード等の検証管理用ヘッダは `RawPosting` に入れない（判定入力にしない）。

### A1.2 `EvaluationResult`（戻り値の構造）

```python
# evaluate.py
class EvaluationKind(StrEnum):
    HARD = "hard"    # ハード除外（除外ログ行き）
    CHECK = "check"  # ハード除外されず適合度「要確認」
    OK = "ok"        # ハード除外されず適合度「高」または「中」

@dataclass(frozen=True)
class EvaluationResult:
    posting: RawPosting
    hard_excluded: bool
    exclusions: tuple[ExclusionHit, ...]        # judge_hard.py の型（本体 §3.8）
    duty: DutyResult
    english: EnglishResult
    remote: RemoteResult
    location: LocationResult
    fitness_result: FitnessResult | None        # ハード除外時は None
    rule_ids: tuple[str, ...]                   # 全判定の発火ルールID（重複なし・発火順）

    @property
    def kind(self) -> EvaluationKind: ...
        # hard_excluded → HARD / fitness_result.fitness == 要確認 → CHECK / それ以外 → OK
```

### A1.3 `evaluate_posting` の評価順序とハード除外の集約

評価順序は本体 §12 手順7 と同一に固定する。

1. `normalize_remote_frequency(posting.remote_raw, cfg.remote)` → `RemoteResult`
2. `judge_location(posting, remote, whitelist, cfg.location)` → `LocationResult`
3. `judge_main_duty(posting.sections, cfg.duty)` → `DutyResult`
4. `judge_english(posting.sections, cfg.english)` → `EnglishResult`
5. `judge_hard_exclusions(posting, cfg.hard)` → `list[ExclusionHit]`
6. ハード除外の集約: 次のいずれか 1 つでもあれば `hard_excluded=True` とし、
   `fitness_result=None` で打ち切る（適合度付けは行わない）。
   - 手順5 のヒットが 1 件以上
   - `duty.outcome == impl` → `ExclusionHit("JOB-IMPL-HARD", duty の理由, "duty.impl_keywords")` を追加
   - `english.outcome == required` → `ExclusionHit("ENG-HARD", english の理由, "english.required_phrases")` を追加
7. ハード除外でない場合のみ `assign_fitness(...)`（本体 §3.9、引数は A8 の確定を含む）を
   呼び、`fitness_result` に格納する。
8. `rule_ids` は duty → english → hard（手順5） → remote → location → fitness 由来
   （`SAL-*`／`BIZFORM-UNKNOWN` 等）の順に、重複を除いて連結したものとする。

`ExclusionHit` の確定形（本体 §3.8 の具体化）: `judge_hard.py` の
`ExclusionHit = NamedTuple(rule_id: str, reason: str, config_ref: str)` とする。

---

## A2. Config サブ設定オブジェクトの生成方法（本体 §3.1、§4）

### A2.1 置き場と共通規則

- サブ設定クラスは全て `config.py` に置く `dataclass(frozen=True)` とする。
  各 `judge_*`／`fitness` 等の引数型はこのクラスを指す（本体 §3.4〜§3.9 の
  `RemoteConfig` 等の型名は本節のクラス名と同一である）。
- 各クラスは共通の生成方法 **`from_dict(cls, data: Mapping[str, Any]) -> Self`**（classmethod）を持つ。
  - キー名は YAML の葉キー名と完全一致させる（下表のフィールド名どおり）。
  - YAML の list は `tuple` に変換して保持する。未知のキーは `ConfigError`、
    必須キーの欠落・型不一致も `ConfigError`（キー名をメッセージに含める。本体 §3.1 と同じ）。
  - 直接のキーワード引数による構築（通常の dataclass コンストラクタ）も常時可能とする。
    テストは `DutyConfig.from_dict(judge_dict["duty"])` の形、またはキーワード構築の
    どちらでも同じオブジェクトを作れる。
- `load_config(profile_path, judge_path, local_path)`（本体 §3.1 のシグネチャは不変）は、
  3 ファイルを読み、各 `from_dict` で検証してから `AppConfig` を組み立てる。

### A2.2 フィールド一覧（確定）

| クラス | フィールド（全てキーワード可） |
|---|---|
| `ProfileConfig` | `profile_version: int`, `target_domains: tuple[str, ...]`, `target_role_labels: tuple[str, ...]`, `salary_target_jpy: int`, `employment_allowed: tuple[str, ...]`, `remote_preference: str \| None = None`, `location_condition_enabled: bool = True`, `location_hard_exclude: bool = False`, `search_queries: tuple[str, ...] = ()`, `last_updated: date \| None = None` |
| `NormalizeConfig` | `tracking_params: tuple[str, ...] = ()` |
| `DutyConfig` | `target_sections: tuple[str, ...]`, `upstream_keywords: tuple[str, ...]`, `impl_keywords: tuple[str, ...]`, `title_assist_keywords: tuple[str, ...] = ()`, `primary_markers: tuple[str, ...] = ()`, `mixed_handling: str = "check"` |
| `LocationConfig` | `target_sections: tuple[str, ...]`, `whitelist_file: Path \| None = None`（勤務地対応表は別引数で渡すため、本フィールドは実ファイル読込時の参照用。`from_dict` では欠落を許す） |
| `RemoteConfig` | `rules: tuple[RemoteRule, ...]`。`RemoteRule` は `dataclass(frozen=True)` で `pattern: str`, `division: str`（値は本体 §6.3 の閉じた 6 区分のいずれか。範囲外は `from_dict` 時点で `ConfigError`）。`RemoteRule.from_dict({"pattern": ..., "division": ...})` を持つ |
| `EnglishConfig` | `target_sections: tuple[str, ...]`, `mandatory_sections: tuple[str, ...] = ()`, `required_phrases: tuple[str, ...] = ()`, `welcome_phrases: tuple[str, ...] = ()`, `level_phrases: tuple[str, ...] = ()` |
| `HardConfig` | `non_regular_keywords: tuple[str, ...] = ()`, `out_of_domain_keywords: tuple[str, ...] = ()`, `business_form_keywords: tuple[str, ...] = ()` |
| `FitnessConfig` | `salary_check_threshold_jpy: int`, `high_threshold: int`, `domain_match_points: int = 0`, `salary_points: int = 0`, `condition_points: int = 0`, `location_points: int = 0`, `salary_target_jpy: int \| None = None`, `target_domains: tuple[str, ...] = ()` |
| `PresentationConfig` | `weekly_limit: int = 20` |
| `ValidityConfig` | `listing_valid_days: int` |
| `CollectionConfig` | `request_interval_sec: int`, `per_source_max_items: int`, `timeout_sec: int` |
| `GuardConfig` | `max_close_ratio: float`, `max_total_change_ratio: float` |
| `OpenworkConfig` | `weekly_once_per_company: bool = True` |
| `AutoApplyConfig` | `enabled: bool = False`, `weekly_limit: int = 3`, `result_unknown_hold_runs: int = 2` |
| `LocalConfig` | `spreadsheet_id: str = ""`, `sheet_names: SheetNamesConfig`, `banned_words_file: Path \| None = None`, `ats_boards: Mapping[str, tuple[str, ...]] = {}`, `profile_file: Path \| None = None`, `judge_file: Path \| None = None`。`SheetNamesConfig` は `dataclass(frozen=True)` で `candidates: str = "候補リスト"`, `registry: str = "情報源レジストリ"`, `exclusions: str = "除外ログ"`（YAML では `sheet_names.candidates` 等のドット配下） |
| `AppConfig` | `profile: ProfileConfig`, `normalize: NormalizeConfig`, `duty: DutyConfig`, `location: LocationConfig`, `remote: RemoteConfig`, `english: EnglishConfig`, `hard: HardConfig`, `fitness: FitnessConfig`, `presentation: PresentationConfig`, `validity: ValidityConfig`, `collection: CollectionConfig`, `guard: GuardConfig`, `openwork: OpenworkConfig`, `auto_apply: AutoApplyConfig`, `local: LocalConfig` |

- `AppConfig.from_dicts(profile_data: Mapping, judge_data: Mapping, local_data: Mapping) -> AppConfig`
  （classmethod）を確定する。`judge_data` は本体 §4.2 のドットキーをネストした dict
  （`judge_data["duty"]["upstream_keywords"]` の形）を受け取り、各サブ設定へ振り分ける。
  ファイルを読まない単体検証はこの入口を使う。
- 適合度付けに必要なプロファイル由来の値の受け渡し: `from_dicts`／`load_config` は
  `FitnessConfig.salary_target_jpy` が未設定（`None`）の場合に `profile.salary_target_jpy` を、
  `FitnessConfig.target_domains` が空の場合に `profile.target_domains` を、それぞれ補って
  格納する。これにより本体 §3.9 の `assign_fitness` のシグネチャ（`cfg: FitnessConfig`）を
  変えずに、本体 §6.6 の「年収目安との距離」「領域との一致度」を評価できる。
- `DutyConfig.mixed_handling` は `"check"` 以外を `from_dict` 時点で `ConfigError` とする
  （本体 §4.2 の確定どおり）。

### A2.3 勤務地対応表オブジェクト（本体 §3.6、§13）

```python
# models.py（judge_location.py から再エクスポートする）
@dataclass(frozen=True)
class LocationEntry:
    prefecture: str
    municipality: str

@dataclass(frozen=True)
class LocationWhitelist:
    version: int
    municipalities: tuple[LocationEntry, ...]
    notes: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LocationWhitelist: ...
        # §13 の YAML 構造（version / municipalities[{prefecture, municipality}] / notes）と同一形。
    def contains(self, municipality: str) -> bool: ...
        # municipalities のいずれかの municipality と完全一致すれば True。
```

`load_whitelist(path)`（本体 §3.6）は YAML を読み `LocationWhitelist.from_dict` に委譲する。

---

## A3. `CandidateRecord` のコンストラクタ仕様（本体 §2.2、§10.1）

`models.py` の `dataclass(frozen=True)` として確定する。**必須引数は `candidate_id` のみ**とし、
他は全てデフォルトを持つ（単体検証で ID だけからレコードを構築できるようにするため）。
更新は `dataclasses.replace()` で行う（frozen のため代入はしない）。

| フィールド | 型 | デフォルト | 備考 |
|---|---|---|---|
| `candidate_id` | `str` | （必須） | |
| `company` | `str` | `""` | |
| `position` | `str` | `""` | |
| `company_norm` | `str` | `""` | |
| `position_norm` | `str` | `""` | |
| `employment_type` | `str` | `""` | |
| `salary_range_text` | `str \| None` | `None` | |
| `location_text` | `str` | `"不明"` | 本体 §2.2 の「読み取れない場合は `"不明"`」をデフォルトとして確定 |
| `location_result` | `LocationDivision` | `勤務地判定不能` | |
| `remote_division` | `RemoteDivision` | `不明` | |
| `fitness` | `Fitness` | `要確認` | 安全側のデフォルト。提示対象（高／中）にするには明示的に渡すこと（A8 参照） |
| `fit_reasons` | `tuple[Reason, ...]` | `()` | |
| `check_reasons` | `tuple[Reason, ...]` | `()` | |
| `source_type` | `SourceType` | `公開求人` | |
| `source_url` | `LinkCell` | `LinkCell("求人ページ", "")` | |
| `openwork_score` | `float \| None` | `None` | |
| `openwork_url` | `LinkCell \| None` | `None` | |
| `openwork_fetched_on` | `date \| None` | `None` | |
| `presentation` | `Presentation` | `控え` | 「未提示」を表す初期値。提示済みへの遷移は提示処理が行う |
| `presentation_key` | `tuple[int, int, int, str] \| None` | `None` | A8 で確定 |
| `manual_judgment` | `str \| None` | `None` | 人手列「本人の判断」の読み取り専用スナップショット。シート読取時にのみ設定し、**書き込み対象には絶対に含めない**（本体 §3.11 の人手列保護と両立させるための保持場所） |
| `confirmed_on` | `date \| None` | `None` | 本体 §2.2 の型を `date \| None` に読み替える（追補が優先）。`None` は未確認を表し、Sheets 書込時（充足条件を満たすべき実候補）には非 `None` であることを gateway 側で検証エラーとする |
| `status` | `CandidateStatus` | `新規` | |
| `apply_status` | `ApplyStatus` | `未応募` | 本体 §2.2 の初期化規則どおり |
| `applied_at` | `datetime \| None` | `None` | |
| `apply_result` | `str \| None` | `None` | |

付随する型の確定（`models.py`）:

- `Reason = NamedTuple(text: str, rule_ids: tuple[str, ...])`
- `LinkCell = NamedTuple(display_text: str, url: str)` の正本は `models.py` とする。
  本体 §3.11 の `sheets_gateway.LinkCell` は `models.LinkCell` の再エクスポートであり、
  同一クラスを指す（両方の import 経路が使える）。
- 列挙型（全て `StrEnum`、`models.py`）: `SourceType`（本体 §2.1 の 4 値。シート表示値
  `ログイン必須（Secure Vault保存済み）`／`補助：届いたスカウト` への変換は gateway の責務とし、
  型の値は `公開求人`／`企業採用ページ`／`ログイン必須`／`補助スカウト` で固定）、
  `LocationDivision`、`RemoteDivision`、`Fitness`、`Presentation`、`CandidateStatus`、
  `ApplyStatus`（値はいずれも本体 §2.2／§6.3／§10.1 の文字列どおり）。

---

## A4. 通知文生成の公開インターフェースと `RunSummary`（本体 §3.15、§12 手順13、完了の定義⑤）

### A4.1 `RunSummary` のフィールド構成（確定）

`pipeline.py` の `dataclass(frozen=True)` として確定する。**件数と候補 ID・情報源名のみ**を持ち、
会社名・ポジション名・年収等の詳細フィールドは持たない（フィールド名に `company`／
`position`／`salary` を含めないことも仕様とする）。

| フィールド | 型 |
|---|---|
| `run_on` | `date` |
| `dry_run` | `bool` |
| `stopped_by_guard` | `bool` |
| `collected_count` | `int` |
| `added_count` / `updated_count` / `closed_count` | `int` |
| `excluded_count` | `int` |
| `check_only_count` | `int`（要確認の総数） |
| `check_counts_by_reason` | `tuple[tuple[str, int], ...]`（理由区分名 → 件数。本体 §6.2 の区分別件数。区分名は `主務`／`英語`／`勤務地・リモート`／`その他`） |
| `presented_count` | `int` |
| `held_new_count` / `held_promoted_count` / `held_remaining_count` / `held_closed_count` | `int` |
| `openwork_disabled_count` | `int`（本体 §12 手順8 の `OW-DISABLED` 集計） |
| `source_names` | `tuple[str, ...]`（収集対象とした情報源名） |
| `skipped_source_names` | `tuple[str, ...]`（取得エラーでスキップした情報源名） |
| `added_ids` / `updated_ids` / `closed_ids` / `excluded_ids` | `tuple[str, ...]`（候補 ID／除外 ID のみ） |

### A4.2 通知文生成関数（確定）

```python
# pipeline.py
def build_notification_text(summary: RunSummary, list_url: str) -> str: ...
    # 純粋関数。summary とリスト参照（Spreadsheet の URL）だけから通知文を作る。
    # 会社名・ポジション名・年収・勤務地等の詳細は、入力にも出力にも現れない。
```

出力形式（固定テンプレート。数値は summary の件数で置換する）:

```
応募候補リストを更新しました（<run_on の YYYY-MM-DD>）
新規 <added_count>件／更新 <updated_count>件／掲載終了 <closed_count>件／除外 <excluded_count>件／要確認 <check_only_count>件
提示 <presented_count>件／控え 新規<held_new_count>件・繰り上げ<held_promoted_count>件・残<held_remaining_count>件
候補リスト: <list_url>
```

- `dry_run=True` の場合は 1 行目を `応募候補リストのドライラン結果（<日付>）` とする。
- `stopped_by_guard=True` の場合は 1 行目を
  `応募候補リストの更新を停止しました（大量変更ガード・<日付>）` とし、
  件数行はドライラン差分の件数をそのまま使う。本実行が行われていないことを文面で判別できる形にする。
- 通知文に対しても禁止ワードスキャン（本体 §3.3）の対象に含めるかは従来どおり
  運用側の手順に従い、本関数はスキャンを行わない（生成と検査を分離する）。

### A4.3 付随確定: `Diff`／`GuardResult`（本体 §3.13）

```python
# diff_guard.py
@dataclass(frozen=True)
class Diff:
    added: tuple[str, ...]              # 候補ID
    updated: tuple[str, ...]            # 候補ID（同一IDで自動列のいずれかが変化したもの）
    closed: tuple[str, ...]             # 候補ID
    presented_planned: tuple[str, ...]  # 候補ID
    held_new: tuple[str, ...]
    held_promoted: tuple[str, ...]
    held_remaining: tuple[str, ...]
    held_closed: tuple[str, ...]
    check_only_count: int
    registry_changes: tuple[str, ...]   # 変更内容の区分文字列（情報源名＋変更種別）のみ
    auto_apply_stop: bool = False       # 本体 §3.13 の「自動応募のみ停止フラグ」

class GuardStatus(StrEnum):
    OK = "ok"
    STOP = "stop"

@dataclass(frozen=True)
class GuardResult:
    status: GuardStatus
    reasons: tuple[str, ...] = ()       # 停止理由の区分文字列（詳細情報は含めない）
```

`compute_diff` の `updated` 判定は「候補 ID が一致し、自動列に対応するフィールドの
いずれかが異なる」ことをもって行う（人手列スナップショット `manual_judgment` は比較対象外）。

---

## A5. `SheetsGateway` の具象クラス名（本体 §3.11）

具象クラス名を **`GoogleSheetsGateway`**（`sheets_gateway.py`）で確定する。

```python
# sheets_gateway.py
class SheetsBackend(Protocol):
    """実 Sheets API との低レベル入出力境界。テストではフェイクに差し替える。"""
    def read_rows(self, tab_name: str) -> list[list[str]]: ...
    def read_links(self, tab_name: str) -> dict[tuple[int, int], str]: ...
        # (行, 列) → 実URL。表示テキスト付きリンクの読み戻し用。
    def write_rows(self, tab_name: str, rows: Sequence[Sequence[str]]) -> None: ...
        # gateway が自動列の範囲だけに切り出した行を受け取り、タブへ書き込む。

class GoogleSheetsGateway:  # SheetsGateway Protocol の具象実装
    def __init__(self, backend: SheetsBackend,
                 sheet_names: Mapping[str, str] | None = None) -> None: ...
        # sheet_names のキーは "candidates" / "registry" / "exclusions"。
        # None の場合は SheetNamesConfig のデフォルト値（A2.2）を使う。
```

- 人手列保護の実クラス検証は `GoogleSheetsGateway` に対して行う: `upsert_candidates` が
  backend へ渡す行は `AUTO_COLUMNS_CANDIDATE` の列だけから構成され、
  `MANUAL_COLUMNS_CANDIDATE` の列位置へは書き込みデータを渡さない。
- 実 API 接続用の backend 実装クラス名は `GoogleApiSheetsBackend` とする
  （コンストラクタは `(service: object, spreadsheet_id: str)`。`service` は
  Google Sheets API クライアントオブジェクトで、型は Protocol で縛らない）。
- テスト側の `FakeSheetsGateway`（conftest）は Protocol に対するフェイクとして従来どおり有効であり、
  具象クラス名の確定によって置き換えない。

付随確定: `append_exclusions` の行型（本体 §3.11、§10.3）。

```python
# models.py
@dataclass(frozen=True)
class ExclusionRow:
    exclusion_id: str
    fetched_on: date
    company: str
    position: str
    employment_type: str | None
    source_type: SourceType
    source_url: LinkCell
    reason: str
    rule_ids: tuple[str, ...]
    config_refs: tuple[str, ...]
```

---

## A6. `Registry`／`SourceEntry`／`RegistryIssue` の属性名（本体 §3.10、§8）

テスト係の想定どおり、**`Registry.sources` を正式な属性名として確定する**（差分なし）。

```python
# registry.py
@dataclass(frozen=True)
class MeasuredResult:      # 実測結果セルのパース結果（本体 §8.3）
    result: str            # 成功／失敗／未実施／対象外（ログイン不要）
    method: str            # 取得方法の値文字列
    checked_on: date | None

@dataclass(frozen=True)
class RiskAssessment:      # リスク評価セルのパース結果（本体 §8.3）
    level: str             # 低／中／高／未評価
    basis: str             # 根拠の文字列
    checked_on: date | None

@dataclass(frozen=True)
class SourceEntry:
    source_id: str                 # 情報源名（行キー列）
    url_pattern: str               # URLパターン
    login_requirement: str         # ログイン要否（閉じた値の文字列）
    credential_status: str         # 資格情報の有無（閉じた値の文字列）
    measured_text: str             # 実測結果セルの原文
    measured: MeasuredResult | None  # パース結果。形式不正時は None とし検証で issue 化する
    fetch_availability: str        # 自動取得可否（閉じた値の文字列）
    risk_text: str                 # リスク評価セルの原文
    risk: RiskAssessment | None
    cost_condition: str            # 費用条件確認（閉じた値の文字列）
    auto_apply: str                # 自動応募可否（可／不可）
    send_method: str               # 送信確定方法（閉じた値の文字列）
    final_decision: str            # 対象化の最終判断（人手列・閉じた値の文字列）
    note: str                      # 備考（人手列）
    row_index: int                 # データ行の 0 始まりインデックス（ヘッダを除く）

@dataclass(frozen=True)
class Registry:
    header: tuple[str, ...]        # §8.1 の列名列（検証済みのもの）
    sources: tuple[SourceEntry, ...]  # 行順を保持する

    def find(self, source_id: str) -> SourceEntry | None: ...

class IssueKind(StrEnum):
    SCHEMA = "schema"
    SOURCE_BLOCKED = "source_blocked"
    MAJOR_CHANGE = "major_change"

@dataclass(frozen=True)
class RegistryIssue:
    source_id: str | None
    kind: IssueKind
    detail: str
```

- `parse_registry(rows)` は行 → `SourceEntry` への変換と §8.3 形式のパースまでを行い、
  閉じた値の範囲外・形式不正は例外にせず保持し、`validate_registry` が
  `RegistryIssue(kind=SOURCE_BLOCKED)` として返す（全体停止はヘッダ不一致の
  `RegistrySchemaError` のみ。本体 §3.10／§8.4 と同じ振り分け）。
- `openwork_gate(reg)` の対象行は `source_id == "OpenWork"` の行とし、定数
  `OPENWORK_SOURCE_ID: str = "OpenWork"`（`registry.py`）を公開する。

---

## A7. OpenWork 再取得失敗時の前回値保持の責務（本体 §3.14、§6.7、§5.5）

**保持の責務は `attach_openwork` 側に置く**ことを確定し、引数を次のとおり改める
（本体 §3.14 のシグネチャに対する追補変更）。

```python
# openwork.py
def attach_openwork(candidate: CandidateRecord, provider: OpenworkProvider,
                    gate_enabled: bool, cfg: OpenworkConfig,
                    previous: OpenworkResult | None = None) -> OpenworkResult: ...
```

- `previous` は前回取得済みの 3 項目（候補の既存行の `openwork_score`／`openwork_url`／
  `openwork_fetched_on` から pipeline が組み立てて渡す）。pipeline 側の責務は
  「既存行があれば `previous` を渡すこと」と「`OpenworkBlocked` 送出時は既存行の
  3 項目を書き換えないこと」の 2 点に限る。
- 失敗時の戻り値（本体 §6.7 の「前回値を保持し取得日は前回のまま」の具体化）:
  - 突合不能（`OW-NOMATCH`）／存在しない（`OW-NOTFOUND`）／取得エラー（`OW-UNAVAILABLE`）の
    いずれでも、`previous` が非 `None` の値を持つ場合は、その値をそのまま返し、
    `rule_ids` には当該失敗のルール ID を格納する（保持したことは `fetched_on` が
    前回の日付のままであることで判別する。新規ルール ID は追加しない）。
  - `previous` が無い／全て `None` の場合は、本体 §3.14 どおり 3 項目すべて `None` を返す。
  - ゲート無効（`gate_enabled=False`）の場合は provider を一切呼ばず、
    `previous` があればその値を `rule_ids=["OW-DISABLED"]` で返し、無ければ
    3 項目 `None` を `rule_ids=["OW-DISABLED"]` で返す。
  - `OpenworkBlocked`（403／429 等）は従来どおり例外として送出し、戻り値では表さない。
    例外の正本は `errors.py` とし、`openwork.py` からも同名で再エクスポートする（両経路可）。
- 付随確定: 突合入力の型（本体 §3.14）。

```python
# openwork.py
@dataclass(frozen=True)
class OpenworkCompanyRef:
    company_norm: str        # 正規化会社名
    score: float
    url: str                 # 実URL（表示テキストではない）
    location_text: str | None = None   # 公開情報の勤務地（突合用）
    industry_text: str | None = None   # 公開情報の業種（突合用）
```

  成功時の戻り値は `OpenworkResult(score=ref.score, url=LinkCell("OpenWork", ref.url),
  fetched_on=date.today(), rule_ids=[])` とする。突合は本体 §3.14 どおり、
  正規化会社名の一致に加え `location_text`／`industry_text` が候補側の公開情報と
  矛盾しない候補がちょうど 1 件の場合のみ成功とする。

---

## A8. `recheck_held` の `refitness` 引数の型と、提示順キーの保持場所（本体 §3.9、§3.12、§6.6）

### A8.1 `refitness` の型（確定）

```python
# presentation.py
def recheck_held(candidate: CandidateRecord, latest: RawPosting | None,
                 refitness: FitnessResult | None) -> HeldDecision: ...
```

- 型は **`FitnessResult | None`** とする（`Fitness` 単体ではない）。
  理由・ルール ID を含む再判定結果一式を受け取るためである。
- 引数の意味と戻り値の確定（本体 §3.12 の 3 点確認の具体化）:
  - `latest is None` → `closed`（掲載終了。再判定の有無によらない）
  - `candidate.status == 掲載終了` → `closed`
  - `latest` があり `refitness is None` → `to_check`（再判定なしでは繰り上げない安全側）
  - `candidate.manual_judgment == "対象外"` → `keep_held`（本人の判断が対象外の間は繰り上げない）
  - `refitness.fitness == 要確認` → `to_check`
  - `refitness.fitness in (高, 中)` かつ上記いずれにも該当しない → `promote`
- `HeldDecision` は `presentation.py` の `StrEnum` で、値は本体 §3.12 どおり
  `promote`／`keep_held`／`closed`／`to_check` とする。

### A8.2 提示順キー（`presentation_key`）の保持場所（確定）

- **候補側（`CandidateRecord.presentation_key`）に保持する**ことを確定する（A3 のフィールド）。
  `assign_fitness` が生成した `FitnessResult.presentation_key` を、pipeline が
  レコード生成時にそのままコピーして保持する。
- `FitnessResult.presentation_key` の型は `tuple[int, int, int, str]` で、要素は
  `(適合度順位, -機械的一致度, -掲載確認日の toordinal(), candidate_id)` とする
  （適合度順位は 高=0／中=1／要確認=2。昇順ソートで本体 §6.6 の提示順になる）。
- `split_presentation` のソート規則（確定）:
  - 対象は `fitness in (高, 中)` かつ `status != 掲載終了` かつ
    `manual_judgment != "対象外"` かつ `presentation == 控え`（＝未提示）の候補とする。
    `presentation == 提示済み` の候補は対象に含めない（週をまたいだ二重提示の防止）。
  - ソートキーは `candidate.presentation_key` をそのまま使う。`None` の候補は
    フォールバックキー `(適合度順位, 0, -confirmed_on の toordinal（None は 0）, candidate_id)`
    で比較する（機械的一致度不明は 0 点扱い）。
  - 補助関数 `presentation_sort_key(candidate: CandidateRecord) -> tuple[int, int, int, str]`
    を `presentation.py` に公開し、`split_presentation` はこの関数でソートする。

---

## A9. (f) サンプルの勤務地表記とテスト側フィクスチャ対応表（本体 §11.2 (f)、§13、§15-1）

**確定: テスト係の暫定措置（架空名＋テスト側フィクスチャ対応表で内／外を決める形）を
正式な恒常措置として承認する。** 本人の実対応表が未確定（本体 §15-1 のチェックポイント）
であるための暫定措置として妥当であるだけでなく、確定後も差し替えない。

根拠と方針:

- サンプル実体に実在の市区町村名（本人の実対応表の範囲を推測させるもの）を書くことは、
  本体 §11.1／§13 の秘匿原則（範囲の具体値はローカル保持）に反する。架空名
  （対応表内を表す架空名／対応表外を表す架空名／遠方を表す架空名）はこの原則と整合する。
- (f) の期待結果は「フィクスチャ対応表に対して内か外か」で定義されており、
  実対応表の内容には依存しない。したがって実対応表の確定は (f) の期待結果を変えない。
- テスト側フィクスチャ対応表の正本は `tests/conftest.py` の `location_whitelist_dict`
  とし、その構造は A2.3 の `LocationWhitelist.from_dict` がそのまま受け取れる形
  （`version`／`municipalities[{prefecture, municipality}]`／`notes`）で固定する。

実対応表確定後の扱い（差し替え方針）:

- **サンプル実体・フィクスチャ対応表・(f) の期待結果は差し替えない**（恒常の検証資産とする）。
- 実対応表確定時に行うのは次の 2 点のみ: (1) ローカル設定（gitignore 対象）への実値登録、
  (2) 必要に応じて、実対応表を使った勤務地判定の確認をローカル環境だけで行う
  （実値を含む検証結果はリポジトリにコミットしない）。
- 配置表（storage-spec/配置表.md）の凡例にある「勤務地は架空名を用い、内／外は
  テスト側フィクスチャで決める」旨の注記は、実対応表確定後もそのまま維持する。

付随する判定規則の具体化（本体 §3.6 の範囲内）: `judge_location` の照合順序を次で固定する。

1. 対応表内の市区町村名が勤務地系セクションの本文に部分文字列として現れる →
   勤務地適合（`LOC-OK`、`matched_municipality` はその市区町村名）
2. 現れないが、市区町村らしい表記（`市区町村` のいずれかで終わる文字列）が抽出できる →
   勤務地不適合（`LOC-OUT`）
3. どちらでもない → 勤務地判定不能（`LOC-UNKNOWN`）

---

## A10. 配置表「種類」の値集合（本体 §11.1）

**確定: 配置表「種類」の値集合は `検証用求人サンプル`／`モックアップ`／`その他` の
3 値で正式に固定し、現在の 39 行は全行 `検証用求人サンプル` で正とする。**
テスト係が配置表の凡例に明記した形をそのまま正式仕様とする（差分なし）。

- 値集合の正本は配置表の凡例とし、本体 §11.1 の表記と一致していることをもって確定とする。
- 新しい値を追加する場合は、本体 §11.1 の改訂・配置表の凡例・テスト側の許容値集合
  （`ALLOWED_KINDS`）の 3 か所を同時に更新することを必須とする（片方だけの追加は
  値検証で不一致として扱う）。

---

## A11. `remote.rules` の同義変換の表現方法（本体 §3.4、§4.2、§6.3）

**確定: 同義表記は正規表現パターンの選択肢（`|`）で吸収する。変換ロジック
（日数の数値計算等）は実装しない。**

- `remote.rules` は `list[{pattern, division}]`（A2.2 の `RemoteRule`）の順序評価とし、
  `normalize_remote_frequency` は規則を先頭から評価し、`re.search(pattern, raw)` が
  最初に一致した規則の `division` を採用する。一致が無ければ `不明`（`REMOTE-UNKNOWN`）。
- 「週3日リモート＝週2日出社」のような同義は、同一区分の規則の pattern に選択肢として
  並べて表現する（例: `週3日リモート|週2日出社` → `出社週2〜3日`）。
  週 5 日勤務を前提とする読み替えは、規則を書く側（設定）が表現し、コードは日数を計算しない。
- 規則の記述順序は「具体的な表記 → 一般的な表記」とし、より具体的な同義規則を
  一般規則より前に置く運用を、テンプレート側のコメントで案内する（コードは順序を解釈しない）。
- `RemoteResult.raw_found` には一致した部分文字列（`re.search` の `group(0)`）を格納し、
  原文表記が適合理由に残る仕組み（本体 §5.2 `REMOTE-NORMALIZED`）を担保する。
- 同義表記の追加は設定変更のみで対応し、コード変更を要しない（完了の定義① の構造を維持する）。

---

## 付録. 本追補に付随して確定する残りの型（本体 §1.2、§3.15）

情報源アダプタの共通インターフェース（`pipeline.run_collection` の `adapters` の値型）:

```python
# sources/base.py
class SourceAdapter(Protocol):
    def fetch(self, source: SourceEntry) -> list[RawPosting]: ...
        # 取得エラー時は SourceFetchError（errors.py）を送出する。
        # pipeline は当該情報源をスキップし、リトライしない（本体 §12 手順5）。
```

以上。
