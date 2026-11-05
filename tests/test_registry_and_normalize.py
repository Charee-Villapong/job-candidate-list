"""完了の定義②: レジストリ検証・正規化・重複判定・候補レコード必須項目。

根拠: 企画書「完了の定義」②、architecture.md §3.2 / §3.10 / §7 / §8 / §10.1。
レジストリ行は §8.1 の列構成（情報源名＋自動列9＋人手列2）で組み立てる。
"""

from __future__ import annotations

import pytest

from conftest import impl

HEADER = [
    "情報源名", "URLパターン", "ログイン要否", "資格情報の有無", "実測結果",
    "自動取得可否", "リスク評価結果と判定根拠", "費用条件確認", "自動応募可否",
    "送信確定方法", "対象化の最終判断", "備考",
]

MEASURED_OK = "結果=対象外（ログイン不要）; 取得方法=公式公開API; 確認日=2026-11-05"
RISK_LOW = "リスク=低; 根拠=公式ドキュメントで公開求人の取得が明記されている; 確認日=2026-11-05"
PAID_OK = "確認済み（無料または既存契約範囲内）"
STAGE1_SEND = "対象外（自動応募は第3段階で確定）"


def row(name="サンプル情報源A", url="https://example.invalid/board-a/*", **over):
    base = [
        name, url, "不要", "不要（ログイン不要のため）", MEASURED_OK, "可",
        RISK_LOW, PAID_OK, "不可", STAGE1_SEND, "対象", "",
    ]
    idx = {h: i for i, h in enumerate(HEADER)}
    for k, v in over.items():
        base[idx[k]] = v
    return base


def rows(*rs):
    return [HEADER, *rs]


# ---------------------------------------------------------------------------
# パースとスキーマ検証
# ---------------------------------------------------------------------------
def test_parse_registry_accepts_valid_rows():
    parse = impl("job_candidate_list.registry.parse_registry")
    reg = parse(rows(row()))
    assert reg is not None


def test_parse_registry_header_mismatch_is_schema_error():
    """列構成が §8.1 と一致しない場合は RegistrySchemaError（全体停止側）。"""
    parse = impl("job_candidate_list.registry.parse_registry")
    errors = impl("job_candidate_list.errors")
    bad = rows(row())
    bad[0] = HEADER[:-1]  # 列欠落
    with pytest.raises(errors.RegistrySchemaError):
        parse(bad)


@pytest.mark.parametrize(
    "override",
    [
        {"ログイン要否": "要"},                    # 閉じた値の範囲外（表記ゆれ）
        {"自動取得可否": ""},                      # 空欄
        {"費用条件確認": "無料"},                  # 範囲外
        {"対象化の最終判断": "対象にする"},        # 人手列も閉じた値で検証する
        {"送信確定方法": "未確定"},
    ],
)
def test_out_of_range_or_blank_value_blocks_only_that_source(override):
    """閉じた値の範囲外・空欄は当該情報源のみフェイルクローズ（§8.4-3）。"""
    parse = impl("job_candidate_list.registry.parse_registry")
    validate = impl("job_candidate_list.registry.validate_registry")
    is_collectable = impl("job_candidate_list.registry.is_collectable")
    reg = parse(rows(row(**override), row(name="サンプル情報源B",
                                          url="https://example.invalid/board-b/*")))
    issues = validate(reg)
    blocked = [i for i in issues if str(getattr(i, "kind", "")) == "source_blocked"]
    assert blocked, "範囲外の値に対する source_blocked が検出されていない"
    entries = list(getattr(reg, "sources", []) or [])
    if entries:
        assert is_collectable(entries[0]) is False
        assert is_collectable(entries[1]) is True


def test_duplicate_url_pattern_blocks_all_duplicated_rows():
    """URLパターンの重複行はすべて対象外（§8.4-4）。"""
    parse = impl("job_candidate_list.registry.parse_registry")
    is_collectable = impl("job_candidate_list.registry.is_collectable")
    reg = parse(rows(row(name="重複A"), row(name="重複B")))  # 同一URLパターン
    entries = list(getattr(reg, "sources", []) or [])
    assert entries, "Registry から情報源エントリを取り出す方法が仕様どおりでない"
    assert all(is_collectable(e) is False for e in entries)


def test_auto_apply_allowed_without_confirmed_send_method_is_blocked():
    """自動応募可否＝可＋送信確定方法が未確定（確定3値以外）は検出される（§8.4-5）。"""
    parse = impl("job_candidate_list.registry.parse_registry")
    validate = impl("job_candidate_list.registry.validate_registry")
    reg = parse(rows(row(**{"自動応募可否": "可", "送信確定方法": STAGE1_SEND})))
    issues = validate(reg)
    assert issues, "「自動応募可＝可＋送信確定方法未確定」が検出されていない"


