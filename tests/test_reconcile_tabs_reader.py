"""tools/reconcile_tabs.py の読み取り側の回帰防止テスト（実装係による追加）。

独立テスト tests/test_reconcile_tabs.py には触らない。
ここではシート読み取りまわりの純粋ヘルパーだけを検証する。
実シートには一切触らない。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_RECONCILE_PATH = Path(__file__).resolve().parents[1] / "tools" / "reconcile_tabs.py"
_spec = importlib.util.spec_from_file_location("reconcile_tabs", _RECONCILE_PATH)
reconcile_tabs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reconcile_tabs)

GRAY = (0.85, 0.85, 0.85)
GRAY_API = (216 / 255, 216 / 255, 216 / 255)  # API が返す 0.85 の実値
WHITE = (1.0, 1.0, 1.0)


def _cell(color=None):
    if color is None:
        return {}
    return {
        "userEnteredFormat": {
            "backgroundColor": {"red": color[0], "green": color[1], "blue": color[2]}
        }
    }


def test_row_gray_state_all_gray_cells_is_true():
    cells = [_cell(GRAY_API) for _ in range(24)]
    assert reconcile_tabs._row_gray_state(cells, 24, GRAY, 0.01, WHITE) is True


def test_row_gray_state_no_fill_is_false():
    assert reconcile_tabs._row_gray_state([], 24, GRAY, 0.01, WHITE) is False
    assert (
        reconcile_tabs._row_gray_state([{} for _ in range(24)], 24, GRAY, 0.01, WHITE)
        is False
    )


def test_row_gray_state_partial_gray_is_none():
    cells = [_cell(GRAY_API) for _ in range(12)] + [{} for _ in range(12)]
    assert reconcile_tabs._row_gray_state(cells, 24, GRAY, 0.01, WHITE) is None


def test_row_gray_state_uniform_non_gray_is_false():
    cells = [_cell(WHITE) for _ in range(24)]
    assert reconcile_tabs._row_gray_state(cells, 24, GRAY, 0.01, WHITE) is False


def test_row_gray_state_baseline_is_gray_is_none():
    cells = [_cell(GRAY_API) for _ in range(24)]
    assert reconcile_tabs._row_gray_state(cells, 24, GRAY, 0.01, GRAY) is None


def test_rows_from_values_pads_short_rows_and_numbers_rows():
    values = [
        ["会社名", "職種", "応募日"],
        ["テスト商事", "プロジェクトマネージャー"],
        ["架空工業", "ITコンサルタント", "2026-11-10"],
    ]
    rows = reconcile_tabs._rows_from_values(values)
    assert rows[0] == {
        "会社名": "テスト商事",
        "職種": "プロジェクトマネージャー",
        "応募日": "",
        "_row": 2,
    }
    assert rows[1]["_row"] == 3
    assert reconcile_tabs._rows_from_values([]) == []
