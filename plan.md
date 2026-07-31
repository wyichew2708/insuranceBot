# plan.md — GenAI Insurance Chatbot (Etiqa / Tiq)
Development plan for an AI coding agent (GitHub Copilot agent mode). Work through phases in order.
Complete every task's **DoD (Definition of Done)** before moving on. Do not skip Phase 0.
---
## 0. Context, scope, and what NOT to build
**Goal:** a production GenAI chatbot for internal and external insurance enquiries plus policy-servicing
guidance, for two brands (etiqa.com.sg and tiq.com.sg) operated by one insurer (Singapore, MAS-regulated).
**Already hosted externally — DO NOT implement, only consume via config:**
- **LLM serving (vLLM, OpenAI-compatible HTTP):** four endpoints — agent model (Qwen3-32B class),
  judge model (different family, e.g. Llama-3.3-70B), embedding model (BGE-M3, dense + sparse),
  reranker (BGE-reranker-v2-m3). All support `/v1/chat/completions` or `/v1/embeddings`;
  agent endpoint supports structured outputs (guided decoding / `response_format` json_schema).
- **CMS (SOURCE Console):** authors content and publishes an **OKF knowledge bundle** (markdown files
  with YAML frontmatter, per contract in §4) into a git repository, plus a publish event on Redis Streams.
  We only build the *consumer* side (loader, validator, indexer).
**We DO build:** gateway, agent harness (router + loop + tools + verification), knowledge ingestion &
retrieval, site crawler & web index, catalogue service, actions registry, chat widget, internal portal
view, observability wiring, and the evaluation harness.
**Hard product rules (encode as code, not prose):**
1. Grounded-only answers: every factual claim must cite a KB block id or an allowlisted URL.
2. Verbatim-only zone: phone numbers, bank accounts, SWIFT codes, emails are copied character-exact
   from source blocks; a grader rejects any digit/email string not exactly present in a cited source.
3. The bot never claims to execute policy changes. Servicing = guided steps + deep links only (v1).
4. Promotions are only quoted from the fresh web index (allowlisted domains), never from model memory,
   never from third-party aggregators. Expired validity window ⇒ do not quote.
5. Emergency priority route: messages indicating an ongoing overseas emergency return the travel
   Emergency Services Hotline immediately, before any retrieval.
