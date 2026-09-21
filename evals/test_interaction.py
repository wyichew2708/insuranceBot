from evals.interaction import interaction_metrics


def test_metrics_use_only_labelled_selection_and_do_not_double_count_final_turn() -> None:
    result = interaction_metrics(
        [
            {
                "observed": {"clarifying": True},
                "turn_results": [
                    {"expected_product": "travel", "observed": {"selected_product": "travel"}},
                    {
                        "expected_product": "home",
                        "observed": {"selected_product": "travel", "clarifying": True},
                    },
                    {"observed": {"handoff": True, "destinations": 1}},
                    {"observed": {"handoff": True}},
                    {
                        "observed": {
                            "handler": "knowledge.general",
                            "selected_product": "home",
                            "suggestions": 3,
                        }
                    },
                ],
            }
        ]
    )
    assert result["selection accuracy"] == {"count": 1, "total": 2, "rate": 0.5}
    assert result["clarification rate"]["rate"] == 0.2
    assert result["handoff dead-end rate"]["rate"] == 0.5
    assert result["overview chip coverage"]["rate"] == 1
    assert interaction_metrics([])["selection accuracy"]["rate"] is None
