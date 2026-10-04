"""Tests for the KPI preparation arithmetic that no setup run pins down.

The KPI preparation normally exists only inside a finished post-processing run, so its edge
cases — above all the degenerate energy balances a setup can legitimately produce — are exactly
the branches an end-to-end test never steers into. The tests here build the preparation object
around its two load-bearing attributes and drive the computation directly, which is what lets a
branch like "production without any consumption" be exercised in milliseconds.

Each test states the failure mode it catches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

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
from hisim.postprocessing.kpi_computation.kpi_address import KpiAddress
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiSource, KpiTagEnumClass
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


def _source(component_name: str) -> KpiSource:
    """The source a plain component of that name stamps on its KPI entries."""
    return KpiSource.for_component(ComponentID(component_name), DisplayConfig())


def _component_entry(
    name: str, value: float, component_name: str, *, tag: KpiTagEnumClass, unit: str = "kWh"
) -> KpiEntry:
    """A component KPI entry as ``Component.component_kpi_entries`` returns it: with its source.

    Args:
        name: The KPI's name.
        value: Its value.
        component_name: The plain name of the component reporting it.
        tag: Its tag; every KPI entry has one.
        unit: Its unit.
    """
    return KpiEntry(
        name=name,
        unit=unit,
        value=value,
        tag=tag,
        source=_source(component_name),
        name_of_source_component=component_name,
    )


def _keyed(entries: List[KpiEntry]) -> Dict[str, Dict]:
    """The entries as one building's collection holds them, keyed by their address."""
    return {KpiAddress.key_for(entry.name, entry.source): entry.to_dict() for entry in entries}


@pytest.mark.base
def test_a_single_component_is_qualified_too_so_no_neighbour_renames_its_keys() -> None:
    """Catches a key that depends on who else lives in the building (``kpi_address_spec.md``).

    The key used to be the bare name while one component emitted it and became qualified as soon
    as a second one did, so adding a second car renamed the first car's "Distance driven" for
    every consumer. The key is now a property of the KPI: a lone component is qualified too, and
    adding a neighbour adds a key and renames none.
    """
    alone = KpiPreparation.keyed_component_entries(
        [_component_entry("Distance driven", 42.0, "Car1", unit="km", tag=KpiTagEnumClass.CAR)]
    )
    with_a_neighbour = KpiPreparation.keyed_component_entries(
        [
            _component_entry("Distance driven", 42.0, "Car1", unit="km", tag=KpiTagEnumClass.CAR),
            _component_entry("Distance driven", 7.0, "Car2", unit="km", tag=KpiTagEnumClass.CAR),
        ]
    )

    assert list(alone) == ["Distance driven (Car1)"]
    assert set(with_a_neighbour) - set(alone) == {"Distance driven (Car2)"}
    assert with_a_neighbour["Distance driven (Car1)"] == alone["Distance driven (Car1)"]


@pytest.mark.base
def test_a_derived_kpi_keeps_its_bare_name_and_carries_no_source() -> None:
    """Catches a derived KPI (no component behind it) being qualified or given a source.

    Derived KPIs -- the General tag, the meter-derived cost totals -- are singletons per building
    and keep their bare name; their entry says ``"source": null``.
    """
    derived = KpiEntry(name="Total electricity consumption", unit="kWh", value=3.0, tag=KpiTagEnumClass.GENERAL)

    assert KpiAddress.key_for(derived.name, derived.source) == "Total electricity consumption"
    assert derived.to_dict()["source"] is None
    assert derived.to_dict()["nameOfSourceComponent"] is None


@pytest.mark.base
def test_two_components_of_different_classes_keep_both_their_kpi_entries() -> None:
    """Catches a shared KPI name across components dropping one of the two.

    The keying looks at names and sources only, never at classes, so this covers two instances
    of one class as well -- the two CHPs of the ``dynamic_components`` setup both report
    "Electrical energy produced": a gas boiler and a solar thermal system both report "Total
    thermal energy delivered", and both keep their entry under their own source.
    """
    entries = [
        _component_entry(
            "Total thermal energy delivered", 635.5, "CondensingGasBoiler", tag=KpiTagEnumClass.GAS_BOILER
        ),
        _component_entry(
            "Total thermal energy delivered", 0.0, "SolarThermalSystem", tag=KpiTagEnumClass.SOLAR_THERMAL
        ),
        _component_entry(
            "Thermal energy delivered for space heating", 542.0, "CondensingGasBoiler", tag=KpiTagEnumClass.GAS_BOILER
        ),
    ]

    keyed = KpiPreparation.keyed_component_entries(entries)

    assert keyed["Total thermal energy delivered (CondensingGasBoiler)"]["value"] == 635.5
    assert keyed["Total thermal energy delivered (SolarThermalSystem)"]["value"] == 0.0
    assert keyed["Thermal energy delivered for space heating (CondensingGasBoiler)"]["value"] == 542.0
    assert keyed["Total thermal energy delivered (SolarThermalSystem)"]["source"]["name"] == "SolarThermalSystem"
    assert "Total thermal energy delivered" not in keyed


