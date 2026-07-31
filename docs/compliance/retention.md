# Retention configuration (compliance evidence, §10.4)

| Data | Store | TTL | Mechanism |
|---|---|---|---|
| Chat messages (raw, encrypted + redacted) | `messages` | `MESSAGE_TTL_DAYS` (default 90d) | daily purge job |
| Sessions | `sessions` | with messages | cascade of purge policy |
| Feedback | `feedback` | kept (no PII; comments redacted at ingest) | — |
| Audit log | `audit_log` | kept for regulatory audit | — |
| Web index | `web_chunks` | `expires_at` per page (24h default, 6h promos) | crawler refresh + SQL filter |
| KB bundles | `kb_chunks` | last 3 bundles | activation cleanup |

The purge job is `python -m gateway.retention_cli`, scheduled by
`infra/systemd/retention.timer` (daily). The deletion count is logged;
runs are visible in the service journal.

Encryption at rest for raw message content: pgcrypto `pgp_sym_encrypt`
with `MESSAGE_ENC_KEY` (enable via migration 0003 + env). Key rotation =
re-encrypt job + key swap; document the rotation date here when performed.
