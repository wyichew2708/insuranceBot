"""Content portal tests.

The portal's promise is that you cannot use it to break the corpus, so most of
these assert a *refusal*: the linter rules are enforced on the way in, not
reported after the fact.
"""

from __future__ import annotations

import datetime as dt
import shutil
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from api import main as api_main
from api.scan import Suggestion, verify
from api.settings import Settings
from api.store import ContentStore, StoreError, taxonomy
from fastapi.testclient import TestClient

from okf import Bundle, Status

BUNDLE_ROOT = Path(__file__).resolve().parents[3] / "okf"


@pytest.fixture()
def bundle_root(tmp_path: Path) -> Path:
    """A throwaway copy of the seed bundle: these tests write to it."""
    root = tmp_path / "okf"
    shutil.copytree(BUNDLE_ROOT, root)
    return root


@pytest.fixture()
def client(bundle_root: Path) -> Iterator[TestClient]:
    root = bundle_root
    api_main._state["settings"] = Settings(bundle_path=root)
    api_main._state["bundle"] = None
    with TestClient(api_main.app) as test_client:
        yield test_client
    api_main._state["settings"] = None
    api_main._state["bundle"] = None


def test_studio_is_served(client: TestClient) -> None:
    response = client.get("/studio")
    assert response.status_code == 200
    assert "Content Studio" in response.text


def test_overview_reports_what_actually_answers(client: TestClient) -> None:
    data = client.get("/v1/cms/overview").json()
    assert data["bundle"]["pages"] > 0
    health = data["health"]
    # Retrievable is not the same as existing, and the portal must not blur it.
    assert health["retrievable"] <= data["bundle"]["pages"]
    assert health["lint_errors"] == 0
    assert {"type", "status", "tags"} <= set(data["taxonomy"])


def test_page_listing_filters_and_search(client: TestClient) -> None:
    everything = client.get("/v1/cms/pages").json()
    products = client.get("/v1/cms/pages?type=product").json()
    assert 0 < products["count"] < everything["count"]
    assert all(p["type"] == "product" for p in products["pages"])

    found = client.get("/v1/cms/pages?q=travel").json()
    assert found["count"]
    assert all("travel" in (p["id"] + p["title"] + " ".join(p["aliases"])).lower() for p in found["pages"])


def test_reading_a_page_shows_its_graph_and_its_figures(client: TestClient) -> None:
    page = client.get("/v1/cms/pages/product/general/travel").json()
    assert page["id"] == "product/general/travel"
    assert "product/general/travel/exclusions" in page["neighbours"]
    assert page["table_rows"], "a product page should surface the rows its figures come from"
    assert all(row["row_id"].startswith("travel:") for row in page["table_rows"])


def test_a_number_typed_into_prose_is_refused(client: TestClient) -> None:
    page = client.get("/v1/cms/pages/product/general/travel").json()
    body = page["body"] + "\n\n## Handy summary\n\nThe medical limit is S$500,000 for every tier.\n"
    response = client.put(
        "/v1/cms/pages/product/general/travel",
        json={"frontmatter": page["frontmatter"], "body": body, "actor": "test"},
    )
    assert response.status_code == 422
    rules = {v["rule"] for v in response.json()["detail"]["violations"]}
    assert "number-in-prose" in rules
    # And the file on disk is untouched.
    assert "S$500,000" not in client.get("/v1/cms/pages/product/general/travel").json()["body"]


def test_an_unreferenced_claim_is_refused(client: TestClient) -> None:
    page = client.get("/v1/cms/pages/concept/excess").json()
    body = page["body"] + "\n\nThis paragraph asserts something with no source at all.\n"
    response = client.put(
        "/v1/cms/pages/concept/excess",
        json={"frontmatter": page["frontmatter"], "body": body, "actor": "test"},
    )
    assert response.status_code == 422
    assert "source-ref" in {v["rule"] for v in response.json()["detail"]["violations"]}


def test_lint_preview_never_writes(client: TestClient) -> None:
    page = client.get("/v1/cms/pages/concept/excess").json()
    before = page["body"]
    result = client.post(
        "/v1/cms/lint",
        json={"frontmatter": page["frontmatter"], "body": "## X\n\nNo reference here at all, sorry.\n"},
    ).json()
    assert result["ok"] is False and result["violations"]
    assert client.get("/v1/cms/pages/concept/excess").json()["body"] == before


def test_a_valid_edit_saves_and_reloads(client: TestClient) -> None:
    page = client.get("/v1/cms/pages/concept/excess").json()
    addition = (
        "\n\n## When it is waived\n\nThe excess may be waived where the policy schedule says so "
        "[src:raw/regulatory/advice-boundary.md#advice-boundary].\n"
    )
    response = client.put(
        "/v1/cms/pages/concept/excess",
        json={"frontmatter": page["frontmatter"], "body": page["body"] + addition, "actor": "tester"},
    )
    assert response.status_code == 200
    assert "When it is waived" in client.get("/v1/cms/pages/concept/excess").json()["body"]


