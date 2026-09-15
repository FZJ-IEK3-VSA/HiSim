"""Tests of resolving the sizing laws the measure layer leaves pending.

What is pinned here is the arithmetic and the sentence that explains it, because both reach the
caller: the number sizes a battery and the sentence is what the translation report shows instead
of pretending the number was measured. The expensive half -- loading an occupancy profile -- is
deliberately not exercised; the end-to-end test does that, and everything here runs offline.
"""

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from hisim.renovisor.base_files import BaseFileKey
from hisim.renovisor.effects import LawRequest, SizingLaw
from hisim.renovisor.inventory import Inventory
from hisim.renovisor.laws import (
    HeatPumpTerm,
    LawResolver,
    PreRunDemandEstimator,
    StaticDemandEstimator,
    TabulaHeatingDemand,
)
from hisim.renovisor.occupancy import HouseholdMatcher
from hisim.renovisor.vocabulary import HeatGenerator
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base

EXAMPLE_INVENTORY_PATH = Path(__file__).resolve().parent / "renovisor" / "example_inventory_ie_1988_detached.json"


@pytest.fixture(name="inventory")
def fixture_inventory() -> Inventory:
    """The example Irish 1988 detached house: gas boiler, 157 m2, two working adults."""
    return Inventory.from_dict(json.loads(EXAMPLE_INVENTORY_PATH.read_text(encoding="utf-8")))


@pytest.fixture(name="parameters")
def fixture_parameters() -> SimulationParameters:
    """One simulated year, so the estimator's daily figures are annual ones over 365."""
    return SimulationParameters.full_year(year=2021, seconds_per_timestep=3600)


def test_the_battery_law_multiplies_the_days_by_the_three_demands() -> None:
    """Decision Q12: capacity is days times household plus heat pump plus vehicle."""
    estimator = StaticDemandEstimator(8.4, 4.2, 1.5)

    result = LawResolver.resolve(
        LawRequest(law=SizingLaw.BATTERY_FROM_DAYS_TO_COVER, argument=2.0), Inventory.from_dict({}), estimator
    )

    assert result.value == pytest.approx(2.0 * (8.4 + 4.2 + 1.5))


def test_the_rule_names_all_three_inputs_and_their_values() -> None:
    """Requirement R7: the report shows the arithmetic, so the rule carries it rather than a label."""
    result = LawResolver.battery_from_days_to_cover(2.0, StaticDemandEstimator(8.4, 4.2, 1.5))

    assert SizingLaw.BATTERY_FROM_DAYS_TO_COVER.value in result.rule
    assert "8.4 household" in result.rule
    assert "4.2 heat pump" in result.rule
    assert "1.5 vehicle" in result.rule
    assert "2 day(s)" in result.rule


def test_the_heat_pump_term_is_zero_for_a_boiler() -> None:
    """A gas house draws no heating electricity, so the term is zero rather than merely small."""
    estimator = StaticDemandEstimator(8.4, 4.2, 0.0, generator=HeatGenerator.GAS_HEATING)

    result = LawResolver.battery_from_days_to_cover(2.0, estimator)

    assert estimator.heat_pump_electricity_in_kwh_per_day() == 0.0
    assert result.value == pytest.approx(2.0 * 8.4)
    assert "0 heat pump" in result.rule


@pytest.mark.parametrize(
    "generator,expected",
    [
        (HeatGenerator.HEAT_PUMP, True),
        (HeatGenerator.ELECTRIC_HEATING, True),
        (HeatGenerator.HYBRID_HEAT_PUMP, True),
        (HeatGenerator.GAS_HEATING, False),
        (HeatGenerator.DISTRICT_HEATING, False),
        (HeatGenerator.PELLET_HEATING, False),
    ],
)
def test_only_the_electric_generators_contribute_a_heating_term(
    generator: HeatGenerator, expected: bool
) -> None:
    """The rule lives in one place, so both estimators agree about which generators are electric."""
    assert HeatPumpTerm.applies_to(generator) is expected


def test_a_law_without_a_resolver_is_a_programming_error() -> None:
    """Decision Q18 keeps the photovoltaic sizing in the base file's own law, so none reaches here."""
    with pytest.raises(NotImplementedError):
        LawResolver.resolve(
            LawRequest(law=SizingLaw.PV_SHARE_OF_ROOF, argument=0.4),
            Inventory.from_dict({}),
            StaticDemandEstimator(8.4, 4.2, 0.0),
        )


