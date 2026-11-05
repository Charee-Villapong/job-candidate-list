"""例外型の定義（architecture.md §3）。

例外メッセージには個人情報・禁止ワード自体・認証情報を含めない。
件数・キー名・情報源名など、一般化した情報のみを含める。
"""

from __future__ import annotations


class ConfigError(Exception):
    """設定ファイルの欠落・形式不正・値の範囲外。"""


class RegistrySchemaError(Exception):
    """レジストリの列構成がスキーマと一致しない（全体停止側）。"""


class BannedWordHit(Exception):
    """禁止ワードがヒットした。メッセージには件数のみを含める。"""


class SourceFetchError(Exception):
    """情報源からの取得に失敗した（当該情報源のみスキップする）。"""


class GuardStopped(Exception):
    """大量変更ガードにより本実行が停止した。"""


class OpenworkBlocked(Exception):
    """OpenWork 取得が制限された（403／429 等。以後の付与を全件停止する）。"""
