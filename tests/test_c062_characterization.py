"""C-062（候補4・小さな重複の散在（束））の特性テスト（現状固定）。

テスト係が実装モジュール本体を見ず、仕様ドキュメント
（docs/architecture.md／architecture-addendum.md）と既存テストが使う
公開インターフェース、および現行コードの入出力の実測だけから書いた。
リファクタリング（共通化）で挙動が変わったら検知する網であり、
現行挙動が「正しい」ことの証明ではない。

対象の小項目（refactoring-candidates-2026-11-07.md §8 候補4の分解）:
 1. _parse_date の2実装（registry の構造化セル／sheets のセル読取）
 2. 適合度順位表 _FITNESS_RANK の2定義（fitness／presentation）
 3. 文節分割ヘルパー（judge_duty の主担当節／judge_english の結びつき）
 4. 検索対象テキスト組み立て（judge_hard の会社概要系テキスト等）
 5. YAML 読込のエラー方針（load_config／load_whitelist／load_company_names）
 6. 年収の万円換算（evaluate のサンプル年収パース／pipeline の年収テキスト）
 7. シート名既定値（LocalConfig／GoogleSheetsGateway）
 8. リモート区分の文字列集合（RemoteDivision／RemoteRule 検証）
 9. openwork の3条件チェック（会社名・勤務地・業種の矛盾なし）
10. judge_location の「判定不能」結果構築
"""

from __future__ import annotations

import datetime as dt

import pytest
import yaml
from conftest import (
    FakeOpenworkProvider,
    build_app_config,
    build_candidate_record,
    build_location_whitelist,
    build_raw_posting,
    enum_value,
    impl,
    registry_rows,
    run_fake_collection,
)


# ---------------------------------------------------------------------------
# 1. _parse_date の2実装
# ---------------------------------------------------------------------------
def _registry_entry(measured=None, risk=None):
    rows = registry_rows()
    if measured is not None:
        rows[1][4] = measured
    if risk is not None:
        rows[1][6] = risk
    parse = impl("job_candidate_list.registry.parse_registry")
    return parse(rows).sources[0]


def test_registry_checked_on_parses_iso_date():
    """実測結果・リスク評価の確認日は YYYY-MM-DD を date として保持する（§8.3）。"""
    entry = _registry_entry()
    assert entry.measured is not None
    assert entry.measured.checked_on == dt.date(2026, 11, 5)
    assert entry.risk is not None
    assert entry.risk.checked_on == dt.date(2026, 11, 5)


@pytest.mark.parametrize(
    "measured",
    [
        "結果=成功; 取得方法=公式公開API",  # 確認日なし
        "結果=成功; 取得方法=公式公開API; 確認日=未定",  # 日付でない
        "結果=成功; 取得方法=公式公開API; 確認日=2026-13-45",  # 存在しない日付
        "結果=成功; 取得方法=公式公開API; 確認日=2026/11/05",  # スラッシュ形式
        "",  # 空欄
    ],
)
def test_registry_bad_measured_date_makes_whole_result_none(measured):
    """現行挙動: 確認日が ISO 形式でなければ実測結果全体が None になり、
    validate_registry が source_blocked（実測結果の形式不正）を返す。"""
    entry = _registry_entry(measured=measured)
    assert entry.measured is None
    validate = impl("job_candidate_list.registry.validate_registry")
    parse = impl("job_candidate_list.registry.parse_registry")
    rows = registry_rows()
    rows[1][4] = measured
    issues = validate(parse(rows))
    assert any(
        enum_value(i.kind) == "source_blocked" for i in issues
    ), f"source_blocked が検出されていない: {issues}"


def test_registry_bad_risk_date_makes_risk_none():
    """リスク評価側も同じく、確認日が不正なら RiskAssessment 全体が None。"""
    entry = _registry_entry(risk="リスク=低; 根拠=根拠文; 確認日=abc")
    assert entry.risk is None
    assert entry.measured is not None  # 実測側は独立にパースされる


class _SheetsBackend:
    """read_candidates の日付パースを観測するための最小フェイク backend。"""

    def __init__(self, rows, links):
        self._rows = rows
        self._links = links

    def read_rows(self, tab_name):
        return self._rows

    def read_links(self, tab_name):
        return self._links

    def write_rows(self, tab_name, rows):
        pass


def _gateway_with_dates(confirmed: str, fetched: str = ""):
    gw = impl("job_candidate_list.sheets_gateway")
    auto = list(gw.AUTO_COLUMNS_CANDIDATE)
    header = auto + list(gw.MANUAL_COLUMNS_CANDIDATE)
    row = [""] * len(header)
    row[0] = "cand_0000000000000001"
    row[1] = "株式会社サンプル青葉"
    row[2] = "プロダクトマネージャー"
    row[auto.index("情報源URL")] = "求人ページ"
    row[auto.index("掲載確認日")] = confirmed
    row[auto.index("OpenWork点数取得日")] = fetched
    links = {(1, auto.index("情報源URL")): "https://example.invalid/j/1"}
    return gw.GoogleSheetsGateway(_SheetsBackend([header, row], links))


