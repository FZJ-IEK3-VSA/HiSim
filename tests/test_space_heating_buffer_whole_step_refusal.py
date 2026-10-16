"""A start-of-step space-heating buffer refuses whole steps above 600 s when it is built.

The buffer with its heat exchanger is a start-of-step node: it books every circuit on the temperature it starts the
step at, so it does not see its own temperature change inside the step. Above 600 s its generators and its heat
distribution system therefore run a fraction of the step, and they do so only for steps longer than the run's
part-load threshold, ``SimulationParameters.part_load_above_seconds``. A step above 600 s with a threshold at or above
that step would give every circuit whole steps, which drained the gas twin's buffer below 0 °C at 3600 s and overshot
a heat pump's buffer to 93.3 °C in stage D's measurements. The owner decided on 2026-10-16 that such a run is refused
when the system is built (review of the stage-D spec, finding M-C).

These tests check the rule on the buffer itself and on recorded twins built at 900 s:

* refused: 900 s steps with the threshold at 900 s (and above it);
* accepted: 900 s with the default 600 s threshold, 600 s with any threshold;
* never refused: a buffer without its heat exchanger (not a start-of-step node), and a system without a
  space-heating buffer (the district-heating twin, ``gas_boiler_household``).
"""

import dataclasses
from pathlib import Path

import pytest
import yaml

from hisim.calculation_scope import CalculationScope
from hisim.components.simple_water_storage import (
    SimpleHotWaterStorage,
    SimpleHotWaterStorageConfig,
    StartOfStepBufferWholeStepError,
)
from hisim.energy_system.errors import EnergySystemWiringError
from hisim.energy_system.executor import SimulationParametersReader, build_energy_system
from tests.part_load_rigs import Parameters


class BufferBuild:
    """Builds a buffer, or a recorded twin with its buffer, for one step length and one part-load threshold."""

    #: The repository's root.
    ROOT = Path(__file__).resolve().parents[1]

    #: A buffer volume, l; any positive volume will do, the refusal does not depend on it.
    VOLUME_IN_LITER: float = 500.0

    @classmethod
    def buffer(
        cls, seconds_per_timestep: int, *, heat_exchanger_is_present: bool = True, **extra: float
    ) -> SimpleHotWaterStorage:
        """Build the fleet's space-heating buffer for one day of 2021 at a step length.

        Args:
            seconds_per_timestep: The step length, s.
            heat_exchanger_is_present: Whether the buffer has its heat exchanger, which makes it a start-of-step node.
            **extra: Further arguments of the simulation parameters, such as ``part_load_above_seconds``.

        Returns:
            The buffer.
        """
        config = dataclasses.replace(
            SimpleHotWaterStorageConfig.preset_buffer("Buffer"),
            volume_heating_water_storage_in_liter=cls.VOLUME_IN_LITER,
            heat_exchanger_is_present=heat_exchanger_is_present,
        )
        buffer: SimpleHotWaterStorage = SimpleHotWaterStorage(Parameters.one_day(seconds_per_timestep, **extra), config)
        return buffer

    @classmethod
    def twin(cls, twin: str, work: Path, seconds_per_timestep: int, part_load_above_seconds: float) -> None:
        """Build (not run) a recorded twin for one day of 2021 in ``work``.

        Raises:
            EnergySystemWiringError: When a component of the twin cannot be constructed, the buffer's refusal included.
        """
        text = (cls.ROOT / "energy_systems" / f"{twin}.energy_system.yaml").read_text(encoding="utf-8")
        energy_system = work / f"{twin}.energy_system.yaml"
        energy_system.write_text(text.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), encoding="utf-8")
        parameters = work / "day.simulation.yaml"
        parameters.write_text(
            yaml.safe_dump(
                {
                    "start_date": "2021-01-01T00:00:00",
                    "end_date": "2021-01-02T00:00:00",
                    "seconds_per_timestep": seconds_per_timestep,
                    "country": "DE",
                    "logging_level": 3,
                    "post_processing_options": [],
                    "part_load_above_seconds": part_load_above_seconds,
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        results = work / "results"
        with CalculationScope.open(label=str(energy_system), run_directory=str(results)):
            simulation_parameters = SimulationParametersReader.read(parameters)
            simulation_parameters.result_directory = str(results)
            build_energy_system(energy_system, simulation_parameters)


@pytest.mark.base
@pytest.mark.parametrize("part_load_above_seconds", [900, 1800])
def test_a_buffer_refuses_900_s_steps_with_a_threshold_at_or_above_the_step(part_load_above_seconds: int) -> None:
    """900 s steps with a threshold of 900 s or more would run every circuit of the buffer for whole steps.

    Fails if the buffer is built anyway, or if its error does not name the step, the threshold and the reason.
    """
    with pytest.raises(StartOfStepBufferWholeStepError) as refusal:
        BufferBuild.buffer(900, part_load_above_seconds=part_load_above_seconds)
    message = str(refusal.value)
    assert f"900 s steps with part_load_above_seconds = {part_load_above_seconds} s" in message
    assert "start-of-step node" in message
    assert "fraction of the step" in message


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [900, 3600])
def test_a_buffer_accepts_a_coarse_step_with_the_default_threshold(seconds_per_timestep: int) -> None:
    """Above the default 600 s threshold the circuits run fractions, so the buffer is built.

    Fails if the refusal also caught the runs that do run the part-load mode, RenoVisor's 900 s among them.
    """
    buffer = BufferBuild.buffer(seconds_per_timestep)
    assert buffer.my_simulation_parameters.runs_part_load()


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 600])
@pytest.mark.parametrize("part_load_above_seconds", [0, 60, 600, 900, 3600])
def test_a_buffer_accepts_any_threshold_at_and_below_600_s(
    seconds_per_timestep: int, part_load_above_seconds: int
) -> None:
    """Whole steps of up to 600 s are safe for a start-of-step buffer, so no threshold is refused there.

    Fails if the refusal compared the threshold with the step alone, without the 600 s bound (600 s with a 900 s
    threshold runs whole steps, measured safe).
    """
    BufferBuild.buffer(seconds_per_timestep, part_load_above_seconds=part_load_above_seconds)


@pytest.mark.base
def test_a_buffer_without_its_heat_exchanger_is_never_refused() -> None:
    """Without its heat exchanger the buffer mixes masses and is no start-of-step node, so the rule does not apply.

    Fails if the refusal were made for every space-heating buffer instead of the start-of-step one.
    """
    BufferBuild.buffer(900, heat_exchanger_is_present=False, part_load_above_seconds=900)


@pytest.mark.base
def test_a_twin_with_a_buffer_is_refused_when_it_is_built(tmp_path: Path) -> None:
    """The gas twin built at 900 s with the threshold at 900 s is refused before anything runs.

    Fails if the energy-system build swallowed the buffer's refusal or reported it without the threshold.
    """
    with pytest.raises(EnergySystemWiringError, match="part_load_above_seconds = 900 s"):
        BufferBuild.twin("household_gas_building_sizer", tmp_path, 900, 900)


@pytest.mark.base
@pytest.mark.parametrize("twin", ["household_district_heating_building_sizer", "gas_boiler_household"])
def test_a_system_without_a_space_heating_buffer_is_never_refused(twin: str, tmp_path: Path) -> None:
    """A system without a space-heating buffer builds at 900 s with the threshold at 900 s.

    Fails if the refusal were made by a component other than the start-of-step buffer, such as the distribution
    system or the generators, which also run without a buffer.
    """
    BufferBuild.twin(twin, tmp_path, 900, 900)
