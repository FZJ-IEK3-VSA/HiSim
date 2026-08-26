"""Tests for the class-independent rules an energy-system file must obey.

Structural validation is what stands between a syntactically valid YAML document and a
file that describes a system at all: unique names, legal groups, references that resolve,
an entry that says how it is configured, input lists that do not contradict themselves and
paths that work on more than one machine. This module has one test per rule, each built
from the smallest file that can break it, plus the assertion that the three normative
mockups pass every rule as written.

Each test states the failure mode it catches, and each asserts more than "something was
raised": the error identifier, the offending element's name in the message and — wherever a
closed set of valid values exists — that the set is listed, because a rejection that does
not say what to write instead only moves the guessing to the author.
"""

# clean

from typing import ClassVar

import pytest

from hisim.energy_system import (
    EnergySystemErrorId,
    EnergySystemFormatError,
    load_energy_system,
    parse_energy_system,
    validate_structure,
)
from tests.test_energy_system_loader import Mockups


class Systems:
    """Builds the small energy-system documents the individual rule tests break.

    Every test needs a file that is valid except for the one rule under test, and writing
    those out in full would bury the interesting line. The helpers here render a valid
    two-component skeleton and let a test replace or extend one part of it.
    """

    #: A minimal, valid pair of components: a source with nothing to say and a consumer
    #: that takes its defaults. Every rule test starts from this and breaks one thing.
    SKELETON: ClassVar[str] = """
schema_version: 3
name: rule under test
components:
  weather:
    class: hisim.components.weather.Weather
    preset: standard
  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    inputs:
      - weather
"""

    @classmethod
    def with_extra(cls, extra: str) -> str:
        """Returns the skeleton with more text appended at the top level.

        Args:
            extra: Additional YAML, already indented for the top level.

        Returns:
            The complete document.
        """
        return cls.SKELETON + extra


@pytest.mark.base
@pytest.mark.parametrize("name", Mockups.NAMES)
def test_the_mockups_satisfy_every_structural_rule(name: str) -> None:
    """Catches a structural rule that is stricter than the format the mockups describe.

    The mockups are normative, so a rule they fail is a bug in the rule and not in them;
    running the validator separately from the loader keeps that distinction visible.
    """
    validate_structure(parse_energy_system(Mockups.path(name)))


@pytest.mark.base
def test_an_input_from_an_undeclared_component_is_refused_with_candidates() -> None:
    """Catches a misspelled source name being wired to nothing at all.

    Consumer-side wiring means a wrong source produces no error anywhere downstream, so the
    reference graph has to be closed here, and the message has to list the real names.
    """
    text = Systems.SKELETON.replace("      - weather\n", "      - weathr\n")
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.UNKNOWN_SOURCE
    message = str(caught.value)
    assert "boiler" in message and "weathr" in message
    assert "Did you mean: weather?" in message


@pytest.mark.base
def test_a_sizing_source_naming_an_undeclared_component_is_refused() -> None:
    """Catches a sizing source pointing at a component that the file does not have.

    A dangling provider would leave the consuming field unsized with no explanation, so the
    message names both the consumer and the reference and lists the declared components.
    """
    text = Systems.SKELETON + """    sizing_sources:
      heating_load_in_watt: bulding.heating_load_in_watt
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.UNKNOWN_SIZING_SOURCE
    message = str(caught.value)
    assert "boiler" in message and "bulding" in message and "weather" in message


@pytest.mark.base
def test_a_sizing_source_may_not_rename_the_fact_it_provides() -> None:
    """Catches a source line whose dotted half names a different fact than its key.

    Such a line reads as if it renamed a fact on the way, which the format does not do; the
    message shows the reference that was meant.
    """
    text = Systems.SKELETON + """    sizing_sources:
      heating_load_in_watt: weather.number_of_apartments
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.SIZING_FACT_MISMATCH
    assert "weather.heating_load_in_watt" in str(caught.value)


