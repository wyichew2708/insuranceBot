"""Export eval-gate history to CSV (compliance evidence pack, §10.4).

Run: python scripts/export_eval_history.py [--out eval_history.csv]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from contracts.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("eval_history.csv"))
    args = parser.parse_args()

    settings = get_settings()
    if not settings.database_url:
        print("DATABASE_URL required", file=sys.stderr)
        raise SystemExit(1)

    import psycopg

    dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT created_at, bundle_id, git_sha, suite, pass_rate,"
            " report->>'activated' AS activated FROM eval_runs ORDER BY created_at"
        )
        rows = cur.fetchall()

    with args.out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["created_at", "bundle_id", "git_sha", "suite", "pass_rate", "activated"])
        writer.writerows(rows)
    print(f"wrote {len(rows)} eval runs to {args.out}")


if __name__ == "__main__":
    main()
