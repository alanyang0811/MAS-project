"""Auditable shared evidence surface for one stockout case."""

from __future__ import annotations

from typing import Any

from .schemas import (
    AgentMessage,
    ApprovalPacket,
    EvidenceRecord,
    FinalPlan,
    GovernanceDecision,
    HumanDecision,
    ProposedAction,
    SupplierBid,
)


class Blackboard:
    """Externalized case state; agents communicate through typed records."""

    def __init__(self, case_metadata: dict[str, Any]) -> None:
        self.case_metadata = case_metadata
        self.evidence: dict[str, list[EvidenceRecord]] = {}
        self.messages: list[AgentMessage] = []
        self.proposed_actions: list[ProposedAction] = []
        self.supplier_bids: list[SupplierBid] = []
        self.governance_decisions: list[GovernanceDecision] = []
        self.human_approval_status: HumanDecision | None = None
        self.approval_packet: ApprovalPacket | None = None
        self.final_plan: FinalPlan | None = None
        self.warnings: list[str] = []
        self.failure_flags: list[str] = []
        self.tool_results: list[dict[str, Any]] = []
        self.metrics: dict[str, Any] = {}
        self._idempotency_keys: set[str] = set()

    def post_message(self, message: AgentMessage) -> bool:
        if message.idempotency_key in self._idempotency_keys:
            self.warnings.append(
                f"duplicate_message_ignored:{message.idempotency_key}"
            )
            return False
        self._idempotency_keys.add(message.idempotency_key)
        self.messages.append(message)
        return True

    def post_evidence(self, record: EvidenceRecord) -> None:
        self.evidence.setdefault(record.agent, []).append(record)

    def latest_evidence(
        self, agent: str, evidence_type: str | None = None
    ) -> EvidenceRecord | None:
        records = self.evidence.get(agent, [])
        if evidence_type is not None:
            records = [r for r in records if r.evidence_type == evidence_type]
        return records[-1] if records else None

    def snapshot(self) -> dict[str, Any]:
        return {
            "case_metadata": self.case_metadata,
            "evidence": {
                agent: [record.model_dump(mode="json") for record in records]
                for agent, records in self.evidence.items()
            },
            "messages": [message.model_dump(mode="json") for message in self.messages],
            "proposed_actions": [
                action.model_dump(mode="json") for action in self.proposed_actions
            ],
            "supplier_bids": [
                bid.model_dump(mode="json") for bid in self.supplier_bids
            ],
            "governance_decisions": [
                decision.model_dump(mode="json")
                for decision in self.governance_decisions
            ],
            "human_approval_status": (
                self.human_approval_status.model_dump(mode="json")
                if self.human_approval_status
                else None
            ),
            "final_plan": (
                self.final_plan.model_dump(mode="json") if self.final_plan else None
            ),
            "warnings": self.warnings,
            "failure_flags": self.failure_flags,
            "tool_results": self.tool_results,
            "metrics": self.metrics,
        }
