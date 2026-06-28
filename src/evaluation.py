"""Trace-derived coordination and emergence signals."""

from __future__ import annotations

from collections import Counter

from .blackboard import Blackboard


def compute_run_metrics(blackboard: Blackboard) -> dict[str, object]:
    """Compute cheap signals suitable for CI and later cross-run aggregation."""
    senders = Counter(message.sender_agent for message in blackboard.messages)
    actions = Counter(action.action_type for action in blackboard.proposed_actions)
    blocked = sum(
        len(decision.blocked_actions)
        for decision in blackboard.governance_decisions
    )
    escalations = sum(
        1
        for decision in blackboard.governance_decisions
        if decision.requires_human_approval
    )
    donor_levels = [
        float(action.details["donor_service_level_after"])
        for action in blackboard.proposed_actions
        if action.action_type == "transfer_inventory"
        and "donor_service_level_after" in action.details
    ]
    supplier_evidence = blackboard.latest_evidence(
        "supplier_logistics_agent", "supplier_selection"
    )
    supplier_costs = [bid.cost for bid in blackboard.supplier_bids]
    infeasible_bids = (
        len(supplier_evidence.facts.get("rejected_bids", []))
        if supplier_evidence
        else 0
    )
    return {
        "messages_total": len(blackboard.messages),
        "messages_by_sender": dict(sorted(senders.items())),
        "evidence_records_total": sum(
            len(records) for records in blackboard.evidence.values()
        ),
        "proposed_action_mix": dict(sorted(actions.items())),
        "guardrail_blocks": blocked,
        "human_escalations": escalations,
        "supplier_bids_total": len(blackboard.supplier_bids),
        "supplier_bid_cost_spread": (
            max(supplier_costs) - min(supplier_costs)
            if len(supplier_costs) >= 2
            else None
        ),
        "infeasible_supplier_bids": infeasible_bids,
        "minimum_donor_service_level_after": (
            min(donor_levels) if donor_levels else None
        ),
        "recommendation_reversals": blocked,
        "duplicate_messages_ignored": sum(
            warning.startswith("duplicate_message_ignored:")
            for warning in blackboard.warnings
        ),
    }
