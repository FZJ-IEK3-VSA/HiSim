"""Maintenance and fixed operation & maintenance cost (cost-spec-v2 §2.3).

The §2.3 "maintenance & fixed O&M" calculator. Recurring, non-energy operating cost of one
subject over years 1..T: a maintenance *rate* applied to the gross investment plus a flat
fixed operation cost, both escalated with the general price escalation rate from their year-1
level (cost_spec.md §3.6 rule 4).

**§7 B2 — fixed 2026-08-12.** The two cost kinds used to be summed into a *single* entry whose
category was decided by a heuristic (MAINTENANCE if the maintenance share was positive,
FIXED_OPERATION otherwise). That mislabelled the fixed opex of every subject that has both, and
it was not cosmetic: the DE2024 ruleset splits MAINTENANCE between landlord and tenant but
treats FIXED_OPERATION as tenant-only (§6.2), so the heuristic moved real money between actors.
They are now two separately categorized entries per year, each emitted only when it carries an
amount.

The module owns the *recurring non-energy* operating cost and nothing else: it does not price
anything (the rate and the fixed fee come resolved from `context_resolution.py`), it never
touches year 0, and energy is `energy.py`'s business. `evaluator.build_timeline` calls it once
per cost subject, immediately after the investment schedule, and extends the timeline with the
result.

Realizes: cost_spec.md §3.6 rule 4, §3.2 (escalation), §6.2 (why the categories must be kept
apart).
"""

from __future__ import annotations

from typing import List

from hisim.economics.calculators.context_resolution import DeviceCosting
from hisim.economics.calculators.escalation import escalate
from hisim.economics.timeline import CashFlowEntry, CostCategory
from hisim.economics.uncertainty import UncertainValue


def build_maintenance_entries(
    costing: DeviceCosting,
    gross: UncertainValue,
    general_escalation_rate: float,
    horizon: int,
) -> List[CashFlowEntry]:
    """Maintenance and fixed O&M entries for years 1..T (§3.6 rule 4).

    Up to two entries per year, each carrying its own category (§7 B2):

    * MAINTENANCE — `maintenance_rate * gross investment` band-wise;
    * FIXED_OPERATION — the flat annual fixed operation cost.

    Both are escalated with the general price escalation rate from their year-1 level. A cost
    kind that is zero in every slot emits no entry at all (both bands are non-negative, so
    `maximum != 0` is exactly "nonzero in some slot"), which is why a subject with only one of
    the two produces exactly the same timeline as before.

    Note that the maintenance base is the *original* gross investment, not the escalated
    replacement price: a replacement resets nothing here, the rate simply keeps compounding with
    the general escalation rate over the whole horizon. That is the §3.6 rule 4 formula
    `(maintenance_rate * I_gross + fixed_operation_cost) * (1 + r_gen)**(t-1)`, and it is applied
    to every subject including kept brownfield assets, which pay maintenance without ever having
    been charged an investment.

    Args:
        costing: The subject's resolved costing — supplies `maintenance_rate` (a dimensionless
            share of gross investment per year), `fixed_operation_cost` (euro per year, e.g.
            chimney sweep or metering fee), the subject name and the provenance ids.
        gross: `costing.gross_investment`, euro band at price-basis-year prices, cost-positive.
        general_escalation_rate: Nominal annual rate as a fraction, applied from the year-1 level
            (`EconomicParameters.general_price_escalation_rate`).
        horizon: Observation period T in years; entries are emitted for years 1..T inclusive.

    Returns:
        Cost-positive euro entries in year order, up to two per year, in nominal euros of their
        own year (undiscounted).
    """
    entries: List[CashFlowEntry] = []
    annual_maintenance = costing.maintenance_rate.multiply_band(gross)
    for year in range(1, horizon + 1):
        maintenance = escalate(annual_maintenance, general_escalation_rate, year - 1)
        fixed_operation = escalate(costing.fixed_operation_cost, general_escalation_rate, year - 1)
        if maintenance.maximum != 0:
            entries.append(
                CashFlowEntry(
                    year=year,
                    amount_in_euro=maintenance,
                    category=CostCategory.MAINTENANCE,
                    subject=costing.subject,
                    provenance_ids=costing.provenance_ids,
                )
            )
        if fixed_operation.maximum != 0:
            entries.append(
                CashFlowEntry(
                    year=year,
                    amount_in_euro=fixed_operation,
                    category=CostCategory.FIXED_OPERATION,
                    subject=costing.subject,
                    provenance_ids=costing.provenance_ids,
                )
            )
    return entries
