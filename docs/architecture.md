# 設計文書（architecture）― 応募候補リスト作成・第1段階

- 位置づけ: 本書は企画書（組織リポジトリ Issue #1。以下「企画書」）の「構成案」「完了の定義①〜⑩」を
  実装可能な設計に落としたものである。企画書と本書が矛盾する場合は企画書が優先し、本書を改訂する。
- 本書の範囲: **第1段階のみ**（志向プロファイルの設定ファイル化／情報源レジストリの確定／
  応募候補リスト Spreadsheet の作成／初回リストの作成）。
  第2段階（cron 化）・第3段階（自動応募）は実装しない。ただし、第3段階で必要になる列・値定義
  （応募状態、自動応募可否、送信確定方法等）は第1段階のスキーマに含めて確定する。
- 調査日: 本書の情報源調査は **2026-11-05** に実施した（§9）。
- 秘匿原則: 本書・本リポジトリには、個人名・現職の具体名・起点駅名・メールアドレス・接続情報・
  認証情報を書かない。勤務地条件は「起点から現実的に通勤可能な東京都近辺」と一般化して表記する。
  設定の実値はローカルの gitignore 対象ファイルにのみ置き、リポジトリには伏せ字テンプレートのみを置く。

## 1. 全体構成

### 1.1 実行形態（第1段階）

- 実行は **手動実行のみ**（CLI を人が起動する）。cron・常駐プロセスは作らない。
- 処理の流れ: 設定読込 → レジストリ読取と検証（フェイルクローズ）→ 禁止ワードスキャン →
  情報源ごとに取得（エラー時はその情報源をスキップし、リトライしない）→ 項目抽出・正規化 →
  判定（ハード除外／適合度付け）→ OpenWork 付与（レジストリゲート付き）→ ドライラン差分 →
  大量変更ガード → Sheets 書込（自動列のみ）→ 実行ログ（件数と ID のみ、詳細は書かない）。

### 1.2 モジュール構成（Python パッケージ `src/job_candidate_list/`）

| モジュール | 責務 | 主な入出力 |
|---|---|---|
| `config.py` | 設定ファイル（YAML）の読込と検証。コードへの値の直書きを防ぐ唯一の入口 | パス → `AppConfig` |
| `models.py` | データ構造定義（`RawPosting`／`CandidateRecord`／判定結果群）。副作用なし | 型定義のみ |
| `normalize.py` | 会社名・ポジション名・URL の正規化、候補 ID 生成、突合キー生成 | 文字列 → 正規化文字列／候補 ID |
| `banned_words.py` | 禁止ワードスキャン（検索クエリ・外部入力・OpenWork 検索クエリ・将来の応募入力が対象） | 文字列群 → ヒット一覧（1 件でもあれば例外） |
| `registry.py` | 情報源レジストリ（Sheets タブ）の読取、スキーマ検証・値検証、フェイルクローズ判定、OpenWork ゲート判定 | 行群 → `Registry`／検証エラー一覧 |
| `sources/base.py` | 情報源アダプタの共通インターフェース（Protocol） | `fetch()` |
| `sources/ats_greenhouse.py` 他 | 情報源ごとの取得・項目抽出（公式公開 API 優先。HTML 取得は条件付き対象源のみ） | ボード参照 → `list[RawPosting]` |
| `judge_duty.py` | 主務判定（上流／実装／不明／混在） | セクション群 → `DutyResult` |
| `judge_location.py` | 勤務地判定（対応表ホワイトリストによるローカル判定） | 勤務地記述＋対応表 → `LocationResult` |
| `judge_remote.py` | リモート頻度の出社日数基準への正規化 | 原文表記 → `RemoteResult` |
| `judge_english.py` | 英語必須判定（必須／非必須／不明） | セクション群 → `EnglishResult` |
| `judge_hard.py` | その他ハード除外（雇用形態／領域外／事業形態） | `RawPosting` → 除外ヒット一覧 |
| `fitness.py` | 適合度付け（高／中／要確認）、適合理由・要確認理由の組み立て、提示順スコア算出 | 各判定結果 → `FitnessResult` |
| `openwork.py` | OpenWork 突合・点数付与（レジストリで可と確定した場合のみ動作。それ以外は即・空欄を返す） | 候補＋会社名 → `OpenworkResult` |
| `sheets_gateway.py` | Google Sheets 入出力。**自動列のみ**書込可能（列ホワイトリストで物理的に制限）。表示テキスト付きリンクの生成・読み戻し | `CandidateRecord` ⇄ セル群 |
| `presentation.py` | 提示上限 N・控え管理、提示順ソート、繰り上げ再確認 | 候補群 → 提示／控えの振り分け |
| `diff_guard.py` | ドライラン差分（追加・更新・掲載終了・提示/控え内訳）、大量変更ガード、レジストリ重大変更の検出 | 旧状態＋新状態 → `Diff`／`GuardResult` |
| `pipeline.py` | 第1段階の手動実行パイプライン（上記の統括。失敗段ごとの扱いは §12） | `AppConfig` → 実行結果サマリ（件数・ID のみ） |
| `cli.py` | CLI 入口（`collect`（初回収集）／`dry-run`／`validate-config`／`validate-registry`） | コマンドライン |

設計原則:

- 判定に効く値はすべて設定側（§4）に置き、各モジュールは設定を受け取って動く（依存性注入）。
  モジュール内にキーワード・閾値・上限を直書きしない。
- `judge_*`／`normalize`／`fitness`／`presentation`／`diff_guard` は外部 I/O を持たない純粋関数群とし、
  テスト係がモックなしで検証できる形にする。I/O は `sources/*`／`sheets_gateway`／`openwork` の
  プロバイダ境界に寄せ、テストではフェイク実装へ差し替える。
- 人手列への書込は `sheets_gateway` の列ホワイトリストに人手列を含めないことで構造的に不可能にする。

### 1.3 プロジェクト構成と pyproject.toml の設計方針（実ファイル作成は Executor）

```
pyproject.toml            # [build-system] は setuptools、requires-python >= 3.11
src/job_candidate_list/   # 上記モジュール群
tests/                    # pytest（テスト係が完了の定義から先行作成）
config/
  templates/              # 伏せ字テンプレートのみを git 管理（profile.example.yaml 等）
  local/                  # 実値（gitignore 済み。.gitignore の config/local/ で担保）
storage-spec/             # モックアップ／サンプル配置の正本（§11）
docs/architecture.md      # 本書
docs/引き継ぎ.md           # Executor が作成（骨子は §14）
```

- 整形: Black（line-length は Black 既定 88）、import 順: isort（`profile = "black"`）。
  いずれも `pyproject.toml` の `[tool.black]`／`[tool.isort]` に設定する。
- 型: 公開インターフェース（§3）の引数・戻り値には型注釈を必須とする（`dataclass(frozen=True)` を基本形とする）。
- 依存の上限: 実行時依存は YAML 読込・HTTP 取得・Google Sheets 接続に必要な最小限に絞る。
  有料 API・有料サービスに依存するライブラリ／SDK は入れない（費用 0 円の前提）。
- テスト: pytest。外部 I/O は Protocol 境界でフェイクに差し替える。実ネットワーク・実シートへの
  結合テストは第1段階の範囲外とし、検証は §11 のサンプルとフェイクで行う。

## 2. データ構造

### 2.1 `RawPosting`（取得直後・判定前の求人）

| フィールド | 型 | 備考 |
|---|---|---|
| `source_id` | `str` | レジストリの情報源名（行キー）と一致させる |
| `source_type` | `SourceType` | `公開求人`／`企業採用ページ`／`ログイン必須`／`補助スカウト` のいずれか |
| `raw_url` | `str` | リンク先の実 URL（表示テキストではない） |
| `company_raw` | `str` | 会社名（原文） |
| `position_raw` | `str` | ポジション名（原文） |
| `employment_raw` | `str \| None` | 雇用形態の原文。読み取れない場合は `None`（推測で埋めない） |
| `salary_min_jpy` / `salary_max_jpy` | `int \| None` | 年収レンジ（円・年額換算できた場合のみ）。換算不能なら両方 `None` とし `salary_raw` を残す |
| `salary_raw` | `str \| None` | 年収の原文表記 |
| `location_raw` | `str \| None` | 勤務地の原文（勤務地系セクションからのみ読む） |
| `remote_raw` | `str \| None` | リモート頻度の原文表記 |
| `sections` | `dict[str, str]` | セクション名 → 本文。主務・英語判定の入力。キー集合は設定の「対象セクション」で参照する |
| `company_overview_raw` | `str \| None` | 事業内容・会社概要の原文（事業形態・領域判定の入力） |
| `fetched_on` | `date` | 掲載確認日として使う取得日 |