@pytest.mark.base
@pytest.mark.parametrize("reference", ["pv_[*].pv_peak_power_in_watt", "../pv/pv_south.pv_peak_power_in_watt"])
def test_a_wildcard_or_relative_sizing_reference_is_refused(reference: str) -> None:
    """Catches glob and path syntax being accepted where only a plain reference belongs.

    Both spellings belong to a preprocessor this format version does not have; accepting
    them quietly would make files written for that preprocessor resolve to something else.
    """
    text = Systems.SKELETON + f"""    sizing_sources:
      pv_peak_power_in_watt: {reference}
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.WILDCARD_OR_RELATIVE_REFERENCE


@pytest.mark.base
def test_a_wildcard_in_an_input_item_is_refused() -> None:
    """Catches glob syntax reaching the wiring stage through the ``inputs`` list.

    The same reference grammar has to hold on both sides of the format, or one of them
    becomes the loophole through which patterns arrive.
    """
    text = Systems.SKELETON.replace("      - weather\n", "      - pv_[*]\n")
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.WILDCARD_OR_RELATIVE_REFERENCE


@pytest.mark.base
def test_an_absolute_path_in_a_config_value_is_refused() -> None:
    """Catches a file that only works on the machine it was written on.

    Paths are symbolic so that a colleague or a container resolves them locally; an
    absolute one is cheap to catch here and expensive to diagnose from a failing run.
    """
    text = Systems.SKELETON + """    config:
      source_path: /home/someone/hisim/inputs/weather
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.ABSOLUTE_PATH
    assert "${inputs}" in str(caught.value)


@pytest.mark.base
def test_a_symbolic_path_in_a_config_value_is_accepted() -> None:
    """Catches the absolute-path rule over-reaching onto the spelling it is meant to force.

    Rejecting ``${inputs}/...`` alongside ``/home/...`` would leave no way to write a path
    at all, so the accepted form is pinned next to the rejected one.
    """
    text = Systems.SKELETON + """    config:
      source_path: ${inputs}/weather/berlin
"""
    assert load_energy_system(text).all_components()["boiler"].config["source_path"] == "${inputs}/weather/berlin"


@pytest.mark.base
def test_an_entry_with_both_a_preset_and_a_constructor_is_refused() -> None:
    """Catches an entry that leaves it open which of the two produces its configuration.

    A preset and a constructor both build the configuration before overrides, so naming
    both would make the executor choose on the author's behalf.
    """
    text = Systems.SKELETON + """    constructor:
      for_tabula_code:
        building_code: DE.N.SFH.05.Gen.ReEx.001.002
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.PRESET_AND_CONSTRUCTOR
    assert "condensing_gas" in str(caught.value) and "for_tabula_code" in str(caught.value)


@pytest.mark.base
def test_an_entry_with_no_preset_no_constructor_and_no_config_is_refused() -> None:
    """Catches an entry that says nothing at all about how its component is configured.

    Falling back to the class's own defaults would be an inference the format does not
    make; the author has to name a preset, a constructor or a complete config block.
    """
    text = """
schema_version: 3
name: x
components:
  weather:
    class: hisim.components.weather.Weather
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.NO_CONFIGURATION_SOURCE
    assert "weather" in str(caught.value)


@pytest.mark.base
def test_a_nested_group_is_refused() -> None:
    """Catches a group written inside a group, which the flat group model does not have.

    Nesting would make the off rule recursive and give component names a scope, both of
    which the format deliberately avoids.
    """
    text = """
schema_version: 3
name: x
components: {}
groups:
  outer:
    enabled: true
    components:
      inner:
        enabled: true
        components:
          pv_south:
            class: hisim.components.generic_pv_system.PVSystem
            preset: standard
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.NESTED_GROUP
    assert "inner" in str(caught.value)


@pytest.mark.base
def test_a_component_listed_in_two_groups_is_refused() -> None:
    """Catches a component that two switches would each claim to control.

    With the component in two groups it is unclear whether disabling one removes it, so the
    message names both groups.
    """
    text = """
schema_version: 3
name: x
components: {}
groups:
  pv:
    enabled: true
    components:
      pv_south:
        class: hisim.components.generic_pv_system.PVSystem
        preset: standard
  more_pv:
    enabled: false
    components:
      pv_south:
        class: hisim.components.generic_pv_system.PVSystem
        preset: standard
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.COMPONENT_IN_TWO_GROUPS
    assert "pv_south" in str(caught.value) and "pv" in str(caught.value)


