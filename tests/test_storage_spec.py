"""完了の定義⑩: 配置表と実体の一致（storage-spec/）。

根拠: 企画書「完了の定義」⑩、architecture.md §11.1。
本テストは実装（src/）に依存せず、配置表と実ファイルだけで検証できる。
"""

from __future__ import annotations

import re

import pytest

from conftest import PLACEMENT_TABLE, REPO_ROOT

REQUIRED_COLUMNS = [
    "ID", "ファイル名", "種類", "形式", "置き場のパス", "原本の入手元",
    "原本の形式", "原本保持状況", "サンプルの検証状態", "サイズ（バイト数）",
]

ALLOWED_KINDS = {"検証用求人サンプル", "モックアップ", "その他"}
ALLOWED_VERIFICATION = {"未検証", "検証済み（期待結果と一致）", "検証済み（不一致あり）"}


def _rows() -> list[dict[str, str]]:
    text = PLACEMENT_TABLE.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.startswith("| SMP-")]
    rows = []
    header = None
    for ln in text.splitlines():
        if ln.startswith("| ID |"):
            header = [c.strip() for c in ln.strip("|").split("|")]
            break
    assert header, "配置表のヘッダ行が見つからない"
    for ln in lines:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        assert len(cells) == len(header), f"列数がヘッダと一致しない: {ln}"
        rows.append(dict(zip(header, cells)))
    return rows


def test_placement_table_exists_with_required_columns():
    assert PLACEMENT_TABLE.exists()
    text = PLACEMENT_TABLE.read_text(encoding="utf-8")
    for col in REQUIRED_COLUMNS:
        assert f"| {col} " in text or f"| {col} |" in text, f"列 {col} が無い"


def test_placement_table_has_39_rows_with_all_fields():
    rows = _rows()
    assert len(rows) == 39
    ids = [r["ID"] for r in rows]
    assert len(set(ids)) == 39, "IDが重複している"
    for r in rows:
        for col in REQUIRED_COLUMNS:
            assert r[col], f"{r['ID']}: {col} が空欄"
        assert r["種類"] in ALLOWED_KINDS
        assert r["サンプルの検証状態"] in ALLOWED_VERIFICATION


@pytest.mark.parametrize("row", _rows(), ids=lambda r: r["ID"])
def test_placement_row_matches_real_file(row):
    """実体ファイルが samples/ に存在し、ファイル名・サイズが配置表と一致。"""
    path = REPO_ROOT / row["置き場のパス"]
    assert path.exists(), f"実体が無い: {row['置き場のパス']}"
    assert path.name == row["ファイル名"]
    assert path.stat().st_size == int(row["サイズ（バイト数）"]), (
        f"{row['ID']}: サイズ不一致 配置表={row['サイズ（バイト数）']} "
        f"実体={path.stat().st_size}"
    )
    assert row["原本保持状況"] == "実体を同梱"


def test_no_row_relies_only_on_external_path_or_temporary_url():
    """外部パスや一時URLへのリンクのみで配置を済ませた行が0件（⑩）。"""
    for r in _rows():
        assert r["置き場のパス"].startswith("storage-spec/samples/")
        assert "http" not in r["置き場のパス"]


def test_samples_have_no_real_names_or_credentials():
    """サンプル実体に実在の個人名・認証情報らしき文字列が含まれない。"""
    patterns = [
        r"[\w.+-]+@[\w-]+\.[\w.]+",
        r"(password|api[_-]?key|secret)\s*[:=]",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    ]
    for path in sorted((REPO_ROOT / "storage-spec" / "samples").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for pat in patterns:
            hits = [h for h in re.findall(pat, text, flags=re.IGNORECASE)
                    if "example.invalid" not in str(h)]
            assert not hits, f"{path.name}: {pat} にヒット {hits}"
        # 会社名は全て架空の「株式会社サンプル○○」形式
        m = re.search(r"^会社名: (.+)$", text, flags=re.MULTILINE)
        assert m and m.group(1).startswith("株式会社サンプル"), path.name
        # URLは架空ドメインのみ
        urls = re.findall(r"https?://\S+", text)
        assert all(u.startswith("https://example.invalid/") for u in urls), (
            f"{path.name}: 架空ドメイン以外のURL {urls}")
