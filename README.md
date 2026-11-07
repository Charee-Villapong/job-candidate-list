# 応募候補リスト作成

AI組織の第1号企画「応募候補リスト作成」の実装リポジトリ。

本人のキャリア志向（志向プロファイル）に照らして、公開求人情報等から応募候補を能動的に収集し、適合判定（ハード除外／適合度付け）を行い、Google Sheets の「応募候補リスト」に記録する仕組み。

- 企画の正本：ai-organization リポジトリの Issue #1（企画書・完了の定義）
- 本リポジトリは第1段階（プロファイル＋初回リスト作成）の実装に加え、第2段階の一部（Sheets 書込経路の修正・勤務地対応表の確定値更新・本番収集の試走1回）を実施済みである。第2段階の週次自動更新の cron 化は未実施（未作成）。第3段階（自動応募）は含まない。

## 構成

- `src/job_candidate_list/` ― 実装パッケージ
  - `config.py` ― 設定（YAML）の読込と検証。判定に効く値の唯一の入口
  - `models.py` ― データ構造定義（`RawPosting`／`CandidateRecord` 等）
  - `normalize.py` ― 会社名・ポジション名・URL の正規化、候補 ID 生成
  - `banned_words.py` ― 禁止ワードスキャン
  - `registry.py` ― 情報源レジストリの読取、スキーマ検証、フェイルクローズ判定
  - `sources/` ― 情報源アダプタ（ATS 公式公開 API 等。共通 I/F は `sources/base.py`）
  - `judge_duty.py`／`judge_location.py`／`judge_remote.py`／`judge_english.py`／`judge_hard.py` ― 各判定（主務／勤務地／リモート頻度／英語必須／その他ハード除外）
  - `fitness.py` ― 適合度付けと提示順スコア算出
  - `openwork.py` ― OpenWork 付与（レジストリゲート付き。第1段階では付与しない）
  - `sheets_gateway.py` ― Google Sheets 入出力（自動列のみ書込）
  - `presentation.py` ― 提示上限・控え管理、繰り上げ再確認
  - `diff_guard.py` ― ドライラン差分、大量変更ガード
  - `evaluate.py` ― 求人 1 件の単体判定入口（サンプル検証と共用）
  - `pipeline.py` ― 手動実行パイプライン、通知文生成
  - `cli.py` ― CLI 入口（`collect`／`dry-run`／`validate-config`／`validate-registry`）
- `config/templates/` ― 設定テンプレート（伏せ字のみを git 管理）
- `config/local/` ― 設定の実値（gitignore 対象。リポジトリには含めない）
- `storage-spec/` ― モックアップ／サンプル配置の正本（配置表と検証用サンプル 39 件）
- `docs/` ― 設計文書（`architecture.md`（旧追補統合済み）／`architecture-addendum.md`（統合済み記録））と引き継ぎ資料
- `scripts/pre_push_scan.py` ― push 前検閲スクリプト
- `tests/` ― 受け入れテスト（pytest）

## 引き継ぎ

`docs/引き継ぎ.md` を参照。
