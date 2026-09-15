"""Tests for the KPI preparation arithmetic that no setup run pins down.

The KPI preparation normally exists only inside a finished post-processing run, so its edge
cases — above all the degenerate energy balances a setup can legitimately produce — are exactly
the branches an end-to-end test never steers into. The tests here build the preparation object
around its two load-bearing attributes and drive the computation directly, which is what lets a
branch like "production without any consumption" be exercised in milliseconds.

Each test states the failure mode it catches.
"""

# clean

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd
import pytest
from dataclasses_json import dataclass_json

from hisim.component import CapexCostDataClass, Component, OpexCostDataClass
from hisim.component_wrapper import ComponentWrapper
from hisim.config import ComponentID, ConfigBase, DisplayConfig
from hisim.loadtypes import ComponentType, LoadTypes, Units
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions
from hisim.postprocessing.cost_and_emission_computation.opex_and_capex_cost_calculation import opex_calculation
from hisim.postprocessing.kpi_computation.kpi_preparation import KpiPreparation
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass
from hisim.simulationparameters import SimulationParameters


def _bare_preparation(building: str) -> KpiPreparation:
    """Builds a KPI preparation around its two load-bearing attributes, skipping the heavy init.

    ``KpiPreparation.__init__`` consumes a whole post-processing data transfer and immediately
    computes every component's KPIs, which needs a finished simulation. The computation under
    test reads only the simulation parameters and the collection dict, so the object is created
    without the constructor and given exactly those two.

    Args:
        building: The building label the computed entries are collected under.

    Returns:
        The preparation object, ready for direct method calls.
    """
    preparation = KpiPreparation.__new__(KpiPreparation)
    preparation.simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    preparation.kpi_collection_dict_unsorted = {building: {}}
    return preparation


@pytest.mark.base
def test_production_without_consumption_reports_zero_self_sufficiency() -> None:
    """Catches the zero-consumption energy balance crashing or misreporting the rate (T: #641).

    A system can produce without consuming anything the meters see — a bare generation chain
    feeding a converter, like the electrolyzer setup — and the self-sufficiency rate used to be a
    division by that zero. The guarded branch reports the rate as zero, matching the
    no-production branch, and the whole computation must finish so the other three entries are
    still written.
    """
    preparation = _bare_preparation("BUI1")
    frame = pd.DataFrame(
        {
            "total_production": [1000.0, 1000.0],
            "total_consumption": [0.0, 0.0],
            "battery_charge": [0.0, 0.0],
            "battery_discharge": [0.0, 0.0],
        }
    )

    preparation.compute_self_consumption_injection_self_sufficiency(
        result_dataframe=frame,
        electricity_production_in_kilowatt_hour=0.5,
        electricity_consumption_in_kilowatt_hour=0.0,
        building_objects_in_district="BUI1",
        kpi_tag=KpiTagEnumClass.GENERAL,
    )

    collected = preparation.kpi_collection_dict_unsorted["BUI1"]
    assert collected["Self-sufficiency rate of electricity"]["value"] == 0
    assert "Grid injection of electricity" in collected
    assert "Self-consumption of electricity" in collected
    assert "Self-consumption rate of electricity" in collected


@pytest.mark.base
def test_two_components_of_one_class_keep_both_their_kpi_entries() -> None:
    """Catches a second same-class instance silently overwriting the first one's KPIs.

    The collection is keyed by KPI name, and two components of one class report the same names —
    the two CHPs of the `dynamic_components` setup both report "Electrical energy produced" — so
    keying by name alone made one of the two CHPs vanish from the report, the webtool JSON and
    the golden comparison without anything failing. Where names collide, each entry keys as
    "<name> (<source component>)"; a name only one component emits stays unqualified, so a
    single-CHP setup renames nothing.
    """
    entries = [
        KpiEntry(name="Electrical energy produced", unit="kWh", value=10.0, name_of_source_component="CHP1"),
        KpiEntry(name="Electrical energy produced", unit="kWh", value=90.0, name_of_source_component="CHP2"),
        KpiEntry(
            name="Total electricity consumption", unit="kWh", value=3.0, name_of_source_component="ElectricityMeter"
        ),
    ]

    keyed = KpiPreparation.keyed_component_entries(entries)

    assert keyed["Electrical energy produced (CHP1)"]["value"] == 10.0
    assert keyed["Electrical energy produced (CHP2)"]["value"] == 90.0
    assert keyed["Total electricity consumption"]["value"] == 3.0
    assert "Electrical energy produced" not in keyed


