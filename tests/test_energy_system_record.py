"""Tests for the run record: what a run wrote down, and whether running that again reproduces it.

Everything else in this package checks that a file produces the right system. This module checks
the opposite direction: that the system produces the right file. A run leaves three artifacts —
the realized record, the audit companion and the wire log — and the record is the one with a
promise attached to it. It claims to state every value the run decided, so that running the record
instead of the file decides nothing and lands on the same numbers. A claim like that is worth only
as much as the test that holds it, so the central test here runs the household three times: once
from the mockup, once from the record that produced, and once from the record *that* produced.

The other properties this module pins are the ones that make the promise safe to rely on. A record
carries its provenance as comments, and stripping every one of them changes nothing about the run
— which is what lets a person annotate the file heavily without any of it becoming load-bearing.
The annotated writer, which runs on a second YAML library because the first cannot attach
comments, produces byte-identical output to the canonical writer when there is nothing to annotate
— which is what stops the format from quietly growing two styles. And switching a group off leaves
exactly the record that deleting it by hand would have left, checked against a copy no expansion
code ever touched.

Each test states the failure mode it catches.
"""

# clean

from __future__ import annotations

import hashlib
import io
import json
import os
import re
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Tuple

import numpy as np
import pytest
import yaml
from ruamel.yaml import YAML

from hisim.energy_system.audit import AuditWriter, build_audit
from hisim.energy_system.classes import validate_classes
from hisim.energy_system.comments import (
    AnnotatedEmitter,
    CanonicalRepresenter,
    ProvenanceComments,
    strip_comments,
)
from hisim.energy_system.emitter import EnergySystemEmitter
from hisim.energy_system.errors import EnergySystemFormatError, EnergySystemRecordError
from hisim.energy_system.executor import (
    SimulationParametersReader,
    build_energy_system,
    run_energy_system,
    write_records,
)
from hisim.energy_system.loader import dump_energy_system, load_energy_system
from hisim.energy_system.metadata import RunMetadata
from hisim.energy_system.record import realize
from hisim.energy_system.sizing_bridge import sizing_sources_bridge