def test_sheets_date_iso_format_parses():
    """Sheets セルの日付は YYYY-MM-DD のみ date になる（現行挙動）。"""
    record = _gateway_with_dates("2026-11-05", "2026-11-01").read_candidates()[0]
    assert record.confirmed_on == dt.date(2026, 11, 5)
    assert record.openwork_fetched_on == dt.date(2026, 11, 1)


@pytest.mark.parametrize(
    "raw", ["2026/11/05", "2026-11-5", "2026-11-05 10:30:00", "未定", ""]
)
def test_sheets_date_non_iso_becomes_none_without_error(raw):
    """現行挙動: ISO 形式でない日付セルはエラーにならず None になる。

    registry 側（形式不正で source_blocked）とは方針が異なる点に注意。
    共通化時にどちらの方針へ寄せるかは判断が要る（記録 C-062 参照）。
    """
    record = _gateway_with_dates(raw).read_candidates()[0]
    assert record.confirmed_on is None


# ---------------------------------------------------------------------------
# 2. 適合度順位表 _FITNESS_RANK の2定義
# ---------------------------------------------------------------------------
def _fitness_inputs():
    duty_cls = impl("job_candidate_list.judge_duty.DutyResult")
    location_cls = impl("job_candidate_list.judge_location.LocationResult")
    remote_cls = impl("job_candidate_list.judge_remote.RemoteResult")
    english_cls = impl("job_candidate_list.judge_english.EnglishResult")
    duty = duty_cls(
        outcome="upstream",
        rule_ids=["JOB-UPSTREAM-OK"],
        reasons=[],
        matched_upstream=["企画立案"],
        matched_impl=[],
    )
    location = location_cls(
        division="勤務地適合",
        rule_ids=["LOC-OK"],
        reasons=[],
        matched_municipality="サンプル甲市",
    )
    remote = remote_cls(
        division="出社週2〜3日",
        rule_ids=["REMOTE-NORMALIZED"],
        raw_found="出社週2〜3日",
    )
    english = english_cls(outcome="no_mention", rule_ids=[], reasons=[])
    return duty, location, remote, english


def test_assign_fitness_presentation_key_rank(
    judge_config_dict, profile_config_dict, local_config_dict
):
    """fitness 側の順位: 高=0／中=1／要確認=2 が presentation_key 先頭に入る。"""
    assign = impl("job_candidate_list.fitness.assign_fitness")
    cfg = build_app_config(profile_config_dict, judge_config_dict, local_config_dict)
    duty, location, remote, english = _fitness_inputs()

    high = assign(build_raw_posting(), duty, location, remote, english, cfg.fitness)
    assert enum_value(high.fitness) == "高"
    assert high.presentation_key[0] == 0

    duty_unknown = impl("job_candidate_list.judge_duty.DutyResult")(
        outcome="unknown",
        rule_ids=["JOB-UNKNOWN"],
        reasons=[],
        matched_upstream=[],
        matched_impl=[],
    )
    check = assign(
        build_raw_posting(), duty_unknown, location, remote, english, cfg.fitness
    )
    assert enum_value(check.fitness) == "要確認"
    assert check.presentation_key[0] == 2


def test_presentation_sort_key_fallback_rank_matches_fitness_rank():
    """presentation 側のフォールバック順位も 高=0／中=1／要確認=2 で一致する。"""
    sort_key = impl("job_candidate_list.presentation.presentation_sort_key")
    ordinal = dt.date(2026, 11, 5).toordinal()
    for fitness, rank in (("高", 0), ("中", 1), ("要確認", 2)):
        record = build_candidate_record(
            "cand_0000000000000001",
            fitness=fitness,
            confirmed_on=dt.date(2026, 11, 5),
        )
        assert record.presentation_key is None  # フォールバック経路であること
        assert sort_key(record) == (rank, 0, -ordinal, "cand_0000000000000001")


def test_presentation_sort_key_fallback_without_confirmed_on():
    """掲載確認日が無い候補のフォールバックは日付成分 0（現行挙動）。"""
    sort_key = impl("job_candidate_list.presentation.presentation_sort_key")
    record = build_candidate_record("cand_0000000000000001", fitness="高")
    assert sort_key(record) == (0, 0, 0, "cand_0000000000000001")


def test_presentation_sort_key_prefers_stored_key():
    """presentation_key が保持されていればフォールバックでなくそちらを使う。"""
    sort_key = impl("job_candidate_list.presentation.presentation_sort_key")
    stored = (0, -50, -739925, "cand_0000000000000009")
    record = build_candidate_record(
        "cand_0000000000000001", fitness="高", presentation_key=stored
    )
    assert sort_key(record) == stored


