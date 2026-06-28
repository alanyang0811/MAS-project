"""Behavioral evaluations for coordination, safety, and durable evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.coordinator import StockoutCoordinator
from src.schemas import AgentMessage, MessageType
from src.simulator import load_scenario, run_scenario


SCENARIOS = Path(__file__).parents[1] / "scenarios"


def execute(name: str, tmp_path: Path):
    return run_scenario(SCENARIOS / f"{name}.json", tmp_path / "runs")


def action_types(result) -> list[str]:
    return [action.action_type for action in result.plan.actions]


def test_normal_demand_only_monitors(tmp_path: Path) -> None:
    result = execute("normal_demand", tmp_path)
    assert action_types(result) == ["monitor"]
    assert result.plan.governance.requires_human_approval is False
    assert result.blackboard.supplier_bids == []


@pytest.mark.parametrize(
    "scenario_name",
    [
        "transfer_solves_stockout",
        "supplier_expedite_required",
        "approval_required_high_cost",
        "unsafe_price_blocked",
    ],
)
def test_high_stockout_risk_triggers_response(
    scenario_name: str, tmp_path: Path
) -> None:
    result = execute(scenario_name, tmp_path)
    assert "monitor" not in action_types(result)
    inventory = result.blackboard.latest_evidence(
        "inventory_agent", "inventory_position"
    )
    assert inventory is not None
    assert inventory.facts["shortfall_units"] > 0


def test_transfer_is_preferred_when_it_fully_solves_gap(tmp_path: Path) -> None:
    result = execute("transfer_solves_stockout", tmp_path)
    assert action_types(result) == ["transfer_inventory"]
    assert result.blackboard.supplier_bids == []
    transfer = result.plan.actions[0]
    assert transfer.details["from_location"] == "STORE-B"
    assert transfer.details["donor_service_level_after"] >= 0.90


def test_contract_net_filters_and_ranks_all_supplier_dimensions(
    tmp_path: Path,
) -> None:
    result = execute("supplier_expedite_required", tmp_path)
    supplier = result.blackboard.latest_evidence(
        "supplier_logistics_agent", "supplier_selection"
    )
    assert supplier is not None
    selected = supplier.facts["selected_bid"]
    assert selected["supplier_id"] == "RapidCo"
    assert selected["capacity"] >= supplier.facts["required_units"]
    assert selected["lead_time_days"] <= 4
    assert selected["reliability"] >= 0.85
    assert supplier.facts["feasible_ranking"][0]["score"] > 0
    expedite = next(
        action
        for action in result.plan.actions
        if action.action_type == "supplier_expedite"
    )
    assert expedite.details["requested_quantity"] == supplier.facts["required_units"]
    rejected = {
        row["supplier_id"]: row["reasons"]
        for row in supplier.facts["rejected_bids"]
    }
    assert "insufficient_capacity" in rejected["TinyVendor"]


def test_unsafe_price_action_is_blocked_but_safe_transfer_survives(
    tmp_path: Path,
) -> None:
    result = execute("unsafe_price_blocked", tmp_path)
    assert "price_increase" not in action_types(result)
    assert "transfer_inventory" in action_types(result)
    assert result.plan.governance.outcome == "approved_with_blocks"
    assert any(
        action.action_type == "price_increase"
        for action in result.plan.governance.blocked_actions
    )


def test_high_cost_expedite_requires_human_approval(tmp_path: Path) -> None:
    result = execute("approval_required_high_cost", tmp_path)
    assert result.plan.governance.requires_human_approval is True
    assert result.plan.approval_packet is not None
    assert result.plan.human_decision is not None
    assert result.plan.human_decision.status == "approved"
    assert result.plan.status == "approved"
    assert "supplier_expedite" in action_types(result)


def test_missing_forecast_refuses_autonomous_action(tmp_path: Path) -> None:
    result = execute("missing_forecast_evidence", tmp_path)
    assert action_types(result) == ["human_review"]
    assert result.plan.status == "needs_human_review"
    assert result.plan.governance.autonomous_action_allowed is False
    assert "missing_forecast_evidence" in result.blackboard.failure_flags
    assert result.plan.human_decision.status == "needs_review"


def test_low_confidence_forecast_also_refuses_autonomous_action(
    tmp_path: Path,
) -> None:
    scenario = load_scenario(SCENARIOS / "normal_demand.json")
    scenario.demand.confidence = 0.50
    plan, _, _ = StockoutCoordinator(tmp_path / "runs").run(scenario)
    assert plan.governance.autonomous_action_allowed is False
    assert plan.governance.requires_human_approval is True
    assert plan.status == "needs_human_review"
    assert [action.action_type for action in plan.actions] == ["human_review"]


@pytest.mark.parametrize(
    "scenario_path", sorted(SCENARIOS.glob("*.json")), ids=lambda p: p.stem
)
def test_every_scenario_creates_parseable_complete_trace(
    scenario_path: Path, tmp_path: Path
) -> None:
    result = run_scenario(scenario_path, tmp_path / "runs")
    assert result.trace_path.exists()
    lines = result.trace_path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert len(events) >= 10
    assert all(event["trace_id"] == result.plan.trace_id for event in events)
    assert events[0]["event_type"] == "case_opened"
    assert events[-1]["event_type"] == "run_completed"
    assert any(event["event_type"] == "message_posted" for event in events)
    assert any(event["event_type"] == "governance_decision" for event in events)


def test_all_side_effecting_tools_remain_dry_run(tmp_path: Path) -> None:
    result = execute("approval_required_high_cost", tmp_path)
    assert result.blackboard.tool_results
    assert all(item["executed"] is False for item in result.blackboard.tool_results)
    assert all(item["rollback_token"] for item in result.blackboard.tool_results)


def test_message_contract_rejects_unknown_fields() -> None:
    payload = {
        "trace_id": "trace-test",
        "correlation_id": "case-test",
        "sender_agent": "inventory_agent",
        "receiver_agent": "supervisor_agent",
        "msg_type": MessageType.INVENTORY_STATUS,
        "idempotency_key": "inventory-test",
        "confidence": 0.9,
        "unexpected": "not allowed",
    }
    with pytest.raises(ValueError):
        AgentMessage.model_validate(payload)


def test_scenario_schema_rejects_duplicate_supplier_id() -> None:
    scenario = load_scenario(SCENARIOS / "supplier_expedite_required.json")
    duplicate = scenario.model_dump(mode="json")
    duplicate["supplier_bids"].append(duplicate["supplier_bids"][0])
    with pytest.raises(ValueError, match="supplier_id"):
        type(scenario).model_validate(duplicate)
