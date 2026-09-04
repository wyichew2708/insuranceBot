"""Expand the conversation taxonomy into the golden dataset.

    uv run python scripts/conversation_suite.py --bundle okf-real
    make conversation-suite

`evals/taxonomy/conversation.yaml` is the authored half: what customers ask,
and — the part that matters — what a correct reply is for each. This expands
it across the product catalogue into `evals/suites/conversation.yaml`, which is
what the runner executes.

Two things it does that a list of questions cannot.

**It resolves the product axis.** A template scoped `product` becomes one case
per product in the catalogue, asked with that product's own customer-facing
name, so "what does {name} cover" is asked of all thirty-seven. A template
scoped to a line becomes one case against the line's flagship, or one per
member where the question is worth asking of each — "does it cover skiing" is a
travel question and asking it of a marine cargo policy tests nothing.

**It resolves the expectation.** Each template names a behaviour contract; the
contract, the template's own overrides and the dataset-wide hygiene assertions
are merged into the `expect` block the runner checks. `cite_product` is filled
in from the product the case was generated for, so a product question that is
answered about a *different* product fails, which is the failure this corpus
produces most often.

The output is committed. A golden dataset that is regenerated on every run is
not a golden dataset — it is a moving target, and a score against it means
nothing across two builds.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / p) for p in ("packages/okf", "packages/harness", "apps/api")]

TAXONOMY = ROOT / "evals" / "taxonomy" / "conversation.yaml"
OUT = ROOT / "evals" / "suites" / "conversation.yaml"

#: `expect` keys whose values are lists and must be unioned rather than
#: replaced when a template overrides its contract. Everything else is a
#: scalar and the template wins.
LIST_KEYS = ("must_contain", "must_not_contain", "must_cite", "gates_pass", "gates_fail")


def _merge(*blocks: dict[str, Any]) -> dict[str, Any]:
    """Later blocks win on scalars; list-valued keys accumulate in order."""
    out: dict[str, Any] = {}
    for block in blocks:
        for key, value in (block or {}).items():
            if key in LIST_KEYS:
                merged = list(out.get(key, []))
                merged += [v for v in value if v not in merged]
                out[key] = merged
            else:
                out[key] = value
    return out


def _catalogue(bundle_root: Path) -> dict[str, dict[str, Any]]:
    """slug → catalogue entry, for the products that actually compiled.

    Read from `catalogue.yaml` rather than from the wiki, because the
    customer-facing *name* is what a customer types and the compiled page's
    title is not always it. Filtered against the bundle, because a catalogue
    entry whose pages did not compile is not a product this dataset can ask
    about — and a case that could never pass is noise, not a finding.

    `regulated_advice` is carried over from the compiled page. A product the
    frontmatter marks as advised may not receive a factual-only answer without
    an adviser handoff path (§F.2), so *every* answer about it carries the
    advice flag — including "what is the maximum entry age". The first cut of
    this dataset asserted `advice_flag: false` on all product facts and scored
    Tiq Invest, the one advised product in the catalogue, at 1/27. The bot was
    right and the dataset was wrong.
    """
    from okf import Bundle, PageType

    bundle = Bundle.load(bundle_root)
    compiled = {
        page.id.rsplit("/", 1)[-1]: page
        for page in bundle.pages.values()
        if page.frontmatter.type is PageType.product and page.id.count("/") == 2
    }
    data = yaml.safe_load((bundle_root / "catalogue.yaml").read_text())
    out: dict[str, dict[str, Any]] = {}
    for entry in data.get("products", []):
        slug = str(entry["slug"])
        page = compiled.get(slug)
        if page is not None:
            out[slug] = {**entry, "regulated_advice": bool(page.frontmatter.regulated_advice)}
    missing = sorted({str(e["slug"]) for e in data.get("products", [])} - set(out))
    if missing:
        print(f"  skipped {len(missing)} catalogue entries with no compiled product page: {missing}")
    advised = sorted(s for s, e in out.items() if e["regulated_advice"])
    print(f"  regulated-advice products (every answer must carry the flag): {advised or 'none'}")
    return out


def _targets(
    template: dict[str, Any], catalogue: dict[str, dict[str, Any]], lines: dict[str, list[str]]
) -> list[str]:
    """The products this template is asked about — [] for a global question."""
    scope = str(template.get("scope", "global"))
    if scope == "global":
        return []
    if scope == "product":
        return sorted(catalogue)
    if scope.startswith("line:"):
        name = scope.split(":", 1)[1]
        members = [slug for slug in lines.get(name, []) if slug in catalogue]
        if not members:
            raise SystemExit(f"{template['id']}: line {name!r} has no compiled products")
        return members if template.get("fanout") == "all" else members[:1]
    raise SystemExit(f"{template['id']}: unknown scope {scope!r}")


def build(bundle_root: Path) -> list[dict[str, Any]]:
    spec = yaml.safe_load(TAXONOMY.read_text())
    contracts = spec["contracts"]
    lines = spec["lines"]
    hygiene = {"must_not_contain": list(spec.get("hygiene", []))}
    today = str(spec["evaluation_date"])
    catalogue = _catalogue(bundle_root)
    print(f"  {len(catalogue)} products, {len(spec['templates'])} templates")

    cases: list[dict[str, Any]] = []
    for template in spec["templates"]:
        contract_name = str(template["contract"])
        contract = contracts[contract_name]
        # `[""]` for a global template: one pass, no product, and the empty
        # slug is what `case["product"]` records for a question that names none.
        targets = _targets(template, catalogue, lines) or [""]
        for slug in targets:
            entry = catalogue.get(slug, {})
            name = str(entry.get("name", ""))
            # `cite_product` is what makes a product question a product
            # question. Only where the contract says the corpus can answer it:
            # a handoff carries no claims, so asserting a citation on one would
            # be asserting the bot answered something it should not have.
            pinned: dict[str, Any] = (
                {"cite_product": slug} if slug and contract_name in {"product_fact", "corpus_fact"} else {}
            )
            # An advised product's every answer is advice-flagged, whatever the
            # question was, so the contract's `advice_flag: false` is wrong for
            # it and the correct assertion is the opposite one.
            if entry.get("regulated_advice") and contract_name == "product_fact":
                pinned |= {"advice_flag": True, "gates_pass": ["advice-boundary"]}
            expect = _merge(hygiene, contract, pinned, template.get("expect") or {})
            case: dict[str, Any] = {
                "id": f"{template['id']}--{slug}" if slug else str(template["id"]),
                # The three layers, carried onto every case so the report can
                # group by them. The runner ignores them; `conversation_report`
                # is the reason they are here.
                "section": template["section"],
                "journey": template["journey"],
                "intent": template["intent"],
                "entities": list(template.get("entities", [])),
                "contract": contract_name,
                "product": slug or "",
                "brand": entry.get("brand", ""),
                "lifecycle": entry.get("status", ""),
                # Pinned, not defaulted to the wall clock: `okf-real` pages are
                # review-due 2026-12-02, and a dataset whose score depends on
                # the day it is run is not a golden dataset.
                "session": {"channel": "channel/direct", "auth_level": "L0", "today": today},
                "expect": expect,
            }
            if template.get("turns"):
                case["turns"] = [str(t).format(name=name) for t in template["turns"]]
            else:
                case["question"] = str(template["ask"]).format(name=name)
            cases.append(case)
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "okf-real")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    cases = build(args.bundle)
    header = (
        "# The golden conversation dataset — GENERATED, do not edit by hand.\n"
        "#\n"
        "#   uv run python scripts/conversation_suite.py --bundle "
        f"{args.bundle.name}\n"
        "#\n"
        "# Authored source: evals/taxonomy/conversation.yaml — that is the file to\n"
        "# argue with. It carries the question list *and the contract for a correct\n"
        "# reply*, which is the half that decides whether a score means anything:\n"
        "# roughly a third of what customers ask an insurer is not answerable from a\n"
        "# knowledge corpus at all, and for those the correct behaviour is a handoff.\n"
        "#\n"
        f"# {len(cases)} cases. Every case carries its journey, intent and entity\n"
        "# labels; `scripts/conversation_report.py` groups the score by them.\n"
        "#\n"
        "# The evaluation date is pinned. okf-real pages are review-due 2026-12-02,\n"
        "# so a suite that let the session default to the wall clock would start\n"
        "# failing every case on 2026-12-03 for reasons that are not the bot's.\n"
    )
    body = yaml.safe_dump(
        {"bundle": args.bundle.name, "cases": cases},
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    args.out.write_text(header + body)
    print(f"  wrote {len(cases)} cases → {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
