# Deploying the knowledge layer

Every command here was run against this repository at commit `91a10db`, and
every output shown is what it printed. Where a step can fail, the failure is
written down next to it.

The system is **one FastAPI process reading a directory of Markdown and CSV**.
No cache, no queue, and by default no database. That is not a simplification
for the guide — it is the architecture, and it is why deployment is short.

Since v2.1 there is one optional addition: a Postgres/pgvector index over the
compiled wiki sections, for *recall* on customer vocabulary the lexical scorer
cannot reach ("my flat got flooded"). It is off unless `PGVECTOR_DSN` is set,
it never bypasses the frontmatter filter or the gates, and the API runs
exactly as before without it. See §8a.

---

## 0. What you are deploying

| | |
|---|---|
| image | `insurancebot/api:dev`, 264 MB, non-root (uid 10001) |
| port | 8080 inside the container |
| state | none — the trace store is an in-memory ring buffer |
| corpus | a read-only bind mount of a directory in this repo |
| network egress | none, unless you configure a model |

Three surfaces come up together on the same port:

| path | what it is |
|---|---|
| `/` | debug console — ask a question, see the gates and the trace |
| `/studio` | content studio — read pages, review them, flip their status |
| `/docs` | OpenAPI surface, for whoever integrates against this |
| `/v1/*` | the JSON API |

---

## 1. Prerequisites

Docker is the only hard requirement.

```bash
docker version
```

Verified on Docker server 24.0.5 with Compose v2. Anything recent enough to
have `docker compose` as a subcommand rather than the standalone
`docker-compose` binary should work, though I have only run it on 24.0.5.

You do **not** need Python, `uv`, an API key, or network access to serve. You
need Python 3.11+ and `uv` only if you intend to recompile the corpus or run
the evaluation suites — see §7.

---

## 2. Get the code and the corpus

```bash
git clone git@github.com:wyichew2708/insuranceBot.git
cd insuranceBot
git checkout claude/genai-insurance-chatbot-v2
```

The compiled corpus is committed, so the clone is the deployment. Nothing is
crawled, parsed, or downloaded at deploy time. Confirm it arrived:

```bash
ls okf-real/wiki/product | head
find okf-real/wiki -name '*.md' | wc -l      # 768
```

If `okf-real/` is missing you are on the wrong branch — it does not exist on
`main`.

---

## 3. Choose a corpus

`BUNDLE_PATH` selects which compiled bundle the API serves. Three exist:

| value | contents | when to use |
|---|---|---|
| `okf` *(default)* | 3 products, hand-written and hand-checked | smoke tests, demos, CI |
| `okf-real` | 108+ Etiqa/Tiq products, 768 pages, compiled from the websites, the published FAQs and 220 policy documents | the real thing |
| `okf-web` | synthetic fixture content from `make knowledge` | volume testing; **never** real figures |

### Read this before serving `okf-real`

Its pages are signed off `UNREVIEWED-eval-only`, and that string is literal.

The compiler writes every page as `draft`. A draft page is invisible to
retrieval and fails the `reference-integrity` gate, so a freshly compiled
bundle answers nothing — deliberately, because compiled-from-a-crawl is not the
same as fit to say to a customer. Two things promote a page: **a person reading
it at `/studio` and flipping its status** (`POST /v1/cms/pages/{id}/status`),
or `--sign-off`, which stamps the whole bundle at compile time and records who
claimed it. `make corpus-compile`
uses the second, with a name chosen to be impossible to miss in the frontmatter
of all 768 pages.

Nobody has read these pages. Serving them to customers is a decision someone
has to make; serving them to yourself, to a review team, or to an evaluation
harness is what they are for.

---

## 4. Configure

```bash
cp .env.example .env
```

`.env` is gitignored and is read from the working directory, so it belongs at
the repository root and `make` must be run from there. **With no `.env` at all
every value falls back to a default and the system runs fully offline** — that
is a working state, not a broken one.

The settings that matter:

```bash
BUNDLE_PATH=okf-real          # which corpus to serve
API_PORT=8080                 # host port; the container always listens on 8080

LLM_PROVIDER=auto             # auto | deterministic | anthropic | vllm
ANTHROPIC_API_KEY=            # only for LLM_PROVIDER=anthropic
ANTHROPIC_MODEL=claude-sonnet-5
VLLM_BASE_URL=                # only for LLM_PROVIDER=vllm
VLLM_MODEL=

GUARDRAILS=auto               # auto | rules | off
GUARDRAIL_MODEL=              # smaller model for the two screening calls
GUARDRAIL_FAIL_CLOSED=false   # true = refuse the turn if screening errors
```

Two of these decide your bill and your failure mode:

