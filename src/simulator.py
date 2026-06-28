"""Scenario loading and repeatable batch execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .blackboard import Blackboard
from .coordinator import StockoutCoordinator
from .schemas import FinalPlan, Scenario


@dataclass(frozen=True)
class RunResult:
    scenario_path: Path
    plan: FinalPlan
    blackboard: Blackboard
    trace_path: Path


def load_scenario(path: Path | str) -> Scenario:
    scenario_path = Path(path)
    with scenario_path.open(encoding="utf-8") as handle:
        return Scenario.model_validate(json.load(handle))


def run_scenario(
    path: Path | str, runs_dir: Path | str = "runs"
) -> RunResult:
    scenario_path = Path(path)
    scenario = load_scenario(scenario_path)
    plan, blackboard, trace_path = StockoutCoordinator(runs_dir).run(scenario)
    return RunResult(scenario_path, plan, blackboard, trace_path)


def run_all(
    scenarios_dir: Path | str = "scenarios", runs_dir: Path | str = "runs"
) -> list[RunResult]:
    paths = sorted(Path(scenarios_dir).glob("*.json"))
    return [run_scenario(path, runs_dir) for path in paths]