class Fixtures:
    """The files these tests run and the two normalizations their comparisons need.

    The energy system is the normative minimal mockup itself rather than a copy: a test running a
    private variant would keep passing while the file the requirements point at rotted. The
    parameters are one January day, short enough to run three times inside a test.

    Two things in a record legitimately differ between two runs of the same system and are
    normalized away before any comparison. The ``source_`` keys of the metadata name the file the
    run started from, which is a different file for a record than for the mockup it came from. And
    the version in the metadata and in the header line changes when HiSim does, which is a real
    difference between two runs but not one a committed golden should notice.
    """

    #: The directory holding the normative mockups, which are the format's contract.
    MOCKUPS: ClassVar[Path] = Path(__file__).resolve().parent.parent / "roadmap" / "declarative_energy_systems"

    #: The gas-boiler household: the one mockup whose classes are all converted.
    MINIMAL: ClassVar[Path] = MOCKUPS / "energy_system_mockup_minimal.yaml"

    #: Every mockup, for the checks that hold for any file rather than only for a record.
    ALL_MOCKUPS: ClassVar[Tuple[str, ...]] = (
        "energy_system_mockup_minimal.yaml",
        "energy_system_mockup.yaml",
        "energy_system_mockup_mfh.yaml",
    )

    #: The dependency list a fresh installation of HiSim is built from.
    REQUIREMENTS: ClassVar[Path] = Path(__file__).resolve().parent.parent / "requirements.txt"

    #: The directory holding this suite's own fixtures and the committed golden record.
    ENERGY_SYSTEMS: ClassVar[Path] = Path(__file__).resolve().parent / "energy_systems"

    #: The shipped simulation parameters this repository's example run uses, so that the tests and
    #: the documented command line exercise the same file rather than two copies of it.
    SHIPPED: ClassVar[Path] = Path(__file__).resolve().parent.parent / "energy_systems"

    #: One January day at a quarter-hour resolution, asking only for the result table.
    PARAMETERS: ClassVar[Path] = SHIPPED / "one_day_15min.simulation.yaml"

    #: The committed record of the minimal mockup, with its version and its source files pinned.
    GOLDEN: ClassVar[Path] = ENERGY_SYSTEMS / "uc1.realized.energy_system.yaml"

    #: Environment variable that rewrites the golden. Regenerating is deliberate: the resulting
    #: diff is part of the change and has to be justified, because it is the only place a change
    #: in what a run records can be seen.
    REGENERATION_ENVIRONMENT_VARIABLE: ClassVar[str] = "HISIM_REGENERATE_ENERGY_SYSTEM_GOLDENS"

    #: Values of the regeneration variable that count as "yes".
    TRUTHY_VALUES: ClassVar[Tuple[str, ...]] = ("1", "true", "yes", "on")

    #: What the metadata block says in a pinned record, in the record's own key order.
    PINNED_METADATA: ClassVar[Dict[str, Any]] = {
        "hisim_version": "<version>",
        "git_commit": "<commit>",
        "source_energy_system": "<energy system>",
        "source_simulation_parameters": "<simulation parameters>",
    }

    @classmethod
    def parameters(cls, result_directory: Path) -> Any:
        """Reads the fixture parameters and points them at a directory the test owns.

        Args:
            result_directory: The test's temporary directory.

        Returns:
            The parameters of the run.
        """
        parameters = SimulationParametersReader.read(cls.PARAMETERS)
        parameters.result_directory = str(result_directory)
        return parameters

    @classmethod
    def build(cls, source: Any, result_directory: Path, *, rerun: bool = False) -> Any:
        """Builds one energy system without running it, and points it at a results directory.

        Args:
            source: Path of the energy-system file.
            result_directory: Where a record written from the build would go.
            rerun: Whether the source is a record being re-executed.

        Returns:
            The built system.
        """
        return build_energy_system(
            source,
            cls.parameters(result_directory),
            rerun=rerun,
            simulation_parameters_path=cls.PARAMETERS,
        )

    @classmethod
    def pinned(cls, text: str) -> str:
        """Replaces the version and the source files of a rendered record with fixed words.

        Args:
            text: The rendered record.

        Returns:
            The same text with the header's version and the four metadata values pinned.
        """
        header = "# " + ProvenanceComments.HEADER_TEMPLATE.format(
            version=cls.PINNED_METADATA["hisim_version"]
        )
        lines = text.split("\n")
        lines[0] = header
        pinned = "\n".join(lines)
        for key, value in cls.PINNED_METADATA.items():
            pinned = re.sub(rf"^  {key}: .*$", f"  {key}: {value}", pinned, flags=re.MULTILINE)
        return pinned

    @classmethod
    def without_sources(cls, text: str) -> str:
        """Removes the two metadata lines naming a record's input files.

        Args:
            text: The rendered record.

        Returns:
            The same text without the ``source_`` lines, which is what two records of the same
            system are compared on.
        """
        kept = [
            line
            for line in text.split("\n")
            if not line.startswith(f"  {RunMetadata.SOURCE_KEY_PREFIX}")
        ]
        return "\n".join(kept)

    @classmethod
    def config_lines(cls, text: str) -> Dict[str, Dict[str, str]]:
        """Maps each ungrouped component of a rendered record to the lines of its config block.

        The record's indentation is fixed by the canonical style, so the block a line belongs to
        follows from how far it is indented: a component key sits at two spaces, an entry key at
        four and a configuration field at six. Anything deeper is a nested value inside a field
        and is not a field of its own.

        Args:
            text: The rendered record; only its ungrouped components are read, which is all the
                minimal household has.

        Returns:
            Per component, the rendered line of each field of its ``config`` block.
        """
        blocks: Dict[str, Dict[str, str]] = {}
        component, in_config = "", False
        for line in text.split("\n"):
            if re.fullmatch(r"  \w+:", line):
                component, in_config = line.strip()[:-1], False
                blocks[component] = {}
            elif re.fullmatch(r"    \w+:.*", line):
                in_config = line.startswith("    config:")
            elif in_config and component and re.fullmatch(r"      \w+:.*", line):
                blocks[component][line.strip().split(":", 1)[0]] = line
        return blocks

    @classmethod
    def digest(cls, directory: Path) -> Dict[str, str]:
        """Hashes every result table in a directory, so two runs can be compared in one assertion.

        Args:
            directory: A run's results directory.

        Returns:
            The file name of each table mapped to the hash of its contents.
        """
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.glob("*.csv"))
        }


