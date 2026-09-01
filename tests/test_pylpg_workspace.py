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

from hisim.components.pylpg_workspace import (
    LocalLpgCalculationFailedError,
    PylpgWorkingDirectoryInUseError,
    PylpgWorkspace,
)

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


@pytest.mark.base
def test_a_calculation_that_left_no_results_is_reported_as_a_calculation_failure(tmp_path: Any) -> None:
    """A missing result directory names the calculation, not an arbitrary json file.

    ``pylpg`` runs the LoadProfileGenerator with ``subprocess.run`` and neither passes
    ``check=True`` nor reads the return code, so a calculation that died returns looking like one
    that worked. Reading its outputs then fails on whichever file is opened first, which is how a
    dead calculation used to be reported as ``FileNotFoundError`` on
    ``.../BodilyActivityLevel.High.HH1.json`` with no mention of the generator.
    """
    with pytest.raises(LocalLpgCalculationFailedError) as failure:
        PylpgWorkspace.verify_results_were_produced(200, str(tmp_path / "never_created"))

    assert "200" in str(failure.value), "the message must name the calculation index"
    assert "produced no results" in str(failure.value)


@pytest.mark.base
def test_the_generators_own_log_is_quoted_into_the_failure(tmp_path: Any) -> None:
    """The reason a calculation failed is in the generator's log, so the error carries it.

    The log is deleted moments later by :meth:`PylpgWorkspace.release`, and on a CI runner the whole
    directory goes with the workspace, so a message that merely names the file is a message that
    tells a later reader nothing.
    """
    results = tmp_path / "results" / "Results"
    results.mkdir(parents=True)
    (results.parent / PylpgWorkspace.LOG_FILE_NAME).write_text(
        "Error: database is locked\n", encoding="utf-8"
    )

    with pytest.raises(LocalLpgCalculationFailedError) as failure:
        PylpgWorkspace.verify_results_were_produced(300, str(results))

    assert "database is locked" in str(failure.value), "the generator's own reason must be quoted"


@pytest.mark.base
def test_results_that_are_present_pass_silently(tmp_path: Any) -> None:
    """The check has to be invisible when the calculation worked, which is nearly always."""
    results = tmp_path / "results" / "Results"
    results.mkdir(parents=True)
    (results / "SumProfiles.HH1.Electricity.csv").write_text("x", encoding="utf-8")

    PylpgWorkspace.verify_results_were_produced(400, str(results))


@pytest.mark.base
def test_the_install_lock_admits_one_process_at_a_time() -> None:
    """Concurrent installs are what write an executable another process is running.

    ``LPGExecutor.__init__`` checks whether the binaries are on disk and extracts them if not, and
    those two steps are not atomic with respect to each other. Several processes starting together
    in a fresh environment all see them missing and all extract into the same directory; the first
    to finish executes the file the others are still writing, and the kernel refuses that with
    ``ETXTBSY``. The lock is what makes check-and-install atomic, so this asserts the only property
    that matters: two holders are never inside it at once.
    """
    import multiprocessing  # pylint: disable=import-outside-toplevel
    import os  # pylint: disable=import-outside-toplevel
    import time  # pylint: disable=import-outside-toplevel

    def hold(events: Any) -> None:
        with PylpgWorkspace._binary_install_lock():  # pylint: disable=protected-access
            events.append(("enter", os.getpid()))
            time.sleep(0.4)
            events.append(("leave", os.getpid()))

    with multiprocessing.Manager() as manager:
        events = manager.list()
        processes = [multiprocessing.Process(target=hold, args=(events,)) for _ in range(3)]
        for process in processes:
            process.start()
        for process in processes:
            process.join()
        recorded = list(events)

    assert len(recorded) == 6, f"every process must record an enter and a leave, got {recorded}"
    for first, second in zip(recorded, recorded[1:]):
        assert not (first[0] == "enter" and second[0] == "enter"), (
            f"two processes were inside the install lock at once: {recorded}"
        )
