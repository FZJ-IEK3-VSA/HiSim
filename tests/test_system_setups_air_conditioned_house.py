"""Test for the air_conditioned_house system setup.

This module contains a single integration test that loads the
air_conditioned_house.py setup, runs it for one day with the KPI and cost
post-processing switched on, and verifies that a complete KPI collection comes
out the other side.
"""

import json
from pathlib import Path
import pytest
from hisim import hisim_main
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters
from hisim import utils


class ExpectedKpis:
    """The KPI names this setup must produce with a value.

    These four are the end of the electricity-balance chain in
    ``kpi_preparation.py``: grid exchange comes from the electricity meter, the
    relative demand is derived from it, and both self-sufficiency figures are
    derived from that in turn. A setup with no electricity meter yields ``None``
    for every one of them, so asserting they carry numbers is what distinguishes
    a setup that is genuinely wired from one that merely does not crash.
    """

    NAMES = (
        "Total energy from grid",
        "Relative electricity demand from grid",
        "Self-sufficiency rate according to solar htw berlin",
        "Self-consumption rate according to solar htw berlin",
    )


@pytest.mark.system_setups
@utils.measure_execution_time
def test_air_conditioned_house() -> None:
    """Run the air-conditioned house for one day and check its KPIs are complete.

    Loads the system setup from ../system_setups/air_conditioned_house.py and
    executes a one-day simulation at 60-second timesteps with ``COMPUTE_KPIS``,
    ``WRITE_KPIS_TO_JSON``, ``COMPUTE_CAPEX`` and ``COMPUTE_OPEX`` enabled.

    The post-processing options matter more than the run itself. A setup can
    complete every timestep and still fail the moment KPIs are computed -- which
    is exactly how this one was broken for as long as it was, since the default
    options leave KPI computation switched off and the failure therefore stayed
    invisible to a test that only asserted the simulation finished. Enabling
    them here means the test exercises the path that actually breaks.

    ``finished.flag`` is written by ``Simulator.run_all_timesteps`` only after
    post-processing has completed, so its presence separates a real run from a
    silent no-op, and ``all_kpis.json`` must then contain a value for every KPI
    in :class:`ExpectedKpis`.
    """
    path = Path("../system_setups/air_conditioned_house.py")

    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    sim_params.post_processing_options = [
        PostProcessingOptions.COMPUTE_KPIS,
        PostProcessingOptions.WRITE_KPIS_TO_JSON,
        PostProcessingOptions.COMPUTE_CAPEX,
        PostProcessingOptions.COMPUTE_OPEX,
    ]
    result_directory = hisim_main.main(str(path), sim_params)

    result_path = Path(result_directory)
    assert result_directory, "no result directory was configured for the run"
    assert result_path.is_dir(), f"result directory does not exist: {result_directory}"
    assert (result_path / "finished.flag").is_file(), (
        f"finished.flag not found in result directory: {result_directory}"
    )

    kpi_path = result_path / "all_kpis.json"
    assert kpi_path.is_file(), f"all_kpis.json not found in result directory: {result_directory}"

    # Flatten the nested collection to {kpi name: value}; the nesting is by building
    # object and tag, neither of which this test cares about.
    values_by_name: dict = {}

    def collect(node: object) -> None:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if isinstance(value, dict) and "value" in value and "unit" in value:
                values_by_name[key] = value["value"]
            else:
                collect(value)

    collect(json.loads(kpi_path.read_text(encoding="utf-8")))

    for name in ExpectedKpis.NAMES:
        assert name in values_by_name, f"KPI '{name}' is missing from {kpi_path}"
        assert values_by_name[name] is not None, (
            f"KPI '{name}' has no value -- the electricity balance could not be computed, "
            "which usually means the setup has no electricity meter."
        )
