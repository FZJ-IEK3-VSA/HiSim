"""Synthetic stages and a synthetic cost database for the staged-evaluator tests.

The staged evaluator is a pure function of stored ``economic_inputs.json`` records, so its tests
need neither a simulation nor the shipped price data — they need one cost database that
contributes no price of its own and a handful of hand-built
:class:`~hisim.economics.evaluator.EvaluationInputs` records. Both live here rather than in the
test module so that the invariant tests read as statements about the evaluator and not as fixture
plumbing, and so that the document tests of ``test_staged_document.py`` drive the same plan.

Everything the database carries is zero or an explicit override: the device figures come from
:class:`~hisim.economics.facts.ComponentCostFacts` overrides, the energy price entry exists only
so that the country passes the "has price data" check and the energy calculator has a carrier to
bill, and no escalation defaults file is written, so the parameter fallback chain stays at the
rates a test declares.
"""

import json
import os
from typing import Dict, List, Optional, Tuple

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import (
    BillingDeterminants,
    ComponentCostFacts,
    ExistingAsset,
    ExistingAssetRegister,
)
from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
from hisim.economics.staged import Stage
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units


class SyntheticPlan:
    """The constants the synthetic plan is built from, in one reviewable place.

    Every number here is invented for the tests and describes nothing in the world: the point of
    the fixture is that each figure reaches the engine as an explicit override, so an assertion
    about the staged evaluator can never be satisfied or broken by a shipped price. They are class
    attributes rather than free module constants because they describe one object — the synthetic
    plan — and are read from two test modules.
    """

    #: A country code no shipped data file uses, so nothing can leak in.
    COUNTRY = "XX"

    #: The year the synthetic simulations are dated to, and the database's only price year.
    YEAR = 2024

    #: Horizon of every synthetic evaluation, in years.
    HORIZON = 12

    #: Nominal discount rate of every synthetic evaluation.
    INTEREST_RATE = 0.03

    #: The baseline generator's purchase price in euro, as an override.
    BOILER_INVESTMENT_IN_EURO = 6000.0

    #: The staged generator's purchase price in euro, as an override.
    HEAT_PUMP_INVESTMENT_IN_EURO = 18000.0

    #: The envelope measure's price in euro, as an override.
    ENVELOPE_INVESTMENT_IN_EURO = 24000.0

    #: Service life of every synthetic subject, in years — inside the horizon on purpose, so the
    #: replacement and residual-value flows the ageing register schedules are actually exercised.
    LIFETIME_IN_YEARS = 10.0

    #: Annual maintenance as a share of the purchase price.
    MAINTENANCE_RATE = 0.02

    #: Electricity bought per year in the baseline state, in kWh.
    BASELINE_ELECTRICITY_IN_KWH = 9000.0

    #: Electricity bought per year once the plan has been carried out, in kWh.
    RENOVATED_ELECTRICITY_IN_KWH = 4000.0

    #: The synthetic electricity price, in euro per kWh, written into the database.
    ELECTRICITY_PRICE_IN_EURO_PER_KWH = 0.30

    #: Subject name of the baseline generator.
    BOILER_SUBJECT = "GenericBoiler"

    #: Subject name of the staged generator.
    HEAT_PUMP_SUBJECT = "HeatPump"

    #: Subject name of the envelope measure.
    ENVELOPE_SUBJECT = "facade_insulation"

    #: Installation year of the inventory generator, i.e. the house's own starting point.
    INVENTORY_INSTALLATION_YEAR = 2010


