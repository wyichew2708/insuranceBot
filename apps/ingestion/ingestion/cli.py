"""Ingestion CLI: ingest | activate | rollback | lint | watch.

`watch` consumes the KB publish stream (§6.1.7); activation is gated on the
eval suite in the CI/eval harness — the watcher records intent and defers to
`evals/runner.py` for the gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from contracts.api import PublishEvent
from contracts.settings import get_settings

from ingestion.loader import load_bundle, sync_bundle_repo
from ingestion.pipeline import activate_bundle, ingest_bundle, rollback
from ingestion.validator import lint_bundle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("ingestion.cli")


async def watch() -> None:
    import redis.asyncio as aioredis

    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    last_id = "$"
    checkout = Path("/tmp/kb-bundle")
    logger.info("watching stream %s", settings.kb_publish_stream)
    while True:
        entries: Any = await client.xread({settings.kb_publish_stream: last_id}, block=30_000, count=1)
        for _stream, events in entries:
            for event_id, fields in events:
                last_id = event_id if isinstance(event_id, str) else event_id.decode()
                raw = {
                    (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                    for k, v in fields.items()
                }
                event = PublishEvent.model_validate_json(raw.get("payload", json.dumps(raw)))
                logger.info("publish event: bundle=%s delta=%s", event.bundle_id, event.delta)
                bundle_dir = sync_bundle_repo(
                    settings.kb_bundle_git_url, event.git_ref or settings.kb_bundle_git_ref, checkout
                )
                # Ingest inactive; activation is performed by the eval gate step
                # (evals/runner.py --activate-bundle) only if pass-rate >= EVAL_GATE.
                bundle_id = await ingest_bundle(bundle_dir, settings, activate=False)
                logger.info("bundle %s staged (inactive), awaiting eval gate", bundle_id)


def main() -> None:
    parser = argparse.ArgumentParser(prog="ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="ingest a local bundle directory")
    p_ingest.add_argument("--bundle-path", type=Path, required=True)
    p_ingest.add_argument("--no-activate", action="store_true")

    p_lint = sub.add_parser("lint", help="lint a bundle without touching the DB")
    p_lint.add_argument("--bundle-path", type=Path, required=True)

    p_activate = sub.add_parser("activate", help="activate a staged bundle")
    p_activate.add_argument("bundle_id")

    p_rollback = sub.add_parser("rollback", help="re-activate a previous bundle")
    p_rollback.add_argument("bundle_id")

    sub.add_parser("watch", help="consume the KB publish stream")

    args = parser.parse_args()
    settings = get_settings()

    if args.command == "lint":
        report = lint_bundle(load_bundle(args.bundle_path))
        if report.ok:
            print("lint OK")
        else:
            print("\n".join(report.violations))
            raise SystemExit(1)
    elif args.command == "ingest":
        bundle_id = asyncio.run(ingest_bundle(args.bundle_path, settings, activate=not args.no_activate))
        print(f"bundle_id={bundle_id}")
    elif args.command == "activate":
        asyncio.run(activate_bundle(args.bundle_id, settings))
    elif args.command == "rollback":
        asyncio.run(rollback(args.bundle_id, settings))
    elif args.command == "watch":
        asyncio.run(watch())


if __name__ == "__main__":
    main()
