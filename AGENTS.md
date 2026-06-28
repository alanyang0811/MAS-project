# AGENTS.md

This repository is a bounded, deterministic course prototype. Preserve that property.

## Working contract

1. Read `README.md` and `docs/course_alignment.md` before changing architecture.
2. Use Python 3.10+ and typed Pydantic contracts. Do not add network services, model APIs, databases, or non-deterministic LLM calls.
3. Keep specialist boundaries narrow. A specialist posts evidence or a recommendation; only the supervisor assembles a plan; governance may block or gate it.
4. Never turn a mocked tool into a real side effect. `inventory.transfer`, `supplier.expedite`, and `pricing.propose_change` must keep `executed=false` unless the project scope is explicitly renegotiated.
5. Every new scenario must:
   - validate through `Scenario`;
   - produce a JSONL trace;
   - state an expected safe outcome;
   - add or update a pytest regression.
6. Every new message must carry the v1 trace, correlation, idempotency, deadline, confidence, approval, and security fields.
7. Put configurable thresholds in the scenario policy, not inside specialist logic.
8. Preserve evidence before changing a decision path. A failure should become a trace event and a regression test.

## One-command checks

From this directory:

```bash
python -m pytest
python -m src.run_demo --all
```

Run both before handing off a change. Generated `runs/*.jsonl` files are evidence artifacts and intentionally ignored by Git; `runs/.gitkeep` preserves the directory.

## Architecture map

- `src/schemas.py`: versioned contracts and validated scenario input
- `src/blackboard.py`: shared case evidence and audit state
- `src/agents.py`: specialist roles
- `src/coordinator.py`: routing, synthesis, escalation, final decision
- `src/guardrails.py`: policy and permission checks
- `src/tools.py`: pure calculations and dry-run tool adapters
- `src/tracing.py`: append-only JSONL evidence
- `src/evaluation.py`: trace-derived coordination metrics
- `src/simulator.py`: scenario loader and batch runner
- `tests/test_scenarios.py`: behavioral release gates

## Safety invariants

- Missing or low-confidence forecast evidence forbids autonomous action.
- A transfer cannot lower the donor below the configured service floor.
- Supplier bids must satisfy capacity, lead-time, and reliability constraints before ranking.
- High-cost expedite requires human approval.
- An excessive price increase is removed from the plan.
- Customer-promise risk requires escalation.
- Agent permission violations are blocked.
- Tool adapters remain dry-run and supply rollback/discard tokens.
- Governance logic stays outside specialist prompts or recommendations.

## Review checklist

- Does a role now know or do more than its responsibility allows?
- Is the message and scenario schema still strict (`extra="forbid"`)?
- Can the final decision be reconstructed from the trace?
- Is every blocked action absent from the final plan?
- Is a human given a concise evidence packet rather than raw chatter?
- Are the README commands and scenario expectations still true?