@pytest.mark.base
def test_two_components_of_different_classes_keep_both_their_kpi_entries() -> None:
    """Catches a shared KPI name across component classes dropping one of the two components.

    The keying looks at names and sources only, never at classes, so a name two *different*
    classes emit collides exactly like a name two instances of one class emit: a gas boiler and
    a solar thermal system both report "Total thermal energy delivered", and keying by name alone
    left only whichever ran last. Both sides have to survive, qualified, while each class's own
    unshared names stay unqualified.
    """
    entries = [
        KpiEntry(
            name="Total thermal energy delivered",
            unit="kWh",
            value=635.5,
            name_of_source_component="CondensingGasBoiler",
        ),
        KpiEntry(
            name="Total thermal energy delivered",
            unit="kWh",
            value=0.0,
            name_of_source_component="SolarThermalSystem",
        ),
        KpiEntry(
            name="Thermal energy delivered for space heating",
            unit="kWh",
            value=542.0,
            name_of_source_component="CondensingGasBoiler",
        ),
    ]

    keyed = KpiPreparation.keyed_component_entries(entries)

    assert keyed["Total thermal energy delivered (CondensingGasBoiler)"]["value"] == 635.5
    assert keyed["Total thermal energy delivered (SolarThermalSystem)"]["value"] == 0.0
    assert keyed["Thermal energy delivered for space heating"]["value"] == 542.0
    assert "Total thermal energy delivered" not in keyed


@pytest.mark.base
def test_a_qualified_fuel_meter_entry_is_still_read_into_the_general_costs() -> None:
    """Catches qualification zeroing the general fuel costs and emissions KPIs.

    The general cost and emission KPIs read the meters' entries out of the collection, and doing
    that by collection key broke the moment a second component of the building shared a meter's
    KPI name and the meter's own key became qualified: the lookup missed, the fuel costs silently
    became 0, and the building reported no heating fuel at all. The lookup matches the entry's
    own name, so a qualified key must still be found.
    """
    building = "BUI1"
    preparation = _bare_preparation(building)
    preparation.kpi_collection_dict_unsorted[building] = {
        "OPEX - Energy costs (FuelMeter)": KpiEntry(
            name="OPEX - Energy costs",
            unit="EUR",
            value=50.14,
            tag=KpiTagEnumClass.FUEL_METER,
            name_of_source_component="FuelMeter",
        ).to_dict(),
        "OPEX - CO2 Footprint (FuelMeter)": KpiEntry(
            name="OPEX - CO2 Footprint",
            unit="kg",
            value=169.63,
            tag=KpiTagEnumClass.FUEL_METER,
            name_of_source_component="FuelMeter",
        ).to_dict(),
        "Total energy consumption (FuelMeter)": KpiEntry(
            name="Total energy consumption",
            unit="kWh",
            value=605.83,
            tag=KpiTagEnumClass.FUEL_METER,
            name_of_source_component="FuelMeter",
        ).to_dict(),
        "Self-sufficiency rate according to solar htw berlin": KpiEntry(
            name="Self-sufficiency rate according to solar htw berlin", unit="%", value=50.0
        ).to_dict(),
        "Total electricity consumption": KpiEntry(
            name="Total electricity consumption", unit="kWh", value=100.0
        ).to_dict(),
    }

    preparation.read_opex_and_capex_costs_from_results(building_object=building)

    collected = preparation.kpi_collection_dict_unsorted[building]
    assert collected["Costs of other heating fuels for simulated period"]["value"] == 50.14
    assert collected["CO2 footprint of other heating fuels for simulated period"]["value"] == 169.63


@pytest.mark.base
def test_two_fuel_meters_of_one_building_both_reach_the_general_costs() -> None:
    """Catches a building's second meter being dropped from the general cost and emission KPIs.

    A building can run two meters of one tag — an oil and a pellet meter heating it together —
    and since each of them now keeps its own entry under a source-qualified key, both entries
    reach the cost reader. Assigning each value made the last meter read stand for all of them,
    so half the building's heating fuel bill and emissions silently disappeared. The general KPIs
    are the sum over the meters, not the value of whichever one happened to come last.
    """
    building = "BUI1"
    preparation = _bare_preparation(building)
    meter_entries = {
        f"{name} ({source})": KpiEntry(
            name=name, unit=unit, value=value, tag=KpiTagEnumClass.FUEL_METER, name_of_source_component=source
        ).to_dict()
        for source, costs_in_euro, co2_in_kg, energy_in_kwh in (
            ("OilMeter", 50.14, 169.63, 605.83),
            ("PelletMeter", 20.0, 30.0, 100.0),
        )
        for name, unit, value in (
            ("OPEX - Energy costs", "EUR", costs_in_euro),
            ("OPEX - CO2 Footprint", "kg", co2_in_kg),
            ("Total energy consumption", "kWh", energy_in_kwh),
        )
    }
    preparation.kpi_collection_dict_unsorted[building] = {
        **meter_entries,
        "Self-sufficiency rate according to solar htw berlin": KpiEntry(
            name="Self-sufficiency rate according to solar htw berlin", unit="%", value=50.0
        ).to_dict(),
        "Total electricity consumption": KpiEntry(
            name="Total electricity consumption", unit="kWh", value=100.0
        ).to_dict(),
    }

    preparation.read_opex_and_capex_costs_from_results(building_object=building)

    collected = preparation.kpi_collection_dict_unsorted[building]
    assert collected["Costs of other heating fuels for simulated period"]["value"] == pytest.approx(70.14)
    assert collected["CO2 footprint of other heating fuels for simulated period"]["value"] == pytest.approx(199.63)


