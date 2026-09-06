"""Tests for the KPI preparation arithmetic that no setup run pins down.

The KPI preparation normally exists only inside a finished post-processing run, so its edge
cases — above all the degenerate energy balances a setup can legitimately produce — are exactly
the branches an end-to-end test never steers into. The tests here build the preparation object
around its two load-bearing attributes and drive the computation directly, which is what lets a
branch like "production without any consumption" be exercised in milliseconds.

Each test states the failure mode it catches.
"""

# clean

from __future__ import annotations

import pandas as pd
import pytest

from hisim.postprocessing.kpi_computation.kpi_preparation import KpiPreparation
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass
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
