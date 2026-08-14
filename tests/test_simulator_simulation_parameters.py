"""Tests for :attr:`hisim.simulator.Simulator.simulation_parameters`.

The parameters a run was configured with are read constantly from outside the simulator — a
system setup wants the result directory the run resolved, post-processing wants the timestep
length — and until this property existed the only attribute-style access was the private
`_simulation_parameters`, which the reference system setups reached into behind a `noqa: SLF001`.
Reference material is copied, so the habit spread from there; these tests pin the public accessor
that replaces it, including its read-only half, since a settable one would bypass the logging-level
side effects of `set_simulation_parameters`.

**Error class.** A failure here is an *API* failure, not a simulation one: nothing about a
simulated number depends on it. What it does affect is whether setups and post-processing can read
the run's configuration without touching a private, so a red test here means either the accessor
disappeared or it stopped agreeing with the object the simulator actually uses.
"""

# clean

import pytest

from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator

pytestmark = pytest.mark.base


def _simulator(parameters: SimulationParameters) -> Simulator:
    """A simulator carrying the given parameters and nothing else.

    Constructing one touches no file: the module directory and filename are only read when the
    setup function is actually imported, which these tests never do. That keeps the accessor under
    test isolated from everything a real run would drag in.
    """
    return Simulator(
        module_directory="system_setups",
        module_filename="does_not_need_to_exist.py",
        my_simulation_parameters=parameters,
    )


class TestSimulationParametersAccessor:
    """The public read-only view of the run's parameters."""

    def test_it_is_the_object_the_simulator_runs_with(self):
        """Identity, not a copy: mutating a field through it reaches the simulator's own state."""
        parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
        simulator = _simulator(parameters)

        assert simulator.simulation_parameters is parameters
        assert simulator.simulation_parameters is simulator.get_simulation_parameters()
        simulator.simulation_parameters.result_directory = "results/run_1"
        assert parameters.result_directory == "results/run_1"

    def test_it_cannot_be_assigned(self):
        """Replacing the parameters goes through `set_simulation_parameters`, which does more."""
        simulator = _simulator(SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600))
        replacement = SimulationParameters.one_day_only(year=2022, seconds_per_timestep=60)

        with pytest.raises(AttributeError):
            simulator.simulation_parameters = replacement  # type: ignore[misc]
        simulator.set_simulation_parameters(replacement)
        assert simulator.simulation_parameters is replacement