### 2.2 `CandidateRecord`（シートの候補リストタブ 1 行に対応）

自動列の項目定義は §10.1 と 1 対 1 に対応する。型の要点のみ記す。

| フィールド | 型 | 備考 |
|---|---|---|
| `candidate_id` | `str` | §7 の生成規則。初回登録時に確定し以後変更しない |
| `company` / `position` | `str` | 表示用は原文、突合用は正規化形を別途保持（`company_norm`／`position_norm`） |
| `employment_type` | `str` | 原文から読み取れた雇用形態。不明な場合は空ではなく理由側に記録し、本列は原文どおり |
| `salary_range_text` | `str \| None` | 公開情報がある場合のみ。なければ `None`（シートでは空欄） |
| `location_text` | `str` | 勤務地。読み取れない場合は `"不明"` |
| `location_result` | `LocationDivision` | `勤務地適合`／`勤務地不適合`／`勤務地判定不能`／`フルリモート対象外` |
| `remote_division` | `RemoteDivision` | §6.3 の閉じた 6 区分 |
| `fitness` | `Fitness` | `高`／`中`／`要確認` |
| `fit_reasons` / `check_reasons` | `list[Reason]` | `Reason = (text: str, rule_ids: list[str])`。理由文には駅名を書かない |
| `source_type` / `source_url` | `SourceType` / `LinkCell` | `LinkCell = (display_text: str, url: str)`。表示テキストは `"求人ページ"` 固定 |
| `openwork_score` / `openwork_url` / `openwork_fetched_on` | `float \| None` / `LinkCell \| None` / `date \| None` | 3 列はセットで埋めるか、セットで空欄（§6.7） |
| `presentation` | `Presentation` | `提示済み`／`控え` |
| `confirmed_on` | `date` | 掲載確認日 |
| `status` | `CandidateStatus` | `新規`／`継続`／`更新あり`／`掲載終了` |
| `apply_status` | `ApplyStatus` | `未応募`／`送信処理中`／`応募済み`／`応募失敗`／`結果不明`／`手動応募が必要`。第1段階では全件 `未応募` で初期化し、以後この段階では遷移させない（スキーマのみ確定） |
| `applied_at` / `apply_result` | `datetime \| None` / `str \| None` | 第1段階では空欄（スキーマのみ確定） |

### 2.3 判定結果の共通形

各 `judge_*` の戻り値は `dataclass` とし、最低限 `rule_ids: list[str]`（発火した §5 の ID）と
`reasons: list[str]`（一般化した理由文。駅名・個人情報を含めない）を持つ。
テスト係は `rule_ids` と区分フィールドを検証の主対象とする（理由文の文言一致は検証対象にしない）。

## 3. 公開インターフェース仕様

テスト係は本節だけを見てテストを書けることを基準とする。例外型は `job_candidate_list.errors` に置く:
`ConfigError`／`RegistrySchemaError`／`BannedWordHit`／`SourceFetchError`／`GuardStopped`。

### 3.1 設定読込（`config.py`）

```python
def load_config(profile_path: Path, judge_path: Path, local_path: Path) -> AppConfig: ...
    # 必須キーの欠落・型不一致・閉じた値の範囲外は ConfigError（キー名をメッセージに含める）。
    # 実値ファイル（local）が無い場合も ConfigError とし、テンプレートを実値として代用しない。

def validate_config(cfg: AppConfig) -> list[str]: ...
    # 検証のみ（例外を投げない）。問題の説明文字列のリストを返す。正常時は空リスト。
```

### 3.2 正規化・候補 ID（`normalize.py`）

```python
def normalize_company(name: str) -> str: ...
    # 法人格表記（株式会社・有限会社・合同会社等、前置・後置とも）の除去、全角→半角（英数・カナは
    # NFKC を適用しつつ日本語の表記は保持する旨を実装メモに残す）、空白（全角含む）の除去。
def normalize_position(name: str) -> str: ...
    # 全角・半角の統一、空白の除去、前後の装飾記号の除去（対象は設定ではなく本関数の仕様として固定）。
def normalize_url(url: str) -> str: ...
    # スキームを https に統一、末尾スラッシュの統一（パス末尾の "/" を除去。ただしルートは保持）、
    # トラッキングパラメータの除去（除去対象パラメータ名は設定 `normalize.tracking_params`）。
    # フラグメントは除去する。表示テキストは入力に一切使わない。
def candidate_id(company_norm: str, position_norm: str, url_norm: str) -> str: ...
    # 形式: "cand_" + sha256(f"{company_norm}\n{position_norm}\n{url_norm}" UTF-8) の hex 先頭 16 桁。
    # 同一入力では常に同一値を返す（安定性はテスト対象）。
def fallback_match_key(company_norm: str, position_norm: str) -> str: ...
    # URL の正規化結果が変わった場合の突合キー: sha256(f"{company_norm}\n{position_norm}") の hex 先頭 16 桁。
```

### 3.3 禁止ワードスキャン（`banned_words.py`）

```python
def load_banned_words(path: Path) -> list[str]: ...
    # ローカル保持（gitignore 対象）。ファイルが無い場合は ConfigError（スキャン省略で続行しない）。
def scan_banned_words(texts: Sequence[str], words: Sequence[str]) -> list[BannedHit]: ...
    # BannedHit = (text_index: int, word: str)。部分一致で判定する。ヒット箇所の周辺文は返さない
    # （禁止ワード自体をログ・例外メッセージに含めないため、word は呼び出し側のメモリ内でのみ使う）。
def assert_no_banned_words(texts: Sequence[str], words: Sequence[str]) -> None: ...
    # 1 件でもヒットしたら BannedWordHit を送出する。例外メッセージには件数のみを含める。
```

### 3.4 リモート頻度正規化（`judge_remote.py`）

```python
def normalize_remote_frequency(raw: str | None, cfg: RemoteConfig) -> RemoteResult: ...
    # RemoteResult = (division: RemoteDivision, rule_ids: list[str], raw_found: str | None)
    # 区分（閉じた集合）: フルリモート（出社なし）／出社週1日以下／出社週2〜3日／出社週4日以上／
    #   出社必須（フル出社）／不明
    # - raw が None・空・規則に一致しない → 不明（REMOTE-UNKNOWN）。
    # - 同義表記は出社日数へ寄せる（週3日リモート＝週2日出社 → 出社週2〜3日）。
    #   変換規則は設定 `remote.rules`（正規表現 → 区分） の順序評価で実装し、直書きしない。
    # - 「月1回出社」等の部分出社はフルリモートに含めない（出社週1日以下側へ倒す）。
```

### 3.5 主務判定（`judge_duty.py`）

```python
def judge_main_duty(sections: dict[str, str], cfg: DutyConfig) -> DutyResult: ...
    # DutyResult = (outcome: DutyOutcome, rule_ids: list[str], reasons: list[str],
    #               matched_upstream: list[str], matched_impl: list[str])
    # DutyOutcome: upstream（上流）／impl（実装等）／unknown（不明）／mixed（混在）
    # 根拠は設定 `duty.target_sections` のセクションのみ。他セクションは根拠にしない。
    # 「主担当」「メイン業務」等の主担当明示（設定 `duty.primary_markers`）があれば、その記述を優先する。
    # 上流のみ → upstream（JOB-UPSTREAM-OK）。実装のみ → impl（JOB-IMPL-HARD）。
    # 両群ヒットかつ主担当明示なし → mixed（JOB-MIXED）。どちらもヒットなし → unknown（JOB-UNKNOWN）。
    # 「設計」の単独語は両群に登録しない（文脈付き表現のみ）。設計のみと読める場合は
    #   unknown とし JOB-DESIGN-ONLY を併記する。要件定義のみの場合も unknown（JOB-UNKNOWN）。
    # 肩書きキーワード（設定 `duty.title_assist_keywords`）は補助情報として理由に残すだけで、
    #   単独では outcome を決定しない。
```

### 3.6 勤務地判定（`judge_location.py`）

