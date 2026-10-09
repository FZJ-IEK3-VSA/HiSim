"""The test contract of every assembly of a library (``assemblies_spec.md`` §9.4, D24; lean v1 §13.1).

The mock library and the real one, ``energy_systems/assemblies/``, by default; ``pytest
--assembly-library DIR`` runs another one instead, with the test partners of its
``test_partners.yaml``. Each assembly gets its library check, and each of its samples one
isolation run (one day at 900 s, the energy balance and ``i_doublecheck`` on) that one test per
check kind reads: the run, the energy balance, finiteness, the member contract, every
applicable ``bounds`` entry, and ``expect`` at the defaults. Every ``monotone`` entry sweeps its
parameter from the samples admitting a sweep, once per distinct point set
(:func:`~hisim.energy_system.assemblies.testing.checks.evaluate_monotone`). The deterministic
samples run under the ``assemblies`` marker (the PR gate's job ``pytest (assemblies)``), the Latin
hypercube under ``nightly`` (the golden-year workflow), sized and seeded by ``--samples`` and
``--seed``. A failure reads ``pv/array sample s003 bounds PVSystem.ElectricityOutput [WATT] in
[-100, 33000]: …``.

A sample's run is made once by the module's run cache, which keeps the run of one sample at a
time: ``tests/assemblies/conftest.py`` puts the tests of one sample next to each other, and the
run of a sample (its frames and its directory) is released when the next sample's run is made.
Under ``xdist`` every sample is one ``xdist_group``, so ``--dist loadgroup`` keeps its tests on one
worker.

A sample a member's component refuses at construction (``ConfigurationRefusedError``, such as a heat
pump's W55 SCOP above its W35 SCOP) is handled, not failed: its tests are skipped, naming the
refusal. A monotone sweep drops the refused points and is skipped when fewer than two remain.
Hypercube samples are not redrawn.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

import pytest

from hisim.energy_system.assemblies.library import check_assembly
from hisim.energy_system.assemblies.model import BoundsDeclaration, ExpectDeclaration, MonotoneDeclaration
from hisim.energy_system.assemblies.parameters import select
from hisim.energy_system.assemblies.resolver import AssemblyResolver, ResolvedAssembly
from hisim.energy_system.assemblies.testing import checks
from hisim.energy_system.assemblies.testing.isolation import IsolationRun, run_isolation
from hisim.energy_system.assemblies.testing.partners import TestPartnerRegistry
from hisim.energy_system.assemblies.testing.samples import (
    ParameterSpace,
    Sample,
    deterministic_samples,
    hypercube_samples,
    sweeps,
)
from tests.assemblies.helpers import Mocks

#: The real library of the repository, whose test contracts run beside the mock library's.
REAL_LIBRARY = Path(__file__).resolve().parents[2] / "energy_systems" / "assemblies"


@dataclass(frozen=True)
class Case:
    """One sample of one assembly of one library, and its place in the order the run cache needs."""

    library: str
    assembly: str
    sample: Sample
    members: Tuple[str, ...]
    order: int

    @property
    def label(self) -> str:
        """How a failure names the sample: ``pv/array sample s003``."""
        return f"{self.assembly} sample {self.sample.sample_id}"

    def param(self, *values: Any, name: str = "") -> Any:
        """The case as a pytest parameter, marked by its tier and grouped by its sample under xdist."""
        tier = pytest.mark.nightly if self.sample.nightly else pytest.mark.assemblies
        group = pytest.mark.xdist_group(f"{self.assembly}:{self.sample.sample_id}")
        identifier = f"{self.assembly}:{self.sample.sample_id}" + (f":{name}" if name else "")
        return pytest.param(self, *values, id=identifier, marks=[tier, group])


@dataclass(frozen=True)
class Sweep:
    """One monotone declaration swept from one base sample."""

    library: str
    assembly: str
    declaration: MonotoneDeclaration
    base: Sample


class ContractLibrary:
    """The assemblies of one library with their samples and sweeps, drawn once per test session and worker."""

    def __init__(self, directory: Path, size: int, seed: int, first_order: int = 0) -> None:
        """Resolves every assembly the library offers and draws the samples of those its library check accepts.

        Args:
            directory: The library directory.
            size: The Latin hypercube samples per constraint branch.
            seed: The hypercube's seed.
            first_order: The order of the library's first case, behind the cases of the libraries before it.
        """
        self.directory = str(directory)
        self.resolver = AssemblyResolver([directory])
        self.registry = TestPartnerRegistry.from_directories(self.resolver.directories)
        self.paths = sorted(self.resolver.available())
        self.cases: List[Case] = []
        self.sweeps: List[Any] = []
        for path in self.paths:
            assembly = self.assembly(path)
            if check_assembly(assembly):
                continue  # test_library_check names every problem; nothing else of it can run
            space = ParameterSpace(assembly.model)
            deterministic = deterministic_samples(space)
            hypercube = hypercube_samples(space, size, seed, known=deterministic)
            members: Dict[str, Tuple[str, ...]] = {}
            for sample in deterministic + hypercube:
                selection = select(assembly.model, assembly.label, sample.values, f"sample {sample.sample_id}")
                members[sample.sample_id] = tuple(selection.members)
                self.cases.append(
                    Case(self.directory, path, sample, members[sample.sample_id], first_order + len(self.cases))
                )
            for index, declaration in enumerate(assembly.model.tests.monotone if assembly.model.tests else ()):
                # A hypercube base reaching a deterministic base's sweep is that sweep, run in the PR tier.
                bases = [
                    base
                    for base in deterministic + hypercube
                    if declaration.member in (None,) + members[base.sample_id]
                ]
                for base, _ in sweeps(space, bases, declaration):
                    self.sweeps.append(
                        pytest.param(
                            Sweep(self.directory, path, declaration, base),
                            id=f"{path}:{base.sample_id}:monotone[{index}]",
                            marks=pytest.mark.nightly if base.nightly else pytest.mark.assemblies,
                        )
                    )

    def assembly(self, path: str) -> ResolvedAssembly:
        """One resolved assembly."""
        return self.resolver.resolve(path, "the assembly test contracts")

    def declarations(self, kind: str) -> List[Any]:
        """Every ``bounds`` entry (on every sample) or ``expect`` entry (at the defaults) whose member is present."""
        params = []
        for case in self.cases:
            tests = self.assembly(case.assembly).model.tests
            if tests is None or (kind == "expect" and case.sample.origins[0] != "defaults"):
                continue
            for index, declaration in enumerate(getattr(tests, kind)):
                output = getattr(declaration, "output", None)
                member = output.split(".", 1)[0] if output else declaration.member
                if member is None or member in case.members:
                    params.append(case.param(declaration, name=f"{kind}[{index}]"))
        return params


@functools.lru_cache(maxsize=1)
def contract_libraries(directories: Tuple[str, ...], size: int, seed: int) -> Dict[str, ContractLibrary]:
    """The libraries under test by directory, once per session and worker, their cases in one order."""
    libraries: Dict[str, ContractLibrary] = {}
    for directory in directories:
        first = sum(len(library.cases) for library in libraries.values())
        libraries[directory] = ContractLibrary(Path(directory), size, seed, first)
    return libraries


def libraries_of(config: pytest.Config) -> Dict[str, ContractLibrary]:
    """The libraries under test: ``--assembly-library``, else the mock and the real one; ``--samples``, ``--seed``."""
    chosen = config.getoption("--assembly-library")
    directories = (chosen,) if chosen else (str(Mocks.LIBRARY), str(REAL_LIBRARY))
    return contract_libraries(directories, config.getoption("--samples"), config.getoption("--seed"))


def library_of(config: pytest.Config, directory: str) -> ContractLibrary:
    """One library under test."""
    return libraries_of(config)[directory]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrizes every test over the assemblies, the samples, the declarations or the sweeps it checks."""
    libraries = list(libraries_of(metafunc.config).values())
    if "assembly_path" in metafunc.fixturenames:
        metafunc.parametrize(
            "library, assembly_path",
            [
                pytest.param(library.directory, path, id=path, marks=pytest.mark.assemblies)
                for library in libraries
                for path in library.paths
            ],
        )
    elif "sweep" in metafunc.fixturenames:
        metafunc.parametrize("sweep", [sweep for library in libraries for sweep in library.sweeps])
    elif "declaration" in metafunc.fixturenames:
        kind = metafunc.function.__name__.removeprefix("test_")
        metafunc.parametrize(
            "case, declaration", [item for library in libraries for item in library.declarations(kind)]
        )
    elif "case" in metafunc.fixturenames:
        metafunc.parametrize("case", [case.param() for library in libraries for case in library.cases])