@pytest.mark.base
def test_a_qualified_fuel_meter_entry_is_still_read_into_the_general_costs() -> None:
    """Catches qualification zeroing the general fuel costs and emissions KPIs.

    The general cost and emission KPIs read the meters' entries out of the collection, and doing
    that by collection key would miss every meter, since a component KPI's key carries its
    source: the fuel costs would silently become 0. The lookup matches the entry's own name.
    """
    building = "BUI1"
    preparation = _bare_preparation(building)
    preparation.kpi_collection_dict_unsorted[building] = {
        **_keyed(
            [
                _component_entry(name, value, "FuelMeter", unit=unit, tag=KpiTagEnumClass.FUEL_METER)
                for name, unit, value in (
                    ("OPEX - Energy costs", "EUR", 50.14),
                    ("OPEX - CO2 Footprint", "kg", 169.63),
                    ("Total energy consumption", "kWh", 605.83),
                )
            ]
        ),
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

    A building can run two meters of one tag -- an oil and a pellet meter heating it together --
    and each keeps its own entry under its source-qualified key, so both reach the cost reader.
    Assigning each value made the last meter read stand for all of them; the general KPIs are
    the sum over the meters.
    """
    building = "BUI1"
    preparation = _bare_preparation(building)
    meter_entries = _keyed(
        [
            _component_entry(name, value, source, unit=unit, tag=KpiTagEnumClass.FUEL_METER)
            for source, costs_in_euro, co2_in_kg, energy_in_kwh in (
                ("OilMeter", 50.14, 169.63, 605.83),
                ("PelletMeter", 20.0, 30.0, 100.0),
            )
            for name, unit, value in (
                ("OPEX - Energy costs", "EUR", costs_in_euro),
                ("OPEX - CO2 Footprint", "kg", co2_in_kg),
                ("Total energy consumption", "kWh", energy_in_kwh),
            )
        ]
    )
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
def test_a_component_kpi_entry_without_a_source_is_refused() -> None:
    """Catches the keying silently producing an anonymous or bare key for a component KPI.

    Every component entry is keyed by its source; an entry without one would key as its bare
    name, which is the shape of a derived KPI, and hide the defect. Refusing names the KPI so the
    component author knows what to fix.
    """
    entries = [KpiEntry(name="Electrical energy produced", unit="kWh", value=90.0)]

    with pytest.raises(ValueError, match="'Electrical energy produced' carries no source"):
        KpiPreparation.keyed_component_entries(entries)


@pytest.mark.base
def test_an_entry_whose_two_source_fields_disagree_is_refused() -> None:
    """Catches the deprecated ``name_of_source_component`` drifting from ``source.name``.

    Both are written for one release, and a reader of either must read the same component.
    """
    entry = _component_entry("Electrical energy produced", 10.0, "CHP1", tag=KpiTagEnumClass.CHP)
    entry.name_of_source_component = "CHP2"

    with pytest.raises(ValueError, match="must equal source.name"):
        KpiPreparation.keyed_component_entries([entry])


@pytest.mark.base
def test_one_source_name_with_two_addresses_is_refused() -> None:
    """Catches two entries naming one component by its name but with different addresses.

    A component has exactly one source; an entry reported on its behalf that built the source
    differently (another display name, say) would make one component two in every reader that
    filters on the source.
    """
    entries = [
        _component_entry("Electrical energy produced", 10.0, "CHP1", tag=KpiTagEnumClass.CHP),
        KpiEntry(
            name="Thermal energy produced",
            unit="kWh",
            value=5.0,
            tag=KpiTagEnumClass.CHP,
            source=KpiSource.for_component(ComponentID("CHP1"), DisplayConfig.show("Combined heat and power")),
            name_of_source_component="CHP1",
        ),
    ]

    with pytest.raises(ValueError, match="different addresses"):
        KpiPreparation.keyed_component_entries(entries)


@pytest.mark.base
def test_one_component_emitting_the_same_kpi_name_twice_is_refused() -> None:
    """Catches the one collision the source cannot resolve.

    Two entries of one component with one name key identically, so a consumer would silently
    read only the last one. That is a defect in the component's KPI method, and it has to fail
    loudly there rather than pass as a plausible-looking value.
    """
    entries = [
        _component_entry("Electrical energy produced", 10.0, "CHP1", tag=KpiTagEnumClass.CHP),
        _component_entry("Electrical energy produced", 90.0, "CHP1", tag=KpiTagEnumClass.CHP),
    ]

    with pytest.raises(ValueError, match="same KPI name twice"):
        KpiPreparation.keyed_component_entries(entries)


class _CarReportingOneKpi(Component):
    """A minimal component whose KPI method reports one entry and names no source component.

    It stands for the forty-odd components that build their KPI entries by hand: none of them
    fills in its ``source``, so a component like this one is what the base class's
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
    for car in ("Car1", "Car2"):
        entry = collected[KpiAddress.key_for("Distance driven", _source(car))]
        assert entry["nameOfSourceComponent"] == car
        assert entry["source"] == {
            "import": None,
            "instance": None,
            "path": [],
            "member": car,
            "assembly": None,
            "name": car,
            "display_name": car,
            "label": None,
        }


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