# ---------------------------------------------------------------------------
# 3. 文節分割ヘルパー（節単位の結びつき）
# ---------------------------------------------------------------------------
def _duty_outcome(judge_config_dict, text):
    judge = impl("job_candidate_list.judge_duty.judge_main_duty")
    duty_config = impl("job_candidate_list.config.DutyConfig")
    result = judge({"業務内容": text}, duty_config.from_dict(judge_config_dict["duty"]))
    return enum_value(result.outcome), list(result.rule_ids)


def test_duty_primary_marker_clause_decides_outcome(judge_config_dict):
    """主担当明示がある節の主務が優先される（節単位の判定・§3.5）。"""
    outcome, rule_ids = _duty_outcome(
        judge_config_dict, "実装を主担当として担います。企画立案も担当します。"
    )
    assert outcome == "impl" and "JOB-IMPL-HARD" in rule_ids
    outcome, rule_ids = _duty_outcome(
        judge_config_dict, "企画立案を主担当として担います。実装も担当します。"
    )
    assert outcome == "upstream" and "JOB-UPSTREAM-OK" in rule_ids
    outcome, _ = _duty_outcome(
        judge_config_dict, "テストがメイン業務です。企画立案も担当します。"
    )
    assert outcome == "impl"


def test_duty_primary_marker_alone_or_with_both_groups_is_mixed(judge_config_dict):
    """現行挙動: マーカーだけの節／マーカー節内に両群がある場合は混在。"""
    outcome, rule_ids = _duty_outcome(
        judge_config_dict, "主担当です。企画立案と実装を担当します。"
    )
    assert outcome == "mixed" and "JOB-MIXED" in rule_ids
    outcome, rule_ids = _duty_outcome(
        judge_config_dict, "企画立案と実装を主担当として担います。"
    )
    assert outcome == "mixed" and "JOB-MIXED" in rule_ids


def _english_outcome(judge_config_dict, sections):
    judge = impl("job_candidate_list.judge_english.judge_english")
    english_config = impl("job_candidate_list.config.EnglishConfig")
    result = judge(sections, english_config.from_dict(judge_config_dict["english"]))
    return enum_value(result.outcome), list(result.rule_ids)


def test_english_required_needs_same_clause_linkage(judge_config_dict):
    """現行挙動: 「英語」と必須表現が同一節にある場合のみ ENG-HARD。

    節をまたぐ場合は必須性が読み取れないものとして ENG-UNKNOWN になる。
    文節分割の現行挙動を固定する中核ケース。
    """
    outcome, rule_ids = _english_outcome(
        judge_config_dict, {"応募資格": "英語は必須です。"}
    )
    assert outcome == "required" and "ENG-HARD" in rule_ids
    outcome, rule_ids = _english_outcome(
        judge_config_dict, {"応募資格": "英語の使用経験があります。必須です。"}
    )
    assert outcome == "unknown" and "ENG-UNKNOWN" in rule_ids


def test_english_welcome_needs_same_clause_linkage(judge_config_dict):
    """歓迎表現も同一節で英語と結びつく場合のみ ENG-WELCOME-OK（現行挙動）。"""
    outcome, rule_ids = _english_outcome(
        judge_config_dict, {"応募資格": "英語力を歓迎します。"}
    )
    assert outcome == "not_required" and "ENG-WELCOME-OK" in rule_ids
    outcome, rule_ids = _english_outcome(
        judge_config_dict, {"応募資格": "英語の読み書きができます。歓迎します。"}
    )
    assert outcome == "unknown" and "ENG-UNKNOWN" in rule_ids


def test_english_score_linkage_within_clause(judge_config_dict):
    """数値スコアは単独では ENG-UNKNOWN（§3.7-3、サンプル SMP-038 と同じ）。"""
    outcome, rule_ids = _english_outcome(
        judge_config_dict, {"応募資格": "TOEIC 800点以上をお持ちの方。"}
    )
    assert outcome == "unknown" and "ENG-UNKNOWN" in rule_ids
    # スコアと別節でも、英語必須の明記節があればそちらで ENG-HARD
    outcome, rule_ids = _english_outcome(
        judge_config_dict, {"応募資格": "英語必須です。TOEIC 800点以上。"}
    )
    assert outcome == "required" and "ENG-HARD" in rule_ids


# ---------------------------------------------------------------------------
# 4. 検索対象テキスト組み立て（judge_hard）
# ---------------------------------------------------------------------------
def _hard_hits(judge_config_dict, **over):
    judge = impl("job_candidate_list.judge_hard.judge_hard_exclusions")
    hard_config = impl("job_candidate_list.config.HardConfig")
    posting = build_raw_posting(**over)
    return [
        h.rule_id
        for h in judge(posting, hard_config.from_dict(judge_config_dict["hard"]))
    ]


