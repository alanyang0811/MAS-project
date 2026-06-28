"""Specialist agents with narrow responsibilities and typed outputs."""

from __future__ import annotations

from typing import Any

from .blackboard import Blackboard
from .guardrails import GovernancePolicy
from .schemas import (
    AgentMessage,
    AgentName,
    EvidenceRecord,
    GovernanceDecision,
    HumanDecision,
    MessageType,
    ProposedAction,
    Scenario,
    SupplierBid,
)
from .tools import (
    demand_spike_ratio,
    inventory_shortfall,
    rank_supplier_bids,
    select_transfer,
)
from .tracing import TraceLogger


class BaseAgent:
    name: AgentName

    def __init__(
        self,
        *,
        trace_id: str,
        correlation_id: str,
        blackboard: Blackboard,
        tracer: TraceLogger,
    ) -> None:
        self.trace_id = trace_id
        self.correlation_id = correlation_id
        self.blackboard = blackboard
        self.tracer = tracer
        self._message_count = 0

    def emit(
        self,
        *,
        receiver: str,
        msg_type: MessageType,
        payload: dict[str, Any],
        confidence: float,
        priority: str = "normal",
        requires_approval: bool = False,
        idempotency_suffix: str | None = None,
        sender: str | None = None,
    ) -> AgentMessage:
        self._message_count += 1
        sender_name = sender or self.name.value
        suffix = idempotency_suffix or str(self._message_count)
        message = AgentMessage(
            trace_id=self.trace_id,
            message_id=f"msg-{self.name.value}-{self._message_count:03d}",
            correlation_id=self.correlation_id,
            sender_agent=sender_name,
            receiver_agent=receiver,
            msg_type=msg_type,
            priority=priority,
            idempotency_key=f"{self.correlation_id}:{sender_name}:{msg_type.value}:{suffix}",
            confidence=confidence,
            requires_approval=requires_approval,
            payload=payload,
        )
        if self.blackboard.post_message(message):
            self.tracer.record_message(message)
        return message

    def post_evidence(
        self,
        evidence_type: str,
        facts: dict[str, Any],
        confidence: float,
        source: str,
    ) -> EvidenceRecord:
        record = EvidenceRecord(
            agent=self.name.value,
            evidence_type=evidence_type,
            facts=facts,
            confidence=confidence,
            source=source,
        )
        self.blackboard.post_evidence(record)
        self.tracer.record(
            agent=self.name.value,
            event_type="evidence_posted",
            message=record.model_dump(mode="json"),
            confidence=confidence,
        )
        return record


class ForecastingAgent(BaseAgent):
    name = AgentName.FORECASTING

    def assess(self, scenario: Scenario) -> dict[str, Any] | None:
        demand = scenario.demand
        ratio = demand_spike_ratio(
            demand.observed_daily_units, demand.baseline_daily_units
        )
        if not demand.evidence_available:
            self.blackboard.failure_flags.append("missing_forecast_evidence")
            self.blackboard.warnings.append("Forecast source returned no usable evidence.")
            payload = {
                "sku": scenario.sku,
                "evidence_available": False,
                "confidence": demand.confidence,
            }
            self.emit(
                receiver=AgentName.SUPERVISOR.value,
                msg_type=MessageType.FORECAST_STATUS,
                payload=payload,
                confidence=demand.confidence,
                priority="critical",
                requires_approval=True,
            )
            self.tracer.record(
                agent=self.name.value,
                event_type="failure_flag",
                message=payload,
                decision="no_forecast_evidence",
                confidence=demand.confidence,
                requires_approval=True,
            )
            return None

        facts = {
            "sku": scenario.sku,
            "forecast_7d_units": demand.forecast_7d_units,
            "baseline_daily_units": demand.baseline_daily_units,
            "observed_daily_units": demand.observed_daily_units,
            "spike_ratio": ratio,
            "demand_spike": ratio >= 1.25,
        }
        self.post_evidence(
            "demand_forecast",
            facts,
            demand.confidence,
            "mock_forecast_service:v1",
        )
        self.emit(
            receiver=AgentName.SUPERVISOR.value,
            msg_type=MessageType.FORECAST_STATUS,
            payload=facts,
            confidence=demand.confidence,
            priority="high" if facts["demand_spike"] else "normal",
        )
        return facts


