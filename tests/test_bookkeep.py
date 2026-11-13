"""tools/bookkeep.py のテスト。

subprocess 呼び出しをインメモリのフェイク CLI に差し替え、実シートには
一切触らずに、生成されるリクエスト内容・読み戻し検証・dry-run の
無書き込みを検証する。
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

_BOOKKEEP_PATH = Path(__file__).resolve().parents[1] / "tools" / "bookkeep.py"
_spec = importlib.util.spec_from_file_location("bookkeep", _BOOKKEEP_PATH)
bookkeep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bookkeep)

GRAY_API_VALUE = 216 / 255  # API が返す 0.85 の実値（216/255）


def _col_to_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index


def _index_to_col(index: int) -> str:
    letters = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


_RANGE_RE = re.compile(r"!([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$")


def _parse_range(range_: str) -> tuple[str, int, str, int]:
    m = _RANGE_RE.search(range_)
    assert m, f"範囲を解釈できない: {range_}"
    start_col, start_row = m.group(1), int(m.group(2))
    end_col = m.group(3) or start_col
    end_row = int(m.group(4)) if m.group(4) else start_row
    return start_col, start_row, end_col, end_row


class FakeGwsCli:
    """hatch_gws_cli の代わりになるインメモリフェイク。

    values update/get と batchUpdate（グレー化）、書式読み戻し
    （spreadsheets get の includeGridData）を最小限再現する。
    """

    def __init__(self) -> None:
        self.cells: dict[tuple[int, str], str] = {}
        self.gray_rows: set[int] = set()
        self.commands: list[list[str]] = []
        self.drop_writes = False  # True なら書込が成功扱いでも反映されない
        self.reformat_hour = False  # True なら日時の「時」のゼロ落ちを再現する

    # -- subprocess.run の代替 -------------------------------------------
    def __call__(self, cmd, **kwargs):
        self.commands.append(list(cmd))
        params = self._extract(cmd, "--params")
        payload = self._extract(cmd, "--json")
        method = cmd[1:4]
        if method == ["sheets", "spreadsheets", "values"] and cmd[4] == "update":
            response = self._values_update(params, payload)
        elif method == ["sheets", "spreadsheets", "values"] and cmd[4] == "get":
            response = self._values_get(params)
        elif method == ["sheets", "spreadsheets", "batchUpdate"]:
            response = self._batch_update(payload)
        elif method == ["sheets", "spreadsheets", "get"]:
            response = self._spreadsheets_get(params)
        else:  # pragma: no cover - 想定外の呼び出しはテストを落とす
            raise AssertionError(f"想定外の CLI 呼び出し: {cmd}")
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(response, ensure_ascii=False), stderr=""
        )

    @staticmethod
    def _extract(cmd, flag):
        if flag not in cmd:
            return None
        return json.loads(cmd[cmd.index(flag) + 1])

    # -- 各メソッドの再現 --------------------------------------------------
    def _values_update(self, params, payload):
        if not self.drop_writes:
            start_col, start_row, _, _ = _parse_range(params["range"])
            base = _col_to_index(start_col)
            for row_offset, row_values in enumerate(payload["values"]):
                for col_offset, value in enumerate(row_values):
                    col = _index_to_col(base + col_offset)
                    self.cells[(start_row + row_offset, col)] = value
        return {"updatedRange": params["range"], "updatedCells": 1}

    def _values_get(self, params):
        start_col, start_row, end_col, end_row = _parse_range(params["range"])
        rows = []
        for row in range(start_row, end_row + 1):
            values = []
            for idx in range(_col_to_index(start_col), _col_to_index(end_col) + 1):
                value = self.cells.get((row, _index_to_col(idx)), "")
                if self.reformat_hour:
                    value = re.sub(
                        r"(\d{4}-\d{2}-\d{2} )0(\d:\d{2})", r"\g<1>\2", value
                    )
                values.append(value)
            rows.append(values)
        while rows and all(v == "" for v in rows[-1]):
            rows.pop()
        return {"values": rows} if rows else {}

    def _batch_update(self, payload):
        if not self.drop_writes:
            for request in payload["requests"]:
                grid = request["repeatCell"]["range"]
                self.gray_rows.add(grid["startRowIndex"] + 1)
        return {"replies": [{} for _ in payload["requests"]]}

    def _spreadsheets_get(self, params):
        _, row, _, _ = _parse_range(params["ranges"][0])
        if row in self.gray_rows:
            color = {
                "red": GRAY_API_VALUE,
                "green": GRAY_API_VALUE,
                "blue": GRAY_API_VALUE,
            }
            cells = [
                {"userEnteredFormat": {"backgroundColor": color}} for _ in range(24)
            ]
        else:
            cells = [{} for _ in range(24)]
        return {"sheets": [{"data": [{"rowData": [{"values": cells}]}]}]}

    # -- テスト用ヘルパ ----------------------------------------------------
    def value_commands(self) -> list[list[str]]:
        return [c for c in self.commands if "values" in c and "update" in c]

    def batch_update_payloads(self) -> list[dict]:
        payloads = []
        for cmd in self.commands:
            if "batchUpdate" in cmd:
                payloads.append(self._extract(cmd, "--json"))
        return payloads


@pytest.fixture()
def fake_cli(monkeypatch):
    fake = FakeGwsCli()
    monkeypatch.setattr(bookkeep.subprocess, "run", fake)
    return fake


# ---------------------------------------------------------------------------
# applied
# ---------------------------------------------------------------------------
def test_applied_writes_values_gray_and_verifies(fake_cli):
    result = bookkeep.op_applied(
        64, memo="応募ID: ID00022028", timestamp="2026-11-14 01:23"
    )
    assert result["verified"] is True
    assert fake_cli.cells[(64, "T")] == "応募済み"
    assert fake_cli.cells[(64, "U")] == "2026-11-14 01:23"
    assert fake_cli.cells[(64, "X")] == "応募ID: ID00022028"
    assert 64 in fake_cli.gray_rows
    assert result["readback"]["T"] == "応募済み"
    assert result["readback"]["gray"] is True


def test_applied_request_payloads(fake_cli):
    bookkeep.op_applied(64, memo="メモ", timestamp="2026-11-14 01:23")
    updates = fake_cli.value_commands()
    assert len(updates) == 2
    first_params = FakeGwsCli._extract(updates[0], "--params")
    first_payload = FakeGwsCli._extract(updates[0], "--json")
    assert first_params["range"] == "候補リスト!T64:U64"
    assert first_params["valueInputOption"] == "USER_ENTERED"
    assert first_payload == {"values": [["応募済み", "2026-11-14 01:23"]]}
    second_params = FakeGwsCli._extract(updates[1], "--params")
    assert second_params["range"] == "候補リスト!X64"
    (payload,) = fake_cli.batch_update_payloads()
    repeat = payload["requests"][0]["repeatCell"]
    assert repeat["range"] == {
        "sheetId": 0,
        "startRowIndex": 63,
        "endRowIndex": 64,
        "startColumnIndex": 0,
        "endColumnIndex": 24,
    }
    assert repeat["cell"]["userEnteredFormat"]["backgroundColor"] == {
        "red": 0.85,
        "green": 0.85,
        "blue": 0.85,
    }
    assert repeat["fields"] == "userEnteredFormat.backgroundColor"


def test_applied_without_memo_does_not_touch_memo_cell(fake_cli):
    fake_cli.cells[(64, "X")] = "既存メモ"
    result = bookkeep.op_applied(64, timestamp="2026-11-14 01:23")
    assert result["verified"] is True
    assert fake_cli.cells[(64, "X")] == "既存メモ"
    ranges = [
        FakeGwsCli._extract(c, "--params")["range"] for c in fake_cli.value_commands()
    ]
    assert ranges == ["候補リスト!T64:U64"]


def test_applied_verification_tolerates_reformatted_hour(fake_cli):
    fake_cli.reformat_hour = True
    result = bookkeep.op_applied(64, timestamp="2026-11-14 01:23")
    assert result["readback"]["U"] == "2026-11-14 1:23"
    assert result["checks"]["applied_at"] is True
    assert result["verified"] is True


def test_applied_verification_fails_when_write_is_dropped(fake_cli):
    fake_cli.drop_writes = True
    result = bookkeep.op_applied(64, memo="メモ", timestamp="2026-11-14 01:23")
    assert result["verified"] is False
    assert result["checks"]["apply_status"] is False
    assert result["checks"]["gray"] is False


def test_applied_dry_run_writes_nothing(fake_cli):
    result = bookkeep.op_applied(64, memo="メモ", dry_run=True)
    assert result["dry_run"] is True
    assert "verified" not in result
    assert fake_cli.commands == []
    assert fake_cli.cells == {}
    assert fake_cli.gray_rows == set()
    assert [w["type"] for w in result["writes"]] == ["values", "values", "gray"]


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------
def test_close_writes_status_and_reason_without_touching_apply_columns(fake_cli):
    fake_cli.cells[(61, "T")] = "応募済み"
    fake_cli.cells[(61, "U")] = "2026-11-14 00:54"
    result = bookkeep.op_close(61, reason="基幹系のため対象外（本人の判断）")
    assert result["verified"] is True
    assert fake_cli.cells[(61, "S")] == "終了"
    assert fake_cli.cells[(61, "W")] == "基幹系のため対象外（本人の判断）"
    # S と W を個別に書くので、間の T・U は元の値のまま残る
    assert fake_cli.cells[(61, "T")] == "応募済み"
    assert fake_cli.cells[(61, "U")] == "2026-11-14 00:54"
    ranges = [
        FakeGwsCli._extract(c, "--params")["range"] for c in fake_cli.value_commands()
    ]
    assert ranges == ["候補リスト!S61", "候補リスト!W61"]


def test_close_dry_run_writes_nothing(fake_cli):
    result = bookkeep.op_close(61, reason="理由", dry_run=True)
    assert result["dry_run"] is True
    assert fake_cli.commands == []
    assert fake_cli.cells == {}


def test_close_verification_fails_when_write_is_dropped(fake_cli):
    fake_cli.drop_writes = True
    result = bookkeep.op_close(61, reason="理由")
    assert result["verified"] is False
    assert result["checks"] == {"status": False, "judgment": False}


# ---------------------------------------------------------------------------
# gray
# ---------------------------------------------------------------------------
def test_gray_only_sets_background(fake_cli):
    result = bookkeep.op_gray(70)
    assert result["verified"] is True
    assert 70 in fake_cli.gray_rows
    assert fake_cli.value_commands() == []
    assert fake_cli.cells == {}


def test_gray_dry_run_writes_nothing(fake_cli):
    result = bookkeep.op_gray(70, dry_run=True)
    assert result["dry_run"] is True
    assert fake_cli.commands == []
    assert fake_cli.gray_rows == set()


def test_gray_verification_fails_when_write_is_dropped(fake_cli):
    fake_cli.drop_writes = True
    result = bookkeep.op_gray(70)
    assert result["verified"] is False
    assert result["readback"] == {"gray": False}


# ---------------------------------------------------------------------------
# CLI（main）
# ---------------------------------------------------------------------------
def test_main_applied_returns_zero_and_prints_json(fake_cli, capsys):
    exit_code = bookkeep.main(["applied", "--row", "64", "--memo", "メモ"])
    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["operation"] == "applied"
    assert printed["verified"] is True


def test_main_close_requires_reason():
    with pytest.raises(SystemExit) as excinfo:
        bookkeep.main(["close", "--row", "61"])
    assert excinfo.value.code == 2


def test_main_rejects_header_row(fake_cli, capsys):
    exit_code = bookkeep.main(["gray", "--row", "1"])
    assert exit_code == 2
    assert fake_cli.commands == []


def test_main_dry_run_returns_zero_without_writes(fake_cli, capsys):
    exit_code = bookkeep.main(["applied", "--row", "64", "--dry-run"])
    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["dry_run"] is True
    assert fake_cli.commands == []