@pytest.mark.parametrize(
    "send_method",
    ["完了画面の文言", "確認メールの到着", "マイページの応募履歴表示"],
)
def test_auto_apply_allowed_with_confirmed_send_method_is_not_blocked(send_method):
    """送信確定方法が確定3値のいずれかなら上記条件には触れない（§8.2）。"""
    parse = impl("job_candidate_list.registry.parse_registry")
    validate = impl("job_candidate_list.registry.validate_registry")
    reg = parse(rows(row(**{"自動応募可否": "可", "送信確定方法": send_method})))
    issues = [i for i in validate(reg)
              if "送信確定" in str(getattr(i, "detail", ""))]
    assert not issues


def test_login_required_without_vault_or_measurement_is_blocked():
    """ログイン必須で Vault保存済みでない／実測成功でない行は対象外（§8.4-6）。"""
    parse = impl("job_candidate_list.registry.parse_registry")
    is_collectable = impl("job_candidate_list.registry.is_collectable")
    reg = parse(rows(row(**{
        "ログイン要否": "必須",
        "資格情報の有無": "未保存",
        "実測結果": "結果=未実施; 取得方法=Secure Vault経由; 確認日=2026-11-05",
        "対象化の最終判断": "対象",
    })))
    entries = list(getattr(reg, "sources", []) or [])
    assert entries and is_collectable(entries[0]) is False


def test_unconfirmed_cost_or_unavailable_fetch_targeted_is_blocked():
    """費用未確認・自動取得不可/未確認・リスク高/未評価の対象化は不可（§8.4-7,8）。"""
    parse = impl("job_candidate_list.registry.parse_registry")
    is_collectable = impl("job_candidate_list.registry.is_collectable")
    for override in (
        {"費用条件確認": "未確認"},
        {"自動取得可否": "未確認"},
        {"自動取得可否": "不可"},
        {"リスク評価結果と判定根拠": "リスク=未評価; 根拠=未実施; 確認日=2026-11-05"},
    ):
        reg = parse(rows(row(**override)))
        entries = list(getattr(reg, "sources", []) or [])
        assert entries and is_collectable(entries[0]) is False, override


def test_conditional_fetch_requires_manual_only_decision():
    """条件付き可は最終判断「対象（手動収集のみ）」の場合に限る（§8.4-8）。"""
    parse = impl("job_candidate_list.registry.parse_registry")
    is_collectable = impl("job_candidate_list.registry.is_collectable")
    reg = parse(rows(row(**{"自動取得可否": "条件付き可（手動・低頻度の範囲のみ）",
                            "対象化の最終判断": "対象"})))
    entries = list(getattr(reg, "sources", []) or [])
    assert entries and is_collectable(entries[0]) is False


def test_openwork_row_allows_special_send_method_value():
    """OpenWork行の送信確定方法「対象外（企業評価の補助情報源）」は許容値（§8.2）。"""
    parse = impl("job_candidate_list.registry.parse_registry")
    validate = impl("job_candidate_list.registry.validate_registry")
    reg = parse(rows(row(name="OpenWork",
                         url="https://example.invalid/openwork/*",
                         **{"資格情報の有無": "未保存",
                            "自動取得可否": "不可",
                            "費用条件確認": "有料条件あり",
                            "送信確定方法": "対象外（企業評価の補助情報源）",
                            "対象化の最終判断": "対象外"})))
    schema_issues = [i for i in validate(reg)
                     if str(getattr(i, "kind", "")) == "schema"]
    assert not schema_issues


def test_openwork_gate_false_when_row_not_collectable():
    """OpenWork行が対象でなければゲートは False（点数付与のみ停止・収集は継続）。"""
    parse = impl("job_candidate_list.registry.parse_registry")
    gate = impl("job_candidate_list.registry.openwork_gate")
    reg = parse(rows(row(name="OpenWork",
                         url="https://example.invalid/openwork/*",
                         **{"自動取得可否": "不可",
                            "送信確定方法": "対象外（企業評価の補助情報源）",
                            "対象化の最終判断": "対象外"})))
    assert gate(reg) is False


def test_openwork_gate_false_when_row_missing():
    parse = impl("job_candidate_list.registry.parse_registry")
    gate = impl("job_candidate_list.registry.openwork_gate")
    assert gate(parse(rows(row()))) is False


