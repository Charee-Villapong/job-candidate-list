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
