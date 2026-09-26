"""Tests for the write guard (``hisim.write_guard``) and the calculation scope around it (bead hisim-epc.23).

The base tests exercise the guard and the registry it reads (``CalculationDirectories``): the guard
refuses a write outside the calculation's result and cache directories, naming the path and the line
that wrote it; it allows writes below those directories, device files and read-only databases; it is
off outside a calculation; two calculations get two result directories even when they start in the
same second; one calculation gets one; and matplotlib's configuration lands in the cache (or the
result directory) on a machine with an empty home. The end-to-end test runs a one-day system setup through
``hisim_main.main`` with the guard enforcing, twice in one process, deleting the first run's result
directory before the second starts -- the contract a backend relies on.
"""

import datetime
import json
import os
import shutil
import sqlite3
import subprocess  # nosec B404 - the matplotlib test runs a fixed program in a fresh interpreter
import sys
import textwrap
from pathlib import Path
from typing import Iterator

import pytest

from hisim import hisim_main, log
from hisim import result_path_provider
from hisim.calculation_scope import CalculationScope
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.result_path_provider import ResultPathProviderSingleton
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator
from hisim.write_guard import CalculationDirectories, CalculationDirectoryError, GuardMode, StrayWriteError, WriteGuard

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


class _FrozenClock(datetime.datetime):
    """A ``datetime.datetime`` whose ``now`` is always the same second."""

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        """Return the one instant, whatever the time is."""
        del tz
        return cls(2026, 9, 26, 12, 0, 0)


def _simulator(directory: Path, name: str = "twin") -> Simulator:
    """A simulator of a one-day hourly run of the module *name* in *directory*, nothing added yet."""
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
    simulator: Simulator = Simulator(
        module_directory=str(directory), module_filename=name, my_simulation_parameters=parameters
    )
    return simulator


