# Audit-log query examples (compliance evidence, §10.4)

Every tool call, routing decision, verification verdict, degraded turn and
handover is written to `audit_log` (`orchestrator/audit.py`).

## All events for one session (customer complaint investigation)
```sql
SELECT created_at, event, payload
FROM audit_log
WHERE session_id = :session_id
ORDER BY created_at;
```

## Verification failures in the last 7 days, by grader
```sql
SELECT r->>'name' AS grader, count(*) AS failures
FROM audit_log, jsonb_array_elements(payload->'rules') AS r
WHERE event = 'verification_verdict'
  AND (r->>'passed')::bool = false
  AND created_at > now() - interval '7 days'
GROUP BY 1 ORDER BY 2 DESC;
```

## Judge-ungrounded answers (sampled groundedness audit, §12)
```sql
SELECT session_id, created_at, payload->'judge'
FROM audit_log
WHERE event = 'verification_verdict'
  AND payload->'judge' IS NOT NULL
  AND (payload->'judge'->>'grounded')::bool = false
ORDER BY created_at DESC;
```

## Degraded turns (failure drills / incident review)
```sql
SELECT date_trunc('hour', created_at) AS hour, count(*)
FROM audit_log
WHERE event = 'degraded'
GROUP BY 1 ORDER BY 1 DESC;
```

## Eval-gate history export (also: `scripts/export_eval_history.py`)
```sql
SELECT created_at, bundle_id, suite, pass_rate, report->>'activated' AS activated
FROM eval_runs ORDER BY created_at DESC;
```
