"""Tests for the class-bound half of validation: a file checked against the classes it names.

Structural validation says a file is well formed; this stage says the system it describes can
exist. Every rule here needs a component class in memory — the dotted path has to import and
name a component, the preset or the named constructor has to be declared on the configuration
class, the arguments have to match the constructor's parameters, a ``config`` key has to be a
field and a ``sizing_sources`` key has to be a fact the class actually reads.

There is one test per rejection, each built from the smallest file that can trigger it and
each asserting the identifier, the offending entry's name in the message and — wherever the
set of valid values is closed — that the set is listed. Alongside them sits the pinned set of
entries the three normative mockups currently fail on: those files are written against classes
that have not been converted to presets and laws yet, so the honest statement of where the
work stands is a list that shrinks with every conversion, checked by a test so it cannot
silently rot.

Each test states the failure mode it catches.
"""

# clean

from typing import ClassVar, Dict, Mapping

import pytest

from hisim.energy_system import (
    EnergySystemBindingError,
    EnergySystemErrorId,
    EnergySystemFile,
    expand_groups,
    parse_energy_system,
)
from hisim.energy_system.bindings import facts_read_by
from hisim.energy_system.classes import collect_class_failures, validate_classes
from hisim.energy_system.document import RawDocument
from hisim.energy_system.loader import EnergySystemReader
from tests.test_energy_system_loader import Mockups


class Systems:
    """Renders the small files the class-bound rule tests break.

    Every test needs a document that is structurally valid and wrong in exactly one
    class-bound way, so the helpers here render one entry at a time around a fixed, converted
    skeleton. Only classes that already carry presets, constructors and laws appear, because a
    fixture built on an unconverted class would fail for the wrong reason.
    """

    #: A building configured by its preset: the one entry every other fixture can lean on,
    #: since it provides the facts a boiler reads.
    BUILDING: ClassVar[str] = """  building:
    class: hisim.components.building.Building
    preset: standard
"""

    @classmethod
    def of(cls, entries: str) -> EnergySystemFile:
        """Parses one inline document made of the given component entries.

        Args:
            entries: The body of the ``components`` block, indented by two spaces.

        Returns:
            The parsed, unexpanded file.
        """
        text = f"schema_version: 3\nname: class rule under test\ncomponents:\n{entries}"
        return EnergySystemReader.build(RawDocument.parse_text(text, "inline"), "inline")


class ExpectedFailures:
    """The entries of each normative mockup that class-bound validation rejects today.

    The mockups are written against the format, not against the current state of HiSim's
    component classes, so most of their entries name presets and constructors that do not
    exist yet. Pinning the exact set — entry name to catalogue identifier — turns that gap
    from an unwritten assumption into a checked fact: a conversion that lands removes lines
    here in the same change, and a conversion that regresses adds one.

    ``EF-13`` means the configuration class declares no such preset (usually none at all),
    ``EF-15`` means it declares no such named constructor, ``EF-10`` means the module the
    entry names does not exist in this repository at all, and ``EF-43`` means the class reads
    no such sizing fact.
    """

    MINIMAL: ClassVar[Mapping[str, str]] = {
        "weather": "EF-13",
        "occupancy": "EF-13",
        "hds_controller": "EF-13",
        "boiler_controller": "EF-13",
        "meter": "EF-13",
    }

    HEAT_PUMP: ClassVar[Mapping[str, str]] = {
        "weather": "EF-15",
        "occupancy": "EF-15",
        "hds_controller": "EF-13",
        "heat_pump_controller_sh": "EF-13",
        "heat_pump_controller_dhw": "EF-13",
        "heat_pump": "EF-13",
        "buffer_storage": "EF-13",
        "dhw_storage": "EF-13",
        "meter": "EF-13",
        "pv_south": "EF-13",
        "pv_east": "EF-13",
        "battery": "EF-13",
        "ems": "EF-43",
        "heating_rod": "EF-13",
        "heating_rod_controller": "EF-13",
    }

    MULTI_FAMILY: ClassVar[Mapping[str, str]] = {
        "weather": "EF-13",
        "meter": "EF-13",
        "heat_pump": "EF-13",
        "heat_pump_controller_sh": "EF-13",
        "buffer_storage": "EF-13",
        "pv_central": "EF-13",
        "battery": "EF-13",
        "ems": "EF-43",
        **{
            f"apt{index}_{suffix}": error_id
            for index in (1, 2, 3)
            for suffix, error_id in (
                ("occupancy", "EF-15"),
                ("hds_controller", "EF-13"),
                ("hds", "EF-13"),
                ("dhw_storage", "EF-13"),
                ("dhw_heater", "EF-13"),
                ("dhw_controller", "EF-13"),
                ("car", "EF-13"),
                ("charger", "EF-13"),
            )
        },
        "apt1_pv": "EF-13",
        "apt2_pv": "EF-13",
    }

    BY_MOCKUP: ClassVar[Mapping[str, Mapping[str, str]]] = {
        "energy_system_mockup_minimal.yaml": MINIMAL,
        "energy_system_mockup.yaml": HEAT_PUMP,
        "energy_system_mockup_mfh.yaml": MULTI_FAMILY,
    }