class InventoryAgent(BaseAgent):
    name = AgentName.INVENTORY

    def assess(
        self, scenario: Scenario, forecast: dict[str, Any] | None
    ) -> dict[str, Any]:
        if forecast is None:
            facts = {
                "assessment_complete": False,
                "reason": "forecast_evidence_unavailable",
                "on_hand": scenario.inventory.on_hand,
                "pending_orders": scenario.inventory.pending_orders,
            }
            confidence = 0.0
        else:
            shortfall = inventory_shortfall(
                int(forecast["forecast_7d_units"]),
                scenario.inventory.on_hand,
                scenario.inventory.pending_orders,
            )
            selected, residual = select_transfer(
                scenario.inventory.transfer_options,
                shortfall,
                scenario.policy.min_donor_service_level,
            )
            facts = {
                "assessment_complete": True,
                "on_hand": scenario.inventory.on_hand,
                "pending_orders": scenario.inventory.pending_orders,
                "available_before_response": (
                    scenario.inventory.on_hand + scenario.inventory.pending_orders
                ),
                "shortfall_units": shortfall,
                "transfer_options": [
                    option.model_dump(mode="json")
                    for option in scenario.inventory.transfer_options
                ],
                "selected_transfer": (
                    selected.model_dump(mode="json") if selected else None
                ),
                "residual_shortfall_units": residual,
                "transfer_solves_shortfall": bool(selected and residual == 0),
            }
            confidence = 0.98

        self.post_evidence(
            "inventory_position",
            facts,
            confidence,
            "mock_inventory_read_model:v1",
        )
        self.emit(
            receiver=AgentName.SUPERVISOR.value,
            msg_type=MessageType.INVENTORY_STATUS,
            payload=facts,
            confidence=confidence,
            priority="high" if facts.get("shortfall_units", 0) else "normal",
        )
        return facts


class SupplierLogisticsAgent(BaseAgent):
    name = AgentName.SUPPLIER_LOGISTICS

    def run_contract_net(
        self, scenario: Scenario, required_units: int
    ) -> tuple[SupplierBid | None, dict[str, Any]]:
        task = {
            "sku": scenario.sku,
            "required_units": required_units,
            "max_lead_days": scenario.policy.max_supplier_lead_days,
            "min_reliability": scenario.policy.min_supplier_reliability,
        }
        self.emit(
            receiver=self.name.value,
            msg_type=MessageType.CALL_FOR_PROPOSALS,
            payload=task,
            confidence=1.0,
            priority="high",
            sender=AgentName.SUPERVISOR.value,
            idempotency_suffix="cfp",
        )

        self.blackboard.supplier_bids.extend(scenario.supplier_bids)
        for bid in scenario.supplier_bids:
            self.emit(
                receiver=self.name.value,
                msg_type=MessageType.SUPPLIER_BID,
                payload=bid.model_dump(mode="json"),
                confidence=bid.reliability,
                sender=f"supplier_bidder:{bid.supplier_id}",
                idempotency_suffix=bid.supplier_id,
            )

        ranked, rejected = rank_supplier_bids(
            scenario.supplier_bids,
            required_units,
            scenario.policy.min_supplier_reliability,
            scenario.policy.max_supplier_lead_days,
        )
        selected = ranked[0][0] if ranked else None
        ranking = [
            {
                "supplier_id": bid.supplier_id,
                "score": score,
                "cost": bid.cost,
                "lead_time_days": bid.lead_time_days,
                "capacity": bid.capacity,
                "reliability": bid.reliability,
            }
            for bid, score in ranked
        ]
        facts = {
            "contract_net_stage": "award_recommendation",
            "required_units": required_units,
            "ranking_weights": {
                "cost": 0.40,
                "lead_time": 0.35,
                "reliability": 0.25,
            },
            "feasible_ranking": ranking,
            "rejected_bids": rejected,
            "selected_bid": selected.model_dump(mode="json") if selected else None,
        }
        confidence = selected.reliability if selected else 0.0
        self.post_evidence(
            "supplier_selection",
            facts,
            confidence,
            "mock_contract_net:v1",
        )
        self.emit(
            receiver=AgentName.SUPERVISOR.value,
            msg_type=MessageType.BID_AWARD_RECOMMENDATION,
            payload=facts,
            confidence=confidence,
            priority="high",
        )
        return selected, facts


