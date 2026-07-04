"""Test for project code overview generator."""
# clean
from pathlib import Path

import pytest
from openpyxl import load_workbook

from hisim import utils
from hisim.project_code_overview_generator import OverviewGenerator


@pytest.mark.extendedbase
@utils.measure_execution_time
def test_project_code_overview_generator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that the project code overview generator produces its output.

    The :class:`OverviewGenerator` walks the HiSim source tree, collects
    information about every Python module and writes the result into an Excel
    workbook named ``components_information.xlsx``. Running it must therefore
    produce a non-empty workbook with the expected ``HiSim Files`` sheet.
    ``write_clean_files`` additionally emits the flake8/prospector call files
    into the parent of the working directory.

    ``run()`` writes its artifacts relative to the current working directory,
    so the test runs from a dedicated sub-folder of ``tmp_path``.  This keeps
    every artifact inside ``tmp_path`` (the ``../`` files land in ``tmp_path``
    itself) and avoids leaving calculation artifacts in the repository.
    """
    work_dir = tmp_path / "overview_work"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)

    overview_generator = OverviewGenerator()
    overview_generator.run()

    # Primary artifact: the Excel overview of all HiSim modules.
    expected_output = work_dir / "components_information.xlsx"
    assert expected_output.exists()
    assert expected_output.stat().st_size > 0

    workbook = load_workbook(filename=expected_output)
    assert "HiSim Files" in workbook.sheetnames
    worksheet = workbook["HiSim Files"]
    # The generator writes one block per source file; the repository contains
    # many modules, so the sheet must hold real tabular data.
    assert worksheet.max_row > 1
    assert worksheet.max_column > 1
    # The first column of the first row is the module name (a .py file name).
    first_module = worksheet.cell(row=1, column=1).value
    assert isinstance(first_module, str)
    assert first_module.endswith(".py")

    # Secondary artifacts written by write_clean_files into the parent dir.
    assert (tmp_path / "flake8_calls.txt").exists()
    assert (tmp_path / "prospector_calls.txt").exists()
    assert (tmp_path / "prospector_mass_call.cmd").exists()
