from orchestrator.procedures import CANNOT_EXECUTE_LINE, render_procedure


def test_full_procedure_rendering() -> None:
    block = {
        "block_id": "common/procedure/update-address",
        "text": "## Steps\n\n1. Log in.\n2. Update.",
        "metadata": {
            "channels": ["customer-portal", "branch"],
            "sla": "3 working days",
            "action_ref": "customer-portal",
        },
    }
    view = render_procedure(block)
    assert "1. Log in." in view.text
    assert "customer-portal, branch" in view.text
    assert "3 working days" in view.text
    assert CANNOT_EXECUTE_LINE in view.text
    assert view.citation == "common/procedure/update-address"
    assert view.action_ids == ["customer-portal"]


def test_minimal_procedure_still_gets_cannot_execute_line() -> None:
    view = render_procedure({"block_id": "b", "text": "Do the thing.", "metadata": {}})
    assert CANNOT_EXECUTE_LINE in view.text
    assert view.action_ids == []