def test_hard_domain_search_text_is_overview_fields(judge_config_dict):
    """領域外判定の検索対象は会社概要系（原文＋会社概要セクション）。

    業務内容セクションだけに領域外キーワードがあっても発火しない。
    """
    # company_overview_raw にある場合
    assert "HARD-DOMAIN" in _hard_hits(
        judge_config_dict,
        company_overview_raw="飲食店を運営する会社です。",
        sections={"業務内容": "企画立案を主担当として担います。"},
    )
    # 会社概要セクションにある場合（原文側は中立）
    assert "HARD-DOMAIN" in _hard_hits(
        judge_config_dict,
        company_overview_raw="サンプル事業を行っています。",
        sections={
            "業務内容": "企画立案を主担当として担います。",
            "会社概要": "飲食店を運営しています。",
        },
    )
    # 業務内容セクションだけにある場合は発火しない
    assert "HARD-DOMAIN" not in _hard_hits(
        judge_config_dict,
        sections={
            "業務内容": "飲食店の企画立案を主担当として担います。",
            "会社概要": "データ分析基盤を提供する事業を行っています。",
        },
    )


def test_hard_domain_blocked_when_target_domain_in_text(judge_config_dict):
    """対象領域キーワードが求人テキストにあれば HARD-DOMAIN は発火しない（§3.8）。"""
    assert "HARD-DOMAIN" not in _hard_hits(
        judge_config_dict,
        company_overview_raw="飲食店を運営する会社です。",
        # 既定の業務内容セクションには「データ活用」が含まれる
    )
    assert "HARD-DOMAIN" not in _hard_hits(
        judge_config_dict, company_overview_raw="飲食店向けにデータ分析を提供します。"
    )


def test_hard_bizform_search_text_is_overview_fields(judge_config_dict):
    """事業形態判定も会社概要系テキストが対象（業務内容は対象外）。"""
    assert "HARD-BIZFORM" in _hard_hits(
        judge_config_dict,
        company_overview_raw="システムインテグレーション事業を行っています。",
    )
    assert "HARD-BIZFORM" in _hard_hits(
        judge_config_dict,
        sections={
            "業務内容": "企画立案を主担当として担います。",
            "会社概要": "SI事業を行っています。",
        },
    )
    assert "HARD-BIZFORM" not in _hard_hits(
        judge_config_dict,
        sections={
            "業務内容": "システムインテグレーション事業の企画立案を主担当として担います。",
            "会社概要": "データ分析基盤を提供する事業を行っています。",
        },
    )


def test_hard_emptype_not_fired_when_employment_unknown(judge_config_dict):
    """雇用形態が読み取れない（None）場合は HARD-EMPTYPE を発火させない（§3.8）。"""
    assert _hard_hits(judge_config_dict, employment_raw=None) == []


# ---------------------------------------------------------------------------
# 5. YAML 読込のエラー方針（実装ごとに現行挙動を固定する）
# ---------------------------------------------------------------------------
def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_load_config_malformed_yaml_is_config_error(
    tmp_path, judge_config_dict, profile_config_dict, local_config_dict
):
    """load_config: YAML 構文が壊れたファイルは ConfigError（現行挙動）。"""
    load_config = impl("job_candidate_list.config.load_config")
    errors = impl("job_candidate_list.errors")
    profile = tmp_path / "profile.local.yaml"
    profile.write_text("profile_version: [unclosed\n", encoding="utf-8")
    judge = _write_yaml(tmp_path / "judge.local.yaml", judge_config_dict)
    local = _write_yaml(tmp_path / "local.local.yaml", local_config_dict)
    with pytest.raises(errors.ConfigError):
        load_config(profile, judge, local)


def test_load_config_last_updated_accepts_str_and_date(
    tmp_path, judge_config_dict, profile_config_dict, local_config_dict
):
    """load_config: profile の last_updated は文字列でも date でも date になる。"""
    load_config = impl("job_candidate_list.config.load_config")
    judge = _write_yaml(tmp_path / "judge.local.yaml", judge_config_dict)
    local = _write_yaml(tmp_path / "local.local.yaml", local_config_dict)
    for value in ("2026-11-01", dt.date(2026, 11, 1)):
        profile = _write_yaml(
            tmp_path / "profile.local.yaml",
            dict(profile_config_dict, last_updated=value),
        )
        cfg = load_config(profile, judge, local)
        assert cfg.profile.last_updated == dt.date(2026, 11, 1)


