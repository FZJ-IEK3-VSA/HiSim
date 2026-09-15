"""Tests for the ``hisim`` command line: the four verbs, their output and their exit codes.

Every test drives the real command the way a user does — a list of words handed to
:func:`hisim.cli.main` — and reads what it wrote. Nothing is mocked and no subprocess is started:
the command returns its exit code rather than exiting, so the whole surface a user meets, from
argument parsing to the message on the error stream, is exercised in process.

What is asserted is what a user relies on. ``describe`` has to name the presets, the constructors
and the sizable fields of a class, because a wrong or missing one sends an author to write a file
the executor will reject. ``facts`` has to state where each sized value came from, and to refuse a
file the executor refuses, with the same message. ``schema`` has to produce exactly the committed
file. And ``run`` has to leave a result directory holding the three artifacts every run writes.
"""

# clean

import os
from pathlib import Path
from typing import ClassVar

import pytest

from hisim import cli
from hisim.cli import ExitCodes, main
from hisim.energy_system.audit import AuditWriter
from hisim.energy_system.comments import AnnotatedEmitter
from hisim.energy_system.schema_export import default_schema_path


class Fixtures:
    """The files the command-line tests run against, and where they live.

    The minimal mockup is the one energy system every class of which is converted, so it is what
    ``facts`` and ``run`` are exercised on; the heat-pump mockup is the file that is still
    expected to be refused, which is what makes the refusal path testable.
    """

    #: Directory holding the normative mockups, which are the format's design reference.
    MOCKUPS: ClassVar[Path] = Path(__file__).resolve().parents[1] / "roadmap" / "declarative_energy_systems"

    #: The gas-boiler household: the file that loads, resolves, builds and runs today.
    MINIMAL: ClassVar[Path] = MOCKUPS / "energy_system_mockup_minimal.yaml"

    #: The heat-pump household, whose classes P4 has still to convert.
    HEAT_PUMP: ClassVar[Path] = MOCKUPS / "energy_system_mockup.yaml"

    #: One January day at a quarter-hour resolution: long enough to run every state machine,
    #: short enough for a test suite.
    PARAMETERS: ClassVar[Path] = (
        Path(__file__).resolve().parents[1] / "energy_systems" / "one_day_15min.simulation.yaml"
    )

    #: The configuration class ``describe`` is exercised on: the one with the most presets, a
    #: sized power band and contributed facts, so a single output covers every section.
    BOILER_CONFIG: ClassVar[str] = "hisim.components.generic_boiler.GenericBoilerConfig"

    #: The component class whose configuration is the same one, reached the other way round.
    BOILER_COMPONENT: ClassVar[str] = "hisim.components.generic_boiler.GenericBoiler"


@pytest.mark.base
def test_describe_prints_the_presets_sizable_fields_and_facts_of_a_class(capsys) -> None:
    """Catches a description that omits what an author needs to write an entry.

    The three sections are what a file's ``preset``, ``config`` and ``sizing_sources`` keys are
    written from, so a missing one makes the command useless for the job it exists for.
    """
    code = main(["energy-system", "describe", Fixtures.BOILER_CONFIG])
    printed = capsys.readouterr().out

    assert code == ExitCodes.OK
    assert "GenericBoilerConfig" in printed
    assert "condensing_gas" in printed and "condensing_gas_12kw" in printed
    assert "maximal_thermal_power_in_watt" in printed
    assert "heating_load_in_watt (ONE)" in printed
    assert "facts provided" in printed


@pytest.mark.base
@pytest.mark.parametrize(
    "class_path, fact",
    [
        (
            "hisim.components.heat_distribution_system.HeatDistributionControllerConfig",
            "set_heating_threshold_outside_temperature_in_celsius",
        ),
        ("hisim.components.building.BuildingConfig", "roof_area_in_m2"),
        ("hisim.components.generic_pv_system.PVSystemConfig", "pv_peak_power_in_watt"),
        ("hisim.components.generic_boiler.GenericBoilerConfig", "energy_carrier"),
        ("hisim.components.generic_boiler.GenericBoilerConfig", "heating_value_of_fuel_in_kwh_per_liter"),
        ("hisim.components.generic_boiler.GenericBoilerConfig", "fuel_density_in_kg_per_m3"),
    ],
)
def test_describe_lists_each_batch_one_fact_under_its_provider(capsys, class_path: str, fact: str) -> None:
    """Every fact R2.1 added is visible as provided by the class that computes it.

    ``describe`` is where an author finds out which component can supply the fact a
    ``sizing_sources`` line has to name, so a contributed fact that does not reach this output is
    a fact nobody can be pointed at.
    """
    code = main(["energy-system", "describe", class_path])
    printed = capsys.readouterr().out

    assert code == ExitCodes.OK
    provided = printed.split("facts provided", 1)[1]
    assert fact in provided


@pytest.mark.base
def test_describe_accepts_the_component_class_a_file_writes(capsys) -> None:
    """Catches the command demanding the configuration class an author never spells.

    A file names the component, so that is the name an author has in hand; being forced to know
    the configuration class as well would make the command answer a question nobody asked.
    """
    component = main(["energy-system", "describe", Fixtures.BOILER_COMPONENT])
    from_component = capsys.readouterr().out
    config = main(["energy-system", "describe", Fixtures.BOILER_CONFIG])
    from_config = capsys.readouterr().out

    assert component == config == ExitCodes.OK
    assert from_component == from_config


