"""The space-heating chain behind a buffer delivers the same heat to the building at 900 s as the blessed 60 s golden.

The space-heating buffer is a two-mode buffer (``roadmap/two_mode_buffer_spec.md``, decision 23 of
``roadmap/hydronic_part_load_spec.md``): at and below the part-load threshold (600 s) an iterated mixed node, above it
a start-of-step node. Above 600 s every circuit books its heat on the temperature the buffer starts the step at, and
the generator's charge and the heat distribution system's draw each run the fraction of the step that keeps the buffer
between the room and the heating curve's supply temperature (``hisim/part_load.py``, decisions 21 and 22). The owner
decided on 2026-10-16 (review of the stage-D spec, finding M-B, option a) what stage D is held to against the 60 s
reference, and kept it for the two-mode buffer:

* asserted: the heat the heat distribution system delivers to the building over the full year 2021 at 900 s,
  RenoVisor's step length, lies within 1 % of the 60 s value, for the four twins with a buffer: gas, pellets, heat
  pump, and heat pump with solar thermal;
* recorded, not asserted: the generator's booked space heat, the fuel and the electricity, and every 3600 s value
  (stage D's pull requests and ``roadmap/hydronic_coupling_spec.md`` §12 list them).

**The reference is the blessed golden, not a run** (owner, 2026-10-16). The test runs only the 900 s year and compares
its KPI "Thermal output energy of heat distribution system" with the same KPI of the twin's committed full-year
golden, ``golden_references/<twin>__full_year_60s.json``, which the golden gate already keeps equal to a 60 s run of
the same setup. Both are run as committed, with the local LoadProfileGenerator's occupancy, and the KPI counts the
steps in which the distribution system heats. Since the two-mode buffer's bless (owner decision Q5, 2026-10-18) the
goldens are the iterated buffer's 60 s values; measured on 2026-10-18, the four 900 s years lie within 1 % of them:
gas −0.16 %, pellets −0.04 %, heat pump −0.45 %, heat pump with solar thermal +0.15 %
(``SpaceHeatingResolutionRuns.MEASURED_AGAINST_ITERATED_60_S_GOLDENS``).

The comparison needs the year, not a window: the delivered heat at a coarse step drifts with the buffer's charge and
the room's comfort over the heating season, and a month would hide a shortfall the year shows. Each test runs one twin
at 900 s for a full year (18 to 41 s on a local machine with the occupancy profile cached; generating the profile
with the local LoadProfileGenerator adds about two to four minutes), so it is ``extendedbase``.

The week runs at the end of the module (:class:`SpaceHeatingWeekRuns`, ``system_setups``, about 35 s for all 22 runs
with the predefined occupancy) check that neither model of the buffer aborts at the step lengths it runs: the iterated
buffer at 300 and 600 s within 50 passes per step, the start-of-step buffer at 900, 1800 and 3600 s. Their pass
counts, forced steps and delivered heat against 60 s are recorded, not asserted (owner decision Q7).
"""

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Tuple

import pytest
import yaml

from hisim.calculation_scope import CalculationScope
from hisim.energy_system.executor import SimulationParametersReader, build_energy_system, run_energy_system
from hisim.postprocessing.kpi_computation.kpi_address import ALL_KPIS_FILE_NAME
from scripts.golden_kpis import golden_leaves, load_golden