class TwoGroupFile:
    """A small energy system with two groups, written twice: one switched off, one deleted.

    The identity rule says that switching a group off leaves exactly the system that deleting the
    group and every reference to it by hand would have left. Checking that against the expansion
    code would be circular, so the second side is written out here as plain text, by hand, and
    never passes through a group at all.

    The system is the minimal household with its meter moved into a group of its own and its
    boiler pair into a second one that stays on, so that both an enabled and a disabled group are
    exercised. The meter is the right component to switch: nothing reads it, so the rest of the
    household still wires when it is gone, which is what lets the comparison run over a full build
    rather than over a parse.
    """

    #: The part both variants share: everything up to the groups.
    SHARED: ClassVar[str] = """schema_version: 3
name: Identity probe
description: The minimal household with its meter in a group of its own.
components:
  weather:
    class: hisim.components.weather.Weather
    preset: standard
  occupancy:
    class: hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector
    preset: standard
  building:
    class: hisim.components.building.Building
    preset: standard
    inputs:
      - weather
      - occupancy
      - hds
  hds_controller:
    class: hisim.components.heat_distribution_system.HeatDistributionController
    preset: standard
    inputs:
      - weather
      - building
  hds:
    class: hisim.components.heat_distribution_system.HeatDistribution
    preset: standard
    config:
      position_hot_water_storage_in_system: NO_STORAGE_MASS_FLOW_FIX
    inputs:
      - building
      - hds_controller
      - input: WaterTemperatureInput
        from: boiler.WaterOutputTemperatureSh
groups:
  heating:
    enabled: true
    components:
      boiler:
        class: hisim.components.generic_boiler.GenericBoiler
        preset: condensing_gas
        inputs:
          - boiler_controller
          - input: WaterInputTemperatureSh
            from: hds.WaterTemperatureOutput
      boiler_controller:
        class: hisim.components.generic_boiler.GenericBoilerController
        preset: modulating
        inputs:
          - weather
          - hds_controller
          - input: WaterTemperatureInputFromWaterStorage
            from: hds.WaterTemperatureOutput
"""

    #: The switched-off variant: the metering group is present and its flag says no.
    SWITCHED_OFF: ClassVar[str] = SHARED + """  metering:
    enabled: false
    components:
      meter:
        class: hisim.components.electricity_meter.ElectricityMeter
        preset: standard
        inputs:
          - from: occupancy
            tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED]
            weight: 999
"""

    #: The hand-deleted variant: the metering group is simply not written, and neither is the
    #: component it held. Nothing in this text was produced by expansion.
    DELETED: ClassVar[str] = SHARED

    #: Name of the group that is switched off, which the audit has to report.
    DISABLED_GROUP: ClassVar[str] = "metering"

    @classmethod
    def write(cls, directory: Path) -> Tuple[Path, Path]:
        """Writes both variants into a directory.

        Args:
            directory: Where the two files go.

        Returns:
            The path of the switched-off variant and the path of the hand-deleted one.
        """
        switched_off = directory / "switched_off.energy_system.yaml"
        deleted = directory / "deleted.energy_system.yaml"
        switched_off.write_text(cls.SWITCHED_OFF, encoding="utf-8")
        deleted.write_text(cls.DELETED, encoding="utf-8")
        return switched_off, deleted


@pytest.fixture(name="chain", scope="module")
def chain_fixture(tmp_path_factory: pytest.TempPathFactory) -> List[Path]:
    """Runs the minimal household, then its record, then that record's record.

    Three runs rather than two, because the first record is the only one of the three that had
    anything to size and therefore the only one carrying sizing comments. What must be identical
    is what a record produces when it is re-executed, which is the second pair.

    Args:
        tmp_path_factory: pytest's directory factory; the three runs write below it.

    Returns:
        The three results directories, in order.
    """
    root = tmp_path_factory.mktemp("record_chain")
    directories = [root / "run1", root / "run2", root / "run3"]
    sources: List[Any] = [Fixtures.MINIMAL]
    for index, directory in enumerate(directories):
        run_energy_system(
            sources[index],
            Fixtures.PARAMETERS,
            result_directory=str(directory),
            rerun=index > 0,
        )
        sources.append(directory / AnnotatedEmitter.RECORD_FILENAME)
    return directories


