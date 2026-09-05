"""Tests for the structured component identity :py:class:`hisim.config.ComponentID`.

These tests pin down the behaviour that the whole identity sweep rests on: how a runtime
component name is derived from the structured fields, that the identity is an immutable and
hashable value object, that it survives a JSON round trip as a nested object inside a
configuration, and that the derived name no longer depends on the ``multiple_buildings``
simulation parameter. They also cover the postprocessing grouping helpers that replaced the
former name-substring matching, so that a district-style setup can be verified without
running a full district simulation. A final section covers the identifier rule the runtime
name has to satisfy, and that the declarative file format enforces the very same rule from
the very same definition.
"""

# clean

import dataclasses
from dataclasses import dataclass
from typing import Optional

import pytest
from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.config import ConfigBase, ComponentID, DisplayConfig, NameSyntax
from hisim.components import example_component
from hisim.energy_system.model import NameRules
from hisim.simulationparameters import SimulationParameters


@dataclass_json
@dataclass
class _IdentityOnlyConfig(ConfigBase):
    """Minimal configuration used to exercise the JSON round trip of a nested identity.

    The sweep moved the component identity from two loose strings into one nested object, so a
    configuration that carries nothing but that identity plus a trivial payload field is enough
    to prove the serialization contract. Keeping the class local to this test module avoids
    coupling the contract test to any particular real component.
    """

    component_id: ComponentID
    payload: int

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the class name of the component this configuration would configure.

        ``ConfigBase`` requires every concrete configuration to name its component class so the
        JSON machinery can round-trip it. This test double has no real component, so it simply
        names itself.
        """
        return cls.__module__ + "._IdentityOnlyConfig"


class _NamedComponent(cp.Component):
    """Component stub that exists only so a name can be derived from a configuration.

    The lifecycle methods are no-ops because no test in this module ever advances a time step;
    all that is exercised is the naming path in :py:meth:`hisim.component.Component.get_component_name`
    and the identity shortcut on the component.
    """

    def __init__(self, config: ConfigBase, my_simulation_parameters: SimulationParameters) -> None:
        """Builds the stub component from a configuration and simulation parameters."""
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )

    def i_prepare_simulation(self) -> None:
        """Does nothing; no preparation is needed for a naming-only stub."""

    def i_save_state(self) -> None:
        """Does nothing; the stub holds no state."""

    def i_restore_state(self) -> None:
        """Does nothing; the stub holds no state."""

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Does nothing; the stub never produces values."""


# --------------------------------------------------------------------------- #
# Key derivation
# --------------------------------------------------------------------------- #


@pytest.mark.base
@pytest.mark.parametrize(
    "building, unit, expected_key",
    [
        (None, None, "Weather"),
        ("BUI1", None, "BUI1_Weather"),
        (None, "APT2", "APT2_Weather"),
        ("BUI1", "APT2", "BUI1_APT2_Weather"),
    ],
)
def test_key_joins_only_the_present_fields(building: Optional[str], unit: Optional[str], expected_key: str) -> None:
    """The key concatenates building, unit and name, skipping the fields that are absent."""
    assert ComponentID(name="Weather", building=building, unit=unit).key == expected_key


@pytest.mark.base
def test_key_examples_from_the_design() -> None:
    """The three documented example identities derive exactly the documented keys."""
    assert ComponentID("Weather").key == "Weather"
    assert ComponentID("Weather", building="BUI1").key == "BUI1_Weather"
    assert ComponentID("HeatPump", building="BUI1", unit="APT2").key == "BUI1_APT2_HeatPump"


@pytest.mark.base
def test_name_is_mandatory_and_must_not_be_blank() -> None:
    """An empty or whitespace-only name is rejected instead of producing a malformed key."""
    with pytest.raises(ValueError):
        ComponentID(name="")
    with pytest.raises(ValueError):
        ComponentID(name="   ")


@pytest.mark.base
def test_building_label_falls_back_to_the_default_label() -> None:
    """A component without a building is grouped under the historical default building name."""
    assert ComponentID(name="Weather").building_label == ComponentID.DEFAULT_BUILDING_LABEL
    assert ComponentID(name="Weather").building_label == "BUI1"
    assert ComponentID(name="Weather", building="BUI2").building_label == "BUI2"


# --------------------------------------------------------------------------- #
# Value-object semantics
# --------------------------------------------------------------------------- #


