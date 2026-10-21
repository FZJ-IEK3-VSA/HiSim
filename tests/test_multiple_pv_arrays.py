"""Several PV arrays in one energy system: each array generates alone, and everything downstream sees their sum.

The fixture ``tests/energy_systems/two_pv_arrays.energy_system.yaml`` is a household with a south array of 4 kWp
and a west array of 2 kWp, a battery sized to the arrays and the energy manager maximising self-consumption. No
component knows how many arrays there are: each array is an ordinary ``PVSystem`` entry with a name of its own, the
manager and the meter sum every production input they are given, the battery's laws sum the peak power of every
array (``Sum(Many(Size.PV_PEAK_POWER_IN_WATT))``) and the cost engine prices every array as a subject of its own.

The tests run the fixture, each array alone, the household metered directly (no manager, no battery) and the same
household composed from the assembly library, all over one June day at 15 minutes, and check that:

- each array's generation is the series that array produces alone, so a second array changes nothing about the
  first;
- the manager, the meter and the KPIs work on the summed production;
- the battery is sized to the sum of the arrays;
- the lifecycle cost engine prices each array alone and attributes to each array its own generation;
- the composed file, two instances of ``pv/array``, reproduces the flat file;
- a run's record names both arrays as the battery's sizing sources and says it reads every provider.

The tests that read the runs are ``extendedbase``; the check of the variant builder runs nothing and is ``base``.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterator, List, Tuple

import numpy as np
import pandas as pd
import pytest
import yaml

from hisim.energy_system.assemblies.twins import rename_column
from hisim.energy_system.comments import ProvenanceComments
from hisim.energy_system.configure import ConfiguredSystem
from hisim.energy_system.executor import run_energy_system


@dataclass
class ArrayRun:
    """What one run of a household variant produced.

    Attributes:
        results: Every output of every time step, one column per output, as the simulator holds them.
        kpis: The run's ``all_kpis.json``.
        lifecycle_costs: The run's ``lifecycle_costs.json``, the lifecycle cost engine's result.
        configured: The resolved configuration of every component, sized fields included.
        record_text: The run's ``realized.energy_system.yaml``, the record of what was run.
    """

    results: pd.DataFrame
    kpis: Dict[str, Any]
    lifecycle_costs: Dict[str, Any]
    configured: ConfiguredSystem
    record_text: str

    def column(self, component: str, output: str) -> pd.Series:
        """Returns the one result column of an output, found by its component and output name.

        Example: ``run.column("PVSouth", "ElectricityOutput")`` is the south array's AC power in W per step.

        Args:
            component: The component's runtime name.
            output: The output's field name.

        Returns:
            The column's values per time step.

        Raises:
            KeyError: If no column or more than one column belongs to that output.
        """
        prefix = f"{component} - {output} ["
        matches = [name for name in self.results.columns if name.startswith(prefix)]
        if len(matches) != 1:
            raise KeyError(f"expected one result column for {component}.{output}, found {matches}")
        return self.results[matches[0]]

    def general_kpi(self, name: str) -> float:
        """Returns the value of one derived KPI of the General tag, the building's totals.

        Args:
            name: The KPI's name, for example ``"PV production"``.

        Returns:
            The KPI's value.
        """
        return float(self.kpis["BUI1"]["General"][name]["value"])

    def breakdown(self, subject: str) -> Dict[str, Any]:
        """Returns the lifecycle cost engine's breakdown of one priced subject under the gross greenfield view.

        Args:
            subject: The subject's name, which is the component's runtime name.

        Returns:
            The breakdown: asset class, net present value per cost category and in total.
        """
        return dict(self.lifecycle_costs["greenfield_gross"]["component_breakdowns"][subject])

    def attributed_generation_in_kwh(self, subject: str) -> float:
        """Returns the annual PV generation the lifecycle cost engine attributes to one array.

        Args:
            subject: The array's runtime name.

        Returns:
            The generation in kWh per year, extrapolated from the simulated day.
        """
        by_subject = self.lifecycle_costs["greenfield_gross"]["annual_energy_attribution_by_subject_in_kwh"]
        return float(by_subject[subject]["PV_GENERATION"])


class TwoArrays:
    """The fixture household, its variants and how one of them is run.

    Each variant is the fixture's YAML edited as data, so the variants differ from the fixture in exactly the
    entries named here and nowhere else.
    """

    #: The flat fixture: two ``PVSystem`` entries beside the manager, the battery and the meter.
    FLAT: ClassVar[Path] = Path(__file__).resolve().parent / "energy_systems" / "two_pv_arrays.energy_system.yaml"

    #: The same household composed from the assembly library: two instances of the ``pv/array`` import.
    COMPOSED: ClassVar[Path] = (
        Path(__file__).resolve().parent / "energy_systems" / "two_pv_arrays.composed.energy_system.yaml"
    )

    #: Entry names of the flat fixture.
    SOUTH: ClassVar[str] = "PVSouth"
    WEST: ClassVar[str] = "PVWest"
    ARRAYS: ClassVar[Tuple[str, ...]] = (SOUTH, WEST)
    RESIDENTS: ClassVar[str] = "UTSPConnector"
    METER: ClassVar[str] = "ElectricityMeter"
    BATTERY: ClassVar[str] = "Battery"
    MANAGER: ClassVar[str] = "L2EMSElectricityController"

    #: The peak power of each array in W, as the fixture states it.
    PEAK_POWER_IN_WATT: ClassVar[Dict[str, float]] = {SOUTH: 4000.0, WEST: 2000.0}

    #: How the composed file's members are named in the flat fixture.
    COMPOSED_NAMES: ClassVar[Dict[str, str]] = {
        "pv-south-PVSystem": SOUTH,
        "pv-west-PVSystem": WEST,
        "grid-ElectricityMeter": METER,
        "battery-Battery": BATTERY,
        "control-EMS": MANAGER,
    }

    #: One June day at 15 minutes, with the KPIs and the lifecycle costs. June, so both arrays generate enough for a
    #: difference between them and a surplus for the battery to show.
    PARAMETERS: ClassVar[Dict[str, Any]] = {
        "start_date": "2021-06-01T00:00:00",
        "end_date": "2021-06-02T00:00:00",
        "seconds_per_timestep": 900,
        "country": "DE",
        "logging_level": 3,
        "post_processing_options": [
            "COMPUTE_CAPEX",
            "COMPUTE_OPEX",
            "COMPUTE_KPIS",
            "WRITE_KPIS_TO_JSON",
            "COMPUTE_LIFECYCLE_COSTS",
        ],
    }

    #: Tolerance for two series that must be the same numbers: an array's generation in two runs, or a sum
    #: computed in two orders. In W.
    POWER_TOLERANCE_IN_WATT: ClassVar[float] = 1e-6

    #: Tolerance of a KPI against a sum of KPIs: every KPI of the General tag is rounded to 0.1 kWh, so a sum of
    #: two of them may differ from the rounded total by up to 0.1 kWh.
    ROUNDED_KPI_TOLERANCE_IN_KWH: ClassVar[float] = 0.1 + 1e-9

    @classmethod
    def document(cls) -> Dict[str, Any]:
        """Reads the flat fixture as plain data.

        Returns:
            A fresh copy of the fixture's YAML document.
        """
        return dict(yaml.safe_load(cls.FLAT.read_text(encoding="utf-8")))

    @classmethod
    def alone(cls, array: str) -> Dict[str, Any]:
        """Builds the fixture with only one of its two arrays: the other's entry and its two readers' inputs removed.

        The battery stays, now sized to the remaining array, so the variant is the household that array would
        make on its own.

        Args:
            array: The entry name of the array that stays.

        Returns:
            The variant's document.
        """
        document = cls.document()
        for other in cls.ARRAYS:
            if other != array:
                del document["components"][other]
                for reader in (cls.MANAGER, cls.METER):
                    entry = document["components"][reader]
                    entry["inputs"] = [item for item in entry["inputs"] if not item["from"].startswith(f"{other}.")]
        return document

    @classmethod
    def metered_directly(cls) -> Dict[str, Any]:
        """Builds the fixture without the manager and the battery, the meter observing both arrays and the residents.

        This is the household the way ``basic_household`` meters its single array: the meter is the only component
        that sums the production.

        Returns:
            The variant's document.
        """
        document = cls.document()
        del document["components"][cls.MANAGER]
        del document["components"][cls.BATTERY]
        production: List[Dict[str, Any]] = [
            {"from": f"{array}.ElectricityOutput", "component_type": "PV", "tags": ["ELECTRICITY_PRODUCTION"],
             "weight": 999}
            for array in cls.ARRAYS
        ]
        consumption = {
            "from": f"{cls.RESIDENTS}.ElectricalPowerConsumption",
            "tags": ["ELECTRICITY_CONSUMPTION_UNCONTROLLED"],
            "weight": 999,
        }
        document["components"][cls.METER]["inputs"] = production + [consumption]
        return document

    @classmethod
    def run(cls, energy_system: Any, directory: Path) -> ArrayRun:
        """Runs one variant over the June day and reads back what it produced.

        Args:
            energy_system: The variant's document, written into ``directory`` before the run, or the path of a
                file to run as it is.
            directory: An empty directory for the variant's file, its parameters and its results.

        Returns:
            The run's results, KPIs, lifecycle costs, configurations and record.
        """
        directory.mkdir(parents=True, exist_ok=True)
        parameters = directory / "june_day.simulation.yaml"
        parameters.write_text(yaml.safe_dump(cls.PARAMETERS, sort_keys=False), encoding="utf-8")
        if isinstance(energy_system, dict):
            path = directory / "variant.energy_system.yaml"
            path.write_text(yaml.safe_dump(energy_system, sort_keys=False), encoding="utf-8")
        else:
            path = Path(energy_system)
        results = directory / "results"
        built = run_energy_system(path, parameters, result_directory=str(results))
        return ArrayRun(
            results=built.simulator.results_data_frame,
            kpis=json.loads((results / "all_kpis.json").read_text(encoding="utf-8")),
            lifecycle_costs=json.loads((results / "lifecycle_costs.json").read_text(encoding="utf-8")),
            configured=built.configured,
            record_text=(results / "realized.energy_system.yaml").read_text(encoding="utf-8"),
        )

    @classmethod
    def renamed_column(cls, column: str) -> str:
        """Renames one result column of the composed run to the flat fixture's name of the same output.

        It renames as the twin gates do (``rename_column``): the component, and every port name derived from a
        member's name, so the manager's ``DispatchTobattery_Battery_LoadingPowerInput`` becomes
        ``DispatchToBattery_LoadingPowerInput``.

        Example: ``"pv-south-PVSystem - ElectricityOutput [Electricity - W]"`` becomes
        ``"PVSouth - ElectricityOutput [Electricity - W]"``.

        Args:
            column: The composed run's column name.

        Returns:
            The flat run's name of that column; a site component's column unchanged.
        """
        return rename_column(column, cls.COMPOSED_NAMES)


@pytest.fixture(name="runs", scope="module")
def fixture_runs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Dict[str, ArrayRun]]:
    """Runs the fixture and its variants once for every test of this module.

    Yields:
        The runs by variant: ``both``, ``PVSouth`` and ``PVWest`` alone, ``metered`` and ``composed``.
    """
    directory = tmp_path_factory.mktemp("two_pv_arrays")
    runs = {
        "both": TwoArrays.run(TwoArrays.document(), directory / "both"),
        "metered": TwoArrays.run(TwoArrays.metered_directly(), directory / "metered"),
        "composed": TwoArrays.run(TwoArrays.COMPOSED, directory / "composed"),
    }
    for array in TwoArrays.ARRAYS:
        runs[array] = TwoArrays.run(TwoArrays.alone(array), directory / array)
    yield runs


@pytest.mark.extendedbase
def test_each_array_generates_what_it_generates_alone(runs: Dict[str, ArrayRun]) -> None:
    """Catches a second array changing the first: a shared cache entry, a shared state or an overwritten output."""
    for array in TwoArrays.ARRAYS:
        for output in ("ElectricityOutput", "ElectricityEnergyOutput"):
            np.testing.assert_allclose(
                runs["both"].column(array, output).to_numpy(),
                runs[array].column(array, output).to_numpy(),
                rtol=0.0,
                atol=TwoArrays.POWER_TOLERANCE_IN_WATT,
                err_msg=f"{array}.{output}",
            )
    south = runs["both"].column(TwoArrays.SOUTH, "ElectricityOutput")
    west = runs["both"].column(TwoArrays.WEST, "ElectricityOutput")
    assert south.max() > 1000.0 and west.max() > 500.0, "a June day in Aachen must make both arrays generate"
    assert not np.allclose(south.to_numpy() / 2.0, west.to_numpy()), "the west array is not the south one halved"


@pytest.mark.extendedbase
def test_the_manager_balances_the_summed_production(runs: Dict[str, ArrayRun]) -> None:
    """Catches the manager reading one array of two: its grid balance is both arrays minus what it dispatched."""
    run = runs["both"]
    production = run.column(TwoArrays.SOUTH, "ElectricityOutput") + run.column(TwoArrays.WEST, "ElectricityOutput")
    expected = (
        production
        - run.column(TwoArrays.RESIDENTS, "ElectricalPowerConsumption")
        - run.column(TwoArrays.BATTERY, "AcBatteryPowerUsed")
    )
    balance = run.column(TwoArrays.MANAGER, "TotalElectricityToOrFromGrid")
    np.testing.assert_allclose(
        balance.to_numpy(), expected.to_numpy(), rtol=0.0, atol=TwoArrays.POWER_TOLERANCE_IN_WATT
    )
    np.testing.assert_allclose(
        run.column(TwoArrays.METER, "ElectricityAvailable").to_numpy(),
        balance.to_numpy(),
        rtol=0.0,
        atol=TwoArrays.POWER_TOLERANCE_IN_WATT,
    )
    assert run.column(TwoArrays.BATTERY, "AcBatteryPowerUsed").max() > 0.0, "the surplus must charge the battery"


@pytest.mark.extendedbase
def test_a_meter_observing_both_arrays_sums_them(runs: Dict[str, ArrayRun]) -> None:
    """Catches a meter that keeps one production input of two, the shape a household without a manager has."""
    run = runs["metered"]
    production = run.column(TwoArrays.SOUTH, "ElectricityOutput") + run.column(TwoArrays.WEST, "ElectricityOutput")
    consumption = run.column(TwoArrays.RESIDENTS, "ElectricalPowerConsumption")
    meter = TwoArrays.METER
    tolerance = TwoArrays.POWER_TOLERANCE_IN_WATT
    np.testing.assert_allclose(
        run.column(meter, "ElectricityProductionInWatt").to_numpy(), production.to_numpy(), rtol=0.0, atol=tolerance
    )
    np.testing.assert_allclose(
        run.column(meter, "ElectricityToGridInWatt").to_numpy(),
        (production - consumption).clip(lower=0.0).to_numpy(),
        rtol=0.0,
        atol=tolerance,
    )
    np.testing.assert_allclose(
        run.column(meter, "ElectricityFromGridInWatt").to_numpy(),
        (consumption - production).clip(lower=0.0).to_numpy(),
        rtol=0.0,
        atol=tolerance,
    )


@pytest.mark.extendedbase
def test_the_building_kpis_report_the_sum_of_the_arrays(runs: Dict[str, ArrayRun]) -> None:
    """Catches a total that counts one array, or self-consumption computed on part of the production."""
    both = runs["both"]
    seconds_per_timestep = TwoArrays.PARAMETERS["seconds_per_timestep"]
    generated_in_kwh = sum(
        both.column(array, "ElectricityOutput").clip(lower=0.0).sum() * seconds_per_timestep / 3.6e6
        for array in TwoArrays.ARRAYS
    )
    assert both.general_kpi("PV production") == pytest.approx(round(generated_in_kwh, 1), abs=1e-9)
    alone_in_kwh = sum(runs[array].general_kpi("PV production") for array in TwoArrays.ARRAYS)
    assert both.general_kpi("PV production") == pytest.approx(alone_in_kwh, abs=TwoArrays.ROUNDED_KPI_TOLERANCE_IN_KWH)
    assert both.general_kpi("Total electricity production") == both.general_kpi("PV production")
    # Self-consumption plus grid injection is the summed production, the battery's round trip aside: what the
    # battery takes in and gives back is self-consumed, and its losses are part of the consumption.
    metered = runs["metered"]
    assert metered.general_kpi("Self-consumption of electricity") + metered.general_kpi(
        "Grid injection of electricity"
    ) == pytest.approx(metered.general_kpi("PV production"), abs=TwoArrays.ROUNDED_KPI_TOLERANCE_IN_KWH)
    assert metered.general_kpi("PV production") == both.general_kpi("PV production")


@pytest.mark.extendedbase
def test_the_battery_is_sized_to_the_sum_of_the_arrays(runs: Dict[str, ArrayRun]) -> None:
    """Catches the battery law reading one array of two, or refusing two providers of its fact."""
    total_in_watt = sum(TwoArrays.PEAK_POWER_IN_WATT.values())
    battery = runs["both"].configured.config_of(TwoArrays.BATTERY)
    assert battery.custom_battery_capacity_generic_in_kilowatt_hour == pytest.approx(total_in_watt * 1e-3)
    assert battery.custom_pv_inverter_power_generic_in_watt == pytest.approx(total_in_watt * 0.5)
    alone = runs[TwoArrays.SOUTH].configured.config_of(TwoArrays.BATTERY)
    assert alone.custom_battery_capacity_generic_in_kilowatt_hour == pytest.approx(
        TwoArrays.PEAK_POWER_IN_WATT[TwoArrays.SOUTH] * 1e-3
    )


@pytest.mark.extendedbase
def test_the_cost_engine_prices_and_credits_each_array_on_its_own(runs: Dict[str, ArrayRun]) -> None:
    """Catches arrays merged into one cost subject, or one array priced at the other's size.

    Each array is a subject of its own, priced at its own peak power, so its investment is the one it has in a
    household where it stands alone; and the generation the engine attributes to it is its own, so the two
    attributions stand in the ratio of the two arrays' energies.
    """
    both = runs["both"]
    for array in TwoArrays.ARRAYS:
        breakdown = both.breakdown(array)
        assert breakdown["asset_class"] == "PV"
        assert breakdown["npv_by_category"]["INVESTMENT"] == pytest.approx(
            runs[array].breakdown(array)["npv_by_category"]["INVESTMENT"], rel=1e-12
        )
        assert both.attributed_generation_in_kwh(array) == pytest.approx(
            runs[array].attributed_generation_in_kwh(array), rel=1e-9
        )
    south_investment = both.breakdown(TwoArrays.SOUTH)["npv_by_category"]["INVESTMENT"]
    west_investment = both.breakdown(TwoArrays.WEST)["npv_by_category"]["INVESTMENT"]
    assert south_investment == pytest.approx(2.0 * west_investment, rel=1e-9), "PV is priced per kW"
    energy = {array: both.column(array, "ElectricityEnergyOutput").sum() for array in TwoArrays.ARRAYS}
    assert both.attributed_generation_in_kwh(TwoArrays.SOUTH) / both.attributed_generation_in_kwh(
        TwoArrays.WEST
    ) == pytest.approx(energy[TwoArrays.SOUTH] / energy[TwoArrays.WEST], rel=1e-6)


@pytest.mark.extendedbase
def test_the_composed_file_with_two_array_instances_reproduces_the_flat_file(runs: Dict[str, ArrayRun]) -> None:
    """Catches the assembly path losing an instance, mis-binding the battery's fact port or reordering the arrays."""
    flat = runs["both"].results
    composed = runs["composed"].results.rename(columns=TwoArrays.renamed_column)
    assert set(composed.columns) == set(flat.columns), sorted(set(composed.columns) ^ set(flat.columns))
    pd.testing.assert_frame_equal(composed[list(flat.columns)], flat, check_exact=False, rtol=1e-12, atol=1e-9)
    battery = runs["composed"].configured.config_of("battery-Battery")
    assert battery.custom_battery_capacity_generic_in_kilowatt_hour == pytest.approx(
        sum(TwoArrays.PEAK_POWER_IN_WATT.values()) * 1e-3
    )