6. `audience: internal` content must never be retrievable in public-channel sessions (filtered at SQL level).
7. No financial advice for life/investment products: factual answers + route to human adviser ("Get Advice").
---
## 1. Architecture summary
```
channels (widget: tiq | etiqa | internal portal)
   │  HTTPS
   ▼
gateway (FastAPI)          auth/session • PII redaction • injection screen • rate limit
   ▼
orchestrator (FastAPI + LangGraph)
   ├─ intent router        deterministic-first classification
   ├─ agent loop           plan → tool call → observe (max N steps)
   ├─ tool registry        typed, permission-tagged tools
   └─ verification loop    rule graders → LLM judge → 1 retry → degrade/handover
   ▼
knowledge plane
   ├─ retrieval API        hybrid dense+sparse + rerank + metadata filters (pgvector)
   ├─ wiki reader          read_page(id) over OKF bundle
   ├─ catalogue API        structured plans/benefits/eligibility (products.json)
   ├─ web index            crawler output, TTL'd, freshness-scored
   └─ actions registry     canonical deep links + verbatim contact facts, per brand
observability: Langfuse traces • feedback • audit log • eval harness (CI gate)
state: PostgreSQL (+pgvector) • Redis (sessions, streams)
```
---
## 2. Repository layout (monorepo)
```
/apps
  /gateway            FastAPI  – edge API, auth, redaction, rate limit
  /orchestrator       FastAPI  – LangGraph harness, tools, verification
  /retrieval          FastAPI  – search endpoints over pgvector (kb + web indexes)
  /ingestion          worker   – OKF bundle loader, validator, chunker, embedder, index swap
  /crawler            worker   – sitemap/WP crawl, extraction, web-index upsert
  /widget             React (Vite, TS) – embeddable chat UI, brand-themed
  /portal             React – internal staff chat + transcript/feedback viewer (thin)
/packages
  /contracts          pydantic models + JSON Schemas shared by all services (single source)
  /clients            typed clients: vllm (chat/embed/rerank), langfuse, redis, db
/evals
  /golden             golden question sets (yaml)
  /graders            rule graders + judge prompts
  runner.py           regression runner; exit non-zero on gate failure
/infra
  docker-compose.yml  local dev: postgres+pgvector, redis, langfuse (dev only)
  /containerfiles     one per app (rootless-podman compatible, non-root UID)
  /nginx              reverse-proxy sample conf
/db
  /migrations         alembic
plan.md               this file
```
Conventions: Python 3.11+, FastAPI + pydantic v2, `uv` or `pip-tools` lock, `ruff` + `mypy` strict,
pytest; TS strict mode, no `localStorage` (in-memory session only). All services stateless; state in
Postgres/Redis. All containers must run rootless (no privileged ports, UID ≠ 0, writable dirs explicit).
---
## 3. Environment & configuration
Single `packages/contracts/settings.py` (pydantic-settings). Every service reads the same env names:
```
# LLM endpoints (external, OpenAI-compatible)
VLLM_AGENT_BASE_URL=        VLLM_AGENT_MODEL=
VLLM_JUDGE_BASE_URL=        VLLM_JUDGE_MODEL=
VLLM_EMBED_BASE_URL=        VLLM_EMBED_MODEL=          # BGE-M3: dense + sparse (lexical weights)
VLLM_RERANK_BASE_URL=       VLLM_RERANK_MODEL=
VLLM_API_KEY=
# Knowledge bundle (external CMS output)
KB_BUNDLE_GIT_URL=          KB_BUNDLE_GIT_REF=main
KB_PUBLISH_STREAM=kb.publish            # Redis stream name
# State
DATABASE_URL=postgresql+psycopg://...   # pgvector extension required
REDIS_URL=redis://...
# Observability
LANGFUSE_HOST=  LANGFUSE_PUBLIC_KEY=  LANGFUSE_SECRET_KEY=
# Crawler
CRAWL_ALLOWLIST=www.etiqa.com.sg,www.tiq.com.sg
CRAWL_DEFAULT_REFRESH_HOURS=24
CRAWL_PROMO_REFRESH_HOURS=6
# Runtime
BRANDS=etiqa,tiq
AGENT_MAX_STEPS=6
VERIFY_MAX_RETRIES=1
SESSION_TTL_MINUTES=60
```
---
## 4. Shared contracts (implement in /packages/contracts first)
### 4.1 OKF block frontmatter (input contract from CMS — validate, never author)
```yaml
okf: "0.2"
id: tiq-trv/exclusions/pre-existing-conditions   # stable; becomes chunk/citation id
type: faq | benefit | eligibility | exclusion | procedure | disclaimer | escalation
title: str
product_code: str | "ALL"
line: personal/travel | commercial/marine | common | ...
audience: public | policyholder | internal
brand: [etiqa, tiq] | [etiqa] | [tiq]
language: en | ms | zh
jurisdiction: SG | MY
version: int
status: draft | in_review | published | retired
effective_from: date        effective_to: date | null
distribution_channel: banca | ifa_ad | direct | all      # optional
takaful: bool                                            # optional, default false
source_ref: str             # policy wording + version, optional
action_ref: str | null      # reserved for future transactional binding
channels: [tiq-app, customer-portal, branch, hotline]    # procedures only
sla: str                                                 # procedures only
related: [block-id, ...]
tags: [str]
```
Body = markdown with `##` sections. Chunking rule: one chunk per block; if block > 700 tokens,
one chunk per `##` section with `chunk_id = {block_id}#{section-slug}`. Never split mid-section.
### 4.2 Core DB tables (alembic migrations)
- `kb_chunks(chunk_id pk, block_id, bundle_id, text, dense vector(1024), sparse jsonb, metadata jsonb, active bool)`
- `web_chunks(chunk_id pk, url, canonical_url, brand, text, dense, sparse, fetched_at, expires_at, accurate_as_of date, page_type)`
- `catalogue_products(product_code pk, brand[], line, name, data jsonb, bundle_id)`
- `actions(action_id pk, brand, kind link|phone|email, value, label, verbatim bool)`
- `sessions(session_id pk, channel, brand, audience, created_at, state jsonb)`
- `messages(id, session_id, role, content, redacted_content, created_at)`
- `feedback(id, session_id, message_id, rating, comment, created_at)`
- `audit_log(id, session_id, event, payload jsonb, created_at)`  # every tool call + verification verdict
- `eval_runs(id, bundle_id, git_sha, suite, pass_rate, report jsonb, created_at)`
### 4.3 Internal API contracts (pydantic; also serve as OpenAPI)
- `POST /v1/chat` (gateway → orchestrator): `{session_id, brand, audience, message}` →
  SSE stream of `{type: token|citation|action|handover|done, ...}`
