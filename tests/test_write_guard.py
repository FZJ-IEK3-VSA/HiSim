"""Tests for the write guard (``hisim.write_guard``) and the calculation scope around it (bead hisim-epc.23).

The base tests exercise the guard directly: it refuses a write outside the calculation's result and
cache directories, naming the path and the line that wrote it; it allows writes below those
directories; it is off outside a calculation; and two calculations get two result directories even
when they start in the same second. The end-to-end test runs a one-day system setup through
``hisim_main.main`` with the guard enforcing, twice in one process, deleting the first run's result
directory before the second starts -- the contract a backend relies on.
"""

import os
import shutil
from pathlib import Path
from typing import Iterator

import pytest

from hisim import hisim_main, log
from hisim.calculation_scope import CalculationScope
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.result_path_provider import ResultPathProviderSingleton
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator
from hisim.write_guard import GuardMode, StrayWriteError, WriteGuard

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(name="clean_provider")
def fixture_clean_provider() -> Iterator[None]:
    """Start and end every test with a fresh result path provider and logger."""
    ResultPathProviderSingleton.reset()
    log.logger.reset()
    yield
    ResultPathProviderSingleton.reset()
    log.logger.reset()


@pytest.mark.base
def test_a_write_outside_is_refused_with_the_path_and_the_writing_line(tmp_path: Path, clean_provider: None) -> None:
    """The error names the path and this test's own line, which is the code that wrote."""
    del clean_provider
    stray = tmp_path / "elsewhere" / "stray.txt"
    stray.parent.mkdir()
    with pytest.raises(StrayWriteError) as refusal:
        with CalculationScope.open("test", run_directory=tmp_path / "run", mode=GuardMode.ENFORCE):
            with open(stray, "w", encoding="utf-8") as handle:
                handle.write("nope")
    message = str(refusal.value)
    assert str(stray) in message
    assert "test_write_guard.py" in message
    assert "test_a_write_outside_is_refused" in message
    assert not stray.exists()


@pytest.mark.base
def test_a_swallowed_refusal_still_fails_the_calculation(tmp_path: Path, clean_provider: None) -> None:
    """A ``try``/``except Exception`` between the writer and the run cannot hide a stray write."""
    del clean_provider
    stray = tmp_path / "stray.txt"
    with pytest.raises(StrayWriteError) as refusal:
        with CalculationScope.open("test", run_directory=tmp_path / "run", mode=GuardMode.ENFORCE):
            try:
                stray.write_text("nope", encoding="utf-8")
            except Exception:  # pylint: disable=broad-except  # what a careless writer does
                pass
    assert str(stray) in str(refusal.value)


@pytest.mark.base
def test_writes_below_the_result_and_cache_directories_are_allowed(tmp_path: Path, clean_provider: None) -> None:
    """Files, directories, renames and removals below the allowed directories all pass."""
    del clean_provider
    run = tmp_path / "run"
    cache = tmp_path / "cache"
    with CalculationScope.open(
        "test", run_directory=run, cache_directories=[str(cache)], mode=GuardMode.ENFORCE
    ) as guard:
        (run / "sub").mkdir()
        (run / "sub" / "a.txt").write_text("a", encoding="utf-8")
        os.replace(run / "sub" / "a.txt", run / "b.txt")
        cache.mkdir()
        (cache / "entry.cache").write_bytes(b"x")
        shutil.copy(cache / "entry.cache", run / "copy.cache")
        os.remove(run / "copy.cache")
        with open(os.devnull, "w", encoding="utf-8") as null:
            null.write("discarded")
        assert not guard.stray_writes
    assert (run / "b.txt").read_text(encoding="utf-8") == "a"