@pytest.mark.extendedbase
def test_the_record_names_every_array_as_the_batterys_sizing_source(runs: Dict[str, ArrayRun]) -> None:
    """Catches a record that re-runs against one array of two, or calls a list of providers a unique provider."""
    record = yaml.safe_load(runs["both"].record_text)
    sources = record["components"][TwoArrays.BATTERY]["sizing_sources"]["pv_peak_power_in_watt"]
    assert sources == [f"{array}.pv_peak_power_in_watt" for array in TwoArrays.ARRAYS]
    battery_lines = [line for line in runs["both"].record_text.splitlines() if "pv_peak_power_in_watt:" in line]
    assert any(ProvenanceComments.ADDED_MANY_FOR_REEXECUTION in line for line in battery_lines), battery_lines


@pytest.mark.base
def test_a_variant_differs_from_the_fixture_only_where_it_says() -> None:
    """Catches a variant builder that edits more than the entries it names, which would void the comparisons."""
    fixture = TwoArrays.document()
    south_alone = TwoArrays.alone(TwoArrays.SOUTH)
    assert set(fixture["components"]) - set(south_alone["components"]) == {TwoArrays.WEST}
    for reader in (TwoArrays.MANAGER, TwoArrays.METER):
        expected = copy.deepcopy(fixture["components"][reader]["inputs"])
        expected = [item for item in expected if not item["from"].startswith(f"{TwoArrays.WEST}.")]
        assert south_alone["components"][reader]["inputs"] == expected
    for name in (TwoArrays.SOUTH, TwoArrays.BATTERY, TwoArrays.RESIDENTS):
        assert south_alone["components"][name] == fixture["components"][name]
