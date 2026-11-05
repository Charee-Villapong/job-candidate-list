"""Lever Postings API アダプタ（公式公開 API・公開状態の求人のみ）。

補正: 会社名はローカル設定の対応表（無ければボード識別子）から解決し、
本文には本文プレーン＋項目別リスト（業務内容／必須要件 等の見出し付き）
＋追加情報（会社概要）までを含め、見出しは判定セクション名としても渡す。
年収は salaryRange（JPY の場合）または本文中の日本円表記から抽出し、
リモート原文には本文全体を渡す。
"""

from __future__ import annotations

from typing import Mapping

from ..models import SourceType
from ..registry import SourceEntry
from .base import (
    extract_jpy_salary,
    fetch_json,
    format_salary_raw,
    html_to_text,
    make_posting,
    resolve_company_name,
)


class LeverAdapter:
    def __init__(
        self,
        boards: tuple[str, ...],
        timeout_sec: int = 30,
        company_names: Mapping[str, str] | None = None,
    ) -> None:
        self._boards = boards
        self._timeout = timeout_sec
        self._company_names = company_names

    def fetch(self, source: SourceEntry) -> list:
        postings = []
        for board in self._boards:
            data = fetch_json(
                f"https://api.lever.co/v0/postings/{board}?mode=json",
                self._timeout,
            )
            jobs = data if isinstance(data, list) else []
            for job in jobs:
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
                overview = str(job.get("additionalPlain", "") or "")
                # 追加情報（会社概要）は業務内容セクションには混ぜず、
                # company_overview_raw としてのみ渡す。
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
                    salary = extract_jpy_salary(full_text)
                    if salary is not None:
                        salary_min, salary_max, salary_raw = salary
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
                        company_overview_raw=overview or None,
                        extra_sections=extra_sections,
                    )
                )
        return postings