@pytest.mark.base
def test_the_record_of_the_minimal_mockup_loads_and_validates(tmp_path: Path) -> None:
    """Catches a record that describes a run but is not a file the format can read back.

    A record is only a reproduction if it is an energy system in its own right. Writing something
    the reader rejects — an unknown key, a value the codec cannot take, an entry shape the
    validator refuses — would leave a run documented but not repeatable, and nothing in the run
    itself would notice.
    """
    built = Fixtures.build(Fixtures.MINIMAL, tmp_path)
    record = realize(built)
    path = tmp_path / AnnotatedEmitter.RECORD_FILENAME
    path.write_text(AnnotatedEmitter.render(record, build_audit(built)), encoding="utf-8")

    reloaded = load_energy_system(path)
    bindings = validate_classes(reloaded)

    assert sorted(bindings.names()) == sorted(record.all_components())


@pytest.mark.base
def test_every_entry_of_the_record_states_its_configuration_in_full(tmp_path: Path) -> None:
    """Catches a record that still names a preset, or still leaves a value to be sized.

    Either would make the record look re-executable while quietly deferring a decision to the next
    run: a preset can be re-tuned between two HiSim versions, and an ``AUTO`` field is sized again
    from whatever the system looks like then.
    """
    record = realize(Fixtures.build(Fixtures.MINIMAL, tmp_path))

    for name, entry in record.all_components().items():
        assert entry.preset is None, name
        assert entry.constructor is None, name
        assert entry.config, name
    assert "AUTO" not in dump_energy_system(record)


@pytest.mark.base
def test_the_record_names_the_provider_of_every_fact_a_component_read(tmp_path: Path) -> None:
    """Catches a record leaving the choice of provider to the binding rule of the next run.

    An author may omit a sizing source wherever exactly one component provides the fact. A record
    may not: "the only provider" is a property of the file, and a file that gains a component
    changes it. Writing every read fact out is what makes a record independent of that.
    """
    built = Fixtures.build(Fixtures.MINIMAL, tmp_path)
    record = realize(built)

    for lookup in built.configured.report.lookups:
        written = record.all_components()[lookup.consumer].sizing_sources[lookup.fact]
        references = written if isinstance(written, tuple) else (written,)
        assert f"{lookup.source}.{lookup.fact}" in [reference.text for reference in references]
    bridged = sizing_sources_bridge(record)
    assert bridged["boiler"]["heating_load_in_watt"] == "building.heating_load_in_watt"


@pytest.mark.base
def test_a_record_carries_its_metadata_and_is_refused_on_a_plain_run(tmp_path: Path) -> None:
    """Catches a record being run as though it were an authored file, and a churning metadata block.

    A record and the file it came from describe the same household and mean different things, so
    running one silently in place of the other would make it impossible to say afterwards which of
    the two a set of results came from. The block itself must also be stable: a timestamp in it
    would make every regeneration of an unchanged record a diff.
    """
    record = realize(Fixtures.build(Fixtures.MINIMAL, tmp_path))
    path = tmp_path / AnnotatedEmitter.RECORD_FILENAME
    path.write_text(dump_energy_system(record), encoding="utf-8")

    assert record.metadata is not None
    assert record.metadata[RunMetadata.HISIM_VERSION_KEY]
    commit = record.metadata[RunMetadata.GIT_COMMIT_KEY]
    assert commit is None or re.fullmatch(r"[0-9a-f]{40}", commit)
    assert set(record.metadata) == set(Fixtures.PINNED_METADATA)

    with pytest.raises(EnergySystemFormatError) as failure:
        Fixtures.build(path, tmp_path)
    assert failure.value.error_id.value == "EF-09"

    assert Fixtures.build(path, tmp_path, rerun=True).model.name == record.name


