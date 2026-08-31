"""The index scheme that keeps two local-LPG runs out of each other's working directory.

``pylpg`` computes inside its own installed package, in a directory named after the calculation
index, so the index is the only isolation available. HiSim defaulted it to ``1`` and the
multi-household request restarted its own counter at ``1``, which put every concurrent run in
``pylpg/C1``: sqlite files corrupted each other and a finishing run deleted the folder a running one
was still using. These tests pin the replacement rule -- distinct base indices give disjoint blocks
of directories, an occupied directory stops the run by name, and a run releases what it claimed
whether it succeeded or failed.

None of them touch pylpg or start a calculation; they exercise arithmetic and one filesystem check,
which is why they are ``base`` rather than ``utsp``.
"""

# clean

import pathlib
from typing import Any

import pytest

from hisim.components.pylpg_workspace import PylpgWorkingDirectoryInUseError, PylpgWorkspace

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


@pytest.mark.base
def test_the_default_base_index_is_the_process_when_nothing_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no override the base index is the process id, which no two live runs share.

    The point of the change is that the default is no longer a constant. Any process-derived value
    would do; the process id is chosen because the kernel already guarantees it is unique among
    running processes, so concurrent runs separate without coordinating.
    """
    monkeypatch.delenv(PylpgWorkspace.INDEX_ENVIRONMENT_VARIABLE, raising=False)
    assert PylpgWorkspace.default_base_index() > 0


@pytest.mark.base
def test_an_explicit_base_index_overrides_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller that allocates indices itself keeps control of them.

    The parallel scenario regenerator hands each of its workers a small index of its own, which is
    the one place in the repository that got the working-directory problem right. That has to keep
    working, so the environment variable still wins over the derivation.
    """
    monkeypatch.setenv(PylpgWorkspace.INDEX_ENVIRONMENT_VARIABLE, "17")
    assert PylpgWorkspace.default_base_index() == 17


@pytest.mark.base
def test_two_base_indices_never_derive_the_same_calculation_index() -> None:
    """Blocks derived from different base indices are disjoint, which is the whole guarantee.

    Numbering a multi-household request consecutively from the base would collide immediately with a
    process-derived base, because process ids are handed out in sequence and two runs started back
    to back would differ by one. The stride is what prevents that, so the disjointness is asserted
    across the full width of a block rather than on a couple of examples.
    """
    first_block = {
        PylpgWorkspace.calculation_index(4242, ordinal) for ordinal in range(PylpgWorkspace.HOUSEHOLDS_PER_BASE_INDEX)
    }
    second_block = {
        PylpgWorkspace.calculation_index(4243, ordinal) for ordinal in range(PylpgWorkspace.HOUSEHOLDS_PER_BASE_INDEX)
    }
    assert len(first_block) == PylpgWorkspace.HOUSEHOLDS_PER_BASE_INDEX
    assert first_block.isdisjoint(second_block)


@pytest.mark.base
def test_a_request_wider_than_the_stride_is_refused() -> None:
    """A request that would spill out of its block fails rather than quietly overlapping the next.

    The stride bounds how many households one request can hold. Exceeding it silently would restore
    exactly the defect the stride exists to prevent, so the arithmetic refuses instead.
    """
    with pytest.raises(ValueError):
        PylpgWorkspace.calculation_index(1, PylpgWorkspace.HOUSEHOLDS_PER_BASE_INDEX)


@pytest.mark.base
def test_claiming_a_free_directory_returns_a_path_that_does_not_exist_yet() -> None:
    """A claim on a free index yields the path pylpg will create, and creates nothing itself.

    The claim is a check, not a reservation: ``pylpg`` makes the directory when it starts, and
    creating it here would make the very next claim of the same index fail.
    """
    directory = PylpgWorkspace.claim(PylpgWorkspace.calculation_index(987654321, 0))
    assert not directory.exists()
    assert directory.name.startswith("C")


@pytest.mark.base
def test_claiming_an_occupied_directory_fails_and_names_the_index(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """An occupied directory stops the run immediately with a message a reader can act on.

    Letting the calculation start would either destroy a run that is still going -- ``LPGExecutor``
    clears whatever it finds -- or fail much later with ``Directory not empty``, which is how a
    development box degraded over a session. The message therefore has to say which index collided
    and what the two possible causes are.
    """
    occupied_index = PylpgWorkspace.calculation_index(555, 0)

    def working_directory_in_tmp(calculation_index: int) -> pathlib.Path:
        return pathlib.Path(tmp_path) / f"C{calculation_index}"

    monkeypatch.setattr(PylpgWorkspace, "working_directory", staticmethod(working_directory_in_tmp))
    working_directory_in_tmp(occupied_index).mkdir()

    with pytest.raises(PylpgWorkingDirectoryInUseError) as failure:
        PylpgWorkspace.claim(occupied_index)

    assert str(occupied_index) in str(failure.value)
    assert PylpgWorkspace.INDEX_ENVIRONMENT_VARIABLE in str(failure.value)


@pytest.mark.base
def test_release_removes_the_directories_a_run_claimed(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Cleanup is driven by the claimed indices, so it works for a run that never got a result folder.

    The old cleanup deleted the parent of the result folder the calculation returned, and skipped
    itself entirely when the run failed before producing one. That left the directory on disk and the
    next run failed on ``Directory not empty`` before it started, which is how one failure poisoned
    the next. Indices are known before the attempt begins, so releasing by index covers both
    outcomes; an index whose directory is already gone is skipped rather than raising, because this
    runs in a ``finally`` where an exception would mask the real failure.
    """

    def working_directory_in_tmp(calculation_index: int) -> pathlib.Path:
        return pathlib.Path(tmp_path) / f"C{calculation_index}"

    monkeypatch.setattr(PylpgWorkspace, "working_directory", staticmethod(working_directory_in_tmp))
    claimed = [PylpgWorkspace.calculation_index(31, ordinal) for ordinal in range(2)]
    working_directory_in_tmp(claimed[0]).mkdir()
    (working_directory_in_tmp(claimed[0]) / "results").mkdir()

    PylpgWorkspace.release(claimed)

    assert not working_directory_in_tmp(claimed[0]).exists()
    assert not working_directory_in_tmp(claimed[1]).exists()
