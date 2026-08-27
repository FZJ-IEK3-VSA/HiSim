"""Tests for building the configurations of an energy-system file and resolving their sizing.

This is the stage where a file stops describing a system and starts being one: presets and
named constructors are called, the sparse overrides are written on top with every value
decoded into the type its field holds, and the whole set goes through the sizing kernel so
that each field a law owns comes back as a number. The tests below cover the three things
that can go wrong and the three that must go right.

Wrong: a value that does not fit its field, a sizing fact nobody provides, and a fact several
components provide with nothing in the file to settle the choice — the last one is the "cliff"
the format is designed around, and its message has to carry the candidates and a block the
author can paste. Right: an enum-typed field holds the member and not the string that spells
it, ``AUTO`` in a ``config`` block re-opens a field the preset had pinned, and a component
whose facts nobody reads produces a warning rather than a refusal.

The eight failure modes of the sizing kernel are provoked through the kernel itself and their
messages fed to the wrapper's classifier, so that a reworded kernel message fails here loudly
instead of silently degrading every ``EF-4x`` to the catch-all. Each test states the failure
mode it catches.
"""

# clean

import dataclasses
import enum
import importlib
import pkgutil
import typing
from dataclasses import dataclass
from typing import Any, ClassVar, Iterator, List, Tuple, Type

import pytest
from dataclasses_json import dataclass_json

from hisim.config import ComponentID, ConfigBase, Many, Self, Sizable, Size, sized_field
from hisim.config.contributions import FactContribution
from hisim.config.engine import resolve_all
from hisim.config.laws import SizingError
from hisim.config.sizing import AUTO, SizedFieldMetadata
from hisim.energy_system import (
    EnergySystemBindingError,
    EnergySystemErrorId,
    EnergySystemFile,
    EnergySystemSizingError,
    expand_groups,
)
from hisim.energy_system.configure import configure_energy_system
from hisim.energy_system.document import RawDocument
from hisim.energy_system.loader import EnergySystemReader
from hisim.energy_system.sizing_bridge import KernelFailure, sizing_sources_bridge


class Systems:
    """Renders and configures the small files the sizing tests are built from.

    Only classes that already carry presets and laws appear here — a building, a gas boiler, a
    heat distribution system — because the point of every fixture is the sizing relation
    between them and not the class that happens to hold it. Two buildings provide one fact
    twice, which is the smallest system in which the format's central ambiguity arises.
    """

    #: A boiler whose power band is sized from a building's heating load: the consumer half of
    #: every fixture below.
    BOILER: ClassVar[str] = """  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
"""

    @classmethod
    def parse(cls, entries: str) -> EnergySystemFile:
        """Parses one inline document made of the given component entries.

        Args:
            entries: The body of the ``components`` block, indented by two spaces.

        Returns:
            The parsed, unexpanded file.
        """
        text = f"schema_version: 3\nname: sizing under test\ncomponents:\n{entries}"
        return EnergySystemReader.build(RawDocument.parse_text(text, "inline"), "inline")

    @classmethod
    def configure(cls, entries: str) -> Any:
        """Parses, expands and configures one inline document.

        Args:
            entries: The body of the ``components`` block, indented by two spaces.

        Returns:
            The configured system.
        """
        expanded, _ = expand_groups(cls.parse(entries))
        return configure_energy_system(expanded)

    @classmethod
    def building(cls, name: str) -> str:
        """Renders one building entry under the given name.

        Args:
            name: The component name, which is also the entry's key.

        Returns:
            The entry text, indented for a ``components`` block.
        """
        return f"  {name}:\n    class: hisim.components.building.Building\n    preset: standard\n"


@dataclass_json
@dataclass
class _NullProviderConfig(ConfigBase):
    """A provider whose declared fact is switched off and therefore contributed as null.

    Exists only to make the kernel produce its null-provider message, which is one of the eight
    conditions the wrapper has to recognize and which no converted component class can produce
    today.
    """

    component_id: ComponentID

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_energy_system_configure._NullProviderConfig"


_NullProviderConfig.SIZING_CONTRIBUTIONS = (
    FactContribution(
        facts=("maximal_thermal_power_in_watt",),
        compute=lambda config, context: {"maximal_thermal_power_in_watt": None},
    ),
)


@dataclass_json
@dataclass
class _PowerReaderConfig(ConfigBase):
    """A consumer sized from a single provider's contributed power fact."""

    component_id: ComponentID
    band_in_watt: Sizable[float] = sized_field(rule=0.5 * Size.MAXIMAL_THERMAL_POWER_IN_WATT)

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_energy_system_configure._PowerReaderConfig"


