# Building the corpus

How a directory of Markdown and CSV gets made, stage by stage: crawl → parse
the PDFs → read the published FAQs → compile → lint → review.

Every command below was run. The fixture-site outputs are from a live run
against the in-process synthetic site; the real-host numbers are from the crawl
that produced the committed `okf-real/`. Where a stage can fail, the failure it
actually printed is shown.

**You do not need to run any of this to deploy.** `okf-real/` is committed —
see [DEPLOYMENT.md](DEPLOYMENT.md). This is for rebuilding it, for pointing the
system at a different insurer, or for understanding what the committed corpus
is made of.

---

## The shape of a bundle

```
okf-real/
  okf.yaml                  manifest: taxonomy, authority order, link rules
  raw/                      IMMUTABLE sources — never hand-edited
    web/<host>/<date>/      dated, content-hashed crawl snapshots
    wordings/               policy contracts, parsed from PDF
    product-summaries/      the regulated summaries, parsed from PDF
    brochures/              marketing PDFs — ingested, never authoritative
    faq/                    published question/answer pairs
    benefit-tables/         <slug>.csv — every figure, with a source_ref
  wiki/                     COMPILED pages — a build output, never hand-edited
  conflicts/                source disagreements, filed for a human
  log.md                    append-only operation log
```

The split is the whole idea. `raw/` is evidence and is only ever appended to.
`wiki/` is derived and can be deleted and rebuilt at any time. If you find
yourself editing a page under `wiki/`, fix the compiler or fix the source —
the next compile will overwrite you.

**Authority order**, declared in `okf.yaml` and used to resolve disagreements:

```
raw/wordings  >  raw/product-summaries  >  raw/benefit-tables
              >  raw/web/www.etiqa.com.sg  >  raw/web/www.tiq.com.sg  >  raw/blog
```

The contract outranks the marketing page. That ordering is why stage 2 exists.

---

## Prerequisites

```bash
make install          # uv sync
```

Python 3.11+ and `uv`. Stages 1–3 need network access to the target hosts;
stages 4–6 are fully offline.

---

## Stage 1 — Crawl

```bash
uv run python -m crawler.cli run \
  --allowlist www.etiqa.com.sg www.tiq.com.sg \
  --out okf-real/raw --rps 1.0
```

| flag | meaning |
|---|---|
| `--allowlist` | hosts to crawl. **Host equality, not substring** — `www.etiqa.com.sg.example.test` is a different site and is not crawled |
| `--out` | the bundle's `raw/` directory. Note it is `okf-real/raw`, **not** `okf-real` — getting this wrong writes the tiers one level too high |
| `--rps` | requests per second, per host. Token bucket. `robots.txt` `Crawl-delay` overrides it downward |
| `--max-pages` | stop after N pages, for a smoke test |
| `--ignore-robots` | only for a site you operate |
| `--fixture` | serve the synthetic `.example` site in-process — no network at all |

Discovery goes sitemap → sitemap-index → WordPress REST → bounded link crawl.
Snapshots are content-hashed and written under `raw/web/<host>/<date>/`, so
re-crawling the same day is idempotent and a changed page is visible as a
changed hash.

Verify with the fixture, which needs nothing:

```bash
uv run python -m crawler.cli run \
  --allowlist www.etiqa.example www.tiq.example \
  --out /tmp/bundle/raw --rps 200 --fixture
```

```
crawled 125 pages across 2 hosts
  www.etiqa.example: discovered 79, fetched 79
  www.tiq.example: discovered 46, fetched 46
    claims     35
    product    33
    faq        33
    servicing  14
  documents recorded (not chunked): 66
  manifest: /tmp/bundle/raw/web/crawl-manifest.json
```

**"documents recorded (not chunked)" is the important line.** PDFs are
inventoried here, not parsed. Treating a policy wording as web copy would flatten
the highest-authority tier into prose and lose its tables. Stage 2 handles them
properly.

If you see `no pages were retrieved — check egress policy for the allowlisted
hosts`, the crawler reached nothing: DNS, a proxy, or a firewall.

---

## Stage 2 — Parse the documents

This is the stage that fills the two highest-authority tiers. Without it the
manifest declares wordings authoritative and there are none.

