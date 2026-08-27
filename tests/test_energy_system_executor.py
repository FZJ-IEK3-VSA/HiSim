"""Tests for the executor: a file on disk, a simulation period, and a finished run.

Everything else in this package is checked one stage at a time. This module checks the sequence:
that the minimal mockup — the household the format's first use case describes — really loads,
expands, validates, configures, sizes, builds, wires and *runs*, and that it leaves a result
table behind. It is the one test that would notice a stage quietly doing nothing, because the
only way to produce numbers is for all of them to have worked.

Two smaller rules live here as well, both of them properties of the sequence rather than of any
stage. A file carrying a ``metadata`` block is refused on a plain run and accepted on an explicit
re-run, because such a block is written by a generator and its presence means the caller handed
in a record of a previous run rather than an authored file. And a component nothing reads is
reported as a warning rather than rejected: it is legal, occasionally intended, and far more
often a forgotten input item.

Each test states the failure mode it catches.
"""

# clean

from pathlib import Path
from typing import ClassVar

import pytest

from hisim.energy_system.errors import EnergySystemFormatError
from hisim.energy_system.executor import (
    SimulationParametersReader,
    build_energy_system,
    run_energy_system,
)
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters
from tests.test_energy_system_classes import ExpectedFailures


class Fixtures:
    """The files these tests run, and where they are.

    The energy system is the normative minimal mockup itself rather than a copy of it: a test
    that ran a private variant would keep passing while the file the requirements point at
    rotted. The simulation parameters are a fixture of this suite, because a period is a property
    of a run and the mockup deliberately says nothing about one.
    """

    #: The directory holding the normative mockups, which are the format's contract.
    MOCKUPS: ClassVar[Path] = Path(__file__).resolve().parent.parent / "roadmap" / "declarative_energy_systems"

    #: The gas-boiler household: the one mockup whose classes are all converted, and therefore
    #: the one that runs end to end today.
    MINIMAL: ClassVar[Path] = MOCKUPS / "energy_system_mockup_minimal.yaml"

    #: One January day at a quarter-hour resolution, asking only for the result table.
    PARAMETERS: ClassVar[Path] = (
        Path(__file__).resolve().parent.parent / "energy_systems" / "one_day_15min.simulation.yaml"
    )

    @classmethod
    def parameters(cls, result_directory: Path) -> SimulationParameters:
        """Reads the fixture parameters and points them at a directory the test owns.

        Args:
            result_directory: The test's temporary directory; the run writes below it, so that
                nothing is left behind in the repository.

        Returns:
            The parameters of the run.
        """
        parameters = SimulationParametersReader.read(cls.PARAMETERS)
        parameters.result_directory = str(result_directory / "results")
        return parameters


@pytest.mark.base
def test_the_minimal_mockup_names_only_converted_classes() -> None:
    """Catches the end-to-end test below being run against a file that cannot execute.

    The pinned set of class-bound failures per mockup is what tracks how far the class conversion
    has come. For the minimal mockup it is empty, and it has to stay empty: the moment an entry
    of it names a preset that does not exist, the run below fails for a reason that has nothing
    to do with the executor.
    """
    assert not dict(ExpectedFailures.MINIMAL)


@pytest.mark.base
def test_the_minimal_mockup_builds_runs_and_writes_results(tmp_path: Path) -> None:
    """Catches any stage of the lifecycle failing, or quietly doing nothing.

    This is the acceptance criterion of the whole phase in one test. A break anywhere — a preset
    that no longer exists, a sizing law that stops resolving, a default connection that stops
    expanding, a port renamed on a component — surfaces here, and the result table is what proves
    the simulation actually ran rather than merely being set up.
    """
    results = tmp_path / "results"

    built = run_energy_system(Fixtures.MINIMAL, Fixtures.PARAMETERS, result_directory=str(results))

    assert [name for name, _ in built.wired.components] == [
        "weather",
        "occupancy",
        "building",
        "hds_controller",
        "hds",
        "boiler",
        "boiler_controller",
        "meter",
    ]
    assert built.simulator.get_simulation_parameters().result_directory == str(results)
    assert (results / "finished.flag").is_file()
    assert list(results.glob("*.csv")), "the run wrote no result table"


