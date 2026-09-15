"""Test for the basic_household_only_heating system setup.

This module contains a single integration test that loads the
basic_household_only_heating.py setup, runs it for one day with the KPI and
cost post-processing switched on, and verifies that a complete KPI collection
comes out the other side.
"""

import json
from pathlib import Path
import pytest
from hisim import hisim_main
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils


class ExpectedKpis:
    """The KPI names this setup must produce, and the one it must not.

    ``REQUIRED`` is the end of the electricity-balance chain in
    ``kpi_preparation.py``: grid exchange comes from the electricity meter, the
    relative demand is derived from it, and self-sufficiency from that in turn.
    A setup with no electricity meter yields ``None`` for every one of them, so
    asserting they carry numbers is what distinguishes a setup that is genuinely
    metered from one that merely does not crash.

    ``NOT_COMPUTABLE`` is the counterpart. This household has no generation at
    all, so the share of its own production that it consumes is undefined rather
    than zero, and ``compute_self_consumption_rate_according_to_solar_htw_berlin``
    correctly returns ``None`` for it. Pinning that keeps the distinction honest:
    a ``None`` here is the right answer, and a number would mean the KPI layer had
    invented one.
    """

    REQUIRED = (
        "Total energy from grid",
        "Total electricity consumption",
        "Relative electricity demand from grid",
        "Self-sufficiency rate according to solar htw berlin",
        "Total gas consumption",
    )
    NOT_COMPUTABLE = ("Self-consumption rate according to solar htw berlin",)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_basic_household_only_heating() -> None:
    """Run the basic household only heating setup for one day and check its KPIs.

    Loads the system setup from ../system_setups/basic_household_only_heating.py
    and executes a one-day simulation at 60-second timesteps with
    ``COMPUTE_KPIS``, ``WRITE_KPIS_TO_JSON``, ``COMPUTE_CAPEX`` and
    ``COMPUTE_OPEX`` enabled.

    The post-processing options are the point of this test. Its previous version
    ran with the defaults, which leave KPI computation switched off, so it passed
    for as long as the setup was broken: the failure lived entirely in
    post-processing and nothing here reached it. Enabling the options means the
    test exercises the path that actually breaks.

    ``finished.flag`` is written by ``Simulator.run_all_timesteps`` only after
    post-processing has completed, so its presence separates a real run from a
    silent no-op, and ``all_kpis.json`` must then agree with
    :class:`ExpectedKpis` about which figures exist and which cannot.
    """
    path = Path("../system_setups/basic_household_only_heating.py")

    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    sim_params.post_processing_options = [
        PostProcessingOptions.COMPUTE_KPIS,
        PostProcessingOptions.WRITE_KPIS_TO_JSON,
        PostProcessingOptions.COMPUTE_CAPEX,
        PostProcessingOptions.COMPUTE_OPEX,
    ]
    result_directory = hisim_main.main(str(path), sim_params)
    log.information(str(Path.cwd()))

    result_path = Path(result_directory)
    assert result_directory, "no result directory was configured for the run"
    assert result_path.is_dir(), f"result directory does not exist: {result_directory}"
    assert any(result_path.iterdir()), f"result directory is empty: {result_directory}"
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

    for name in ExpectedKpis.REQUIRED:
        assert name in values_by_name, f"KPI '{name}' is missing from {kpi_path}"
        assert values_by_name[name] is not None, (
            f"KPI '{name}' has no value -- the energy balance could not be computed, "
            "which usually means the setup is missing a meter."
        )

    for name in ExpectedKpis.NOT_COMPUTABLE:
        assert name in values_by_name, f"KPI '{name}' is missing from {kpi_path}"
        assert values_by_name[name] is None, (
            f"KPI '{name}' carries a value, but this household has no generation at all, "
            "so the share of its own production that it consumes is undefined."
        )