@pytest.mark.base
def test_filesystem_locations_are_written_as_portable_references(tmp_path: Path) -> None:
    """Catches a record pinning the absolute paths of the machine that produced it.

    A record that named ``/home/somebody/HiSim/inputs/weather/...`` would reproduce on exactly one
    computer, which is the opposite of what a reproduction artifact is for.
    """
    record = realize(Fixtures.build(Fixtures.MINIMAL, tmp_path))

    weather = record.components["weather"].config["source_path"]
    assert weather.startswith("${"), weather
    assert not os.path.isabs(weather)


@pytest.mark.base
def test_re_running_a_record_reproduces_it_and_its_results(chain: List[Path]) -> None:
    """Catches the reproduction promise breaking, which is the whole reason a record exists.

    Three things are checked and each is a different way the promise can fail. The second run must
    decide nothing — its audit lists no sized field. Its record must say exactly what the record it
    read said, apart from the comments the first run had reason to write and it does not. And the
    numbers must come out the same, which is the only check that would notice a value being
    written in a spelling that reads back as something slightly different.
    """
    first, second, third = (path / AnnotatedEmitter.RECORD_FILENAME for path in chain)

    assert Fixtures.without_sources(second.read_text(encoding="utf-8")) == Fixtures.without_sources(
        third.read_text(encoding="utf-8")
    )
    written = [
        Fixtures.without_sources(dump_energy_system(load_energy_system(path)))
        for path in (first, second)
    ]
    assert written[0] == written[1]
    assert Fixtures.digest(chain[0]) == Fixtures.digest(chain[1])
    assert Fixtures.digest(chain[0]) == Fixtures.digest(chain[2])
    assert len(Fixtures.digest(chain[0])) >= 60


@pytest.mark.base
def test_the_audit_of_a_re_run_shows_that_nothing_was_sized(chain: List[Path]) -> None:
    """Catches a re-run computing a value the record already states.

    The absence of sized fields in the second run's audit is the machine-readable form of "this run
    made no decisions", and it is what makes the record rather than the code the authority on every
    number in the results.
    """
    audit = (chain[1] / AuditWriter.AUDIT_FILENAME).read_text(encoding="utf-8")

    assert "sized_fields: []" in audit
    assert "sized_fields:\n" not in audit


@pytest.mark.base
def test_every_sized_value_carries_a_provenance_comment(chain: List[Path]) -> None:
    """Catches a record stating a computed number without saying where it came from.

    The comment is the only place a reader of the record alone learns that the boiler's power came
    from the building's heating load rather than from the preset, and that is the question a person
    opening a record most often has.
    """
    text = (chain[0] / AnnotatedEmitter.RECORD_FILENAME).read_text(encoding="utf-8")
    built = build_energy_system(
        Fixtures.MINIMAL, Fixtures.parameters(chain[0]), simulation_parameters_path=Fixtures.PARAMETERS
    )
    audit = build_audit(built)

    assert text.startswith("# " + ProvenanceComments.header())
    blocks = Fixtures.config_lines(text)
    for component in audit.components:
        for sized in component.sized_fields:
            line = blocks[component.name][sized.field]
            assert "#" in line, line
    assert "# built from preset: condensing_gas" in text
    assert "# author default (constant law)" in text
    assert "# overrides preset default PARALLEL" in text
    assert "# unique provider, written for re-execution" in text


@pytest.mark.base
def test_stripping_every_comment_changes_nothing_about_the_run(chain: List[Path], tmp_path: Path) -> None:
    """Catches a comment quietly becoming load-bearing.

    Provenance in the margin is only safe as long as nothing reads it back. If a value ever lived
    in a comment alone, a record stripped of its comments would run differently, and every tool
    that rewrites YAML would silently change results.
    """
    annotated = (chain[0] / AnnotatedEmitter.RECORD_FILENAME).read_text(encoding="utf-8")
    stripped = strip_comments(annotated)
    path = tmp_path / "stripped.energy_system.yaml"
    path.write_text(stripped, encoding="utf-8")

    assert "#" not in stripped
    results = tmp_path / "results"
    run_energy_system(path, Fixtures.PARAMETERS, result_directory=str(results), rerun=True)

    assert Fixtures.digest(results) == Fixtures.digest(chain[0])


