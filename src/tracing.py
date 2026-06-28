"""Durable JSONL trace logging."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schemas import AgentMessage, TraceEvent


class TraceLogger:
    def __init__(self, trace_id: str, output_path: Path) -> None:
        self.trace_id = trace_id
        self.output_path = output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8")

    def record(
        self,
        *,
        agent: str,
        event_type: str,
        message: dict[str, Any] | None = None,
        decision: str | None = None,
        confidence: float | None = None,
        requires_approval: bool | None = None,
        message_id: str | None = None,
    ) -> None:
        event = TraceEvent(
            trace_id=self.trace_id,
            agent=agent,
            event_type=event_type,
            message_id=message_id,
            message=message or {},
            decision=decision,
            confidence=confidence,
            requires_approval=requires_approval,
        )
        with self.output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.model_dump(mode="json"), sort_keys=True))
            handle.write("\n")

    def record_message(self, message: AgentMessage) -> None:
        self.record(
            agent=message.sender_agent,
            event_type="message_posted",
            message=message.model_dump(mode="json"),
            confidence=message.confidence,
            requires_approval=message.requires_approval,
            message_id=message.message_id,
        )