def test_the_tabula_heating_need_is_read_from_the_processed_table() -> None:
    """The estimate has to come from the same archetype the Building component will be built from."""
    assert TabulaHeatingDemand.for_code("IE.N.SFH.06.Gen.ReEx.001.001") == pytest.approx(192.5)
    assert TabulaHeatingDemand.for_code("IE.N.SFH.07.Gen.ReEx.001.001") == pytest.approx(174.8)


def test_an_unknown_building_code_has_no_heating_need() -> None:
    """A code the table does not have is a lookup failure rather than a zero demand."""
    with pytest.raises(KeyError):
        TabulaHeatingDemand.for_code("ZZ.N.SFH.99.Gen.ReEx.001.001")


def estimator_for(
    inventory: Inventory, parameters: SimulationParameters, generator: HeatGenerator
) -> PreRunDemandEstimator:
    """Return a pre-run estimator for one dwelling and one generator.

    Args:
        inventory: The dwelling.
        parameters: The run's parameters.
        generator: What heats the dwelling after every measure.

    Returns:
        The estimator; nothing is computed until one of its three methods is called, which is what
        lets these tests ask for the heat-pump and vehicle terms without loading a profile.
    """
    return PreRunDemandEstimator(
        inventory=inventory,
        base_file_key=BaseFileKey(generator=generator, solar_thermal=False, cars=0),
        household_match=HouseholdMatcher().match(inventory),
        simulation_parameters=parameters,
    )


def test_the_pre_run_heat_pump_term_is_the_tabula_need_over_the_performance_factor(
    inventory: Inventory, parameters: SimulationParameters
) -> None:
    """The one invented number in the layer is the seasonal performance factor, and it is visible."""
    estimator = estimator_for(inventory, parameters, HeatGenerator.HEAT_PUMP)

    value = estimator.heat_pump_electricity_in_kwh_per_day()

    need = TabulaHeatingDemand.for_code("IE.N.SFH.07.Gen.ReEx.001.001")
    expected = need * 157 / PreRunDemandEstimator.SEASONAL_PERFORMANCE_FACTOR / 365
    assert value == pytest.approx(expected)
    assert "PROVISIONAL seasonal performance factor" in estimator.notes[0]


def test_the_pre_run_heat_pump_term_is_zero_for_the_dwellings_own_gas_boiler(
    inventory: Inventory, parameters: SimulationParameters
) -> None:
    """No TABULA row is even read when the dwelling does not heat electrically."""
    estimator = estimator_for(inventory, parameters, HeatGenerator.GAS_HEATING)

    assert estimator.heat_pump_electricity_in_kwh_per_day() == 0.0
    assert "GAS_HEATING" in estimator.notes[0]


def test_the_vehicle_term_is_zero_while_the_contract_carries_no_mileage(
    inventory: Inventory, parameters: SimulationParameters
) -> None:
    """Pending path A5: the term is zero and the note says why, rather than silently contributing."""
    estimator = estimator_for(inventory, parameters, HeatGenerator.HEAT_PUMP)

    assert estimator.vehicle_electricity_in_kwh_per_day() == 0.0
    assert "charges no electric vehicle" in estimator.notes[0]


def test_a_vehicle_without_a_mileage_says_which_contract_path_is_missing(
    parameters: SimulationParameters
) -> None:
    """A dwelling that does charge a car still contributes nothing, and names the missing field."""
    document: Dict[str, Any] = {
        "occupancy_config": {"residents_count": 2, "residents_type": ["adult", "adult"],
                             "residents_employment_status": ["full_time", "full_time"]},
        "energy_system_config": {
            "vehicles": {"electric_vehicles": [{"model": "EV_1", "consumption_in_kwh_per_km": 0.15}]}
        },
    }
    inventory = Inventory.from_dict(document)
    estimator = estimator_for(inventory, parameters, HeatGenerator.HEAT_PUMP)

    assert estimator.vehicle_electricity_in_kwh_per_day() == 0.0
    assert PreRunDemandEstimator.VEHICLE_MILEAGE_PATH in estimator.notes[0]
