"""Tests for the exported JSON Schema: that it is valid, current, and says what it should.

The schema is the only part of this format that runs outside HiSim — an editor loads it and
decides on its own what to underline — so the tests have to check it as a document rather than as
code: it must satisfy its own dialect's meta-schema, it must accept a file the executor accepts,
and it must reject in place each of the mistakes the executor rejects with a message. The last
one is checked class by class over every converted class rather than on one example, because a
schema generated per class can be right for the class the author of the generator had in mind
and wrong for the next one.

Two further promises are pinned here. The committed file is regenerated and compared, so a
conversion that adds a preset cannot land without the schema that offers it. And the three
normative mockups are validated as a set: the minimal one passes completely, while the two large
ones fail only on entries whose classes are not converted yet — the same gap the class-bound
validator pins, seen from the other side.
"""

# clean

from typing import Any, ClassVar, Dict, List, Tuple, cast

import jsonschema
import pytest
import yaml

from hisim.energy_system.schema_classes import ComponentClassScan
from hisim.energy_system.schema_export import (
    build_schema,
    default_schema_path,
    schema_is_current,
)
from tests.test_energy_system_classes import ExpectedFailures
from tests.test_energy_system_loader import Mockups


class Documents:
    """Builds the smallest energy-system document that can carry one entry under test.

    A schema test is only about one key at a time, so every fixture here is the same two-line
    document with a single component in it. Building them from one place keeps a test's body down
    to the key it is actually about.
    """

    #: Name every one-entry document gives its component.
    ENTRY_NAME: ClassVar[str] = "under_test"

    @classmethod
    def with_entry(cls, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Wraps one component entry in a complete, otherwise valid document.

        Args:
            entry: The entry's body, class path included.

        Returns:
            The document as plain data.
        """
        return {
            "schema_version": 3,
            "name": "one entry under test",
            "components": {cls.ENTRY_NAME: entry},
        }


@pytest.fixture(name="schema", scope="module")
def fixture_schema() -> Dict[str, Any]:
    """Loads the committed schema once for the whole module."""
    return cast(Dict[str, Any], yaml.safe_load(default_schema_path().read_text(encoding="utf-8")))


@pytest.fixture(name="validator", scope="module")
def fixture_validator(schema: Dict[str, Any]) -> Any:
    """Builds one validator over the committed schema for the whole module."""
    return jsonschema.Draft202012Validator(schema)


@pytest.fixture(name="converted", scope="module")
def fixture_converted() -> Tuple[Tuple[str, Dict[str, Any]], ...]:
    """Returns each converted component class as its path and its own conditional branch."""
    classes = ComponentClassScan.collect()
    branches = build_schema(classes)["$defs"]["entry"]["allOf"]
    return tuple(
        (ComponentClassScan.path_of(component), branch["then"]["properties"])
        for component, branch in zip(classes, branches)
    )


def errors_of(validator: Any, document: Dict[str, Any]) -> List[Any]:
    """Returns every validation error of one document, deepest path first.

    Args:
        validator: The validator to run.
        document: The document to check.

    Returns:
        The errors, ordered by their path so a test can name the entry each one is about.
    """
    return sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))


def entry_of(error: Any) -> str:
    """Names the component entry one validation error is about.

    An error's path runs through the document, and a component can sit at the top level or inside
    a group, so the entry is whatever key follows the last ``components`` on the way down.

    Args:
        error: One validation error.

    Returns:
        The component's name, or the empty string for an error about the document itself.
    """
    path = [str(part) for part in error.absolute_path]
    if "components" not in path:
        return ""
    return path[len(path) - 1 - path[::-1].index("components") + 1]


@pytest.mark.base
def test_the_exported_schema_is_a_valid_schema_of_its_own_dialect(schema: Dict[str, Any]) -> None:
    """Catches a generated construct that no validator would accept in the first place."""
    jsonschema.Draft202012Validator.check_schema(schema)


@pytest.mark.base
def test_the_committed_schema_is_what_an_export_writes_today() -> None:
    """Catches a conversion that added a preset, a field or a class without re-exporting.

    The committed file is what an editor loads, so a stale one silently offers the wrong
    completions and underlines correct files; regenerating it belongs in the change that made it
    stale, which is exactly what this failure asks for.
    """
    assert schema_is_current(), (
        "hisim/energy_system_v3.schema.json is out of date; regenerate it with "
        "'hisim energy-system schema'."
    )


@pytest.mark.base
def test_the_minimal_mockup_validates_completely(validator: Any) -> None:
    """Catches a schema that rejects the one mockup every class of which is converted."""
    document = yaml.safe_load(Mockups.path(Mockups.NAMES[0]).read_text(encoding="utf-8"))

    assert errors_of(validator, document) == []


@pytest.mark.base
@pytest.mark.parametrize("name", Mockups.NAMES[1:])
def test_the_large_mockups_fail_only_on_entries_the_class_pin_lists(validator: Any, name: str) -> None:
    """Catches the schema rejecting an entry the classes themselves would accept.

    The two large mockups are written against classes P4 has still to convert, so a rejection of
    one of their entries is expected; a rejection of any *other* entry would mean the schema is
    stricter than the executor, which is the one thing a schema must never be. The weather entry
    of the heat-pump mockup is the sanctioned exception: it names a location the enumeration does
    not carry, which the schema can see and the class-bound validator — which checks argument
    names and not yet their values — cannot.

    The two sides look at different sets, which the comparison has to account for: a schema reads
    the file as written, switched-off groups included, while the class-bound validator runs after
    group expansion and never sees them. So the entries of a disabled group join the accepted set
    rather than the pin.
    """
    document = yaml.safe_load(Mockups.path(name).read_text(encoding="utf-8"))
    pinned = set(ExpectedFailures.BY_MOCKUP[name])
    switched_off = {
        entry
        for group in document.get("groups", {}).values()
        if not group["enabled"]
        for entry in group["components"]
    }
    rejected = {entry_of(error) for error in errors_of(validator, document)}

    assert rejected - {"weather"} - switched_off <= pinned
    assert pinned - rejected == set()


@pytest.mark.base
def test_every_converted_class_rejects_an_unknown_preset(
    validator: Any, converted: Tuple[Tuple[str, Dict[str, Any]], ...]
) -> None:
    """Catches a class whose branch forgot to close the preset list (AC-P2.6).

    A converted class may legitimately have no preset at all — ``CarConfig`` has none, because
    every one of its builders needs the name of the LoadProfileGenerator profile the car drives
    by. The schema then spells its ``preset`` member as ``false``, forbidding the key outright,
    and the closed-list promise still has to hold: *every* preset name must be refused, not
    merely the unknown ones.
    """
    for class_path, properties in converted:
        document = Documents.with_entry({"class": class_path, "preset": "no_such_preset"})
        assert errors_of(validator, document), f"{class_path} accepts an unknown preset"

        if properties["preset"] is False:
            continue
        known = list(properties["preset"]["enum"])
        assert errors_of(
            validator, Documents.with_entry({"class": class_path, "preset": known[0]})
        ) == [], f"{class_path} rejects its own preset {known[0]}"


@pytest.mark.base
def test_every_converted_class_rejects_an_unknown_config_field(
    validator: Any, converted: Tuple[Tuple[str, Dict[str, Any]], ...]
) -> None:
    """Catches a class whose branch left the ``config`` block open (AC-P2.6).

    A class without presets is checked with a bare ``config`` block, since naming a preset it
    does not have would make the entry fail for the wrong reason.
    """
    for class_path, properties in converted:
        entry: Dict[str, Any] = {"class": class_path, "config": {"no_such_field": 1}}
        if properties["preset"] is not False:
            entry["preset"] = list(properties["preset"]["enum"])[0]

        assert errors_of(validator, Documents.with_entry(entry)), (
            f"{class_path} accepts a config key that is not one of its fields"
        )


@pytest.mark.base
def test_an_unknown_component_class_is_rejected(validator: Any) -> None:
    """Catches a schema that leaves the class list open, which would give no completion at all."""
    document = Documents.with_entry({"class": "hisim.components.nowhere.Nothing"})

    assert errors_of(validator, document)


@pytest.mark.base
def test_auto_is_accepted_on_a_sizable_field_and_refused_elsewhere(validator: Any) -> None:
    """Catches the sizing sentinel leaking into fields no law can size.

    ``AUTO`` is the one value whose legality depends on a declaration rather than on a type, so
    the schema has to carry that declaration; a boiler's power band takes it and its efficiency,
    which nothing sizes, does not.
    """
    boiler = "hisim.components.generic_boiler.GenericBoiler"
    sizable = {"class": boiler, "preset": "condensing_gas", "config": {"maximal_thermal_power_in_watt": "AUTO"}}
    plain = {"class": boiler, "preset": "condensing_gas", "config": {"eff_th_max": "AUTO"}}

    assert errors_of(validator, Documents.with_entry(sizable)) == []
    assert errors_of(validator, Documents.with_entry(plain))


@pytest.mark.base
def test_a_named_constructor_and_its_parameters_are_in_the_schema(validator: Any) -> None:
    """Catches the constructor form being missing from the schema (AC-P2.15).

    The weather is the class the format's constructor form exists for: a station is an open
    identifier space, so the file writes ``constructor: {for_location: {location: AACHEN}}`` and
    the schema has to accept exactly that, name and parameter alike.
    """
    weather = "hisim.components.weather.Weather"
    good = {"class": weather, "constructor": {"for_location": {"location": "AACHEN"}}}
    wrong_name = {"class": weather, "constructor": {"for_place": {"location": "AACHEN"}}}
    wrong_argument = {"class": weather, "constructor": {"for_location": {"place": "AACHEN"}}}

    assert errors_of(validator, Documents.with_entry(good)) == []
    assert errors_of(validator, Documents.with_entry(wrong_name))
    assert errors_of(validator, Documents.with_entry(wrong_argument))


@pytest.mark.base
def test_a_sizing_source_naming_a_fact_the_class_does_not_read_is_rejected(validator: Any) -> None:
    """Catches the sizing-source keys being left open, which is where a typo hides longest."""
    boiler = "hisim.components.generic_boiler.GenericBoiler"
    good = {
        "class": boiler,
        "preset": "condensing_gas",
        "sizing_sources": {"heating_load_in_watt": "building.heating_load_in_watt"},
    }
    typo = {
        "class": boiler,
        "preset": "condensing_gas",
        "sizing_sources": {"heating_load_in_wat": "building.heating_load_in_watt"},
    }

    assert errors_of(validator, Documents.with_entry(good)) == []
    assert errors_of(validator, Documents.with_entry(typo))


@pytest.mark.base
def test_a_schema_of_two_classes_carries_one_conditional_branch_each() -> None:
    """Catches the generator collapsing per-class knowledge into one shared constraint.

    Built over two classes rather than over the repository, so that the shape of what the
    generator produces is checked without the result depending on how far the conversion has got.
    """
    from hisim.components.generic_boiler import GenericBoiler  # noqa: PLC0415  (one test only)
    from hisim.components.weather import Weather  # noqa: PLC0415  (one test only)

    schema = build_schema([GenericBoiler, Weather])
    entry = schema["$defs"]["entry"]

    assert len(entry["allOf"]) == 2
    assert entry["properties"]["class"]["enum"] == [
        "hisim.components.generic_boiler.GenericBoiler",
        "hisim.components.weather.Weather",
    ]
    assert entry["allOf"][0]["then"]["properties"]["constructor"] is False
    assert "for_location" in entry["allOf"][1]["then"]["properties"]["constructor"]["properties"]
