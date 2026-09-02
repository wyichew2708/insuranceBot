"""Build the vector index over a compiled bundle — offline, never at request time.

    uv run python scripts/index_pgvector.py --bundle okf-real
    make index

One row per wiki section, heading bound to body, keyed by `page_id#heading`
and stamped with a content hash. A re-run embeds only sections whose hash
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

import httpx

ROOT = Path(__file__).resolve().parents[1]
for package in ("apps/api", "packages/okf", "packages/harness"):
    sys.path.insert(0, str(ROOT / package))

EMBED_BATCH = 32


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path("okf-real"))
    parser.add_argument("--dsn", default="", help="defaults to PGVECTOR_DSN from .env")
    parser.add_argument("--embed-url", default="", help="defaults to EMBED_BASE_URL from .env")
    parser.add_argument("--dry-run", action="store_true", help="count what would change; embed nothing")
    args = parser.parse_args()

    from api.compose import SOURCE_REF_RE, split_sections
    from api.settings import Settings

    from okf import UNCOMPILED_MARK, Bundle, PageType

    settings = Settings(bundle_path=args.bundle)
    dsn = args.dsn or settings.pgvector_dsn
    embed_url = (args.embed_url or settings.embed_base_url).rstrip("/")
    if not dsn or not embed_url:
        print("need PGVECTOR_DSN and EMBED_BASE_URL (in .env or as flags)", file=sys.stderr)
        return 1

    bundle = Bundle.load(args.bundle)
    name = args.bundle.name

    # --- what the bundle says the index should hold --------------------------
    wanted: dict[str, dict[str, object]] = {}
    for page in bundle.pages.values():
        fm = page.frontmatter
        if UNCOMPILED_MARK in page.body:
            continue
        product_key = bundle.product_key(page) if fm.type == PageType.product else None
        for heading, body in split_sections(page):
            body = SOURCE_REF_RE.sub("", body).strip()
            if len(body) < 40:
                continue
            content = f"{fm.title} — {heading}\n{body}" if heading else f"{fm.title}\n{body}"
            wanted[f"{page.id}#{heading}"] = {
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
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            }
    print(f"{name}: {len(bundle.pages)} pages → {len(wanted)} sections")

    try:
        import psycopg
        from pgvector.psycopg import register_vector
    except ImportError:
        print("install the api package's `pgvector` extra: uv sync --extra pgvector", file=sys.stderr)
        return 1

    with psycopg.connect(dsn) as conn:
        register_vector(conn)
        have = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT id, content_hash FROM chunk WHERE bundle = %s", (name,)
            ).fetchall()
        }
        stale = [cid for cid in have if cid not in wanted]
        changed = [cid for cid, row in wanted.items() if have.get(cid) != row["content_hash"]]
        print(
            print(
                f"  on disk {len(have)} · unchanged {len(wanted) - len(changed)} · "
                f"to embed {len(changed)} · to delete {len(stale)}"
            )
        )
        if args.dry_run:
            return 0

        if stale:
            conn.execute("DELETE FROM chunk WHERE bundle = %s AND id = ANY(%s)", (name, stale))

        for start in range(0, len(changed), EMBED_BATCH):
            ids = changed[start : start + EMBED_BATCH]
            texts = [str(wanted[cid]["content"]) for cid in ids]
            response = httpx.post(
                f"{embed_url}/embeddings",
                json={"model": settings.embed_model, "input": texts},
                timeout=120,
            )
            response.raise_for_status()
            vectors = [item["embedding"] for item in response.json()["data"]]
            for cid, vec in zip(ids, vectors, strict=True):
                row = wanted[cid]
                conn.execute(
                    """
                    INSERT INTO chunk (id, bundle, page_id, heading, product_key, page_type, status,
                        lifecycle, jurisdiction, version_in_force, effective_from, effective_to,
                        review_due, authority, compiled_by, source_refs, content, content_hash, embedding)
                    VALUES (%(id)s, %(bundle)s, %(page_id)s, %(heading)s, %(product_key)s, %(page_type)s,
                        %(status)s, %(lifecycle)s, %(jurisdiction)s, %(version_in_force)s,
                        %(effective_from)s, %(effective_to)s, %(review_due)s, %(authority)s,
                        %(compiled_by)s, %(source_refs)s, %(content)s, %(content_hash)s, %(embedding)s)
                    ON CONFLICT (id) DO UPDATE SET
                        product_key = EXCLUDED.product_key, page_type = EXCLUDED.page_type,
                        status = EXCLUDED.status, lifecycle = EXCLUDED.lifecycle,
                        jurisdiction = EXCLUDED.jurisdiction, version_in_force = EXCLUDED.version_in_force,
                        effective_from = EXCLUDED.effective_from, effective_to = EXCLUDED.effective_to,
                        review_due = EXCLUDED.review_due, authority = EXCLUDED.authority,
                        compiled_by = EXCLUDED.compiled_by, source_refs = EXCLUDED.source_refs,
                        content = EXCLUDED.content, content_hash = EXCLUDED.content_hash,
                        embedding = EXCLUDED.embedding, indexed_at = now()
                    """,
                    {**row, "id": cid, "embedding": vec},
                )
            conn.commit()
            print(f"  embedded {min(start + EMBED_BATCH, len(changed))}/{len(changed)}", flush=True)

        fp = conn.execute(
            "SELECT chunks, fingerprint FROM chunk_fingerprint WHERE bundle = %s", (name,)
        ).fetchone()
        print(f"  index: {fp[0]} chunks, fingerprint {fp[1][:12]}…" if fp else "  index: empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