@pytest.mark.base
def test_identity_is_frozen_and_hashable() -> None:
    """The identity cannot be mutated in place and can be used as a dict key or set member."""
    identity = ComponentID(name="HeatPump", building="BUI1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        identity.name = "Other"  # type: ignore[misc]
    assert {identity: 1}[ComponentID(name="HeatPump", building="BUI1")] == 1
    assert len({identity, ComponentID(name="HeatPump", building="BUI1")}) == 1
    assert dataclasses.replace(identity, building=None).key == "HeatPump"


@pytest.mark.base
def test_identities_compare_by_value() -> None:
    """Two identities are equal exactly when all three of their fields agree."""
    assert ComponentID("Pump", building="BUI1") == ComponentID("Pump", building="BUI1")
    assert ComponentID("Pump", building="BUI1") != ComponentID("Pump", building="BUI2")
    assert ComponentID("Pump") != ComponentID("Pump", unit="APT1")


# --------------------------------------------------------------------------- #
# JSON round trip
# --------------------------------------------------------------------------- #


@pytest.mark.base
def test_config_json_round_trip_keeps_building_and_unit() -> None:
    """A configuration serializes its identity as a nested object and reads it back unchanged."""
    config = _IdentityOnlyConfig(
        component_id=ComponentID(name="HeatPump", building="BUI1", unit="APT2"), payload=7
    )
    as_dict = config.to_dict()
    assert as_dict["component_id"] == {"name": "HeatPump", "building": "BUI1", "unit": "APT2"}

    restored = _IdentityOnlyConfig.from_dict(as_dict)
    assert restored.component_id == config.component_id
    assert restored.component_id.key == "BUI1_APT2_HeatPump"
    assert restored.payload == 7


@pytest.mark.base
def test_config_json_round_trip_of_a_building_less_identity() -> None:
    """Absent building and unit survive the round trip as ``None`` rather than as empty strings."""
    config = _IdentityOnlyConfig(component_id=ComponentID(name="Weather"), payload=1)
    restored = _IdentityOnlyConfig.from_dict(config.to_dict())
    assert restored.component_id.building is None
    assert restored.component_id.unit is None
    assert restored.component_id.key == "Weather"


# --------------------------------------------------------------------------- #
# Result neutrality
# --------------------------------------------------------------------------- #


@pytest.mark.base
def test_get_component_name_ignores_the_multiple_buildings_parameter() -> None:
    """The runtime name depends only on the identity, never on ``multiple_buildings``."""
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = ConfigBase(component_id=ComponentID(name="Pump", building="BUI7"))

    sim_params.multiple_buildings = False
    assert _NamedComponent(config, sim_params).get_component_name() == "BUI7_Pump"
    sim_params.multiple_buildings = True
    assert _NamedComponent(config, sim_params).get_component_name() == "BUI7_Pump"

    building_less = ConfigBase(component_id=ComponentID(name="Pump"))
    sim_params.multiple_buildings = False
    assert _NamedComponent(building_less, sim_params).get_component_name() == "Pump"
    sim_params.multiple_buildings = True
    assert _NamedComponent(building_less, sim_params).get_component_name() == "Pump"


@pytest.mark.base
def test_default_factories_produce_building_less_identities() -> None:
    """Default configurations carry no building, so their runtime names stay unprefixed.

    This is the result-neutrality invariant of the sweep: the decorative ``"BUI1"`` that every
    default configuration used to carry was suppressed by ``multiple_buildings=False`` anyway,
    so dropping it leaves every runtime name and therefore every result column unchanged.
    """
    config = example_component.ExampleComponentConfig.get_default_example_component()
    assert config.component_id.building is None
    assert config.component_id.key == "ExampleComponent"

    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    component = example_component.ExampleComponent(config=config, my_simulation_parameters=sim_params)
    assert component.get_component_name() == "ExampleComponent"
    assert component.component_id is config.component_id


@pytest.mark.base
def test_outputs_carry_the_identity_of_their_component() -> None:
    """Every output records the identity of the component that produced it."""
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = example_component.ExampleComponentConfig.get_default_example_component(
        component_id=ComponentID(name="ExampleComponent", building="BUI2")
    )
    component = example_component.ExampleComponent(config=config, my_simulation_parameters=sim_params)
    assert component.outputs
    for output in component.outputs:
        assert output.component_id == config.component_id
        assert output.building_label == "BUI2"


# --------------------------------------------------------------------------- #
# District grouping (structural replacement of the former substring matching)
# --------------------------------------------------------------------------- #


@pytest.mark.base
@pytest.mark.parametrize(
    "building, expected",
    [
        (None, False),
        ("BUI1", False),
        ("BUI2", False),
        ("District", True),
        ("Quartier", True),
        ("Bezirk", True),
        ("Area", True),
        ("Neighborhood", True),
    ],
)
def test_district_detection_is_a_field_comparison(building: Optional[str], expected: bool) -> None:
    """A building object is a district exactly when it equals one of the DistrictNames members."""
    assert lt.DistrictNames.is_district(building) is expected


@pytest.mark.base
def test_district_style_setup_groups_by_building() -> None:
    """A multi-building setup derives prefixed names and groups results per building object.

    The buildings of a district setup carry the ``DistrictNames`` token values, so the grouping
    that postprocessing used to obtain by matching name substrings is reproduced by comparing
    the structured building field. This exercises the grouping without running an actual
    district simulation, which does not exist among the light test markers.
    """
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    identities = [
        ComponentID(name="ExampleComponent", building="BUI1"),
        ComponentID(name="ExampleComponent", building="BUI2"),
        ComponentID(name="ExampleComponent", building=lt.DistrictNames.DISTRICT.value),
    ]
    components = [
        example_component.ExampleComponent(
            config=example_component.ExampleComponentConfig.get_default_example_component(component_id=identity),
            my_simulation_parameters=sim_params,
        )
        for identity in identities
    ]

    assert [component.get_component_name() for component in components] == [
        "BUI1_ExampleComponent",
        "BUI2_ExampleComponent",
        "District_ExampleComponent",
    ]

    building_objects = {component.component_id.building_label for component in components}
    assert building_objects == {"BUI1", "BUI2", "District"}
    assert {b for b in building_objects if lt.DistrictNames.is_district(b)} == {"District"}

    # Every output can be attributed to exactly one building object, which is what the KPI and
    # cost aggregation loops rely on.
    for component in components:
        for output in component.outputs:
            assert output.building_label == component.component_id.building


# --------------------------------------------------------------------------- #
# The identifier rule on the runtime name
# --------------------------------------------------------------------------- #


@pytest.mark.base
def test_a_component_refuses_a_runtime_name_that_is_not_an_identifier() -> None:
    """Constructing a component with a name that is not a plain identifier fails immediately.

    The runtime name is the prefix of every result column and the key a declarative
    energy-system file addresses the component by, so a name with a space or a hyphen in it
    cannot be expressed by a file at all. ``Component.__init__`` is the one place that name
    becomes real, and rejecting there means the mistake surfaces where it was made rather
    than as an unwritable recording much later.
    """
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = example_component.ExampleComponentConfig.get_default_example_component(
        component_id=ComponentID(name="Example Component")
    )
    with pytest.raises(ValueError) as rejection:
        example_component.ExampleComponent(config=config, my_simulation_parameters=sim_params)
    assert "Example Component" in str(rejection.value)
    assert "component name" in str(rejection.value)


@pytest.mark.base
def test_the_identifier_rule_names_the_specific_mistake() -> None:
    """Each way of getting a name wrong is reported as itself, not as one generic message.

    A glob pattern and a filesystem path are deliberate acts, not typos, so telling their
    author that patterns and paths are not part of the vocabulary is far more useful than
    repeating which characters an identifier may contain.
    """
    NameSyntax.require_identifier("Pv_South_1", "component")
    with pytest.raises(ValueError, match="wildcards are not part of this vocabulary"):
        NameSyntax.require_identifier("pv_*", "component")
    with pytest.raises(ValueError, match="never a path"):
        NameSyntax.require_identifier("../pv", "component")
    with pytest.raises(ValueError, match="must not be empty"):
        NameSyntax.require_identifier("", "component")
    with pytest.raises(ValueError, match="must be a string"):
        NameSyntax.require_identifier(3, "component")


@pytest.mark.base
def test_the_file_format_and_the_component_runtime_share_one_identifier_rule() -> None:
    """The energy-system format's name grammar is the same object the component runtime uses.

    Two copies of one regular expression would drift, and a name accepted at construction but
    refused as a file key — or the reverse — is exactly the failure the shared definition
    exists to make impossible.
    """
    assert NameRules.IDENTIFIER_PATTERN is NameSyntax.IDENTIFIER_PATTERN
    assert NameRules.WILDCARD_CHARACTERS == NameSyntax.WILDCARD_CHARACTERS
    assert NameRules.PATH_CHARACTERS == NameSyntax.PATH_CHARACTERS