class SpaceHeatingResolutionRuns:
    """The full year 2021 of a twin at 900 s, and the heat its distribution system delivered, from the KPI."""

    #: The repository's root.
    ROOT = Path(__file__).resolve().parents[1]

    #: The twins with a space-heating buffer whose delivered heat the owner holds stage D to.
    TWINS: Tuple[str, ...] = (
        "household_gas_building_sizer",
        "household_pellets_building_sizer",
        "household_heatpump_building_sizer",
        "household_heatpump_solar_thermal_building_sizer",
    )

    #: The window: the full year 2021, the window of the ``full_year_60s`` goldens.
    WINDOW: Tuple[str, str] = ("2021-01-01T00:00:00", "2022-01-01T00:00:00")

    #: The compared resolution, s: RenoVisor's.
    COMPARED_SECONDS: int = 900

    #: The golden parameter set the 900 s year is compared with.
    REFERENCE_PARAMETER_SET: str = "full_year_60s"

    #: The KPI that is the heat delivered to the building: the distribution system's heating output over the run,
    #: kWh (``HeatDistribution.get_component_kpi_entries``).
    DELIVERED_HEAT_KPI_NAME: str = "Thermal output energy of heat distribution system"

    #: The post-processing that writes the KPIs, as the golden parameter sets do.
    POST_PROCESSING_OPTIONS: Tuple[str, ...] = ("COMPUTE_KPIS", "WRITE_KPIS_TO_JSON")

    #: Relative tolerance of the delivered heat: the 1 % of ``system_docs/modelling_guide.md`` §3 for a coarse-step
    #: model against the 60 s reference, which the owner kept for stage D on 2026-10-16.
    DELIVERED_HEAT_RELATIVE_TOLERANCE: float = 0.01

    #: Measured 2026-10-18 on the two-mode buffer (T3), a record and not asserted: per twin the 900 s KPI, kWh, and
    #: its relative deviation (a fraction) from the golden blessed with the iterated buffer at 60 s (T2). The goldens
    #: themselves are gas 10766.6, pellets 10778.4, heat pump 11469.1, heat pump with solar thermal 11466.9 kWh. The
    #: 900 s runs are those of the start-of-step buffer, which the two-mode buffer leaves bit-equal.
    MEASURED_AGAINST_ITERATED_60_S_GOLDENS: ClassVar[Dict[str, Tuple[float, float]]] = {
        "household_gas_building_sizer": (10749.5, -0.0016),
        "household_pellets_building_sizer": (10774.0, -0.0004),
        "household_heatpump_building_sizer": (11417.5, -0.0045),
        "household_heatpump_solar_thermal_building_sizer": (11484.4, +0.0015),
    }

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
    def delivered_heat_leaf(cls, leaves: Mapping[str, Mapping[str, Any]], origin: str) -> Mapping[str, Any]:
        """The one leaf of building ``BUI1`` named :attr:`DELIVERED_HEAT_KPI_NAME`, in kWh.

        Raises:
            AssertionError: When there is not exactly one such leaf, or its unit is not kWh.
        """
        matches = [
            leaf
            for leaf in leaves.values()
            if leaf["name"] == cls.DELIVERED_HEAT_KPI_NAME and leaf["building"] == "BUI1"
        ]
        assert len(matches) == 1, (origin, cls.DELIVERED_HEAT_KPI_NAME, len(matches))
        assert matches[0]["unit"] == "kWh", (origin, matches[0]["unit"])
        return matches[0]

    @classmethod
    def golden_delivered_heat_in_kwh(cls, twin: str) -> float:
        """The delivered heat of the twin's committed full-year 60 s golden, kWh."""
        path = cls.ROOT / "golden_references" / f"{twin}__{cls.REFERENCE_PARAMETER_SET}.json"
        return float(cls.delivered_heat_leaf(load_golden(path), str(path))["value"])

    @classmethod
    def run(cls, twin: str, work: Path) -> float:
        """Run the twin as committed over :attr:`WINDOW` at 900 s in ``work``; return its delivered-heat KPI, kWh."""
        energy_system = cls.ROOT / "energy_systems" / f"{twin}.energy_system.yaml"
        parameters = work / "year.simulation.yaml"
        start, end = cls.WINDOW
        parameters.write_text(
            yaml.safe_dump(
                {
                    "start_date": start,
                    "end_date": end,
                    "seconds_per_timestep": cls.COMPARED_SECONDS,
                    "country": "DE",
                    "logging_level": 3,
                    "post_processing_options": list(cls.POST_PROCESSING_OPTIONS),
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        results = work / "results"
        run_energy_system(energy_system, parameters, result_directory=str(results))
        kpi_files = sorted(results.rglob(ALL_KPIS_FILE_NAME))
        assert len(kpi_files) == 1, kpi_files
        leaves = golden_leaves(json.loads(kpi_files[0].read_text(encoding="utf-8")))
        return float(cls.delivered_heat_leaf(leaves, str(kpi_files[0]))["value"])


@pytest.mark.parametrize("twin", SpaceHeatingResolutionRuns.twin_parameters())
def test_the_heat_delivered_to_the_building_at_900_s_is_within_1_percent_of_the_60_s_golden(
    twin: str, tmp_path: Path
) -> None:
    """The year's heat delivered at 900 s lies within 1 % of the twin's blessed full-year 60 s golden.

    Fails when the buffer's part-load mode no longer keeps a coarse step's space heating on the 60 s reference: a
    generator or distribution circuit that ran whole steps into the start-of-step buffer, a floor that let the buffer
    drain, or a comfort remainder that went missing would each move the heat delivered at 900 s away from 60 s.
    """
    reference = SpaceHeatingResolutionRuns.golden_delivered_heat_in_kwh(twin)
    compared = SpaceHeatingResolutionRuns.run(twin, tmp_path)
    # Every one of the four houses takes about 11,000 kWh a year; a reference far below it would be the wrong KPI or
    # a broken golden, which a relative comparison would not notice.
    assert reference > 5000.0
    assert compared == pytest.approx(reference, rel=SpaceHeatingResolutionRuns.DELIVERED_HEAT_RELATIVE_TOLERANCE)


class SpaceHeatingWeekRuns:
    """A week of a twin with a buffer at one step length, and how many passes its steps needed.

    The two-mode buffer (``roadmap/two_mode_buffer_spec.md`` §2, §7.1) iterates as a mixed node at and below 600 s,
    where a step's passes grow with the step's turnover of the buffer, and runs as a start-of-step node above it, where
    nothing the buffer publishes moves within a step. These runs show that neither aborts at the step lengths it runs:

    * the iterated buffer, the four twins with a buffer at 300 and 600 s, one January and one July week each: the
      largest pass count of any step is asserted below :attr:`ITERATED_LARGEST_PASS_COUNT` (measured at most 35 over
      the full year 2021 at 600 s);
    * the start-of-step buffer, the gas and heat-pump twins at 900, 1800 and 3600 s, one January week: they finish.

    Forced steps (a pass after the tenth, in which the controllers hold their decision) are recorded, not asserted
    (owner decision Q7): with the iterated buffer at 600 s about half of all steps are forced, which is intended.
    Every run replaces the load-profile generator by the shipped predefined profile, as the buffer's per-step checks
    do, so a week takes a few seconds.
    """

    #: The repository's root.
    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[1]

    #: The weeks, Monday to Monday, of the runs: one in the heating season and one outside it.
    WEEKS: ClassVar[Dict[str, Tuple[str, str]]] = {
        "january": ("2021-01-11T00:00:00", "2021-01-18T00:00:00"),
        "july": ("2021-07-12T00:00:00", "2021-07-19T00:00:00"),
    }

    #: The twins with a buffer that the iterated buffer is run in, one per generator kind of the golden gate.
    ITERATED_TWINS: ClassVar[Tuple[str, ...]] = SpaceHeatingResolutionRuns.TWINS

    #: The step lengths of the iterated buffer's runs, s: up to the default part-load threshold of 600 s.
    ITERATED_SECONDS: ClassVar[Tuple[int, ...]] = (300, 600)

    #: The twins the start-of-step buffer is run in at the coarse step lengths.
    START_OF_STEP_TWINS: ClassVar[Tuple[str, ...]] = (
        "household_gas_building_sizer",
        "household_heatpump_building_sizer",
    )

    #: The step lengths of the start-of-step buffer's runs, s: above the threshold.
    START_OF_STEP_SECONDS: ClassVar[Tuple[int, ...]] = (900, 1800, 3600)

    #: The asserted bound on any step's passes with the iterated buffer: the full year 2021 needed at most 35 at 600 s
    #: (gas twin) and 32 (heat-pump twin), so 50 leaves a margin and still catches a slower contraction.
    ITERATED_LARGEST_PASS_COUNT: ClassVar[int] = 50

    #: Measured on 2026-10-18, recorded and not asserted: per (twin, week, step length in s) the largest pass count of
    #: any step, the number of forced steps of the week, and the heat delivered to the building over the week against
    #: the same week at 60 s, a fraction (predefined occupancy, as the runs). A week has 2016 steps at 300 s, 1008 at
    #: 600 s, 672 at 900 s, 336 at 1800 s and 168 at 3600 s. The January weeks deliver 381 to 398 kWh; the July weeks
    #: only 7 to 51 kWh, so there a few kWh are several percent.
    MEASURED: ClassVar[Dict[Tuple[str, str, int], Tuple[int, int, float]]] = {
        ("household_gas_building_sizer", "january", 300): (21, 1559, -0.0002),
        ("household_gas_building_sizer", "january", 600): (35, 938, +0.0002),
        ("household_gas_building_sizer", "january", 900): (12, 1, -0.0015),
        ("household_gas_building_sizer", "january", 1800): (13, 1, -0.0021),
        ("household_gas_building_sizer", "january", 3600): (19, 24, -0.0130),
        ("household_gas_building_sizer", "july", 300): (17, 285, -0.0098),
        ("household_gas_building_sizer", "july", 600): (25, 192, -0.0180),
        ("household_heatpump_building_sizer", "january", 300): (23, 463, -0.0095),
        ("household_heatpump_building_sizer", "january", 600): (25, 817, -0.0104),
        ("household_heatpump_building_sizer", "january", 900): (27, 47, -0.0059),
        ("household_heatpump_building_sizer", "january", 1800): (41, 27, -0.0087),
        ("household_heatpump_building_sizer", "january", 3600): (84, 22, -0.0195),
        ("household_heatpump_building_sizer", "july", 300): (22, 116, -0.0703),
        ("household_heatpump_building_sizer", "july", 600): (26, 155, -0.0654),
        ("household_heatpump_solar_thermal_building_sizer", "january", 300): (21, 487, +0.0071),
        ("household_heatpump_solar_thermal_building_sizer", "january", 600): (25, 824, +0.0026),
        ("household_heatpump_solar_thermal_building_sizer", "july", 300): (21, 68, -0.0049),
        ("household_heatpump_solar_thermal_building_sizer", "july", 600): (23, 82, -0.0017),
        ("household_pellets_building_sizer", "january", 300): (15, 616, -0.0001),
        ("household_pellets_building_sizer", "january", 600): (21, 860, -0.0001),
        ("household_pellets_building_sizer", "july", 300): (15, 16, -0.0098),
        ("household_pellets_building_sizer", "july", 600): (21, 30, -0.0180),
    }

    @classmethod
    def iterated_parameters(cls) -> List[Any]:
        """One parameter per twin, step length and week of the iterated buffer."""
        return [
            pytest.param(twin, seconds, week, id=f"{twin}-{seconds}s-{week}")
            for twin in cls.ITERATED_TWINS
            for seconds in cls.ITERATED_SECONDS
            for week in cls.WEEKS
        ]

    @classmethod
    def start_of_step_parameters(cls) -> List[Any]:
        """One parameter per twin and step length of the start-of-step buffer, in the January week."""
        return [
            pytest.param(twin, seconds, "january", id=f"{twin}-{seconds}s-january")
            for twin in cls.START_OF_STEP_TWINS
            for seconds in cls.START_OF_STEP_SECONDS
        ]

    @classmethod
    def run(cls, twin: str, seconds_per_timestep: int, week: str, work: Path) -> List[Tuple[int, bool]]:
        """Run a twin over one of :attr:`WEEKS` at a step length; return every step's passes and whether it was forced.

        The passes are read from the simulator's own step function, which returns them per step; nothing is added to
        the simulator's loop.

        Args:
            twin: The twin's name, ``energy_systems/<twin>.energy_system.yaml``.
            seconds_per_timestep: The step length, s.
            week: A key of :attr:`WEEKS`.
            work: The directory to write the files and results into.

        Returns:
            Per step, the number of passes and whether the step was forced.
        """
        model = yaml.safe_load((cls.ROOT / "energy_systems" / f"{twin}.energy_system.yaml").read_text(encoding="utf-8"))
        for entry in model["components"].values():
            if str(entry.get("class", "")).endswith("UtspLpgConnector"):
                entry.setdefault("config", {})["data_acquisition_mode"] = "USE_PREDEFINED_PROFILE"
        energy_system = work / f"{twin}.energy_system.yaml"
        energy_system.write_text(yaml.safe_dump(model, sort_keys=False), encoding="utf-8")
        parameters_path = work / "week.simulation.yaml"
        start, end = cls.WEEKS[week]
        parameters_path.write_text(
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
        steps: List[Tuple[int, bool]] = []
        results_directory = work / "results"
        with CalculationScope.open(label=str(energy_system), run_directory=str(results_directory)):
            parameters = SimulationParametersReader.read(parameters_path)
            parameters.result_directory = str(results_directory)
            simulator = build_energy_system(energy_system, parameters, assembly_resolver=None).simulator
            step_function = simulator.process_one_timestep

            def counting_step(timestep: int, previous_stsv: Any) -> Any:
                """The simulator's step, noting its passes and its forcing."""
                result = step_function(timestep, previous_stsv)
                steps.append((result[1], result[2]))
                return result

            setattr(simulator, "process_one_timestep", counting_step)
            simulator.run_all_timesteps()
        return steps


@pytest.mark.system_setups
@pytest.mark.parametrize("twin, seconds_per_timestep, week", SpaceHeatingWeekRuns.iterated_parameters())
def test_the_iterated_buffer_finishes_a_week_at_300_and_600_s_within_50_passes_per_step(
    twin: str, seconds_per_timestep: int, week: str, tmp_path: Path
) -> None:
    """A January and a July week of each twin with a buffer at 300 and 600 s, where the buffer is an iterated node.

    Fails if a step aborts at the simulator's pass limit or needs 50 passes or more, as a contraction slower than the
    measured one would. A controller that reads the step mean instead of the start temperature does not fail it: the
    forced passes hold its decision (measured: the gas and heat-pump twins rewired so still need at most 32 and 31
    passes in the January week at 600 s); ``tests/test_space_heating_buffer_sensor_port.py`` checks that wiring.
    """
    steps = SpaceHeatingWeekRuns.run(twin, seconds_per_timestep, week, tmp_path)
    start, end = SpaceHeatingWeekRuns.WEEKS[week]
    assert len(steps) == 7 * 86400 // seconds_per_timestep, (start, end, len(steps))
    largest_pass_count = max(passes for passes, _ in steps)
    assert largest_pass_count < SpaceHeatingWeekRuns.ITERATED_LARGEST_PASS_COUNT, largest_pass_count


@pytest.mark.system_setups
@pytest.mark.parametrize("twin, seconds_per_timestep, week", SpaceHeatingWeekRuns.start_of_step_parameters())
def test_the_start_of_step_buffer_finishes_a_january_week_at_900_to_3600_s(
    twin: str, seconds_per_timestep: int, week: str, tmp_path: Path
) -> None:
    """A January week of the gas and the heat-pump twin at 900, 1800 and 3600 s, the start-of-step buffer.

    Fails if a step aborts at the simulator's pass limit (1500 passes at 900 s, 6000 at 3600 s), which the simulator
    raises within the run, or if the week ends early; the largest pass counts are recorded in
    :attr:`SpaceHeatingWeekRuns.MEASURED`, not asserted.
    """
    steps = SpaceHeatingWeekRuns.run(twin, seconds_per_timestep, week, tmp_path)
    assert len(steps) == 7 * 86400 // seconds_per_timestep
