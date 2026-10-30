"""Command line entry point: python -m portfolio_check --all | --check NAME [--target NAME]."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .checks import CHECKS, canonical
from .checks.denylist import load_terms
from .config import load_config, read_allowlist
from .context import Context
from .report import counts, write_reports
from .result import FAIL, SKIP, CheckResult


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m portfolio_check",
        description="Check every public portfolio artifact the way a logged-out visitor meets it.",
    )
    sel = p.add_mutually_exclusive_group()
    sel.add_argument("--all", action="store_true", help="run every check against every target (default)")
    sel.add_argument("--check", action="append", metavar="NAME",
                     help=f"run only this check; repeatable. One of: {', '.join(CHECKS)}")
    p.add_argument("--target", action="append", metavar="NAME", help="limit to this target; repeatable")
    p.add_argument("--targets", default=None, metavar="FILE", help="targets file (default: targets.yaml next to the package)")
    p.add_argument("--out", default=".", metavar="DIR", help="where report.md, report.json and screenshots go (default: .)")
    p.add_argument("--denylist-file", metavar="FILE", help="denylist terms, one per line (default: env DENYLIST)")
    p.add_argument("--local", action="append", default=[], metavar="NAME=PATH",
                   help="use a local checkout (repo) or file (CV) instead of the public source; repeatable")
    p.add_argument("--cv-de", metavar="PATH", help="German CV .docx (or env PORTFOLIO_CV_DE)")
    p.add_argument("--cv-en", metavar="PATH", help="English CV .docx (or env PORTFOLIO_CV_EN)")
    p.add_argument("--allowlist-file", action="append", default=[], metavar="FILE",
                   help="extra LanguageTool allowlist kept outside the repo (names, addresses); "
                        "repeatable, or env PORTFOLIO_ALLOWLIST")
    p.add_argument("--require-cv", action="store_true", help="fail CV checks when no CV file is found instead of skipping")
    p.add_argument("--list", action="store_true", help="list checks and targets, then exit")
    p.add_argument("--version", action="version", version=f"portfolio-check {__version__}")
    return p


def _default_targets() -> Path:
    here = Path.cwd() / "targets.yaml"
    if here.exists():
        return here
    return Path(__file__).resolve().parent.parent / "targets.yaml"


def _github_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    if shutil.which("gh"):
        proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.targets or _default_targets())

    if args.list:
        print("checks:")
        for name, (kinds, _fn) in CHECKS.items():
            print(f"  {name:<11} applies to: {', '.join(kinds)}")
        print("targets:")
        for t in config.targets:
            print(f"  {t.name:<26} {t.kind}")
        return 0

    try:
        checks = [canonical(c) for c in args.check] if args.check else list(CHECKS)
    except KeyError as exc:
        print(f"unknown check {exc}; known: {', '.join(CHECKS)}", file=sys.stderr)
        return 2
    known = {t.name for t in config.targets}
    for name in args.target or []:
        if name not in known:
            print(f"unknown target {name!r}; known: {', '.join(sorted(known))}", file=sys.stderr)
            return 2

    local: dict[str, Path] = {}
    for item in args.local:
        if "=" not in item:
            print(f"--local expects NAME=PATH, got {item!r}", file=sys.stderr)
            return 2
        name, path = item.split("=", 1)
        local[name] = Path(path).expanduser().resolve()
    if args.cv_de:
        local["cv-de"] = Path(args.cv_de).expanduser().resolve()
    if args.cv_en:
        local["cv-en"] = Path(args.cv_en).expanduser().resolve()

    extra_allow = list(args.allowlist_file)
    if os.environ.get("PORTFOLIO_ALLOWLIST"):
        extra_allow.append(os.environ["PORTFOLIO_ALLOWLIST"])
    for item in extra_allow:
        path = Path(item).expanduser()
        if not path.is_file():
            print(f"allowlist file not found: {path}", file=sys.stderr)
            return 2
        config.allowlist.extend(read_allowlist(path))

    if args.denylist_file:
        terms = load_terms(Path(args.denylist_file).read_text(encoding="utf-8"))
    else:
        terms = load_terms(os.environ.get("DENYLIST", ""))

    out_dir = Path(args.out).resolve()
    ctx = Context(
        config=config,
        out_dir=out_dir,
        cache_dir=Path(os.environ.get("PORTFOLIO_CHECK_CACHE", out_dir / ".portfolio-check-cache")),
        denylist=terms,
        local_paths=local,
        require_cv=args.require_cv,
        github_token=_github_token(),
        languagetool_url=os.environ.get("LANGUAGETOOL_URL", "https://api.languagetool.org/v2/check"),
        gitleaks_bin=os.environ.get("GITLEAKS_BIN", "gitleaks"),
        soffice_bin=os.environ.get("SOFFICE_BIN", "soffice"),
    )

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results: list[CheckResult] = []
    try:
        for check in checks:
            kinds, fn = CHECKS[check]
            for target in config.targets:
                if target.kind not in kinds:
                    continue
                if args.target and target.name not in args.target:
                    continue
                if check in {canonical(c) for c in target.skip_checks}:
                    reason = target.skip_reason or "not part of this target's acceptance criteria"
                    result = CheckResult(check, target.name, SKIP, f"skipped by targets.yaml: {reason}")
                    results.append(result)
                    print(f"[SKIP] {check:<10} {target.name:<26} {result.summary}", flush=True)
                    continue
                try:
                    result = fn(ctx, target)
                except Exception as exc:  # noqa: BLE001 - a crash is a failed check, not a crashed run
                    result = CheckResult(check, target.name, FAIL, f"check crashed: {type(exc).__name__}: {exc}")
                results.append(result)
                print(f"[{result.status.upper():4}] {check:<10} {target.name:<26} {result.summary}", flush=True)
    finally:
        ctx.close()

    if not results:
        print("no check applies to the selected targets", file=sys.stderr)
        return 2
    md, js = write_reports(
        results,
        out_dir,
        {"started": started, "checks": checks, "targets": args.target or "all", "denylist_terms": len(terms)},
    )
    c = counts(results)
    print(f"\n{c['pass']} pass, {c['fail']} fail, {c['skip']} skipped. Wrote {md} and {js}")
    return 1 if c["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