def test_load_whitelist_missing_and_malformed_are_config_error(tmp_path):
    """load_whitelist: ファイルなし・YAML 構文不正はいずれも ConfigError（§3.6）。"""
    load_whitelist = impl("job_candidate_list.judge_location.load_whitelist")
    errors = impl("job_candidate_list.errors")
    with pytest.raises(errors.ConfigError):
        load_whitelist(tmp_path / "nonexistent.yaml")
    bad = tmp_path / "whitelist.yaml"
    bad.write_text("version: 1\nmunicipalities: [unclosed\n", encoding="utf-8")
    with pytest.raises(errors.ConfigError):
        load_whitelist(bad)


def test_load_whitelist_valid_file(tmp_path, location_whitelist_dict):
    """load_whitelist: 正常な YAML は LocationWhitelist として読み込める（§13）。"""
    load_whitelist = impl("job_candidate_list.judge_location.load_whitelist")
    path = _write_yaml(tmp_path / "whitelist.yaml", location_whitelist_dict)
    whitelist = load_whitelist(path)
    assert whitelist.version == 1
    assert whitelist.contains("サンプル甲市") is True
    assert whitelist.contains("サンプル乙市") is False


def test_load_whitelist_wrong_structure_raises_type_error(tmp_path):
    """現行挙動（方針のバラつき）: 構造不正（municipalities が非リスト）は
    ConfigError ではなく TypeError がそのまま伝播する。

    load_config 側は同種の不正を ConfigError に寄せており、方針が揃っていない。
    共通化時にどちらの方針へ寄せるかは判断が要る（記録 C-062 参照）。
    """
    load_whitelist = impl("job_candidate_list.judge_location.load_whitelist")
    path = tmp_path / "whitelist.yaml"
    path.write_text("version: 1\nmunicipalities: 42\n", encoding="utf-8")
    with pytest.raises(TypeError):
        load_whitelist(path)


def test_load_company_names_valid_mapping(tmp_path):
    """YAML 読込の3実装目（会社名対応表・sources/base の公開名）:
    正常な対応表は dict[str, str] として読み込まれる。"""
    load = impl("job_candidate_list.sources.base.load_company_names")
    path = tmp_path / "company_names.local.yaml"
    path.write_text(
        'board-a: "株式会社サンプル青葉"\nboard-b: "サンプル乙"\n',
        encoding="utf-8",
    )
    assert load(path) == {
        "board-a": "株式会社サンプル青葉",
        "board-b": "サンプル乙",
    }


@pytest.mark.parametrize(
    "content",
    [
        None,  # ファイル自体が無い
        "board-a: [unclosed\n",  # YAML 構文不正
        "- just\n- a\n- list\n",  # 構造不正（リスト）
        "",  # 空ファイル
    ],
)
def test_load_company_names_unreadable_returns_empty_dict(tmp_path, content):
    """現行挙動（3実装の中で最も緩い）: 読めない・壊れている場合は
    例外を出さず空 dict を返す（フェイルオープン）。

    load_config／load_whitelist が ConfigError で止める方針とは対照的であり、
    共通化時の方針統一の判断が要る（記録 C-062 参照）。
    """
    load = impl("job_candidate_list.sources.base.load_company_names")
    path = tmp_path / "company_names.local.yaml"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert load(path) == {}


def test_load_company_names_drops_non_string_values(tmp_path):
    """現行挙動: 値が文字列でないエントリだけが黙って落ち、他は残る。"""
    load = impl("job_candidate_list.sources.base.load_company_names")
    path = tmp_path / "company_names.local.yaml"
    path.write_text('good: "株式会社サンプル"\nbad: 42\n', encoding="utf-8")
    assert load(path) == {"good": "株式会社サンプル"}


# ---------------------------------------------------------------------------
# 6. 年収の万円換算
# ---------------------------------------------------------------------------
_SAMPLE_TEMPLATE = """分類コード: x
会社名: 株式会社サンプル青葉
ポジション名: プロダクトマネージャー
雇用形態: 正社員
情報源種別: 公開求人
情報源URL: https://example.invalid/j/1
想定年収: {salary}
勤務地: サンプル甲市
リモート頻度（原文）: 出社週2〜3日

## 業務内容
本文
"""


@pytest.mark.parametrize(
    "salary,expected",
    [
        ("年収 600万円〜800万円", (6_000_000, 8_000_000)),
        ("年収 750万円", (7_500_000, 7_500_000)),
        # 逆順でも小さい方が min・大きい方が max（追補 A1.1）
        ("年収 800万円〜600万円", (6_000_000, 8_000_000)),
        ("年収 650.5万円〜800万円", (6_505_000, 8_000_000)),
        ("記載なし", (None, None)),
    ],
)
def test_parse_sample_salary_man_en_conversion(salary, expected):
    """サンプル年収の万円→円換算（追補 A1.1 の正規表現仕様どおり）。"""
    parse = impl("job_candidate_list.evaluate.parse_sample_text")
    posting = parse(
        _SAMPLE_TEMPLATE.format(salary=salary), fetched_on=dt.date(2026, 11, 5)
    )
    assert (posting.salary_min_jpy, posting.salary_max_jpy) == expected
    assert posting.salary_raw == salary