**`LLM_PROVIDER`.** `deterministic` uses the composer's own wording — no
network, no key, no cost, 4 ms per answer. `anthropic` and `vllm` hand the
model *prose to write*, never a fact to establish. Measured on the same
604-case suite: deterministic 95.0%, local Qwen3.6-35B-A3B 94.2%, with citation
F1, figure exact match and numeric binding **identical** between them. Start
deterministic; add a model when you want the phrasing, not the accuracy.

**`GUARDRAIL_FAIL_CLOSED`.** `false` means a screening error is logged and the
turn proceeds on the deterministic rules alone. `true` means it refuses. For a
regulated deployment, close it.

---

## 5. Start it

```bash
make docker-up
```

Or without `make`:

```bash
docker compose --project-directory . -f infra/docker-compose.yml up -d --build
```

`--project-directory .` is not optional. It is what makes the repo-root `.env`
the source of settings and the relative volume paths resolve from the repo root
rather than from `infra/`. The Makefile targets pass it for you.

First build takes a few minutes. Then:

```
 Container insurancebot-api-1  Started
```

---

## 6. Verify — four checks, in order

Do not skip to the last one. Each check isolates a different failure.

### 6.1 The process is alive

```bash
curl -s http://localhost:8080/healthz
```

```json
{"status":"ok"}
```

### 6.2 The corpus actually loaded

```bash
curl -s http://localhost:8080/readyz
```

```json
{"status":"ready","pages":768,"table_rows":73}
```

**`pages` is the check.** `768` means `okf-real` mounted and parsed. `23` means
you are serving the seed bundle — `BUNDLE_PATH` did not reach the container,
usually because `--project-directory .` was omitted. `0` means the mount is
there but empty.

### 6.3 The corpus is well-formed

```bash
curl -s http://localhost:8080/v1/bundle/lint
```

```json
{"ok":true,"violations":[]}
```

A violation here is a corpus defect, not a runtime one: an unreferenced claim,
a number typed into prose, a broken graph edge. Fix it with a recompile (§7),
not a restart.

### 6.4 It answers, and the gates ran

```bash
curl -s -X POST http://localhost:8080/v1/answer \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "What is not covered by fire insurance?",
    "session": {
      "session_id": "deploy-check",
      "channel": "channel/direct",
      "auth_level": "L0"
    }
  }'
```

The enum values are strict and the API will tell you so with a 422 listing the
alternatives:

| field | accepted |
|---|---|
| `channel` | `channel/direct`, `channel/bancassurance`, `channel/agency`, `channel/broker`, `channel/ifa`, `unknown` |
| `auth_level` | `L0` anonymous, `L1` identified, `L2` authenticated |

A healthy response carries `delivered: true`, a non-empty `claims` array, and a
`gates` array in which nothing is `fail`:

```
delivered: True
claims: 6 | figures: 1
gates: guardrail-output=pass, reference-integrity=pass, numeric-binding=pass,
       version-coherence=skip, channel-coherence=pass, exclusion-completeness=skip,
       advice-boundary=pass, groundedness=pass, answerability=pass
```

`skip` is not a failure — it means the gate had nothing to judge (no in-force
policy in the session, no coverage assertion in the answer).

You can also see review progress directly:

```bash
curl -s http://localhost:8080/v1/cms/overview
```

```json
{"bundle":{"root":"okf-real","pages":768,"table_rows":73},
 "health":{"approved":768,"draft":0,"in_review":0,...}}
```

`approved: 768, draft: 0` on this bundle is the `--sign-off` stamp, not 768
reviews. See §3.

### 6.5 The human surfaces

```bash
open http://localhost:8080/          # console
open http://localhost:8080/studio    # content studio
open http://localhost:8080/docs      # OpenAPI
```

Both should return 200. The console is where you watch a question move through
retrieval, composition and the gates; the studio is where a reviewer reads
pages and promotes them.

---

## 7. Operating it

### Logs and lifecycle

```bash
make docker-logs      # follow
make docker-down      # stop and remove
```

The container has `restart: unless-stopped` and a healthcheck that probes
`/healthz` every 15s with a 30s start period.

### Changing the corpus without rebuilding

The corpus is a bind mount, not a layer in the image. Edit or recompile it on
the host, then:

```bash
curl -s -X POST http://localhost:8080/v1/bundle/reload
```

No restart, no rebuild. This is also what the studio calls after a page is
promoted.

### Recompiling the corpus

Needs Python 3.11+ and `uv` on the host, not in the container.

```bash
make install
make corpus-compile     # compile + lint from sources already on disk. No network.
```

`corpus-compile` is reproducible offline because every source it reads —
`raw/web/`, `raw/wordings/`, `raw/product-summaries/`, `raw/faq/` — is
committed. To re-crawl from the live sites instead:

```bash
make corpus             # crawl, parse the PDFs, read the FAQs, compile, lint
```