```bash
uv run python -m crawler.cli documents \
  --manifest okf-real/raw/web/crawl-manifest.json \
  --out okf-real/raw
```

| flag | meaning |
|---|---|
| `--backend` | `auto` (default), `markitdown`, `docling`, `builtin` |
| `--ocr` | docling only. **Off by default and leave it off** unless you have genuine scans |
| `--max-documents` | 0 = no limit |
| `--keep-superseded` | also write older revisions; off by default |

Documents route by filename into `raw/wordings/`, `raw/product-summaries/` or
`raw/brochures/`. Backends, measured on a real 46-page wording:

| backend | tables | time | cost |
|---|---|---|---|
| `markitdown` *(auto picks this)* | **yes** — every benefit row | **3.9 s** | pdfplumber |
| `docling` | yes, cleaner column boundaries | 29 s | `uv sync --extra docling`, ~2 GB |
| `builtin` | **none** — flattened to prose | 2.4 s | pypdf |

The builtin backend returns 155k characters and **zero tables**: the Table of
Benefits arrives as a paragraph and every limit in it is lost. Use it only to
check whether a PDF has a text layer at all.

**OCR costs 9× for nothing** on insurer PDFs — 272 s against 29 s for identical
output — because they carry a text layer already.

On the real corpus this produced **168 wordings and 52 product summaries**. On
the fixture it correctly produces nothing, because fixture PDFs have no bytes
behind them:

```
ingested 0 documents with the 'markitdown' backend
  skipped   66  unreachable (ConnectError)
```

---

## Stage 3 — Published FAQs

The FAQs are a WordPress custom post type in no sitemap, rendered client-side.
The ordinary crawl cannot see them; this reads the REST API.

```bash
uv run python -m crawler.cli faqs \
  --allowlist www.etiqa.com.sg www.tiq.com.sg --out okf-real/raw
```

Not every host serves one, and the command says so rather than failing:

```
  www.etiqa.example           0 pairs   (no REST FAQ endpoint)
  www.tiq.example             0 pairs   (no REST FAQ endpoint)
no published FAQs found on any allowlisted host
```

On the real hosts, `tiq.com.sg` serves them and `etiqa.com.sg` returns 403.
That asymmetry is why this is a separate stage you can run alone.

---

## Stage 4 — Compile

Everything from here is offline. The compiler reads only what is on disk, so a
compile is reproducible from the repository alone.

```bash
uv run python -m compiler.cli --bundle okf-real wiki
```

Note `--bundle` comes **before** the subcommand.

```
compiled 107 pages into /tmp/bundle/wiki
  benefit tables: 22 products, 122 rows
  policy documents read: 0
  website defects filed: 4 (see /tmp/bundle/conflicts)
  skipped    1  no source sentence defines 'Excess'

pages are `draft`: nothing is retrievable until a human reviews them.
re-run with --sign-off <name> to record the review and mark them approved.
```

What the compile does, and why:

| crawled | compiled |
|---|---|
| the same plan on both websites | **one** `product/<line>/<slug>` page, one channel binding, two front doors |
| a benefit table in HTML | rows in `raw/benefit-tables/<slug>.csv`; prose keeps `{{table:…}}` |
| "What is not covered" | its own exclusions page, linked — traversable, not hoped for |
| a policy wording | exclusions, definitions, conditions, claims and cover pages, cited to the PDF and printed page |
| a wording for a product with no web page | the product page itself |
| a claims or servicing page | a `journey/` page |
| two front doors disagreeing on a figure | the higher-authority value, plus a **website defect ticket** |
| any page at all | `status: draft` |

**Read the skip lines.** They are the compiler telling you what it refused to
publish — a number it could not bind to a row, a table that turned out to be a
blog comparison grid, a paragraph carrying a channel-varying hotline. Silence
there would be worse than the skips.

### The conflicts directory

