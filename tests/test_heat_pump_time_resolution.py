"""The hplib heat pump's space heating gives the same energies at 60, 900 and 3600 s, and plausible starts.

Since the heat pump holds its outlet at its controller's set point and modulates down to its minimum output, its 900 s
part-load mode runs the same law as its 60 s steps: the outlet is the set point whenever the needed power lies in the
modulation range, so the COP is the same, and the delivered heat is the house's. What still differs is the buffer: at
60 s it is an iterated mixed node, above 600 s a start-of-step node whose charge ends at the controller's target.

Two kinds of check, by cost:

* :class:`HeatPumpResolutionRuns` runs the grouped heat-pump twin for the first four weeks of 2021 at 60, 900 and
  3600 s, without its energy manager (``metered_directly``), and asserts the heat the distribution system delivers to
  the building and the heat pump's space-heating electricity at 900 s within 1 % of 60 s, the tolerance of the
  modelling guide. Its hot-water electricity misses the 1 % (+1.27 %) and is a strict expected failure until the owner
  decides (:attr:`HeatPumpResolutionRuns.ASSERTED`). 3600 s and the lowest room temperature are recorded in the test's
  output, not asserted. ``extendedbase2``, with the other heat-pump resolution runs; about 45 s on a local machine.
* :class:`BlessedStarts` reads the twin's blessed full-year goldens, which the golden gate keeps equal to a run of the
  same setup, and runs nothing (``base``), as tests/test_space_heating_time_resolution.py reads its 60 s reference.
  The yearly starts at 60 s lie below the 5,500 a year that 90 % of the air/water units of the Fraunhofer ISE field
  test stay below (WP-QS im Bestand, final report 2025, §6.3), and the 900 s starts, the controller's hysteresis-cycle
  estimate, lie within 25 % of the 60 s count, the space-heating starts and all starts alike (owner decision of
  2026-11-26: the starts are an order-of-magnitude figure, not an energy).
"""

from pathlib import Path
from typing import Any, ClassVar, Dict, Mapping, Tuple

import pytest
import yaml

from hisim.components.heat_distribution_system import HeatDistribution
from hisim.components.more_advanced_heat_pump.heat_pump import MoreAdvancedHeatPumpHPLib
from hisim.energy_system.executor import run_energy_system
from scripts.golden_kpis import load_golden


