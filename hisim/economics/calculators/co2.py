"""CO2 accounting: mass, damage cost — and what must never be added (cost-spec-v2 §2.3).

The §2.3 "CO2 accounting" calculator. The engine handles **four** CO2 figures that live in
different units and different views, and summing any two of them is a modelling error
(cost_spec.md §3.8, `results.py`):

1. **Embodied mass** (kg) — the manufacture of a device, charged again at every replacement.
   Accumulated by the investment calculator, folded in here.
2. **Operational mass** (kg/a) — emission factor × annualized energy bought, per carrier.
   Produced by the energy calculator, folded in here.
3. **CO2 price** (EUR) — a *real cash flow*, part of the energy bill, already on the timeline
   as ENERGY_CO2_PRICE entries; emitted by the energy calculator because it is priced per
   carrier off the CO2 price path.
4. **CO2 damage cost** (EUR) — a *shadow price* that exists only under the macroeconomic
   accounting (§4.5): the social cost of the operational emissions, emitted here as CO2_DAMAGE
   entries. It is not money anyone pays, and it never coexists with (3) — the macroeconomic
   view suppresses the CO2 price component precisely so the two are not double counted.

This module owns the mass bookkeeping (1, 2) and the damage entries (4). The price component
(3) stays in `energy.py`, where the tariff and the price path already are.

**Threading note.** `LifecycleCo2Result` is a result object the orchestrator owns and two
phases contribute to. Rather than force purity on it, the contributions are explicit
`accumulate_*` calls with the accumulator passed in — the mutation is visible at the call site
and its order (which matters: float addition is not associative) is the orchestrator's.

Realizes: cost_spec.md §3.8 (parallel CO2 accounting), §4.5 (macroeconomic damage cost).
"""

from __future__ import annotations

from typing import Iterable, List

from hisim.economics.calculators.energy import EnergyFlowResult
from hisim.economics.parameters import EconomicParameters
from hisim.economics.results import LifecycleCo2Result
from hisim.economics.timeline import CashFlowEntry, CostCategory
from hisim.economics.uncertainty import UncertainValue


class Co2Constants:
    """Labels and unit conversions of the parallel CO2 accounting (§3.8, §4.5).

    The two things the damage-cost calculation needs that are neither data nor formula: the
    synthetic timeline subject the shadow cost is booked under (it belongs to no component, like
    the financing and reserve subjects), and the kg/t conversion. The conversion is named because
    it is exactly the kind of factor that silently produces a 1000x error — emissions are tracked
    in kilograms everywhere in the engine while carbon prices and damage costs are quoted per
    metric ton.
    """

    #: Timeline subject the macroeconomic damage cost is booked under.
    CO2_DAMAGE_SUBJECT = "co2 damage"

    #: Kilograms per metric ton — the damage cost is quoted per ton, emissions are tracked in kg.
    KILOGRAMS_PER_TON = 1000.0


def accumulate_embodied_co2(
    co2_result: LifecycleCo2Result, subject: str, masses_in_kg: Iterable[float]
) -> None:
    """Adds one subject's embodied CO2 masses to the total and to its per-subject entry (§3.8).

    Takes the masses in emit order (installation first, then one per replacement) instead of a
    pre-summed figure, so the addition order into the running totals is unchanged.

    Called by `evaluator.build_timeline` once per cost subject with the masses the investment
    calculator scheduled, which is why a device replaced twice within the horizon is counted
    three times: embodied CO2 is charged at manufacture, and every replacement is a new device.
    The result feeds `LifecycleCo2Result.embodied_co2_in_kg` (the headline embodied figure) and
    the per-subject map the §7.4 breakdowns and the report's embodied-vs-operational bars read.

    Args:
        co2_result: The orchestrator's accumulator; **mutated in place** (see the module's
            threading note).
        subject: Timeline subject name, the key of the per-subject map.
        masses_in_kg: One mass per installation event, in kilograms, in emit order.
    """
    for mass in masses_in_kg:
        co2_result.embodied_co2_in_kg += mass
        co2_result.embodied_by_subject_in_kg[subject] = (
            co2_result.embodied_by_subject_in_kg.get(subject, 0.0) + mass
        )


