"""Compile-loop CLI (Loop 2).

compile conflicts   — scan raw/ for source disagreements, file them
compile impact      — which wiki pages a changed source touches
compile facts       — dump extracted facts for a source
compile lint        — run the bundle linter
compile wiki        — compile raw/web snapshots into the OKF wiki (§D.1)
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from compiler.conflicts import scan, write_conflicts
from compiler.facts import SourceDoc, extract_facts
from compiler.impact import impact_set
from compiler.wiki import CompileConfig, compile_bundle
from okf import Bundle, lint_bundle


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def cmd_conflicts(args: argparse.Namespace) -> int:
    conflicts = scan(args.bundle)
    if not conflicts:
        print("no conflicts detected")
        return 0
    for conflict in conflicts:
        print(
            f"CONFLICT {conflict.benefit_code}.{conflict.attribute}: "
            f"{conflict.winner.source_path} says {conflict.winner.value} {conflict.winner.unit}, "
            f"{conflict.loser.source_path} says {conflict.loser.value} {conflict.loser.unit}"
        )
    if args.write:
        written = write_conflicts(args.bundle, conflicts)
        print(f"\nfiled {len(written)} conflict entries under {args.bundle / 'conflicts'}")
    return 1 if args.fail_on_conflict else 0


def cmd_impact(args: argparse.Namespace) -> int:
    bundle = Bundle.load(args.bundle)
    for source, pages in impact_set(bundle, args.sources).items():
        print(f"{source} → {', '.join(pages) if pages else '(no pages cite this)'}")
    return 0


def cmd_facts(args: argparse.Namespace) -> int:
    path = Path(args.source)
    doc = SourceDoc(path=str(path), text=path.read_text())
    print(f"# {doc.path} (hash {doc.content_hash})")
    for fact in extract_facts(doc):
        print(f"  {fact.benefit_code}.{fact.attribute} = {fact.value} {fact.unit}  @{fact.locator}")
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    report = lint_bundle(Bundle.load(args.bundle))
    for violation in report.violations:
        print(
            f"{violation.severity.value.upper():7} {violation.page_id:44} "
            f"[{violation.rule}] {violation.message}"
        )
    print(f"\n{len(report.errors)} errors · {len(report.warnings)} warnings")
    return 1 if report.errors else 0


def cmd_wiki(args: argparse.Namespace) -> int:
    config = CompileConfig(
        source_root=args.source or args.bundle,
        dest_root=args.bundle,
        version=args.commit,
        today=args.today,
        sign_off=args.sign_off or [],
        review_months=args.review_months,
    )
    report = compile_bundle(config)
    if not report.pages:
        print(
            f"no snapshots under {config.source_root / 'raw' / 'web'} — run `crawl run` first",
            file=sys.stderr,
        )
        return 1

    print(f"compiled {len(report.pages)} pages into {config.dest_root / 'wiki'}")
    print(f"  benefit tables: {len(report.tables)} products, {sum(report.tables.values())} rows")
    print(f"  policy documents read: {report.documents}")
    if report.conflicts:
        print(f"  website defects filed: {len(report.conflicts)} (see {config.dest_root / 'conflicts'})")
    for reason, count in sorted(report.skipped.items(), key=lambda kv: -kv[1]):
        print(f"  skipped {count:4}  {reason}")
    if not args.sign_off:
        print(
            "\npages are `draft`: nothing is retrievable until a human reviews them.\n"
            "re-run with --sign-off <name> to record the review and mark them approved."
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="compile")
    parser.add_argument("--bundle", type=Path, default=Path("okf"))
    sub = parser.add_subparsers(dest="command", required=True)

    p_conf = sub.add_parser("conflicts", help="detect source disagreements (§D.2)")
    p_conf.add_argument("--write", action="store_true", help="file entries under conflicts/")
    p_conf.add_argument("--fail-on-conflict", action="store_true")
    p_conf.set_defaults(func=cmd_conflicts)

    p_imp = sub.add_parser("impact", help="pages touched by a changed source")
    p_imp.add_argument("sources", nargs="+")
    p_imp.set_defaults(func=cmd_impact)

    p_wiki = sub.add_parser("wiki", help="compile raw/web snapshots into wiki pages")
    p_wiki.add_argument("--source", type=Path, help="bundle holding raw/web (default: --bundle)")
    p_wiki.add_argument("--commit", default="", help="source commit recorded on every page")
    p_wiki.add_argument("--today", type=_date, default=dt.date.today())
    p_wiki.add_argument(
        "--sign-off",
        nargs="+",
        help="reviewer sign-offs; without these pages stay `draft` and are not retrievable",
    )
    p_wiki.add_argument("--review-months", type=int, default=3)
    p_wiki.set_defaults(func=cmd_wiki)

    p_fac = sub.add_parser("facts", help="extract typed facts from one source")
    p_fac.add_argument("source")
    p_fac.set_defaults(func=cmd_facts)

    p_lint = sub.add_parser("lint", help="lint the bundle")
    p_lint.set_defaults(func=cmd_lint)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