```python
def load_whitelist(path: Path) -> LocationWhitelist: ...
    # §13 の形式。ファイルが無い・形式不正の場合は ConfigError（判定不能で全件流すことはしない）。
def judge_location(posting: RawPosting, remote: RemoteResult,
                   whitelist: LocationWhitelist, cfg: LocationConfig) -> LocationResult: ...
    # LocationResult = (division: LocationDivision, rule_ids: list[str], reasons: list[str],
    #                   matched_municipality: str | None)
    # - remote.division == フルリモート（出社なし）→ フルリモート対象外（LOC-FULLREMOTE-EXEMPT）。
    #   部分出社（月1回等）は対象外にしない。
    # - remote.division == 不明 → 勤務地判定不能（LOC-UNKNOWN かつ REMOTE-UNKNOWN を併記）。
    # - 勤務地は設定 `location.target_sections`（勤務地／勤務先／就業場所／アクセス等）からのみ読む。
    #   本社所在地のみの記載は根拠にせず判定不能。複数勤務地で主たる勤務地が特定できなければ判定不能。
    #   転勤あり・全国勤務は判定不能。勤務地選択制は選択肢の全てが対応表内にある場合のみ適合。
    # - 市区町村名を抽出し対応表と照合: 内 → 勤務地適合（LOC-OK）／外であることが明確 → 勤務地不適合
    #   （LOC-OUT）／抽出できない・対応表に結論がない → 勤務地判定不能（LOC-UNKNOWN）。
    # - 外部 API・経路計算は使わない。本関数はネットワークアクセスを行わない。
```

### 3.7 英語必須判定（`judge_english.py`）

```python
def judge_english(sections: dict[str, str], cfg: EnglishConfig) -> EnglishResult: ...
    # EnglishResult = (outcome: EnglishOutcome, rule_ids: list[str], reasons: list[str])
    # EnglishOutcome: required（必須）／not_required（非必須）／unknown（不明）／no_mention（言及なし）
    # 根拠は設定 `english.target_sections` のみ。判定順序（優先順位はこの順で固定する）:
    #  1. 対象セクション内に必須明記表現（設定 `english.required_phrases`）が 1 つでもあれば required
    #     （ENG-HARD）。歓迎表現の併記があっても変わらない（必須優先）。
    #  2. 必須系セクション（設定 `english.mandatory_sections`、または見出しに「必須」を含むセクション）内に
    #     レベル表現（設定 `english.level_phrases`）のみがある場合も required（ENG-HARD）。
    #  3. 数値スコア（TOEIC 等）が必須語と結びつく → required、目安・歓迎・尚可と結びつく → not_required、
    #     どちらとも結びつかない単独出現 → unknown（ENG-UNKNOWN）。
    #  4. 歓迎表現（設定 `english.welcome_phrases`）に結びつく記載のみ → not_required（ENG-WELCOME-OK）。
    #  5. 英語への言及はあるが必須性が読み取れない → unknown（ENG-UNKNOWN）。
    #  6. 対象セクション外にのみ必須明記がある → unknown とし ENG-OUT-SECTION を併記する（ハード除外しない）。
    #  7. 英語への言及自体がない → no_mention（ルール ID なし。通常判定に進む）。
    # 対象言語は英語のみ。他言語の必須記載は本判定の対象外とする。
```

### 3.8 その他ハード除外（`judge_hard.py`）

```python
def judge_hard_exclusions(posting: RawPosting, cfg: HardConfig) -> list[ExclusionHit]: ...
    # ExclusionHit = (rule_id: str, reason: str, config_ref: str)
    # config_ref は「どの設定を直せば判定が変わるか」を示す設定キー名（例: "hard.business_form_keywords"）。
    # 判定する区分:
    #  - 雇用形態が正社員でない（設定 `hard.non_regular_keywords` に一致）→ HARD-EMPTYPE
    #  - 領域外（対象領域キーワードが無く、領域外キーワードに一致）→ HARD-DOMAIN
    #  - 事業形態が SIer・コンサル寄りと公開情報（company_overview_raw・勤務形態の記述）から
    #    明確に判断できる → HARD-BIZFORM。判断が曖昧な場合はヒットさせず、fitness 側で
    #    BIZFORM-UNKNOWN（要確認）として扱う（本関数では返さない）。
    # 雇用形態が読み取れない（employment_raw is None）場合は HARD-EMPTYPE を発火させない
    # （不明を除外に倒さない。fitness 側で要確認理由に残す）。
```

### 3.9 適合度付け（`fitness.py`）

```python
def assign_fitness(posting: RawPosting, duty: DutyResult, location: LocationResult,
                   remote: RemoteResult, english: EnglishResult,
                   cfg: FitnessConfig) -> FitnessResult: ...
    # FitnessResult = (fitness: Fitness, fit_reasons: list[Reason], check_reasons: list[Reason],
    #                  presentation_key: tuple)
    # 前提: ハード除外ヒット済みの候補は本関数に渡さない（pipeline が先に除外ログへ回す）。
    # 適合度の決定規則（上から順に評価し、最初に該当したものを採用する）:
    #  1. duty.outcome in (unknown, mixed) → 要確認
    #  2. english.outcome == unknown → 要確認
    #  3. remote.division == 不明 → 要確認（固定）
    #  4. location.division in (勤務地不適合, 勤務地判定不能) → 要確認
    #  5. 年収レンジが取得でき、かつ上限が設定 `fitness.salary_check_threshold_jpy` を
    #     大幅に下回る → 要確認（SAL-BELOW）。年収不明は単独では要確認にしない（SAL-UNKNOWN を理由に残す）
    #  6. 事業形態が曖昧（BIZFORM-UNKNOWN）→ 要確認
    #  7. 上記以外で duty.outcome == upstream の場合のみ 高／中 を付ける:
    #     年収目安との距離・領域一致・勤務条件の確認可否・勤務地適合の機械的評価（§6.6）で
    #     設定 `fitness.high_threshold` 以上 → 高、未満 → 中。
    # すべての候補に fit_reasons と check_reasons の両方を必ず生成する（該当が無い側は
    # 「特になし」相当の固定文ではなく、評価した観点とルール ID を残す）。
    # presentation_key は提示順の比較キー（§6.6）: (適合度順位, 機械的一致度の降順用負値,
    #   掲載確認日の降順用負値, candidate_id) のタプルとし、ソートで毎回再現可能にする。
```

### 3.10 レジストリ読取・検証（`registry.py`）― §8 が正

```python
def parse_registry(rows: Sequence[Sequence[str]]) -> Registry: ...
    # ヘッダ行（列構成）が §8.1 と一致しない場合は RegistrySchemaError（収集全体を止める側）。
def validate_registry(reg: Registry) -> list[RegistryIssue]: ...
    # RegistryIssue = (source_id: str | None, kind: IssueKind, detail: str)
    # IssueKind: schema（全体停止）／source_blocked（当該情報源のみフェイルクローズ）／
    #            major_change（第2段階以降のドライラン用。第1段階では検出のみ）
def is_collectable(source: SourceEntry) -> bool: ...
    # 人手列「対象化の最終判断」が 対象 系の値であり、かつ自動列の検証をすべて通過した場合のみ True。
    # 値が閉じた集合の範囲外・空欄の行は False（フェイルクローズ）。
def openwork_gate(reg: Registry) -> bool: ...
    # OpenWork 行が存在し、自動取得可否 == 可（または条件付き可の範囲内）かつ最終判断が対象の場合のみ True。
    # 行が無い・対象外・値不正の場合は False（点数付与のみ停止し、収集は継続する）。
```

### 3.11 Sheets 入出力（`sheets_gateway.py`）

```python
class LinkCell(NamedTuple):
    display_text: str
    url: str

class SheetsGateway(Protocol):
    def read_tab(self, tab_name: str) -> list[list[str]]: ...
    def read_candidates(self) -> list[CandidateRecord]: ...
        # URL 列は (表示テキスト, 実 URL) の両方を読み戻す。実 URL が取れない行は検証エラーとして返す。
    def upsert_candidates(self, records: Sequence[CandidateRecord]) -> None: ...
        # 書き込むのは §10.1 の自動列のみ。人手列のセル範囲は書き込み対象に含めない
        # （列名のホワイトリスト AUTO_COLUMNS_CANDIDATE で実装し、テストで検証可能にする）。
    def append_exclusions(self, rows: Sequence[ExclusionRow]) -> None: ...
    def read_registry_rows(self) -> list[list[str]]: ...

AUTO_COLUMNS_CANDIDATE: tuple[str, ...]   # 自動列の列名一覧（定数として公開し、テスト対象とする）
MANUAL_COLUMNS_CANDIDATE: tuple[str, ...] # 人手列の列名一覧（書込禁止の検証用に公開する）
```