@dataclass_json
@dataclass
class _PowerProviderConfig(ConfigBase):
    """A provider contributing a fixed power fact, so a reader of it has something to bind to."""

    component_id: ComponentID

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_energy_system_configure._PowerProviderConfig"


_PowerProviderConfig.SIZING_CONTRIBUTIONS = (
    FactContribution(
        facts=("maximal_thermal_power_in_watt",),
        compute=lambda config, context: {"maximal_thermal_power_in_watt": 5_000.0},
    ),
)


@dataclass_json
@dataclass
class _CyclicConfig(ConfigBase):
    """A configuration whose two sizable fields read each other through ``Self``."""

    component_id: ComponentID
    first: Sizable[float] = sized_field(rule=Self("second") * 2.0)
    second: Sizable[float] = sized_field(rule=Self("first") * 0.5)

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_energy_system_configure._CyclicConfig"


@dataclass_json
@dataclass
class _ManyReaderConfig(ConfigBase):
    """A consumer whose law aggregates over every provider of one fact."""

    component_id: ComponentID
    total_in_watt: Sizable[float] = sized_field(rule=Many(Size.MAXIMAL_THERMAL_POWER_IN_WATT))

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_energy_system_configure._ManyReaderConfig"


def kernel_message(configs: List[Any], sources: Any = None) -> str:
    """Runs the sizing kernel on configurations chosen to fail, and returns its message.

    The wrapper recognizes the kernel's eight conditions by their prose, so the tests must feed
    it prose the kernel really produces rather than a hand-copied string that would keep
    matching long after the kernel was reworded.

    Args:
        configs: The configurations to resolve.
        sources: The per-consumer sources mapping, when the failure needs one.

    Returns:
        The message of the exception the kernel raised.

    Raises:
        AssertionError: If the kernel unexpectedly succeeded.
    """
    try:
        resolve_all(configs, sources=sources)
    except SizingError as error:
        return str(error)
    raise AssertionError("the kernel was expected to refuse these configurations")


def sizable_enum_fields() -> Iterator[Tuple[type, str, Type[enum.Enum]]]:
    """Yields every enum-typed sizable field HiSim's component packages declare.

    Walking the whole component tree rather than a hand-kept list is what makes the enum
    contract below a repository-wide guarantee: a class converted next month is covered the
    day it lands, without anyone remembering to extend a fixture.

    Returns:
        Triples of configuration class, field name and the enum the field holds.
    """
    import hisim.components as components_package

    seen: set = set()
    for module_info in pkgutil.walk_packages(components_package.__path__, f"{components_package.__name__}."):
        try:
            module = importlib.import_module(module_info.name)
        except Exception:  # pylint: disable=broad-except
            continue
        for attribute in vars(module).values():
            if not (isinstance(attribute, type) and dataclasses.is_dataclass(attribute)):
                continue
            if not issubclass(attribute, ConfigBase) or attribute in seen:
                continue
            seen.add(attribute)
            try:
                hints = typing.get_type_hints(attribute)
            except Exception:  # pylint: disable=broad-except
                continue
            for field in dataclasses.fields(attribute):
                if SizedFieldMetadata.LAW not in field.metadata:
                    continue
                for argument in typing.get_args(hints.get(field.name)):
                    if isinstance(argument, type) and issubclass(argument, enum.Enum):
                        yield attribute, field.name, argument


@pytest.mark.base
def test_a_system_of_converted_classes_is_configured_and_sized() -> None:
    """Catches a configuring stage that leaves a law-owned field open or loses an override.

    Everything else in this module tests a refusal, and a refusal is only evidence of a rule if
    the ordinary case actually goes through: the boiler's power band has to come out as the
    number the building's heating load implies.
    """
    system = Systems.configure(Systems.building("building") + Systems.BOILER)

    assert [name for name, _ in system.configs] == ["building", "boiler"]
    boiler = system.config_of("boiler")
    assert isinstance(boiler.maximal_thermal_power_in_watt, float)
    assert boiler.maximal_thermal_power_in_watt > 0.0
    assert boiler.maximal_thermal_power_in_watt is not AUTO


