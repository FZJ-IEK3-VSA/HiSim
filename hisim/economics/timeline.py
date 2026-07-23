"""The canonical cash-flow timeline (cost_spec.md §3.6).

One set of dated, categorized, payer-tagged cash flows per variant; every perspective, actor
view and KPI is a filter, allocation or discounting of this same timeline.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from hisim.economics.uncertainty import UncertainValue


class CostCategory(str, enum.Enum):
    """Categories of timeline entries (§3.6)."""

    INVESTMENT = "INVESTMENT"
    PLANNING = "PLANNING"
    REMOVAL = "REMOVAL"
    REPLACEMENT = "REPLACEMENT"
    RESIDUAL_VALUE = "RESIDUAL_VALUE"
    MAINTENANCE = "MAINTENANCE"
    FIXED_OPERATION = "FIXED_OPERATION"
    ENERGY_WORKING = "ENERGY_WORKING"
    ENERGY_STANDING = "ENERGY_STANDING"
    ENERGY_CO2_PRICE = "ENERGY_CO2_PRICE"
    ENERGY_CAPACITY_CHARGE = "ENERGY_CAPACITY_CHARGE"
    FEED_IN_REVENUE = "FEED_IN_REVENUE"
    SUBSIDY = "SUBSIDY"
    LOAN_INTEREST = "LOAN_INTEREST"
    LOAN_PRINCIPAL = "LOAN_PRINCIPAL"
    LOAN_DISBURSEMENT = "LOAN_DISBURSEMENT"
    CO2_DAMAGE = "CO2_DAMAGE"  # macroeconomic only
    ANYWAY_COST_CREDIT = "ANYWAY_COST_CREDIT"  # avoided like-for-like replacement (§4.1)
    REPLACEMENT_RESERVE = "REPLACEMENT_RESERVE"  # sinking fund of the operating view (§4.2)
    MODERNIZATION_LEVY = "MODERNIZATION_LEVY"  # tenant pays, landlord receives (§6.4)


#: Categories whose parameters are revenue-type for slot assembly (§3.9): the optimistic world
#: takes their band maximum. Their entries are negative.
REVENUE_CATEGORIES = frozenset(
    {
        CostCategory.FEED_IN_REVENUE,
        CostCategory.SUBSIDY,
        CostCategory.RESIDUAL_VALUE,
        CostCategory.ANYWAY_COST_CREDIT,
    }
)

#: Categories dropped when a perspective excludes investment (OPERATING_ONLY, §4.2).
INVESTMENT_CATEGORIES = frozenset(
    {
        CostCategory.INVESTMENT,
        CostCategory.PLANNING,
        CostCategory.REMOVAL,
        CostCategory.REPLACEMENT,
        CostCategory.RESIDUAL_VALUE,
        CostCategory.SUBSIDY,
        CostCategory.LOAN_INTEREST,
        CostCategory.LOAN_PRINCIPAL,
        CostCategory.LOAN_DISBURSEMENT,
        CostCategory.ANYWAY_COST_CREDIT,
    }
)

#: Support flows dropped by `subsidy_mode = NONE` (§5.5).
SUBSIDY_FLOW_CATEGORIES = frozenset({CostCategory.SUBSIDY})


class Actor(str, enum.Enum):
    """Payers of cash flows (§6.1)."""

    SYSTEM = "system"  # before allocation / total view
    OWNER_OCCUPIER = "owner_occupier"
    LANDLORD = "landlord"
    TENANT = "tenant"


class SubjectKind(str, enum.Enum):
    """What a timeline subject refers to (§3.7)."""

    COMPONENT = "COMPONENT"
    CARRIER = "CARRIER"


@dataclass(frozen=True)
class CashFlowEntry:
    """One dated, categorized, payer-tagged cash flow (§3.6).

    Sign convention: cost positive, revenue/subsidy negative. ``amount_in_euro`` carries the
    LOW/AVERAGE/HIGH world values (§3.9).
    """

    year: int
    amount_in_euro: UncertainValue
    category: CostCategory
    subject: str  # component name or carrier
    subject_kind: SubjectKind = SubjectKind.COMPONENT
    payer: Actor = Actor.SYSTEM
    subsidy_scheme_id: Optional[str] = None
    provenance_ids: Tuple[int, ...] = ()

    def with_payer(self, payer: Actor) -> "CashFlowEntry":
        """Copy with a different payer (allocation rulesets, §6)."""
        return replace(self, payer=payer)

    def scaled(self, factor: float) -> "CashFlowEntry":
        """Copy with the amount scaled by a non-negative share (entry splitting, §6)."""
        return replace(self, amount_in_euro=self.amount_in_euro.scale(factor))


@dataclass
class CashFlowTimeline:
    """The canonical timeline of one variant under one perspective."""

    entries: List[CashFlowEntry] = field(default_factory=list)

    def add(self, entry: CashFlowEntry) -> None:
        """Appends an entry."""
        self.entries.append(entry)

    def extend(self, entries: Iterable[CashFlowEntry]) -> None:
        """Appends entries."""
        self.entries.extend(entries)

    def filtered(self, predicate: Callable[[CashFlowEntry], bool]) -> "CashFlowTimeline":
        """New timeline with the entries matching the predicate."""
        return CashFlowTimeline(entries=[entry for entry in self.entries if predicate(entry)])

    def without_categories(self, categories: frozenset) -> "CashFlowTimeline":
        """New timeline without the given categories."""
        return self.filtered(lambda entry: entry.category not in categories)

    def npv(self, interest_rate: float) -> UncertainValue:
        """Slot-wise net present value at the given discount rate."""
        total = UncertainValue.exact(0.0)
        for entry in self.entries:
            total = total + entry.amount_in_euro.scale(1.0 / ((1.0 + interest_rate) ** entry.year))
        return total

    def npv_by(
        self,
        interest_rate: float,
        key: Callable[[CashFlowEntry], Any],
    ) -> Dict[Any, UncertainValue]:
        """Slot-wise NPV pivot by an arbitrary key (category, subject, payer)."""
        result: Dict[Any, UncertainValue] = {}
        for entry in self.entries:
            discounted = entry.amount_in_euro.scale(1.0 / ((1.0 + interest_rate) ** entry.year))
            bucket = key(entry)
            result[bucket] = result.get(bucket, UncertainValue.exact(0.0)) + discounted
        return result

    def nominal_annual_series(self, horizon_years: int) -> List[UncertainValue]:
        """Nominal euros per year 0..T (index = year); the liquidity view (§4.3)."""
        series = [UncertainValue.exact(0.0) for _ in range(horizon_years + 1)]
        for entry in self.entries:
            if 0 <= entry.year <= horizon_years:
                series[entry.year] = series[entry.year] + entry.amount_in_euro
        return series

    def subjects(self) -> List[str]:
        """All distinct subjects in first-appearance order."""
        seen: Dict[str, None] = {}
        for entry in self.entries:
            seen.setdefault(entry.subject, None)
        return list(seen.keys())