リンク形式: URL 列のセルは表示テキスト付きリンクとする。Google Sheets 上では
`HYPERLINK("実URL", "表示テキスト")` 相当のリンク付きセルとして記録し、読み戻しでは
表示テキストと実 URL の対応を検証する。生 URL をセル文字列として書いてはならない。

### 3.12 提示・控え（`presentation.py`）

```python
def split_presentation(candidates: Sequence[CandidateRecord], limit: int
                       ) -> tuple[list[CandidateRecord], list[CandidateRecord]]: ...
    # 対象は fitness in (高, 中) かつ status != 掲載終了 かつ本人の判断 != 対象外 の未提示候補。
    # FitnessResult.presentation_key の順で上位 limit 件を「提示」、残りを「控え」とする。
    # 収集・本体リストへの記録には上限を適用しない（本関数は提示の振り分けのみ）。
def recheck_held(candidate: CandidateRecord, latest: RawPosting | None,
                 refitness: Fitness) -> HeldDecision: ...
    # 繰り上げ直前の再確認: 掲載継続／再判定でも 高・中／本人の判断が対象外でない、の 3 点。
    # HeldDecision: promote（繰り上げ可）／keep_held（控えのまま）／closed（掲載終了）／to_check（要確認へ）
```

### 3.13 ドライラン差分・大量変更ガード（`diff_guard.py`）

```python
def compute_diff(old: Sequence[CandidateRecord], new: Sequence[CandidateRecord],
                 old_registry: Registry | None, new_registry: Registry | None) -> Diff: ...
    # Diff は件数と対象 ID の集合のみを持つ（会社名等の詳細を持たない）:
    # added / updated / closed / presented_planned / held_new / held_promoted / held_remaining /
    # held_closed / check_only_count / registry_changes（情報源の増減を含む）
def mass_change_guard(diff: Diff, existing_count: int, cfg: GuardConfig) -> GuardResult: ...
    # GuardResult: ok（本実行可）／stop（本実行を止め要確認に倒す）
    # stop 条件（閾値は設定 `guard.*`、初期値は §4）:
    #  - 掲載終了件数が既存件数に対する比率 `guard.max_close_ratio` を超える
    #  - 変更合計（追加+更新+終了）が `guard.max_total_change_ratio` を超える
    #  - レジストリ重大変更（情報源の追加／自動応募可否の変更／送信確定方法の変更）が含まれる
    #    場合は、第3段階相当の自動応募を止める対象として必ず stop ではなく
    #    「自動応募のみ停止フラグ」を Diff 側に立てる（第1段階では記録のみ）。
```

### 3.14 OpenWork 付与（`openwork.py`）

```python
class OpenworkProvider(Protocol):
    def search_company(self, company_norm: str) -> list[OpenworkCompanyRef]: ...
        # 外部へ送るのは正規化した会社名のみ。

def attach_openwork(candidate: CandidateRecord, provider: OpenworkProvider,
                    gate_enabled: bool, cfg: OpenworkConfig) -> OpenworkResult: ...
    # OpenworkResult = (score: float | None, url: LinkCell | None, fetched_on: date | None,
    #                   rule_ids: list[str])
    # - gate_enabled が False の場合は provider を一切呼ばず、3 項目すべて None を返す。
    # - 突合: 名称一致に加え、勤務地・業種等の公開情報が一致した場合のみ付与する。
    #   候補が複数残る・情報が食い違う場合は付けない（OW-NOMATCH）。
    #   OpenWork に会社が存在しない場合は OW-NOTFOUND、取得エラーの場合は OW-UNAVAILABLE を
    #   要確認理由側のルール ID として返し、3 項目は None（空欄）とする。適合度は変えない。
    # - 取得は正規化会社名単位で週 1 回まで（呼び出し側でキャッシュする前提を docstring に明記）。
    # - 403／429・制限・警告を受けた場合は例外 `OpenworkBlocked` を送出し、pipeline は
    #   以後の付与を全件停止する（求人の収集・判定は継続）。
```

### 3.15 パイプライン（`pipeline.py`）

```python
def run_collection(cfg: AppConfig, gateway: SheetsGateway,
                   adapters: Mapping[str, SourceAdapter],
                   openwork_provider: OpenworkProvider | None,
                   dry_run: bool) -> RunSummary: ...
    # RunSummary は件数と候補 ID・情報源名のみを持つ（会社名・年収等の詳細を含めない）。
    # 段ごとの失敗時の扱いは §12 に従う。dry_run=True の場合はシートへ書き込まない。
```

## 4. 設定ファイルの形式と全キー一覧

形式は YAML とする。3 ファイル構成とし、実値は `config/local/`（gitignore 対象）に置く。
リポジトリに置くのは `config/templates/*.example.yaml`（伏せ字テンプレート）のみ。

### 4.1 `profile.local.yaml`（志向プロファイル。実値はローカルのみ）

| キー | 型 | 備考（初期値の一般化表現） |
|---|---|---|
| `profile_version` | int | スキーマ版数。初期値 1 |
| `last_updated` | date | プロファイル最終更新日。週次通知に添える材料 |
| `target_domains` | list[str] | 志向領域のキーワード群（データ／AI 領域を表す一般化キーワード） |
| `target_role_labels` | list[str] | 対象職種の肩書き例（PM／マネジメント／企画・推進／ディレクター／アーキテクト等）。判定は肩書きではなく主務で行うため、本キーは補助 |
| `salary_target_jpy` | int | 年収目安（円／年） |
| `employment_allowed` | list[str] | 許可する雇用形態。初期値は正社員のみ |
| `remote_preference` | str \| null | リモート希望頻度。初期値は未指定（null）。指定された場合のみ適合条件化する |
| `location_condition_enabled` | bool | 勤務地条件の適用可否。初期値 true |
| `location_hard_exclude` | bool | 勤務地条件のハード除外化。初期値 false（本人の指定があった場合のみ true） |
| `search_queries` | list[str] | 収集に使う一般化キーワードの組み合わせ。個人情報・現職の具体名を含めない。実行前に禁止ワードスキャンを通す |

### 4.2 `judge.local.yaml`（判定設定。構造はテンプレートと同一、値の一部はローカル保持）

| キー | 型 | 備考 |
|---|---|---|
| `normalize.tracking_params` | list[str] | URL 正規化で除去するパラメータ名（utm 系・ref 等） |
| `duty.target_sections` | list[str] | 主務判定の対象セクション名（業務内容／担当業務 等） |
| `duty.upstream_keywords` | list[str] | 上流主務キーワード群。「設計」は単独語を登録せず文脈付き表現のみ（システム全体の設計／アーキテクチャ設計／全体設計／設計方針の策定／技術選定／標準化 等） |
| `duty.impl_keywords` | list[str] | 実装主務キーワード群（要件定義後の設計／詳細設計／実装／開発／テスト／運用保守 等を表す表現） |
| `duty.title_assist_keywords` | list[str] | 職種名の補助キーワード。単独ではハード除外を発火させない |
| `duty.primary_markers` | list[str] | 主担当の明示を表す表現（主担当／メイン業務 等） |
| `duty.mixed_handling` | str | 主務混在時の扱い。初期値 `check`（要確認）。他の値は `ConfigError` |
| `location.target_sections` | list[str] | 勤務地判定の対象セクション名（勤務地／勤務先／就業場所／アクセス 等） |
| `location.whitelist_file` | path | 市区町村対応表へのパス（ローカル保持。§13） |
| `remote.rules` | list[{pattern, division}] | リモート原文 → 出社日数区分への変換規則（正規表現と区分の組。順序評価） |
| `english.target_sections` | list[str] | 英語判定の対象セクション名（応募資格／必須スキル／求める人物像／業務内容 等） |
| `english.mandatory_sections` | list[str] | 必須系セクション名。見出しに「必須」を含むセクションも必須系として扱う |
| `english.required_phrases` | list[str] | 英語必須明記表現群 |
| `english.welcome_phrases` | list[str] | 英語歓迎表現群（歓迎／尚可／活かせる／読み書き程度 等を表す表現） |
| `english.level_phrases` | list[str] | レベル表現群（ビジネスレベル／日常会話レベル 等） |
| `hard.non_regular_keywords` | list[str] | 正社員でない雇用形態を表す表現（業務委託／派遣／契約社員 等） |
| `hard.out_of_domain_keywords` | list[str] | 領域外を表す表現 |
| `hard.business_form_keywords` | list[str] | SIer・コンサル寄りの事業形態を表す表現（判定は公開情報に基づく） |
| `fitness.salary_check_threshold_jpy` | int | 年収上限がこの値を下回る場合は要確認（SAL-BELOW）。目安に対する「大幅に下回る」の閾値 |
| `fitness.high_threshold` | int | 機械的一致度（§6.6）の「高」境界 |
| `fitness.domain_match_points` / `fitness.salary_points` / `fitness.condition_points` / `fitness.location_points` | int | 機械的一致度の配点（§6.6） |
| `presentation.weekly_limit` | int | 提示上限 N。**初期値 20** |
| `validity.listing_valid_days` | int | 掲載確認の有効日数 |
| `collection.request_interval_sec` | int | 情報源へのアクセス間隔（人の通常利用と同程度の低頻度） |
| `collection.per_source_max_items` | int | 1 情報源・1 回あたりの取得上限（大量巡回防止） |
| `collection.timeout_sec` | int | 取得タイムアウト。超過時はその情報源をスキップ（リトライしない） |
| `guard.max_close_ratio` | float | 大量変更ガード: 掲載終了比率の上限。初期値 0.5 |
| `guard.max_total_change_ratio` | float | 大量変更ガード: 変更合計比率の上限。初期値 1.0 |
| `openwork.weekly_once_per_company` | bool | 初期値 true（同一会社は週 1 回まで） |
| `auto_apply.enabled` | bool | 自動応募の有効化フラグ。**既定値 false**。第1段階では常に false（スキーマのみ確定）。変更は本人の明示指示によってのみ行う |
| `auto_apply.weekly_limit` | int | 自動応募の週上限。初期値 3（第3段階用にスキーマのみ確定） |
| `auto_apply.result_unknown_hold_runs` | int | 結果不明時の応募前控え保持上限（次々回実行時まで = 2。第3段階用） |