That one takes hours of network and hits `www.etiqa.com.sg` and
`www.tiq.com.sg` at 1 request/second under robots.txt. It is the reason the
output is committed rather than built on deploy.

### Running the evaluation suites

```bash
make ci                 # lint, typecheck, 620 tests, bundle lint,
                        # guardrail backtest, 4 eval suites. Fully offline.
```

---

## 8. Serving with a local model

A local model is deliberately **not** a compose service. On Apple Silicon it
runs on Metal, which Docker Desktop does not pass through to containers — a
containerised MLX server falls back to CPU and is unusably slow. Run it on the
host and point the container at `host.docker.internal`, because inside a
container `localhost` is the container:

```bash
mlx-openai-server launch --model-type lm --port 8090 \
  --model-path mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit \
  --served-model-name qwen3.6-35b-a3b

VLLM_BASE_URL=http://host.docker.internal:8090 \
VLLM_MODEL=qwen3.6-35b-a3b GUARDRAIL_MODEL= make docker-up
```

Expect p50 latency around 3.3 s against 4 ms deterministic. The model receives
the same JSON schema the Anthropic provider does, so moving between them
changes the runtime, not the answer contract.

On a Linux host with NVIDIA GPUs, vLLM in a container is the right answer,
and it is the `vllm` service under the compose `gpu` profile (§8a).

## 8a. The GPU host: vectors, embeddings, reranking

Everything the API depends on is a URL, so the Mac and the GPU host differ by
a `.env`, and both can be tested against the same code.

    docker compose --profile gpu up -d        # postgres, embed, rerank, vllm
    make index                                # embed the served bundle, once

    PGVECTOR=auto
    PGVECTOR_DSN=postgresql://okf:okf@gpu-host:5432/okf
    EMBED_BASE_URL=http://gpu-host:8080/v1    # TEI serving BAAI/bge-m3
    RERANK_BASE_URL=http://gpu-host:8081      # optional cross-encoder
    VLLM_BASE_URL=http://gpu-host:8000        # or the Mac's MLX server

What it is, and is not. A chunk found by similarity is a *candidate* — under
the same frontmatter filter, composition and gates as one found by words. It
is fused into the lexical rank as a bonus, so a page the words missed can rise
above the confidence floor, and a draft or expired chunk cannot win on
similarity. The index is the wiki, not `raw/`, because wiki sections carry the
frontmatter the filter needs. It is built offline by `make index`, keyed by
content hash so a recompile re-embeds what changed, and never inside
`Bundle.load` — the evaluators and CI need no database. At request time the
API embeds only the question.

Failure is a mode, not an outage. `PGVECTOR=auto` degrades to the lexical
path when the database or the embedder is unreachable, records why on the
trace as `vector_degraded`, and marks the turn `retrieval_mode: lexical`. An
evaluation refuses to score a "hybrid" run served that way. `PGVECTOR=on`
fails the turn instead, for testing the path; `off` never opens a connection.
`/v1/integrations` probes the database and reports its own error verbatim.

Why the earlier guide said "no vector store". Grep with a frontmatter filter
beats embeddings on precision at this bundle size, and still does. What it
loses is recall on the words customers use, which became a measured failure
in the field test. The vectors are bounded to recall by design; every admitted
chunk still carries a page id and a source ref, and the rejected-candidate log
still explains every rejection.

---

## 9. What is not production-ready

Stated plainly, because a deployment guide that omits this is worse than none.

- **The content API has no authentication.** `POST /v1/cms/pages/{id}/status`
  and every other write takes `actor` as a field the caller supplies, not an
  identity the service verifies. Every write is linted; none is authorised.
  Fine on localhost. Not fine on a network.
- **The corpus is unreviewed.** See §3.
- **Traces are in memory.** A restart loses them. There is no exporter.
- **Scan jobs are in-process.** A restart loses a running scan's suggestions.
- **No TLS, no rate limiting, no request authentication** on `/v1/*`. Put it
  behind something that has them.

---

## 10. Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `readyz` reports 23 pages | `BUNDLE_PATH` never reached the container | run from the repo root, or pass `--project-directory .` |
| `readyz` reports 0 pages | mount path wrong or directory empty | check `okf-real/` exists in the clone |
| 422 on `/v1/answer` | enum spelling | `channel/direct`, not `direct`; `L0`, not `anonymous` |
| every answer refuses | pages are `draft` | the bundle was compiled without `--sign-off` |
| numeric-binding fails on quoted figures | `raw/` not mounted | the gate re-reads the wordings to verify quotations; `raw/` is not optional |
| console 404 | image built without the console assets | rebuild with `make docker-build` |
| model calls time out | `localhost` used from inside the container | use `host.docker.internal` |
