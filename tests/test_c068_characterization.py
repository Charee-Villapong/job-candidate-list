"""C-068（候補10・テストの薄い領域）の特性テスト（現状固定）。

テスト係が仕様ドキュメント（docs/architecture.md）と公開インターフェース、
および現行コードの入出力の実測だけから書いた。後続のリファクタリング
（C-059 アダプタ共通化／C-060 run_collection 分割／C-061 from_dict 共通化／
C-063 候補 ID 一本化／C-067 sheets_gateway 分離）の安全網であり、
現行挙動が「正しい」ことの証明ではない。仕様と食い違う現行挙動も、
そのまま固定し、 docstring に「現行挙動」と明記する。

対象:
 1. sources/base.py の補助関数群（年収抽出・整形・HTML→テキスト・
    会社名解決・make_posting・fetch_json）― 従来テストゼロ
 2. ATS アダプタ 4 本（Greenhouse／Lever／Ashby／SmartRecruiters）の
    固定 JSON フィクスチャ → RawPosting 変換 ― 従来テストゼロ
 3. green_manual（手動専用アダプタ）
 4. sheets_gateway のサービス経路（C-067 の前提）― C-137 で削除（§4 は墓標）
 5. config.py の from_dict 異常系のクラス横断固定（C-061 の前提）
 6. C-063: 候補 ID の計算方法の統一（C-068 で食い違いを固定→
    C-063 の本人決定で設定側に統一→本ファイルで一致を固定）
 7. cli.py の入口（validate-config の終了コード・出力、argparse の挙動）

外部ネットワークには接続しない（fetch_json／urlopen／subprocess は
すべてモック・フェイクで切り分ける）。実シート・実レジストリ・
実設定ファイルには触れない。
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import urllib.error

import pytest
import yaml
from conftest import (
    build_app_config,
    build_raw_posting,
    enum_value,
    impl,
    impl_module,
    registry_rows,
    run_fake_collection,
)


def _entry(source_id: str):
    """レジストリ行フィクスチャから SourceEntry を作る。"""
    parse = impl("job_candidate_list.registry.parse_registry")
    return parse(registry_rows(source_id)).sources[0]


def _patch_fetch(monkeypatch, module_name: str, payload, record: list | None = None):
    """アダプタモジュール内の fetch_json を固定 payload で差し替える。"""

    def fake_fetch(url, timeout_sec=30):
        if record is not None:
            record.append((url, timeout_sec))
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr(impl_module(module_name), "fetch_json", fake_fetch)


# ---------------------------------------------------------------------------
# 1a. base: extract_jpy_salary（年収抽出）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        # 万円レンジ（区切りは 〜／～ の両方、空白有無を問わない）
        ("年収 600万円〜800万円", (6000000, 8000000, "600万円〜800万円")),
        ("年収600万円～800万円", (6000000, 8000000, "600万円～800万円")),
        # 単一額は min == max
        ("500万円", (5000000, 5000000, "500万円")),
        ("年収 1,000万円", (10000000, 10000000, "1,000万円")),
        ("年収 400万円以上", (4000000, 4000000, "400万円")),
        ("想定年収：700万円程度", (7000000, 7000000, "700万円")),
        # 小数万円
        ("年収 650.5万円〜720.25万円", (6505000, 7202500, "650.5万円〜720.25万円")),
        # 逆転レンジは小さい方を min に並べ替える（raw は原文のまま）
        ("800万円〜600万円", (6000000, 8000000, "800万円〜600万円")),
        # ¥ 表記レンジ
        ("¥6,000,000〜¥8,000,000", (6000000, 8000000, "¥6,000,000〜¥8,000,000")),
        # 現行挙動: 月給表記も年額換算せず原文の数値をそのまま使う
        ("月給 50万円", (500000, 500000, "50万円")),
    ],
)
def test_extract_jpy_salary_matches(text, expected):
    extract = impl("job_candidate_list.sources.base.extract_jpy_salary")
    assert extract(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "給与は応相談",
        "",
        None,
        # 現行挙動: 「万」なしの円表記（¥ なし）は抽出できない
        "6000000円〜8000000円",
        "年収 5,000,000円",
        "時給 2,000円",
        # 現行挙動: 「万円」でなく「万」で終わる表記は抽出できない
        "年収 600万〜800万",
    ],
)
def test_extract_jpy_salary_no_match_returns_none(text):
    extract = impl("job_candidate_list.sources.base.extract_jpy_salary")
    assert extract(text) is None


# ---------------------------------------------------------------------------
# 1b. base: format_salary_raw / resolve_company_name / html_to_text
# ---------------------------------------------------------------------------
def test_format_salary_raw_range_and_single():
    fmt = impl("job_candidate_list.sources.base.format_salary_raw")
    assert fmt(6000000, 8000000) == "600万円〜800万円"
    assert fmt(5000000, 5000000) == "500万円"


@pytest.mark.parametrize(
    "board,mapping,api_name,expected",
    [
        ("acme", None, None, "acme"),  # 対応表も API 名も無ければボード名
        ("acme", {"acme": "株式会社アクメ"}, None, "株式会社アクメ"),
        ("acme", None, "API Corp", "API Corp"),  # API 名がボード名より優先
        ("acme", {"acme": "Mapped"}, "API Corp", "Mapped"),  # 対応表が最優先
        ("acme", {}, "", "acme"),  # 空文字の API 名は無効としてボード名
        ("acme", {"other": "X"}, None, "acme"),  # 対応表に無いボードは素通り
    ],
)
def test_resolve_company_name_priority(board, mapping, api_name, expected):
    resolve = impl("job_candidate_list.sources.base.resolve_company_name")
    assert resolve(board, mapping, api_name) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("plain", "plain"),
        ("", ""),
        ("<p>Hello <b>World</b></p>", "Hello\nWorld"),  # タグは改行に置換
        ("<p>a</p><p>b</p>", "a\nb"),
        ("a<br>b", "a\nb"),
        ("<ul><li>x</li><li>y</li></ul>", "x\ny"),
        ("<script>var x=1;</script>text", "var x=1;\ntext"),  # 中身は残る
        ("<p>  spaces   kept? </p>", "spaces   kept?"),  # 前後空白は除去
        ("line1\nline2", "line1\nline2"),
        # 現行挙動: エンティティを実体化してからタグとして除去するため
        # &lt;tag&gt; は本文に残らない
        ("&lt;tag&gt; &amp; &quot;q&quot;", '& "q"'),
    ],
)
def test_html_to_text_current_behavior(raw, expected):
    html_to_text = impl("job_candidate_list.sources.base.html_to_text")
    assert html_to_text(raw) == expected


# ---------------------------------------------------------------------------
# 1c. base: make_posting（RawPosting の組み立て）
# ---------------------------------------------------------------------------
def test_make_posting_basic_sections_and_defaults():
    make = impl("job_candidate_list.sources.base.make_posting")
    models = impl("job_candidate_list.models")
    entry = _entry("サンプル情報源A")
    p = make(
        entry,
        models.SourceType("公開求人"),
        "https://example.invalid/j/1",
        "株式会社サンプル",
        "PM職",
        description="業務内容です。",
        location_raw="サンプル甲市",
        employment_raw="正社員",
    )
    assert p.source_id == "サンプル情報源A"  # source_id は SourceEntry 由来
    assert enum_value(p.source_type) == "公開求人"
    # description は「業務内容」セクションに、location_raw は「勤務地」に
    assert p.sections == {"業務内容": "業務内容です。", "勤務地": "サンプル甲市"}
    assert p.employment_raw == "正社員"
    assert p.salary_min_jpy is None and p.salary_raw is None
    assert p.remote_raw is None and p.company_overview_raw is None
    assert p.fetched_on == dt.date.today()  # 取得日は実行日


def test_make_posting_empty_description_and_location_only():
    make = impl("job_candidate_list.sources.base.make_posting")
    models = impl("job_candidate_list.models")
    entry = _entry("サンプル情報源A")
    p = make(entry, models.SourceType("公開求人"), "u", "c", "pos")
    assert p.sections == {}  # 説明も勤務地も無ければセクションは空
    p2 = make(entry, models.SourceType("公開求人"), "u", "c", "pos", location_raw="L")
    assert p2.sections == {"勤務地": "L"}


def test_make_posting_extra_sections_append_to_description():
    """現行挙動: extra_sections の「業務内容」は上書きでなく末尾に連結される。"""
    make = impl("job_candidate_list.sources.base.make_posting")
    models = impl("job_candidate_list.models")
    entry = _entry("サンプル情報源A")
    p = make(
        entry,
        models.SourceType("公開求人"),
        "u",
        "c",
        "pos",
        description="d",
        extra_sections={"業務内容": "override", "勤務地": "X"},
    )
    assert p.sections == {"業務内容": "d\noverride", "勤務地": "X"}
    # 説明文の前後空白は除去されない
    p2 = make(
        entry, models.SourceType("公開求人"), "u", "c", "pos", description="  pad  "
    )
    assert p2.sections == {"業務内容": "  pad  "}


# ---------------------------------------------------------------------------
# 1d. base: fetch_json（urlopen をモック。実ネットワークには出ない）
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_urlopen(monkeypatch, fake):
    base = impl_module("sources.base")
    monkeypatch.setattr(base.urllib.request, "urlopen", fake)


def test_fetch_json_sends_user_agent_and_timeout(monkeypatch):
    fetch_json = impl("job_candidate_list.sources.base.fetch_json")
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        seen["headers"] = dict(req.headers)
        return _FakeResponse(json.dumps({"jobs": [1, 2]}).encode())

    _patch_urlopen(monkeypatch, fake_urlopen)
    assert fetch_json("https://example.invalid/x", timeout_sec=7) == {"jobs": [1, 2]}
    assert seen["url"] == "https://example.invalid/x"
    assert seen["timeout"] == 7
    # 現行の User-Agent（候補7の直書き値。アダプタ共通）
    assert seen["headers"].get("User-agent") == "job-candidate-list/0.1"


def test_fetch_json_default_timeout_and_list_payload(monkeypatch):
    fetch_json = impl("job_candidate_list.sources.base.fetch_json")
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _FakeResponse(b"[1, 2]")

    _patch_urlopen(monkeypatch, fake_urlopen)
    assert fetch_json("https://example.invalid/x") == [1, 2]
    assert seen["timeout"] == 30  # 既定タイムアウト


@pytest.mark.parametrize(
    "failure",
    [
        "http403",
        "urlerror",
        "badjson",
    ],
)
def test_fetch_json_errors_become_source_fetch_error(monkeypatch, failure):
    """取得失敗・形式不正は SourceFetchError（pipeline がスキップする型）。"""
    fetch_json = impl("job_candidate_list.sources.base.fetch_json")
    errors = impl_module("errors")

    def fake_urlopen(req, timeout=None):
        if failure == "http403":
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", None, None)
        if failure == "urlerror":
            raise urllib.error.URLError("boom")
        return _FakeResponse(b"not json")

    _patch_urlopen(monkeypatch, fake_urlopen)
    with pytest.raises(errors.SourceFetchError):
        fetch_json("https://example.invalid/x")


# ---------------------------------------------------------------------------
# 2a. Greenhouse アダプタ
# ---------------------------------------------------------------------------
def _greenhouse_payload():
    return {
        "jobs": [
            {
                "id": 123,
                "title": "Product Manager",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
                "location": {"name": "Tokyo, Japan"},
                "content": "&lt;p&gt;Job &lt;b&gt;desc&lt;/b&gt;&lt;/p&gt;",
                "metadata": [{"name": "Employment Type", "value": "Full-time"}],
            },
            {
                "id": 124,
                "title": "Engineer",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/124",
                "location": {"name": "Remote"},
                "content": "plain text",
            },
        ]
    }


def test_greenhouse_fetch_maps_jobs(monkeypatch):
    adapter_cls = impl("job_candidate_list.sources.ats_greenhouse.GreenhouseAdapter")
    record: list = []
    _patch_fetch(monkeypatch, "sources.ats_greenhouse", _greenhouse_payload(), record)
    adapter = adapter_cls(
        ("acme",), timeout_sec=9, company_names={"acme": "株式会社アクメ"}
    )
    out = adapter.fetch(_entry("Greenhouse Job Board API"))
    # ボードごとに公式 API の一覧 URL を 1 回だけ叩く（timeout は伝播）
    assert record == [
        ("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true", 9)
    ]
    assert len(out) == 2
    first, second = out
    assert first.source_id == "Greenhouse Job Board API"
    assert enum_value(first.source_type) == "公開求人"
    assert first.company_raw == "株式会社アクメ"  # 対応表で解決
    assert first.position_raw == "Product Manager"
    assert first.raw_url == "https://boards.greenhouse.io/acme/jobs/123"
    assert first.location_raw == "Tokyo, Japan"
    # 雇用形態は metadata の name == "Employment Type" の値だけを見る
    assert first.employment_raw == "Full-time"
    assert second.employment_raw is None
    # content は HTML エンティティ実体化＋タグ除去されて本文になる
    assert first.sections == {"業務内容": "Job\ndesc", "勤務地": "Tokyo, Japan"}
    # 現行挙動: remote_raw には本文テキスト全体がそのまま入る
    assert first.remote_raw == "Job\ndesc"
    assert second.remote_raw == "plain text"


def test_greenhouse_salary_extracted_from_content(monkeypatch):
    adapter_cls = impl("job_candidate_list.sources.ats_greenhouse.GreenhouseAdapter")
    payload = {
        "jobs": [
            {
                "title": "T",
                "absolute_url": "https://x/1",
                "location": {"name": "Tokyo"},
                "content": "年収 600万円〜800万円 の求人",
            }
        ]
    }
    _patch_fetch(monkeypatch, "sources.ats_greenhouse", payload)
    out = adapter_cls(("acme",)).fetch(_entry("Greenhouse Job Board API"))
    assert (out[0].salary_min_jpy, out[0].salary_max_jpy) == (6000000, 8000000)
    assert out[0].salary_raw == "600万円〜800万円"


def test_greenhouse_sparse_job_and_empty_payloads(monkeypatch):
    adapter_cls = impl("job_candidate_list.sources.ats_greenhouse.GreenhouseAdapter")
    entry = _entry("Greenhouse Job Board API")
    _patch_fetch(
        monkeypatch,
        "sources.ats_greenhouse",
        {"jobs": [{"absolute_url": "https://x/9"}]},
    )
    out = adapter_cls(("acme",)).fetch(entry)
    assert len(out) == 1
    assert out[0].position_raw == "" and out[0].raw_url == "https://x/9"
    assert out[0].location_raw is None and out[0].sections == {}
    assert out[0].company_raw == "acme"  # 対応表なしではボード名が会社名
    for payload in [{"jobs": []}, {}, []]:
        _patch_fetch(monkeypatch, "sources.ats_greenhouse", payload)
        assert adapter_cls(("acme",)).fetch(entry) == []


def test_greenhouse_jobs_none_raises_type_error(monkeypatch):
    """現行挙動: jobs が None の場合は TypeError がそのまま伝播する
    （SourceFetchError には変換されない）。"""
    adapter_cls = impl("job_candidate_list.sources.ats_greenhouse.GreenhouseAdapter")
    _patch_fetch(monkeypatch, "sources.ats_greenhouse", {"jobs": None})
    with pytest.raises(TypeError):
        adapter_cls(("acme",)).fetch(_entry("Greenhouse Job Board API"))


def test_greenhouse_multiple_boards_in_order_and_error_propagates(monkeypatch):
    adapter_cls = impl("job_candidate_list.sources.ats_greenhouse.GreenhouseAdapter")
    record: list = []
    _patch_fetch(monkeypatch, "sources.ats_greenhouse", {"jobs": []}, record)
    adapter_cls(("b1", "b2")).fetch(_entry("Greenhouse Job Board API"))
    assert [u for u, _ in record] == [
        "https://boards-api.greenhouse.io/v1/boards/b1/jobs?content=true",
        "https://boards-api.greenhouse.io/v1/boards/b2/jobs?content=true",
    ]
    errors = impl_module("errors")
    _patch_fetch(monkeypatch, "sources.ats_greenhouse", errors.SourceFetchError("x"))
    with pytest.raises(errors.SourceFetchError):
        adapter_cls(("acme",)).fetch(_entry("Greenhouse Job Board API"))


def test_greenhouse_metadata_other_names_ignored(monkeypatch):
    adapter_cls = impl("job_candidate_list.sources.ats_greenhouse.GreenhouseAdapter")
    payload = {
        "jobs": [
            {
                "title": "T",
                "absolute_url": "https://x/1",
                "content": "desc",
                "metadata": [
                    {"name": "Salary", "value": "年収 500万円"},
                    {"name": "employment_type", "value": "X"},
                ],
            }
        ]
    }
    _patch_fetch(monkeypatch, "sources.ats_greenhouse", payload)
    out = adapter_cls(("acme",)).fetch(_entry("Greenhouse Job Board API"))
    assert out[0].employment_raw is None
    assert out[0].salary_min_jpy is None and out[0].salary_raw is None


# ---------------------------------------------------------------------------
# 2b. Lever アダプタ
# ---------------------------------------------------------------------------
def _lever_payload():
    return [
        {
            "id": "abc",
            "text": "Product Manager",
            "hostedUrl": "https://jobs.lever.co/acme/abc",
            "categories": {
                "location": "Tokyo",
                "commitment": "Full-time",
                "team": "Product",
            },
            "descriptionPlain": "年収 700万円〜900万円。フルリモート可。",
            "workplaceType": "remote",
        },
        {
            "id": "def",
            "text": "Engineer",
            "hostedUrl": "https://jobs.lever.co/acme/def",
            "categories": {"location": "Osaka", "commitment": "Contract"},
            "description": "<p>HTML desc</p>",
            "descriptionPlain": "plain desc",
        },
    ]


def test_lever_fetch_maps_postings(monkeypatch):
    adapter_cls = impl("job_candidate_list.sources.ats_lever.LeverAdapter")
    record: list = []
    _patch_fetch(monkeypatch, "sources.ats_lever", _lever_payload(), record)
    adapter = adapter_cls(
        ("acme",), timeout_sec=5, company_names={"acme": "株式会社アクメ"}
    )
    out = adapter.fetch(_entry("Lever Postings API"))
    assert record == [("https://api.lever.co/v0/postings/acme?mode=json", 5)]
    assert len(out) == 2
    first, second = out
    assert first.company_raw == "株式会社アクメ"
    assert first.position_raw == "Product Manager"
    assert first.raw_url == "https://jobs.lever.co/acme/abc"  # hostedUrl が実 URL
    assert first.location_raw == "Tokyo"  # categories.location
    assert first.employment_raw == "Full-time"  # categories.commitment
    assert (first.salary_min_jpy, first.salary_max_jpy) == (7000000, 9000000)
    assert first.salary_raw == "700万円〜900万円"
    assert first.sections == {
        "業務内容": "年収 700万円〜900万円。フルリモート可。",
        "勤務地": "Tokyo",
    }
    # 現行挙動: workplaceType がある場合は remote_raw の末尾に
    # "workplaceType: <値>" の行が付く
    assert (
        first.remote_raw
        == "年収 700万円〜900万円。フルリモート可。\nworkplaceType: remote"
    )
    # descriptionPlain が優先され、HTML の description は使われない
    assert second.sections == {"業務内容": "plain desc", "勤務地": "Osaka"}
    assert second.remote_raw == "plain desc"


def test_lever_html_only_description_is_ignored(monkeypatch):
    """現行挙動: descriptionPlain が無い求人は本文なしとして扱われる
    （description の HTML は読まれない）。"""
    adapter_cls = impl("job_candidate_list.sources.ats_lever.LeverAdapter")
    payload = [{"text": "T", "hostedUrl": "https://x/1", "description": "<p>HTML</p>"}]
    _patch_fetch(monkeypatch, "sources.ats_lever", payload)
    out = adapter_cls(("acme",)).fetch(_entry("Lever Postings API"))
    assert out[0].sections == {}
    assert out[0].remote_raw is None
    assert out[0].employment_raw is None and out[0].location_raw is None


def test_lever_lists_become_sections_and_salary_range_fallback(monkeypatch):
    adapter_cls = impl("job_candidate_list.sources.ats_lever.LeverAdapter")
    entry = _entry("Lever Postings API")
    payload = [
        {
            "text": "T",
            "hostedUrl": "https://x/1",
            "descriptionPlain": "d",
            "lists": [{"text": "Requirements", "content": "<li>a</li>"}],
        }
    ]
    _patch_fetch(monkeypatch, "sources.ats_lever", payload)
    out = adapter_cls(("acme",)).fetch(entry)
    # lists は本文末尾に連結され、見出し名でもセクション化される
    assert out[0].sections == {"業務内容": "d\na", "Requirements": "a"}
    # 本文に年収が無い場合は salaryRange オブジェクトを使う
    payload2 = [
        {
            "text": "T",
            "hostedUrl": "https://x/1",
            "descriptionPlain": "d",
            "salaryRange": {"min": 6000000, "max": 8000000, "currency": "JPY"},
        }
    ]
    _patch_fetch(monkeypatch, "sources.ats_lever", payload2)
    out2 = adapter_cls(("acme",)).fetch(entry)
    assert (out2[0].salary_min_jpy, out2[0].salary_max_jpy) == (6000000, 8000000)
    assert out2[0].salary_raw == "600万円〜800万円"
    # 現行挙動: salaryRange は currency が "JPY" の時だけ採用される
    # （通貨なし・USD は無視。interval は見ない）
    for sr in [
        {"min": 6000000, "max": 8000000},
        {"min": 6000000, "max": 8000000, "currency": "USD", "interval": "year"},
        {"max": 8000000, "currency": "JPY", "interval": "year"},  # min 欠落も無視
    ]:
        _patch_fetch(
            monkeypatch,
            "sources.ats_lever",
            [
                {
                    "text": "T",
                    "hostedUrl": "https://x/1",
                    "descriptionPlain": "d",
                    "salaryRange": sr,
                }
            ],
        )
        out_sr = adapter_cls(("acme",)).fetch(entry)
        assert out_sr[0].salary_min_jpy is None and out_sr[0].salary_raw is None
    # min のみの JPY は min == max として採用される
    _patch_fetch(
        monkeypatch,
        "sources.ats_lever",
        [
            {
                "text": "T",
                "hostedUrl": "https://x/1",
                "descriptionPlain": "d",
                "salaryRange": {"min": 6000000, "currency": "JPY", "interval": "month"},
            }
        ],
    )
    out_min = adapter_cls(("acme",)).fetch(entry)
    assert (out_min[0].salary_min_jpy, out_min[0].salary_max_jpy) == (6000000, 6000000)
    # 本文の年収と salaryRange が両方あれば本文側が勝つ
    payload3 = [
        {
            "text": "T",
            "hostedUrl": "https://x/1",
            "descriptionPlain": "年収 500万円〜600万円",
            "salaryRange": {"min": 7000000, "max": 9000000},
        }
    ]
    _patch_fetch(monkeypatch, "sources.ats_lever", payload3)
    out3 = adapter_cls(("acme",)).fetch(entry)
    assert (out3[0].salary_min_jpy, out3[0].salary_max_jpy) == (5000000, 6000000)


def test_lever_empty_and_non_list_payloads(monkeypatch):
    adapter_cls = impl("job_candidate_list.sources.ats_lever.LeverAdapter")
    entry = _entry("Lever Postings API")
    for payload in [[], {}, {"postings": []}, None]:
        _patch_fetch(monkeypatch, "sources.ats_lever", payload)
        assert adapter_cls(("acme",)).fetch(entry) == []
    _patch_fetch(monkeypatch, "sources.ats_lever", [{"descriptionPlain": "d"}])
    out = adapter_cls(("acme",)).fetch(entry)
    assert out[0].position_raw == "" and out[0].raw_url == ""
    assert out[0].company_raw == "acme"


# ---------------------------------------------------------------------------
# 2c. Ashby アダプタ
# ---------------------------------------------------------------------------
def _ashby_payload():
    return {
        "jobs": [
            {
                "id": "j1",
                "title": "Product Manager",
                "jobUrl": "https://jobs.ashbyhq.com/acme/j1",
                "location": "Tokyo",
                "secondaryLocations": [{"location": "Osaka"}],
                "employmentType": "FullTime",
                "descriptionPlain": "年収 800万円〜1000万円",
                "descriptionHtml": "<p>年収 800万円〜1000万円</p>",
                "isListed": True,
                "workplaceType": "Hybrid",
            },
            {
                "id": "j2",
                "title": "Secret Role",
                "jobUrl": "https://jobs.ashbyhq.com/acme/j2",
                "location": "Remote",
                "descriptionPlain": "desc2",
                "isListed": False,
            },
            {
                "id": "j3",
                "title": "NoListedFlag",
                "jobUrl": "https://jobs.ashbyhq.com/acme/j3",
                "location": "Kyoto",
                "descriptionPlain": "desc3",
            },
        ]
    }


def test_ashby_fetch_maps_jobs_and_skips_unlisted(monkeypatch):
    adapter_cls = impl("job_candidate_list.sources.ats_ashby.AshbyAdapter")
    record: list = []
    _patch_fetch(monkeypatch, "sources.ats_ashby", _ashby_payload(), record)
    adapter = adapter_cls(
        ("acme",), timeout_sec=3, company_names={"acme": "株式会社アクメ"}
    )
    out = adapter.fetch(_entry("Ashby Job Postings API"))
    assert record == [
        (
            "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true",
            3,
        )
    ]
    # isListed=False は対象外（仕様 §9.4 の注記どおり）。欠落は対象に含む
    assert [p.position_raw for p in out] == ["Product Manager", "NoListedFlag"]
    first = out[0]
    assert first.company_raw == "株式会社アクメ"
    assert first.raw_url == "https://jobs.ashbyhq.com/acme/j1"  # jobUrl
    assert first.employment_raw == "FullTime"
    # 副勤務地は " / " で連結される
    assert first.location_raw == "Tokyo / Osaka"
    assert (first.salary_min_jpy, first.salary_max_jpy) == (8000000, 10000000)
    assert first.salary_raw == "800万円〜1000万円"
    # Lever と同じく workplaceType 行が remote_raw に付く
    assert first.remote_raw == "年収 800万円〜1000万円\nworkplaceType: Hybrid"
    assert out[1].employment_raw is None and out[1].remote_raw == "desc3"


def test_ashby_location_composition_edge_cases(monkeypatch):
    adapter_cls = impl("job_candidate_list.sources.ats_ashby.AshbyAdapter")
    entry = _entry("Ashby Job Postings API")
    payload = {
        "jobs": [
            {
                "title": "T",
                "jobUrl": "https://x/1",
                "location": None,
                "secondaryLocations": [{"location": "Osaka"}, {"foo": 1}, {}],
            }
        ]
    }
    _patch_fetch(monkeypatch, "sources.ats_ashby", payload)
    out = adapter_cls(("acme",)).fetch(entry)
    assert out[0].location_raw == "Osaka"  # location なし・不正要素は読み飛ばす
    payload2 = {
        "jobs": [
            {
                "title": "T",
                "jobUrl": "https://x/1",
                "location": "Tokyo",
                "secondaryLocations": [],
            }
        ]
    }
    _patch_fetch(monkeypatch, "sources.ats_ashby", payload2)
    assert adapter_cls(("acme",)).fetch(entry)[0].location_raw == "Tokyo"


def test_ashby_html_only_and_compensation_components_ignored(monkeypatch):
    """現行挙動: descriptionHtml のみでは本文なし。compensation の
    summaryComponents（構造化された金額）も年収には反映されない。"""
    adapter_cls = impl("job_candidate_list.sources.ats_ashby.AshbyAdapter")
    entry = _entry("Ashby Job Postings API")
    payload = {
        "jobs": [
            {
                "title": "T",
                "jobUrl": "https://x/1",
                "descriptionHtml": "<p>HTML only</p>",
            }
        ]
    }
    _patch_fetch(monkeypatch, "sources.ats_ashby", payload)
    out = adapter_cls(("acme",)).fetch(entry)
    assert out[0].sections == {} and out[0].remote_raw is None
    payload2 = {
        "jobs": [
            {
                "title": "T",
                "jobUrl": "https://x/1",
                "descriptionPlain": "d",
                "compensation": {
                    "summaryComponents": [
                        {"compensationType": "Salary", "minValue": 600, "maxValue": 800}
                    ]
                },
            }
        ]
    }
    _patch_fetch(monkeypatch, "sources.ats_ashby", payload2)
    out2 = adapter_cls(("acme",)).fetch(entry)
    assert out2[0].salary_min_jpy is None and out2[0].salary_raw is None


def test_ashby_empty_payloads_and_jobs_none(monkeypatch):
    adapter_cls = impl("job_candidate_list.sources.ats_ashby.AshbyAdapter")
    entry = _entry("Ashby Job Postings API")
    for payload in [{"jobs": []}, {}, [], None]:
        _patch_fetch(monkeypatch, "sources.ats_ashby", payload)
        assert adapter_cls(("acme",)).fetch(entry) == []
    # 現行挙動: Greenhouse と同じく jobs=None は TypeError が伝播する
    _patch_fetch(monkeypatch, "sources.ats_ashby", {"jobs": None})
    with pytest.raises(TypeError):
        adapter_cls(("acme",)).fetch(entry)


# ---------------------------------------------------------------------------
# 2d. SmartRecruiters アダプタ
# ---------------------------------------------------------------------------
def _smartrecruiters_listing():
    return {
        "totalFound": 1,
        "content": [
            {
                "id": "p1",
                "name": "Product Manager",
                "location": {"city": "Tokyo", "region": "Tokyo", "country": "Japan"},
                "typeOfEmployment": {"label": "Full-time"},
                "industry": {"label": "Software"},
                "department": {"label": "Product"},
            }
        ],
    }


def test_smartrecruiters_fetch_maps_listing_only(monkeypatch):
    """現行挙動: 一覧 API のみを使い、求人ごとの詳細取得は行われない
    （仕様 §9.4 の注記「詳細取得は求人単位で行う」とは食い違う現行実装。
    C-059 で詳細取得を足す場合はこのテストが検知する）。"""
    adapter_cls = impl(
        "job_candidate_list.sources.ats_smartrecruiters.SmartRecruitersAdapter"
    )
    record: list = []
    _patch_fetch(
        monkeypatch, "sources.ats_smartrecruiters", _smartrecruiters_listing(), record
    )
    adapter = adapter_cls(
        ("acme",), timeout_sec=4, company_names={"acme": "株式会社アクメ"}
    )
    out = adapter.fetch(_entry("SmartRecruiters Posting API"))
    assert record == [("https://api.smartrecruiters.com/v1/companies/acme/postings", 4)]
    assert len(out) == 1
    p = out[0]
    assert p.company_raw == "株式会社アクメ"
    assert p.position_raw == "Product Manager"
    # 実 URL は一覧の id から組み立てる
    assert p.raw_url == "https://jobs.smartrecruiters.com/acme/p1"
    assert p.employment_raw == "Full-time"  # typeOfEmployment.label
    assert p.location_raw == "Tokyo, Tokyo, Japan"  # city, region, country の連結
    # 一覧に本文が無いため、セクションは勤務地のみ・年収は空
    assert p.sections == {"勤務地": "Tokyo, Tokyo, Japan"}
    assert p.salary_min_jpy is None and p.salary_raw is None
    assert p.remote_raw is None


def test_smartrecruiters_location_and_employment_variants(monkeypatch):
    adapter_cls = impl(
        "job_candidate_list.sources.ats_smartrecruiters.SmartRecruitersAdapter"
    )
    entry = _entry("SmartRecruiters Posting API")

    def fetch_one(item):
        _patch_fetch(monkeypatch, "sources.ats_smartrecruiters", {"content": [item]})
        return adapter_cls(("acme",)).fetch(entry)[0]

    base = {"id": "p1", "name": "PM", "typeOfEmployment": {"label": "Full-time"}}
    # remote / hybrid フラグは現行挙動として無視される
    item = dict(base, location={"city": "A", "remote": True, "hybrid": True})
    p = fetch_one(item)
    assert p.location_raw == "A" and p.remote_raw is None
    # country のみでも連結できる
    item = dict(base, location={"country": "Japan"})
    assert fetch_one(item).location_raw == "Japan"
    # typeOfEmployment が文字列でもそのまま使う
    item = dict(base, typeOfEmployment="Contract")
    assert fetch_one(item).employment_raw == "Contract"
    # id が無ければ URL はボードまでの末尾スラッシュで終わる
    item = {"name": "NoId"}
    p = fetch_one(item)
    assert p.raw_url == "https://jobs.smartrecruiters.com/acme/"
    assert p.position_raw == "NoId"


def test_smartrecruiters_empty_payloads_and_content_none(monkeypatch):
    adapter_cls = impl(
        "job_candidate_list.sources.ats_smartrecruiters.SmartRecruitersAdapter"
    )
    entry = _entry("SmartRecruiters Posting API")
    for payload in [{"content": []}, {}, [], None, {"totalFound": 0}]:
        _patch_fetch(monkeypatch, "sources.ats_smartrecruiters", payload)
        assert adapter_cls(("acme",)).fetch(entry) == []
    # 現行挙動: content=None は TypeError が伝播する
    _patch_fetch(monkeypatch, "sources.ats_smartrecruiters", {"content": None})
    with pytest.raises(TypeError):
        adapter_cls(("acme",)).fetch(entry)


# ---------------------------------------------------------------------------
# 3. green_manual（手動収集のみ）
# ---------------------------------------------------------------------------
def test_green_manual_always_raises_source_fetch_error():
    """Green は手動・低頻度のみの条件付き対象（仕様 §9.2）。自動取得は
    SourceFetchError で pipeline にスキップさせる現行挙動を固定する。"""
    adapter_cls = impl("job_candidate_list.sources.green_manual.GreenManualAdapter")
    errors = impl_module("errors")
    with pytest.raises(errors.SourceFetchError) as excinfo:
        adapter_cls().fetch(_entry("Green"))
    assert "手動収集のみ" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 4. sheets_gateway のサービス経路（削除済み ― 墓標）
# ---------------------------------------------------------------------------
# C-137 の仕様変更に伴う削除。本人の決定で GoogleApiSheetsBackend の
# サービス経路を削除し CLI 経路に一本化した（実装はコミット fb7409e で
# コンストラクタが (spreadsheet_id: str) に確定）。これに伴い、本節に
# あったサービス経路テスト 4 件を削除した。削除したテスト名と、各テスト
# が固定していた挙動の要約は次のとおり:
# - test_service_backend_read_rows_uses_service:
#     サービス経由の read_rows が spreadsheets.values.get を
#     spreadsheetId／range 指定で呼び、返ってきた values をそのまま返す。
# - test_service_backend_read_links_always_empty:
#     サービス経路の read_links は、リンク付きセルでも常に空 dict を返す。
# - test_service_backend_write_rows_ignores_service_and_uses_cli:
#     service があっても write_rows はサービス経路を使わず CLI 経路
#     （subprocess）へ行き、サービスには一切触れない。
# - test_gateway_read_candidates_with_service_backend_fails_on_links:
#     サービス経路では read_links が空のため、URL 列を持つ候補行の
#     読み戻しは ValueError（実 URL が読み取れない）になる。
# あわせて、サービス経路専用のフェイク 3 クラス
# （_FakeExecuteRequest／_FakeValuesResource／_FakeSheetsService）も除去した。
# 後継の固定先（CLI 経路の特性はそちらで固定する）:
# - tests/test_sheets_backend_cli.py:
#     test_candidate_roundtrip_via_cli／test_tabs_are_independent／
#     test_read_links_via_cli_returns_real_urls／
#     test_bulk_upsert_writes_all_rows
# - tests/test_c137_characterization.py:
#     test_cli_read_rows_and_registry_rows_return_seeded_rows_verbatim／
#     test_cli_read_links_on_exclusions_tab_maps_only_formula_cells／
#     test_cli_read_candidates_restores_both_link_columns_and_openwork／
#     test_cli_write_rows_single_append_call_with_exact_payload_and_range／
#     test_cli_write_rows_empty_rows_makes_no_cli_call／
#     test_cli_append_exclusions_serializes_row_as_single_append／
#     test_backend_full_flow_completes_entirely_via_cli


# ---------------------------------------------------------------------------
# 5. config.py from_dict 異常系（クラス横断。C-061 の前提）
#    正常系・反映系は test_config_and_rules.py が担当する。
#
#    本セクションの ProfileConfig 入力 dict に残る profile_version /
#    target_role_labels は削除済みキーである。読み捨て仕様の下では
#    有効な入力として残せること、本セクションの固定意図は他のキーの
#    未知キー・必須欠落・型不正のエラー挙動であり削除済みキーの
#    有無に依存しないことから、入力として残す。削除済みキー自体の
#    読み捨てと属性の不存在は test_c066_characterization.py が固定
#    する（本セクションに残すことで、読み捨て対象を含む入力でも
#    他キーのエラーが従来どおり観測できることも兼ねる）。
# ---------------------------------------------------------------------------
_CONFIG_CLASSES = [
    (
        "ProfileConfig",
        {
            "profile_version": 1,
            "target_domains": [],
            "target_role_labels": [],
            "salary_target_jpy": 1,
            "employment_allowed": [],
        },
    ),
    (
        "DutyConfig",
        {"target_sections": [], "upstream_keywords": [], "impl_keywords": []},
    ),
    ("LocationConfig", {"target_sections": []}),
    ("RemoteConfig", {"rules": []}),
    ("EnglishConfig", {"target_sections": []}),
    ("HardConfig", {}),
    ("FitnessConfig", {"salary_check_threshold_jpy": 1, "high_threshold": 70}),
    ("PresentationConfig", {}),
    ("ValidityConfig", {"listing_valid_days": 14}),
    (
        "CollectionConfig",
        {"request_interval_sec": 1, "per_source_max_items": 2, "timeout_sec": 3},
    ),
    ("GuardConfig", {"max_close_ratio": 0.5, "max_total_change_ratio": 1.0}),
    ("OpenworkConfig", {}),
    ("AutoApplyConfig", {}),
    ("NormalizeConfig", {}),
    ("LocalConfig", {}),
    ("SheetNamesConfig", {}),
]


@pytest.mark.parametrize("cls_name,good", _CONFIG_CLASSES)
def test_from_dict_unknown_key_is_config_error(cls_name, good):
    cfg_mod = impl_module("config")
    errors = impl_module("errors")
    cls = getattr(cfg_mod, cls_name)
    data = dict(good)
    data["__unknown_key__"] = 1
    with pytest.raises(errors.ConfigError) as excinfo:
        cls.from_dict(data)
    assert "未知のキー" in str(excinfo.value)
    assert "__unknown_key__" in str(excinfo.value)


@pytest.mark.parametrize(
    "cls_name,good,missing",
    [
        (
            "ProfileConfig",
            {
                "profile_version": 1,
                "target_domains": [],
                "target_role_labels": [],
                "salary_target_jpy": 1,
                "employment_allowed": [],
            },
            "salary_target_jpy",
        ),
        (
            "DutyConfig",
            {"target_sections": [], "upstream_keywords": [], "impl_keywords": []},
            "target_sections",
        ),
        ("LocationConfig", {"target_sections": []}, "target_sections"),
        ("EnglishConfig", {"target_sections": []}, "target_sections"),
        (
            "FitnessConfig",
            {"salary_check_threshold_jpy": 1, "high_threshold": 70},
            "salary_check_threshold_jpy",
        ),
        ("ValidityConfig", {"listing_valid_days": 14}, "listing_valid_days"),
        (
            "CollectionConfig",
            {"request_interval_sec": 1, "per_source_max_items": 2, "timeout_sec": 3},
            "timeout_sec",
        ),
        (
            "GuardConfig",
            {"max_close_ratio": 0.5, "max_total_change_ratio": 1.0},
            "max_close_ratio",
        ),
    ],
)
def test_from_dict_missing_required_key_is_config_error(cls_name, good, missing):
    cfg_mod = impl_module("config")
    errors = impl_module("errors")
    cls = getattr(cfg_mod, cls_name)
    data = dict(good)
    del data[missing]
    with pytest.raises(errors.ConfigError) as excinfo:
        cls.from_dict(data)
    assert "必須キーがありません" in str(excinfo.value)
    assert missing in str(excinfo.value)


@pytest.mark.parametrize(
    "cls_name,data,key",
    [
        # str を list 項目へ
        (
            "DutyConfig",
            {"target_sections": "勤務地", "upstream_keywords": [], "impl_keywords": []},
            "target_sections",
        ),
        (
            "ProfileConfig",
            {
                "profile_version": 1,
                "target_domains": "AI",
                "target_role_labels": [],
                "salary_target_jpy": 1,
                "employment_allowed": [],
            },
            "target_domains",
        ),
        # str を int 項目へ
        (
            "ProfileConfig",
            {
                "profile_version": 1,
                "target_domains": [],
                "target_role_labels": [],
                "salary_target_jpy": "800",
                "employment_allowed": [],
            },
            "salary_target_jpy",
        ),
        ("ValidityConfig", {"listing_valid_days": "14"}, "listing_valid_days"),
        # 現行挙動: bool は int 項目として受理されない（bool は int の
        # サブクラスだが型不正として弾く）
        (
            "ProfileConfig",
            {
                "profile_version": 1,
                "target_domains": [],
                "target_role_labels": [],
                "salary_target_jpy": True,
                "employment_allowed": [],
            },
            "salary_target_jpy",
        ),
        # float も int 項目として受理されない
        (
            "ProfileConfig",
            {
                "profile_version": 1,
                "target_domains": [],
                "target_role_labels": [],
                "salary_target_jpy": 1.5,
                "employment_allowed": [],
            },
            "salary_target_jpy",
        ),
        # str を float 項目へ
        (
            "GuardConfig",
            {"max_close_ratio": "0.5", "max_total_change_ratio": 1.0},
            "max_close_ratio",
        ),
        # str を bool 項目へ
        (
            "OpenworkConfig",
            {"weekly_once_per_company": "yes"},
            "weekly_once_per_company",
        ),
        ("AutoApplyConfig", {"enabled": "true"}, "enabled"),
        # int を str 項目へ
        ("SheetNamesConfig", {"candidates": 3}, "candidates"),
        # rules が list でない
        ("RemoteConfig", {"rules": "x"}, "rules"),
    ],
)
def test_from_dict_wrong_type_is_config_error(cls_name, data, key):
    cfg_mod = impl_module("config")
    errors = impl_module("errors")
    cls = getattr(cfg_mod, cls_name)
    with pytest.raises(errors.ConfigError) as excinfo:
        cls.from_dict(data)
    assert "型が不正" in str(excinfo.value)
    assert key in str(excinfo.value)


def test_from_dict_closed_value_ranges():
    """閉じた値の範囲外は from_dict 時点で ConfigError（仕様 A2.2）。"""
    cfg_mod = impl_module("config")
    errors = impl_module("errors")
    with pytest.raises(errors.ConfigError) as excinfo:
        cfg_mod.DutyConfig.from_dict(
            {
                "target_sections": [],
                "upstream_keywords": [],
                "impl_keywords": [],
                "mixed_handling": "ignore",
            }
        )
    assert "範囲外" in str(excinfo.value) and "mixed_handling" in str(excinfo.value)
    with pytest.raises(errors.ConfigError):
        cfg_mod.RemoteRule.from_dict({"pattern": "x", "division": "存在しない区分"})
    # RemoteRule の必須欠落・未知キーも ConfigError
    with pytest.raises(errors.ConfigError):
        cfg_mod.RemoteRule.from_dict({"division": "不明"})
    with pytest.raises(errors.ConfigError):
        cfg_mod.RemoteConfig.from_dict(
            {"rules": [{"pattern": "x", "division": "不明", "extra": 1}]}
        )


def test_from_dict_list_becomes_tuple_and_defaults():
    cfg_mod = impl_module("config")
    duty = cfg_mod.DutyConfig.from_dict(
        {"target_sections": ["a", "b"], "upstream_keywords": ["x"], "impl_keywords": []}
    )
    assert duty.target_sections == ("a", "b")  # YAML の list は tuple で保持
    assert duty.mixed_handling == "check"  # 既定値
    # 全項目が任意のクラスは空 dict で構築できる
    assert cfg_mod.HardConfig.from_dict({}).non_regular_keywords == ()
    assert cfg_mod.PresentationConfig.from_dict({}).weekly_limit == 20
    assert cfg_mod.AutoApplyConfig.from_dict({}).enabled is False
    # int が必要な float 項目には int も受理される（1 → 1.0）
    guard = cfg_mod.GuardConfig.from_dict(
        {"max_close_ratio": 1, "max_total_change_ratio": 1}
    )
    assert guard.max_close_ratio == 1.0
    # LocalConfig: sheet_names の部分指定は残りが既定値で補われる
    local = cfg_mod.LocalConfig.from_dict({"sheet_names": {"candidates": "X"}})
    assert local.sheet_names.candidates == "X"
    assert local.sheet_names.registry == "情報源レジストリ"
    # ats_boards の list も tuple 化される
    local2 = cfg_mod.LocalConfig.from_dict({"ats_boards": {"greenhouse": ["b1", "b2"]}})
    assert local2.ats_boards["greenhouse"] == ("b1", "b2")
    # ProfileConfig: last_updated は文字列でも date として受理される
    profile = cfg_mod.ProfileConfig.from_dict(
        {
            "profile_version": 1,
            "target_domains": [],
            "target_role_labels": [],
            "salary_target_jpy": 1,
            "employment_allowed": [],
            "last_updated": "2026-11-05",
        }
    )
    assert profile.last_updated == dt.date(2026, 11, 5)


def test_from_dicts_backfills_fitness_from_profile(
    profile_config_dict, judge_config_dict
):
    """仕様 A2.2: FitnessConfig の未設定項目は profile 側から補われる。"""
    cfg = build_app_config(profile_config_dict, judge_config_dict)
    assert cfg.fitness.salary_target_jpy == profile_config_dict["salary_target_jpy"]
    assert cfg.fitness.target_domains == tuple(profile_config_dict["target_domains"])
    # fitness 側に明示値があればそちらが勝つ
    judge2 = copy.deepcopy(judge_config_dict)
    judge2["fitness"]["salary_target_jpy"] = 123
    cfg2 = build_app_config(profile_config_dict, judge2)
    assert cfg2.fitness.salary_target_jpy == 123


# ---------------------------------------------------------------------------
# 6. C-063: 候補 ID の計算方法の統一（確定挙動の固定）
# ---------------------------------------------------------------------------
def test_candidate_id_unified_between_record_and_presentation_key(
    profile_config_dict, judge_config_dict
):
    """確定挙動（C-063 で統一）: 設定の tracking_params で追加した
    パラメータは、レコードの candidate_id でも提示順キーに埋め込まれる
    ID でも同じく除去され、両 ID が一致する。

    経緯: C-068 では当初、提示順キー側が既定の除去リストで再計算する
    ため両 ID が食い違う現行挙動を固定していた（旧テスト名
    test_candidate_id_diverges_between_record_and_presentation_key）。
    その後 C-063 の本人決定で「設定を使う側に統一（提示順キーも
    設定の tracking_params で計算する）」となり、旧テストは意図的に
    落ちて検知した。本更新では同じ構成（tracking_params =
    ["utm_source", "session"]）のまま assert を一致側に切り替え、
    統一後の確定挙動を固定する。"""
    judge = copy.deepcopy(judge_config_dict)
    judge["normalize"]["tracking_params"] = ["utm_source", "session"]
    posting = build_raw_posting(
        raw_url="https://example.invalid/j/1?session=abc&utm_source=x"
    )
    summary, gateway = run_fake_collection(
        profile_config_dict, judge, postings=[posting]
    )
    assert len(gateway.upserted) == 1
    record = gateway.upserted[0]
    assert summary.added_ids == (record.candidate_id,)
    assert record.presentation_key is not None
    embedded_id = record.presentation_key[3]
    assert embedded_id == record.candidate_id  # C-063 で統一された一致


def test_candidate_id_agrees_when_no_custom_tracking_param(
    profile_config_dict, judge_config_dict
):
    """対照: 既定の除去リストで消えるパラメータだけの URL では両 ID は一致する。"""
    posting = build_raw_posting(raw_url="https://example.invalid/j/1?utm_source=x")
    summary, gateway = run_fake_collection(
        profile_config_dict, judge_config_dict, postings=[posting]
    )
    record = gateway.upserted[0]
    assert record.presentation_key is not None
    assert record.presentation_key[3] == record.candidate_id


# ---------------------------------------------------------------------------
# 7. cli.py の入口（validate-config と argparse の表面のみ。
#    collect／dry-run／validate-registry は実 Sheets 境界に触れるため対象外）
# ---------------------------------------------------------------------------
def _write_config_dir(tmp_path, profile_data, judge_data, local_data):
    (tmp_path / "profile.local.yaml").write_text(
        yaml.safe_dump(profile_data, allow_unicode=True), encoding="utf-8"
    )
    (tmp_path / "judge.local.yaml").write_text(
        yaml.safe_dump(judge_data, allow_unicode=True), encoding="utf-8"
    )
    (tmp_path / "local.local.yaml").write_text(
        yaml.safe_dump(local_data, allow_unicode=True), encoding="utf-8"
    )


def test_cli_validate_config_ok(
    tmp_path, capsys, profile_config_dict, judge_config_dict, local_config_dict
):
    main = impl("job_candidate_list.cli.main")
    _write_config_dir(
        tmp_path, profile_config_dict, judge_config_dict, local_config_dict
    )
    # --config-dir はサブコマンドより前に置く（現行の argparse 構成）
    rc = main(["--config-dir", str(tmp_path), "validate-config"])
    assert rc == 0
    assert "設定は正常です" in capsys.readouterr().out


def test_cli_validate_config_problems_return_1(
    tmp_path, capsys, profile_config_dict, judge_config_dict, local_config_dict
):
    main = impl("job_candidate_list.cli.main")
    judge = copy.deepcopy(judge_config_dict)
    judge["presentation"]["weekly_limit"] = 0  # validate_config が問題として返す値
    _write_config_dir(tmp_path, profile_config_dict, judge, local_config_dict)
    rc = main(["--config-dir", str(tmp_path), "validate-config"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "presentation.weekly_limit" in out
    assert "設定に問題があります" in out


def test_cli_validate_config_missing_files_raise_config_error(tmp_path):
    """現行挙動: 設定ファイルが無い場合、main は ConfigError を捕捉せず
    そのまま伝播させる（終了コードへの変換はされない）。"""
    main = impl("job_candidate_list.cli.main")
    errors = impl_module("errors")
    with pytest.raises(errors.ConfigError):
        main(["--config-dir", str(tmp_path), "validate-config"])


def test_cli_validate_config_invalid_value_raises_config_error(
    tmp_path, profile_config_dict, judge_config_dict, local_config_dict
):
    main = impl("job_candidate_list.cli.main")
    errors = impl_module("errors")
    judge = copy.deepcopy(judge_config_dict)
    judge["duty"]["mixed_handling"] = "ignore"
    _write_config_dir(tmp_path, profile_config_dict, judge, local_config_dict)
    with pytest.raises(errors.ConfigError):
        main(["--config-dir", str(tmp_path), "validate-config"])


def test_cli_argparse_surface():
    main = impl("job_candidate_list.cli.main")
    with pytest.raises(SystemExit) as no_args:
        main([])
    assert no_args.value.code == 2  # サブコマンド必須
    with pytest.raises(SystemExit) as bogus:
        main(["bogus-cmd"])
    assert bogus.value.code == 2
    with pytest.raises(SystemExit) as help_exit:
        main(["--help"])
    assert help_exit.value.code == 0