### 4.3 `local.local.yaml`（接続・実値系。テンプレートは伏せ字のみ）

| キー | 型 | 備考 |
|---|---|---|
| `spreadsheet_id` | str | 応募候補リスト Spreadsheet の ID（実値はローカルのみ） |
| `sheet_names.candidates` / `sheet_names.registry` / `sheet_names.exclusions` | str | タブ名。初期値: `候補リスト`／`情報源レジストリ`／`除外ログ` |
| `banned_words_file` | path | 禁止ワードリストへのパス（ローカル保持・gitignore 対象） |
| `ats_boards.greenhouse` / `ats_boards.lever` / `ats_boards.ashby` / `ats_boards.smartrecruiters` | list[str] | ATS 公開 API のボード識別子（企業ボード）一覧。発見手順は §12。企業名そのものは求人の公開情報だが、収集対象企業の選定経緯等の機微は含めない |
| `profile_file` / `judge_file` | path | §4.1／§4.2 への参照 |

設定変更の反映: 設定値を 1 つ変更して再実行すると、コードを変更せずに次回の判定結果へ反映される
構造とする（完了の定義①）。プロファイルは本人の指示以外では書き換えない。

## 5. ルール ID 一覧（確定）

理由文には発火したルール ID を必ず併記する。ハード除外は除外ログに、要確認は候補リスト本体の
要確認理由に、それぞれ ID を残す。ID の文字列は本表で確定し、以後は変更しない（変更は新 ID の追加で対応する）。

### 5.1 主務

| ID | 区分 | 発火条件 | 結果 |
|---|---|---|---|
| `JOB-IMPL-HARD` | 主務が実装等であることによるハード除外 | 主務判定が impl | 除外ログ行き |
| `JOB-UNKNOWN` | 主務不明による要確認 | 主務が読み取れない／要件定義のみ 等 | 適合度「要確認」、週次提示から外す |
| `JOB-MIXED` | 主務混在による要確認 | 上流・実装の両群がヒットし主担当明示なし | 適合度「要確認」、週次提示から外す |
| `JOB-DESIGN-ONLY` | 設計のみで判別不可（JOB-UNKNOWN の内訳） | 主務が「設計」のみと読める | JOB-UNKNOWN と併記し要確認 |
| `JOB-UPSTREAM-OK` | 上流主務の確認（肯定側） | 主務判定が upstream | 高／中の付与資格を得る |

### 5.2 勤務地・リモート

| ID | 区分 | 発火条件 | 結果 |
|---|---|---|---|
| `LOC-OK` | 勤務地適合 | 勤務地が対応表の範囲内 | 適合度を下げない |
| `LOC-OUT` | 勤務地不適合 | 勤務地が対応表の範囲外であることが明確 | 適合度「要確認」（下げる扱いはしない） |
| `LOC-UNKNOWN` | 勤務地判定不能 | 勤務地が読み取れない／主たる勤務地が特定できない／転勤あり 等 | 適合度「要確認」 |
| `LOC-FULLREMOTE-EXEMPT` | フルリモート対象外 | 出社が一切発生しない | 勤務地条件を適用しない |
| `REMOTE-UNKNOWN` | リモート頻度不明 | 頻度の記載がない・読み取れない | リモート頻度列は「不明」、適合度「要確認」に固定。勤務地も判定不能として扱う |
| `REMOTE-NORMALIZED` | リモート頻度正規化済み（記録側） | 原文を出社日数区分へ正規化できた | 原文表記を理由欄に残す |

### 5.3 英語

| ID | 区分 | 発火条件 | 結果 |
|---|---|---|---|
| `ENG-HARD` | 英語必須によるハード除外 | 対象セクション内に必須明記、または必須系セクション内にレベル表現のみ | 除外ログ行き |
| `ENG-UNKNOWN` | 英語要件不明による要確認 | 言及はあるが必須性が読み取れない／数値が単独出現 | 適合度「要確認」、週次提示から外す |
| `ENG-OUT-SECTION` | 対象セクション外のみ必須明記（ENG-UNKNOWN の内訳） | 必須明記が対象セクション外にのみある | ENG-UNKNOWN と併記し要確認（ハード除外しない） |
| `ENG-WELCOME-OK` | 英語は必須でない（肯定側） | 歓迎表現等に結びつく記載のみ | 英語を理由に適合度を下げない |

### 5.4 その他ハード除外・要確認

| ID | 区分 | 発火条件 | 結果 |
|---|---|---|---|
| `HARD-EMPTYPE` | 雇用形態によるハード除外 | 正社員でないことが明確 | 除外ログ行き |
| `HARD-DOMAIN` | 領域外によるハード除外 | データ／AI 領域から明らかに外れる | 除外ログ行き |
| `HARD-BIZFORM` | 事業形態によるハード除外 | SIer・コンサル寄りと公開情報から明確に判断できる | 除外ログ行き |
| `BIZFORM-UNKNOWN` | 事業形態が判断できない | 曖昧で断定できない | 適合度「要確認」 |
| `SAL-BELOW` | 年収要確認 | 年収上限が閾値を大幅に下回る | 適合度「要確認」以下に倒す |
| `SAL-UNKNOWN` | 年収不明 | 公開情報に年収の記載がない | 理由に残す（単独では適合度を決めない） |
| `SAL-OK` | 年収目安圏内（肯定側） | 年収レンジが目安と大きく乖離しない | 機械的一致度の材料 |

### 5.5 OpenWork・レジストリ・運用

| ID | 区分 | 発火条件 | 結果 |
|---|---|---|---|
| `OW-NOMATCH` | OpenWork 突合不能 | 同名の別会社・複数候補・勤務地/業種の食い違いで同一企業と確認できない | 点数 3 列は空欄。適合度は変えない |
| `OW-NOTFOUND` | OpenWork に存在しない | 検索で該当会社が見つからない | 点数 3 列は空欄 |
| `OW-UNAVAILABLE` | OpenWork 取得不能 | 取得エラー等（403／429 の場合は付与自体を即時停止し `OW-BLOCKED` を併記） | 点数 3 列は空欄。再取得失敗時は前回値を保持する規則（§6.7）を優先 |
| `OW-BLOCKED` | OpenWork 付与停止 | 403／429・アクセス制限・警告 | レジストリの OpenWork 行を対象外へ戻す材料とし、以後の付与を停止 |
| `OW-DISABLED` | OpenWork ゲート無効 | レジストリで自動取得可と確定していない | 取得を試行せず全件空欄 |
| `SRC-BLOCKED` | 情報源フェイルクローズ | レジストリの値検証に失敗した情報源 | 当該情報源の収集・自動応募を行わない |
| `SRC-REGISTRY-MAJOR` | レジストリ重大変更 | 情報源の追加／自動応募可否の変更／送信確定方法の変更を検出 | その回の自動応募を停止（第1段階では記録のみ） |

## 6. 判定仕様の詳細（企画書の手順の設計への写像）