@pytest.mark.base
@pytest.mark.parametrize("mockup", Fixtures.ALL_MOCKUPS)
def test_the_annotated_writer_agrees_with_the_canonical_writer(mockup: str) -> None:
    """Catches the two writers of this format drifting into two styles.

    The record writer runs on a second YAML library because the first cannot attach comments, and
    two libraries writing the same document is exactly how a format grows a second style. With
    nothing to annotate the two must produce the same bytes, which is what makes the second library
    a comment mechanism rather than a second emitter.
    """
    model = load_energy_system(Fixtures.MOCKUPS / mockup)

    assert AnnotatedEmitter.render(model) == dump_energy_system(model)


@pytest.mark.base
def test_the_annotated_writer_agrees_with_the_canonical_writer_on_a_record(chain: List[Path]) -> None:
    """Catches the drift above on the one document that really is written by both.

    A record carries nulls, floats, symbolic paths and nested blocks that a hand-written mockup
    never does, so it is the harder half of the same guarantee.
    """
    model = load_energy_system(chain[0] / AnnotatedEmitter.RECORD_FILENAME)

    assert AnnotatedEmitter.render(model) == dump_energy_system(model)


@pytest.mark.base
def test_both_writers_spell_a_numpy_number_as_a_plain_one() -> None:
    """Both YAML writers render numpy scalars and arrays as plain values that load back as ``float``, ``int``, ``bool``, ``list``.

    A config field may hold a numpy scalar (for example a scale factor computed from the sized building),
    and a writer without a representer for it refuses the whole document; ``air_conditioned_house`` could
    not be recorded for this reason. Both writers are checked because the record writer runs on a second
    YAML library, and the two must produce the same bytes.
    """
    document = {
        "single": np.float32(0.5),
        "double": np.float64(0.5364243908677532),
        "integer": np.int64(7),
        "boolean": np.bool_(True),
        "array": np.array([1.5, 2.5]),
    }
    expected = {
        "single": 0.5,
        "double": 0.5364243908677532,
        "integer": 7,
        "boolean": True,
        "array": [1.5, 2.5],
    }

    canonical = EnergySystemEmitter.render(dict(document))

    annotated_stream = io.StringIO()
    annotated_writer = YAML()
    annotated_writer.Representer = CanonicalRepresenter.configured()
    annotated_writer.dump(dict(document), annotated_stream)

    for written, writer in ((canonical, "canonical"), (annotated_stream.getvalue(), "annotated")):
        loaded = yaml.safe_load(written)
        assert loaded == expected, f"the {writer} writer changed the values: {loaded}"
        assert [type(loaded[key]) for key in ("single", "double", "integer", "boolean")] == [
            float,
            float,
            int,
            bool,
        ], f"the {writer} writer left a value in a type a plain YAML reader would not produce"


@pytest.mark.base
def test_a_disabled_group_records_exactly_what_deleting_it_by_hand_would(tmp_path: Path) -> None:
    """Catches an off switch leaving a trace in the record of the system it removed.

    The identity is what makes a group an editing convenience rather than a semantic feature: a
    reader of a record must not be able to tell whether the household never had a meter or had one
    that was switched off. The hand-deleted side of the comparison is written out as text and never
    goes near the expansion code, so the two sides cannot agree by sharing a bug.
    """
    switched_off, deleted = TwoGroupFile.write(tmp_path)

    off_build = Fixtures.build(switched_off, tmp_path / "off")
    deleted_build = Fixtures.build(deleted, tmp_path / "gone")
    off_record = Fixtures.without_sources(
        AnnotatedEmitter.render(realize(off_build), build_audit(off_build))
    )
    deleted_record = Fixtures.without_sources(
        AnnotatedEmitter.render(realize(deleted_build), build_audit(deleted_build))
    )

    assert off_record == deleted_record
    assert "hisim.components.electricity_meter" not in off_record
    assert build_audit(off_build).disabled_groups == (TwoGroupFile.DISABLED_GROUP,)
    assert not build_audit(deleted_build).disabled_groups
    assert "heating" in off_record, "an enabled group is part of the system and stays"