@pytest.mark.base
def test_two_providers_of_one_fact_are_refused_with_the_candidates_and_a_paste_ready_block() -> None:
    """Catches the format's central cliff being reported without a way over it.

    A fact with two providers is not something the machine may decide, so the run stops — but
    the author has to be told which components could provide it and exactly what to write, or
    the rule turns a working file into a puzzle the moment a second provider is added.
    """
    with pytest.raises(EnergySystemSizingError) as raised:
        Systems.configure(Systems.building("house_a") + Systems.building("house_b") + Systems.BOILER)

    message = str(raised.value)
    assert raised.value.error_id is EnergySystemErrorId.SIZING_AMBIGUOUS
    assert raised.value.location == "components.boiler"
    assert "house_a" in message and "house_b" in message
    assert "sources=" in message and "heating_load_in_watt" in message


@pytest.mark.base
def test_a_source_line_settles_the_ambiguity_the_two_providers_created() -> None:
    """Catches a ``sizing_sources`` block that is validated but never reaches the kernel.

    The bridge is a plain translation and easy to get subtly wrong — the wrong key, the file's
    order lost, the enabled set not respected — and every such mistake looks like the file
    having no effect at all.
    """
    system = Systems.configure(
        Systems.building("house_a")
        + Systems.building("house_b")
        + """  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    sizing_sources:
      heating_load_in_watt: house_b.heating_load_in_watt
      number_of_apartments: house_b.number_of_apartments
"""
    )

    lookups = {(entry.consumer, entry.fact): entry.source for entry in system.report.lookups}
    assert lookups[("boiler", "heating_load_in_watt")] == "house_b"


@pytest.mark.base
def test_a_fact_nobody_provides_is_refused_naming_the_consumer() -> None:
    """Catches a missing provider surfacing as an unresolved ``AUTO`` deep in a component.

    A boiler on its own has nothing to size from; if that passed, the sentinel would travel
    into the component and fail there, naming a field rather than the component that is
    missing from the file.
    """
    with pytest.raises(EnergySystemSizingError) as raised:
        Systems.configure(Systems.BOILER)

    assert raised.value.error_id is EnergySystemErrorId.SIZING_UNPROVIDED
    assert raised.value.location == "components.boiler"
    assert "heating_load_in_watt" in str(raised.value)


@pytest.mark.base
def test_a_source_naming_a_component_that_does_not_provide_the_fact_is_refused() -> None:
    """Catches a plausible but wrong provider being accepted and then quietly ignored.

    The component exists and the fact exists, so nothing before the kernel can see the mistake;
    the rejection has to name the consumer whose line is wrong, not the component it points at.
    """
    with pytest.raises(EnergySystemSizingError) as raised:
        Systems.configure(
            Systems.building("building")
            + """  hds:
    class: hisim.components.heat_distribution_system.HeatDistribution
    preset: standard
    config:
      heating_system: FLOORHEATING
      water_mass_flow_rate_in_kg_per_second: 0.5
      absolute_conditioned_floor_area_in_m2: 100.0
  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    sizing_sources:
      heating_load_in_watt: hds.heating_load_in_watt
"""
        )

    assert raised.value.error_id is EnergySystemErrorId.SIZING_NOT_A_PROVIDER
    assert raised.value.location == "components.boiler"


@pytest.mark.base
def test_a_list_written_for_a_fact_a_law_reads_once_is_refused() -> None:
    """Catches the list and scalar spellings being treated as interchangeable.

    A list means "aggregate over these"; a law that reads one value cannot aggregate, and
    silently taking the first element would make the file's meaning depend on its order.
    """
    with pytest.raises(EnergySystemSizingError) as raised:
        Systems.configure(
            Systems.building("building")
            + """  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    sizing_sources:
      heating_load_in_watt: []
"""
        )

    assert raised.value.error_id is EnergySystemErrorId.SIZING_SHAPE_MISMATCH
    assert raised.value.location == "components.boiler"


@pytest.mark.base
def test_the_bridge_carries_both_source_shapes_including_the_empty_list() -> None:
    """Catches a bridge that drops the list spelling or normalizes an empty list away.

    An empty list is the format's explicit "nobody provides this fact", so it has to reach the
    kernel as a list; turning it into an absent key would silently restore the guessing the
    line was written to prevent.
    """
    model = Systems.parse(
        Systems.building("house_a")
        + Systems.building("house_b")
        + """  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    sizing_sources:
      heating_load_in_watt: house_a.heating_load_in_watt
      number_of_apartments: []
"""
    )

    bridge = sizing_sources_bridge(model)
    assert bridge == {
        "boiler": {"heating_load_in_watt": "house_a.heating_load_in_watt", "number_of_apartments": []}
    }


