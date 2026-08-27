"""Tests for HiSim's third execution mode and for the files this repository ships to run in it.

``hisim_main.py`` grew a third mode: hand it a ``*.energy_system.yaml`` and it runs the declarative
executor instead of a Python setup or a v1 scenario. The mode is selected by the first argument's
suffix and by nothing else, which makes two things worth pinning: that an energy-system file
reaches the new path, and that the two older paths still claim exactly what they claimed before —
a dispatch rule is the one kind of change that can break a mode without anybody touching it.

The second half of the module is about the shipped directory. ``energy_systems/`` holds the first
runnable pair, and its energy-system file is the canonically written twin of the format's normative
mockup. Two copies of one household is a drift waiting to happen, so the identity of the two is a
test rather than a promise in a README.
"""

# clean

import argparse
from pathlib import Path
from typing import ClassVar, List

import pytest

from hisim.energy_system import dump_energy_system, parse_energy_system
from hisim.energy_system.executor import SimulationParametersReader
from hisim.hisim_main import EnergySystemMode, validate_args


class Shipped:
    """Where the runnable energy systems and their design reference live."""

    #: The directory holding the energy-system files this repository ships.
    DIRECTORY: ClassVar[Path] = Path(__file__).resolve().parents[1] / "energy_systems"

    #: The runnable gas-boiler household.
    HOUSEHOLD: ClassVar[Path] = DIRECTORY / "gas_boiler_household.energy_system.yaml"

    #: The design reference the runnable household is the canonical twin of.
    MOCKUP: ClassVar[Path] = (
        Path(__file__).resolve().parents[1]
        / "roadmap"
        / "declarative_energy_systems"
        / "energy_system_mockup_minimal.yaml"
    )

    #: The simulation-parameters files shipped beside the household.
    PARAMETERS: ClassVar[List[str]] = ["one_day_15min.simulation.yaml", "2021_minutely.simulation.yaml"]


def parsed(*inputs: str) -> argparse.Namespace:
    """Builds the namespace ``validate_args`` reads, without going through argparse.

    Args:
        inputs: The positional arguments as a user would type them.

    Returns:
        The namespace.
    """
    return argparse.Namespace(inputs=list(inputs))


@pytest.mark.base
def test_an_energy_system_file_selects_the_new_mode() -> None:
    """Catches the third mode not being reachable from the command line at all."""
    config = validate_args(parsed(str(Shipped.HOUSEHOLD), str(Shipped.DIRECTORY / Shipped.PARAMETERS[0])))

    assert config["mode"] == "energy_system"
    assert config["energy_system"] == str(Shipped.HOUSEHOLD)
    assert config["simulation"] == str(Shipped.DIRECTORY / Shipped.PARAMETERS[0])


@pytest.mark.base
def test_the_new_mode_accepts_both_simulation_parameter_spellings() -> None:
    """Catches the JSON parameter files the existing setups ship being locked out of the new mode."""
    json_parameters = Path(__file__).resolve().parents[1] / "system_setups" / "2021_minutely_plots.simulation.json"

    config = validate_args(parsed(str(Shipped.HOUSEHOLD), str(json_parameters)))

    assert config["mode"] == "energy_system"
    assert config["simulation"] == str(json_parameters)


@pytest.mark.base
def test_the_two_older_modes_still_claim_what_they_claimed() -> None:
    """Catches the new dispatch stealing an argument from the Python or the v1 JSON mode.

    Nothing released may change: a ``.py`` first argument is still a Python setup and a ``.json``
    first argument is still a v1 scenario, whatever the second argument happens to be.
    """
    root = Path(__file__).resolve().parents[1]
    setup = root / "system_setups" / "basic_household.py"
    scenario = root / "system_setups" / "basic_household.scenario.json"
    parameters = root / "system_setups" / "2021_minutely_plots.simulation.json"

    python_mode = validate_args(parsed(str(setup)))
    json_mode = validate_args(parsed(str(scenario), str(parameters)))

    assert python_mode["mode"] == "python" and python_mode["module_file"] == str(setup)
    assert json_mode["mode"] == "json" and json_mode["scenario"] == str(scenario)


@pytest.mark.base
def test_a_plain_yaml_first_argument_is_not_mistaken_for_an_energy_system() -> None:
    """Catches the dispatch claiming every YAML file, parameters files included.

    The compound suffix is the whole safeguard: ``*.simulation.yaml`` and ``*.energy_system.yaml``
    are both YAML, and handing over the wrong one has to be reported rather than parsed.
    """
    with pytest.raises(ValueError, match="First argument must be"):
        validate_args(parsed(str(Shipped.DIRECTORY / Shipped.PARAMETERS[0]), str(Shipped.HOUSEHOLD)))


@pytest.mark.base
def test_the_new_mode_refuses_a_parameters_file_it_cannot_read() -> None:
    """Catches an unreadable second argument surfacing as a parse failure deep in the executor."""
    with pytest.raises(ValueError, match="Invalid simulation-parameters file"):
        validate_args(parsed(str(Shipped.HOUSEHOLD), str(Shipped.MOCKUP.with_suffix(".txt"))))


@pytest.mark.base
def test_the_new_mode_needs_exactly_two_files() -> None:
    """Catches a third argument being silently ignored rather than questioned."""
    with pytest.raises(ValueError, match="exactly 2 files"):
        validate_args(parsed(str(Shipped.HOUSEHOLD)))


@pytest.mark.base
def test_the_suffixes_the_dispatch_uses_are_the_ones_the_reader_reads() -> None:
    """Catches the dispatch and the parameters reader disagreeing about what is readable."""
    assert set(EnergySystemMode.PARAMETER_SUFFIXES) == set(
        SimulationParametersReader.YAML_SUFFIXES + (SimulationParametersReader.JSON_SUFFIX,)
    )


@pytest.mark.base
def test_the_shipped_household_is_the_design_reference_word_for_word() -> None:
    """Catches the runnable file and the normative mockup drifting apart.

    The mockup is the format's design contract and the shipped file is what a user runs; they
    describe one household, so a change to either has to be made to both. Comparing them after one
    canonicalising pass ignores exactly what is allowed to differ — the comments and the layout —
    and nothing else.
    """
    shipped = dump_energy_system(parse_energy_system(Shipped.HOUSEHOLD))
    reference = dump_energy_system(parse_energy_system(Shipped.MOCKUP))

    assert shipped == reference


@pytest.mark.base
@pytest.mark.parametrize("name", Shipped.PARAMETERS)
def test_every_shipped_parameters_file_reads(name: str) -> None:
    """Catches a shipped example that cannot be run, which is worse than no example at all."""
    parameters = SimulationParametersReader.read(Shipped.DIRECTORY / name)

    assert parameters.seconds_per_timestep > 0
    assert parameters.end_date > parameters.start_date


@pytest.mark.base
def test_the_directory_explains_itself() -> None:
    """Catches the shipped files losing the one page that says how to run them."""
    readme = (Shipped.DIRECTORY / "README.md").read_text(encoding="utf-8")

    assert "hisim energy-system run" in readme
    assert "system_setups/" in readme
