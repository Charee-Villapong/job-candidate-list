"""会社名・ポジション名・URL の正規化と候補 ID 生成（§3.2／§7）。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# normalize_url の引数で設定が渡されない場合の既定の除去対象。
# 呼び出し側（pipeline）は設定 normalize.tracking_params を渡す。
DEFAULT_TRACKING_PARAMS: tuple[str, ...] = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "fbclid",
    "gclid",
)

_LEGAL_FORMS: tuple[str, ...] = (
    "特定非営利活動法人",
    "一般社団法人",
    "一般財団法人",
    "公益社団法人",
    "公益財団法人",
    "株式会社",
    "有限会社",
    "合同会社",
    "合資会社",
    "合名会社",
    "NPO法人",
)

_DECORATIVE_CHARS = (
    "★☆◆◇●○■□▲△▼▽※【】「」『』（）()[]<>〈〉《》・!！?？:：;；/／|｜_-―—~〜…"
)


def _remove_whitespace(text: str) -> str:
    return re.sub(r"\s+", "", text)


def normalize_company(name: str) -> str:
    """法人格表記の除去・NFKC 統一・空白除去を行う。"""
    text = unicodedata.normalize("NFKC", name)
    text = _remove_whitespace(text)
    changed = True
    while changed:
        changed = False
        for form in _LEGAL_FORMS:
            if text.startswith(form) and len(text) > len(form):
                text = text[len(form) :]
                changed = True
            if text.endswith(form) and len(text) > len(form):
                text = text[: -len(form)]
                changed = True
    return text


def normalize_position(name: str) -> str:
    """全角・半角の統一、空白の除去、前後の装飾記号の除去を行う。"""
    text = unicodedata.normalize("NFKC", name)
    text = _remove_whitespace(text)
    return text.strip(_DECORATIVE_CHARS)


def normalize_url(url: str, tracking_params: tuple[str, ...] | None = None) -> str:
    """スキーム統一・末尾スラッシュ統一・トラッキング除去・フラグメント除去。"""
    params = DEFAULT_TRACKING_PARAMS if tracking_params is None else tracking_params
    parts = urlsplit(url.strip())
    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
        if not path:
            path = "/"
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in params
    ]
    query = urlencode(kept)
    return urlunsplit((scheme, parts.netloc, path, query, ""))


def candidate_id(company_norm: str, position_norm: str, url_norm: str) -> str:
    digest = hashlib.sha256(
        f"{company_norm}\n{position_norm}\n{url_norm}".encode("utf-8")
    ).hexdigest()
    return "cand_" + digest[:16]


def fallback_match_key(company_norm: str, position_norm: str) -> str:
    return hashlib.sha256(
        f"{company_norm}\n{position_norm}".encode("utf-8")
    ).hexdigest()[:16]
