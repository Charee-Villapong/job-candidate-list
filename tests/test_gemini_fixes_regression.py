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


# ---------------------------------------------------------------------------
# 項目3: applied_at（応募日時）の読み戻し
# ---------------------------------------------------------------------------
def test_parse_iso_datetime_formats():
    """ISO 形式とシート上の表記の両方を datetime として読み取れる。"""
    parse = impl("job_candidate_list.parsing.parse_iso_datetime")
    expected = dt.datetime(2026, 11, 13, 21, 39)
    assert parse("2026-11-13T21:39:00") == dt.datetime(2026, 11, 13, 21, 39, 0)
    assert parse("2026-11-13 21:39") == expected
    assert parse("2026-11-13 21:39:00") == dt.datetime(2026, 11, 13, 21, 39, 0)
    assert parse("2026/11/13 21:39") == expected
    assert parse("") is None
    assert parse("未応募") is None


def test_read_candidates_restores_applied_at():
    """応募日時のセル（bookkeep の表記）が applied_at に読み戻される。"""
    gateway, _backend = _seeded_gateway("継続", applied_text="2026-11-13 21:39")
    records = gateway.read_candidates()
    assert records[0].applied_at == dt.datetime(2026, 11, 13, 21, 39)


# ---------------------------------------------------------------------------
# 項目5: ひらがな市区町村名の抽出
# ---------------------------------------------------------------------------
def test_extract_municipalities_includes_hiragana_names():
    """「さいたま市」等のひらがな自治体名を抽出できる。

    抽出は前後の文字を巻き込むヒューリスティクスなので、文中の場合は
    自治体名で終わるマッチが得られること（修正前は抽出ゼロだった）を
    確認する。
    """
    extract = impl("job_candidate_list.judge_location._extract_municipalities")
    for name in ("さいたま市", "つくば市", "むつ市", "いわき市"):
        assert extract(name, set()) == [name]
        found = extract(f"勤務地は{name}です", set())
        assert found and found[0].endswith(name)


def test_judge_location_hiragana_city_outside_whitelist_is_unfit(
    judge_config_dict, location_whitelist_dict
):
    """対応表外のひらがな市区町村は「判定不能」でなく「不適合」になる。"""
    from conftest import build_location_whitelist, build_raw_posting

    judge = impl("job_candidate_list.judge_location.judge_location")
    location_config = impl("job_candidate_list.config.LocationConfig")
    remote_cls = impl("job_candidate_list.judge_remote.RemoteResult")
    posting = build_raw_posting(
        location_raw="さいたま市",
        sections={
            "業務内容": "データ活用推進の企画立案を主担当として担います。",
            "勤務地": "勤務地はさいたま市です。",
        },
    )
    remote = remote_cls(
        division="出社週2〜3日",
        rule_ids=["REMOTE-NORMALIZED"],
        raw_found="出社週2〜3日",
    )
    cfg = location_config.from_dict(judge_config_dict["location"])
    whitelist = build_location_whitelist(location_whitelist_dict)
    result = judge(posting, remote, whitelist, cfg)
    assert str(result.division) == "勤務地不適合"
    assert "LOC-OUT" in list(result.rule_ids)


# ---------------------------------------------------------------------------
# 項目7: 数式インジェクション無害化とリンクのスキーム制限
# ---------------------------------------------------------------------------
def test_record_to_row_neutralizes_formula_leading_text():
    """通常文字列セルの先頭が = + - @ の場合は ' を前置して無害化する。"""
    gw = impl("job_candidate_list.sheets_gateway")
    models = impl("job_candidate_list.models")
    record = models.CandidateRecord(
        candidate_id="cand_0000000000000001",
        company="=SUM(A1:A2)",
        position="+エンジニア",
        salary_range_text="-100万円",
        location_text="@東京",
        source_url=models.LinkCell("求人ページ", "https://example.invalid/j/1"),
        apply_result="=結果",
    )
    row = gw._record_to_row(record)
    auto = list(gw.AUTO_COLUMNS_CANDIDATE)
    assert row[auto.index("会社名")] == "'=SUM(A1:A2)"
    assert row[auto.index("ポジション名")] == "'+エンジニア"
    assert row[auto.index("想定年収レンジ")] == "'-100万円"
    assert row[auto.index("勤務地")] == "'@東京"
    assert row[auto.index("応募結果")] == "'=結果"
    # リンク列は設計どおり HYPERLINK 数式のまま
    assert row[auto.index("情報源URL")].startswith('=HYPERLINK("https://')


def test_text_cell_leaves_normal_text_untouched():
    """通常の文字列（先頭が対象記号でない）は変換しない。"""
    gw = impl("job_candidate_list.sheets_gateway")
    assert gw._text_cell("株式会社サンプル") == "株式会社サンプル"
    assert gw._text_cell("") == ""
    assert gw._text_cell(" =SUM(A1)") == "' =SUM(A1)"


def test_hyperlink_allows_only_http_and_https():
    """_hyperlink は http/https のみ数式化し、それ以外は空にする。"""
    gw = impl("job_candidate_list.sheets_gateway")
    models = impl("job_candidate_list.models")

    def link(url):
        return gw._hyperlink(models.LinkCell("求人ページ", url))

    assert link("https://example.invalid/j/1").startswith("=HYPERLINK(")
    assert link("http://example.invalid/j/1").startswith("=HYPERLINK(")
    assert link("javascript:alert(1)") == ""
    assert link("ftp://example.invalid/j/1") == ""
    assert link("example.invalid/j/1") == ""


def test_applied_at_survives_record_roundtrip():
    """_record_to_row → _row_to_record の往復で応募日時が消えない。"""
    gw = impl("job_candidate_list.sheets_gateway")
    models = impl("job_candidate_list.models")
    record = models.CandidateRecord(
        candidate_id="cand_0000000000000001",
        company="株式会社サンプル青葉",
        position="プロダクトマネージャー",
        source_url=models.LinkCell("求人ページ", "https://example.invalid/j/1"),
        applied_at=dt.datetime(2026, 11, 13, 21, 39, 5),
    )
    row = gw._record_to_row(record)
    auto = list(gw.AUTO_COLUMNS_CANDIDATE)
    links = {(0, auto.index("情報源URL")): "https://example.invalid/j/1"}
    restored = gw._row_to_record(row, links, 0)
    assert restored.applied_at == record.applied_at