### 6.1 主務判定
§3.5 のとおり。補足: アーキテクト職にも専用経路を作らず同一の主務判定を適用する。
要件定義・設計はいずれも単独では断定しない（組み合わせで判定し、単独のみは unknown）。

### 6.2 提示対象と週次提示からの除外
適合度「要確認」の候補は候補リスト本体に保持するが、週次提示（高／中のみ）の対象に含めず、
N の枠にも数えない。要確認の件数は理由区分（主務／英語／勤務地・リモート／その他）ごとに
件数のみ数えられる形にする（`FitnessResult` の理由にルール ID が必須であることが担保になる）。

### 6.3 リモート頻度区分（閉じた集合）
`フルリモート（出社なし）`／`出社週1日以下`／`出社週2〜3日`／`出社週4日以上`／
`出社必須（フル出社）`／`不明`。この集合以外の値で記録してはならない（型 `RemoteDivision` で担保）。
現段階ではリモート頻度はハード除外に使わず、提示項目・要確認の材料とする。
将来、希望頻度が指定された場合は `profile.remote_preference` と設定規則の追加のみで適合条件化する。

### 6.4 勤務地判定
§3.6 のとおり。勤務地条件は第1段階ではハード除外にせず適合条件として扱う
（`profile.location_hard_exclude` で切替可能にしておく）。

### 6.5 英語必須判定
§3.7 のとおり（必須優先、必須系セクションのレベル表現、数値スコアの結びつき、
対象セクション外の扱いを優先順位つきで固定した）。

### 6.6 適合度の機械的一致度（提示順の内訳）
`高` の境界判定と提示順に使う機械的一致度は、次の配点の合計とする（配点は設定 `fitness.*_points`）。
評価の内訳は適合理由にルール ID とともに残っているものを使う。

| 観点 | 評価 |
|---|---|
| 年収目安との距離 | レンジが目安を含む／近いほど高得点。不明は 0 点とし SAL-UNKNOWN を残す |
| 領域との一致度 | 対象領域キーワードの一致数に応じて配点 |
| 勤務条件の確認可否 | リモート頻度・勤務条件が読み取れた場合に配点 |
| 勤務地条件への適合 | LOC-OK の場合に配点。LOC-FULLREMOTE-EXEMPT は減点しない |

提示順: 適合度（高→中）→ 機械的一致度の高い順 → 掲載確認日が新しい順 → 候補 ID 順。
新規候補と控えは区別なく合流させて並べる（新規を常に優先しない）。

### 6.7 OpenWork 付与
- 位置づけは補助情報のみ。点数の有無・高低で適合度を変えない。
- 付与は §3.14 のゲートが有効な場合のみ。第1段階の確定は §9.3 のとおり **ゲート無効（付与しない）**
  で確定するため、第1段階では全候補の OpenWork 関連 3 列は空欄が正常状態である。
- 再取得規則（第2段階以降に効く設計として確定）: 再取得成功時は最新値に更新する（状態・差分には
  含めない）。一度取得できた候補で再取得に失敗した場合は前回の点数・URL を保持し取得日は前回のまま。
- 突合は正規化会社名＋勤務地・業種等の公開情報の一致が取れた場合のみ。外部へ送るのは会社名のみ。

## 7. 重複管理・候補 ID の安定化

- 候補 ID は §3.2 `candidate_id()` で生成し、初回登録時に確定して以後変更しない。
- 突合の順序: (1) 候補 ID（正規化した会社名＋ポジション名＋実 URL）で既存行と突合する。
  (2) 一致しない場合は `fallback_match_key`（正規化した会社名＋ポジション名）で突合し、
  一致すれば既存行を正とし、候補 ID は初回登録時の値を維持する。その際、情報源 URL のリンク先のみを
  新しい実 URL へ更新し、表示テキストは「求人ページ」のまま維持する。応募希望マーク・応募状態は
  既存行に引き継ぐ。
- 重複判定の対象には提示済みだけでなく控えの候補も含める。同一候補が複数行になる状態を
  「重複行」と定義し、初回リストでは重複行 0 件を完了条件とする。
- 同じ会社の別ポジションは別候補としてよい。

## 8. 情報源レジストリのスキーマ定義

### 8.1 管理場所と列構成

- 管理場所: 応募候補リスト Spreadsheet の「情報源レジストリ」タブを**正（マスタ）**とする。
  実装側の設定ファイルにレジストリの判定値を置かない（二重管理をしない）。
- 実装は毎回の収集処理の開始時に当該タブを読み取って判定に使う。
- 列構成（左から順に固定する）:

| # | 列名 | 区分 | 内容 |
|---|---|---|---|
| 0 | 情報源名 | 行キー列 | 情報源を一意に識別する名称。自動列 9 項目には含まれない識別列として本設計で追加する（行の同定と重複検出に必須のため） |
| 1 | URLパターン | 自動列 | 対象 URL のパターン（例: `https://<ドメイン>/*`、ATS の場合は API エンドポイントのパターン） |
| 2 | ログイン要否 | 自動列 | §8.2 の閉じた値 |
| 3 | 資格情報の有無 | 自動列 | Secure Vault 保存済みか否かの区分のみ。実値は書かない |
| 4 | 実測結果 | 自動列 | §8.3 の構造化文字列（アクセス可否・取得方法・確認日） |
| 5 | 自動取得可否 | 自動列 | §8.2 の閉じた値 |
| 6 | リスク評価結果と判定根拠 | 自動列 | リスク区分＋根拠（確認した規約・ページ・日付）を 1 セルに構造化して記録（§8.3） |
| 7 | 費用条件確認 | 自動列 | §8.2 の閉じた値 |
| 8 | 自動応募可否 | 自動列 | `可`／`不可` のみ |
| 9 | 送信確定方法 | 自動列 | §8.2 の閉じた値 |
| 10 | 対象化の最終判断 | **人手列** | §8.2 の閉じた値。自動更新は書き込まない |
| 11 | 備考 | **人手列** | 自由記述。自動更新は書き込まない |

### 8.2 閉じた値集合（この集合以外の値は値検証で不正とする）

| 列 | 許容値 |
|---|---|
| ログイン要否 | `不要`／`必須` |
| 資格情報の有無 | `Secure Vault保存済み`／`未保存`／`不要（ログイン不要のため）` |
| 自動取得可否 | `可`／`条件付き可（手動・低頻度の範囲のみ）`／`不可`／`未確認`（未確認は収集判断では「不可」と同等に扱う） |
| リスク評価（実測結果等の構造化要素ではなく本列の先頭区分） | `低`／`中`／`高`／`未評価`（未評価は対象化判断では「高」と同等に扱う） |
| 費用条件確認 | `確認済み（無料または既存契約範囲内）`／`有料条件あり`／`未確認`（未確認は対象化判断では対象外とする） |
| 自動応募可否 | `可`／`不可` |
| 送信確定方法 | `完了画面の文言`／`確認メールの到着`／`マイページの応募履歴表示`／`対象外（企業評価の補助情報源）`／`対象外（自動応募は第3段階で確定）` |
| 対象化の最終判断（人手列） | `対象`／`対象（手動収集のみ）`／`対象外`／`対象外（実測待ち）` |

送信確定方法の値に関する確定事項:

- `対象外（企業評価の補助情報源）` は OpenWork 行専用の正式な許容値である（空欄による
  フェイルクローズに触れないための扱い）。
- `対象外（自動応募は第3段階で確定）` を、第1段階の求人情報源（全て自動応募可否＝不可）用の
  正式な許容値として本設計で登録する。第3段階で自動応募可否を `可` に変更する際は、本列を
  確定方法 3 値のいずれかに置き換えることを必須とし、その変更はレジストリ重大変更として扱う。

### 8.3 実測結果・リスク評価のセル内構造

セルは文字列とし、次のキー=値形式で統一する（パーサは `registry.py` に置き、形式不正は値検証エラーとする）。

- 実測結果: `結果=<成功|失敗|未実施|対象外（ログイン不要）>; 取得方法=<公開ページ取得|公式公開API|Secure Vault経由（予定含む）>; 確認日=<YYYY-MM-DD>`
  - ログイン必須の情報源でのみ「成功／失敗」が意味を持つ。ログイン不要の情報源は
    `結果=対象外（ログイン不要）` とし、到達確認を行った場合は確認日にその日付を入れる。
- リスク評価結果と判定根拠: `リスク=<低|中|高|未評価>; 根拠=<確認した規約名・条項・ページの要旨>; 確認日=<YYYY-MM-DD>`