@pytest.mark.base
@pytest.mark.parametrize("name", Mockups.NAMES)
def test_each_mockup_fails_on_exactly_the_pinned_entries(name: str) -> None:
    """Catches a conversion landing or regressing without the pin being updated with it.

    The set is expected to shrink to nothing as the component classes gain their presets,
    constructors and laws; until then it is the honest statement of how far a normative mockup
    is from running, and a difference in either direction is a change somebody has to see.
    """
    expanded, _ = expand_groups(parse_energy_system(Mockups.path(name)))
    actual: Dict[str, str] = {
        failure.name: failure.error_id.value for failure in collect_class_failures(expanded)
    }

    assert actual == dict(ExpectedFailures.BY_MOCKUP[name])


@pytest.mark.base
def test_a_file_of_converted_classes_binds_without_complaint() -> None:
    """Catches a class-bound rule that rejects a file the converted classes fully support.

    Every rejection below is only meaningful if the passing case actually passes: a validator
    that refuses everything would satisfy each error test on its own.
    """
    bindings = validate_classes(
        Systems.of(
            Systems.BUILDING
            + """  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
  hds:
    class: hisim.components.heat_distribution_system.HeatDistribution
    preset: standard
  ems:
    class: hisim.components.controller_l2_energy_management_system.L2GenericEnergyManagementSystem
    preset: optimize_own_consumption
"""
        )
    )

    assert bindings.names() == ("building", "boiler", "hds", "ems")
    assert bindings["boiler"].config_class.__name__ == "GenericBoilerConfig"
    assert bindings["boiler"].preset is not None and bindings["boiler"].preset.name == "condensing_gas"
    assert bindings["building"].component_class.__name__ == "Building"


@pytest.mark.base
def test_a_class_path_whose_module_does_not_exist_is_refused() -> None:
    """Catches a missing or renamed component module being reported as something else.

    A dotted path is data in a file, so a wrong one has to be a format rejection naming the
    entry rather than a bare ``ModuleNotFoundError`` from somewhere inside an import.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        validate_classes(
            Systems.of(
                """  ghost:
    class: hisim.components.no_such_module.Ghost
    preset: standard
"""
            )
        )

    assert raised.value.error_id is EnergySystemErrorId.CLASS_NOT_IMPORTABLE
    assert "ghost" in str(raised.value) and "hisim.components.no_such_module" in str(raised.value)


@pytest.mark.base
def test_a_class_path_naming_a_configuration_class_is_refused_with_the_components() -> None:
    """Catches an entry naming the config class instead of the component it configures.

    The two live in the same module and their names differ by one suffix, so the mistake is
    easy to make and worth a message that lists the component classes of that very module.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        validate_classes(
            Systems.of(
                """  boiler:
    class: hisim.components.generic_boiler.GenericBoilerConfig
    preset: condensing_gas
"""
            )
        )

    assert raised.value.error_id is EnergySystemErrorId.CLASS_NOT_IMPORTABLE
    assert "GenericBoiler" in str(raised.value)


@pytest.mark.base
def test_an_unknown_preset_is_refused_and_the_known_presets_are_listed() -> None:
    """Catches a misspelled preset reported without the names that would have worked.

    Preset names are wire format and closed per class, so the rejection can and must print
    the whole set; without it an author has to read the class's source to find the typo.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        validate_classes(
            Systems.of(
                """  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gaz
"""
            )
        )

    message = str(raised.value)
    assert raised.value.error_id is EnergySystemErrorId.UNKNOWN_PRESET
    assert "boiler" in message and "condensing_gaz" in message
    assert "condensing_gas" in message and "wood_chips" in message


@pytest.mark.base
def test_an_unknown_constructor_is_refused_and_the_known_constructors_are_listed() -> None:
    """Catches a wrong constructor name reported without the class's actual constructors.

    A constructor is chosen from a small declared set exactly like a preset, so the same rule
    applies: name the entry, name what was written, list what may be written instead.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        validate_classes(
            Systems.of(
                """  building:
    class: hisim.components.building.Building
    constructor:
      for_tabula:
        building_code: DE.N.SFH.05.Gen.ReEx.001.002
"""
            )
        )

    message = str(raised.value)
    assert raised.value.error_id is EnergySystemErrorId.UNKNOWN_CONSTRUCTOR
    assert "building" in message and "for_tabula" in message and "for_tabula_code" in message