@pytest.mark.base
def test_two_calculations_get_two_result_directories(
    tmp_path: Path, clean_provider: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs of the same module within the same second do not share a directory.

    The flat layout names the directory by the module and a timestamp to the second; the claim
    creates it exclusively and appends a suffix on a collision. The clock is frozen, so the two
    runs collide every time rather than only when they happen to start in the same second.
    """
    del clean_provider
    monkeypatch.setattr(result_path_provider.datetime, "datetime", _FrozenClock)
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
    assert directories[1] == directories[0] + "_2"
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


@pytest.mark.base
def test_a_second_simulator_in_one_calculation_is_refused(tmp_path: Path, clean_provider: None) -> None:
    """One calculation, one result directory: a second simulator claiming its own is refused by name."""
    del clean_provider
    with CalculationScope.open("test", mode=GuardMode.ENFORCE):
        first = _simulator(tmp_path, "first")
        first.prepare_simulation_directory()
        # The same simulator may prepare again; it has its directory.
        first.prepare_simulation_directory()
        second = _simulator(tmp_path, "second")
        with pytest.raises(CalculationDirectoryError) as refusal:
            second.prepare_simulation_directory()
    message = str(refusal.value)
    assert first.describe_as_claimant() in message
    assert second.describe_as_claimant() in message
    assert str(first.simulation_parameters.result_directory) in message
    assert sorted(path.name for path in (tmp_path / "results").iterdir()) == [
        Path(first.simulation_parameters.result_directory).name
    ]


@pytest.mark.base
def test_two_simulators_may_share_a_directory_the_caller_adopted(tmp_path: Path, clean_provider: None) -> None:
    """A RenoVisor-style job directory is the caller's choice, not a claim the scope made."""
    del clean_provider
    run = tmp_path / "job"
    with CalculationScope.open("test", run_directory=run, mode=GuardMode.ENFORCE):
        first, second = _simulator(tmp_path, "first"), _simulator(tmp_path, "second")
        first.prepare_simulation_directory()
        second.prepare_simulation_directory()
    assert first.simulation_parameters.result_directory == second.simulation_parameters.result_directory == str(run)


@pytest.mark.base
def test_a_nested_scope_joins_the_running_calculation(tmp_path: Path, clean_provider: None) -> None:
    """A scope opened inside another registers its directory and resets neither the provider nor the logger."""
    del clean_provider
    outer, inner = tmp_path / "outer", tmp_path / "inner"
    with CalculationScope.open("outer", run_directory=outer, mode=GuardMode.ENFORCE) as guard:
        log.logger.setup(str(outer))
        with CalculationScope.open("inner", run_directory=inner, mode=GuardMode.ENFORCE) as nested:
            assert nested is guard
            assert ResultPathProviderSingleton().get_result_directory_name() == str(outer)
            assert log.logger.logging_path == str(outer)
            assert not log.logger.before_result_dir_created
            inner.mkdir()
            (inner / "inner.txt").write_text("allowed", encoding="utf-8")
        assert str(inner.resolve()) in guard.directories.result_directories
        assert guard.directories.result_directory() == str(outer.resolve())
        # The outer calculation goes on as it was.
        assert ResultPathProviderSingleton().get_result_directory_name() == str(outer)
        assert log.logger.logging_path == str(outer)
        assert WriteGuard.active() is guard


@pytest.mark.base
def test_a_result_directory_below_hisim_inputs_is_refused(tmp_path: Path, clean_provider: None) -> None:
    """The shipped data is nobody's output: a run pointed there fails when the directory is registered."""
    del clean_provider
    inputs = Path(CalculationDirectories.INPUTS_DIRECTORY)
    target = inputs / "a_result_directory_nobody_should_create"
    with pytest.raises(CalculationDirectoryError, match="hisim.inputs"):
        with CalculationScope.open("test", run_directory=target, mode=GuardMode.ENFORCE):
            pass
    assert not target.exists()
    with pytest.raises(CalculationDirectoryError, match="result_directory"):
        with CalculationScope.open("test", mode=GuardMode.ENFORCE):
            simulator = _simulator(tmp_path)
            simulator.simulation_parameters.result_directory = str(target)
            simulator.prepare_simulation_directory()
    assert not target.exists()


@pytest.mark.base
def test_device_files_are_allowed_and_the_rest_of_dev_is_not(tmp_path: Path, clean_provider: None) -> None:
    """``/dev/null`` is a device; ``/dev/shm`` is a directory like any other."""
    del clean_provider
    if not os.path.isdir("/dev/shm"):
        pytest.skip("no /dev/shm on this platform")
    stray = Path("/dev/shm") / f"hisim_write_guard_test_{os.getpid()}"
    with pytest.raises(StrayWriteError, match=str(stray)):
        with CalculationScope.open("test", run_directory=tmp_path / "run", mode=GuardMode.ENFORCE):
            with open(os.devnull, "w", encoding="utf-8") as null:
                null.write("discarded")
            stray.write_text("nope", encoding="utf-8")
    assert not stray.exists()


@pytest.mark.base
def test_an_existing_database_elsewhere_opens_read_only_only(tmp_path: Path, clean_provider: None) -> None:
    """``sqlite3.connect`` outside the directories is a stray write unless the URI says ``mode=ro``."""
    del clean_provider
    database = tmp_path / "elsewhere.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE t (x INTEGER)")
    connection.close()
    run = tmp_path / "run"
    with CalculationScope.open("test", run_directory=run, mode=GuardMode.ENFORCE):
        reader = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        assert reader.execute("SELECT count(*) FROM t").fetchone() == (0,)
        reader.close()
        own = sqlite3.connect(run / "own.sqlite")
        own.close()
    with pytest.raises(StrayWriteError, match="sqlite3.connect"):
        with CalculationScope.open("test", run_directory=run, mode=GuardMode.ENFORCE):
            sqlite3.connect(database).close()