### 8.4 値検証とフェイルクローズの条件

全体停止（その回の収集・週次更新・自動応募をすべて行わず要確認に倒す）:

1. レジストリタブが読み取れない、またはタブ名・列構成（§8.1）がスキーマと一致しない。
2. ヘッダ行に欠落・重複・順序違いがある。

当該情報源のみフェイルクローズ（`SRC-BLOCKED`。当該情報源の収集・自動応募を止め、他は継続する）:

3. 閉じた値の列に、許容値以外の値（空欄・表記ゆれ・誤記を含む）が入っている。
4. URLパターンが重複している行がある（重複行はすべて対象外とする）。
5. 自動応募可否＝`可` なのに送信確定方法が確定方法 3 値のいずれでもない。
6. ログイン要否＝`必須` で対象化されているのに、資格情報の有無が `Secure Vault保存済み` でない、
   または実測結果が `結果=成功` でない（第1段階では §8.5 の行がこれに該当し、全て対象外である）。
7. 費用条件確認が `確認済み（無料または既存契約範囲内）` でない情報源が対象化されている。
8. 自動取得可否が `不可`／`未確認`、またはリスクが `高`／`未評価` の情報源が対象化されている
   （条件付き可は「対象（手動収集のみ）」の場合に限る）。

OpenWork 行の扱い: OpenWork 行が上記の検証に失敗しても全体停止にはせず、点数付与のみを停止する
（`openwork_gate()` が False を返す）。求人の収集・適合判定は継続する。

### 8.5 ログイン必須の情報源候補（Secure Vault 保存済み 8 件）の扱い ― 設計判断と根拠

対象ドメイン（URLパターンとして記録してよい情報のみ。資格情報の実値は含めない）:
`bizreach.jp`／`en-gage.net`／`offers.jp`／`paiza.jp`／`menta.work`／`mid-works.com`／
`freelance-hub.jp`／`visasq.com` の 8 件。

**判断: レジストリの対象範囲外とはせず、「分類済み・対象外（実測待ち）」の行としてレジストリに含める。**

根拠:

- 完了の定義②は、レジストリの各情報源についてログイン要否・資格情報の有無等の項目が
  「未記入 0 件」で一覧化されていることを求め、⑦(i) は全情報源についてログイン要否と
  資格情報の有無が分類され「未分類 0 件」であることを求める。既知の候補をレジストリの外に置くと、
  分類漏れなのか未検討なのかを後から区別できず、この条件と矛盾する。
- 一方で企画書は、実測確認が未完了のログイン必須サイトについて「レジストリ確定の対象に含めない
  （対象外として確定させることは可）」と定めている。すなわち「行として存在し、対象外として確定する」
  形は企画書の許容範囲内であり、第1段階ゲート（ログイン必須サイトの対象化が 0 件の場合は
  分類とリスク評価の完了をもって通過）とも矛盾しない。
- 実測（保存済み資格情報での実際のアクセス可否確認）はブラウザ作業を伴い、開発部だけでは完結しない。
  そのため本段階では実測を行わず、**本人確認待ちチェックポイント**として分離する（§15）。

各行の下書き値（8 件共通の型）:

| 列 | 値 |
|---|---|
| URLパターン | `https://<各ドメイン>/*` |
| ログイン要否 | `必須` |
| 資格情報の有無 | `Secure Vault保存済み` |
| 実測結果 | `結果=未実施; 取得方法=Secure Vault経由; 確認日=2026-11-05`（確認日は分類を行った日。実測日ではないことを備考で明示する） |
| 自動取得可否 | `未確認`（収集判断では不可と同等） |
| リスク評価結果と判定根拠 | `リスク=未評価; 根拠=利用規約・自動取得可否の確認および実測が未実施のため対象外として確定; 確認日=2026-11-05` |
| 費用条件確認 | `未確認`（対象化判断では対象外） |
| 自動応募可否 | `不可` |
| 送信確定方法 | `対象外（自動応募は第3段階で確定）` |
| 対象化の最終判断（人手列の初期値案） | `対象外（実測待ち）` |
| 備考（人手列の初期値案） | 実測と規約確認は本人確認待ちチェックポイント。うち業務委託・フリーランス系の働き方を主とする情報源は、プロファイル（正社員のみ）との関係でも対象化の優先度が低い旨を併記する |

解釈上の注意（本人確認事項）: 完了の定義⑦(ii) はログイン必須サイトごとに実測確認欄の記録を求める。
本設計では「未実施」という状態値そのものを記録とし、未記録（空欄）0 件は満たすが、
「実測の実施自体がゲート条件」と厳密に読む場合は、8 件の実測完了まで第1段階ゲートは
未通過という解釈もありうる。この解釈の確定は §15 のチェックポイントとして本人の確認に委ねる。

## 9. 情報源の実地調査（2026-11-05）とレジストリ行の下書き

### 9.1 調査方法

- 各候補について、求人検索ページ／公式ドキュメントページと利用規約ページを実際に取得して読み、
  到達可否／ログイン要否／利用規約上の自動取得可否／費用条件を確認した。
- 取得できない候補、規約で判断できない候補は対象外とする（フェイルクローズ）。
- 取得は読み取りのみ・低頻度で行い、ログイン・登録・応募等の操作は行っていない。

### 9.2 調査結果表

| 候補 | 到達可否 | ログイン要否（求人閲覧） | 利用規約上の自動取得可否 | 費用条件 | 選定 |
|---|---|---|---|---|---|
| Green（転職サイト） | 可（求人検索ページをログインなしで取得でき、求人一覧が表示された） | 不要（検索・一覧の閲覧） | 規約全文に自動取得・スクレイピングの明示的な禁止条項は見当たらなかった。一方で「入手した情報を私的利用の範囲を超えて使用する行為」の禁止があり、本利用は本人の求職という私的利用の範囲に限定される。自動取得の可否を規約が明示していない点は残るため、**手動・低頻度のみの条件付き可**とし、cron 化（第2段階）の前に再確認を必須とする | 無料で閲覧可（確認済み） | **選定（条件付き）**: 対象（手動収集のみ） |
| Greenhouse Job Board API（ATS 公式公開 API） | 可（公式ドキュメントを取得。GET の一覧・詳細エンドポイントが定義されている） | 不要（公式ドキュメント上、認証が必要なのは応募送信エンドポイントのみで、求人取得の GET は認証不要） | 公式に公開された Job Board API であり、企業の公開求人を JSON で取得する用途がドキュメント自体に明記されている。自動取得可 | 無料（API キー不要の範囲） | **選定**: 対象 |
| Lever Postings API（ATS 公式公開 API） | 可（公式ドキュメント（公開リポジトリ）を取得） | 不要（公開状態の求人は認証なしで取得可能と明記） | 公式ドキュメントに「公開状態の求人は公開されており、第三者に取得されうる」旨が明記されている。自動取得可 | 無料 | **選定**: 対象 |
| Ashby Job Postings API（ATS 公式公開 API） | 可（公式開発者ドキュメントを取得。公開求人を取得する API として定義されている） | 不要（公開 API としてドキュメント化されている） | 自社採用ページ等に公開求人を載せるための公式公開 API であり、自動取得可。レート等の明示がないため低頻度運用を前提とする | 無料 | **選定**: 対象 |
| SmartRecruiters Posting API（ATS 公式公開 API） | 可（公式ドキュメントに基づく公開 API の存在と、認証なしで公開求人を取得できることを確認） | 不要（公開求人に限る。内部求人・顧客 API は認証が必要で対象に含めない） | 公式の Posting API として公開求人の取得が定義されている。自動取得可。ただし企業ごとに公開フィードの有効化が異なるため、ボードごとに到達確認を行い、到達できないボードはその回スキップする | 無料（公開求人の範囲） | **選定**: 対象（ボード単位の到達確認つき） |
| Wantedly | 可（規約・サービスページに到達） | 募集の閲覧自体は一部ログイン不要 | **不可**: 利用規約の禁止事項で、事前の承諾なく自動化されたシステム（ボット、スクレイパー、スパイダー、AI、エージェント型 AI を含む）によるクロール、スクレイピング、データマイニング等を明示的に禁止している（robots.txt の範囲のみ条件付き許可） | 求職者側は無料 | **対象外**（規約明示禁止。高リスク） |
| Indeed | 可（規約ページに到達） | 求人検索の閲覧はログイン不要 | **不可**: 利用規約で、明示的な書面による許可なく自動化システム（ボット、スクレイパー、スパイダー、AI、エージェント型 AI を含む）によるアクセス、データマイニング、クローリング、スクレイピング、データ抽出等を明示的に禁止している | 求職者側は無料 | **対象外**（規約明示禁止。高リスク） |
| 企業公開採用ページ（個別企業・ATS 非経由の自社ページ） | 企業ごとに異なる | 企業ごとに異なる | 企業ごとに利用条件が異なり一律に判断できないため、**媒体としての一括対象化はしない**。個別企業のページを対象化する場合は、企業ごとにレジストリへ 1 行追加し、当該企業の利用条件・到達可否を確認してから対象化する（行がない企業の自社ページは収集しない）。ATS 経由の公開求人は上記 4 API の行でカバーする | 企業ごとに確認 | **対象外（一括扱いとしては）**。個別追加の手順のみ設計する |
| OpenWork（企業評価の補助情報源） | 可（利用規約ページを取得） | 点数等の閲覧範囲は登録なしで到達できる範囲がある一方、全情報の閲覧には登録・有料会員制度がある | **不可**: 利用規約（ユーザーの禁止事項）で「本サービスへ断続的な機械的なアクセス行為、コンテンツを一括ダウンロードする行為又はそれに類する行為」を明示的に禁止している。あわせて、入手した情報の複製・転載等の利用も制限されている | 無料範囲と有料会員（月額制）が併存。有料範囲は使わない | **点数付与は行わない**（§9.3）。求人の収集元ではないため収集自体への影響なし |

