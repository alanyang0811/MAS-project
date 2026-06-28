"""Hierarchical supervisor for the deterministic stockout workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .agents import (
    CustomerPromiseAgent,
    ForecastingAgent,
    GovernanceAgent,
    HumanApprover,
    InventoryAgent,
    PricingAgent,
    SupplierLogisticsAgent,
)
from .blackboard import Blackboard
from .evaluation import compute_run_metrics
from .guardrails import GovernancePolicy
from .schemas import (
    AgentMessage,
    AgentName,
    ApprovalPacket,
    FinalPlan,
    MessageType,
    ProposedAction,
    Scenario,
    SupplierBid,
    TransferOption,
)
from .tools import MockActionTools
from .tracing import TraceLogger


class StockoutCoordinator:
    """Owns routing and synthesis, but can only produce mocked recommendations."""

    def __init__(self, runs_dir: Path | str = "runs") -> None:
        self.runs_dir = Path(runs_dir)

    def run(self, scenario: Scenario) -> tuple[FinalPlan, Blackboard, Path]:
        trace_id = f"trace-{scenario.name}"
        case_id = f"case-{scenario.sku}-{scenario.store_id}-{scenario.name}"
        trace_path = self.runs_dir / f"trace_{scenario.name}.jsonl"
        tracer = TraceLogger(trace_id, trace_path)
        blackboard = Blackboard(
            {
                "case_id": case_id,
                "scenario_name": scenario.name,
                "sku": scenario.sku,
                "store_id": scenario.store_id,
                "trace_id": trace_id,
                "policy_version": "stockout-policy-v1",
            }
        )
        correlation_id = case_id

        tracer.record(
            agent=AgentName.SUPERVISOR.value,
            event_type="case_opened",
            message=blackboard.case_metadata,
            decision="workflow_started",
        )
        opening = AgentMessage(
            trace_id=trace_id,
            message_id="msg-supervisor_agent-001",
            correlation_id=correlation_id,
            sender_agent=AgentName.SUPERVISOR.value,
            receiver_agent=AgentName.FORECASTING.value,
            msg_type=MessageType.CASE_OPENED,
            priority="high",
            idempotency_key=f"{case_id}:case-opened",
            confidence=1.0,
            payload={"sku": scenario.sku, "store_id": scenario.store_id},
        )
        blackboard.post_message(opening)
        tracer.record_message(opening)

        common = {
            "trace_id": trace_id,
            "correlation_id": correlation_id,
            "blackboard": blackboard,
            "tracer": tracer,
        }
        forecasting = ForecastingAgent(**common)
        inventory_agent = InventoryAgent(**common)
        supplier_agent = SupplierLogisticsAgent(**common)
        pricing_agent = PricingAgent(**common)
        customer_agent = CustomerPromiseAgent(**common)
        governance_agent = GovernanceAgent(
            **common, policy=GovernancePolicy()
        )
        human = HumanApprover(**common)

        forecast = forecasting.assess(scenario)
        inventory = inventory_agent.assess(scenario, forecast)
        shortfall = int(inventory.get("shortfall_units", 0))
        residual = int(inventory.get("residual_shortfall_units", shortfall))
        selected_transfer = self._transfer_from_facts(inventory)
        selected_bid: SupplierBid | None = None
        supplier_facts: dict[str, Any] | None = None
        if forecast is not None and residual > 0:
            selected_bid, supplier_facts = supplier_agent.run_contract_net(
                scenario, residual
            )

        pricing = pricing_agent.assess(scenario, shortfall)
        recovery_units, response_delay = self._response_coverage(
            selected_transfer, selected_bid, residual
        )
        customer = customer_agent.assess(
            scenario,
            recovery_units=recovery_units,
            response_delay_days=response_delay,
        )

        proposed = self._draft_actions(
            scenario=scenario,
            forecast=forecast,
            inventory=inventory,
            selected_transfer=selected_transfer,
            selected_bid=selected_bid,
            pricing=pricing,
            customer=customer,
        )
        blackboard.proposed_actions.extend(proposed)
        tracer.record(
            agent=AgentName.SUPERVISOR.value,
            event_type="candidate_plan",
            message={"actions": [a.model_dump(mode="json") for a in proposed]},
            decision="submitted_to_governance",
        )

        governance = governance_agent.review(scenario, proposed)
        blocked_dumps = {
            action.model_dump_json() for action in governance.blocked_actions
        }
        safe_actions = [
            action for action in proposed if action.model_dump_json() not in blocked_dumps
        ]

        approval_packet = None
        human_decision = None
        if governance.requires_human_approval:
            approval_packet = ApprovalPacket(
                case_id=case_id,
                trace_id=trace_id,
                proposed_actions=safe_actions,
                evidence_summary=self._evidence_summary(
                    forecast, inventory, supplier_facts, pricing, customer
                ),
                governance_reasons=governance.reasons,
                rollback_plan=(
                    "Discard the recommendation and its dry-run tokens; no external "
                    "inventory, supplier, price, or promise system was mutated."
                ),
            )
            blackboard.approval_packet = approval_packet
            approval_request = AgentMessage(
                trace_id=trace_id,
                message_id="msg-supervisor-approval-001",
                correlation_id=case_id,
                sender_agent=AgentName.SUPERVISOR.value,
                receiver_agent=AgentName.HUMAN.value,
                msg_type=MessageType.APPROVAL_REQUEST,
                priority="critical",
                idempotency_key=f"{case_id}:approval-request",
                confidence=1.0,
                requires_approval=True,
                payload=approval_packet.model_dump(mode="json"),
            )
            blackboard.post_message(approval_request)
            tracer.record_message(approval_request)
            human_decision = human.decide(scenario)

        status, final_actions = self._resolve_status_and_actions(
            governance=governance,
            safe_actions=safe_actions,
            human_status=human_decision.status if human_decision else None,
        )
        self._dry_run_actions(
            final_actions,
            scenario,
            selected_transfer,
            selected_bid,
            blackboard,
            tracer,
        )

        rationale = self._rationale(
            forecast, inventory, supplier_facts, governance.reasons
        )
        factors = self._explanation_factors(
            forecast, inventory, supplier_facts, governance
        )
        plan = FinalPlan(
            scenario_name=scenario.name,
            trace_id=trace_id,
            case_id=case_id,
            status=status,
            actions=final_actions,
            rationale=rationale,
            governance=governance,
            human_decision=human_decision,
            approval_packet=approval_packet,
            warnings=list(blackboard.warnings),
            explanation_factors=factors,
        )
        blackboard.final_plan = plan
        final_message = AgentMessage(
            trace_id=trace_id,
            message_id="msg-supervisor-final-001",
            correlation_id=case_id,
            sender_agent=AgentName.SUPERVISOR.value,
            receiver_agent="case_owner",
            msg_type=MessageType.FINAL_PLAN,
            priority="high",
            idempotency_key=f"{case_id}:final-plan",
            confidence=1.0,
            requires_approval=status == "needs_human_review",
            payload=plan.model_dump(mode="json"),
        )
        blackboard.post_message(final_message)
        tracer.record_message(final_message)
        blackboard.metrics = compute_run_metrics(blackboard)
        tracer.record(
            agent=AgentName.SUPERVISOR.value,
            event_type="run_completed",
            message={
                "final_plan": plan.model_dump(mode="json"),
                "blackboard_counts": {
                    "messages": len(blackboard.messages),
                    "evidence_records": sum(
                        len(records) for records in blackboard.evidence.values()
                    ),
                    "supplier_bids": len(blackboard.supplier_bids),
                    "warnings": len(blackboard.warnings),
                    "failure_flags": len(blackboard.failure_flags),
                },
                "coordination_metrics": blackboard.metrics,
            },
            decision=status,
            requires_approval=governance.requires_human_approval,
        )
        return plan, blackboard, trace_path

    @staticmethod
    def _transfer_from_facts(facts: dict[str, Any]) -> TransferOption | None:
        raw = facts.get("selected_transfer")
        return TransferOption.model_validate(raw) if raw else None

    @staticmethod
    def _response_coverage(
        transfer: TransferOption | None,
        bid: SupplierBid | None,
        supplier_required_units: int,
    ) -> tuple[int, int]:
        units = transfer.quantity if transfer else 0
        delays = [transfer.eta_days] if transfer else []
        if bid:
            units += min(bid.capacity, supplier_required_units)
            delays.append(bid.lead_time_days)
        return units, max(delays, default=0)

    @staticmethod
    def _draft_actions(
        *,
        scenario: Scenario,
        forecast: dict[str, Any] | None,
        inventory: dict[str, Any],
        selected_transfer: TransferOption | None,
        selected_bid: SupplierBid | None,
        pricing: dict[str, Any],
        customer: dict[str, Any],
    ) -> list[ProposedAction]:
        if forecast is None:
            return [
                ProposedAction(
                    action_type="human_review",
                    requested_by=AgentName.SUPERVISOR.value,
                    details={"reason": "missing_forecast_evidence"},
                )
            ]

        actions: list[ProposedAction] = []
        shortfall = int(inventory["shortfall_units"])
        if shortfall == 0:
            actions.append(
                ProposedAction(
                    action_type="monitor",
                    requested_by=AgentName.SUPERVISOR.value,
                    details={"review_in_hours": 24},
                )
            )
        if selected_transfer and shortfall > 0:
            actions.append(
                ProposedAction(
                    action_type="transfer_inventory",
                    requested_by=AgentName.INVENTORY.value,
                    details=selected_transfer.model_dump(mode="json"),
                    estimated_cost=selected_transfer.cost,
                    execution_mode="dry_run",
                )
            )
        if selected_bid:
            bid_details = selected_bid.model_dump(mode="json")
            bid_details["requested_quantity"] = int(
                inventory.get("residual_shortfall_units", selected_bid.capacity)
            )
            actions.append(
                ProposedAction(
                    action_type="supplier_expedite",
                    requested_by=AgentName.SUPPLIER_LOGISTICS.value,
                    details=bid_details,
                    estimated_cost=selected_bid.cost,
                    reversible=False,
                    execution_mode="dry_run",
                )
            )
        elif int(inventory.get("residual_shortfall_units", 0)) > 0:
            actions.append(
                ProposedAction(
                    action_type="human_review",
                    requested_by=AgentName.SUPERVISOR.value,
                    details={"reason": "no_feasible_supplier_bid"},
                )
            )

        recommendation = pricing["recommendation"]
        if recommendation in {
            "pause_promotion",
            "hold_price",
            "price_increase",
            "discount",
        }:
            actions.append(
                ProposedAction(
                    action_type=recommendation,
                    requested_by=AgentName.PRICING.value,
                    details={
                        "change_pct": pricing["proposed_price_change_pct"],
                        "promotion_active": pricing["promotion_active"],
                    },
                    execution_mode="dry_run",
                )
            )
        if customer["promise_at_risk"]:
            actions.append(
                ProposedAction(
                    action_type="protect_customer_promises",
                    requested_by=AgentName.CUSTOMER_PROMISE.value,
                    details={
                        "committed_units_7d": customer["committed_units_7d"],
                        "shortfall_after_response": max(
                            0,
                            customer["committed_units_7d"]
                            - customer["available_after_proposed_response"],
                        ),
                    },
                )
            )
        return actions

    @staticmethod
    def _resolve_status_and_actions(
        *,
        governance: Any,
        safe_actions: list[ProposedAction],
        human_status: str | None,
    ) -> tuple[str, list[ProposedAction]]:
        if not governance.autonomous_action_allowed:
            review_actions = [
                action for action in safe_actions if action.action_type == "human_review"
            ]
            if not review_actions:
                review_actions = [
                    ProposedAction(
                        action_type="human_review",
                        requested_by=AgentName.GOVERNANCE.value,
                        details={"reason": "autonomous_action_not_allowed"},
                    )
                ]
            return "needs_human_review", review_actions
        if governance.requires_human_approval:
            if human_status == "approved":
                return "approved", safe_actions
            if human_status == "rejected":
                return "rejected", []
            return "needs_human_review", [
                action
                for action in safe_actions
                if action.action_type in {"human_review", "protect_customer_promises"}
            ]
        if not safe_actions:
            return "blocked", []
        return "recommended", safe_actions

    @staticmethod
    def _dry_run_actions(
        actions: list[ProposedAction],
        scenario: Scenario,
        transfer: TransferOption | None,
        bid: SupplierBid | None,
        blackboard: Blackboard,
        tracer: TraceLogger,
    ) -> None:
        for action in actions:
            result = None
            if action.action_type == "transfer_inventory" and transfer:
                result = MockActionTools.transfer(
                    transfer, scenario.sku, scenario.store_id
                )
            elif action.action_type == "supplier_expedite" and bid:
                required = int(action.details.get("requested_quantity", bid.capacity))
                result = MockActionTools.expedite(
                    bid, scenario.sku, min(required, bid.capacity)
                )
            elif action.action_type in {
                "pause_promotion",
                "hold_price",
                "price_increase",
                "discount",
            }:
                result = MockActionTools.pricing(
                    action.action_type,
                    scenario.sku,
                    float(action.details.get("change_pct", 0)),
                )
            if result:
                serialized = result.model_dump(mode="json")
                blackboard.tool_results.append(serialized)
                tracer.record(
                    agent=AgentName.SUPERVISOR.value,
                    event_type="tool_dry_run",
                    message=serialized,
                    decision="not_executed",
                )

    @staticmethod
    def _evidence_summary(
        forecast: dict[str, Any] | None,
        inventory: dict[str, Any],
        supplier: dict[str, Any] | None,
        pricing: dict[str, Any],
        customer: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "forecast": forecast,
            "inventory": inventory,
            "supplier_selection": supplier,
            "pricing": pricing,
            "customer_promise": customer,
        }

    @staticmethod
    def _rationale(
        forecast: dict[str, Any] | None,
        inventory: dict[str, Any],
        supplier: dict[str, Any] | None,
        governance_reasons: list[str],
    ) -> list[str]:
        if forecast is None:
            return [
                "Forecast evidence is unavailable, so the supervisor refuses "
                "autonomous stockout action.",
                *governance_reasons,
            ]
        rationale = [
            (
                f"7-day demand is {forecast['forecast_7d_units']} units at "
                f"{forecast.get('spike_ratio', 0):.2f}x baseline."
            ),
            (
                f"Inventory analysis found a {inventory['shortfall_units']}-unit "
                "shortfall before response."
            ),
        ]
        if inventory.get("selected_transfer"):
            transfer = inventory["selected_transfer"]
            rationale.append(
                f"Transfer from {transfer['from_location']} contributes "
                f"{transfer['quantity']} units in {transfer['eta_days']} day(s)."
            )
        if supplier and supplier.get("selected_bid"):
            bid = supplier["selected_bid"]
            rationale.append(
                f"{bid['supplier_id']} is the highest-ranked feasible bid: "
                f"${bid['cost']:,.0f}, {bid['lead_time_days']} day(s), "
                f"{bid['reliability']:.0%} reliability."
            )
        rationale.extend(governance_reasons)
        return rationale

    @staticmethod
    def _explanation_factors(
        forecast: dict[str, Any] | None,
        inventory: dict[str, Any],
        supplier: dict[str, Any] | None,
        governance: Any,
    ) -> list[dict[str, Any]]:
        factors = [
            {
                "factor": "forecast_evidence",
                "value": forecast["forecast_7d_units"] if forecast else None,
                "effect": "sets required 7-day coverage",
            },
            {
                "factor": "inventory_shortfall",
                "value": inventory.get("shortfall_units"),
                "effect": "selects monitor, transfer, or supplier path",
            },
        ]
        if supplier and supplier.get("feasible_ranking"):
            factors.append(
                {
                    "factor": "contract_net_score",
                    "value": supplier["feasible_ranking"][0]["score"],
                    "effect": "ranks feasible supplier bids",
                }
            )
        factors.append(
            {
                "factor": "governance_outcome",
                "value": governance.outcome,
                "effect": "blocks, gates, or permits the recommendation",
            }
        )
        return factors