@pytest.mark.base
def test_the_wire_log_keeps_the_shape_the_older_machinery_writes(chain: List[Path]) -> None:
    """Catches the wire log growing a second shape that downstream tooling cannot read.

    The file name and the ``From``/``To`` entry shape are shared with what HiSim's own connection
    logging appends, on purpose: one artifact, one format, whichever executor produced the run. A
    renamed key here would break every reader of that file without any test of this package
    noticing.
    """
    entries = json.loads((chain[0] / AuditWriter.CONNECTIONS_FILENAME).read_text(encoding="utf-8"))

    assert isinstance(entries, list) and entries
    for entry in entries:
        assert set(entry) == {"From", "To"}
        assert set(entry["From"]) == {"Component", "Field"}
        assert set(entry["To"]) == {"Component", "Field"}
        assert all(isinstance(value, str) and value for value in entry["From"].values())
        assert all(isinstance(value, str) and value for value in entry["To"].values())
    assert {"From": {"Component": "weather", "Field": "Altitude"},
            "To": {"Component": "building", "Field": "Altitude"}} in entries


@pytest.mark.base
def test_the_record_of_the_minimal_mockup_matches_its_committed_golden(tmp_path: Path) -> None:
    """Catches any change in what a run records, which is the one place such a change is visible.

    Every other test here checks a property; this one checks the text. A preset that gains a field,
    a law that starts rounding differently, a comment that changes wording — none of them breaks a
    property, and all of them change what a person reads in a results directory. Rewriting the
    golden is deliberate and its diff belongs in the change that caused it.
    """
    built = Fixtures.build(Fixtures.MINIMAL, tmp_path)
    record = realize(built).model_copy(update={"metadata": dict(Fixtures.PINNED_METADATA)})
    produced = Fixtures.pinned(AnnotatedEmitter.render(record, build_audit(built)))

    if os.environ.get(Fixtures.REGENERATION_ENVIRONMENT_VARIABLE, "").lower() in Fixtures.TRUTHY_VALUES:
        Fixtures.GOLDEN.write_text(produced, encoding="utf-8")
    assert Fixtures.GOLDEN.is_file(), (
        f"the committed record is missing; set {Fixtures.REGENERATION_ENVIRONMENT_VARIABLE}=1 "
        "to write it deliberately."
    )

    assert produced == Fixtures.GOLDEN.read_text(encoding="utf-8")


@pytest.mark.base
def test_both_yaml_libraries_the_format_uses_are_declared_dependencies() -> None:
    """Catches a library the record path needs being importable here and missing on a fresh install.

    The reader runs on one YAML library and the comment writer on another, and a developer machine
    has both for reasons that have nothing to do with this package. An undeclared dependency
    therefore fails nowhere until somebody installs HiSim from scratch and a run cannot write its
    own record.
    """
    declared = {
        line.split("#", 1)[0].strip().split("=")[0].split("<")[0].split(">")[0].lower()
        for line in (Fixtures.REQUIREMENTS.read_text(encoding="utf-8")).splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert "pyyaml" in declared
    assert "ruamel.yaml" in declared


@pytest.mark.base
def test_a_re_run_that_does_not_reproduce_its_record_is_reported(tmp_path: Path) -> None:
    """Catches the reproduction check itself being asleep.

    The check is the only thing standing between "records reproduce runs" and "records are hoped to
    reproduce runs", so a record that leaves something for the run to decide has to be refused
    rather than run. Re-opening one field is the smallest possible breach, and the one a hand-edit
    of a record most easily causes.
    """
    built = Fixtures.build(Fixtures.MINIMAL, tmp_path)
    record = realize(built)
    text = AnnotatedEmitter.render(record, build_audit(built))
    reopened = text.replace(
        "      minimal_thermal_power_in_watt: 0.0 #", "      minimal_thermal_power_in_watt: AUTO #"
    )
    tampered = tmp_path / "tampered.energy_system.yaml"
    tampered.write_text(reopened, encoding="utf-8")

    rerun = Fixtures.build(tampered, tmp_path / "again", rerun=True)
    with pytest.raises(EnergySystemRecordError) as failure:
        write_records(rerun, str(tmp_path / "again"))

    assert failure.value.error_id.value == "EF-61"
    assert "minimal_thermal_power_in_watt" in str(failure.value)
