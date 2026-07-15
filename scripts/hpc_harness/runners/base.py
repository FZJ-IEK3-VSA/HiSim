"""Runner protocol and registry (spec §4.4).

A Runner makes the harness program-agnostic: the server, DB, dashboard, and worker
know nothing about the simulation being run. A new program integrates by shipping one
Runner class and registering it (directly or via the ``hpc_harness.runners``
setuptools entry-point group).
"""

from typing import Dict, Protocol, runtime_checkable


@runtime_checkable
class Runner(Protocol):
    """One job-execution strategy.

    ``warmup`` runs **once per node, in the spawner process** (heavy imports);
    ``on_fork`` runs once per warm child (cheap re-init: RNG reseed, handle reopen);
    ``run`` executes one job in the child and raises on failure.
    """

    name: str

    def warmup(self) -> None:
        """Perform the heavy one-time imports (called in the spawner, spec §4.3)."""

    def on_fork(self) -> None:
        """Cheap per-child re-initialization after fork."""

    def run(self, payload: dict, result_dir: str) -> None:
        """Execute one job, writing all output into ``result_dir``; raise on failure."""


_REGISTRY: Dict[str, Runner] = {}


def register_runner(runner: Runner) -> None:
    """Add a runner instance to the registry (keyed by its ``name``)."""
    _REGISTRY[runner.name] = runner


def get_runner(name: str) -> Runner:
    """Look up a runner, lazily registering the built-ins and entry points."""
    if name not in _REGISTRY:
        _register_builtins()
        _load_entry_points()
    if name not in _REGISTRY:
        raise KeyError(f"No runner named {name!r}; registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def _register_builtins() -> None:
    from hpc_harness.runners.hisim_runner import HiSimRunner  # pylint: disable=import-outside-toplevel
    from hpc_harness.runners.hisim_setup_runner import HiSimSetupRunner  # pylint: disable=import-outside-toplevel
    from hpc_harness.runners.subprocess_runner import SubprocessRunner  # pylint: disable=import-outside-toplevel

    for runner in (HiSimRunner(), HiSimSetupRunner(), SubprocessRunner()):
        _REGISTRY.setdefault(runner.name, runner)


def _load_entry_points() -> None:
    """External programs can plug in via the ``hpc_harness.runners`` entry-point group."""
    try:
        from importlib.metadata import entry_points  # pylint: disable=import-outside-toplevel

        for entry in entry_points(group="hpc_harness.runners"):
            try:
                runner = entry.load()()
                _REGISTRY.setdefault(runner.name, runner)
            except Exception:  # pylint: disable=broad-except
                pass
    except Exception:  # pylint: disable=broad-except
        pass
