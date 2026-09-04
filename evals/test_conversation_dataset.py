"""The golden dataset's own integrity — not the bot's score, the dataset's.

A golden set is only worth the trust you put in it, and the ways one rots are
mechanical: a template stops expanding because a product was renamed, two cases
collide on an id and one silently replaces the other, a contract is referenced
that no longer exists, the committed suite drifts from the taxonomy it claims to
come from. None of those show up as a failing case — they show up as a score
that quietly measures less than it says it does.
"""

from __future__ import annotations

import collections
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = ROOT / "evals" / "taxonomy" / "conversation.yaml"
SUITE = ROOT / "evals" / "suites" / "conversation.yaml"


@pytest.fixture(scope="module")
def taxonomy() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(TAXONOMY.read_text())
    return loaded


@pytest.fixture(scope="module")
def suite() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(SUITE.read_text())
    return loaded


def test_every_template_names_a_contract_that_exists(taxonomy: dict[str, Any]) -> None:
    known = set(taxonomy["contracts"])
    unknown = sorted({t["contract"] for t in taxonomy["templates"]} - known)
    assert not unknown, f"templates reference contracts that do not exist: {unknown}"


def test_every_line_names_products_and_no_template_names_a_missing_line(taxonomy: dict[str, Any]) -> None:
    lines = taxonomy["lines"]
    assert all(members for members in lines.values()), "a line with no products expands to nothing"
    wanted = {
        t["scope"].split(":", 1)[1] for t in taxonomy["templates"] if str(t["scope"]).startswith("line:")
    }
    assert not (wanted - set(lines)), f"templates scope to lines that are not defined: {wanted - set(lines)}"


def test_template_ids_are_unique(taxonomy: dict[str, Any]) -> None:
    counts = collections.Counter(t["id"] for t in taxonomy["templates"])
    assert not [i for i, n in counts.items() if n > 1]


def test_every_template_asks_something(taxonomy: dict[str, Any]) -> None:
    for template in taxonomy["templates"]:
        assert template.get("ask") or template.get("turns"), f"{template['id']} asks nothing"
        assert not (template.get("ask") and template.get("turns")), f"{template['id']}: ask and turns"


def test_a_product_template_uses_the_product_name(taxonomy: dict[str, Any]) -> None:
    """A product-scoped template that never interpolates `{name}` generates
    thirty-seven identical questions and one real case, which is a way of
    reporting the same finding thirty-seven times."""
    for template in taxonomy["templates"]:
        if str(template["scope"]) == "global":
            continue
        text = " ".join([str(template.get("ask", "")), *map(str, template.get("turns") or [])])
        assert "{name}" in text, f"{template['id']} is product-scoped but names no product"


def test_case_ids_are_unique(suite: dict[str, Any]) -> None:
    counts = collections.Counter(c["id"] for c in suite["cases"])
    duplicates = [i for i, n in counts.items() if n > 1]
    assert not duplicates, f"a duplicate id silently drops a case: {duplicates}"


def test_every_case_carries_all_three_layers(suite: dict[str, Any]) -> None:
    for case in suite["cases"]:
        for label in ("section", "journey", "intent", "entities", "contract"):
            assert case.get(label), f"{case['id']} has no {label}; the report cannot group it"


def test_the_evaluation_date_is_pinned(taxonomy: dict[str, Any], suite: dict[str, Any]) -> None:
    """Not the wall clock. okf-real pages are review-due 2026-12-02, so a
    dataset that defaulted to today would start failing every case on
    2026-12-03 for a reason that is not the bot's."""
    pinned = str(taxonomy["evaluation_date"])
    assert all(c["session"]["today"] == pinned for c in suite["cases"])


def test_a_handoff_case_never_asserts_a_citation(suite: dict[str, Any]) -> None:
    """A handoff carries no claims. Asserting a citation on one would assert
    the bot answered something the contract says it must not."""
    for case in suite["cases"]:
        if case["contract"] in {"handoff", "out_of_scope"}:
            assert "cite_product" not in case["expect"], case["id"]


def test_the_hygiene_assertions_reach_every_case(taxonomy: dict[str, Any], suite: dict[str, Any]) -> None:
    for needle in taxonomy["hygiene"]:
        assert all(needle in c["expect"]["must_not_contain"] for c in suite["cases"]), needle


def test_the_committed_suite_matches_the_taxonomy(suite: dict[str, Any]) -> None:
    """The suite is generated and committed, so the two can drift — a template
    added and never expanded measures nothing. Regenerate with
    `make conversation-suite`."""
    bundle_root = ROOT / suite["bundle"]
    if not bundle_root.is_dir():
        pytest.skip(f"bundle {suite['bundle']} is not in this checkout")
    assert _generator().build(bundle_root) == suite["cases"], "run `make conversation-suite`"


def _generator() -> Any:
    """Load the generator by path rather than as `scripts.conversation_suite`.

    `scripts/` is a directory of standalone entry points, not a package, and
    importing one under a dotted name makes mypy see the same file under two
    module names and refuse to run at all.
    """
    import importlib.util

    path = ROOT / "scripts" / "conversation_suite.py"
    spec = importlib.util.spec_from_file_location("_conversation_suite", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
