"""Lever Postings API アダプタ（公式公開 API・公開状態の求人のみ）。

補正: 会社名はローカル設定の対応表（無ければボード識別子）から解決し、
本文には本文プレーン＋項目別リスト（業務内容／必須要件 等の見出し付き）
を含め、見出しは判定セクション名としても渡す。
年収は salaryRange（JPY の場合）または本文中の日本円表記から抽出し、
リモート原文には本文全体を渡す。

追加情報（additionalPlain）は会社紹介・福利厚生・応募方法が混在する
定型ブロックで、会社概要専用セクションではないため判定には渡さない
（福利厚生中の「介護休暇」等の語が領域判定に混入するのを防ぐ）。
"""

from __future__ import annotations

from ..models import SourceType
from ..registry import SourceEntry
from .base import (
    AtsBoardAdapter,
    extract_jpy_salary,
    fetch_json,
    format_salary_raw,
    html_to_text,
    list_jobs,
    make_posting,
    resolve_company_name,
    unpack_salary,
)


def _board_url(board: str) -> str:
    return f"https://api.lever.co/v0/postings/{board}?mode=json"


class LeverAdapter(AtsBoardAdapter):
    def fetch(self, source: SourceEntry) -> list:
        postings = []
        for board, job in self._iter_board_jobs(fetch_json, _board_url, list_jobs):
            categories = job.get("categories") or {}
            body = str(job.get("descriptionPlain", "") or "")
            extra_sections: dict[str, str] = {}
            list_texts: list[str] = []
            for item in job.get("lists") or []:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("text", "") or "").strip()
                content = html_to_text(str(item.get("content", "") or ""))
                if not content:
                    continue
                list_texts.append(content)
                if label:
                    extra_sections[label] = content
            # 追加情報（additionalPlain）は判定に渡さない（docstring 参照）。
            full_text = "\n".join(part for part in [body, *list_texts] if part)
            salary_min = salary_max = salary_raw = None
            salary_range = job.get("salaryRange") or {}
            if salary_range.get("currency") == "JPY" and isinstance(
                salary_range.get("min"), (int, float)
            ):
                salary_min = int(salary_range["min"])
                salary_max = (
                    int(salary_range["max"])
                    if isinstance(salary_range.get("max"), (int, float))
                    else salary_min
                )
                salary_raw = format_salary_raw(salary_min, salary_max)
            else:
                salary_min, salary_max, salary_raw = unpack_salary(
                    extract_jpy_salary(full_text)
                )
            workplace = job.get("workplaceType")
            remote_raw = full_text
            if workplace:
                remote_raw = f"{full_text}\nworkplaceType: {workplace}"
            postings.append(
                make_posting(
                    source,
                    SourceType.PUBLIC,
                    str(job.get("hostedUrl", "")),
                    resolve_company_name(board, self._company_names),
                    str(job.get("text", "")),
                    full_text,
                    categories.get("location"),
                    categories.get("commitment"),
                    salary_min_jpy=salary_min,
                    salary_max_jpy=salary_max,
                    salary_raw=salary_raw,
                    remote_raw=remote_raw or None,
                    extra_sections=extra_sections,
                )
            )
        return postings