- `POST /search` (retrieval): `{query, index: kb|web, filters: {brand, audience, language, jurisdiction,
  line?, product_code?, active_on: date}, top_k}` → `[{chunk_id, text, score, metadata}]`
- `GET /page/{block_id}` (retrieval): raw block + frontmatter (wiki reader)
- `GET /catalogue/{product_code}` and `POST /catalogue/compare` `{product_codes[], benefit_codes[]?}`
- `GET /actions/{brand}` and `GET /actions/{brand}/{action_id}`
**DoD §4:** contracts package builds; JSON Schemas exported to `/packages/contracts/schema/*.json`;
round-trip tests (yaml frontmatter → model → yaml) pass. **[x — v1 initial commit]**
---
## 5. Phase 0 — Foundations
1. [x] Scaffold monorepo per §2; docker-compose with postgres(pgvector), redis, langfuse; alembic baseline
   migration creating all §4.2 tables; health endpoints (`/healthz`, `/readyz`) on every app.
2. [x] `packages/clients/vllm.py`: async client with `chat()`, `chat_structured(json_schema)` (uses vLLM
   guided decoding), `embed()` returning `{dense, sparse}` for BGE-M3, `rerank(query, docs)`.
   Retries with backoff, per-endpoint timeouts, Langfuse span per call.
3. [x] Langfuse wiring: tracer facade with span-per-call; no-op fallback when unconfigured.
4. [x] Eval harness skeleton: `evals/runner.py` loads a suite yaml
   (`- id, question, brand, audience, expect: {must_cite?: [block_id], must_contain?: [], must_not_contain?: [],
   route?: intent, verbatim?: []}`), calls the chat API, applies checks, writes `eval_runs`, prints a table,
   exits non-zero if pass-rate < threshold (env `EVAL_GATE=0.95`).
5. [x] CI (GitHub Actions + `make ci`): lint, typecheck, unit tests; eval smoke suite (10 Qs) runs
   against the compose stack via `make eval`.
**DoD Phase 0:** `docker compose up` gives healthy stack; `make ci` green; a stub echo-chat flows
end-to-end gateway→orchestrator with a Langfuse trace visible.
---
## 6. Phase 1 — Knowledge plane
### 6.1 Ingestion worker (`/apps/ingestion`)
1. [x] **Loader:** clone/pull `KB_BUNDLE_GIT_URL@ref`; parse every `*.md` (tolerant of unknown frontmatter
   keys; log-and-keep unknown, fail on missing required).
2. [x] **Validator (lint):** required fields present; `id` unique; `related` links resolve; language sets
   share `id`; no overlapping effective windows per `(id, language)`; `audience: internal` never linked
   from public `index.md`; ids immutable (compare with previous bundle manifest).
