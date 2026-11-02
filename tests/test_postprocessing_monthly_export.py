"""The monthly results are exported whether the results go into one file or one file per output."""

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from hisim.postprocessing import postprocessing_main
from hisim.postprocessingoptions import PostProcessingOptions


@pytest.mark.base
def test_monthly_results_are_exported_into_one_file_too(tmp_path: Path) -> None:
    """EXPORT_MONTHLY_RESULTS beside EXPORT_RESULTS_IN_ONE_FILE writes ``all_results_monthly.csv``.

    The one-file branch wrote only ``all_results.csv``, so the monthly option did nothing there.
    """
    index = pd.date_range("2021-01-01", periods=3, freq="D")
    monthly_index = pd.date_range("2021-01-31", periods=1, freq="ME")
    ppdt = SimpleNamespace(
        results=pd.DataFrame({"A - x [Any - W]": [1.0, 2.0, 3.0]}, index=index),
        results_monthly=pd.DataFrame({"A - x [Any - W]": [2.0]}, index=monthly_index),
        post_processing_options=[
            PostProcessingOptions.EXPORT_TO_CSV,
            PostProcessingOptions.EXPORT_RESULTS_IN_ONE_FILE,
            PostProcessingOptions.EXPORT_MONTHLY_RESULTS,
        ],
        simulation_parameters=SimpleNamespace(result_directory=str(tmp_path)),
    )
    postprocessing_main.PostProcessor().export_results_to_csv(ppdt)  # type: ignore[arg-type]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["all_results.csv", "all_results_monthly.csv"]
    assert pd.read_csv(tmp_path / "all_results_monthly.csv", index_col=0).iloc[0, 0] == 2.0
