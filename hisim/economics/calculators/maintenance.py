"""Maintenance and fixed operation cost of one subject over years 1..T (cost_spec.md §2.3, §3.6 rule 4).

A subject is one costed thing on the timeline, such as a heat pump or the building envelope. Each year it pays a
maintenance rate times its gross investment plus a flat fixed operation cost, both escalated with the general price
escalation rate. The two are kept as separate entries because the actor rules split them differently (§6.2).
`evaluator.build_timeline` calls this once per subject; energy cost lives in `energy.py`.
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
    """Return the maintenance and fixed operation entries of one subject for years 1..T (§3.6 rule 4).

    Each year gets up to two entries: MAINTENANCE (`maintenance_rate * gross`) and FIXED_OPERATION (the flat annual
    fee), both computed as `(maintenance_rate * I_gross + fixed_operation_cost) * (1 + r_gen)**(t-1)`. A kind that is
    zero in every band slot (minimum, best estimate, maximum) emits no entry. The base is always the original gross
    investment, not a replacement price, and kept existing assets pay maintenance too.

    Args:
        costing: The subject's resolved costing; supplies the maintenance rate (a share of gross investment per year),
            the fixed operation cost in euro per year, the subject name and the provenance ids.
        gross: The gross investment in euro at price-basis-year prices.
        general_escalation_rate: Nominal annual escalation rate as a fraction.
        horizon: Observation period T in years.

    Returns:
        Cost-positive entries in nominal euros of their own year, in year order, up to two per year.
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
