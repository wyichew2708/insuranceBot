"""The content store — writing to the wiki, safely.

Everything the content portal does to the corpus goes through here, and every
write is validated before it lands. The rule the whole design rests on is that
an *approved* page has been read by a human and passes the linter; a store that
lets the UI write around that is a store that quietly turns the wiki back into
prose someone typed.

So: a candidate page is linted against a shadow copy of the bundle *before*
anything touches the disk, errors block the write, and a status promotion to
`approved` records who signed it off and when it must be looked at again.
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from okf.linter import Violation
from okf.page import Frontmatter, PageType

from okf import Bundle, Page, Status, lint_bundle, parse_page, render_page

ID_RE = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)*")
CUSTOM_SOURCE_DIR = "raw/custom"


class StoreError(Exception):
    """A write the store refuses. Carries the violations that caused it."""

    def __init__(self, message: str, violations: list[Violation] | None = None) -> None:
        super().__init__(message)
        self.violations = violations or []


@dataclass
class SaveResult:
    page_id: str
    path: str
    violations: list[Violation] = field(default_factory=list)
    created: bool = False


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _as_violation_dicts(violations: list[Violation]) -> list[dict[str, Any]]:
    return [
        {
            "page_id": v.page_id,
            "rule": v.rule,
            "message": v.message,
            "severity": v.severity.value,
            "line": v.line,
        }
        for v in violations
    ]


class ContentStore:
    """Reads and writes OKF pages under a bundle root.

    Holds no cache of its own: the caller passes the loaded bundle in, and
    reloads it after a write. Two sources of truth for the corpus would be one
    too many.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # --- paths ------------------------------------------------------------

    def path_for(self, page_id: str) -> Path:
        return self.root / "wiki" / f"{page_id}.md"

    # --- validation -------------------------------------------------------

    def validate(self, bundle: Bundle, page: Page) -> list[Violation]:
        """Lint the candidate against a bundle that already contains it.

        Linting the page alone would miss the two rules that only exist in
        context: link targets must resolve, and every approved product page
        must be listed in the index.
        """
        shadow = Bundle(
            root=bundle.root,
            manifest=bundle.manifest,
            pages={**bundle.pages, page.id: page},
            tables=bundle.tables,
        )
        shadow._build_alias_index()
        report = lint_bundle(shadow)
        return [v for v in report.violations if v.page_id in {page.id, "<bundle>"}]

    def build_page(self, frontmatter: dict[str, Any], body: str) -> Page:
        try:
            fm = Frontmatter.model_validate(frontmatter)
        except Exception as exc:
            raise StoreError(f"invalid frontmatter: {exc}") from exc
        if not ID_RE.fullmatch(fm.id):
            raise StoreError(f"id {fm.id!r} must be a lowercase slug path")
        return Page(frontmatter=fm, body=body.strip() + "\n")

    # --- reads ------------------------------------------------------------

    def summaries(self, bundle: Bundle, today: dt.date) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for page in sorted(bundle.pages.values(), key=lambda p: p.id):
            fm = page.frontmatter
            extra = fm.model_extra or {}
            out.append(
                {
                    "id": page.id,
                    "title": fm.title,
                    "type": fm.type.value,
                    "status": fm.status.value,
                    "lifecycle": fm.lifecycle.value,
                    "line_of_business": fm.line_of_business,
                    "version_in_force": fm.version_in_force,
                    "confidence": fm.confidence.value,
                    "regulated_advice": fm.regulated_advice,
                    "aliases": list(fm.aliases),
                    "tags": [str(t) for t in (extra.get("tags") or [])],
                    "channels": [c.ref for c in fm.channels],
                    "reviewed_by": list(fm.reviewed_by),
                    "review_due": fm.review_due.isoformat() if fm.review_due else None,
                    "review_overdue": fm.is_review_overdue(today),
                    "effective": fm.is_effective_on(today),
                    "retrievable": bundle.retrievable(page, today),
                    "authority": list(fm.authority),
                    "words": len(page.body.split()),
                    "source_path": page.source_path,
                }
            )
        return out

    # --- writes -----------------------------------------------------------

    def save(
        self,
        bundle: Bundle,
        frontmatter: dict[str, Any],
        body: str,
        *,
        actor: str,
        allow_create: bool = False,
    ) -> SaveResult:
        page = self.build_page(frontmatter, body)
        path = self.path_for(page.id)
        exists = path.exists()
        if not exists and not allow_create:
            raise StoreError(f"no page {page.id!r}; use create")
        if exists and allow_create:
            raise StoreError(f"page {page.id!r} already exists")

        violations = self.validate(bundle, page)
        errors = [v for v in violations if v.severity.value == "error"]
        if errors:
            raise StoreError(
                f"{len(errors)} lint error(s) — the wiki does not accept a page that fails its own rules",
                violations,
            )

        page.frontmatter.compiled_at = dt.datetime.now().replace(microsecond=0)
        extra = page.frontmatter.model_extra
        if extra is not None:
            extra["last_edited_by"] = actor
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_page(page))
        return SaveResult(
            page_id=page.id,
            path=str(path.relative_to(self.root)),
            violations=violations,
            created=not exists,
        )

    def create_custom(
        self,
        bundle: Bundle,
        *,
        page_id: str,
        title: str,
        page_type: str,
        body: str,
        source_text: str,
        actor: str,
        tags: list[str] | None = None,
        aliases: list[str] | None = None,
        line_of_business: str | None = None,
    ) -> SaveResult:
        """Author a page by hand — and give it a source.

        Hand-written content is not exempt from provenance. The author's
        supporting material is written to `raw/custom/<slug>.md` and becomes
        the page's authority, so `[src:…]` references resolve to a real file
        and the reference-integrity gate treats the page like any other.
        """
        source_ref = f"{CUSTOM_SOURCE_DIR}/{slugify(page_id)}.md"
        source_path = self.root / source_ref
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(
            f"# {title}\n\n"
            f"Authored in the content portal by {actor} on {dt.date.today().isoformat()}.\n"
            f"This file is the source of record for `{page_id}`; the page cites it.\n\n"
            "## Source material\n\n"
            f"{source_text.strip() or '(none supplied)'}\n"
        )
        frontmatter: dict[str, Any] = {
            "id": page_id,
            "title": title,
            "type": page_type,
            "status": Status.draft.value,
            "jurisdiction": "SG",
            "aliases": aliases or [],
            "authority": [source_ref],
            "effective_from": dt.date.today().isoformat(),
            "confidence": "medium",
            "tags": tags or [],
            "authored": True,
            "last_edited_by": actor,
        }
        if line_of_business:
            frontmatter["line_of_business"] = line_of_business
        try:
            return self.save(bundle, frontmatter, body, actor=actor, allow_create=True)
        except StoreError:
            # Do not leave an orphan source file behind a rejected page.
            source_path.unlink(missing_ok=True)
            raise

    def set_status(
        self,
        bundle: Bundle,
        page_id: str,
        status: str,
        *,
        actor: str,
        review_months: int = 3,
        note: str = "",
    ) -> SaveResult:
        page = bundle.get(page_id)
        if page is None:
            raise StoreError(f"no page {page_id!r}")
        data = page.frontmatter.model_dump(mode="json", exclude_none=True)
        data["status"] = status
        if status == Status.approved.value:
            # Approval is a signature, not a dropdown. It records who, and when
            # the page must be looked at again.
            signoff = f"reviewer:{actor}"
            data["reviewed_by"] = sorted({*data.get("reviewed_by", []), signoff})
            data["review_due"] = (dt.date.today() + dt.timedelta(days=30 * review_months)).isoformat()
        if note:
            data["review_note"] = note
        return self.save(bundle, data, page.body, actor=actor)

    def set_tags(self, bundle: Bundle, page_id: str, tags: list[str], *, actor: str) -> SaveResult:
        page = bundle.get(page_id)
        if page is None:
            raise StoreError(f"no page {page_id!r}")
        data = page.frontmatter.model_dump(mode="json", exclude_none=True)
        data["tags"] = sorted({slugify(t) for t in tags if t.strip()})
        return self.save(bundle, data, page.body, actor=actor)

    def delete(self, bundle: Bundle, page_id: str) -> dict[str, Any]:
        page = bundle.get(page_id)
        if page is None:
            raise StoreError(f"no page {page_id!r}")
        incoming = [p.id for p in bundle.pages.values() if page_id in bundle.neighbours(p.id)]
        if incoming:
            raise StoreError(f"{len(incoming)} page(s) link to {page_id!r}: {', '.join(incoming[:5])}")
        self.path_for(page_id).unlink(missing_ok=True)
        return {"deleted": page_id, "was_status": page.frontmatter.status.value}

    # --- import from a staged compile ------------------------------------

    def adopt(self, bundle: Bundle, staged_root: Path, page_id: str, *, actor: str) -> SaveResult:
        """Copy a page from a scan's staging area into the live wiki as a draft.

        Never as approved: a scan proposes, a person disposes. The staged page
        keeps its compiled provenance, so the diff a reviewer sees in the
        portal is the diff that was actually applied.
        """
        source = staged_root / "wiki" / f"{page_id}.md"
        if not source.exists():
            raise StoreError(f"{page_id!r} is not in this scan")
        staged = parse_page(source.read_text())
        data = staged.frontmatter.model_dump(mode="json", exclude_none=True)
        data["status"] = Status.draft.value
        data["reviewed_by"] = []
        data["last_edited_by"] = f"scan:{actor}"
        return self.save(bundle, data, staged.body, actor=actor, allow_create=True)

    def adopt_tables(self, staged_root: Path, product: str) -> str:
        """Take a scan's benefit table for one product wholesale.

        Figures move as a set. Copying one drifted row and leaving its
        neighbours behind produces a table that is internally inconsistent —
        exactly the state the numeric-binding gate cannot detect, because every
        individual row still resolves.
        """
        source = staged_root / "raw" / "benefit-tables" / f"{product}.csv"
        if not source.exists():
            raise StoreError(f"no staged benefit table for {product!r}")
        target = self.root / "raw" / "benefit-tables" / f"{product}.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return str(target.relative_to(self.root))


def taxonomy(bundle: Bundle) -> dict[str, Any]:
    """Every grouping the portal can filter by, counted. Derived from the
    corpus rather than configured, so a new tag appears the moment it is used."""
    counters: dict[str, dict[str, int]] = {
        "type": {},
        "status": {},
        "line_of_business": {},
        "tags": {},
        "lifecycle": {},
        "confidence": {},
        "channels": {},
    }

    def bump(bucket: str, key: str | None) -> None:
        if not key:
            return
        counters[bucket][key] = counters[bucket].get(key, 0) + 1

    for page in bundle.pages.values():
        fm = page.frontmatter
        bump("type", fm.type.value)
        bump("status", fm.status.value)
        bump("line_of_business", fm.line_of_business)
        bump("lifecycle", fm.lifecycle.value)
        bump("confidence", fm.confidence.value)
        for channel in fm.channels:
            bump("channels", channel.ref)
        for tag in (fm.model_extra or {}).get("tags") or []:
            bump("tags", str(tag))
    return {
        bucket: [{"value": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        for bucket, counts in counters.items()
    } | {"page_types": [t.value for t in PageType], "statuses": [s.value for s in Status]}
