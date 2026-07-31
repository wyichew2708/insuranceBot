# Data-flow diagram (compliance evidence, §10.4)

```mermaid
flowchart LR
    U[Customer / Staff] -->|HTTPS + session JWT| GW[gateway]
    GW -->|1. redact PII\n2. injection screen| ORC[orchestrator]
    GW -->|raw msg pgp_sym_encrypt\n+ redacted msg| PG[(PostgreSQL)]
    ORC -->|filtered search\naudience/brand/language in SQL| RET[retrieval]
    RET --> PG
    ORC -->|tool calls + verdicts| AUD[(audit_log)]
    ORC -->|redacted prompts only| LLM[external vLLM endpoints]
    ORC -->|handover payloads| RS[(Redis stream\nhandover.requests)]
    CMS[SOURCE Console CMS] -->|OKF bundle git + publish event| ING[ingestion]
    ING -->|eval-gated activation| PG
    WEB[etiqa.com.sg / tiq.com.sg] -->|allowlisted crawl| CRW[crawler]
    CRW --> PG
```

Key properties for review:

1. **PII never reaches the model.** The gateway redacts NRIC/FIN, passport,
   policy numbers, emails and phone numbers before forwarding
   (`gateway/redaction.py`); only the redacted form is sent to the
   orchestrator and on to external LLM endpoints.
2. **Raw content is encrypted at rest** when `MESSAGE_ENC_KEY` is set
   (pgcrypto `pgp_sym_encrypt`, migration 0003); the redacted form remains
   queryable.
3. **Internal content isolation is structural**: the SQL filter in
   `retrieval/search.py` excludes `audience: internal` for public sessions,
   and the audience-leak grader re-checks the final answer.
4. **External calls** are limited to: the four configured vLLM endpoints,
   the CMS bundle git remote, and the two allowlisted public websites.
5. **Session claims are server-side**: brand comes from the widget-key
   registry, audience from the issuance path — clients cannot escalate.