```
# Website defect — private-car ALL:own_damage.limit

- opened: 2026-08-26
- kept (higher authority): `S$5000` from `raw/web/www.etiqa.example/…#what-is-covered`
- contradicted: `S$2500` from `raw/web/www.tiq.example/…#what-is-covered`
```

This is a ticket against the **website**, not the wiki. Two published surfaces
disagree and a customer can read either. The wiki carries the higher-authority
value and someone should go fix the page.

---

## Stage 5 — Lint

```bash
uv run python -m compiler.cli --bundle okf-real lint
```

```
0 errors · 0 warnings
```

The linter is what stops a wiki rotting into confidently-wrong prose:

| rule | blocks |
|---|---|
| `source-ref` | a factual claim with no `[src:…]` |
| `number-in-prose` | a figure typed into a sentence instead of coming from a row |
| `broken-link` | a graph edge that resolves to nothing |
| `unbound-token` | `{{table:x.y}}` with no row for this product and version |
| `bare-route` | a hotline or deep link baked into a product page |
| `approval` | an approved page with no `reviewed_by`, `review_due` or `authority` |

Errors block `approved`. Fix them by fixing the compiler or the source, then
recompiling — never by editing a page under `wiki/`.

---

## Stage 6 — Review

**A freshly compiled bundle answers nothing, deliberately.** Every page is
`draft`; the frontmatter filter admits only `approved`, and citing a draft page
fails the `reference-integrity` gate.

Confirm it for yourself. Against the 107-page bundle just compiled:

```
Q: What is the baggage loss limit on travel insurance?
A: I could not establish that from our approved product pages.
   Let me pass you to a colleague who can confirm it.
-> pages: []
```

That is not a bug. Compiled-from-a-crawl is not the same as fit to say to a
customer.

### The right way: a person reads the page

Open `/studio`, read the page, promote it. The button calls:

```bash
curl -X POST http://localhost:8080/v1/cms/pages/product/general/travel/benefits/status \
  -H 'Content-Type: application/json' \
  -d '{"status":"approved","actor":"jimmy@etiqa","review_months":3,
       "note":"checked against the 2026.1 wording"}'
```

```json
{"page_id":"product/general/travel/benefits","status":"approved","retrievable":true}
```

The overview moves, and `retrievable` moves with it:

```
before  {'approved': 0, 'draft': 107, 'retrievable': 0}
after   {'approved': 1, 'draft': 106, 'retrievable': 1}
```

And the page frontmatter records who, when, and why:

```yaml
status: approved
reviewed_by: ['reviewer:jimmy@etiqa']
review_due: 2026-11-24
review_note: checked against the 2026.1 wording
```

`review_due` matters: an overdue page is auto-demoted out of wiki-first
retrieval, because a stale trusted page is worse than no page.

### The fast way, and what it costs

```bash
uv run python -m compiler.cli --bundle okf-real wiki --sign-off "your-name"
```

This stamps **every** page `approved` at compile time and writes the name into
`reviewed_by`. It is how the committed `okf-real/` was built, with the actor
`UNREVIEWED-eval-only` — chosen to be impossible to miss in the frontmatter of
all 768 pages, because nobody has read them.

Use it for evaluation and measurement. Understand that using it for a customer-
facing bundle means shipping unreviewed compiled content, and that the system
will not stop you.

---

## The whole thing, two commands

```bash
make corpus           # stages 1-5. Hours of network. Hits the live sites at 1 rps.
make corpus-compile   # stages 4-5 only, from sources on disk. No network.
```

`corpus-compile` is the one you will use. Because every source is committed, it
reproduces the served wiki exactly and offline — which is also what makes a
compiler change reviewable: change the code, recompile, diff the bundle.

---

## Pointing this at a different insurer

Nothing above is Etiqa-specific except the data. What you would change:

| what | where |
|---|---|
| hosts | `--allowlist`, and `okf.yaml` `authority_order` |
| legal name, UEN | `LEGAL_NAME` / `UEN` in `apps/compiler/compiler/wiki.py` |
| distribution channels | `packages/okf/okf/channels.py` — the registry the gates bind to |
| product taxonomy | `LOB_RULES` in `wiki.py`; `product_roots` in `okf.yaml` |
| customer vocabulary | `okf/vocabulary.yaml` — customer words → benefit codes |
| document tiering | `TIER_RULES` in `apps/crawler/crawler/documents.py` |
| section vocabulary | `ROLE_RULES` in `apps/compiler/compiler/documents.py` |

The last two are where most of the work would be. Section detection is tuned to
how these insurers write their contracts — "What do we mean with these words?"
is a definitions heading here, and would not be everywhere.
