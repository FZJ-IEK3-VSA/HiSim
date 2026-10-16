"""The space-heating chain behind a buffer delivers the same heat to the building at 900 s as the blessed 60 s golden.

Since hydronic stage D the space-heating buffer is a start-of-step node: every circuit books its heat on the
temperature the buffer starts the step at, and above 600 s the generator's charge and the heat distribution system's
draw each run the fraction of the step that keeps the buffer between the room and the heating curve's supply
temperature (``hisim/part_load.py``, decisions 21 and 22 of ``roadmap/hydronic_part_load_spec.md``). The owner decided
on 2026-10-16 (review of the stage-D spec, finding M-B, option a) what stage D is held to against the 60 s reference:

* asserted: the heat the heat distribution system delivers to the building over the full year 2021 at 900 s,
  RenoVisor's step length, lies within 1 % of the 60 s value, for the four twins with a buffer: gas, pellets, heat
  pump, and heat pump with solar thermal;
* recorded, not asserted: the generator's booked space heat, the fuel and the electricity, and every 3600 s value
  (stage D's pull requests and ``roadmap/hydronic_coupling_spec.md`` §12 list them).

**The reference is the blessed golden, not a run** (owner, 2026-10-16). The test runs only the 900 s year and compares
its KPI "Thermal output energy of heat distribution system" with the same KPI of the twin's committed full-year
golden, ``golden_references/<twin>__full_year_60s.json``, which the golden gate already keeps equal to a 60 s run of
the same setup. Both are run as committed, with the local LoadProfileGenerator's occupancy, and the KPI counts the
steps in which the distribution system heats. So the test holds stage D to 60 s once stage D's goldens are re-blessed
(golden-update workflow). Until then the goldens are the stage-D base's 60 s values; measured on 2026-10-16, the four
900 s years lie within 1 % of those too: gas −0.19 %, pellets −0.04 %, heat pump −0.74 %, heat pump with solar
thermal −0.84 % (``SpaceHeatingResolutionRuns.MEASURED_AGAINST_PRE_STAGE_D_GOLDENS``).

The comparison needs the year, not a window: the delivered heat at a coarse step drifts with the buffer's charge and
the room's comfort over the heating season, and a month would hide a shortfall the year shows. Each test runs one twin
at 900 s for a full year (18 to 41 s on a local machine with the occupancy profile cached; generating the profile
with the local LoadProfileGenerator adds about two to four minutes), so it is ``extendedbase``.
"""

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Tuple

import pytest
import yaml

from hisim.energy_system.executor import run_energy_system
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

    #: Measured 2026-10-16 on stage D's close-out, a record and not asserted: the 900 s KPI, kWh, and its relative
    #: deviation (a fraction) from the goldens committed before stage D's re-bless (the stage-D base's 60 s values).
    MEASURED_AGAINST_PRE_STAGE_D_GOLDENS: ClassVar[Dict[str, Tuple[float, float]]] = {
        "household_gas_building_sizer": (10749.5, -0.0019),
        "household_pellets_building_sizer": (10774.0, -0.0004),
        "household_heatpump_building_sizer": (11382.2, -0.0074),
        "household_heatpump_solar_thermal_building_sizer": (11353.8, -0.0084),
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