def test_authored_content_still_needs_a_source(client: TestClient, bundle_root: Path) -> None:
    payload = {
        "id": "concept/waiting-period",
        "title": "Waiting period",
        "type": "concept",
        "source_text": "From the 2026 wording, clause 3.1.",
        "body": (
            "## What it means\n\nA waiting period is the time after cover starts during which a "
            "claim cannot be made [src:raw/custom/concept-waiting-period.md].\n"
        ),
        "tags": ["health"],
        "aliases": ["qualifying period"],
        "actor": "author",
    }
    created = client.post("/v1/cms/pages", json=payload)
    assert created.status_code == 200, created.text

    page = client.get("/v1/cms/pages/concept/waiting-period").json()
    # A hand-written page is a draft, and a draft answers nothing.
    assert page["frontmatter"]["status"] == Status.draft.value
    assert page["retrievable"] is False
    # Its authority resolves to a file that now exists, and carries what the
    # author actually supplied — provenance, not a placeholder.
    source = page["frontmatter"]["authority"][0]
    assert source == "raw/custom/concept-waiting-period.md"
    written = (bundle_root / source).read_text()
    assert "clause 3.1" in written and "author" in written


def test_authored_content_with_a_dangling_reference_is_refused(client: TestClient) -> None:
    response = client.post(
        "/v1/cms/pages",
        json={
            "id": "concept/nonsense",
            "title": "Nonsense",
            "type": "concept",
            "body": "## What it means\n\nThis claim has no reference on it whatsoever.\n",
            "actor": "author",
        },
    )
    assert response.status_code == 422


def test_approval_is_a_signature(client: TestClient) -> None:
    client.post(
        "/v1/cms/pages",
        json={
            "id": "concept/waiting-period",
            "title": "Waiting period",
            "type": "concept",
            "source_text": "clause 3.1",
            "body": "## What it means\n\nTime after cover starts before a claim can be made "
            "[src:raw/custom/concept-waiting-period.md].\n",
            "actor": "author",
        },
    )
    response = client.post(
        "/v1/cms/pages/concept/waiting-period/status",
        json={"status": "approved", "actor": "yichew"},
    )
    assert response.status_code == 200
    assert response.json()["retrievable"] is True

    frontmatter = client.get("/v1/cms/pages/concept/waiting-period").json()["frontmatter"]
    assert "reviewer:yichew" in frontmatter["reviewed_by"]
    assert frontmatter["review_due"] > dt.date.today().isoformat()


def test_tags_are_grouping_not_free_text(client: TestClient) -> None:
    response = client.post(
        "/v1/cms/pages/concept/excess/tags",
        json={"tags": ["Motor Claims", " deductible ", ""], "actor": "test"},
    )
    assert response.status_code == 200
    assert response.json()["tags"] == ["deductible", "motor-claims"]
    assert "motor-claims" in {t["value"] for t in client.get("/v1/cms/overview").json()["taxonomy"]["tags"]}


def test_deleting_a_linked_page_is_refused(client: TestClient) -> None:
    response = client.delete("/v1/cms/pages/product/general/travel/exclusions")
    assert response.status_code == 422
    assert "link to" in response.json()["detail"]["message"]


def test_integrations_report_configuration_honestly(client: TestClient) -> None:
    integrations = client.get("/v1/cms/integrations").json()["integrations"]
    by_name = {i["name"]: i for i in integrations}
    assert by_name["vllm"]["configured"] is False
    assert by_name["vllm"]["fallback"], "an optional dependency must say what happens without it"
    assert by_name["answer-api"]["direction"] == "inbound"
    result = client.post("/v1/cms/integrations/sor/test").json()
    assert result["ok"] is True and "policies" in result["detail"]


def test_unknown_scan_and_page_are_404(client: TestClient) -> None:
    assert client.get("/v1/cms/scan/nope").status_code == 404
    assert client.get("/v1/cms/pages/does/not/exist").status_code == 404


# --- verification, without running a crawl ---------------------------------


def test_verify_reports_a_moved_figure_as_blocking(tmp_path: Path) -> None:
    live = Bundle.load(BUNDLE_ROOT)
    staged = Bundle.load(BUNDLE_ROOT)
    row = staged.tables.rows[0]
    moved = type(row)(**{**row.__dict__, "value": str(int(row.value) + 1)})
    staged.tables = type(staged.tables)([moved, *staged.tables.rows[1:]])

    found = verify(live, staged, SimpleNamespace(conflicts=[]), dt.date.today())
    drift = [s for s in found if s.kind == "figure-drift"]
    assert drift and drift[0].severity == "blocking"
    assert drift[0].action == "adopt-tables"
    assert drift[0].before != drift[0].after


def test_suggestions_carry_labels_for_their_own_shape() -> None:
    defect = Suggestion(id="s1", kind="website-defect", severity="high", title="t", detail="d").as_dict()
    # Two websites disagreeing is not the wiki disagreeing with a website.
    assert "authority" in defect["before_label"] and "authority" in defect["after_label"]
    drift = Suggestion(id="s2", kind="figure-drift", severity="blocking", title="t", detail="d").as_dict()
    assert drift["before_label"] == "in the wiki"


def test_store_refuses_an_id_that_is_not_a_slug_path(tmp_path: Path) -> None:
    store = ContentStore(tmp_path)
    with pytest.raises(StoreError):
        store.build_page({"id": "Not A Slug", "title": "x", "type": "concept"}, "body")


def test_taxonomy_counts_come_from_the_corpus() -> None:
    counts = taxonomy(Bundle.load(BUNDLE_ROOT))
    assert sum(entry["count"] for entry in counts["type"]) == len(Bundle.load(BUNDLE_ROOT).pages)