@pytest.mark.base
def test_a_grouped_component_may_not_reuse_an_ungrouped_name() -> None:
    """Catches two components sharing one name, which would make every reference ambiguous.

    Names are global whether or not an entry sits in a group, so the collision has to be
    reported instead of one entry quietly shadowing the other.
    """
    text = Systems.with_extra("""groups:
  pv:
    enabled: true
    components:
      weather:
        class: hisim.components.generic_pv_system.PVSystem
        preset: standard
""")
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.DUPLICATE_NAME
    assert "weather" in str(caught.value)


@pytest.mark.base
def test_a_group_may_not_share_a_name_with_a_component() -> None:
    """Catches a bare reference that could mean either a group or a component.

    Groups and components live in one namespace precisely because a reference is a bare
    name and the reader must not have to guess which of the two it addresses.
    """
    text = Systems.with_extra("""groups:
  boiler:
    enabled: true
    components:
      pv_south:
        class: hisim.components.generic_pv_system.PVSystem
        preset: standard
""")
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.DUPLICATE_NAME
    assert "boiler" in str(caught.value)


@pytest.mark.base
def test_mixing_an_explicit_wire_and_a_feed_from_one_source_is_refused() -> None:
    """Catches one relationship being described twice in two different spellings.

    A wire and a feed from the same source into the same consumer would wire the flow once
    directly and once through the aggregator, silently double-counting it.
    """
    text = """
schema_version: 3
name: x
components:
  occupancy:
    class: hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector
    preset: standard
  meter:
    class: hisim.components.electricity_meter.ElectricityMeter
    preset: standard
    inputs:
      - input: ElectricityInput
        from: occupancy.ElectricityOutput
      - from: occupancy.ElectricityOutput
        tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED]
        weight: 999
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.MIXED_INPUT_SPELLING
    assert "meter" in str(caught.value) and "occupancy" in str(caught.value)


@pytest.mark.base
def test_two_wires_into_one_input_are_refused() -> None:
    """Catches an input whose value would depend on the order of the input list.

    Two sources on one input cannot both be applied, and picking the last one would make
    the meaning of the file depend on where a line was inserted.
    """
    text = """
schema_version: 3
name: x
components:
  weather:
    class: hisim.components.weather.Weather
    preset: standard
  other_weather:
    class: hisim.components.weather.Weather
    preset: standard
  heat_pump:
    class: hisim.components.more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib
    preset: air_water
    inputs:
      - input: TemperatureInputPrimary
        from: weather.DailyAverageOutsideTemperatures
      - input: TemperatureInputPrimary
        from: other_weather.DailyAverageOutsideTemperatures
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.DUPLICATE_WIRE
    assert "TemperatureInputPrimary" in str(caught.value)


@pytest.mark.base
def test_the_same_feed_written_twice_is_refused() -> None:
    """Catches an aggregator counting one flow twice.

    A repeated ``(source, output)`` pair adds the same power to the balance a second time,
    which produces a plausible-looking but wrong result rather than a failure.
    """
    text = """
schema_version: 3
name: x
components:
  occupancy:
    class: hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector
    preset: standard
  meter:
    class: hisim.components.electricity_meter.ElectricityMeter
    preset: standard
    inputs:
      - from: occupancy.ElectricityOutput
        tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED]
        weight: 999
      - from: occupancy.ElectricityOutput
        tags: [ELECTRICITY_PRODUCTION]
        weight: 1
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.DUPLICATE_FEED
    assert "occupancy.ElectricityOutput" in str(caught.value)


@pytest.mark.base
def test_one_source_named_twice_as_a_defaults_item_is_refused() -> None:
    """Catches the same default connections being applied twice between one pair.

    The second item adds nothing and would either duplicate every wire or be silently
    ignored, so it is reported as the contradiction it is.
    """
    text = Systems.SKELETON.replace("      - weather\n", "      - weather\n      - weather\n")
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.MIXED_INPUT_SPELLING


@pytest.mark.base
def test_a_bare_item_next_to_an_explicit_wire_from_the_same_source_is_accepted() -> None:
    """Catches the don't-mix rule over-reaching onto a combination the mockups rely on.

    Taking a source's declared defaults and adding one further wire from the same source is
    the normal way to connect an extra port, and the heat pump of the second mockup does it.
    """
    heat_pump = load_energy_system(Mockups.path("energy_system_mockup.yaml")).all_components()["heat_pump"]
    sources = [item.source for item in heat_pump.inputs]
    assert sources.count("weather") == 2
