"""Exported JSON Schemas must be regenerated in the same commit as model changes (§13)."""

from contracts.export_schemas import SCHEMA_DIR, render_schemas


def test_exported_schemas_match_models() -> None:
    fresh = render_schemas()
    for name, content in fresh.items():
        path = SCHEMA_DIR / f"{name}.json"
        assert path.exists(), f"{path} missing — run python -m contracts.export_schemas"
        assert path.read_text() == content, (
            f"{path} is stale — run python -m contracts.export_schemas and commit the result"
        )
    on_disk = {p.stem for p in SCHEMA_DIR.glob("*.json")}
    assert on_disk == set(fresh), f"orphan schema files: {on_disk - set(fresh)}"
