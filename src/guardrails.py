"""Deterministic safety policy evaluated outside specialist agents."""

from __future__ import annotations

from .blackboard import Blackboard
from .schemas import AgentName, GovernanceDecision, ProposedAction, Scenario


ALLOWED_PROPOSERS: dict[str, set[str]] = {
    "monitor": {AgentName.SUPERVISOR.value},
    "transfer_inventory": {
        AgentName.INVENTORY.value,
        AgentName.SUPERVISOR.value,
    },
    "supplier_expedite": {
        AgentName.SUPPLIER_LOGISTICS.value,
        AgentName.SUPERVISOR.value,
    },
    "pause_promotion": {AgentName.PRICING.value, AgentName.SUPERVISOR.value},
    "hold_price": {AgentName.PRICING.value},
    "price_increase": {AgentName.PRICING.value},
    "discount": {AgentName.PRICING.value},
    "protect_customer_promises": {
        AgentName.CUSTOMER_PROMISE.value,
        AgentName.SUPERVISOR.value,
    },
    "human_review": {AgentName.SUPERVISOR.value, AgentName.GOVERNANCE.value},
}


class GovernancePolicy:
    def review(
        self,
        scenario: Scenario,
        blackboard: Blackboard,
        actions: list[ProposedAction],
    ) -> GovernanceDecision:
        reasons: list[str] = []
        blocked: list[ProposedAction] = []
        requires_human = False
        autonomous_allowed = True

        forecast = blackboard.latest_evidence(
            AgentName.FORECASTING.value, "demand_forecast"
        )
        evidence_present = forecast is not None
        confidence_ok = bool(
            forecast
            and forecast.confidence >= scenario.policy.min_forecast_confidence
        )
        if not evidence_present:
            reasons.append("Missing forecast evidence: autonomous action is forbidden.")
            requires_human = True
            autonomous_allowed = False
        elif not confidence_ok:
            reasons.append(
                "Forecast confidence is below policy threshold: human review required."
            )
            requires_human = True
            autonomous_allowed = False

        permissions_ok = True
        price_safe = True
        fairness_safe = True
        customer_safe = True
        cost_safe = True

        for action in actions:
            if action.requested_by not in ALLOWED_PROPOSERS[action.action_type]:
                blocked.append(action)
                permissions_ok = False
                reasons.append(
                    f"Permission violation: {action.requested_by} cannot propose "
                    f"{action.action_type}."
                )
                continue

            if action.action_type == "price_increase":
                price_change = float(action.details.get("change_pct", 0))
                if price_change > scenario.policy.max_price_increase_pct:
                    blocked.append(action)
                    price_safe = False
                    reasons.append(
                        f"Price increase {price_change:.0%} exceeds the "
                        f"{scenario.policy.max_price_increase_pct:.0%} policy limit."
                    )

            if action.action_type == "supplier_expedite":
                if action.estimated_cost > scenario.policy.expedite_approval_cost:
                    requires_human = True
                    cost_safe = False
                    reasons.append(
                        f"Expedite cost ${action.estimated_cost:,.0f} exceeds the "
                        f"${scenario.policy.expedite_approval_cost:,.0f} approval threshold."
                    )

            if action.action_type == "transfer_inventory":
                donor_level = float(
                    action.details.get("donor_service_level_after", 0)
                )
                if donor_level < scenario.policy.min_donor_service_level:
                    blocked.append(action)
                    fairness_safe = False
                    reasons.append(
                        "Transfer would push the donor location below its service-level "
                        "fairness floor."
                    )

        promise = blackboard.latest_evidence(
            AgentName.CUSTOMER_PROMISE.value, "customer_promise"
        )
        if promise and bool(promise.facts.get("promise_at_risk")):
            requires_human = True
            customer_safe = False
            reasons.append("Existing customer promises remain at risk.")

        if not reasons:
            reasons.append("All evidence, permission, cost, pricing, and fairness checks passed.")

        checks = {
            "forecast_evidence_present": evidence_present,
            "forecast_confidence_ok": confidence_ok,
            "permissions_ok": permissions_ok,
            "price_policy_ok": price_safe,
            "expedite_below_approval_threshold": cost_safe,
            "donor_fairness_ok": fairness_safe,
            "customer_promises_safe": customer_safe,
        }

        if not autonomous_allowed:
            outcome = "blocked"
        elif requires_human:
            outcome = "approval_required"
        elif blocked:
            outcome = "approved_with_blocks"
        else:
            outcome = "approved"

        return GovernanceDecision(
            outcome=outcome,
            reasons=reasons,
            blocked_actions=blocked,
            requires_human_approval=requires_human,
            autonomous_action_allowed=autonomous_allowed,
            checks=checks,
        )