def accumulate_operational_emissions(
    energy_result: EnergyFlowResult, co2_result: LifecycleCo2Result, horizon: int
) -> None:
    """Folds the per-carrier operational CO2 into the lifecycle CO2 result (§3.8).

    Carrier by carrier, year ascending — the order the former inline accumulation used, kept
    because float addition into `operational_co2_by_year_in_kg` is order-dependent.

    Emissions are held constant across the horizon: the annualized year-1 energy purchase times
    the carrier's emission factor, repeated for every year. Grid decarbonization is therefore
    *not* modelled in v1 — a falling electricity emission factor would be a per-year factor path
    and is out of scope — which a reviewer reading a 20-year electricity CO2 figure should know.
    Run after `build_energy_flows` and before :func:`build_co2_damage_entries`, which prices what
    this accumulates.

    Args:
        energy_result: The energy calculator's output; only its `emissions` list is read.
        co2_result: The orchestrator's accumulator; **mutated in place**. Its
            `operational_co2_by_year_in_kg` is a per-year array in kg indexed 0..T (year 0 stays
            zero), while `operational_co2_by_carrier_in_kg` holds the carrier's total over the
            *whole* horizon — two different units in two fields of the same object. Both
            accumulate across records, so two meters buying the same carrier add up in the
            carrier map exactly as they do in the yearly series.
        horizon: Observation period T in years.
    """
    for carrier_emissions in energy_result.emissions:
        annual = carrier_emissions.annual_emissions_in_kg
        for projection_year in range(1, horizon + 1):
            co2_result.operational_co2_by_year_in_kg[projection_year] += annual
        # Accumulated, not assigned: a carrier can be billed by more than one meter, and the
        # per-year series above sums those records too — the two views must describe one quantity.
        co2_result.operational_co2_by_carrier_in_kg[carrier_emissions.carrier_value] = (
            co2_result.operational_co2_by_carrier_in_kg.get(carrier_emissions.carrier_value, 0.0)
            + annual * horizon
        )


def build_co2_damage_entries(
    co2_result: LifecycleCo2Result, parameters: EconomicParameters, horizon: int
) -> List[CashFlowEntry]:
    """The macroeconomic shadow cost of the operational emissions (§4.5).

    Only called under MACROECONOMIC accounting, and only after the operational masses have been
    accumulated. Years with no emissions emit no entry.

    This is figure (4) of the module docstring: a *shadow price* on the emissions, not money any
    household pays, applied at a flat damage cost (default 250 EUR/t, the UBA recommendation) that
    does not vary over the horizon — unlike the CO2 *price* component of the energy bill, which
    follows a trajectory. The macroeconomic view suppresses that price component precisely so the
    two never coexist. Only *operational* emissions are priced; embodied CO2 carries no damage
    cost in v1.

    Args:
        co2_result: Must already hold the accumulated `operational_co2_by_year_in_kg`.
        parameters: Supplies `co2_damage_cost_in_euro_per_ton`.
        horizon: Observation period T in years; entries are considered for years 1..T.

    Returns:
        Cost-positive CO2_DAMAGE entries in nominal euros of their year, one per emitting year,
        booked under the synthetic `co2 damage` subject.
    """
    damage_rate = parameters.co2_damage_cost_in_euro_per_ton / Co2Constants.KILOGRAMS_PER_TON  # EUR per kg
    entries: List[CashFlowEntry] = []
    for year in range(1, horizon + 1):
        emissions = (
            co2_result.operational_co2_by_year_in_kg[year]
            if year < len(co2_result.operational_co2_by_year_in_kg)
            else 0.0
        )
        if emissions:
            entries.append(
                CashFlowEntry(
                    year=year,
                    amount_in_euro=UncertainValue.exact(emissions * damage_rate),
                    category=CostCategory.CO2_DAMAGE,
                    subject=Co2Constants.CO2_DAMAGE_SUBJECT,
                )
            )
    return entries


def finalize_total_co2(co2_result: LifecycleCo2Result) -> None:
    """Closes the mass accounting: embodied + operational over the whole horizon (§3.8).

    The one place the two *mass* figures (1) and (2) are legitimately added — they share the unit
    kilograms and the same physical system boundary — and the last step of CO2 accounting, called
    by `evaluator.build_timeline` after every subject and every carrier has contributed. The
    result is the undiscounted lifecycle CO2 the KPI exports and the report publish; the two euro
    figures (3) and (4) are never mixed in here.

    Args:
        co2_result: The accumulator; **mutated in place**, setting `total_co2_in_kg`.
    """
    co2_result.total_co2_in_kg = co2_result.embodied_co2_in_kg + sum(
        co2_result.operational_co2_by_year_in_kg
    )