class HeatPumpResolutionRuns:
    """Four weeks of the grouped heat-pump twin at one resolution, and the energies read from its outputs."""

    #: The repository's root.
    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[1]

    #: The twin, with its electricity-management variant: the heat pump's own control, no energy manager.
    TWIN: ClassVar[str] = "household_heatpump_building_sizer.grouped"
    VARIANT: ClassVar[Tuple[str, str]] = ("electricity_management", "metered_directly")

    #: The window: the first four weeks of 2021, the heating season's core, where the machine modulates and cycles.
    WINDOW: ClassVar[Tuple[str, str]] = ("2021-01-01T00:00:00", "2021-01-29T00:00:00")

    #: The reference resolution, s.
    REFERENCE_SECONDS: ClassVar[int] = 60

    #: The tolerance of every asserted energy at 900 s against 60 s, a fraction: the modelling guide's 1 %, which the
    #: heat-pump modulation spec keeps (owner decision of 2026-11-26).
    RELATIVE_TOLERANCE: ClassVar[float] = 0.01

    #: The energies already computed in this test process, by resolution.
    computed: ClassVar[Dict[int, Dict[str, float]]] = {}

    @classmethod
    def energies(cls, seconds_per_timestep: int, factory: pytest.TempPathFactory) -> Dict[str, float]:
        """The window's energies of the twin at one resolution, in kWh, each run once per test process.

        Returns:
            ``delivered_heat_in_kwh``: the heat the distribution system delivered to the building;
            ``space_heating_electricity_in_kwh``: the heat pump compressor's space-heating electricity;
            ``hot_water_electricity_in_kwh``: its hot-water electricity;
            ``lowest_room_temperature_in_celsius`` and ``backup_rod_electricity_in_kwh``, recorded only.
        """
        if seconds_per_timestep not in cls.computed:
            cls.computed[seconds_per_timestep] = cls.run(seconds_per_timestep, factory.mktemp(f"hp_{seconds_per_timestep}"))
        return cls.computed[seconds_per_timestep]

    @classmethod
    def run(cls, seconds_per_timestep: int, work: Path) -> Dict[str, float]:
        """Run the twin over :attr:`WINDOW` at one resolution in ``work`` and sum its energies."""
        document = yaml.safe_load((cls.ROOT / "energy_systems" / f"{cls.TWIN}.energy_system.yaml").read_text(encoding="utf-8"))
        variant, option = cls.VARIANT
        document["variants"][variant]["selected"] = option
        energy_system = work / "twin.energy_system.yaml"
        energy_system.write_text(
            yaml.safe_dump(document, sort_keys=False).replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), encoding="utf-8"
        )
        parameters = work / "window.simulation.yaml"
        start, end = cls.WINDOW
        parameters.write_text(
            yaml.safe_dump(
                {
                    "start_date": start,
                    "end_date": end,
                    "seconds_per_timestep": seconds_per_timestep,
                    "country": "DE",
                    "logging_level": 3,
                    "post_processing_options": [],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        results = run_energy_system(energy_system, parameters, result_directory=str(work / "results")).simulator.results_data_frame
        hours_per_step = seconds_per_timestep / 3600.0

        def column(component: str, field: str) -> Any:
            """Return the one result column of ``component``'s output ``field``."""
            matches = [name for name in results.columns if name.startswith(f"{component} - {field} [")]
            assert len(matches) == 1, (component, field, matches)
            return results[matches[0]]

        def energy_in_kwh(component: str, field: str) -> float:
            """Return the energy of ``component``'s power output ``field``, in W, over the window, in kWh."""
            return float(column(component, field).sum()) * hours_per_step / 1000.0

        heat_pump = "MoreAdvancedHeatPumpHPLib"
        return {
            "delivered_heat_in_kwh": energy_in_kwh("HeatDistributionSystem", HeatDistribution.ThermalPowerDelivered),
            "space_heating_electricity_in_kwh": energy_in_kwh(heat_pump, MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH),
            "hot_water_electricity_in_kwh": energy_in_kwh(heat_pump, MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW),
            "backup_rod_electricity_in_kwh": energy_in_kwh(heat_pump, MoreAdvancedHeatPumpHPLib.ElectricalInputPowerHeatingRod),
            "lowest_room_temperature_in_celsius": float(column("Building", "TemperatureIndoorAir").min()),
        }

    #: The energies the 900 s run is held to. The hot-water electricity misses the 1 %: measured on 2026-11-27,
    #: 95.63 kWh at 900 s against 94.43 kWh at 60 s (+1.27 %; +0.92 % with the local LoadProfileGenerator's
    #: profile), while the heat the tap and the charges book agree within 1 % (tests/test_dhw_chain_time_resolution.py).
    #: The hot-water charge runs whole steps above the threshold until its part load (#916), so a 900 s charge ends
    #: the cylinder hotter and at a lower COP. The modulation spec gives no fix for it: the owner decides, and until
    #: then the case is a strict expected failure, which fails as soon as it passes.
    ASSERTED: ClassVar[Tuple[Any, ...]] = (
        "delivered_heat_in_kwh",
        "space_heating_electricity_in_kwh",
        pytest.param(
            "hot_water_electricity_in_kwh",
            marks=pytest.mark.xfail(
                strict=True,
                reason="+1.27 % at 900 s: the hot-water charge runs whole steps above 600 s (#916); owner decision pending",
            ),
        ),
    )


@pytest.mark.extendedbase2
@pytest.mark.xdist_group("tests/test_heat_pump_time_resolution.py::runs")
@pytest.mark.parametrize("energy_name", HeatPumpResolutionRuns.ASSERTED)
def test_the_heat_pump_s_energies_at_900_s_lie_within_1_percent_of_60_s(
    energy_name: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The delivered heat and the space-heating and hot-water electricity at 900 s within 1 % of 60 s; 3600 s recorded.

    Catches a coarse-step model of the heat pump or its buffer that charges at another outlet, holds the buffer
    elsewhere or loses heat the 60 s run does not: the step length would decide the result. Every energy and the
    lowest room temperature of the three resolutions are printed, so the 3600 s values are on record.
    """
    reference = HeatPumpResolutionRuns.energies(HeatPumpResolutionRuns.REFERENCE_SECONDS, tmp_path_factory)
    compared = HeatPumpResolutionRuns.energies(900, tmp_path_factory)
    hourly = HeatPumpResolutionRuns.energies(3600, tmp_path_factory)
    for name, value in reference.items():
        print(f"{name}: 60 s {value:.3f}, 900 s {compared[name]:.3f}, 3600 s {hourly[name]:.3f}")
    assert compared[energy_name] == pytest.approx(
        reference[energy_name], rel=HeatPumpResolutionRuns.RELATIVE_TOLERANCE
    ), energy_name


class BlessedStarts:
    """The heat pump's starts in the heat-pump twin's blessed full-year goldens."""

    #: The repository's root.
    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[1]

    #: The setup whose goldens are read: the heat-pump twin as recorded, with its energy manager.
    SETUP: ClassVar[str] = "household_heatpump_building_sizer"

    #: The starts a year 90 % of the 51 air/water units of the Fraunhofer ISE field test stay below (heating and hot
    #: water, year 2024; final report "WP-QS im Bestand", 2025, §6.3: 540 to 15,820, a third at most 2,000):
    #: the upper bound of what the field accepts.
    FIELD_UPPER_BOUND_OF_STARTS_PER_YEAR: ClassVar[int] = 5500

    #: How far the 900 s estimate may lie from the 60 s count, a fraction (owner decision of 2026-11-26).
    ESTIMATE_RELATIVE_TOLERANCE: ClassVar[float] = 0.25

    #: The two starts KPIs: every start, and the starts into space heating, which the estimate covers.
    STARTS_KPI_NAMES: ClassVar[Tuple[str, ...]] = ("Number of heat pump starts", "Number of space-heating starts")

    @classmethod
    def starts(cls, parameter_set: str, kpi_name: str) -> float:
        """The value of one starts KPI of the heat pump in the golden of ``parameter_set``.

        Raises:
            AssertionError: When the golden holds not exactly one such KPI of the heat pump.
        """
        path = cls.ROOT / "golden_references" / f"{cls.SETUP}__{parameter_set}.json"
        leaves: Mapping[str, Mapping[str, Any]] = load_golden(path)
        matches = [
            leaf for leaf in leaves.values() if leaf["name"] == kpi_name and leaf["source"]["member"] == "MoreAdvancedHeatPumpHPLib"
        ]
        assert len(matches) == 1, (str(path), kpi_name, len(matches))
        return float(matches[0]["value"])


@pytest.mark.base
def test_the_yearly_starts_at_60_s_lie_in_the_field_range() -> None:
    """The heat-pump twin starts its machine fewer than 5,500 times a year at 60 s, the field test's upper bound.

    Catches a controller or a minimum time that lets the machine cycle far more often than real units do.
    """
    assert 0 < BlessedStarts.starts("full_year_60s", "Number of heat pump starts") < BlessedStarts.FIELD_UPPER_BOUND_OF_STARTS_PER_YEAR


@pytest.mark.base
@pytest.mark.parametrize("kpi_name", BlessedStarts.STARTS_KPI_NAMES)
def test_the_900_s_starts_estimate_lies_within_25_percent_of_the_60_s_count(kpi_name: str) -> None:
    """The year's starts at 900 s, the controller's hysteresis-cycle estimate, within 25 % of the 60 s count.

    Catches a coarse-step start count that counts the end of every idle step (four times the 60 s count before the
    estimate) or loses the cycles inside a step.
    """
    reference = BlessedStarts.starts("full_year_60s", kpi_name)
    assert BlessedStarts.starts("full_year_900s", kpi_name) == pytest.approx(
        reference, rel=BlessedStarts.ESTIMATE_RELATIVE_TOLERANCE
    )
