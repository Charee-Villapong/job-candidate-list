"""Green アダプタ（条件付き可・手動収集のみ）。

自動取得は行わない。手動収集の導線用に置き、自動実行では
SourceFetchError としてスキップされる（レジストリのフェイルクローズと整合）。
"""

from __future__ import annotations

from ..errors import SourceFetchError
from ..registry import SourceEntry


class GreenManualAdapter:
    def fetch(self, source: SourceEntry) -> list:
        raise SourceFetchError("手動収集のみの情報源です（自動取得は行いません）")
