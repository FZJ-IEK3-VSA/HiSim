"""End-to-end test of the RenoVisor translator pipeline: translate -> simulate -> collect (no upload).

Runs a real full simulation of the example-1 baseline (gas setup), so it carries the
``system_setups`` marker like the other slow setup tests.
"""

import json
from pathlib import Path

import pytest

from hisim.renovisor.__main__ import EXIT_SUCCESS, main

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "hisim" / "renovisor" / "examples"


@pytest.mark.system_setups
def test_full_pipeline_base_variant_no_upload(tmp_path: Path) -> None:
    """The CLI runs example 1 (base variant) end to end and produces KPIs + mapping report."""
    request_file = EXAMPLES_DIR / "example_1_gas_to_heatpump_pv_insulation.json"
    result_dir = tmp_path / "results"

    exit_code = main(
        [
            "run",
            str(request_file),
            "--variant",
            "base",
            "--result-dir",
            str(result_dir),
            "--no-upload",
        ]
    )

    assert exit_code == EXIT_SUCCESS
    report_path = result_dir / "renovisor_mapping_report.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["selectedSetup"] == "household_gas_building_sizer.py"
    assert (result_dir / "all_kpis.json").is_file()
    kpi_files = list(result_dir.glob("*_kpi_config_for_building_sizer.json"))
    assert kpi_files, "expected the building-sizer KPI JSON in the result directory"
