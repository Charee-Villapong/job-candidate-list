#!/usr/bin/env python3
"""応募候補リストの二タブ照合ドライバー（Issue #87）。

3タブ（選考結果／応募済み企業一覧／候補リスト）を読み取り専用で読み、
4種の差分 (1) 一覧にない (2) 結果の不一致 (3) 選考結果にない
(4) グレー化されていない を抽出して、同期の下書きを JSON で出す。

読み取り専用: シートへの書き込み機能・フラグを持たない。
承認後の反映は既存ツール（sheet-writer / bookkeep.py）経由で行う前提で、
下書き JSON には反映に使える range / values を含める。

使い方:
    python3 tools/reconcile_tabs.py [--config <path>] [--out <path>]

接続先（CLI パス・スプレッドシート ID）は tools/reconcile_tabs.local.json
（.gitignore 対象）から読む。実値はそのファイルにだけ置く。
会社名の正規化ルール・グレー判定の色・タブ名・列構成はコミット対象の
設定ファイル tools/reconcile_tabs_config.json から読む。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

_DEFAULT_CONFIG_PATH = Path(__file__).with_name("reconcile_tabs_config.json")
_LOCAL_CONFIG_PATH = Path(__file__).with_name("reconcile_tabs.local.json")

TAB_RESULTS = "選考結果"
TAB_LIST = "応募済み企業一覧"
TAB_CANDIDATES = "候補リスト"

CLOSED_TEXT = "終了"
APPLIED_TEXT = "応募済み"

# 一覧 C 列の単独文言として判定できる既知の結果値。設定ファイルの
# known_results があればそちらを優先する。
_DEFAULT_KNOWN_RESULTS = (
    "選考中",
    "書類選考中",
    "書類選考で不合格",
    "書類選考通過",
    "面接中",
    "内定",
    "辞退",
    "不採用",
    "募集終了",
    "選考終了",
    "内定辞退",
)

_KIND_LABELS = {
    "missing_in_list": "一覧にない",
    "result_mismatch": "結果の不一致",
    "missing_in_results": "選考結果にない",
    "not_grayed": "グレー化されていない",
}

_DATE_RE = re.compile(
    r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?$"
)
_COUNT_SEGMENT_RE = re.compile(r"^(.+?)[（(](\d+)件[）)]$")


class ReconcileError(Exception):
    """設定・シート読み取りの実行時エラー。"""


# ---------------------------------------------------------------------------
# 正規化（純粋関数）
# ---------------------------------------------------------------------------
def normalize_company(name, rules=None) -> str:
    """会社名を正規化する。ルールは設定ファイル由来の辞書。

    rules のキー:
      remove_whitespace: 空白（半角・全角）を除去する
      normalize_width: 全角・半角を統一する（NFKC）
      remove_corporate_forms: 除去する法人格表記のリスト
    ルールに無い処理は行わない（空の rules なら入力をそのまま返す）。
    """
    if name is None:
        return ""
    text = str(name)
    rules = rules or {}
    if rules.get("normalize_width"):
        text = unicodedata.normalize("NFKC", text)
    for form in rules.get("remove_corporate_forms") or []:
        if not form:
            continue
        form_text = str(form)
        if rules.get("normalize_width"):
            form_text = unicodedata.normalize("NFKC", form_text)
        text = text.replace(form_text, "")
    if rules.get("remove_whitespace"):
        text = re.sub(r"\s+", "", text)
    return text


def normalize_date(text) -> str | None:
    """応募日を YYYY-MM-DD に落とす。空欄・解析不能は None。

    受け付ける形: 2026-11-13 / 2026/11/13 / 時刻付き
    （2026-11-13 21:45、2026-11-14 1:31 のような時のゼロ落ちを含む）。
    """
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    match = _DATE_RE.match(text)
    if not match:
        return None
    try:
        parsed = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return parsed.isoformat()


def _normalize_role(text) -> str:
    """職種・ポジション名の比較用正規化（全角・半角統一＋空白除去）。"""
    if text is None:
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text)))


# ---------------------------------------------------------------------------
# 集計文言 (2)
# ---------------------------------------------------------------------------
def _aggregate_results(rows, result_header: str) -> dict:
    """選考結果タブ側の結果値を数える（初出順を保つ）。空欄は数えない。"""
    counts: dict = {}
    for row in rows:
        value = str(row.get(result_header) or "").strip()
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def _canonical_summary(counts: dict) -> str:
    """件数付き集計文言（例: 選考中（3件）、募集終了（1件））。"""
    return "、".join(f"{value}（{count}件）" for value, count in counts.items())


def _parse_counted_summary(text) -> dict | None:
    """件数形式の集計文言を {結果値: 件数} にする。形式でなければ None。"""
    if text is None:
        return None
    normalized = unicodedata.normalize("NFKC", str(text)).strip()
    if not normalized:
        return None
    parsed: dict = {}
    for part in re.split(r"[、,，\s]+", normalized):
        part = part.strip()
        if not part:
            return None
        match = _COUNT_SEGMENT_RE.match(part)
        if not match:
            return None
        parsed[match.group(1).strip()] = int(match.group(2))
    return parsed


# ---------------------------------------------------------------------------
# 照合（純粋関数）
# ---------------------------------------------------------------------------
def _headers(config: dict) -> dict:
    defaults = {
        "results": {
            "company": "会社名",
            "role": "職種",
            "applied_date": "応募日",
            "result": "選考結果",
        },
        "list": {"company": "会社名", "applied_date": "応募日", "result": "結果"},
        "candidates": {
            "company": "会社名",
            "position": "ポジション名",
            "status": "状態",
            "apply_status": "応募状態",
            "applied_at": "応募日時",
        },
    }
    override = (config or {}).get("headers") or {}
    for tab, mapping in override.items():
        if isinstance(mapping, dict) and tab in defaults:
            defaults[tab].update(mapping)
    return defaults


def _tabs(config: dict) -> dict:
    defaults = {
        "results": TAB_RESULTS,
        "list": TAB_LIST,
        "candidates": TAB_CANDIDATES,
    }
    override = (config or {}).get("tabs") or {}
    for key, value in override.items():
        if key in defaults and value:
            defaults[key] = value
    return defaults


def _columns(config: dict) -> dict:
    defaults = {
        "results": {"company": "A", "role": "B", "applied_date": "C", "result": "D"},
        "list": {"company": "A", "applied_date": "B", "result": "C"},
        "candidates": {
            "first": "A",
            "last": "X",
            "company": "B",
            "position": "C",
            "status": "S",
            "apply_status": "T",
            "applied_at": "U",
            "result": "V",
        },
    }
    override = (config or {}).get("columns") or {}
    for tab, mapping in override.items():
        if isinstance(mapping, dict) and tab in defaults:
            defaults[tab].update(mapping)
    return defaults


def _diff(
    kind,
    company,
    applied_date,
    role,
    tab,
    row,
    before,
    after,
    note,
    range_=None,
    values=None,
) -> dict:
    diff = {
        "kind": kind,
        "company": company,
        "applied_date": applied_date,
        "role": role,
        "target": {"tab": tab, "row": row},
        "before": before,
        "after": after,
        "note": note,
    }
    if range_ is not None:
        diff["range"] = range_
    if values is not None:
        diff["values"] = values
    return diff


def reconcile(results_rows, list_rows, candidate_rows, config=None) -> dict:
    """3タブの行データを照合し、下書き結果の辞書を返す（純粋関数）。

    行データはヘッダー名をキーとする辞書のリスト。行番号は "_row" キー、
    候補リスト行のグレー状態は "_gray" キー（True / False / None）。
    シート読み取りは含まない。入力は変更しない。
    """
    config = config or {}
    rules = config.get("rules") or {}
    headers = _headers(config)
    tabs = _tabs(config)
    columns = _columns(config)
    known_results = set(config.get("known_results") or _DEFAULT_KNOWN_RESULTS)
    res_h, list_h, cand_h = headers["results"], headers["list"], headers["candidates"]

    diffs: list = []
    needs_review: list = []

    # (1)(3) のキー: 正規化した会社名＋正規化した応募日（空欄同士は一致）。
    results_groups: dict = {}
    results_order: list = []
    for row in results_rows or []:
        key = (
            normalize_company(row.get(res_h["company"]), rules),
            normalize_date(row.get(res_h["applied_date"])),
        )
        if key not in results_groups:
            results_groups[key] = []
            results_order.append(key)
        results_groups[key].append(row)
    list_groups: dict = {}
    list_order: list = []
    for row in list_rows or []:
        key = (
            normalize_company(row.get(list_h["company"]), rules),
            normalize_date(row.get(list_h["applied_date"])),
        )
        if key not in list_groups:
            list_groups[key] = []
            list_order.append(key)
        list_groups[key].append(row)

    # 追加行の書き込み先（行番号）。既存の最大行＋1 から順に割り振る。
    def _next_row(rows):
        numbers = [r.get("_row") for r in rows or [] if isinstance(r.get("_row"), int)]
        return (max(numbers) + 1) if numbers else 2

    list_next_row = _next_row(list_rows)
    results_next_row = _next_row(results_rows)

    # (1) 選考結果にあって一覧にない
    for key in results_order:
        if key in list_groups:
            continue
        rows = results_groups[key]
        company, applied_date = key
        counts = _aggregate_results(rows, res_h["result"])
        summary = _canonical_summary(counts)
        first = rows[0]
        role = str(first.get(res_h["role"]) or "").strip() or None
        after = summary or f"{company} を応募済み企業一覧へ追加"
        range_ = (
            f"{tabs['list']}!"
            f"{columns['list']['company']}{list_next_row}"
            f":{columns['list']['result']}{list_next_row}"
        )
        diffs.append(
            _diff(
                "missing_in_list",
                company,
                applied_date,
                role,
                tabs["results"],
                first.get("_row"),
                None,
                after,
                "選考結果タブにあるが、応募済み企業一覧にない。"
                "応募済み企業一覧へ行を追加する。",
                range_=range_,
                values=[[company, applied_date or "", summary]],
            )
        )
        list_next_row += 1

    # (2) 結果の不一致（一覧 C 列の集計文言と選考結果側の集計を比較）
    for key in list_order:
        if key not in results_groups:
            continue
        list_row = list_groups[key][0]
        company, applied_date = key
        counts = _aggregate_results(results_groups[key], res_h["result"])
        if not counts:
            continue
        summary = _canonical_summary(counts)
        list_text = str(list_row.get(list_h["result"]) or "")
        stripped = list_text.strip()
        target_row = list_row.get("_row")
        range_ = (
            f"{tabs['list']}!{columns['list']['result']}{target_row}"
            if isinstance(target_row, int)
            else None
        )
        parsed = _parse_counted_summary(stripped) if stripped else None
        if parsed is not None:
            if parsed == counts:
                continue
            diffs.append(
                _diff(
                    "result_mismatch",
                    company,
                    applied_date,
                    None,
                    tabs["list"],
                    target_row,
                    list_text,
                    summary,
                    "一覧の集計文言と、選考結果タブ側の集計が食い違う。"
                    "一覧の結果列を正しい集計文言に更新する。",
                    range_=range_,
                    values=[[summary]],
                )
            )
            continue
        values_list = list(counts)
        if stripped == "":
            diffs.append(
                _diff(
                    "result_mismatch",
                    company,
                    applied_date,
                    None,
                    tabs["list"],
                    target_row,
                    list_text,
                    summary,
                    "一覧の結果列が空欄で、選考結果タブ側の集計と食い違う。"
                    "一覧の結果列に正しい集計文言を入れる。",
                    range_=range_,
                    values=[[summary]],
                )
            )
            continue
        if len(values_list) == 1 and values_list[0] in stripped:
            continue
        if len(values_list) > 1 and all(v in stripped for v in values_list):
            continue
        if stripped in known_results:
            diffs.append(
                _diff(
                    "result_mismatch",
                    company,
                    applied_date,
                    None,
                    tabs["list"],
                    target_row,
                    list_text,
                    summary,
                    "一覧の結果文言が、選考結果タブ側の結果と食い違う。"
                    "一覧の結果列を正しい集計文言に更新する。",
                    range_=range_,
                    values=[[summary]],
                )
            )
            continue
        review = _diff(
            "result_mismatch",
            company,
            applied_date,
            None,
            tabs["list"],
            target_row,
            list_text,
            summary,
            "一覧の結果文言が集計形式でも単独の結果値でもなく、自動判定しない。",
            range_=range_,
            values=[[summary]],
        )
        review["reason"] = (
            "一覧の結果文言を解釈できなかったため自動判定しない。要・手動確認。"
        )
        needs_review.append(review)

    # (3) 一覧にあって選考結果にない
    for key in list_order:
        if key in results_groups:
            continue
        list_row = list_groups[key][0]
        company, applied_date = key
        list_text = str(list_row.get(list_h["result"]) or "")
        after = list_text or f"{company} を選考結果タブへ追加"
        range_ = (
            f"{tabs['results']}!"
            f"{columns['results']['company']}{results_next_row}"
            f":{columns['results']['result']}{results_next_row}"
        )
        diffs.append(
            _diff(
                "missing_in_results",
                company,
                applied_date,
                None,
                tabs["list"],
                list_row.get("_row"),
                None,
                after,
                "応募済み企業一覧にあるが、選考結果タブにない。"
                "選考結果タブへ行を追加する。",
                range_=range_,
                values=[[company, "", applied_date or "", list_text]],
            )
        )
        results_next_row += 1

    # (4) グレーアウト判定。対応する応募は会社名＋職種＋応募日で切り分ける
    # （完了の定義どおり）。応募日は日付部分で比較し、両方空欄
    # （None）同士は一致として扱う（(1)(3) と同じ扱い）。
    results_roles: set = set()
    for row in results_rows or []:
        company = normalize_company(row.get(res_h["company"]), rules)
        role = _normalize_role(row.get(res_h["role"]))
        applied_date = normalize_date(row.get(res_h["applied_date"]))
        if company and role:
            results_roles.add((company, role, applied_date))
    for row in candidate_rows or []:
        apply_status = row.get(cand_h["apply_status"])
        if apply_status is not None and str(apply_status).strip() != APPLIED_TEXT:
            continue
        if str(row.get(cand_h["status"]) or "").strip() == CLOSED_TEXT:
            continue
        company = normalize_company(row.get(cand_h["company"]), rules)
        role_raw = str(row.get(cand_h["position"]) or "").strip()
        role_norm = _normalize_role(role_raw)
        applied_date = normalize_date(row.get(cand_h["applied_at"]))
        target_row = row.get("_row")
        gray_range = (
            f"{tabs['candidates']}!"
            f"{columns['candidates']['first']}{target_row}"
            f":{columns['candidates']['last']}{target_row}"
            if isinstance(target_row, int)
            else None
        )
        gray = row.get("_gray")
        if gray is True:
            continue
        if gray is not True and gray is not False:
            review = _diff(
                "not_grayed",
                company,
                applied_date,
                role_raw or None,
                tabs["candidates"],
                target_row,
                "グレー化されていない（背景色の読み取り不能）",
                "グレー化する",
                "候補リスト行の背景色を読み取れないため、グレー判定ができない。",
                range_=gray_range,
            )
            review["reason"] = (
                "背景色を読み取れなかったためグレー判定ができない。" "要・手動確認。"
            )
            needs_review.append(review)
            continue
        if role_norm and (company, role_norm, applied_date) in results_roles:
            diffs.append(
                _diff(
                    "not_grayed",
                    company,
                    applied_date,
                    role_raw or None,
                    tabs["candidates"],
                    target_row,
                    "グレー化されていない",
                    "グレー化する",
                    "応募済みの候補リスト行がグレー化されていない。"
                    "行全体をグレー化する。",
                    range_=gray_range,
                )
            )
        else:
            review = _diff(
                "not_grayed",
                company,
                applied_date,
                role_raw or None,
                tabs["candidates"],
                target_row,
                "グレー化されていない",
                "グレー化する",
                "候補リストの職種が選考結果タブの職種と突き合わないため、"
                "対応する応募を一意に定められない。",
                range_=gray_range,
            )
            review["reason"] = (
                "候補リストの職種が選考結果タブのどの職種とも突き合わず、"
                "対応する応募を一意に定められないため自動判定しない。"
                "要・手動確認。"
            )
            needs_review.append(review)

    if not diffs and not needs_review:
        summary = "差分なし"
    elif not diffs:
        summary = f"差分なし（要確認{len(needs_review)}件）"
    else:
        kind_counts = Counter(d["kind"] for d in diffs)
        parts = "、".join(f"{_KIND_LABELS[k]}{n}件" for k, n in kind_counts.items())
        summary = f"差分{len(diffs)}件（{parts}）、要確認{len(needs_review)}件"

    return {
        "generated_at": datetime.now(JST).isoformat(),
        "diffs": diffs,
        "needs_review": needs_review,
        "false_positive_count": len(needs_review),
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# シート読み取り（bookkeep.py と同じ流儀。読み取り専用）
# ---------------------------------------------------------------------------
def _load_local_config() -> dict:
    try:
        data = json.loads(_LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_config(path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReconcileError(f"設定ファイルを読めない: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReconcileError(f"設定ファイルの形が不正: {path}")
    return data


def _invoke(cli: str, spreadsheet_id: str, argv: list, params: dict) -> dict:
    if not cli or not spreadsheet_id:
        raise ReconcileError(
            "ローカル設定ファイル（tools/reconcile_tabs.local.json）に "
            "cli と spreadsheet_id を設定してください"
        )
    cmd = [cli, *argv, "--params", json.dumps(params, ensure_ascii=False)]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "")[:300]
        raise ReconcileError(f"CLI 呼び出しに失敗しました ({' '.join(argv)}): {detail}")
    if not completed.stdout.strip():
        return {}
    return json.loads(completed.stdout)


def _values_get(cli: str, spreadsheet_id: str, range_: str) -> list:
    response = _invoke(
        cli,
        spreadsheet_id,
        ["sheets", "spreadsheets", "values", "get"],
        {"spreadsheetId": spreadsheet_id, "range": range_},
    )
    return response.get("values", [])


def _rows_from_values(values: list) -> list:
    """values get の結果をヘッダー名キー＋ _row 付きの行データにする。"""
    if not values:
        return []
    headers = [str(header).strip() for header in values[0]]
    rows = []
    for index, raw in enumerate(values[1:], start=2):
        row: dict = {"_row": index}
        for col_index, header in enumerate(headers):
            if not header:
                continue
            row[header] = raw[col_index] if col_index < len(raw) else ""
        rows.append(row)
    return rows


def _cell_color(cell: dict):
    color = (cell.get("userEnteredFormat") or {}).get("backgroundColor") or {}
    if not color:
        return None
    return tuple(float(color.get(channel, 0.0)) for channel in ("red", "green", "blue"))


def _close_to(color, target, tolerance: float) -> bool:
    return all(abs(c - t) <= tolerance for c, t in zip(color, target))


def _row_gray_state(
    cells: list, expected_cells: int, gray_rgb, tolerance: float, baseline
) -> bool | None:
    """1行分のセル書式からグレー状態を判定する。

    True: 全セルが設定のグレー色（同パリティ行の背景色＝交互色とは別色）。
    False: グレー色のセルが1つもない（無地・交互色を含む）。
    None: 一部のセルだけグレーなど、判定できない（要・手動確認）。
    セルが1つも無い行は無地（白）として False。
    """
    if not cells:
        return False
    padded = list(cells[:expected_cells])
    padded += [{}] * max(0, expected_cells - len(padded))
    effective = []
    for cell in padded:
        color = _cell_color(cell)
        effective.append(color if color is not None else (1.0, 1.0, 1.0))
    gray_count = sum(1 for c in effective if _close_to(c, gray_rgb, tolerance))
    if gray_count == len(effective):
        if baseline is not None and _close_to(baseline, gray_rgb, tolerance):
            return None
        return True
    if gray_count == 0:
        return False
    return None


def _candidate_gray_map(
    cli: str,
    spreadsheet_id: str,
    tab: str,
    last_row: int,
    last_col_letter: str,
    gray_rgb,
    tolerance: float,
) -> dict | None:
    """候補リスト各行のグレー状態（行番号 -> True / False / None）。

    背景色の取得に失敗した場合は None を返し、呼び出し元が全行を
    読み取り不能（_gray=None）として要確認扱いにする。
    実測した API の挙動: rowData は範囲先頭から最後の書式付き行まで
    位置対応で並び、それより後ろの行は省略される（無地として扱う）。
    """
    if last_row < 1:
        return {}
    try:
        response = _invoke(
            cli,
            spreadsheet_id,
            ["sheets", "spreadsheets", "get"],
            {
                "spreadsheetId": spreadsheet_id,
                "includeGridData": True,
                "ranges": [f"{tab}!A1:{last_col_letter}{last_row}"],
                "fields": "sheets.data.rowData.values.userEnteredFormat.backgroundColor",
            },
        )
        row_data = response["sheets"][0]["data"][0].get("rowData") or []
    except (ReconcileError, KeyError, IndexError, TypeError):
        return None

    def _col_index(letters: str) -> int:
        index = 0
        for ch in letters:
            index = index * 26 + (ord(ch) - ord("A") + 1)
        return index

    expected_cells = _col_index(last_col_letter)

    # 行ごとの代表色（全セル一致の場合のみ）。同パリティ行の最頻色を
    # 交互色の基準とし、グレーと区別する。
    dominants: dict = {}
    for index, entry in enumerate(row_data):
        row_no = index + 1
        cells = entry.get("values") or []
        if not cells:
            dominants[row_no] = (1.0, 1.0, 1.0)
            continue
        colors = []
        for cell in cells[:expected_cells]:
            color = _cell_color(cell)
            colors.append(color if color is not None else (1.0, 1.0, 1.0))
        distinct: list = []
        for color in colors:
            if not any(_close_to(color, other, tolerance) for other in distinct):
                distinct.append(color)
        dominants[row_no] = distinct[0] if len(distinct) == 1 else None
    baselines: dict = {}
    for parity in (0, 1):
        pool = [
            color
            for row_no, color in dominants.items()
            if row_no % 2 == parity
            and color is not None
            and not _close_to(color, gray_rgb, tolerance)
        ]
        baselines[parity] = Counter(pool).most_common(1)[0][0] if pool else None

    gray_map: dict = {}
    for row_no in range(1, last_row + 1):
        if row_no - 1 < len(row_data):
            cells = row_data[row_no - 1].get("values") or []
            gray_map[row_no] = _row_gray_state(
                cells, expected_cells, gray_rgb, tolerance, baselines[row_no % 2]
            )
        else:
            gray_map[row_no] = False
    return gray_map


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="応募候補リストの二タブ照合（読み取り専用）。"
        "3タブを読み、差分の下書きを JSON で出す。"
    )
    parser.add_argument(
        "--config",
        default=None,
        help="設定ファイルのパス（省略時は tools/reconcile_tabs_config.json）",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="出力先のパス（省略時は標準出力へ JSON）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    config_path = Path(args.config) if args.config else _DEFAULT_CONFIG_PATH
    try:
        config = _load_config(config_path)
    except ReconcileError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    local = _load_local_config()
    cli = str(local.get("cli") or "")
    spreadsheet_id = str(local.get("spreadsheet_id") or "")
    tabs = _tabs(config)
    columns = _columns(config)
    gray_cfg = config.get("gray") or {"red": 0.85, "green": 0.85, "blue": 0.85}
    gray_rgb = tuple(
        float(gray_cfg.get(channel, 0.85)) for channel in ("red", "green", "blue")
    )
    tolerance = float(config.get("gray_tolerance", 0.01))
    try:
        results_values = _values_get(cli, spreadsheet_id, f"{tabs['results']}!A1:Z1000")
        list_values = _values_get(cli, spreadsheet_id, f"{tabs['list']}!A1:Z1000")
        cand_values = _values_get(cli, spreadsheet_id, f"{tabs['candidates']}!A1:Z1000")
    except ReconcileError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    results_rows = _rows_from_values(results_values)
    list_rows = _rows_from_values(list_values)
    cand_rows = _rows_from_values(cand_values)
    warnings: list = []
    gray_map = _candidate_gray_map(
        cli,
        spreadsheet_id,
        tabs["candidates"],
        len(cand_values),
        columns["candidates"]["last"],
        gray_rgb,
        tolerance,
    )
    if gray_map is None:
        warnings.append(
            "背景色の読み取りに失敗したため、グレー判定は全行を要・手動確認とする。"
        )
    for row in cand_rows:
        row["_gray"] = gray_map.get(row["_row"]) if gray_map is not None else None
    result = reconcile(results_rows, list_rows, cand_rows, config)
    if warnings:
        result["warnings"] = warnings
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
