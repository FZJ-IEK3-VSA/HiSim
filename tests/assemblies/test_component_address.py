"""The structured address of an assembly member (``assemblies_spec.md`` §2.4, D5).

``ComponentID`` gains a ``path`` of import (and instance) keys and an informative ``assembly``; its
key joins building, unit and the serialized address. Everything here pins the two promises that
make that change safe: with an empty path every key, every dump and every cache key is byte for
byte what it was, and the address separator ``-`` reaches a runtime name only through an address.
"""

import dataclasses
import itertools
from typing import Optional

import pytest

from hisim.config import AddressStep, ComponentID, DisplayConfig, NameSyntax
from hisim.energy_system.errors import EnergySystemFormatError
from hisim.energy_system.validation import validate_structure
from hisim.postprocessing.kpi_computation.kpi_structure import KpiSource
from tests.assemblies.mock_components import MockPVSystemConfig
from tests.assemblies.helpers import read_system


@pytest.mark.base
@pytest.mark.parametrize("building, unit", list(itertools.product((None, "BUI1"), (None, "APT2"))))
def test_an_empty_path_leaves_the_key_and_the_dump_exactly_as_they_were(
    building: Optional[str], unit: Optional[str]
) -> None:
    """The key of a component outside every assembly is today's ``_``-joined key, and its dump has no new field."""
    identity = ComponentID("HeatPump", building=building, unit=unit)

    expected = "_".join(part for part in (building, unit, "HeatPump") if part is not None)
    assert identity.key == expected
    assert identity.address == "HeatPump"
    assert identity.to_dict() == {"name": "HeatPump", "building": building, "unit": unit}
    assert ComponentID.from_dict(identity.to_dict()) == identity


@pytest.mark.base
def test_the_key_serializes_the_address_with_hyphens() -> None:
    """Import and instance keys, then the member, joined by ``-``; building and unit still by ``_``."""
    east = ComponentID("PVSystem", path=(AddressStep("pv", "east"),), assembly="mock/pv_array")
    nested = ComponentID("Tank", path=(AddressStep("dhw"), AddressStep("tank")))
    housed = ComponentID("HeatPump", building="BUI1", path=(AddressStep("heating"),))

    assert east.key == "pv-east-PVSystem"
    assert nested.key == "dhw-tank-Tank"
    assert housed.key == "BUI1_heating-HeatPump"
    assert housed.address == "heating-HeatPump"


@pytest.mark.base
def test_an_address_stays_frozen_hashable_and_round_trips_through_its_dump() -> None:
    """The identity is a value: equal addresses hash alike, a list path is frozen, the dump reads back."""
    identity = ComponentID(
        "PVSystem", path=[AddressStep("pv", "east")], assembly="mock/pv_array"  # type: ignore[arg-type]
    )

    assert identity.path == (AddressStep("pv", "east"),)
    assert hash(identity) == hash(ComponentID("PVSystem", path=(AddressStep("pv", "east"),), assembly="mock/pv_array"))
    assert ComponentID.from_dict(identity.to_dict()) == identity
    with pytest.raises(dataclasses.FrozenInstanceError):
        identity.name = "Other"  # type: ignore[misc]


@pytest.mark.base
@pytest.mark.parametrize("bad", ["pv-east", "pv east", ""])
def test_an_import_or_instance_key_obeys_the_identifier_rule(bad: str) -> None:
    """A key with the separator would make the serialization ambiguous, so it is refused."""
    with pytest.raises(ValueError, match="not a usable import name"):
        AddressStep(bad)
    with pytest.raises(ValueError, match="not a usable instance name"):
        AddressStep("pv", bad)
    with pytest.raises(ValueError, match="not a usable component name"):
        ComponentID(bad or "a b")


@pytest.mark.base
def test_the_cache_key_is_the_same_wherever_a_member_sits() -> None:
    """The address, like the building, decides nothing about a cached series."""
    plain = MockPVSystemConfig.preset_rooftop("PVSystem")
    addressed = MockPVSystemConfig.preset_rooftop("PVSystem")
    addressed.component_id = ComponentID("PVSystem", path=(AddressStep("pv", "east"),), assembly="mock/pv_array")

    assert addressed.cache_key_view().to_json() == plain.cache_key_view().to_json()
    assert plain.to_dict()["component_id"] == {"name": "PVSystem", "building": None, "unit": None}


@pytest.mark.base
def test_a_runtime_name_admits_the_separator_only_between_identifiers() -> None:
    """The component-key grammar: identifiers joined by ``-``; wildcards and paths still refused."""
    assert NameSyntax.is_component_key("pv-east-PVSystem")
    assert NameSyntax.is_component_key("Weather")
    for bad in ("pv--east", "-pv", "pv-", "pv_*", "../pv", "pv east"):
        assert not NameSyntax.is_component_key(bad)
    with pytest.raises(ValueError, match="wildcards"):
        NameSyntax.require_component_key("pv-*")


@pytest.mark.base
def test_the_kpi_source_of_a_member_names_its_import_instance_member_and_assembly() -> None:
    """``KpiSource.for_component`` fills the address fields from the outermost step."""
    identity = ComponentID("Tank", path=(AddressStep("dhw", "main"), AddressStep("tank")), assembly="storage/tank")

    source = KpiSource.for_component(identity, DisplayConfig())

    assert (source.import_key, source.instance, source.member, source.assembly) == (
        "dhw",
        "main",
        "Tank",
        "storage/tank",
    )
    assert source.name == "dhw-main-tank-Tank"


@pytest.mark.base
def test_the_kpi_source_of_a_plain_component_is_unchanged() -> None:
    """Without a path the source is exactly what it was before assemblies."""
    source = KpiSource.for_component(ComponentID("Building"), DisplayConfig(pretty_name="House"))

    assert source == KpiSource(member="Building", name="Building", display_name="House")


@pytest.mark.base
def test_an_authored_file_with_a_hyphen_in_a_name_is_refused() -> None:
    """The authored grammar never admits the separator: only an expansion produces such a name."""
    with pytest.raises(EnergySystemFormatError, match="EF-08"):
        read_system(
            """
            schema_version: 3
            name: authored
            components:
              pv-east:
                class: tests.assemblies.mock_components.MockWeather
                preset: standard
            """
        )


@pytest.mark.base
def test_a_hyphenated_name_no_expansion_produced_is_refused_by_the_validator() -> None:
    """A model built in memory with a hyphenated name and no address for it fails validation."""
    model, _lines = read_system(
        """
        schema_version: 3
        name: built
        components:
          Weather:
            class: tests.assemblies.mock_components.MockWeather
            preset: standard
        """
    )
    entry = model.components["Weather"].model_copy(update={"name": "pv-Weather"})
    smuggled = model.model_copy(update={"components": {"pv-Weather": entry}})

    with pytest.raises(EnergySystemFormatError, match="EF-08 at components.pv-Weather"):
        validate_structure(smuggled)