@pytest.mark.base
def test_collect_mode_lists_every_stray_write_at_the_end(tmp_path: Path, clean_provider: None) -> None:
    """In collect mode the body runs on and the error lists each stray path once."""
    del clean_provider
    first, second = tmp_path / "one.txt", tmp_path / "two.txt"
    with pytest.raises(StrayWriteError) as refusal:
        with CalculationScope.open("test", run_directory=tmp_path / "run", mode=GuardMode.COLLECT):
            first.write_text("1", encoding="utf-8")
            with open(first, "a", encoding="utf-8") as handle:
                handle.write("again")
            second.write_text("2", encoding="utf-8")
    paths = [write.path for write in refusal.value.stray_writes]
    assert paths == [str(first), str(second)]


@pytest.mark.base
def test_the_guard_is_off_outside_a_calculation(tmp_path: Path) -> None:
    """A plain unit test writes where it likes; only a running calculation is guarded."""
    assert WriteGuard.active() is None
    (tmp_path / "free.txt").write_text("free", encoding="utf-8")


@pytest.mark.base
def test_an_unknown_mode_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """``HISIM_WRITE_GUARD`` has no ``off``: a value it does not know is an error, not a switch."""
    monkeypatch.setenv(WriteGuard.MODE_VARIABLE, "off")
    with pytest.raises(ValueError, match=WriteGuard.MODE_VARIABLE):
        WriteGuard.mode_from_environment()


@pytest.mark.base
def test_two_calculations_get_two_result_directories(tmp_path: Path, clean_provider: None) -> None:
    """Two runs of the same module within the same second do not share a directory.

    The flat layout names the directory by the module and a timestamp to the second; the provider's
    claim creates it exclusively and appends a suffix on a collision.
    """
    del clean_provider
    directories = []
    for _ in range(2):
        with CalculationScope.open("test", mode=GuardMode.ENFORCE):
            parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
            simulator = Simulator(
                module_directory=str(tmp_path), module_filename="twin", my_simulation_parameters=parameters
            )
            simulator.prepare_simulation_directory()
            directories.append(simulator.simulation_parameters.result_directory)
            Path(directories[-1], "marker.txt").write_text("mine", encoding="utf-8")
    assert directories[0] != directories[1]
    assert all(os.path.isdir(directory) for directory in directories)
    # The provider does not carry the calculation's directory into the next one.
    assert ResultPathProviderSingleton().claimed_directory is None


@pytest.mark.base
def test_the_logger_writes_nothing_before_it_knows_the_result_directory(tmp_path: Path, clean_provider: None) -> None:
    """Messages before ``setup`` are buffered and written into the result directory, not ``../logs``."""
    del clean_provider
    with CalculationScope.open("test", run_directory=tmp_path / "run", mode=GuardMode.ENFORCE) as guard:
        log.information("before the result directory is known")
        assert not guard.stray_writes
        log.logger.setup(str(tmp_path / "run"))
        log.information("after")
    written = (tmp_path / "run" / "hisim_simulation.log").read_text(encoding="utf-8")
    assert "before the result directory is known" in written
    assert "after" in written


@pytest.mark.system_setups
def test_a_run_writes_only_into_its_result_and_cache_directories(monkeypatch: pytest.MonkeyPatch) -> None:
    """A one-day setup run twice with the guard enforcing; the first directory is deleted in between.

    Enforcing means any write outside the run's result directory and the cache directories raises
    inside ``hisim_main.main``; the second run starting after the first one's directory is gone shows
    that nothing a run needs lives there.
    """
    monkeypatch.setenv(WriteGuard.MODE_VARIABLE, GuardMode.ENFORCE.value)
    setup = str(REPO_ROOT / "system_setups" / "simple_system_setup_one.py")
    for _ in range(2):
        parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)
        parameters.post_processing_options = [PostProcessingOptions.COMPUTE_KPIS, PostProcessingOptions.EXPORT_TO_CSV]
        directory = hisim_main.main(setup, parameters)
        assert os.path.isfile(os.path.join(directory, "finished.flag"))
        # Deleted whole: the next run must not need anything in it. (Its name may come round again
        # when the next run starts within the same second -- it is free by then.)
        shutil.rmtree(directory)