@pytest.mark.base
def test_describe_shows_a_named_constructor_with_its_parameters(capsys) -> None:
    """Catches the constructor form being invisible from the command line (AC-P2.15)."""
    code = main(["energy-system", "describe", "hisim.components.weather.Weather"])
    printed = capsys.readouterr().out

    assert code == ExitCodes.OK
    assert "for_location(location:" in printed


@pytest.mark.base
def test_describing_something_that_is_not_a_config_class_is_a_usage_error(capsys) -> None:
    """Catches a mistyped class path being reported as a file problem or as a traceback."""
    code = main(["energy-system", "describe", "hisim.components.nowhere.Nothing"])
    captured = capsys.readouterr()

    assert code == ExitCodes.USAGE
    assert "cannot be imported" in captured.err


@pytest.mark.base
def test_facts_prints_where_every_sized_value_of_a_file_comes_from(capsys) -> None:
    """Catches the resolution table losing a provider, a consumer or the rule that bound them.

    This is the report an author reads instead of guessing, and it has to hold all three halves:
    who provides a fact, who reads one, and which provider each read actually resolved to.
    """
    code = main(["energy-system", "facts", str(Fixtures.MINIMAL)])
    printed = capsys.readouterr().out

    assert code == ExitCodes.OK
    assert "facts provided" in printed and "facts consumed" in printed
    assert "heating_load_in_watt" in printed
    assert "boiler.heating_load_in_watt" in printed
    assert "<- building" in printed
    assert "[UNIQUE]" in printed


@pytest.mark.base
def test_facts_refuses_a_file_the_executor_refuses_and_says_why(capsys) -> None:
    """Catches the command being more permissive than a run, which would be worse than useless."""
    code = main(["energy-system", "facts", str(Fixtures.HEAT_PUMP)])
    captured = capsys.readouterr()

    assert code == ExitCodes.FILE_REJECTED
    assert "EF-13" in captured.err


@pytest.mark.base
def test_schema_writes_exactly_the_committed_file(tmp_path: Path, capsys) -> None:
    """Catches the export and the committed schema drifting apart in either direction."""
    target = tmp_path / "exported.schema.json"

    code = main(["energy-system", "schema", "--out", str(target)])
    capsys.readouterr()

    assert code == ExitCodes.OK
    assert target.read_text(encoding="utf-8") == default_schema_path().read_text(encoding="utf-8")


@pytest.mark.base
def test_run_simulates_a_file_and_leaves_the_three_artifacts_behind(tmp_path: Path, capsys) -> None:
    """Catches a run that finishes without writing down what it ran.

    A record that states every decided value, its audit companion and the flat log of every wire
    are what makes a finished run reproducible and reviewable, so the command is only correct if
    all three are there afterwards.
    """
    results = tmp_path / "results"

    code = main(
        [
            "energy-system",
            "run",
            str(Fixtures.MINIMAL),
            str(Fixtures.PARAMETERS),
            "--result-dir",
            str(results),
        ]
    )
    printed = capsys.readouterr().out

    assert code == ExitCodes.OK
    assert str(results) in printed
    assert (results / AnnotatedEmitter.RECORD_FILENAME).is_file()
    assert (results / AuditWriter.AUDIT_FILENAME).is_file()
    assert (results / AuditWriter.CONNECTIONS_FILENAME).is_file()


@pytest.mark.base
def test_a_command_line_naming_no_verb_is_a_usage_error(capsys) -> None:
    """Catches a bare invocation running something instead of printing what there is to run."""
    assert main([]) == ExitCodes.USAGE
    assert main(["energy-system"]) == ExitCodes.USAGE
    capsys.readouterr()


#: A variable no real environment holds, so the test can tell a file that was read from one that
#: was not without touching the two variables a developer may legitimately have set.
DOTENV_SENTINEL = "HISIM_TEST_DOTENV_SENTINEL"


@pytest.mark.base
def test_the_command_line_reads_the_environment_file_on_start_up(tmp_path: Path, monkeypatch, capsys) -> None:
    """Catches the console script running without the credentials the module entry point has.

    ``hisim/hisim_main.py`` reads the ``.env`` that holds ``UTSP_URL`` and ``UTSP_API_KEY``, so a
    run started the other documented way has to read the same file or the UTSP occupancy fails for
    want of credentials rather than saying what is missing. python-dotenv resolves that file by
    walking up from the directory of the module that calls it, and from the working directory
    instead whenever a tracer is installed, so the sentinel is planted on both paths: what is
    asserted is that the file was read, not which of the two python-dotenv happened to take.
    """
    beside_the_package = Path(cli.__file__).parent / ".env"
    if beside_the_package.exists():  # a real one, which a test must never overwrite
        pytest.skip(f"{beside_the_package} exists")
    written = f"{DOTENV_SENTINEL}=read\n"
    beside_the_package.write_text(written, encoding="utf-8")
    (tmp_path / ".env").write_text(written, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(DOTENV_SENTINEL, raising=False)

    try:
        code = main(["energy-system", "schema", "--out", str(tmp_path / "exported.schema.json")])
        loaded = os.environ.get(DOTENV_SENTINEL)
    finally:
        beside_the_package.unlink()
        os.environ.pop(DOTENV_SENTINEL, None)
    capsys.readouterr()

    assert code == ExitCodes.OK
    assert loaded == "read"