補足: 初回の対象情報源は「選定」した 5 件（Green 条件付き＋ATS 公式公開 API 4 件）であり、
企画書の「初回の対象情報源は 3〜5 件に絞る」の範囲内である。ATS 型の情報源は企業ボード単位で
求人を取得するため、対象企業のボード識別子の発見手順を §12 に定める。国内の求人媒体の多くが
規約で自動取得を明示的に禁止しているため、第1段階の収集量は ATS 経由と Green（手動）に
依存する構造になる。この制約は初回リストの件数見通しに影響するため、§15 にも転記する。

### 9.3 OpenWork の判定（確定）

- 判定: **第1段階では OpenWork の点数付与を行わない**（全候補で OpenWork 関連 3 列は空欄が正常）。
- 根拠: 利用規約が機械的なアクセス行為を明示的に禁止しているため、「利用規約等で自動取得が
  制限される場合は点数付与のみを行わない運用とする」という企画書の条件に直接該当する。
  確認できない場合の扱い（付けない）とも整合する。
- レジストリ行は §9.4 のとおり対象外として記録し、`openwork_gate()` は False を返す。
  将来、取得方法の前提が変わる場合は、レジストリ行の再確認と本人の判断を経てのみ変更する。

### 9.4 レジストリ行の下書き（選定分＋OpenWork＋対象外の主要媒体）

シート作成時（Executor）は本表をレジストリタブへ転記する。人手列 2 列は「初期値案」であり、
シート上での記入自体は運用側の記入として扱い、自動プログラムは書き込まない。

| 情報源名 | URLパターン | ログイン要否 | 資格情報の有無 | 実測結果 | 自動取得可否 | リスク評価結果と判定根拠 | 費用条件確認 | 自動応募可否 | 送信確定方法 | 最終判断（人手・初期値案） | 備考（人手・初期値案） |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Green | `https://www.green-japan.com/*` | 不要 | 不要（ログイン不要のため） | `結果=対象外（ログイン不要）; 取得方法=公開ページ取得; 確認日=2026-11-05` | 条件付き可（手動・低頻度の範囲のみ） | `リスク=中; 根拠=利用規約（2026-11-05取得）に自動取得の明示的禁止条項なし。私的利用の範囲を超える使用の禁止条項があるため本人の求職目的の私的利用に限定し、手動・低頻度のみ可とする。cron化前に再確認必須; 確認日=2026-11-05` | 確認済み（無料または既存契約範囲内） | 不可 | 対象外（自動応募は第3段階で確定） | 対象（手動収集のみ） | 第2段階で cron 化する場合は規約再確認が前提 |
| Greenhouse Job Board API | `https://boards-api.greenhouse.io/v1/boards/*` | 不要 | 不要（ログイン不要のため） | `結果=対象外（ログイン不要）; 取得方法=公式公開API; 確認日=2026-11-05` | 可 | `リスク=低; 根拠=公式Job Board APIドキュメント（2026-11-05取得）で求人取得GETの認証不要と公開求人のJSON提供が明記されている; 確認日=2026-11-05` | 確認済み（無料または既存契約範囲内） | 不可 | 対象外（自動応募は第3段階で確定） | 対象 | 応募送信エンドポイントは認証が必要であり第1段階では使わない |
| Lever Postings API | `https://api.lever.co/v0/postings/*` | 不要 | 不要（ログイン不要のため） | `結果=対象外（ログイン不要）; 取得方法=公式公開API; 確認日=2026-11-05` | 可 | `リスク=低; 根拠=公式Postings APIドキュメント（2026-11-05取得）で公開状態の求人は公開され第三者に取得されうる旨が明記されている; 確認日=2026-11-05` | 確認済み（無料または既存契約範囲内） | 不可 | 対象外（自動応募は第3段階で確定） | 対象 | 全文検索は API の対象外のため、取得後にローカルで判定する |
| Ashby Job Postings API | `https://api.ashbyhq.com/posting-api/job-board/*` | 不要 | 不要（ログイン不要のため） | `結果=対象外（ログイン不要）; 取得方法=公式公開API; 確認日=2026-11-05` | 可 | `リスク=低; 根拠=公式開発者ドキュメント（2026-11-05取得）で公開求人を取得する公開APIとして定義されている。レート明示がないため低頻度運用を前提とする; 確認日=2026-11-05` | 確認済み（無料または既存契約範囲内） | 不可 | 対象外（自動応募は第3段階で確定） | 対象 | `isListed=false` の求人は対象に含めない |
| SmartRecruiters Posting API | `https://api.smartrecruiters.com/v1/companies/*/postings*` | 不要 | 不要（ログイン不要のため） | `結果=対象外（ログイン不要）; 取得方法=公式公開API; 確認日=2026-11-05` | 可 | `リスク=低; 根拠=公式Posting API（2026-11-05確認）で公開求人の取得が定義されている。企業ごとに公開フィードの有効化が異なるためボード単位で到達確認する; 確認日=2026-11-05` | 確認済み（無料または既存契約範囲内） | 不可 | 対象外（自動応募は第3段階で確定） | 対象 | 一覧に本文が含まれないため詳細取得は求人単位で行い、取得上限の範囲に収める |
| Wantedly | `https://www.wantedly.com/*` | 不要 | 不要（ログイン不要のため） | `結果=対象外（ログイン不要）; 取得方法=公開ページ取得; 確認日=2026-11-05` | 不可 | `リスク=高; 根拠=利用規約の禁止事項（2026-11-05取得）で自動化システムによるクロール・スクレイピング等を事前承諾なしに明示的に禁止; 確認日=2026-11-05` | 確認済み（無料または既存契約範囲内） | 不可 | 対象外（自動応募は第3段階で確定） | 対象外 | 規約明示禁止のため再評価は規約改定時 |
| Indeed | `https://jp.indeed.com/*` | 不要 | 不要（ログイン不要のため） | `結果=対象外（ログイン不要）; 取得方法=公開ページ取得; 確認日=2026-11-05` | 不可 | `リスク=高; 根拠=利用規約（2026-11-05取得）で書面による明示的許可のない自動化システムによるアクセス・データ抽出等を明示的に禁止; 確認日=2026-11-05` | 確認済み（無料または既存契約範囲内） | 不可 | 対象外（自動応募は第3段階で確定） | 対象外 | 規約明示禁止のため再評価は規約改定時 |
| OpenWork | `https://www.openwork.jp/*` | 不要 | 未保存 | `結果=対象外（ログイン不要）; 取得方法=公開ページ取得; 確認日=2026-11-05` | 不可 | `リスク=高; 根拠=利用規約の禁止事項（2026-11-05取得）で断続的な機械的アクセス行為・コンテンツの一括ダウンロードを明示的に禁止。点数付与のみを行わない運用で確定; 確認日=2026-11-05` | 有料条件あり | 不可 | 対象外（企業評価の補助情報源） | 対象外 | 企業評価の補助情報源。収集元ではない。新規登録は行わない |

ログイン必須 8 件の行の下書きは §8.5 の共通型のとおりとし、情報源名はドメイン名をそのまま使う。
