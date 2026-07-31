"""Retention CLI: `python -m gateway.retention_cli` deletes messages past
MESSAGE_TTL_DAYS. Wire to a daily systemd timer (infra/systemd)."""

from __future__ import annotations

import asyncio

from contracts.settings import get_settings

from gateway.storage import purge_expired_messages


def main() -> None:
    settings = get_settings()
    deleted = asyncio.run(purge_expired_messages(settings.database_url, settings.message_ttl_days))
    print(f"deleted {deleted} expired messages (ttl {settings.message_ttl_days}d)")


if __name__ == "__main__":
    main()
