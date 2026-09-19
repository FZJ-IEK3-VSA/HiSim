"""The shared simulation-parameters library: every file loads, is named for itself, and is unique.

``simulation_parameters/`` has two authors. A person writes a file into it to offer a run shape
the repository did not have; the recorder writes one when it records a setup whose parameters no
file there already describes. Nothing coordinates the two beyond the rules checked here, and each
of the three has cost something before:

  - a file naming an option that no longer exists loads as a ``KeyError`` far from the file
    (``system_setups/2021_minutely_full.simulation.json`` did exactly that for three months);
  - a file whose name does not follow the derivation invites a second file, generated under the
    derived name, that says the same thing under a different one;
  - two files that *do* say the same thing make a recording's reference arbitrary, which is what
    ``scripts/record_all_setups.py --check`` refuses fleet-wide and what is pinned here per file.

The library is small and read by hand, so the tests read it the same way: they load every file
that is in the directory rather than a list of names, and a new file is covered the moment it is
committed.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, List

import pytest

from hisim.energy_system.executor import SimulationParametersReader
from hisim.energy_system.parameters_format import (
    ParameterFileName,
    ParameterFileWriter,
    ParameterNormalisation,
)
from hisim.postprocessingoptions import PostProcessingOptions


class Library:
    """Where the files are and how they are read, stated once for every test below."""

    #: The directory itself.
    DIRECTORY: ClassVar[Path] = Path(__file__).resolve().parent.parent / "simulation_parameters"

    #: Every parameter file in it, in a stable order.
    @classmethod
    def files(cls) -> List[Path]:
        """Returns every committed parameter file of the library, sorted by name.

        Returns:
            The files; never empty, since an empty library would make every test below vacuous.
        """
        found = sorted(cls.DIRECTORY.glob(f"*{ParameterFileName.SUFFIX}"))
        assert found, f"no parameter files in {cls.DIRECTORY}"
        return found


def library_files() -> List[Path]:
    """Collects the library's files for parametrisation.

    Returns:
        The files, so that each one is its own test case and a failure names it.
    """
    return Library.files()


@pytest.mark.base
@pytest.mark.parametrize("path", library_files(), ids=lambda path: path.name)
def test_a_library_file_loads_and_names_only_live_options(path: Path) -> None:
    """Catches a parameter file that cannot be used, or that names an option that no longer is.

    An option that has been retired leaves the name behind in every file that asked for it, and
    the Python reader resolves names with ``PostProcessingOptions[name]`` — a ``KeyError`` naming
    the string, raised wherever the file is loaded rather than where it is wrong.
    """
    parameters = SimulationParametersReader.read(path)

    assert parameters.end_date > parameters.start_date
    assert parameters.seconds_per_timestep > 0
    for option in parameters.post_processing_options:
        assert isinstance(PostProcessingOptions(option), PostProcessingOptions)


@pytest.mark.base
@pytest.mark.parametrize("path", library_files(), ids=lambda path: path.name)
def test_a_library_file_is_named_for_what_is_in_it(path: Path) -> None:
    """Catches a hand-written file whose name the recorder would not have given it.

    The recorder derives the name of a file it writes from the content, so a curated file under
    some other name is an invitation to a second file saying the same thing under the derived one.
    Both would then be in the library, and which one a recording referenced would depend on which
    was found first.
    """
    normalised = ParameterNormalisation.normalise(SimulationParametersReader.read(path))

    assert path.name == f"{ParameterFileName.stem(normalised)}{ParameterFileName.SUFFIX}"


@pytest.mark.base
@pytest.mark.parametrize("path", library_files(), ids=lambda path: path.name)
def test_a_library_file_is_exactly_what_the_writer_would_write(path: Path) -> None:
    """Catches a file edited into a shape the writer does not produce.

    The writer is what a generated file goes through, and a curated file that differs from it —
    an unquoted country, an unsorted option list, a key the reader ignores — would survive here
    and then be silently rewritten the first time the recorder touched the same parameter set.
    """
    normalised = ParameterNormalisation.normalise(SimulationParametersReader.read(path))

    assert path.read_text(encoding="utf-8") == ParameterFileWriter.text(normalised)


@pytest.mark.base
def test_no_two_library_files_describe_the_same_run() -> None:
    """Catches the duplicate that would make a recording's choice of reference arbitrary.

    The fleet check refuses this across the whole directory; this test says the same thing about
    the committed files alone, so a duplicate fails in seconds rather than after a fleet-wide
    re-recording.
    """
    seen: dict = {}
    for path in Library.files():
        normalised = ParameterNormalisation.normalise(SimulationParametersReader.read(path))
        key = repr(sorted(normalised.items(), key=lambda item: item[0]))
        assert key not in seen, f"{path.name} and {seen[key]} describe the same run"
        seen[key] = path.name


@pytest.mark.base
def test_the_library_covers_the_run_shapes_the_repository_talks_about() -> None:
    """Catches the library losing one of the shapes its README and its consumers name.

    Not every file is load-bearing, but four are: the one-day export pair every recording is made
    with, the golden gate's week, the building sizer's year and the full-year production run. A
    library that lost one of them would send a reader back to writing a file by hand, which is
    what it exists to prevent.
    """
    names = {path.name for path in Library.files()}

    assert "one_day_15min_export.simulation.yaml" in names
    assert "one_week_minutely_kpis_costs.simulation.yaml" in names
    assert "2021_15min_kpis_costs_scenarios.simulation.yaml" in names
    assert "2021_minutely_kpis_costs_export.simulation.yaml" in names
