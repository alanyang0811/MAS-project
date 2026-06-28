"""Command-line demo for one or all deterministic scenarios."""

from __future__ import annotations

import argparse
from pathlib import Path

from .simulator import RunResult, run_all, run_scenario


def print_result(result: RunResult) -> None:
    plan = result.plan
    blackboard = result.blackboard
    print(f"\nScenario: {plan.scenario_name}")
    print("Agent findings:")
    for agent, records in blackboard.evidence.items():
        latest = records[-1]
        print(
            f"  - {agent}: {latest.evidence_type} "
            f"(confidence={latest.confidence:.2f})"
        )
    if blackboard.supplier_bids:
        print("Supplier bids:")
        for bid in blackboard.supplier_bids:
            print(
                f"  - {bid.supplier_id}: cost=${bid.cost:,.0f}, "
                f"lead={bid.lead_time_days}d, capacity={bid.capacity}, "
                f"reliability={bid.reliability:.0%}"
            )
    print(
        f"Governance: {plan.governance.outcome} - "
        + "; ".join(plan.governance.reasons)
    )
    print(
        "Human approval required: "
        f"{'yes' if plan.governance.requires_human_approval else 'no'}"
    )
    if plan.human_decision:
        print(f"Human approval status: {plan.human_decision.status}")
    print(f"Final plan status: {plan.status}")
    print("Final recommended plan:")
    if plan.actions:
        for action in plan.actions:
            print(f"  - {action.action_type}: {action.details}")
    else:
        print("  - No action authorized.")
    print(f"Trace: {result.trace_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Stockout Response Multi-Agent System"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", type=Path, help="Path to one scenario JSON")
    group.add_argument("--all", action="store_true", help="Run every scenario")
    parser.add_argument(
        "--scenarios-dir", type=Path, default=Path("scenarios")
    )
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.all:
        results = run_all(args.scenarios_dir, args.runs_dir)
    else:
        results = [run_scenario(args.scenario, args.runs_dir)]
    for result in results:
        print_result(result)


if __name__ == "__main__":
    main()
