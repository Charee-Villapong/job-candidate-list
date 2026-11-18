"""Gemini レビューで実在と検証された不具合の回帰テスト。

対象は ~/workspace/gemini-reviews/VERIFY.md の job-candidate-list 節で
「実在」と確定した指摘。実シートには触らず、フェイク backend で検証する。
"""

from __future__ import annotations

import datetime as dt

from conftest import impl


class _StaticBackend:
    """固定の行を返す最小フェイク backend（読取専用の観測用）。"""

    def __init__(self, rows, links=None):
        self._rows = rows
        self._links = links or {}
        self.writes: list = []
        self.updates: list = []

    def read_rows(self, tab_name):
        return [list(r) for r in self._rows]

    def read_links(self, tab_name):
        return dict(self._links)

    def write_rows(self, tab_name, rows):
        self.writes.append((tab_name, [list(r) for r in rows]))

    def update_rows(self, tab_name, start_row_index, rows):
        self.updates.append((tab_name, start_row_index, [list(r) for r in rows]))


def _seeded_gateway(status_text: str, applied_text: str = ""):
    """状態列と応募日時列に指定値を入れた 1 行だけの候補タブを持つ gateway。"""
    gw = impl("job_candidate_list.sheets_gateway")
    auto = list(gw.AUTO_COLUMNS_CANDIDATE)
    header = auto + list(gw.MANUAL_COLUMNS_CANDIDATE)
    row = [""] * len(header)
    row[0] = "cand_0000000000000001"
    row[1] = "株式会社サンプル青葉"
    row[2] = "プロダクトマネージャー"
    row[auto.index("情報源URL")] = "求人ページ"
    row[auto.index("状態")] = status_text
    row[auto.index("応募日時")] = applied_text
    links = {(1, auto.index("情報源URL")): "https://example.invalid/j/1"}
    backend = _StaticBackend([header, row], links)
    return gw.GoogleSheetsGateway(backend), backend


# ---------------------------------------------------------------------------
# 項目2: bookkeep の「終了」と CandidateStatus の不整合
# ---------------------------------------------------------------------------
def test_status_alias_closed_text_from_bookkeep():
    """bookkeep が書く「終了」を CandidateStatus.CLOSED として読み取れる。"""
    models = impl("job_candidate_list.models")
    record = models.CandidateRecord(candidate_id="cand_x", status="終了")
    assert record.status is models.CandidateStatus.CLOSED


def test_read_candidates_accepts_existing_closed_rows():
    """実シートに既にある「終了」の行を読んでも ValueError で止まらない。"""
    models = impl("job_candidate_list.models")
    gateway, _backend = _seeded_gateway("終了")
    records = gateway.read_candidates()
    assert len(records) == 1
    assert records[0].status is models.CandidateStatus.CLOSED


def test_read_candidates_accepts_canonical_closed_rows():
    """正規の「掲載終了」も従来どおり CLOSED として読める。"""
    models = impl("job_candidate_list.models")
    gateway, _backend = _seeded_gateway("掲載終了")
    records = gateway.read_candidates()
    assert records[0].status is models.CandidateStatus.CLOSED