@pytest.mark.base
def test_a_log_write_that_strays_raises_at_the_write(tmp_path: Path, clean_provider: None) -> None:
    """The logger catches ``OSError`` only, so the guard's refusal is not printed away."""
    del clean_provider
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(StrayWriteError):
        with CalculationScope.open("test", run_directory=tmp_path / "run", mode=GuardMode.ENFORCE):
            with pytest.raises(StrayWriteError, match="hisim_simulation.log"):
                log.information("where does this go", logging_message_path=str(elsewhere))
    assert not (elsewhere / "hisim_simulation.log").exists()


@pytest.mark.base
def test_the_mode_may_be_given_as_its_value(tmp_path: Path, clean_provider: None) -> None:
    """A mode spelled as a string is coerced, so ``"collect"`` collects rather than enforcing."""
    del clean_provider
    assert WriteGuard("test", CalculationDirectories(), mode="collect").mode is GuardMode.COLLECT
    stray = tmp_path / "stray.txt"
    with pytest.raises(StrayWriteError):
        with CalculationScope.open("test", run_directory=tmp_path / "run", mode="collect"):
            stray.write_text("collected, not raised", encoding="utf-8")
            reached = True
    assert reached


@pytest.mark.base
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes into read-only directories")
def test_matplotlib_goes_to_the_first_writable_cache_directory(tmp_path: Path) -> None:
    """A read-only seed listed first is skipped, as ``CacheLocations.write_directory`` skips it."""
    seed, volume = tmp_path / "seed", tmp_path / "volume"
    seed.mkdir()
    seed.chmod(0o555)
    try:
        directories = CalculationDirectories()
        directories.add_cache_directories([str(seed), str(volume)])
        assert directories.writable_cache_directory() == str(volume.resolve())
        assert CalculationDirectories().writable_cache_directory() is None
    finally:
        seed.chmod(0o755)


_MATPLOTLIB_PROGRAM = textwrap.dedent(
    """
    import json, os, sys
    from hisim.calculation_scope import CalculationScope
    run, caches = sys.argv[1], json.loads(sys.argv[2])
    with CalculationScope.open("matplotlib", run_directory=run, cache_directories=caches, mode="enforce"):
        import matplotlib
        import matplotlib.pyplot as plt
        figure = plt.figure()
        figure.savefig(os.path.join(run, "figure.png"))
        print(matplotlib.get_configdir())
    """
)


@pytest.mark.base
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes into read-only directories")
@pytest.mark.parametrize("with_cache", [False, True])
def test_matplotlib_imported_mid_calculation_writes_nothing_in_an_empty_home(tmp_path: Path, with_cache: bool) -> None:
    """A cold machine: an empty home, no XDG directories, matplotlib first imported inside the calculation.

    In a fresh interpreter, because this test process has imported matplotlib long ago. Without a
    cache directory (the economics CLI) matplotlib's configuration goes to the result directory; with
    a read-only seed and a volume it goes to the volume.
    """
    home, run = tmp_path / "home", tmp_path / "run"
    home.mkdir()
    seed, volume = tmp_path / "seed", tmp_path / "volume"
    seed.mkdir()
    seed.chmod(0o555)
    caches = [str(seed), str(volume)] if with_cache else []
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("MPLCONFIGDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "HISIM_CACHE_DIR", "HISIM_CACHE_SHARED_DIR")
    }
    environment["HOME"] = str(home)
    environment["PYTHONPATH"] = str(REPO_ROOT)
    try:
        completed = subprocess.run(  # nosec B603 - fixed argument vector, no shell
            [sys.executable, "-c", _MATPLOTLIB_PROGRAM, str(run), json.dumps(caches)],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
            cwd=str(tmp_path),
        )
    finally:
        seed.chmod(0o755)
    assert completed.returncode == 0, completed.stderr
    expected = (volume if with_cache else run).resolve() / "matplotlib"
    assert Path(completed.stdout.strip().splitlines()[-1]) == expected
    assert (run / "figure.png").is_file()
    assert not list(home.iterdir())
    assert not list(seed.iterdir())
