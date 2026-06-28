"""Typed, deterministic tools with no real-world side effects."""

from __future__ import annotations

from .schemas import SupplierBid, ToolResult, TransferOption


def demand_spike_ratio(observed_daily: float, baseline_daily: float) -> float:
    return round(observed_daily / baseline_daily, 3)


def inventory_shortfall(
    forecast_units: int, on_hand: int, pending_orders: int
) -> int:
    return max(0, forecast_units - on_hand - pending_orders)


def select_transfer(
    options: list[TransferOption],
    shortfall: int,
    min_donor_service_level: float,
) -> tuple[TransferOption | None, int]:
    eligible = [
        option
        for option in options
        if option.donor_service_level_after >= min_donor_service_level
    ]
    eligible.sort(key=lambda option: (option.eta_days, option.cost, -option.quantity))
    for option in eligible:
        if option.quantity >= shortfall:
            return option, 0
    if not eligible:
        return None, shortfall
    best = max(
        eligible,
        key=lambda option: (
            option.quantity,
            -option.eta_days,
            -option.cost,
        ),
    )
    return best, max(0, shortfall - best.quantity)


def rank_supplier_bids(
    bids: list[SupplierBid],
    required_units: int,
    min_reliability: float,
    max_lead_days: int,
) -> tuple[list[tuple[SupplierBid, float]], list[dict[str, object]]]:
    rejected: list[dict[str, object]] = []
    feasible: list[SupplierBid] = []
    for bid in bids:
        reasons: list[str] = []
        if bid.capacity < required_units:
            reasons.append("insufficient_capacity")
        if bid.reliability < min_reliability:
            reasons.append("reliability_below_policy")
        if bid.lead_time_days > max_lead_days:
            reasons.append("lead_time_above_policy")
        if reasons:
            rejected.append({"supplier_id": bid.supplier_id, "reasons": reasons})
        else:
            feasible.append(bid)

    if not feasible:
        return [], rejected

    min_cost = min(bid.cost for bid in feasible)
    max_cost = max(bid.cost for bid in feasible)
    cost_span = max(max_cost - min_cost, 1.0)
    ranked: list[tuple[SupplierBid, float]] = []
    for bid in feasible:
        cost_score = 1 - ((bid.cost - min_cost) / cost_span)
        lead_score = 1 - ((bid.lead_time_days - 1) / max(max_lead_days - 1, 1))
        score = round(
            (0.40 * cost_score)
            + (0.35 * lead_score)
            + (0.25 * bid.reliability),
            4,
        )
        ranked.append((bid, score))
    ranked.sort(key=lambda item: (-item[1], item[0].cost, item[0].supplier_id))
    return ranked, rejected


class MockActionTools:
    """Dry-run adapters prove the permission boundary without external writes."""

    @staticmethod
    def transfer(option: TransferOption, sku: str, destination: str) -> ToolResult:
        return ToolResult(
            tool_name="inventory.transfer",
            summary=(
                f"Dry-run transfer of {option.quantity} {sku} units from "
                f"{option.from_location} to {destination}"
            ),
            rollback_token=f"discard-transfer-{sku}-{option.from_location}",
            payload=option.model_dump(mode="json"),
        )

    @staticmethod
    def expedite(bid: SupplierBid, sku: str, quantity: int) -> ToolResult:
        return ToolResult(
            tool_name="supplier.expedite",
            summary=(
                f"Dry-run expedite of {quantity} {sku} units from {bid.supplier_id}"
            ),
            rollback_token=f"discard-expedite-{sku}-{bid.supplier_id}",
            payload=bid.model_dump(mode="json"),
        )

    @staticmethod
    def pricing(action: str, sku: str, change_pct: float = 0) -> ToolResult:
        return ToolResult(
            tool_name="pricing.propose_change",
            summary=f"Dry-run pricing recommendation {action} for {sku}",
            rollback_token=f"discard-pricing-{sku}",
            payload={"action": action, "change_pct": change_pct},
        )
