"""Lint the OKF bundle — CI gate and local check (§C.3, §H.1)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from okf import Bundle, lint_bundle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=Path("okf"))
    parser.add_argument("--warnings-as-errors", action="store_true")
    args = parser.parse_args()

    bundle = Bundle.load(args.bundle)
    report = lint_bundle(bundle)

    for violation in report.violations:
        where = f"{violation.page_id}" + (f":{violation.line}" if violation.line else "")
        print(f"{violation.severity.value.upper():7} {where:48} [{violation.rule}] {violation.message}")

    print(
        f"\n{len(bundle.pages)} pages · {len(bundle.tables)} table rows · "
        f"{len(report.errors)} errors · {len(report.warnings)} warnings"
    )
    if report.errors or (args.warnings_as_errors and report.warnings):
        sys.exit(1)


if __name__ == "__main__":
    main()
