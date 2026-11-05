"""CLI 入口（§1.1／§1.2）。

手動実行のみ。サブコマンド: collect（初回収集）／dry-run／
validate-config／validate-registry。対外送信・応募は行わない。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config, validate_config
from .pipeline import build_notification_text, run_collection
from .registry import parse_registry, validate_registry
from .sheets_gateway import GoogleApiSheetsBackend, GoogleSheetsGateway
from .sources.ats_ashby import AshbyAdapter
from .sources.ats_greenhouse import GreenhouseAdapter
from .sources.ats_lever import LeverAdapter
from .sources.ats_smartrecruiters import SmartRecruitersAdapter
from .sources.green_manual import GreenManualAdapter

_DEFAULT_CONFIG_DIR = Path("config/local")


def _load(args: argparse.Namespace):
    base = Path(args.config_dir)
    return load_config(
        base / "profile.local.yaml",
        base / "judge.local.yaml",
        base / "local.local.yaml",
    )


def _gateway(cfg) -> GoogleSheetsGateway:
    backend = GoogleApiSheetsBackend(None, cfg.local.spreadsheet_id)
    names = cfg.local.sheet_names
    return GoogleSheetsGateway(
        backend,
        sheet_names={
            "candidates": names.candidates,
            "registry": names.registry,
            "exclusions": names.exclusions,
        },
    )


def _adapters(cfg) -> dict:
    boards = cfg.local.ats_boards
    timeout = cfg.collection.timeout_sec
    return {
        "Green": GreenManualAdapter(),
        "Greenhouse Job Board API": GreenhouseAdapter(
            boards.get("greenhouse", ()), timeout
        ),
        "Lever Postings API": LeverAdapter(boards.get("lever", ()), timeout),
        "Ashby Job Postings API": AshbyAdapter(boards.get("ashby", ()), timeout),
        "SmartRecruiters Posting API": SmartRecruitersAdapter(
            boards.get("smartrecruiters", ()), timeout
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job-candidate-list")
    parser.add_argument(
        "--config-dir",
        default=str(_DEFAULT_CONFIG_DIR),
        help="実値設定ファイルの置き場（既定: config/local）",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect", help="初回収集（本実行）")
    sub.add_parser("dry-run", help="ドライラン（シートへ書き込まない）")
    sub.add_parser("validate-config", help="設定の検証のみ")
    sub.add_parser("validate-registry", help="レジストリの検証のみ")
    args = parser.parse_args(argv)

    cfg = _load(args)
    if args.command == "validate-config":
        problems = validate_config(cfg)
        for problem in problems:
            print(problem)
        print("設定は正常です" if not problems else "設定に問題があります")
        return 0 if not problems else 1
    if args.command == "validate-registry":
        registry = parse_registry(_gateway(cfg).read_registry_rows())
        issues = validate_registry(registry)
        for issue in issues:
            print(f"{issue.source_id}: {issue.kind}: {issue.detail}")
        print(f"検証完了（問題 {len(issues)} 件）")
        return 0
    summary = run_collection(
        cfg,
        _gateway(cfg),
        _adapters(cfg),
        None,
        dry_run=args.command == "dry-run",
    )
    list_url = "https://docs.google.com/spreadsheets/d/" f"{cfg.local.spreadsheet_id}"
    print(build_notification_text(summary, list_url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
