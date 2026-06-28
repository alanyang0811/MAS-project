"""Typed contracts shared by every component in the simulation."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentName(str, Enum):
    SUPERVISOR = "supervisor_agent"
    FORECASTING = "forecasting_agent"
    INVENTORY = "inventory_agent"
    SUPPLIER_LOGISTICS = "supplier_logistics_agent"
    PRICING = "pricing_agent"
    CUSTOMER_PROMISE = "customer_promise_agent"
    GOVERNANCE = "governance_agent"
    HUMAN = "human_approver"


class MessageType(str, Enum):
    CASE_OPENED = "case_opened"
    FORECAST_STATUS = "forecast_status"
    INVENTORY_STATUS = "inventory_status"
    CALL_FOR_PROPOSALS = "call_for_proposals"
    SUPPLIER_BID = "supplier_bid"
    BID_AWARD_RECOMMENDATION = "bid_award_recommendation"
    PRICING_RECOMMENDATION = "pricing_recommendation"
    CUSTOMER_PROMISE_STATUS = "customer_promise_status"
    GOVERNANCE_REVIEW = "governance_review"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_RESPONSE = "approval_response"
    FINAL_PLAN = "final_plan"


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    trace_id: str
    message_id: str = Field(default_factory=lambda: f"msg-{uuid4().hex[:12]}")
    correlation_id: str
    sender_agent: str
    receiver_agent: str
    msg_type: MessageType
    priority: Literal["low", "normal", "high", "critical"] = "normal"
    deadline_ms: int = Field(default=5_000, gt=0)
    idempotency_key: str
    confidence: float = Field(ge=0, le=1)
    requires_approval: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)
    status: Literal["created", "posted", "processed", "rejected"] = "posted"
    timestamp: datetime = Field(default_factory=utc_now)
    security_context: dict[str, str] = Field(
        default_factory=lambda: {
            "classification": "internal",
            "authn": "mock-service-identity",
        }
    )


class TransferOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_location: str
    quantity: int = Field(gt=0)
    eta_days: int = Field(ge=0)
    cost: float = Field(ge=0)
    donor_service_level_after: float = Field(ge=0, le=1)
    region: str


class SupplierBid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: str
    cost: float = Field(gt=0)
    lead_time_days: int = Field(gt=0)
    capacity: int = Field(gt=0)
    reliability: float = Field(ge=0, le=1)


class DemandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline_daily_units: float = Field(gt=0)
    observed_daily_units: float = Field(gt=0)
    forecast_7d_units: int = Field(gt=0)
    confidence: float = Field(ge=0, le=1)
    evidence_available: bool = True


class InventoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on_hand: int = Field(ge=0)
    pending_orders: int = Field(ge=0)
    transfer_options: list[TransferOption] = Field(default_factory=list)


class PricingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    promotion_active: bool = False
    current_margin_pct: float = Field(default=0.3, ge=0, le=1)
    forced_recommendation: Literal[
        "none", "hold_price", "pause_promotion", "price_increase", "discount"
    ] | None = None
    proposed_price_change_pct: float = 0.0


class CustomerPromiseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    committed_units_7d: int = Field(ge=0)
    max_tolerable_delay_days: int = Field(ge=0)
    protected_segment: str = "all_customers"


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_forecast_confidence: float = Field(default=0.70, ge=0, le=1)
    expedite_approval_cost: float = Field(default=5_000, gt=0)
    max_price_increase_pct: float = Field(default=0.10, ge=0)
    min_supplier_reliability: float = Field(default=0.85, ge=0, le=1)
    max_supplier_lead_days: int = Field(default=4, gt=0)
    min_donor_service_level: float = Field(default=0.90, ge=0, le=1)


class HumanApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["approved", "rejected", "needs_review"] = "needs_review"
    reviewer: str = "mock-duty-manager"


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    sku: str
    store_id: str
    demand: DemandInput
    inventory: InventoryInput
    supplier_bids: list[SupplierBid] = Field(default_factory=list)
    pricing: PricingInput = Field(default_factory=PricingInput)
    customer_promise: CustomerPromiseInput
    policy: PolicyInput = Field(default_factory=PolicyInput)
    human_approval: HumanApprovalInput = Field(default_factory=HumanApprovalInput)

    @model_validator(mode="after")
    def unique_suppliers(self) -> "Scenario":
        ids = [bid.supplier_id for bid in self.supplier_bids]
        if len(ids) != len(set(ids)):
            raise ValueError("supplier_id values must be unique")
        return self


class EvidenceRecord(BaseModel):
    agent: str
    evidence_type: str
    facts: dict[str, Any]
    confidence: float = Field(ge=0, le=1)
    source: str
    timestamp: datetime = Field(default_factory=utc_now)


class ProposedAction(BaseModel):
    action_type: Literal[
        "monitor",
        "transfer_inventory",
        "supplier_expedite",
        "pause_promotion",
        "hold_price",
        "price_increase",
        "discount",
        "protect_customer_promises",
        "human_review",
    ]
    requested_by: str
    details: dict[str, Any] = Field(default_factory=dict)
    estimated_cost: float = Field(default=0, ge=0)
    reversible: bool = True
    execution_mode: Literal["recommend_only", "dry_run"] = "recommend_only"


class GovernanceDecision(BaseModel):
    outcome: Literal[
        "approved", "approved_with_blocks", "approval_required", "blocked"
    ]
    reasons: list[str]
    blocked_actions: list[ProposedAction] = Field(default_factory=list)
    requires_human_approval: bool = False
    autonomous_action_allowed: bool = True
    checks: dict[str, bool] = Field(default_factory=dict)


class ApprovalPacket(BaseModel):
    case_id: str
    trace_id: str
    proposed_actions: list[ProposedAction]
    evidence_summary: dict[str, Any]
    governance_reasons: list[str]
    rollback_plan: str


class HumanDecision(BaseModel):
    status: Literal["approved", "rejected", "needs_review"]
    reviewer: str
    rationale: str
    timestamp: datetime = Field(default_factory=utc_now)


class FinalPlan(BaseModel):
    scenario_name: str
    trace_id: str
    case_id: str
    status: Literal[
        "recommended", "approved", "rejected", "needs_human_review", "blocked"
    ]
    actions: list[ProposedAction]
    rationale: list[str]
    governance: GovernanceDecision
    human_decision: HumanDecision | None = None
    approval_packet: ApprovalPacket | None = None
    warnings: list[str] = Field(default_factory=list)
    explanation_factors: list[dict[str, Any]] = Field(default_factory=list)


class TraceEvent(BaseModel):
    timestamp: datetime = Field(default_factory=utc_now)
    trace_id: str
    agent: str
    event_type: str
    message_id: str | None = None
    message: dict[str, Any] = Field(default_factory=dict)
    decision: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    requires_approval: bool | None = None


class ToolResult(BaseModel):
    tool_name: str
    executed: bool = False
    reversible: bool = True
    summary: str
    rollback_token: str
    payload: dict[str, Any] = Field(default_factory=dict)
