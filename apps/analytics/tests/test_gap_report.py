from pathlib import Path

from analytics.gap_report import GapRow, build_report, suggest_block_type, write_csv


def blocks() -> list[dict[str, str]]:
    return [
        {
            "block_id": "tiq-trv/faq/what-is-covered",
            "title": "What is covered",
            "type": "faq",
            "text": "medical baggage delay cancellation covered",
        },
        {
            "block_id": "common/procedure/update-address",
            "title": "Update address",
            "type": "procedure",
            "text": "update address customer portal steps",
        },
    ]


def test_clusters_map_to_nearest_block_and_type() -> None:
    questions = [
        "how do i update my mailing address",
        "how to update address online",
        "is scuba diving covered by travel insurance",
        "is skydiving covered",
    ]
    rows = build_report(questions, blocks(), k=2)
    assert len(rows) == 2
    by_block = {r.nearest_block: r for r in rows}
    assert "common/procedure/update-address" in by_block
    assert by_block["common/procedure/update-address"].suggested_block_type == "procedure"
    assert "tiq-trv/faq/what-is-covered" in by_block
    assert by_block["tiq-trv/faq/what-is-covered"].suggested_block_type == "benefit"
    assert sum(r.count for r in rows) == 4


def test_suggest_block_type_keywords() -> None:
    assert suggest_block_type("How do I cancel my policy") == "procedure"
    assert suggest_block_type("Is golf covered?") == "benefit"
    assert suggest_block_type("What is excluded?") == "exclusion"
    assert suggest_block_type("Who can buy this?") == "eligibility"
    assert suggest_block_type("Tell me about tigers") == "faq"


def test_empty_questions_empty_report(tmp_path: Path) -> None:
    rows = build_report([], blocks(), k=3)
    assert rows == []
    out = tmp_path / "gap.csv"
    write_csv([GapRow("q", 2, "b", "faq")], out)
    content = out.read_text()
    assert "question_cluster" in content and "b,faq" in content.replace('"', "")