@pytest.mark.base
def test_a_colliding_kpi_entry_without_a_source_component_is_refused() -> None:
    """Catches the disambiguation silently producing an anonymous key.

    When names collide, the source component is the only thing left to tell the entries apart
    by; an entry without one would either overwrite its sibling again or key as "<name> (None)",
    both of which hide the defect the keying exists to surface. Refusing names the KPI so the
    component author knows what to fix.
    """
    entries = [
        KpiEntry(name="Electrical energy produced", unit="kWh", value=10.0, name_of_source_component="CHP1"),
        KpiEntry(name="Electrical energy produced", unit="kWh", value=90.0, name_of_source_component=None),
    ]

    with pytest.raises(ValueError, match="name_of_source_component"):
        KpiPreparation.keyed_component_entries(entries)


@pytest.mark.base
def test_one_component_emitting_the_same_kpi_name_twice_is_refused() -> None:
    """Catches the one collision the source component cannot resolve.

    Two entries of one component with one name key identically even after qualification, so a
    consumer would still silently read only the last one. That is a defect in the component's
    KPI method, and it has to fail loudly there rather than pass as a plausible-looking value.
    """
    entries = [
        KpiEntry(name="Electrical energy produced", unit="kWh", value=10.0, name_of_source_component="CHP1"),
        KpiEntry(name="Electrical energy produced", unit="kWh", value=90.0, name_of_source_component="CHP1"),
    ]

    with pytest.raises(ValueError, match="same KPI name twice"):
        KpiPreparation.keyed_component_entries(entries)


class _CarReportingOneKpi(Component):
    """A minimal component whose KPI method reports one entry and names no source component.

    It stands for the forty-odd components that build their KPI entries by hand: none of them
    fills in ``name_of_source_component``, so a component like this one is what the base class's
    stamping has to work on. Two instances of it are two cars of one household.
    """

    def __init__(self, name: str, my_simulation_parameters: SimulationParameters) -> None:
        """Builds the component under the given runtime name, with a bare identity and config."""
        super().__init__(
            name=name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=ConfigBase(component_id=ComponentID(name)),
            my_display_config=DisplayConfig(),
        )

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: object) -> List[KpiEntry]:
        """Reports the one KPI both instances report, without saying which instance reported it."""
        return [KpiEntry(name="Distance driven", unit="km", value=42.0, tag=KpiTagEnumClass.CAR)]


@pytest.mark.base
def test_two_instances_of_one_component_class_survive_the_whole_collection() -> None:
    """Catches the stamping and the keying being right apart but not together.

    The unit tests above key entries that already name their source, and the component library
    never fills that field in, so nothing yet proves that a real collection run keeps two cars of
    one household apart: it is the base class that stamps each entry with the component that
    produced it, and the keying that turns the stamps into distinct keys. Driving the collector
    over two wrapped instances is what shows the two halves meeting — each entry keeps its own
    key and carries the name of the instance that reported it.
    """
    simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    preparation = _bare_preparation("BUI1")
    # get_all_component_kpis reads these three beyond the two attributes the helper provides; the
    # components below answer from their own state, so the outputs and results stay empty.
    preparation.building_objects_in_district_list = ["BUI1"]
    preparation.all_outputs = []
    preparation.results = pd.DataFrame()
    wrapped = [
        ComponentWrapper(
            component=_CarReportingOneKpi(name=name, my_simulation_parameters=simulation_parameters),
            is_cachable=False,
            connect_automatically=False,
        )
        for name in ("Car1", "Car2")
    ]

    preparation.get_all_component_kpis(wrapped_components=wrapped)

    collected = preparation.kpi_collection_dict_unsorted["BUI1"]
    assert set(collected) == {"Distance driven (Car1)", "Distance driven (Car2)"}
    assert collected["Distance driven (Car1)"]["nameOfSourceComponent"] == "Car1"
    assert collected["Distance driven (Car2)"]["nameOfSourceComponent"] == "Car2"