class PricingAgent(BaseAgent):
    name = AgentName.PRICING

    def assess(self, scenario: Scenario, shortfall_units: int) -> dict[str, Any]:
        if scenario.pricing.forced_recommendation is not None:
            recommendation = scenario.pricing.forced_recommendation
        elif shortfall_units > 0 and scenario.pricing.promotion_active:
            recommendation = "pause_promotion"
        else:
            recommendation = "none"

        facts = {
            "recommendation": recommendation,
            "proposed_price_change_pct": scenario.pricing.proposed_price_change_pct,
            "promotion_active": scenario.pricing.promotion_active,
            "current_margin_pct": scenario.pricing.current_margin_pct,
            "shortfall_units": shortfall_units,
        }
        self.post_evidence(
            "pricing_recommendation",
            facts,
            0.90,
            "deterministic_pricing_policy:v1",
        )
        self.emit(
            receiver=AgentName.SUPERVISOR.value,
            msg_type=MessageType.PRICING_RECOMMENDATION,
            payload=facts,
            confidence=0.90,
            priority="high" if recommendation == "price_increase" else "normal",
            requires_approval=recommendation == "price_increase",
        )
        return facts


class CustomerPromiseAgent(BaseAgent):
    name = AgentName.CUSTOMER_PROMISE

    def assess(
        self,
        scenario: Scenario,
        *,
        recovery_units: int,
        response_delay_days: int,
    ) -> dict[str, Any]:
        available = (
            scenario.inventory.on_hand
            + scenario.inventory.pending_orders
            + recovery_units
        )
        promise = scenario.customer_promise
        quantity_risk = available < promise.committed_units_7d
        delay_risk = (
            response_delay_days > promise.max_tolerable_delay_days
            and scenario.inventory.on_hand + scenario.inventory.pending_orders
            < promise.committed_units_7d
        )
        facts = {
            "committed_units_7d": promise.committed_units_7d,
            "available_after_proposed_response": available,
            "response_delay_days": response_delay_days,
            "max_tolerable_delay_days": promise.max_tolerable_delay_days,
            "protected_segment": promise.protected_segment,
            "promise_at_risk": quantity_risk or delay_risk,
            "quantity_risk": quantity_risk,
            "delay_risk": delay_risk,
        }
        self.post_evidence(
            "customer_promise",
            facts,
            0.97,
            "mock_customer_commitment_ledger:v1",
        )
        self.emit(
            receiver=AgentName.SUPERVISOR.value,
            msg_type=MessageType.CUSTOMER_PROMISE_STATUS,
            payload=facts,
            confidence=0.97,
            priority="critical" if facts["promise_at_risk"] else "normal",
            requires_approval=bool(facts["promise_at_risk"]),
        )
        return facts


class GovernanceAgent(BaseAgent):
    name = AgentName.GOVERNANCE

    def __init__(self, *args: Any, policy: GovernancePolicy, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.policy = policy

    def review(
        self, scenario: Scenario, actions: list[ProposedAction]
    ) -> GovernanceDecision:
        decision = self.policy.review(scenario, self.blackboard, actions)
        self.blackboard.governance_decisions.append(decision)
        self.emit(
            receiver=AgentName.SUPERVISOR.value,
            msg_type=MessageType.GOVERNANCE_REVIEW,
            payload=decision.model_dump(mode="json"),
            confidence=1.0,
            priority="critical"
            if decision.outcome in {"blocked", "approval_required"}
            else "high",
            requires_approval=decision.requires_human_approval,
        )
        self.tracer.record(
            agent=self.name.value,
            event_type="governance_decision",
            message=decision.model_dump(mode="json"),
            decision=decision.outcome,
            confidence=1.0,
            requires_approval=decision.requires_human_approval,
        )
        return decision


class HumanApprover(BaseAgent):
    name = AgentName.HUMAN

    def decide(self, scenario: Scenario) -> HumanDecision:
        outcome = scenario.human_approval.outcome
        rationale = {
            "approved": "Mock reviewer accepted the bounded, dry-run plan.",
            "rejected": "Mock reviewer rejected the risk/cost trade-off.",
            "needs_review": "Mock reviewer requested additional evidence.",
        }[outcome]
        decision = HumanDecision(
            status=outcome,
            reviewer=scenario.human_approval.reviewer,
            rationale=rationale,
        )
        self.blackboard.human_approval_status = decision
        self.emit(
            receiver=AgentName.SUPERVISOR.value,
            msg_type=MessageType.APPROVAL_RESPONSE,
            payload=decision.model_dump(mode="json"),
            confidence=1.0,
            priority="critical",
            requires_approval=outcome == "needs_review",
        )
        self.tracer.record(
            agent=self.name.value,
            event_type="human_decision",
            message=decision.model_dump(mode="json"),
            decision=outcome,
            confidence=1.0,
            requires_approval=outcome == "needs_review",
        )
        return decision