def test_parse_sample_salary_three_amounts_uses_min_and_max():
    """現行挙動: 万円表記が3件ある場合は全件の最小・最大を取る。"""
    parse = impl("job_candidate_list.evaluate.parse_sample_text")
    posting = parse(
        _SAMPLE_TEMPLATE.format(salary="年収 600万円〜800万円〜1000万円"),
        fetched_on=dt.date(2026, 11, 5),
    )
    assert (posting.salary_min_jpy, posting.salary_max_jpy) == (6_000_000, 10_000_000)


def test_parse_sample_salary_without_en_suffix_counts_only_full_match():
    """現行挙動: 「600万」（円なし）は換算対象にならず、800万円だけが残る。"""
    parse = impl("job_candidate_list.evaluate.parse_sample_text")
    posting = parse(
        _SAMPLE_TEMPLATE.format(salary="年収 600万〜800万円"),
        fetched_on=dt.date(2026, 11, 5),
    )
    assert (posting.salary_min_jpy, posting.salary_max_jpy) == (8_000_000, 8_000_000)


def test_pipeline_salary_range_text_is_salary_raw_as_is(
    judge_config_dict, profile_config_dict, local_config_dict
):
    """pipeline 側の年収テキストは salary_raw のそのまま保持であり、
    min/max（円）から万円表記を再構成しない（現行挙動）。
    """
    _, gateway = run_fake_collection(
        profile_config_dict,
        judge_config_dict,
        local_config_dict,
        existing=[],
        postings=[
            build_raw_posting(
                salary_min_jpy=6_500_000,
                salary_max_jpy=8_550_000,
                salary_raw="原文表記",
            )
        ],
        dry_run=False,
    )
    assert gateway.upserted[0].salary_range_text == "原文表記"
    _, gateway = run_fake_collection(
        profile_config_dict,
        judge_config_dict,
        local_config_dict,
        existing=[],
        postings=[
            build_raw_posting(salary_min_jpy=None, salary_max_jpy=None, salary_raw=None)
        ],
        dry_run=False,
    )
    assert gateway.upserted[0].salary_range_text is None


# ---------------------------------------------------------------------------
# 7. シート名既定値
# ---------------------------------------------------------------------------
def test_local_config_sheet_name_defaults():
    """LocalConfig のシート名既定値は3タブとも仕様どおり（追補 A2.2・§4.3）。"""
    local_config = impl("job_candidate_list.config.LocalConfig")
    names = local_config.from_dict({}).sheet_names
    assert names.candidates == "候補リスト"
    assert names.registry == "情報源レジストリ"
    assert names.exclusions == "除外ログ"
    names = local_config.from_dict({"spreadsheet_id": "x"}).sheet_names
    assert names.candidates == "候補リスト"


def test_app_config_without_sheet_names_uses_defaults(
    profile_config_dict, judge_config_dict
):
    """from_dicts で sheet_names を欠いても既定値で補われる（現行挙動）。"""
    cfg = build_app_config(
        profile_config_dict, judge_config_dict, {"spreadsheet_id": "s"}
    )
    assert cfg.local.sheet_names.candidates == "候補リスト"
    assert cfg.local.sheet_names.registry == "情報源レジストリ"
    assert cfg.local.sheet_names.exclusions == "除外ログ"


class _RecordingBackend:
    def __init__(self):
        self.reads: list[str] = []
        self.writes: list[str] = []

    def read_rows(self, tab_name):
        self.reads.append(tab_name)
        return []

    def read_links(self, tab_name):
        return {}

    def write_rows(self, tab_name, rows):
        self.writes.append(tab_name)


def test_gateway_default_tab_names():
    """GoogleSheetsGateway は sheet_names=None で既定タブ名を使う（追補 A5）。"""
    gw = impl("job_candidate_list.sheets_gateway")
    models = impl("job_candidate_list.models")
    backend = _RecordingBackend()
    gateway = gw.GoogleSheetsGateway(backend)
    gateway.read_candidates()
    gateway.read_registry_rows()
    gateway.upsert_candidates(
        [
            build_candidate_record(
                "cand_0000000000000001", confirmed_on=dt.date(2026, 11, 5)
            )
        ]
    )
    gateway.append_exclusions(
        [
            models.ExclusionRow(
                exclusion_id="excl_0000000000000001",
                fetched_on=dt.date(2026, 11, 5),
                company="株式会社サンプル乙",
                position="PM",
                employment_type="正社員",
                source_type="公開求人",
                source_url=models.LinkCell("求人ページ", "https://example.invalid/j/9"),
                reason="理由",
                rule_ids=("HARD-EMPTYPE",),
                config_refs=("hard.non_regular_keywords",),
            )
        ]
    )
    assert backend.reads == ["候補リスト", "情報源レジストリ"]
    assert backend.writes == ["候補リスト", "除外ログ"]