def write_database(directory: str) -> CostDatabase:
    """Write a cost database that prices nothing but one electricity carrier.

    Args:
        directory: A directory the three JSON files are written into; normally a ``tmp_path``.
            It is created when it does not exist, so a caller can name a fresh path.

    Returns:
        The loaded database, with one source, one electricity price entry for
        :attr:`SyntheticPlan.COUNTRY` and no device entries at all.
    """
    os.makedirs(directory, exist_ok=True)
    sources = {
        "sources": [
            {
                "id": "src_staged_test",
                "citation": "Synthetic staged-evaluator test data",
                "publication_year": SyntheticPlan.YEAR,
                "retrieved": "2026-09-19",
                "kind": "EXPERT_ESTIMATE",
                "notes": "Invented for tests/economics/test_staged.py; never a research input.",
            }
        ]
    }
    prices = {
        "entries": [
            {
                "carrier": "ELECTRICITY",
                "year": SyntheticPlan.YEAR,
                "working_price_in_euro_per_kwh": SyntheticPlan.ELECTRICITY_PRICE_IN_EURO_PER_KWH,
                "standing_charge_in_euro_per_year": 0.0,
                "emission_factor_in_kg_per_kwh": 0.3,
                "co2_price_exposure": 0.0,
                "tax_and_levy_share": 0.0,
                "quantity_unit": "kWh",
                "source_ids": ["src_staged_test"],
                "notes": "The only price the synthetic database carries.",
            }
        ]
    }
    with open(f"{directory}/sources.json", "w", encoding="utf-8") as handle:
        json.dump(sources, handle)
    with open(f"{directory}/energy_prices_{SyntheticPlan.COUNTRY}.json", "w", encoding="utf-8") as handle:
        json.dump(prices, handle)
    return CostDatabase(directory)


def device_facts(
    asset_class: ComponentType, size: float, investment_in_euro: float
) -> ComponentCostFacts:
    """One fully overridden set of cost facts, so no database figure reaches the result.

    Args:
        asset_class: The cost-database key the subject would be priced under, if anything were
            looked up. Nothing is: every monetary field is overridden here.
        size: The subject's capacity, in kilowatts for the generators and square metres for the
            envelope measure.
        investment_in_euro: The purchase price the override states.

    Returns:
        The facts, with the override source recorded as strict mode requires.
    """
    return ComponentCostFacts(
        asset_class=asset_class,
        size=size,
        size_unit=Units.SQUARE_METER if asset_class == ComponentType.WALL_EXTERNAL_INSULATION else Units.KILOWATT,
        investment_cost_override_in_euro=UncertainValue.exact(investment_in_euro),
        installation_cost_override_in_euro=UncertainValue.exact(0.0),
        lifetime_override_in_years=SyntheticPlan.LIFETIME_IN_YEARS,
        maintenance_rate_override=UncertainValue.exact(SyntheticPlan.MAINTENANCE_RATE),
        fixed_operation_cost_override_in_euro_per_year=UncertainValue.exact(0.0),
        embodied_co2_override_in_kg=0.0,
        override_source="synthetic staged-evaluator test",
    )


def inventory_register() -> ExistingAssetRegister:
    """The house's starting point: one ageing gas boiler that a heat pump would replace.

    The register is what switches the engine into its brownfield accounting, and its
    ``replaced_by_asset_classes`` is what tells the engine that a heat pump supersedes the boiler
    rather than joining it. Both are the state of the world before any stage.
    """
    return ExistingAssetRegister(
        assets=[
            ExistingAsset(
                asset_class=ComponentType.GAS_HEATER,
                size=15.0,
                size_unit=Units.KILOWATT,
                installation_year=SyntheticPlan.INVENTORY_INSTALLATION_YEAR,
                is_functional=True,
                energy_carrier=EnergyCarrier.NATURAL_GAS,
                replaced_by_asset_classes=[ComponentType.HEAT_PUMP],
            )
        ]
    )


def state_inputs(
    subjects: List[Tuple[str, ComponentType, float, float]],
    electricity_in_kwh: float,
    register: Optional[ExistingAssetRegister] = None,
) -> EvaluationInputs:
    """One simulated state as the evaluator sees it, with one electricity meter.

    Args:
        subjects: ``(subject, asset_class, size, investment)`` per cost subject of the state.
        electricity_in_kwh: What the state's meter bought over the simulated period, which is a
            full year here, so the annualization divisor is one.
        register: The existing-asset register the state was simulated with; the house inventory
            when omitted.

    Returns:
        The evaluation inputs.
    """
    return EvaluationInputs(
        simulation_year=SyntheticPlan.YEAR,
        simulated_period_fraction=1.0,
        cost_facts=[
            SubjectCostFacts(subject, device_facts(asset_class, size, investment))
            for subject, asset_class, size, investment in subjects
        ],
        billing=[
            BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY,
                energy_bought_in_kwh=electricity_in_kwh,
            )
        ],
        existing_assets=register if register is not None else inventory_register(),
        annual_heat_demand_in_kwh=11000.0,
    )


