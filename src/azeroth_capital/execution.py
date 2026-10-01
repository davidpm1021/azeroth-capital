"""Purchase-depth and cash-recovery stress tests, without assuming sale fills."""
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from math import isfinite
from statistics import mean, median
from typing import Callable

from .premises import ARMS, PremiseEvaluation
from .timestamps import parse_timestamp


Book = list[tuple[int, int]]


def buy_with_budget(book: Book, budget: int, unit_cap: int) -> tuple[int, int]:
    """Walk ascending asks with integer units/copper; never extrapolate depth."""
    if budget < 0 or unit_cap < 0:
        raise ValueError("Budget and unit cap must be nonnegative")
    units, spent = 0, 0
    for price, quantity in sorted(book):
        if price <= 0 or quantity < 0:
            raise ValueError("Book prices must be positive and quantities nonnegative")
        take = min(quantity, unit_cap-units, (budget-spent)//price)
        units += take
        spent += take*price
    return units, spent


@dataclass(frozen=True)
class ExecutionResult:
    cohort_at: str
    arm: str
    item_id: int
    horizon_hours: int
    entry_at: str
    exit_at: str
    budget_copper: int
    inventory_share_cap: float
    units: int
    spent_copper: int
    unused_copper: int
    entry_vwap_copper: float | None
    entry_floor_copper: int
    exit_floor_copper: int
    exit_reference_copper: int
    break_even_exit_copper: int | None
    break_even_units: int | None
    break_even_sell_pct: float | None
    all_sold_net_copper: int
    half_sold_units: int
    half_sold_net_copper: int
    unsold_units_at_half: int


@dataclass
class ExecutionEvaluation:
    premise_cohorts: int
    complete_book_cohorts: int = 0
    missing_book_orders: int = 0
    results: list[ExecutionResult] = field(default_factory=list)


def stress_execution(
    evaluation: PremiseEvaluation,
    book_lookup: Callable[[int, str], Book],
    budget_g: int = 1000,
    max_inventory_share: float = 0.01,
) -> ExecutionEvaluation:
    if not isinstance(budget_g, int) or budget_g <= 0 or not isfinite(max_inventory_share) or not 0 < max_inventory_share <= 1:
        raise ValueError("Positive gold budget and inventory share in (0, 1] required")
    budget = budget_g*10_000
    cohorts = defaultdict(list)
    for row in evaluation.results:
        if row.mode == "next_snapshot":
            cohorts[row.cohort_at].append(row)
    report = ExecutionEvaluation(premise_cohorts=len(cohorts))

    @lru_cache(maxsize=None)
    def get_book(item_id, stamp):
        return book_lookup(item_id, stamp)

    for stamp, rows in sorted(cohorts.items()):
        results = []
        for row in rows:
            entry = get_book(row.item_id, parse_timestamp(row.entry_at).isoformat())
            exit_book = get_book(row.item_id, parse_timestamp(row.exit_at).isoformat())
            if not entry or not exit_book:
                report.missing_book_orders += 1
                continue
            if any(p <= 0 or q < 0 for p, q in entry+exit_book):
                raise ValueError("Invalid stored price level")
            entry = [(p,q) for p,q in entry if q > 0]
            exit_book = [(p,q) for p,q in exit_book if q > 0]
            if not entry or not exit_book:
                report.missing_book_orders += 1
                continue
            cap = int(sum(q for _,q in entry)*max_inventory_share)
            units, spent = buy_with_budget(entry, budget, cap)
            exit_floor = min(p for p,_ in exit_book)
            # Explicitly conditional: these are proceeds IF units sell at that ask.
            # A future sell listing is not a bid and provides no evidence of fills.
            all_net = units*exit_floor*95//100
            half = units//2
            half_net = half*exit_floor*95//100
            break_units = (100*spent+95*exit_floor-1)//(95*exit_floor) if units else None
            break_price = (100*spent+95*units-1)//(95*units) if units else None
            results.append(ExecutionResult(
                stamp, row.arm, row.item_id, row.horizon_hours, row.entry_at, row.exit_at, budget,
                max_inventory_share, units, spent, budget-spent,
                spent/units if units else None, min(p for p,_ in entry), exit_floor,
                row.exit_price, break_price, break_units,
                break_units/units*100 if units else None, all_net, half, half_net, units-half))
        # Preserve matched coverage across arms: no dropping only a difficult item.
        if len(results) == len(rows):
            report.complete_book_cohorts += 1
            report.results.extend(results)
    return report


def summarize_execution(evaluation: ExecutionEvaluation) -> list[dict]:
    groups = defaultdict(list)
    for row in evaluation.results:
        groups[(row.arm,row.cohort_at)].append(row)
    summaries = []
    for arm in ARMS:
        cohorts = [rows for (a,_),rows in groups.items() if a == arm]
        if not cohorts:
            continue
        allocated_returns, spent_returns, utilizations = [], [], []
        item_contributions = defaultdict(float)
        for rows in cohorts:
            allocated = sum(r.budget_copper for r in rows)
            spent = sum(r.spent_copper for r in rows)
            proceeds = sum(r.all_sold_net_copper for r in rows)
            allocated_returns.append((proceeds-spent)/allocated*100)
            utilizations.append(spent/allocated*100)
            for row in rows:
                item_contributions[row.item_id] += (
                    (row.all_sold_net_copper-row.spent_copper)/allocated*100/len(cohorts))
            if spent:
                spent_returns.append((proceeds-spent)/spent*100)
        orders = [r for rows in cohorts for r in rows]
        bought = [r for r in orders if r.units]
        best_item = min(item_contributions, key=lambda i: (-item_contributions[i], i))
        best_contribution = item_contributions[best_item]
        summaries.append(dict(
            arm=arm, cohorts=len(cohorts), orders=len(orders), purchased_orders=len(bought),
            all_sold_return_on_budget_pct=mean(allocated_returns),
            all_sold_return_on_spend_pct=mean(spent_returns) if spent_returns else None,
            mean_budget_used_pct=mean(utilizations),
            median_break_even_sell_pct=median(r.break_even_sell_pct for r in bought) if bought else None,
            median_all_sold_order_pct=median((r.all_sold_net_copper/r.spent_copper-1)*100 for r in bought) if bought else None,
            positive_all_sold_orders_pct=sum(r.all_sold_net_copper>r.spent_copper for r in bought)/len(bought)*100 if bought else None,
            half_sale_cash_recovery_pct=sum(r.half_sold_net_copper for r in bought)/sum(r.spent_copper for r in bought)*100 if bought else None,
            largest_positive_item_id=best_item if best_contribution > 0 else None,
            largest_positive_item_contribution_pp=max(0, best_contribution),
            return_without_largest_positive_item_pct=mean(allocated_returns)-max(0, best_contribution),
        ))
    return summaries
