"""Build the vector indexes over a bundle — offline, never at request time.

    uv run python scripts/index_pgvector.py --bundle okf-real
    make index

Two indexes, built the same way and synced by the same function.

`chunk` holds one row per *wiki* section — heading bound to body, keyed by
`page_id#heading`, carrying the frontmatter columns retrieval filters on.

`raw_chunk` holds one row per section of the *immutable sources*, keyed by
`source_path#heading`. It is what makes the RAG fallback hybrid, and the
argument for it is the argument for the fallback itself: it fires on the
questions the wiki cannot answer — a clause the compiler did not lift, a
customer on a historic version, a phrasing that shares no vocabulary with the
contract — and those are precisely the questions a word-overlap search is
worst at. `--only wiki` or `--only raw` builds one of them.

Both are stamped with a content hash. A re-run embeds only sections whose hash
changed and deletes rows for sections that no longer exist, so a recompile
costs what it changed, not the whole corpus.

Deliberately not part of `Bundle.load` or the API's `_load()`. There are 26
other callers of `Bundle.load` — the compiler, the evaluators, the linter,
every test — and an embedding pass there would make `make evals` and CI depend
on a GPU. The API embeds only the *question*; this script embeds the pages.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
for package in ("apps/api", "packages/okf", "packages/harness"):
    sys.path.insert(0, str(ROOT / package))

EMBED_BATCH = 32

#: Shorter than a sentence of a contract. A section this small is a heading
#: with a cross-reference under it, and embedding it buys a near neighbour for
#: every question and an answer to none.
MIN_SECTION_CHARS = 40

#: What `raw/` holds that is not a source: build output, manifests, and the
#: conflict queue. `may_support` would refuse them at query time anyway; not
#: embedding them keeps the index the size of the corpus rather than the size
#: of the directory.
SKIP_RAW = ("raw/benefit-tables/",)


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _wiki_rows(bundle: Any, name: str) -> dict[str, dict[str, Any]]:
    """One row per compiled section, with the columns the ladder filters on."""
    from api.compose import split_sections
    from okf.linter import SOURCE_REF_RE

    from okf import UNCOMPILED_MARK, PageType

    rows: dict[str, dict[str, Any]] = {}
    for page in bundle.pages.values():
        fm = page.frontmatter
        if UNCOMPILED_MARK in page.body:
            continue
        product_key = bundle.product_key(page) if fm.type == PageType.product else None
        for heading, body in split_sections(page):
            body = SOURCE_REF_RE.sub("", body).strip()
            if len(body) < MIN_SECTION_CHARS:
                continue
            content = f"{fm.title} — {heading}\n{body}" if heading else f"{fm.title}\n{body}"
            rows[f"{page.id}#{heading}"] = {
                "bundle": name,
                "page_id": page.id,
                "heading": heading,
                "product_key": product_key,
                "page_type": fm.type.value,
                "status": fm.status.value,
                "lifecycle": fm.lifecycle.value,
                "jurisdiction": fm.jurisdiction,
                "version_in_force": fm.version_in_force,
                "effective_from": fm.effective_from,
                "effective_to": fm.effective_to,
                "review_due": fm.review_due,
                "authority": list(fm.authority),
                "compiled_by": str((fm.model_extra or {}).get("compiled_by") or "compiler"),
                "source_refs": sorted({m.group(1) for m in SOURCE_REF_RE.finditer(page.body)})[:20],
                "content": content,
                "content_hash": _hash(content),
            }
    return rows


def _raw_rows(root: Path, name: str) -> dict[str, dict[str, Any]]:
    """One row per section of the sources, split exactly as the query side
    splits them — `api.retrieval.raw_sections`, so the index and the search
    agree on what a section is and an exclusion is never separated from the
    benefit it qualifies (§E.2).

    `doc_type` is carried so the query side can apply `may_support` without
    re-reading the file. It is re-derived from the content there rather than
    trusted, because the classifier can change after the index is built — but
    storing it makes the common case cheap.
    """
    from api.retrieval import raw_sections
    from okf.sources import page_type_of_text

    rows: dict[str, dict[str, Any]] = {}
    raw = root / "raw"
    if not raw.is_dir():
        return rows
    for path in sorted(raw.rglob("*.md")):
        rel = f"raw/{path.relative_to(raw)}"
        if rel.startswith(SKIP_RAW):
            continue
        text = path.read_text(errors="ignore")
        doc_type = page_type_of_text(text)
        for heading, body in raw_sections(text):
            if len(body.strip()) < MIN_SECTION_CHARS:
                continue
            content = f"{heading}\n{body}".strip() if heading else body.strip()
            rows[f"{rel}#{heading}"] = {
                "bundle": name,
                "source_path": rel,
                "heading": heading,
                "doc_type": doc_type,
                "content": content,
                "content_hash": _hash(content),
            }
    return rows


def _sync(
    conn: Any,
    table: str,
    name: str,
    rows: dict[str, dict[str, Any]],
    embed_url: str,
    model: str,
    dry_run: bool,
) -> None:
    """Bring `table` in line with `rows`: delete what is gone, embed what
    changed, leave what did not.

    The upsert is generated from the row's own keys rather than written out
    per table. Two hand-written INSERTs listing nineteen columns each is two
    places for a column to go missing from the DO UPDATE clause and for the
    index to then serve a stale status field forever.
    """
    existing = conn.execute(f"SELECT id, content_hash FROM {table} WHERE bundle = %s", (name,))
    have = {r[0]: r[1] for r in existing.fetchall()}
    stale = sorted(cid for cid in have if cid not in rows)
    changed = sorted(cid for cid, row in rows.items() if have.get(cid) != row["content_hash"])
    print(
        f"  {table}: on disk {len(have)} · unchanged {len(rows) - len(changed)} · "
        f"to embed {len(changed)} · to delete {len(stale)}"
    )
    if dry_run:
        return
    if stale:
        conn.execute(f"DELETE FROM {table} WHERE bundle = %s AND id = ANY(%s)", (name, stale))
        conn.commit()
    if not changed:
        return

    columns = ["id", *rows[changed[0]].keys(), "embedding"]
    placeholders = ", ".join(f"%({c})s" for c in columns)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != "id")
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT (id) DO UPDATE SET {updates}, indexed_at = now()"
    )
    for start in range(0, len(changed), EMBED_BATCH):
        ids = changed[start : start + EMBED_BATCH]
        response = httpx.post(
            f"{embed_url}/embeddings",
            json={"model": model, "input": [str(rows[cid]["content"]) for cid in ids]},
            timeout=120,
        )
        response.raise_for_status()
        vectors = [item["embedding"] for item in response.json()["data"]]
        for cid, vec in zip(ids, vectors, strict=True):
            conn.execute(sql, {**rows[cid], "id": cid, "embedding": vec})
        conn.commit()
        print(f"    embedded {min(start + EMBED_BATCH, len(changed))}/{len(changed)}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path("okf-real"))
    parser.add_argument("--dsn", default="", help="defaults to PGVECTOR_DSN from .env")
    parser.add_argument("--embed-url", default="", help="defaults to EMBED_BASE_URL from .env")
    parser.add_argument("--dry-run", action="store_true", help="count what would change; embed nothing")
    parser.add_argument(
        "--only",
        choices=("wiki", "raw", "both"),
        default="both",
        help="which index to build (default: both)",
    )
    args = parser.parse_args()

    from api.settings import Settings

    from okf import Bundle

    settings = Settings(bundle_path=args.bundle)
    dsn = args.dsn or settings.pgvector_dsn
    embed_url = (args.embed_url or settings.embed_base_url).rstrip("/")
    if not dsn or not embed_url:
        print("need PGVECTOR_DSN and EMBED_BASE_URL (in .env or as flags)", file=sys.stderr)
        return 1

    bundle = Bundle.load(args.bundle)
    name = args.bundle.name

    wiki = _wiki_rows(bundle, name) if args.only in {"wiki", "both"} else {}
    raw = _raw_rows(args.bundle, name) if args.only in {"raw", "both"} else {}
    print(f"{name}: {len(bundle.pages)} pages → {len(wiki)} wiki sections, {len(raw)} raw sections")

    try:
        import psycopg
        from pgvector.psycopg import register_vector
    except ImportError:
        print("install the api package's `pgvector` extra: uv sync --extra pgvector", file=sys.stderr)
        return 1

    with psycopg.connect(dsn) as conn:
        register_vector(conn)
        if args.only in {"wiki", "both"}:
            _sync(conn, "chunk", name, wiki, embed_url, settings.embed_model, args.dry_run)
        if args.only in {"raw", "both"}:
            _sync(conn, "raw_chunk", name, raw, embed_url, settings.embed_model, args.dry_run)
        for view, table in (("chunk_fingerprint", "chunk"), ("raw_chunk_fingerprint", "raw_chunk")):
            fp = conn.execute(f"SELECT chunks, fingerprint FROM {view} WHERE bundle = %s", (name,)).fetchone()
            print(f"  {table}: {fp[0]} chunks, fingerprint {fp[1][:12]}…" if fp else f"  {table}: empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
