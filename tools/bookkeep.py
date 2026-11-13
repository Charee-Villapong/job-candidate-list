#!/usr/bin/env python3
"""応募候補リストの定型後処理ツール。

応募1件ごとの後処理（応募済みの記録・行のグレー化・終了の記録）を
1コマンドにまとめ、実行後の読み戻し検証まで行う。
これまで手作業で CLI を繰り返し叩いていた手順をコードに固定したもの。

使い方:
    python3 tools/bookkeep.py applied --row 64 --memo "応募ID: ..."
    python3 tools/bookkeep.py close --row 61 --reason "対象外のため終了"
    python3 tools/bookkeep.py gray --row 64

共通オプション:
    --dry-run  書き込まず、実行予定だけを JSON で出力する

結果は JSON で標準出力する。読み戻し検証に失敗した場合は終了コード 1。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

CLI = "/opt/hatch/bin/hatch_gws_cli"
SPREADSHEET_ID = "19wGlth8THbDMBJCi4ppIVTz0u7dE8L2X0IqnK8ZSoRw"
TAB = "候補リスト"
SHEET_ID = 0

# 列記号（候補リストの列構成）
COL_STATUS = "S"  # 状態
COL_APPLY_STATUS = "T"  # 応募状態
COL_APPLIED_AT = "U"  # 応募日時
COL_JUDGMENT = "W"  # 本人の判断
COL_MEMO = "X"  # 本人のメモ

ROW_FIRST_COL_INDEX = 0  # A列
ROW_LAST_COL_INDEX = 24  # X列の次（endColumnIndex は排他）

GRAY = {"red": 0.85, "green": 0.85, "blue": 0.85}
GRAY_TOLERANCE = 0.01  # API は 0.85 を 216/255 で返すため丸めを許容する

APPLIED_TEXT = "応募済み"
CLOSED_TEXT = "終了"

JST = ZoneInfo("Asia/Tokyo")
_DT_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
)


class BookkeepError(Exception):
    """CLI 呼び出し等の実行時エラー。"""


def _invoke(argv: list[str], params: dict, payload: dict | None = None) -> dict:
    cmd = [CLI, *argv, "--params", json.dumps(params, ensure_ascii=False)]
    if payload is not None:
        cmd += ["--json", json.dumps(payload, ensure_ascii=False)]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "")[:300]
        raise BookkeepError(f"CLI 呼び出しに失敗しました ({' '.join(argv)}): {detail}")
    if not completed.stdout.strip():
        return {}
    return json.loads(completed.stdout)


def _values_update(range_: str, values: list[list[str]]) -> dict:
    params = {
        "spreadsheetId": SPREADSHEET_ID,
        "range": range_,
        "valueInputOption": "USER_ENTERED",
    }
    return _invoke(
        ["sheets", "spreadsheets", "values", "update"], params, {"values": values}
    )


def _values_get(range_: str) -> list[list[str]]:
    params = {"spreadsheetId": SPREADSHEET_ID, "range": range_}
    response = _invoke(["sheets", "spreadsheets", "values", "get"], params)
    return response.get("values", [])


def _batch_update(requests: list[dict]) -> dict:
    params = {"spreadsheetId": SPREADSHEET_ID}
    return _invoke(
        ["sheets", "spreadsheets", "batchUpdate"], params, {"requests": requests}
    )


def _gray_request(row: int) -> dict:
    return {
        "repeatCell": {
            "range": {
                "sheetId": SHEET_ID,
                "startRowIndex": row - 1,
                "endRowIndex": row,
                "startColumnIndex": ROW_FIRST_COL_INDEX,
                "endColumnIndex": ROW_LAST_COL_INDEX,
            },
            "cell": {"userEnteredFormat": {"backgroundColor": dict(GRAY)}},
            "fields": "userEnteredFormat.backgroundColor",
        }
    }


def _read_gray(row: int) -> bool:
    """行全体（A〜X）の背景色を読み戻し、グレー一色かを確認する。"""
    params = {
        "spreadsheetId": SPREADSHEET_ID,
        "includeGridData": True,
        "ranges": [f"{TAB}!A{row}:X{row}"],
        "fields": "sheets.data.rowData.values.userEnteredFormat.backgroundColor",
    }
    response = _invoke(["sheets", "spreadsheets", "get"], params)
    try:
        cells = response["sheets"][0]["data"][0]["rowData"][0]["values"]
    except (KeyError, IndexError, TypeError):
        return False
    if len(cells) < ROW_LAST_COL_INDEX:
        return False
    for cell in cells:
        color = (cell.get("userEnteredFormat") or {}).get("backgroundColor") or {}
        for channel in ("red", "green", "blue"):
            if abs(color.get(channel, 0.0) - GRAY[channel]) > GRAY_TOLERANCE:
                return False
    return True


def _now_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def _parse_datetime(text: str) -> datetime | None:
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _same_minute(readback: str, expected: str) -> bool:
    """日時の読み戻し比較。シート側の表示整形（時のゼロ落ち等）を許容する。"""
    parsed_readback = _parse_datetime(readback)
    parsed_expected = _parse_datetime(expected)
    if parsed_readback is None or parsed_expected is None:
        return readback == expected
    return parsed_readback == parsed_expected


def _pad(cells: list[str], length: int) -> list[str]:
    return (list(cells) + [""] * length)[:length]


def _first_row(range_: str) -> list[str]:
    rows = _values_get(range_)
    return rows[0] if rows else []


def op_applied(
    row: int,
    memo: str | None = None,
    *,
    dry_run: bool = False,
    timestamp: str | None = None,
) -> dict:
    """応募済みの記録（T・U・任意でX）＋行グレー化＋読み戻し検証。"""
    applied_at = timestamp if timestamp is not None else _now_jst()
    writes: list[dict] = [
        {
            "type": "values",
            "range": f"{TAB}!{COL_APPLY_STATUS}{row}:{COL_APPLIED_AT}{row}",
            "values": [[APPLIED_TEXT, applied_at]],
        }
    ]
    if memo is not None:
        writes.append(
            {
                "type": "values",
                "range": f"{TAB}!{COL_MEMO}{row}",
                "values": [[memo]],
            }
        )
    writes.append({"type": "gray", "range": f"{TAB}!A{row}:X{row}"})
    result: dict = {
        "operation": "applied",
        "row": row,
        "dry_run": dry_run,
        "applied_at": applied_at,
        "writes": writes,
    }
    if dry_run:
        return result
    for write in writes:
        if write["type"] == "values":
            _values_update(write["range"], write["values"])
    _batch_update([_gray_request(row)])
    cells = _pad(_first_row(f"{TAB}!{COL_APPLY_STATUS}{row}:{COL_MEMO}{row}"), 5)
    gray_ok = _read_gray(row)
    checks = {
        "apply_status": cells[0] == APPLIED_TEXT,
        "applied_at": _same_minute(cells[1], applied_at),
        "gray": gray_ok,
    }
    readback: dict = {
        COL_APPLY_STATUS: cells[0],
        COL_APPLIED_AT: cells[1],
        "gray": gray_ok,
    }
    if memo is not None:
        checks["memo"] = cells[4] == memo
        readback[COL_MEMO] = cells[4]
    result["checks"] = checks
    result["readback"] = readback
    result["verified"] = all(checks.values())
    return result


def op_close(row: int, reason: str, *, dry_run: bool = False) -> dict:
    """終了の記録（S・W）。間の列（T・U・V）には触らない。"""
    writes = [
        {
            "type": "values",
            "range": f"{TAB}!{COL_STATUS}{row}",
            "values": [[CLOSED_TEXT]],
        },
        {
            "type": "values",
            "range": f"{TAB}!{COL_JUDGMENT}{row}",
            "values": [[reason]],
        },
    ]
    result: dict = {
        "operation": "close",
        "row": row,
        "dry_run": dry_run,
        "reason": reason,
        "writes": writes,
    }
    if dry_run:
        return result
    for write in writes:
        _values_update(write["range"], write["values"])
    cells = _pad(_first_row(f"{TAB}!{COL_STATUS}{row}:{COL_JUDGMENT}{row}"), 5)
    checks = {
        "status": cells[0] == CLOSED_TEXT,
        "judgment": cells[4] == reason,
    }
    result["checks"] = checks
    result["readback"] = {COL_STATUS: cells[0], COL_JUDGMENT: cells[4]}
    result["verified"] = all(checks.values())
    return result


def op_gray(row: int, *, dry_run: bool = False) -> dict:
    """行全体（A〜X）のグレー化＋読み戻し検証。"""
    writes = [{"type": "gray", "range": f"{TAB}!A{row}:X{row}"}]
    result: dict = {
        "operation": "gray",
        "row": row,
        "dry_run": dry_run,
        "writes": writes,
    }
    if dry_run:
        return result
    _batch_update([_gray_request(row)])
    gray_ok = _read_gray(row)
    result["checks"] = {"gray": gray_ok}
    result["readback"] = {"gray": gray_ok}
    result["verified"] = gray_ok
    return result


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--row", type=int, required=True, help="シートの行番号（2以上）"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="書き込まず、実行予定だけを JSON で出力する",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="応募候補リストの定型後処理（応募済み記録・終了記録・グレー化）"
    )
    sub = parser.add_subparsers(dest="operation", required=True)

    applied = sub.add_parser("applied", help="応募済みを記録し行をグレー化する")
    _add_common_arguments(applied)
    applied.add_argument("--memo", default=None, help="本人のメモ（X列）へ書く文面")

    close = sub.add_parser("close", help="終了と本人の判断（理由）を記録する")
    _add_common_arguments(close)
    close.add_argument("--reason", required=True, help="本人の判断（W列）へ書く理由")

    gray = sub.add_parser("gray", help="行全体をグレー化する")
    _add_common_arguments(gray)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.row < 2:
        print(
            json.dumps(
                {"error": "--row は2以上（1行目はヘッダ）を指定してください"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        if args.operation == "applied":
            result = op_applied(args.row, args.memo, dry_run=args.dry_run)
        elif args.operation == "close":
            result = op_close(args.row, args.reason, dry_run=args.dry_run)
        else:
            result = op_gray(args.row, dry_run=args.dry_run)
    except BookkeepError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0
    return 0 if result.get("verified") else 1


if __name__ == "__main__":
    sys.exit(main())