# ---------------------------------------------------------------------------
# 候補IDの正規化仕様と重複判定（§3.2 / §7）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "a,b",
    [
        ("株式会社サンプル青葉", "サンプル青葉"),
        ("サンプル青葉株式会社", "サンプル青葉"),
        ("有限会社サンプル青葉", "サンプル青葉"),
        ("合同会社サンプル青葉", "サンプル青葉"),
        ("サンプルＡＢＣ", "サンプルABC"),     # 全角→半角
        ("サンプル 青葉", "サンプル青葉"),      # 空白除去
        ("サンプル　青葉", "サンプル青葉"),    # 全角空白除去
    ],
)
def test_normalize_company_unifies_legal_form_and_width(a, b):
    normalize = impl("job_candidate_list.normalize.normalize_company")
    assert normalize(a) == normalize(b)


@pytest.mark.parametrize(
    "raw,expected_part",
    [
        ("http://example.invalid/jobs/1/?utm_source=x&ref=y#frag",
         "https://example.invalid/jobs/1"),
        ("https://example.invalid/jobs/1/", "https://example.invalid/jobs/1"),
        ("https://example.invalid/jobs/1?utm_medium=m",
         "https://example.invalid/jobs/1"),
    ],
)
def test_normalize_url_rules(raw, expected_part, judge_config_dict):
    """スキーム統一・末尾スラッシュ・トラッキング除去・フラグメント除去（§3.2）。"""
    normalize = impl("job_candidate_list.normalize.normalize_url")
    assert normalize(raw) == expected_part


def test_candidate_id_format_and_stability():
    cid = impl("job_candidate_list.normalize.candidate_id")
    a = cid("サンプル青葉", "プロダクトマネージャー", "https://example.invalid/j/1")
    b = cid("サンプル青葉", "プロダクトマネージャー", "https://example.invalid/j/1")
    assert a == b
    assert a.startswith("cand_") and len(a) == len("cand_") + 16
    int(a[5:], 16)  # hexであること


def test_candidate_id_uses_real_url_not_display_text():
    """表示テキストが同一でも実URLが異なれば別ID（②・⑨(iv)）。"""
    cid = impl("job_candidate_list.normalize.candidate_id")
    a = cid("サンプル青葉", "PM", "https://example.invalid/j/1")
    b = cid("サンプル青葉", "PM", "https://example.invalid/j/2")
    assert a != b


def test_fallback_match_key_stable_when_url_changes():
    """URL変更時はフォールバックキーで既存行に突合できる（§7）。"""
    key = impl("job_candidate_list.normalize.fallback_match_key")
    assert key("サンプル青葉", "PM") == key("サンプル青葉", "PM")
    assert key("サンプル青葉", "PM") != key("サンプル青葉", "企画")


def test_duplicate_rows_are_zero_for_same_normalized_posting():
    """正規化後に同一となる求人は重複行として1行に集約される前提（②）。

    重複排除そのものは pipeline / sheets 側の責務であり、公開入口が
    §3 で一意に定まっていないため、まず候補IDレベルの同一性を確認し、
    行レベルの検証は仕様確認事項として報告する。
    """
    cid = impl("job_candidate_list.normalize.candidate_id")
    norm_c = impl("job_candidate_list.normalize.normalize_company")
    norm_u = impl("job_candidate_list.normalize.normalize_url")
    a = cid(norm_c("株式会社サンプル青葉"), "PM",
            norm_u("https://example.invalid/j/1/?utm_source=x"))
    b = cid(norm_c("サンプル青葉"), "PM", norm_u("https://example.invalid/j/1"))
    assert a == b


# ---------------------------------------------------------------------------
# 候補レコードの必須項目（§10.1 充足条件）
# ---------------------------------------------------------------------------
REQUIRED_RECORD_FIELDS = [
    "candidate_id", "company", "position", "location_text", "remote_division",
    "source_url", "confirmed_on", "fitness", "fit_reasons", "check_reasons",
]


@pytest.mark.parametrize("field", REQUIRED_RECORD_FIELDS)
def test_candidate_record_defines_required_fields(field):
    """候補レコードに必須項目のフィールドが定義されている（§2.2）。"""
    models = impl("job_candidate_list.models")
    record = models.CandidateRecord
    annotations = getattr(record, "__annotations__", {})
    assert field in annotations, f"CandidateRecord に {field} が無い"


def test_location_and_remote_unknown_fallback_is_in_spec():
    """勤務地・リモート頻度は「不明」で埋まる仕様（空欄0件の担保）。

    §2.2: location_text は読み取れない場合 "不明"、§6.3: リモート頻度は
    閉じた6区分で「不明」を含む。レコード生成を伴う検証は実装後に行う。
    """
    models = impl("job_candidate_list.models")
    assert hasattr(models, "CandidateRecord")
    pytest.fail("未検証: CandidateRecord の生成方法（必須引数・デフォルト）が"
                "§2.2 で未確定のため、「不明」で埋まる実挙動を確認できない")