3. [x] **Chunker:** per §4.1 rule; embed `title + section heading + text`; call embed endpoint;
   write `kb_chunks` with `active=false` under new `bundle_id`.
4. [x] **Catalogue:** load `catalogue/products.json` → `catalogue_products`; validate every `block_ref`
   resolves to a block id.
5. [x] **Actions:** load `actions.json` → `actions`; entries with `verbatim: true` feed the verbatim registry.
6. [x] **Atomic swap + rollback:** activation = single transaction flipping `active` by `bundle_id`;
   keep last 3 bundles; `rollback(bundle_id)` command.
7. [~] **Event consumer:** subscribe `KB_PUBLISH_STREAM`; stages bundle inactive; eval-gated activation
   wiring (delta re-embed + line-scoped suites) is pending.
### 6.2 Retrieval service (`/apps/retrieval`)
1. [x] Hybrid search: dense cosine (pgvector) + sparse score (BGE-M3 lexical weights via in-process
   fusion), Reciprocal Rank Fusion, then rerank top-30 → top-k via rerank endpoint.
2. [x] Mandatory filters applied in SQL before scoring: `audience` (public sessions ⇒ `audience != 'internal'`
   AND `audience` allowed set), `brand` (match or `both`), `language`, `jurisdiction`,
   `effective_from <= :today < coalesce(effective_to,'infinity')`, `status='published'`, `active=true`.
3. [~] `GET /page/{block_id}` wiki reader done; `GET /index/{language}/{path}` navigation pages pending.
4. [x] Catalogue endpoints per §4.3; `compare` returns aligned benefit rows.
**Seed data note:** initial bundle content derives from the site inventory (product pages → benefit /
eligibility / exclusion / faq blocks; claims pages → procedure blocks; /policy-services/ → ~50 procedure
blocks with Online/Download channels; governance pages → disclaimer blocks). Assume the CMS team
publishes it; for dev, a fixture bundle lives in `/evals/fixture-bundle/` (travel product, claim
procedure, payments block with fake bank details, emergency escalation, internal block). **[x]**
**DoD Phase 1:** ingestion of fixture bundle passes lint **[x, unit-tested]**; hybrid search vs live
pgvector, publish-event→eval-gate→activation demo, and rollback demo remain to be exercised against
the compose stack.
---
## 7. Phase 2 — Site crawler & web index
1. [~] Discovery: sitemap.xml / wp-sitemap.xml + WP REST fallback implemented; HTML link crawl pending.
2. [~] Extraction: trafilatura when installed (fallback tag-stripper); `rel=canonical` handling pending;
   `canonical_map.yml` for known slug drift **[x]**.
3. [x] Classification per URL: `page_type ∈ {product, claims, servicing, governance, promo, blog, other}`
   by path rules; promos get `CRAWL_PROMO_REFRESH_HOURS` TTL; "accurate as of {date}" and promo validity
   windows parsed into `accurate_as_of` / `expires_at`.
4. [x] Exclusions: `/LoginPortal/`, `/iConnect/`, `/online/`, `/buy-online/**`; PDFs under
   `/policy-wordings/` recorded but not chunked.
5. [x] Stale flags: COVID-era / "fully subscribed" pages `demoted=true`.
6. [ ] Chunk + embed into `web_chunks`; nightly full refresh; 6-hourly promo refresh (APScheduler).
**DoD Phase 2:** pending live crawl of both domains.
---
## 8. Phase 3 — Agent harness (orchestrator)
### 8.1 Intent router (deterministic-first)
Order of evaluation (first match wins):
1. [x] **Emergency:** regex signals → escalation + Emergency Services Hotline action verbatim, no
   retrieval, `route=emergency` logged. Small-model classifier augmentation pending.
2. [~] **Servicing intents:** keyword prefilter in place; small-model structured classifier
   (`{intent, product_code?, confidence}`, threshold 0.8) pending.
