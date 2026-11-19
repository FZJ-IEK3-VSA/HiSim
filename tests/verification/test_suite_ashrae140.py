"""The ashrae140 suite as tests: one test per attempted range and time step (spec §9.1).

Each case runs once per session at each time step a range needs it at (:class:`SuiteRuns`), into a
temporary directory outside the repository. While the suite's gating is off (spec §9.3), a range outside
its bounds is reported as a :class:`VerificationStatusWarning` and the test passes; with gating on it
fails. A range the register covers with a known deviation is an xfail. Ranges of cases the suite does not
model get no test, and neither do the diagnostics.

The bounds are private: a message names the value, its unit, the status and the time step, never a
bound, a position in the range or a distance to a bound, since the value together with either of the
last two gives the bound back. The tests skip without ``HISIM_VERIFICATION_DATA``.

Run: ``pytest -m verification tests/verification/test_suite_ashrae140.py [--verification-timesteps 3600]``
"""

import pathlib
import warnings
from typing import Dict, Tuple

import pytest

from hisim.verification import compare, runner, system_builder
from hisim.verification.schema import Case
from hisim.verification.suite import RangeCase, Suite

pytestmark = pytest.mark.verification

SUITE = Suite.load("ashrae140")

#: The step lengths the range tests run at unless ``--verification-timesteps`` names others.
DEFAULT_TIMESTEPS: Tuple[int, ...] = (3600, 900, 60)


class VerificationStatusWarning(UserWarning):
    """A range outside its bounds, reported instead of failed while the suite's gating is off."""


def range_test_id(range_case: RangeCase, timestep: int) -> str:
    """The test id of a range at a time step, e.g. ``ashrae140-620-900-annual_cooling_diff``."""
    suffix = "_diff" if range_case.parent is not None else ""
    return f"{SUITE.name}-{range_case.case}-{timestep}-{range_case.output}{suffix}"


def selected_timesteps(option: object) -> Tuple[int, ...]:
    """The step lengths of ``--verification-timesteps`` (comma-separated seconds), or the default three."""
    if option is None:
        return DEFAULT_TIMESTEPS
    try:
        timesteps = tuple(int(entry) for entry in str(option).split(",") if entry.strip())
    except ValueError as error:
        raise pytest.UsageError(f"--verification-timesteps takes comma-separated seconds, not {option!r}") from error
    if not timesteps or any(timestep <= 0 or 3600 % timestep for timestep in timesteps):
        raise pytest.UsageError(f"--verification-timesteps: every step length must divide the hour, not {option!r}")
    return timesteps


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrise the range test by every range the suite models and every selected time step."""
    if "range_case" not in metafunc.fixturenames:
        return
    timesteps = selected_timesteps(metafunc.config.getoption("verification_timesteps", default=None))
    parameters = [(range_case, timestep) for timestep in timesteps for range_case in SUITE.range_cases if not range_case.not_modelled]
    metafunc.parametrize(("range_case", "timestep"), parameters, ids=[range_test_id(range_case, timestep) for range_case, timestep in parameters])


class SuiteRuns:
    """The annual loads of the cases, each case run once per time step, when a range first needs it."""

    def __init__(self, cases: Dict[str, Case], output_directory: pathlib.Path) -> None:
        """Remember the cases and where their runs are written."""
        self.cases = cases
        self.output_directory = output_directory
        self.loads: Dict[int, Dict[str, Dict[str, float]]] = {}

    def annual(self, case_id: str, timestep: int) -> Dict[str, float]:
        """The annual heating and cooling of a case at a time step, running the case the first time."""
        at_step = self.loads.setdefault(timestep, {})
        if case_id not in at_step:
            settings = runner.RunSettings(year=system_builder.DEFAULT_YEAR, seconds_per_timestep=timestep, output_directory=self.output_directory)
            run = runner.run_case(system_builder.build(self.cases[case_id]), settings, SUITE.outputs_for(case_id))
            at_step.update(compare.annual_loads({case_id: run.summary}))
        return at_step[case_id]


@pytest.fixture(scope="session", name="suite_runs")
def fixture_suite_runs(cases: Dict[str, Case], tmp_path_factory: pytest.TempPathFactory):
    """The session's runs, written below a temporary directory, with the write guard collecting (D8)."""
    patch = pytest.MonkeyPatch()
    patch.setenv("HISIM_WRITE_GUARD", "collect")
    yield SuiteRuns(cases, tmp_path_factory.mktemp(SUITE.name))
    patch.undo()


@pytest.fixture(scope="session", name="references")
def fixture_references(reference_file: pathlib.Path) -> compare.ReferenceRanges:
    """The private ranges."""
    return compare.ReferenceRanges.load(reference_file)


def test_verification(range_case: RangeCase, timestep: int, suite_runs: SuiteRuns, references: compare.ReferenceRanges) -> None:
    """The range's value at the time step lies within its bounds, is a known deviation, or is reported."""
    needed = [case for case in (range_case.case, range_case.parent) if case is not None]
    annual = {case: suite_runs.annual(case, timestep) for case in needed}
    result = compare.compare_range(range_case, annual, references, SUITE, timestep)
    status, value, note = result.status, result.value, result.note
    del result  # nothing below may show the bounds, not even in a traceback with --showlocals
    if status == compare.SKIPPED:
        pytest.skip(note)
    message = f"{range_test_id(range_case, timestep)}: {status}, value {value:.3f} {compare.REFERENCE_UNITS}"
    if status == compare.KNOWN_DEVIATION:
        pytest.xfail(f"{message} ({note})")
    if status == compare.FAIL:
        if SUITE.gating:
            pytest.fail(message, pytrace=False)
        warnings.warn(VerificationStatusWarning(f"{message}; reported, not failed, while the suite's gating is off"))