@dataclass_json
@dataclass
class _MaintainedDeviceConfig(ConfigBase):
    """The config of the maintained device below: the five capex fields and nothing else."""

    @classmethod
    def get_main_classname(cls):
        """Return the name used in place of a real component class name."""
        return "tests.test_kpi_preparation.MaintainedDevice"

    component_id: ComponentID
    device_co2_footprint_in_kg: Optional[float]
    investment_costs_in_euro: Optional[float]
    lifetime_in_years: Optional[float]
    maintenance_costs_in_euro_per_year: Optional[float]
    subsidy_as_percentage_of_investment_costs: Optional[float]


class _MaintainedDevice(Component):
    """A component that costs 100 EUR of maintenance a year and reports it the ordinary way.

    Its CAPEX goes through the shared helper and its OPEX maintenance through
    :meth:`Component.calc_maintenance_cost`, so the figure that ends up in the opex table is the
    one the proration rule produces -- not a number the test wrote there itself.
    """

    def __init__(self, name: str, my_simulation_parameters: SimulationParameters) -> None:
        """Builds the device with a 1000 EUR investment, a 10-year life, and 100 EUR/a upkeep."""
        super().__init__(
            name=name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=_MaintainedDeviceConfig(
                component_id=ComponentID(name),
                device_co2_footprint_in_kg=200.0,
                investment_costs_in_euro=1000.0,
                lifetime_in_years=10.0,
                maintenance_costs_in_euro_per_year=100.0,
                subsidy_as_percentage_of_investment_costs=0.0,
            ),
            my_display_config=DisplayConfig(),
        )

    @staticmethod
    def get_cost_capex(
        config: _MaintainedDeviceConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:
        """Cost the device through the central helper, config branch."""
        return CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
            simulation_parameters=simulation_parameters,
            component_type=ComponentType.HEAT_PUMP,
            unit=Units.KILOWATT,
            size_of_energy_system=1.0,
            config=config,
        )

    def get_cost_opex(self, all_outputs: List, postprocessing_results: pd.DataFrame) -> OpexCostDataClass:
        """Report no energy at all, and the maintenance the capital cost data carries."""
        return OpexCostDataClass(
            opex_energy_cost_in_euro=0.0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0.0,
            total_consumption_in_kwh=0.0,
            loadtype=LoadTypes.ANY,
            # A tag is required rather than cosmetic: the opex writer drops any component whose
            # cost data carries a None field, and an untagged entry is exactly that.
            kpi_tag=KpiTagEnumClass.GENERAL,
        )


@pytest.mark.base
def test_the_building_maintenance_kpi_carries_the_prorated_annual_rate(tmp_path) -> None:
    """Catches the corrected maintenance not surviving the trip from the device to the KPI.

    Between the proration rule and the building-level KPI sit two boundaries the unit tests of
    either side cannot see: the opex writer puts the per-period maintenance into a named CSV
    column, and the KPI preparation reads that column back out of the "Total" row by that same
    name. A rename on one side alone, or a reader looking for the wrong column, would silently
    report zero maintenance for the whole building. Driving the writer and the reader in one
    test is what pins the two names to each other.

    One simulated day of a device with a 100 EUR/a rate is 100 EUR/a * (1/365) a = 0.27 EUR
    after the writer's rounding. The old rule, which divided by the ten-year lifetime too, would
    have put 0.03 EUR here, so the assertion tells the two rules apart.
    """
    simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    simulation_parameters.result_directory = str(tmp_path)
    device = _MaintainedDevice(name="MaintainedDevice", my_simulation_parameters=simulation_parameters)
    expected_maintenance_in_euro = round(device.calc_maintenance_cost(), 2)

    opex_calculation(
        components=[ComponentWrapper(component=device, is_cachable=False, connect_automatically=False)],
        all_outputs=[],
        postprocessing_results=pd.DataFrame(),
        simulation_parameters=simulation_parameters,
        building_objects_in_district_list=["BUI1"],
    )

    preparation = _bare_preparation("BUI1")
    preparation.simulation_parameters = simulation_parameters
    preparation.kpi_collection_dict_unsorted["BUI1"] = {
        "Self-sufficiency rate according to solar htw berlin": KpiEntry(
            name="Self-sufficiency rate according to solar htw berlin", unit="%", value=50.0
        ).to_dict(),
        "Total electricity consumption": KpiEntry(
            name="Total electricity consumption", unit="kWh", value=100.0
        ).to_dict(),
    }

    preparation.read_opex_and_capex_costs_from_results(building_object="BUI1")

    collected = preparation.kpi_collection_dict_unsorted["BUI1"]
    assert expected_maintenance_in_euro == pytest.approx(0.27)
    assert collected["Maintenance costs for simulated period"]["value"] == pytest.approx(
        expected_maintenance_in_euro
    )