@pytest.mark.base
def test_the_bridge_only_carries_the_components_that_survived_expansion() -> None:
    """Catches the uniqueness rule being evaluated over the file instead of the enabled set.

    A switched-off add-on must not force a source line on anybody: if its entries reached the
    kernel, disabling a group would change what the rest of the file has to say.
    """
    model = Systems.parse(
        Systems.building("house_a")
        + """  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
"""
    )
    grouped = model.model_copy(
        update={
            "groups": {},
            "components": dict(model.components),
        }
    )
    expanded, _ = expand_groups(grouped)

    assert not sizing_sources_bridge(expanded)


@pytest.mark.base
def test_an_enum_typed_field_written_in_a_file_holds_the_member_afterwards() -> None:
    """Catches an enum-typed field left holding the string that spells the member.

    HiSim's configuration enums derive from ``str``, so a forgotten decode passes every
    equality check and fails only where a component compares by identity — the one failure this
    codec exists to prevent, and the one an equality assertion would not catch.
    """
    from hisim.components.heat_distribution_system import HeatDistributionSystemType

    system = Systems.configure(
        """  hds:
    class: hisim.components.heat_distribution_system.HeatDistribution
    preset: standard
    config:
      heating_system: FLOORHEATING
      water_mass_flow_rate_in_kg_per_second: 0.5
      absolute_conditioned_floor_area_in_m2: 100.0
"""
    )

    assert system.config_of("hds").heating_system is HeatDistributionSystemType.FLOORHEATING


@pytest.mark.base
def test_every_enum_typed_sizable_field_in_the_repository_decodes_to_its_member() -> None:
    """Catches a sizable field declared with an enum type but without an enum-aware decoder.

    A sizable field replaces the serialization layer's decoder with one that understands
    ``AUTO``, which silently disables the enum handling unless the declaration says which enum
    it is. The class-creation check fills that in; this asserts the result across the whole
    component tree, so a class converted later cannot reintroduce the trap.
    """
    checked = 0
    for config_class, field_name, enum_class in sizable_enum_fields():
        field = {item.name: item for item in dataclasses.fields(config_class)}[field_name]
        decoder = field.metadata["dataclasses_json"]["decoder"]
        for member in enum_class:
            assert decoder(member.name) is member, f"{config_class.__name__}.{field_name}"
        assert decoder("AUTO") is AUTO
        checked += 1
    assert checked >= 1, "the scan found no enum-typed sizable field at all"


@pytest.mark.base
def test_auto_in_a_config_block_reopens_a_field_the_preset_had_pinned() -> None:
    """Catches ``AUTO`` being written onto a configuration as a string or ignored outright.

    Re-opening a pinned field is how an author says "size this after all" without abandoning
    the preset, so the value has to come back as a number computed from the building rather
    than as the preset's fixed one.
    """
    pinned = Systems.configure(
        Systems.building("building")
        + """  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas_12kw
"""
    ).config_of("boiler")
    reopened = Systems.configure(
        Systems.building("building")
        + """  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas_12kw
    config:
      maximal_thermal_power_in_watt: AUTO
"""
    ).config_of("boiler")

    assert pinned.maximal_thermal_power_in_watt == 12_000.0
    assert reopened.maximal_thermal_power_in_watt != pinned.maximal_thermal_power_in_watt
    assert isinstance(reopened.maximal_thermal_power_in_watt, float)


@pytest.mark.base
def test_a_value_that_does_not_fit_its_field_is_refused_naming_the_entry() -> None:
    """Catches a wrong-typed override reaching a component as a plausible-looking value.

    A string where a number belongs produces a run that either crashes far from the file or,
    worse, produces numbers nobody questions; the rejection has to happen while the file is
    still the obvious place to look.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        Systems.configure(
            Systems.building("building")
            + """  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    config:
      eff_th_max: very high
"""
        )

    message = str(raised.value)
    assert raised.value.error_id is EnergySystemErrorId.UNDECODABLE_VALUE
    assert "boiler" in message and "eff_th_max" in message


@pytest.mark.base
def test_an_unknown_enum_member_is_refused_and_the_members_are_listed() -> None:
    """Catches a misspelled enum member reported without the members that exist.

    The set of members is closed and short, so printing it turns the rejection into the fix;
    without it an author has to find the enum in the source of a component module.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        Systems.configure(
            """  hds:
    class: hisim.components.heat_distribution_system.HeatDistribution
    preset: standard
    config:
      heating_system: FLOOR_HEATING
      water_mass_flow_rate_in_kg_per_second: 0.5
      absolute_conditioned_floor_area_in_m2: 100.0
"""
        )

    message = str(raised.value)
    assert raised.value.error_id is EnergySystemErrorId.UNDECODABLE_VALUE
    assert "FLOORHEATING" in message and "RADIATOR" in message


