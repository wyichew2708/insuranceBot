"""Reading the crawler's raw snapshots (§C.1, §D.1).

Snapshots are the immutable interface between the crawl and the compile loop:
dated, content-hashed Markdown with a JSON-ish frontmatter block. Nothing in
this module reaches the network — it only parses what the crawl already wrote,
so a compile is reproducible from the repository alone.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|$")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@dataclass
class Section:
    heading: str
    text: str

    @property
    def anchor(self) -> str:
        return slugify(self.heading) or "body"

    @property
    def paragraphs(self) -> list[str]:
        return [p.strip() for p in re.split(r"\n\s*\n", self.text) if p.strip()]


@dataclass
class Table:
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Snapshot:
    path: Path
    meta: dict[str, str]
    sections: list[Section] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)

    @property
    def url(self) -> str:
        return self.meta.get("canonical_url") or self.meta.get("source_url", "")

    @property
    def host(self) -> str:
        return self.meta.get("host", "")

    @property
    def title(self) -> str:
        return self.meta.get("title", "")

    @property
    def page_type(self) -> str:
        return self.meta.get("page_type", "other")

    @property
    def slug(self) -> str:
        """Last path segment of the URL — the product key on a product,
        claims or FAQ page."""
        path = self.url.split("://", 1)[-1].split("?", 1)[0].rstrip("/")
        return path.rsplit("/", 1)[-1] if "/" in path else ""

    def ref(self, anchor: str = "") -> str:
        """The `[src:...]` locator: bundle-relative path plus a section anchor."""
        rel = self.meta["_ref"]
        return f"{rel}#{anchor}" if anchor else rel

    def section(self, *names: str) -> Section | None:
        wanted = {slugify(n) for n in names}
        for section in self.sections:
            if slugify(section.heading) in wanted:
                return section
        return None

    @property
    def intro(self) -> str:
        for section in self.sections:
            if not section.heading:
                return section.text
        return ""

    @property
    def text(self) -> str:
        return "\n\n".join(s.text for s in self.sections)


def parse_snapshot(path: Path, bundle_root: Path) -> Snapshot:
    raw = path.read_text()
    if not raw.startswith("---"):
        raise ValueError(f"{path} has no frontmatter")
    _, block, body = raw.split("---", 2)
    meta: dict[str, str] = {}
    for line in block.strip().splitlines():
        key, _, value = line.partition(":")
        try:
            meta[key.strip()] = json.loads(value.strip())
        except json.JSONDecodeError:
            meta[key.strip()] = value.strip()
    meta["_ref"] = path.relative_to(bundle_root).as_posix()

    snapshot = Snapshot(path=path, meta=meta)
    heading = ""
    buffer: list[str] = []
    table_lines: list[str] = []

    def flush() -> None:
        if table_lines:
            snapshot.tables.append(_parse_table(table_lines))
            table_lines.clear()
        text = "\n".join(buffer).strip()
        buffer.clear()
        if not text and not heading:
            return
        # The extractor emits the <h1> as a heading too; it repeats the title
        # and carries the page's opening prose, which belongs to the intro.
        name = "" if slugify(heading) == slugify(snapshot.title) else heading
        if name.lower().startswith("table "):
            return
        snapshot.sections.append(Section(name, text))

    for line in body.splitlines():
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            flush()
            heading = line[3:].strip()
            continue
        if line.startswith("|"):
            table_lines.append(line)
            continue
        buffer.append(line)
    flush()
    return snapshot


def _parse_table(lines: list[str]) -> Table:
    table = Table()
    for line in lines:
        if SEPARATOR_RE.match(line):
            continue
        match = TABLE_ROW_RE.match(line.strip())
        if not match:
            continue
        cells = [c.strip() for c in match.group(1).split("|")]
        if not table.header:
            table.header = cells
        else:
            table.rows.append(cells)
    return table


def load_snapshots(bundle_root: Path) -> list[Snapshot]:
    """Every snapshot in the bundle's raw/web tree, newest date per URL."""
    web = bundle_root / "raw" / "web"
    if not web.is_dir():
        return []
    latest: dict[str, Snapshot] = {}
    for path in sorted(web.rglob("*.md")):
        snapshot = parse_snapshot(path, bundle_root)
        if not snapshot.url:
            continue
        previous = latest.get(snapshot.url)
        if previous is None or snapshot.path.parent.name >= previous.path.parent.name:
            latest[snapshot.url] = snapshot
    return sorted(latest.values(), key=lambda s: (s.host, s.url))