@pytest.mark.base
def test_an_unknown_constructor_argument_is_refused_with_the_parameter_list() -> None:
    """Catches a mistyped argument name reported without the parameters it could have been.

    The parameters carry their types and defaults in the message, because an author who got
    the name wrong usually also needs to know what the right one expects.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        validate_classes(
            Systems.of(
                """  building:
    class: hisim.components.building.Building
    constructor:
      for_tabula_code:
        tabula_code: DE.N.SFH.05.Gen.ReEx.001.002
"""
            )
        )

    message = str(raised.value)
    assert raised.value.error_id is EnergySystemErrorId.CONSTRUCTOR_ARGUMENT
    assert "building" in message and "tabula_code" in message
    assert "building_code: str" in message


@pytest.mark.base
def test_a_missing_mandatory_constructor_argument_is_refused() -> None:
    """Catches a constructor called without the one argument it cannot default.

    Omitting a mandatory argument would otherwise surface as a ``TypeError`` from deep inside
    the builder, naming the Python method rather than the entry that called it.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        validate_classes(
            Systems.of(
                """  building:
    class: hisim.components.building.Building
    constructor:
      for_tabula_code:
        number_of_apartments: 2
"""
            )
        )

    message = str(raised.value)
    assert raised.value.error_id is EnergySystemErrorId.CONSTRUCTOR_ARGUMENT
    assert "building_code" in message and "building" in message


@pytest.mark.base
def test_a_config_key_that_is_no_field_is_refused_with_the_field_list() -> None:
    """Catches a misspelled override silently doing nothing at all.

    An override that names no field is the most dangerous kind of typo: the run succeeds, the
    value is ignored, and the results are the defaults the author thought they had changed.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        validate_classes(
            Systems.of(
                """  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    config:
      eff_th_maximum: 0.95
"""
            )
        )

    message = str(raised.value)
    assert raised.value.error_id is EnergySystemErrorId.UNKNOWN_CONFIG_FIELD
    assert "boiler" in message and "eff_th_maximum" in message and "eff_th_max" in message


@pytest.mark.base
def test_writing_the_component_identity_into_a_config_block_is_refused() -> None:
    """Catches a file carrying a second, contradictable spelling of a component's identity.

    The entry's key is the component's whole identity in this format; an identity in the block
    as well could disagree with it, and then two lines of the same entry would name different
    components.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        validate_classes(
            Systems.of(
                """  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    config:
      component_id:
        name: something_else
"""
            )
        )

    assert raised.value.error_id is EnergySystemErrorId.UNKNOWN_CONFIG_FIELD
    assert "component_id" in str(raised.value)


@pytest.mark.base
def test_auto_on_a_field_without_a_law_is_refused_with_the_sizable_fields() -> None:
    """Catches an ``AUTO`` that nothing would ever fill in.

    ``AUTO`` re-opens a field for resolution, which only means something where a law can close
    it again; on any other field it would leave a sentinel object in a configuration and fail
    much later inside the component that reads it.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        validate_classes(
            Systems.of(
                """  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    config:
      eff_th_max: AUTO
"""
            )
        )

    message = str(raised.value)
    assert raised.value.error_id is EnergySystemErrorId.AUTO_ON_CONCRETE_FIELD
    assert "boiler" in message and "eff_th_max" in message
    assert "maximal_thermal_power_in_watt" in message


@pytest.mark.base
def test_a_sizing_source_for_a_fact_the_class_never_reads_is_refused() -> None:
    """Catches a source line that would be accepted and then ignored.

    A ``sizing_sources`` entry answers "where does this component take that fact from", so a
    fact the class never reads makes the line unanswerable — and silently ignoring it would
    leave the author believing a number came from where the line says it did.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        validate_classes(
            Systems.of(
                Systems.BUILDING
                + """  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    sizing_sources:
      conditioned_floor_area_in_m2: building.conditioned_floor_area_in_m2
"""
            )
        )

    message = str(raised.value)
    assert raised.value.error_id is EnergySystemErrorId.FACT_NOT_READ
    assert "boiler" in message and "conditioned_floor_area_in_m2" in message
    assert "heating_load_in_watt" in message


@pytest.mark.base
def test_the_facts_a_class_reads_cover_its_laws_and_its_contributions() -> None:
    """Catches a facts-read list that misses one of the two ways a class can read a fact.

    A class reads facts through its sizing laws and, separately, through the inputs its own
    fact contributions need; a source line may legitimately target either, so a list built
    from only one of them would reject a correct file.
    """
    from hisim.components.generic_boiler import GenericBoilerConfig
    from hisim.components.heat_distribution_system import HeatDistributionConfig

    assert facts_read_by(GenericBoilerConfig) == ("heating_load_in_watt", "number_of_apartments")
    assert "water_mass_flow_rate_in_kg_per_second" in facts_read_by(HeatDistributionConfig)
