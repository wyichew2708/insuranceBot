# Rootless podman quadlet units (§10.1)

Copy these `.container`/`.timer` files to `~/.config/containers/systemd/`
(rootless) and run `systemctl --user daemon-reload`. Images are built by
`make release` (versioned tags) — substitute the tag for `:latest` in prod.

- `gateway.container`, `orchestrator.container`, `retrieval.container` — the
  three HTTP services (non-privileged ports, UID 10001 inside).
- `retention.timer` + `retention.service` — daily message-TTL purge.
- Postgres/Redis are expected as managed services; point `DATABASE_URL` /
  `REDIS_URL` at them via the env file `/etc/insurancebot/env`.