3. [x] **Product discovery / comparison** route detection.
4. [x] **Coverage / FAQ QA** default route.
5. [x] **Out of scope / smalltalk:** static handled responses.
### 8.2 Tools **[x]** — typed args models, permission tags, schema-validated dispatch, malformed-args
tests; renderer-substituted `action_id`s keep raw contact facts away from the model.
### 8.3 Agent loop **[~]** — deterministic harness with step budget, loop detection, guided-procedure
hook, escalate-terminal; the model planner (structured outputs) and LangGraph migration are pending
(echo planner stub in place).
### 8.4 Verification loop **[~]** — all 7 rule graders implemented + table-driven tests; LLM-judge
verdict, retry-with-feedback, and degrade path wiring into the stream pending.
**DoD Phase 3:** pending golden `core.yaml` (60 Qs) and the model planner.
---
## 9. Phase 4 — Channels
1. [~] **Widget:** React/TS SSE chat with brand theming, citations, action buttons, feedback thumbs,
   in-memory session. Embeddable script build + server-side brand binding pending.
2. [~] **Gateway hardening:** regex PII redaction (NRIC/passport/policy/email/phone) + injection screen
   + per-session/per-IP rate limits (Redis or in-memory) in place; JWT session issuance, Presidio,
   encrypted raw storage pending.
3. [~] **Internal portal:** internal-audience chat + placeholder read-only views.
4. [ ] **Handover:** `handover.requests` stream emission.
**DoD Phase 4:** pending e2e tests against the compose stack.
---
## 10. Phase 5 — Hardening & ops
1. [~] Containerfiles rootless-compatible **[x]**; systemd/quadlet units pending; nginx conf with SSE
   buffering off, gzip, security headers, rate limit zone **[x]**.
2. [ ] Load test (locust): 50 concurrent sessions, p95 < 6s RAG turn.
3. [ ] Failure drills: agent endpoint down / judge down / retrieval empty.
4. [ ] Compliance evidence pack; React source maps disabled in prod builds **[x]**.
5. [ ] Weekly analytics job: cluster low-rated + unanswered turns → gap report CSV.
**DoD Phase 5:** pending.
---
## 11. Testing & CI gates (applies to every phase)
- Unit: contracts round-trip, chunker boundaries, filter SQL, graders (table-driven), canonical map. **[x]**
- Integration: ingestion→retrieval on fixture bundle; router classification fixtures; agent loop with
  mocked vLLM (recorded structured outputs). **[~]**
- E2E: docker-compose profile spins full stack with fixture bundle + fixture crawl; run golden suite. **[ ]**
- Gates: `ruff` + `mypy --strict` clean; coverage ≥ 80% on `packages/` and graders; eval pass-rate ≥
  `EVAL_GATE`; no gate ⇒ no merge.
## 12. Overall exit criteria (v1 ship)
- Groundedness (judge-audited sample) ≥ 97%; citation present on 100% of factual answers.
- Verbatim-digit grader: zero violations across full golden + fuzz suite.
- Public/internal isolation: zero leaks in adversarial suite.
- Servicing guidance covers the full Policy Services taxonomy + all 11 claim types with correct
  channels and action links per brand.
- Rollback demonstrated: bad bundle activation reverted in < 1 minute without restart.
## 13. Conventions & guardrails for the coding agent
- Never hard-code URLs, hotlines, promo codes, coverage figures, or product names in application code —
  they live only in the bundle fixtures, `actions` table, or crawler output. Grep-guard test enforces this
  (`tests/test_no_hardcoded_facts.py` scans `apps/` for `+65`, `SWIFT`, `promo`, `S$` literals). **[x]**
- Every new tool: pydantic args model, permission tag, Langfuse span, audit-log entry, unit test, and a
  malformed-args test proving schema rejection.
- Any change to `packages/contracts` requires regenerating exported JSON Schemas in the same commit
  (enforced by `test_schemas_current.py`).
- Prefer small, after-each-task commits on branches named `phaseN/task-slug`. Keep this plan.md updated: mark tasks
  `[x]` with the commit SHA as you complete them.
