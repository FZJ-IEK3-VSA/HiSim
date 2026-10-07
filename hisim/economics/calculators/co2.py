"""CO2 accounting: masses, the damage cost, and the figures that must never be added (cost_spec.md §3.8, §4.5).

The engine keeps four CO2 figures, and summing any two of them is an error: (1) embodied mass in kg, charged at every
installation and replacement; (2) operational mass in kg per year, emission factor times energy bought; (3) the CO2
price in euro, a real cash flow inside the energy bill (`energy.py`); (4) the CO2 damage cost in euro, a shadow price
used only under macroeconomic accounting, which suppresses (3). This module accumulates (1) and (2) into a
`LifecycleCo2Result` passed in by the orchestrator, and emits (4).
"""

from __future__ import annotations

from typing import Iterable, List

from hisim.economics.calculators.energy import EnergyFlowResult
from hisim.economics.catalog_entries import CostDataError
from hisim.economics.parameters import EconomicParameters
from hisim.economics.results import LifecycleCo2Result
from hisim.economics.timeline import CashFlowEntry, CostCategory
from hisim.economics.uncertainty import UncertainValue


#: Difference in kg per kWh below which two records of the same carrier count as carrying the
#: same emission factor. Both come from the same price entry, so the only difference that can
#: legitimately appear is float noise.
EMISSION_FACTOR_TOLERANCE_IN_KG_PER_KWH = 1e-12


class Co2Constants:
    """Labels and unit conversions of the CO2 accounting (§3.8, §4.5).

    Holds the synthetic timeline subject the damage cost is booked under (it belongs to no component) and the
    kg-per-ton factor: emissions are tracked in kg, while damage costs are quoted per metric ton.
    """

    #: Timeline subject the macroeconomic damage cost is booked under.
    CO2_DAMAGE_SUBJECT = "co2 damage"

    #: Kilograms per metric ton — the damage cost is quoted per ton, emissions are tracked in kg.
    KILOGRAMS_PER_TON = 1000.0


def accumulate_embodied_co2(
    co2_result: LifecycleCo2Result, subject: str, masses_in_kg: Iterable[float]
) -> None:
    """Add one subject's embodied CO2 masses to the total and to its per-subject entry (§3.8).

    A device replaced twice within the horizon is counted three times, since every replacement is a new device. The
    masses are added in emit order, so the float sum is reproducible.

    Args:
        co2_result: The orchestrator's accumulator; mutated in place.
        subject: Timeline subject name, the key of the per-subject map.
        masses_in_kg: One mass per installation event in kg, installation first, then one per replacement.
    """
    for mass in masses_in_kg:
        co2_result.embodied_co2_in_kg += mass
        co2_result.embodied_by_subject_in_kg[subject] = (
            co2_result.embodied_by_subject_in_kg.get(subject, 0.0) + mass
        )


def accumulate_operational_emissions(
    energy_result: EnergyFlowResult, co2_result: LifecycleCo2Result, horizon: int
) -> None:
    """Add the per-carrier operational CO2 of the energy calculator to the lifecycle CO2 result (§3.8).

    Emissions are constant over the horizon: annualized year-1 purchases times the carrier's emission factor, repeated
    every year, so grid decarbonization is not modelled. Records are added carrier by carrier, year ascending. Run
    after `build_energy_flows` and before :func:`build_co2_damage_entries`.

    Args:
        energy_result: The energy calculator's output; only its `emissions` list is read.
        co2_result: The accumulator; mutated in place. `operational_co2_by_year_in_kg` is a per-year array in kg
            indexed 0..T (year 0 stays zero); `operational_co2_by_carrier_in_kg` holds each carrier's total over the
            whole horizon. Records of the same carrier add up in both.
        horizon: Observation period T in years.

    Raises:
        CostDataError: If two records of the same carrier carry different emission factors.
    """
    for carrier_emissions in energy_result.emissions:
        annual = carrier_emissions.annual_emissions_in_kg
        for projection_year in range(1, horizon + 1):
            co2_result.operational_co2_by_year_in_kg[projection_year] += annual
        # The factor travels with the mass so the report can print the multiplication. It is a
        # price-entry property, identical for every meter of the same carrier, not a quantity that
        # adds up — so a second meter of the same carrier must arrive with the same factor. When
        # it does not, the mass below is a sum over two different factors and no single published
        # factor reproduces it; assigning last-wins would publish a multiplication that does not
        # come out.
        known_factor = co2_result.emission_factor_by_carrier_in_kg_per_kwh.get(
            carrier_emissions.carrier_value
        )
        if (
            known_factor is not None
            and abs(known_factor - carrier_emissions.emission_factor_in_kg_per_kwh)
            > EMISSION_FACTOR_TOLERANCE_IN_KG_PER_KWH
        ):
            raise CostDataError(
                f"Carrier {carrier_emissions.carrier_value} was billed under two different "
                f"emission factors ({known_factor:g} and "
                f"{carrier_emissions.emission_factor_in_kg_per_kwh:g} kg/kWh). The CO2 section "
                "publishes one factor per carrier and states the mass as factor x kWh, which no "
                "single factor would reproduce here."
            )
        co2_result.emission_factor_by_carrier_in_kg_per_kwh[carrier_emissions.carrier_value] = (
            carrier_emissions.emission_factor_in_kg_per_kwh
        )
        # Accumulated, not assigned: a carrier can be billed by more than one meter, and the
        # per-year series above sums those records too — the two views must describe one quantity.
        co2_result.operational_co2_by_carrier_in_kg[carrier_emissions.carrier_value] = (
            co2_result.operational_co2_by_carrier_in_kg.get(carrier_emissions.carrier_value, 0.0)
            + annual * horizon
        )


def build_co2_damage_entries(
    co2_result: LifecycleCo2Result, parameters: EconomicParameters, horizon: int
) -> List[CashFlowEntry]:
    """Return the macroeconomic CO2 damage cost of the operational emissions (§4.5).

    The damage cost is a shadow price, not money anyone pays: a flat rate per ton (default 250 EUR/t, the UBA
    recommendation) on the operational emissions only. Called only under MACROECONOMIC accounting, after the
    operational masses are accumulated. Years without emissions emit no entry.

    Args:
        co2_result: Must already hold `operational_co2_by_year_in_kg`.
        parameters: Supplies `co2_damage_cost_in_euro_per_ton`.
        horizon: Observation period T in years; years 1..T are considered.

    Returns:
        Cost-positive CO2_DAMAGE entries in nominal euros, one per emitting year, under the synthetic damage subject.
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
    """Set the total lifecycle CO2 mass: embodied plus operational over the whole horizon (§3.8).

    The only place the two masses are added; the euro figures are never mixed in. Called after every subject and
    carrier has contributed.

    Args:
        co2_result: The accumulator; mutated in place, setting `total_co2_in_kg`.
    """
    co2_result.total_co2_in_kg = co2_result.embodied_co2_in_kg + sum(
        co2_result.operational_co2_by_year_in_kg
    )