def baseline_stage() -> Stage:
    """Stage 0: the house as it is, one gas boiler and the untouched envelope."""
    return Stage(
        inputs=state_inputs(
            [
                (
                    SyntheticPlan.BOILER_SUBJECT,
                    ComponentType.GAS_HEATER,
                    15.0,
                    SyntheticPlan.BOILER_INVESTMENT_IN_EURO,
                )
            ],
            SyntheticPlan.BASELINE_ELECTRICITY_IN_KWH,
        ),
        from_year=0,
        label="baseline",
    )


def envelope_stage(from_year: int) -> Stage:
    """A stage that insulates the facade and keeps the boiler.

    Args:
        from_year: The year the insulation is built.

    Returns:
        The stage, whose cost facts are the boiler carried over plus the envelope measure.
    """
    return Stage(
        inputs=state_inputs(
            [
                (
                    SyntheticPlan.BOILER_SUBJECT,
                    ComponentType.GAS_HEATER,
                    15.0,
                    SyntheticPlan.BOILER_INVESTMENT_IN_EURO,
                ),
                (
                    SyntheticPlan.ENVELOPE_SUBJECT,
                    ComponentType.WALL_EXTERNAL_INSULATION,
                    120.0,
                    SyntheticPlan.ENVELOPE_INVESTMENT_IN_EURO,
                ),
            ],
            SyntheticPlan.RENOVATED_ELECTRICITY_IN_KWH,
        ),
        from_year=from_year,
        label="stage 1",
        measures=("external_insulation",),
        job_id="job-envelope",
    )


def heat_pump_stage(from_year: int) -> Stage:
    """A stage that replaces the boiler with a heat pump and keeps the insulation.

    Args:
        from_year: The year the heat pump is installed.

    Returns:
        The stage, whose cost facts are the envelope measure carried over plus the heat pump.
    """
    return Stage(
        inputs=state_inputs(
            [
                (
                    SyntheticPlan.ENVELOPE_SUBJECT,
                    ComponentType.WALL_EXTERNAL_INSULATION,
                    120.0,
                    SyntheticPlan.ENVELOPE_INVESTMENT_IN_EURO,
                ),
                (
                    SyntheticPlan.HEAT_PUMP_SUBJECT,
                    ComponentType.HEAT_PUMP,
                    9.0,
                    SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO,
                ),
            ],
            SyntheticPlan.RENOVATED_ELECTRICITY_IN_KWH,
        ),
        from_year=from_year,
        label="stage 2",
        measures=("heating_system",),
        job_id="job-heat-pump",
    )


def brownfield_perspective(subsidies: bool = False) -> Perspective:
    """The RenoVisor default frame: brownfield, system scope, cash, subsidies on or off.

    Args:
        subsidies: Whether the perspective admits subsidy schemes at all. The synthetic tests run
            without a catalogue, so the default is off and no support flow is generated.

    Returns:
        The perspective, with the id the RenoVisor default carries.
    """
    return Perspective(
        id="brownfield_net" if subsidies else "brownfield_gross",
        installation_context=InstallationContext.BROWNFIELD,
        subsidy_mode=SubsidyMode.full() if subsidies else SubsidyMode.none(),
    )


def entry_signature(timeline_entries) -> List[Tuple[int, str, str, float, float, float]]:
    """A comparable fingerprint of a timeline: one tuple per entry, in timeline order.

    Used by the "a plan of one stage equals a plain evaluation entry for entry" invariant, which
    is about the entries themselves and not about any aggregate of them.

    Args:
        timeline_entries: The entries of one :class:`~hisim.economics.timeline.CashFlowTimeline`.

    Returns:
        ``(year, category, subject, low, best, high)`` per entry.
    """
    return [
        (
            entry.year,
            entry.category.value,
            entry.subject,
            entry.amount_in_euro.minimum,
            entry.amount_in_euro.best_estimate,
            entry.amount_in_euro.maximum,
        )
        for entry in timeline_entries
    ]


def flows_by_year(timeline_entries) -> Dict[int, float]:
    """Nominal best-estimate cash flow per year, summed over every entry of that year.

    The "moving a stage later changes no flow before it" invariant compares these maps, because
    the statement is about money per year rather than about which entry carried it.
    """
    totals: Dict[int, float] = {}
    for entry in timeline_entries:
        totals[entry.year] = totals.get(entry.year, 0.0) + entry.amount_in_euro.best_estimate
    return totals
