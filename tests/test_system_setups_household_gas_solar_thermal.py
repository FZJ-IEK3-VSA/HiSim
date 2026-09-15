"""Test for the household_gas_solar_thermal system setup.

This module contains a single integration test that loads the setup, runs it for one day with the
KPI and cost post-processing switched on, and checks that its electricity balance adds up.
"""

import json
from pathlib import Path
import pytest
from hisim import hisim_main
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters
from hisim import utils


class ElectricityBalance:
    """What the meter must report, and the one figure that cannot exist.

    The household has an occupancy and no electrical generation -- the solar collector is thermal --
    so everything it consumes comes from the grid, nothing goes back, and its self-sufficiency is
    zero. The self-consumption rate is the share of its own production that it uses, and with no
    production that is undefined rather than zero.

    ``RELATIVE_DEMAND_CEILING`` is the assertion that matters. This setup fed the occupancy into the
    electricity meter by hand *and* registered the meter with ``connect_automatically``, which
    applies the meter's own default connection for the same source; the simulator does not
    de-duplicate, so one source was summed into one meter twice and the grid import came out at
    roughly double the total consumption. The KPI layer noticed only because a demand above 100 %
    is impossible, and refused to continue. Pinning the ceiling keeps that shape of wiring mistake
    detectable here rather than as an opaque post-processing failure.
    """

    RELATIVE_DEMAND_CEILING = 100.0
    REQUIRED = (
        "Total electricity consumption",
        "Total energy from grid",
        "Relative electricity demand from grid",
        "Self-sufficiency rate according to solar htw berlin",
    )
    NOT_COMPUTABLE = ("Self-consumption rate according to solar htw berlin",)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_gas_solar_thermal() -> None:
    """Run the setup for one day and check the electricity balance is possible.

    There was no test for this setup at all, and it had been failing in post-processing for long
    enough to be parked as an unexplained KPI-layer bug. It is not one: the wiring counted the
    occupancy twice, and the KPI layer was the only thing that noticed.
    """
    path = Path("../system_setups/household_gas_solar_thermal.py")

    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    sim_params.post_processing_options = [
        PostProcessingOptions.COMPUTE_KPIS,
        PostProcessingOptions.WRITE_KPIS_TO_JSON,
        PostProcessingOptions.COMPUTE_CAPEX,
        PostProcessingOptions.COMPUTE_OPEX,
    ]
    result_directory = hisim_main.main(str(path), sim_params)

    result_path = Path(result_directory)
    assert (result_path / "finished.flag").is_file(), f"the run did not finish: {result_directory}"
    kpi_path = result_path / "all_kpis.json"
    assert kpi_path.is_file(), f"all_kpis.json not found in {result_directory}"

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

    for name in ElectricityBalance.REQUIRED:
        assert name in values_by_name, f"KPI '{name}' is missing from {kpi_path}"
        assert values_by_name[name] is not None, f"KPI '{name}' has no value"

    for name in ElectricityBalance.NOT_COMPUTABLE:
        assert values_by_name.get(name) is None, (
            f"KPI '{name}' carries a value, but this household generates no electricity, so the "
            "share of its own production that it consumes is undefined."
        )

    relative_demand = values_by_name["Relative electricity demand from grid"]
    assert relative_demand <= ElectricityBalance.RELATIVE_DEMAND_CEILING, (
        f"the grid supplied {relative_demand} % of a consumption it cannot exceed -- a source is "
        "very likely fed into the electricity meter twice, by hand and by default connection both."
    )

    assert values_by_name["Total energy from grid"] <= values_by_name["Total electricity consumption"], (
        "grid import exceeds total consumption, which is the same double-counting seen from the "
        "other side."
    )