# ---------------------------------------------------------------------------
# 8. リモート区分の文字列集合
# ---------------------------------------------------------------------------
_REMOTE_DIVISIONS = [
    "フルリモート（出社なし）",
    "出社週1日以下",
    "出社週2〜3日",
    "出社週4日以上",
    "出社必須（フル出社）",
    "不明",
]


def test_remote_division_closed_set():
    """RemoteDivision は §6.3 の閉じた6区分と一致する。"""
    models = impl("job_candidate_list.models")
    assert [enum_value(v) for v in models.RemoteDivision] == _REMOTE_DIVISIONS


def test_remote_rule_rejects_division_outside_closed_set():
    """RemoteRule.from_dict は集合外の区分を ConfigError にする（追補 A2.2）。"""
    remote_rule = impl("job_candidate_list.config.RemoteRule")
    errors = impl("job_candidate_list.errors")
    with pytest.raises(errors.ConfigError):
        remote_rule.from_dict({"pattern": "x", "division": "週5日出社"})
    # 「不明」は集合内なので受理される
    rule = remote_rule.from_dict({"pattern": "x", "division": "不明"})
    assert rule.division == "不明"


@pytest.mark.parametrize(
    "raw,division,rule_id",
    [
        (None, "不明", "REMOTE-UNKNOWN"),
        ("", "不明", "REMOTE-UNKNOWN"),
        ("たまにリモートです", "不明", "REMOTE-UNKNOWN"),
        ("フルリモートです", "フルリモート（出社なし）", "REMOTE-NORMALIZED"),
        ("週3日リモートです", "出社週2〜3日", "REMOTE-NORMALIZED"),
        ("週2日出社です", "出社週2〜3日", "REMOTE-NORMALIZED"),
        ("月1回出社です", "出社週1日以下", "REMOTE-NORMALIZED"),
        ("週4日出社します", "出社週4日以上", "REMOTE-NORMALIZED"),
        ("フル出社です", "出社必須（フル出社）", "REMOTE-NORMALIZED"),
    ],
)
def test_normalize_remote_frequency_current_behavior(
    judge_config_dict, raw, division, rule_id
):
    """正規化結果は常に閉じた6区分のいずれかで、raw_found は一致部分（追補 A11）。"""
    normalize = impl("job_candidate_list.judge_remote.normalize_remote_frequency")
    remote_config = impl("job_candidate_list.config.RemoteConfig")
    cfg = remote_config.from_dict(judge_config_dict["remote"])
    result = normalize(raw, cfg)
    assert enum_value(result.division) == division
    assert division in _REMOTE_DIVISIONS
    assert list(result.rule_ids) == [rule_id]
    if rule_id == "REMOTE-UNKNOWN":
        assert result.raw_found is None
    else:
        assert result.raw_found and result.raw_found in raw


# ---------------------------------------------------------------------------
# 9. openwork の3条件チェック
# ---------------------------------------------------------------------------
def _ow_ref(**over):
    ref_cls = impl("job_candidate_list.openwork.OpenworkCompanyRef")
    fields = {
        "company_norm": "サンプル青葉",
        "score": 3.5,
        "url": "https://example.invalid/openwork/1",
        "location_text": "サンプル甲市",
        "industry_text": None,
    }
    fields.update(over)
    return ref_cls(**fields)


def _ow_attach(refs):
    attach = impl("job_candidate_list.openwork.attach_openwork")
    openwork_config = impl("job_candidate_list.config.OpenworkConfig")
    candidate = build_candidate_record(
        "cand_0000000000000001",
        company="株式会社サンプル青葉",
        company_norm="サンプル青葉",
        location_text="サンプル甲市",
        fitness="中",
    )
    provider = FakeOpenworkProvider(results={"サンプル青葉": refs})
    cfg = openwork_config.from_dict({"weekly_once_per_company": True})
    return attach(candidate, provider, True, cfg)


def test_openwork_location_mismatch_is_nomatch():
    """勤務地が候補と矛盾する ref は突合不能（OW-NOMATCH・§3.14）。"""
    result = _ow_attach([_ow_ref(location_text="サンプル乙市")])
    assert result.score is None and result.url is None
    assert "OW-NOMATCH" in list(result.rule_ids)


def test_openwork_location_unknown_on_ref_still_matches():
    """ref 側の勤務地が不明（None）なら矛盾なしとして突合できる。"""
    result = _ow_attach([_ow_ref(location_text=None)])
    assert result.score == 3.5
    assert result.url.url == "https://example.invalid/openwork/1"