@pytest.mark.base
def test_the_minimal_mockup_sizes_its_boiler_and_its_controller_from_the_building(
    tmp_path: Path,
) -> None:
    """Catches sizing silently falling back to a nominal value instead of reading the building.

    The whole point of the format is that the boiler's power band is written nowhere: it comes
    from the building's heating load, and the controller's band comes from the boiler's. If any
    of those links broke, the household would still run — on a differently sized boiler.
    """
    built = build_energy_system(Fixtures.MINIMAL, Fixtures.parameters(tmp_path))

    boiler = built.configured.config_of("boiler")
    controller = built.configured.config_of("boiler_controller")
    assert boiler.maximal_thermal_power_in_watt > 0
    assert controller.maximal_thermal_power_in_watt == boiler.maximal_thermal_power_in_watt
    assert controller.minimal_thermal_power_in_watt == boiler.minimal_thermal_power_in_watt


@pytest.mark.base
def test_a_component_nothing_reads_is_reported_as_a_warning(tmp_path: Path) -> None:
    """Catches an unread component being rejected, or being passed over in silence.

    A meter measures a household and feeds nothing back into it, so an unread component is a
    legitimate system rather than a broken one; but the far more common cause of one is a
    forgotten input item, so the run says so out loud.
    """
    built = build_energy_system(Fixtures.MINIMAL, Fixtures.parameters(tmp_path))

    assert any("'meter' feeds no other component" in warning for warning in built.warnings)


@pytest.mark.base
def test_a_file_with_a_metadata_block_is_refused_on_a_plain_run(tmp_path: Path) -> None:
    """Catches a generated run record being run as though it were an authored file.

    A record and the file it was generated from describe the same household but mean different
    things: one is what an author wrote, the other is what a run realized, down to every number a
    law computed. Running one silently in place of the other would make it impossible to say
    afterwards which of the two a set of results came from.
    """
    path = tmp_path / "recorded.energy_system.yaml"
    path.write_text(
        "schema_version: 3\n"
        "name: a generated record\n"
        "components:\n"
        "  weather:\n"
        "    class: hisim.components.weather.Weather\n"
        "    preset: standard\n"
        "metadata:\n"
        "  source_energy_system_file: authored.energy_system.yaml\n",
        encoding="utf-8",
    )

    with pytest.raises(EnergySystemFormatError) as failure:
        build_energy_system(path, Fixtures.parameters(tmp_path))

    assert failure.value.error_id.value == "EF-09"
    assert "metadata" in str(failure.value)


@pytest.mark.base
def test_a_file_with_a_metadata_block_runs_when_the_caller_asks_for_a_re_run(tmp_path: Path) -> None:
    """Catches the metadata gate blocking the one thing it is there to make explicit.

    Re-running a record is how a run is reproduced exactly, so the gate must let it through when
    the caller says that is what it means — otherwise the rule would make records unusable
    instead of unambiguous.
    """
    path = tmp_path / "recorded.energy_system.yaml"
    path.write_text(
        "schema_version: 3\n"
        "name: a generated record\n"
        "components:\n"
        "  weather:\n"
        "    class: hisim.components.weather.Weather\n"
        "    preset: standard\n"
        "metadata:\n"
        "  source_energy_system_file: authored.energy_system.yaml\n",
        encoding="utf-8",
    )
    built = build_energy_system(path, Fixtures.parameters(tmp_path), rerun=True)

    assert [name for name, _ in built.wired.components] == ["weather"]


@pytest.mark.base
def test_the_simulation_parameters_are_read_from_their_own_file() -> None:
    """Catches a period or a post-processing option being lost between the file and the run.

    The parameters live in their own file so that one energy system can be run over a day and
    over a year; if a value were dropped on the way in, the run would silently use the default
    period instead of the one that was asked for.
    """
    parameters = SimulationParametersReader.read(Fixtures.PARAMETERS)

    assert parameters.seconds_per_timestep == 900
    assert parameters.start_date.year == 2021
    assert (parameters.end_date - parameters.start_date).days == 1
    assert parameters.post_processing_options == [PostProcessingOptions.EXPORT_TO_CSV]


@pytest.mark.base
def test_a_parameters_file_naming_an_unknown_option_is_rejected(tmp_path: Path) -> None:
    """Catches a misspelled post-processing option being dropped instead of reported.

    Silently ignoring it would mean a run that was asked to produce a report produces none, and
    nothing in the output would say why.
    """
    path = tmp_path / "broken.simulation.yaml"
    path.write_text(
        'start_date: "2021-01-01T00:00:00"\n'
        'end_date: "2021-01-02T00:00:00"\n'
        "seconds_per_timestep: 900\n"
        "post_processing_options:\n"
        "  - EXPORT_TO_CSF\n",
        encoding="utf-8",
    )

    with pytest.raises(EnergySystemFormatError) as failure:
        SimulationParametersReader.read(path)

    assert failure.value.error_id.value == "EF-07"
    assert "EXPORT_TO_CSF" in str(failure.value)
    assert "Did you mean: EXPORT_TO_CSV" in str(failure.value)