class RunCache:
    """The isolation run of one sample at a time: made when a test first asks for it, released at the next."""

    def __init__(self, libraries: Mapping[str, ContractLibrary], factory: pytest.TempPathFactory) -> None:
        """Starts empty."""
        self.libraries = libraries
        self.factory = factory
        self.case: Optional[Case] = None
        self.run: Optional[IsolationRun] = None

    def get(self, case: Case) -> IsolationRun:
        """The run of one sample; skips the calling test when a member refused the sample at construction."""
        if self.run is None or self.case != case:
            self.release()
            self.case = case
            library = self.libraries[case.library]
            self.run = run_isolation(
                library.assembly(case.assembly),
                case.sample.values,
                library.registry,
                library.resolver,
                self.factory.mktemp(case.label.replace("/", "_").replace(" ", "_")) / "run",
                case.label,
            )
        if self.run.refusal is not None:
            pytest.skip(str(checks.refused(self.run)))
        return self.run

    def release(self) -> None:
        """Drops the current run's frame, outputs and components."""
        if self.run is not None:
            self.run.release()
        self.run = None


@pytest.fixture(scope="module", name="runs")
def fixture_runs(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Iterator[RunCache]:
    """The run cache of the module; the last run is released at its end."""
    cache = RunCache(libraries_of(request.config), tmp_path_factory)
    yield cache
    cache.release()


def test_library_check(library: str, assembly_path: str, request: pytest.FixtureRequest) -> None:
    """The library check accepts the assembly: descriptions, defaults, ranges, units, names, the test contract."""
    problems = check_assembly(library_of(request.config, library).assembly(assembly_path))
    assert not problems, f"{assembly_path} library check: " + " | ".join(problems)


def test_run(case: Case, runs: RunCache) -> None:
    """The isolation run raises nothing (an open energy balance is the next test's finding)."""
    checks.check_run(runs.get(case))


def test_energy_balance(case: Case, runs: RunCache) -> None:
    """The energy balance of the isolation run closes."""
    checks.check_energy_balance(runs.get(case))


def test_finite(case: Case, runs: RunCache) -> None:
    """Every numeric result column is finite at every step."""
    checks.check_finite(runs.get(case))


def test_member_contract(case: Case, runs: RunCache, request: pytest.FixtureRequest) -> None:
    """Every energy or temperature output is bounded in its unit, and every named KPI is reported."""
    tests = library_of(request.config, case.library).assembly(case.assembly).model.tests
    assert tests is not None
    checks.check_member_contract(runs.get(case), tests)


def test_bounds(case: Case, declaration: BoundsDeclaration, runs: RunCache) -> None:
    """One ``bounds`` entry holds on the sample."""
    checks.check_bounds(runs.get(case), declaration)


def test_expect(case: Case, declaration: ExpectDeclaration, runs: RunCache) -> None:
    """One ``expect`` entry holds at the defaults."""
    checks.check_expect(runs.get(case), declaration)


def test_monotone(sweep: Sweep, request: pytest.FixtureRequest, tmp_path: Path) -> None:
    """One ``monotone`` entry holds over the sweep of its parameter from one base sample, refused points dropped."""
    library = library_of(request.config, sweep.library)
    assembly = library.assembly(sweep.assembly)

    def run_point(index: int, values: Mapping[str, Any], label: str) -> IsolationRun:
        """The isolation run of one sweep point, in its own directory."""
        return run_isolation(assembly, values, library.registry, library.resolver, tmp_path / f"point{index}", label)

    try:
        checks.evaluate_monotone(run_point, ParameterSpace(assembly.model), sweep.base, sweep.declaration)
    except checks.SampleRefused as refusal:
        pytest.skip(str(refusal))
