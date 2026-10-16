"""The space-heating chain behind a buffer delivers the same heat to the building at 900 s as at 60 s.

Since hydronic stage D the space-heating buffer is a start-of-step node: every circuit books its heat on the
temperature the buffer starts the step at, and above 600 s the generator's charge and the heat distribution system's
draw each run the fraction of the step that keeps the buffer between the room and the heating curve's supply
temperature (``hisim/part_load.py``, decisions 21 and 22 of ``roadmap/hydronic_part_load_spec.md``). The owner decided
on 2026-10-16 (review of the stage-D spec, finding M-B, option a) what stage D is held to against the 60 s reference:

* asserted: the heat the heat distribution system delivers to the building over the full year 2021 at 900 s,
  RenoVisor's step length, lies within 1 % of the 60 s run's, for the four twins with a buffer: gas, pellets, heat
  pump, and heat pump with solar thermal;
* recorded, not asserted: the generator's booked space heat, the fuel and the electricity, and every 3600 s value
  (stage D's pull requests and ``roadmap/hydronic_coupling_spec.md`` §12 list them).

Measured on stage D's close-out (2026-10-16), 900 s against 60 s: gas −0.13 %, pellets −0.09 %, heat pump −0.70 %,
heat pump with solar thermal −0.66 %.

The comparison needs the year, not a window: the delivered heat at a coarse step drifts with the buffer's charge and
the room's comfort over the heating season, and a month would hide a shortfall the year shows. Each test runs one twin
at both resolutions (about 3 to 5 minutes on a local machine), so it is ``extendedbase``.
"""

from pathlib import Path
from typing import Any, List, Tuple

import pytest
import yaml

from hisim.components.heat_distribution_system import HeatDistribution
from hisim.energy_system.executor import run_energy_system


class SpaceHeatingResolutionRuns:
    """The full year 2021 of a twin at one resolution, and the heat its distribution system delivered."""

    #: The repository's root.
    ROOT = Path(__file__).resolve().parents[1]

    #: The twins with a space-heating buffer whose delivered heat the owner holds stage D to.
    TWINS: Tuple[str, ...] = (
        "household_gas_building_sizer",
        "household_pellets_building_sizer",
        "household_heatpump_building_sizer",
        "household_heatpump_solar_thermal_building_sizer",
    )

    #: The window: the full year 2021.
    WINDOW: Tuple[str, str] = ("2021-01-01T00:00:00", "2022-01-01T00:00:00")

    #: The reference resolution, s.
    REFERENCE_SECONDS: int = 60

    #: The compared resolution, s: RenoVisor's.
    COMPARED_SECONDS: int = 900

    #: Relative tolerance of the delivered heat: the 1 % of ``system_docs/modelling_guide.md`` §3 for a coarse-step
    #: model against the 60 s reference, which the owner kept for stage D on 2026-10-16.
    DELIVERED_HEAT_RELATIVE_TOLERANCE: float = 0.01

    @classmethod
    def twin_parameters(cls) -> List[Any]:
        """One parameter per twin of :attr:`TWINS`, in ``extendedbase`` and in an ``xdist_group`` of its own.

        The group, ``tests/test_space_heating_time_resolution.py::<twin>``, is the twin's unit in the CI split of main
        (``tests/ci_split.py``) and its scheduling unit under ``--dist loadgroup``, so the four twins may run in
        different jobs and on different workers.
        """
        return [
            pytest.param(
                twin,
                marks=[
                    pytest.mark.extendedbase,
                    pytest.mark.xdist_group(f"tests/test_space_heating_time_resolution.py::{twin}"),
                ],
                id=twin,
            )
            for twin in cls.TWINS
        ]

    @classmethod
    def run(cls, twin: str, seconds_per_timestep: int, work: Path) -> float:
        """Run one twin over :attr:`WINDOW` at one resolution in ``work`` and sum the heat delivered, kWh."""
        text = (cls.ROOT / "energy_systems" / f"{twin}.energy_system.yaml").read_text(encoding="utf-8")
        energy_system = work / f"{twin}.energy_system.yaml"
        energy_system.write_text(text.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), encoding="utf-8")
        parameters = work / "year.simulation.yaml"
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
        matches = [
            name
            for name in results.columns
            if name.startswith("HeatDistribution") and f" - {HeatDistribution.ThermalPowerDelivered} [" in name
        ]
        assert len(matches) == 1, matches
        assert len(results) == 365 * 86400 // seconds_per_timestep
        mean_power_in_watt = results[matches[0]].to_numpy(dtype=float)
        return float(mean_power_in_watt.sum()) * seconds_per_timestep / 3600.0 / 1000.0


@pytest.mark.parametrize("twin", SpaceHeatingResolutionRuns.twin_parameters())
def test_the_heat_delivered_to_the_building_at_900_s_is_within_1_percent_of_60_s(
    twin: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The year's heat delivered at 900 s lies within 1 % of the 60 s run's.

    Fails when the buffer's part-load mode no longer keeps a coarse step's space heating on the 60 s reference: a
    generator or distribution circuit that ran whole steps into the start-of-step buffer, a floor that let the buffer
    drain, or a comfort remainder that went missing would each move the heat delivered at 900 s away from 60 s.
    """
    runs = SpaceHeatingResolutionRuns
    reference = runs.run(twin, runs.REFERENCE_SECONDS, tmp_path_factory.mktemp(f"{twin}_{runs.REFERENCE_SECONDS}"))
    compared = runs.run(twin, runs.COMPARED_SECONDS, tmp_path_factory.mktemp(f"{twin}_{runs.COMPARED_SECONDS}"))
    # Every one of the four houses takes about 11,000 kWh a year; a reference far below it would be a broken run or
    # the wrong column, which a relative comparison would not notice.
    assert reference > 5000.0
    assert compared == pytest.approx(reference, rel=SpaceHeatingResolutionRuns.DELIVERED_HEAT_RELATIVE_TOLERANCE)