@pytest.mark.base
def test_a_fact_nobody_reads_produces_a_warning_and_not_a_refusal() -> None:
    """Catches an unread provider being treated as an error, or being passed over in silence.

    A component whose facts nobody consumes is legal — a provider added before its consumer, a
    group switched off — but it is also what a misspelled source line looks like from outside,
    so the run says it and continues.
    """
    system = Systems.configure(Systems.building("building") + Systems.BOILER)

    assert any("conditioned_floor_area_in_m2" in line for line in system.warnings)
    assert all("building" in line or "boiler" in line for line in system.warnings)
    assert system.config_of("boiler").maximal_thermal_power_in_watt > 0.0


@pytest.mark.base
def test_the_kernels_null_provider_message_is_recognized() -> None:
    """Catches the null-provider condition degrading to the catch-all identifier.

    A provider whose feature is switched off contributes ``None``, and sizing from it would
    invent a number; the condition has its own identifier so that a caller can tell it apart
    from a fact nobody provides at all.
    """
    message = kernel_message(
        [
            _NullProviderConfig(component_id=ComponentID(name="provider")),
            _PowerReaderConfig(component_id=ComponentID(name="consumer")),
        ]
    )

    assert KernelFailure.classify(message) is EnergySystemErrorId.SIZING_NULL_VALUE


@pytest.mark.base
def test_the_kernels_sibling_cycle_message_is_recognized() -> None:
    """Catches a cycle between two sizable fields degrading to the catch-all identifier.

    A field reading a sibling that reads it back can never be resolved, and the condition is
    about one configuration class rather than about the file's wiring, which is why it carries
    an identifier of its own.
    """
    message = kernel_message([_CyclicConfig(component_id=ComponentID(name="cyclic"))])

    assert KernelFailure.classify(message) is EnergySystemErrorId.SIZING_FIELD_CYCLE


@pytest.mark.base
def test_the_kernels_duplicate_name_message_is_recognized() -> None:
    """Catches two configurations sharing an instance name degrading to the catch-all.

    Sizing addresses every provider and consumer by its instance name, so a duplicate makes
    every reference in the file ambiguous; in this format the names are entry keys and cannot
    collide, which is exactly why the wrapper has to keep recognizing the condition.
    """
    message = kernel_message(
        [
            _PowerReaderConfig(component_id=ComponentID(name="twin")),
            _PowerReaderConfig(component_id=ComponentID(name="twin")),
        ]
    )

    assert KernelFailure.classify(message) is EnergySystemErrorId.SIZING_DUPLICATE_NAME


@pytest.mark.base
def test_the_kernels_ambiguity_and_unprovided_messages_stay_distinguishable() -> None:
    """Catches the two provider-count failures collapsing into one identifier.

    Both messages talk about who provides a fact, and both contain the phrase "provided by";
    only the order in which the wrapper tries them keeps "provided by nobody" from being
    reported as an ambiguity between zero candidates.
    """
    unprovided = kernel_message([_PowerReaderConfig(component_id=ComponentID(name="lonely"))])
    ambiguous = kernel_message(
        [
            _NullProviderConfig(component_id=ComponentID(name="left")),
            _NullProviderConfig(component_id=ComponentID(name="right")),
            _PowerReaderConfig(component_id=ComponentID(name="consumer")),
        ]
    )

    assert KernelFailure.classify(unprovided) is EnergySystemErrorId.SIZING_UNPROVIDED
    assert KernelFailure.classify(ambiguous) is EnergySystemErrorId.SIZING_AMBIGUOUS


@pytest.mark.base
def test_a_many_cardinality_read_is_reported_as_the_unimplemented_condition() -> None:
    """Catches a many-reader failing as an unclassified error instead of the known gap.

    Aggregating over several providers is declared in the format and not implemented in the
    kernel, so a file using it has to be told precisely that rather than being handed a raw
    ``NotImplementedError`` from inside a law.
    """
    from hisim.energy_system.sizing_bridge import resolve_sizing

    with pytest.raises(EnergySystemSizingError) as raised:
        resolve_sizing(
            [
                _PowerProviderConfig(component_id=ComponentID(name="provider")),
                _ManyReaderConfig(component_id=ComponentID(name="aggregator")),
            ],
            {},
            ["provider", "aggregator"],
        )

    assert raised.value.error_id is EnergySystemErrorId.SIZING_MANY_UNSUPPORTED
