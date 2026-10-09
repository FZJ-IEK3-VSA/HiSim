"""The hot-water chain gives the same heat at 60, 900 and 3600 s (hydronic coupling spec §9.4, §9.5 C).

The hot-water tank is a fully mixed node whose step is exact for constant inflows, and every generator books the heat
its water carries into it, so the heat drawn at the tap and the heat the generators deliver no longer depend on the
step length the way they did while the tank mixed masses (the boiler's hot-water heat was 28 % higher at 900 s than at
60 s, and twice as high at 3600 s). Six recorded twins, a boiler, a district-heating substation, an electric heater,
a heat pump and the two with a solar collector beside a boiler or a heat pump, run the first four weeks of 2021 at
each resolution, and:

* the heat drawn at the tap agrees within 1 % at 900 s and at 3600 s;
* the generators' hot-water heat, net of the heat the tank holds more or less at the end of the window, agrees
  within 1 % at 900 s and within 1.5 % at 3600 s.

The 3600 s excess is the standby loss of a hotter tank: a controller decides on the tank's start temperature (D4), so
an hour-long charge keeps the tank near its supply cap, the controller's set temperature, longer than a quarter-hour
one. Reaching 1 % at 3600 s is open (spec §7). The windows start on 1 January, since a window starting later reads
its weather from 1 January (hisim-9g2).
"""

from pathlib import Path
from typing import Any, ClassVar, Dict, List, Tuple

import pytest
import yaml

from hisim.components.simple_water_storage import SimpleDHWStorage
from hisim.energy_system.executor import run_energy_system


class ResolutionRuns:
    """Four weeks of a twin at one resolution, and the hot-water sums read from it."""

    #: The repository's root.
    ROOT = Path(__file__).resolve().parents[1]

    #: The twins compared: one per kind of hot-water generator with a fixed lift or a set supply temperature.
    TWINS: Tuple[str, ...] = (
        "household_gas_building_sizer",
        "household_district_heating_building_sizer",
        "household_electric_heating_building_sizer",
        "household_heatpump_building_sizer",
        "household_gas_solar_thermal_building_sizer",
        "household_heatpump_solar_thermal_building_sizer",
    )

    #: The shard each twin runs in, by wall time (pytest.ini): with its 60 s reference a twin's runs take 11 to 33 s
    #: on a local machine and about five times that in CI. The two heat-pump twins (about 60 s locally) join the
    #: heat-pump booking runs in ``extendedbase2``, which runs serially; the other four (about 65 s locally) run in
    #: ``extendedbase``, whose two workers take this module beside the participant canaries.
    SHARDS: ClassVar[Dict[str, pytest.MarkDecorator]] = {
        "household_gas_building_sizer": pytest.mark.extendedbase,
        "household_district_heating_building_sizer": pytest.mark.extendedbase,
        "household_electric_heating_building_sizer": pytest.mark.extendedbase,
        "household_heatpump_building_sizer": pytest.mark.extendedbase2,
        "household_gas_solar_thermal_building_sizer": pytest.mark.extendedbase,
        "household_heatpump_solar_thermal_building_sizer": pytest.mark.extendedbase2,
    }

    #: The window: the first four weeks of 2021.
    WINDOW: Tuple[str, str] = ("2021-01-01T00:00:00", "2021-01-29T00:00:00")

    #: The reference resolution and the two compared with it, s.
    REFERENCE_SECONDS: int = 60

    #: Relative tolerance per compared resolution, for the generators' heat net of the stored heat.
    GENERATOR_HEAT_TOLERANCE: Dict[int, float] = {900: 0.01, 3600: 0.015}

    #: Relative tolerance of the heat drawn at the tap, at every compared resolution.
    TAP_HEAT_TOLERANCE: float = 0.01

    #: The sums already computed in this test process, by twin and resolution.
    computed: ClassVar[Dict[Tuple[str, int], Dict[str, float]]] = {}

    @classmethod
    def twin_parameters(cls) -> List[Any]:
        """One parameter per twin of :attr:`TWINS`, marked with its shard from :attr:`SHARDS`."""
        return [pytest.param(twin, marks=cls.SHARDS[twin], id=twin) for twin in cls.TWINS]

    @classmethod
    def sums(
        cls, twin: str, seconds_per_timestep: int, factory: pytest.TempPathFactory
    ) -> Dict[str, float]:
        """The window's hot-water sums of one twin at one resolution, in kWh, each run once per test process.

        Returns:
            ``tap``: the heat drawn at the tap (positive); ``generators``: the heat both charging circuits brought;
            ``stored``: the change of the heat the tank holds over the window.
        """
        key = (twin, seconds_per_timestep)
        if key not in cls.computed:
            cls.computed[key] = cls.run(twin, seconds_per_timestep, factory.mktemp(f"{twin}_{seconds_per_timestep}"))
        return cls.computed[key]

    @classmethod
    def run(cls, twin: str, seconds_per_timestep: int, work: Path) -> Dict[str, float]:
        """Run one twin over :attr:`WINDOW` at one resolution in ``work`` and sum its tank's heat flows."""
        text = (cls.ROOT / "energy_systems" / f"{twin}.energy_system.yaml").read_text(encoding="utf-8")
        energy_system = work / f"{twin}.energy_system.yaml"
        energy_system.write_text(text.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), encoding="utf-8")
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
        built = run_energy_system(energy_system, parameters, result_directory=str(work / "results"))
        results = built.simulator.results_data_frame

        def total_kwh(field: str) -> float:
            (column,) = [name for name in results.columns if f" - {field} [" in name and name.startswith("DHW")]
            return float(results[column].sum()) / 1000.0

        return {
            "tap": -total_kwh(SimpleDHWStorage.ThermalEnergyConsumptionDHW),
            "generators": total_kwh(SimpleDHWStorage.ThermalEnergyFromHeatGenerator)
            + total_kwh(SimpleDHWStorage.ThermalEnergyFromSecondaryHeatGenerator),
            "stored": total_kwh(SimpleDHWStorage.ThermalEnergyIncreaseInStorage),
        }


@pytest.mark.parametrize("seconds_per_timestep", [900, 3600])
@pytest.mark.parametrize("twin", ResolutionRuns.twin_parameters())
def test_the_hot_water_heat_agrees_across_resolutions(
    twin: str, seconds_per_timestep: int, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The tap's heat within 1 %, the generators' heat net of the stored heat within the resolution's tolerance."""
    reference = ResolutionRuns.sums(twin, ResolutionRuns.REFERENCE_SECONDS, tmp_path_factory)
    compared = ResolutionRuns.sums(twin, seconds_per_timestep, tmp_path_factory)
    assert compared["tap"] == pytest.approx(reference["tap"], rel=ResolutionRuns.TAP_HEAT_TOLERANCE)
    assert compared["generators"] - compared["stored"] == pytest.approx(
        reference["generators"] - reference["stored"],
        rel=ResolutionRuns.GENERATOR_HEAT_TOLERANCE[seconds_per_timestep],
    )