def test_openwork_mismatched_refs_are_filtered_before_counting():
    """現行挙動: 矛盾する ref を除いた結果が1件なら突合成功になる。

    （既存テストの「同名2件→突合不能」と対になる、絞り込み側の挙動。）
    """
    result = _ow_attach(
        [
            _ow_ref(),
            _ow_ref(
                url="https://example.invalid/openwork/2", location_text="サンプル乙市"
            ),
        ]
    )
    assert result.score == 3.5
    assert result.url.url == "https://example.invalid/openwork/1"


def test_openwork_company_norm_mismatch_is_nomatch():
    """正規化会社名が一致しない ref は突合不能（OW-NOMATCH）。"""
    result = _ow_attach([_ow_ref(company_norm="サンプル乙葉")])
    assert result.score is None
    assert "OW-NOMATCH" in list(result.rule_ids)


def test_openwork_industry_text_alone_does_not_block():
    """現行挙動: 候補側に業種情報が無いため、ref の業種だけでは矛盾にならない。"""
    result = _ow_attach([_ow_ref(industry_text="情報通信業")])
    assert result.score == 3.5


# ---------------------------------------------------------------------------
# 10. judge_location の「判定不能」結果構築
# ---------------------------------------------------------------------------
def _location_result(judge_config_dict, location_whitelist_dict, posting, remote):
    judge = impl("job_candidate_list.judge_location.judge_location")
    location_config = impl("job_candidate_list.config.LocationConfig")
    whitelist = build_location_whitelist(location_whitelist_dict)
    cfg = location_config.from_dict(judge_config_dict["location"])
    return judge(posting, remote, whitelist, cfg)


def _remote_result(division, rule_ids, raw_found):
    remote_cls = impl("job_candidate_list.judge_remote.RemoteResult")
    return remote_cls(division=division, rule_ids=rule_ids, raw_found=raw_found)


_REMOTE_OK = ("出社週2〜3日", ["REMOTE-NORMALIZED"], "出社週2〜3日")


def _posting_with_location(judge_config_dict, location_text):
    sections = dict(build_raw_posting().sections)
    if location_text is None:
        sections.pop("勤務地", None)
    else:
        sections["勤務地"] = location_text
    return build_raw_posting(sections=sections)


@pytest.mark.parametrize(
    "location_text",
    [
        None,  # 勤務地セクション自体が無い
        "",  # 勤務地セクションが空
        "勤務地は選考の過程でご案内します。就業場所の市区町村は記載していません。",
        "本社所在地はサンプル甲市です。",  # 本社所在地のみは根拠にしない（§3.6）
        "勤務地はサンプル甲市またはサンプル乙市です。",  # 複数で主たる勤務地が特定できない
        "勤務地はサンプル甲市です。転勤あり。",  # 転勤あり
        "全国勤務となります。",  # 全国勤務
        "勤務地はサンプル甲市またはサンプル乙市から選択できます。",  # 選択制で一部が対応表外
    ],
)
def test_judge_location_unknown_cases(
    judge_config_dict, location_whitelist_dict, location_text
):
    """判定不能ケースはいずれも同一形（勤務地判定不能／LOC-UNKNOWN／
    matched_municipality=None）で返る（§3.6・追補 A9）。"""
    posting = _posting_with_location(judge_config_dict, location_text)
    result = _location_result(
        judge_config_dict,
        location_whitelist_dict,
        posting,
        _remote_result(*_REMOTE_OK),
    )
    assert enum_value(result.division) == "勤務地判定不能"
    assert list(result.rule_ids) == ["LOC-UNKNOWN"]
    assert result.matched_municipality is None


def test_judge_location_unknown_when_remote_unknown(
    judge_config_dict, location_whitelist_dict
):
    """リモート頻度不明の場合は LOC-UNKNOWN に REMOTE-UNKNOWN を併記する（§3.6）。"""
    result = _location_result(
        judge_config_dict,
        location_whitelist_dict,
        build_raw_posting(),
        _remote_result("不明", ["REMOTE-UNKNOWN"], None),
    )
    assert enum_value(result.division) == "勤務地判定不能"
    assert list(result.rule_ids) == ["LOC-UNKNOWN", "REMOTE-UNKNOWN"]
    assert result.matched_municipality is None


def test_judge_location_choice_all_in_whitelist_is_ok(
    judge_config_dict, location_whitelist_dict
):
    """対照: 選択肢の全てが対応表内なら勤務地適合（§3.6）。"""
    posting = _posting_with_location(
        judge_config_dict, "勤務地はサンプル甲市またはサンプル区から選択できます。"
    )
    result = _location_result(
        judge_config_dict,
        location_whitelist_dict,
        posting,
        _remote_result(*_REMOTE_OK),
    )
    assert enum_value(result.division) == "勤務地適合"
    assert list(result.rule_ids) == ["LOC-OK"]
    assert result.matched_municipality == "サンプル甲市"
