"""The LoadProfileGenerator's catalogue references, written into a record and read back out.

The occupancy of a household is not a number: it is a set of references into the
LoadProfileGenerator's catalogue — which household, which commuting distances, which vehicles,
which charging equipment — and each reference is a name paired with a globally unique identifier.
Those references are the whole content of the configuration, so a record that writes them in a
spelling their own class does not read back is a record that reproduces a *different* household,
silently and without an error: the reference deserializes as an empty one, and the run falls back
to whatever an empty reference means.

That is not a hypothetical. The scenario files of the older JSON mode spell the same references in
snake case — ``household: {name, guid: {str_val}}`` — while the class's fields are ``Name``,
``Guid`` and ``StrVal``, and the older executor only survives it by pascalizing those four keys by
hand before deserializing, one hard-coded special case for one component. This format writes the
class's own spelling, so no special case exists here and none may creep in; these tests are what
says so.

The two directions are checked separately, because they can fail for different reasons. One test
takes the named constructor an author would write, sends it through the record's own value writer
and the file's own reader, and compares the four references field for field. The other takes the
whole shipped household, realizes it and reads the realized record back, which is the path a real
re-run takes.
"""

# clean

from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml
from utspclient.helpers.lpgdata import (
    ChargingStationSets,
    Households,
    TransportationDeviceSets,
    TravelRouteSets,
)

from hisim.components.loadprofilegenerator_utsp_connector import (
    LpgDataAcquisitionMode,
    UtspLpgConnectorConfig,
)
from hisim.energy_system import dump_energy_system, parse_energy_system
from hisim.energy_system.codec import ConfigValueCodec
from hisim.energy_system.executor import SimulationParametersReader, build_energy_system
from hisim.energy_system.record import ConfigBlockWriter, realize
from hisim.energy_system.path_resolver import PathResolver


class References:
    """The occupancy this module round-trips, and the fields that carry its catalogue references."""

    #: The four configuration fields holding a catalogue reference. Every one of them has to
    #: survive a round trip; checking only the household would miss the three the mobility model
    #: reads, which are exactly the ones nobody looks at until a car will not charge.
    FIELDS: ClassVar[tuple] = (
        "household",
        "travel_route_set",
        "transportation_device_set",
        "charging_station_set",
    )

    #: Name given to the occupancy under test.
    NAME: ClassVar[str] = "occupancy"

    #: The shipped household, whose occupancy is configured by a preset that calls the same named
    #: constructor; it is the file a real record is written from.
    HOUSEHOLD: ClassVar[Path] = (
        Path(__file__).resolve().parents[1] / "energy_systems" / "gas_boiler_household.energy_system.yaml"
    )

    #: The one-day parameters the shipped household is exercised with.
    PARAMETERS: ClassVar[Path] = (
        Path(__file__).resolve().parents[1] / "energy_systems" / "one_day_15min.simulation.yaml"
    )

    @classmethod
    def deliberately_unusual(cls) -> UtspLpgConnectorConfig:
        """Builds an occupancy whose four references all differ from the preset's.

        Every reference is chosen away from the default so that a round trip which quietly
        substitutes a default — or an empty reference — cannot pass by coincidence.

        Returns:
            The configuration.
        """
        return UtspLpgConnectorConfig.for_household(
            cls.NAME,
            household=Households.CHR03_Family_1_child_both_at_work,
            # No profile for this household is shipped, so it is one the LPG has to compute.
            data_acquisition_mode=LpgDataAcquisitionMode.USE_LOCAL_LPG,
            travel_route_set=TravelRouteSets.Travel_Route_Set_for_30km_Commuting_Distance,
            transportation_device_set=TransportationDeviceSets.Bus_and_two_30_km_h_Cars,
            charging_station_set=ChargingStationSets.Charging_At_Home_with_03_7_kW,
        )

    @classmethod
    def assert_same(cls, before: Any, after: Any) -> None:
        """Asserts that two configurations carry identical catalogue references.

        Args:
            before: The configuration that was written.
            after: The configuration read back.

        Raises:
            AssertionError: If any reference lost its name or its identifier.
        """
        for field in cls.FIELDS:
            written, read = getattr(before, field), getattr(after, field)
            assert read == written, f"{field} changed: {written!r} became {read!r}"
            assert read.Name, f"{field} came back without a name"
            assert read.Guid is not None and read.Guid.StrVal, f"{field} came back without a Guid"


@pytest.mark.base
def test_a_named_constructors_catalogue_references_survive_the_record_round_trip() -> None:
    """Catches a reference spelling that writes one household and reads back another.

    The round trip is the record's own: the value writer that renders a configuration into the
    block a record carries, one pass through YAML, and the reader that hands a complete block back
    to the class. A mismatch anywhere in it turns the four references into empty ones, which no
    exception reports.
    """
    written = References.deliberately_unusual()
    block = ConfigBlockWriter(PathResolver.default()).block(References.NAME, written)

    reloaded = yaml.safe_load(yaml.safe_dump(block))
    codec = ConfigValueCodec(UtspLpgConnectorConfig)
    payload = codec.to_deserializer_payload(reloaded, "components.occupancy.config", References.NAME)
    payload[ConfigBlockWriter.IDENTITY_FIELD] = written.component_id.to_dict()  # type: ignore[attr-defined]
    read_back = UtspLpgConnectorConfig.from_dict(payload)

    References.assert_same(written, read_back)


@pytest.mark.base
def test_the_written_block_spells_the_references_the_way_their_class_reads_them() -> None:
    """Catches the snake-case spelling of the older scenario files leaking into this format.

    ``Name``/``Guid``/``StrVal`` is what ``utspclient`` declares and therefore what its
    deserializer reads. The older format writes ``name``/``guid``/``str_val`` and repairs it with a
    hard-coded special case in its executor; this format needs no such case, and the absence of the
    lower-case spelling is what proves it.
    """
    block = ConfigBlockWriter(PathResolver.default()).block(
        References.NAME, References.deliberately_unusual()
    )

    for field in References.FIELDS:
        assert set(block[field]) == {"Name", "Guid"}, f"{field} is not spelled as its class reads it"
        assert set(block[field]["Guid"]) == {"StrVal"}


@pytest.mark.base
def test_the_realized_record_of_the_shipped_household_reads_its_occupancy_back(tmp_path: Path) -> None:
    """Catches a real record whose occupancy would not survive its own re-execution.

    This is the path a re-run actually takes — build the household, realize what it built, write it
    out canonically and read it in again — so it is the one that says the promise holds for a file
    somebody runs rather than only for a configuration built in a test.
    """
    parameters = SimulationParametersReader.read(References.PARAMETERS)
    parameters.result_directory = str(tmp_path)
    built = build_energy_system(
        References.HOUSEHOLD, parameters, simulation_parameters_path=References.PARAMETERS
    )
    record_path = tmp_path / "realized.energy_system.yaml"
    record_path.write_text(dump_energy_system(realize(built)), encoding="utf-8")

    reread = parse_energy_system(record_path)
    block = reread.components[References.NAME].config
    read_back = UtspLpgConnectorConfig.from_dict(
        ConfigValueCodec(UtspLpgConnectorConfig).to_deserializer_payload(
            dict(block), "components.occupancy.config", References.NAME
        )
        | {ConfigBlockWriter.IDENTITY_FIELD: built.configured.config_of(References.NAME).component_id.to_dict()}
    )

    References.assert_same(built.configured.config_of(References.NAME), read_back)


@pytest.mark.base
def test_the_predefined_mode_refuses_a_household_whose_profile_is_not_shipped() -> None:
    """Catches the household argument being decoration on the constructor that exists for it.

    In the predefined mode the profile is read from disk by name, and the name — not the
    household reference — decides what is simulated. Accepting any household there would mean
    that asking for a family of three quietly produces the profile of the shipped couple: a run
    that finishes, reports plausible numbers and answers a different question than the one the
    file asked. The refusal has to name the household, the shipped alternatives and the modes
    that can compute it, because the author's next step is to pick one of the three.
    """
    with pytest.raises(ValueError) as refused:
        UtspLpgConnectorConfig.for_household(
            "occupancy", household=Households.CHR03_Family_1_child_both_at_work
        )

    message = str(refused.value)
    assert "CHR03 Family, 1 child, both at work" in message
    assert "CHR01 Couple both at Work" in message
    assert LpgDataAcquisitionMode.USE_LOCAL_LPG.value in message
    assert LpgDataAcquisitionMode.USE_UTSP.value in message


@pytest.mark.base
def test_the_predefined_mode_refuses_several_households() -> None:
    """Catches a multi-apartment occupancy silently collapsing to one shipped profile.

    A list of households is what a multi-apartment building passes, and the predefined mode
    reads exactly one profile file, so the list would be ignored altogether rather than merely
    substituted. The count belongs in the message: it is what tells the author that the
    apartments, not the household, are the reason the mode cannot serve them.
    """
    with pytest.raises(ValueError) as refused:
        UtspLpgConnectorConfig.for_household(
            "occupancy",
            household=[Households.CHR01_Couple_both_at_Work, Households.CHR01_Couple_both_at_Work],
        )

    assert "2 households" in str(refused.value)


@pytest.mark.base
def test_a_computed_household_keeps_its_reference_and_names_no_profile() -> None:
    """Catches a computing mode being handed the profile name of a household it is not simulating.

    Once the LoadProfileGenerator computes the household, the profile name is not the source of
    the data any more; leaving the shipped name behind would make the configuration claim a
    household it does not simulate, and would be what the run silently falls back to if the
    generator failed.
    """
    config = UtspLpgConnectorConfig.for_household(
        "occupancy",
        household=Households.CHR03_Family_1_child_both_at_work,
        data_acquisition_mode=LpgDataAcquisitionMode.USE_LOCAL_LPG,
    )

    assert config.household is Households.CHR03_Family_1_child_both_at_work
    assert config.name_of_predefined_loadprofile is None
    assert config.data_acquisition_mode is LpgDataAcquisitionMode.USE_LOCAL_LPG


@pytest.mark.base
def test_the_shipped_household_is_the_one_the_preset_builds() -> None:
    """Catches the shipped-profile list and the canonical preset drifting apart.

    The preset exists because exactly one household can be simulated with nothing installed; if
    a profile were added or removed without the preset following, the class would advertise a
    default that the predefined mode cannot serve.
    """
    assert UtspLpgConnectorConfig.predefined_households() == ("CHR01 Couple both at Work",)

    preset = UtspLpgConnectorConfig.preset_standard("occupancy")

    assert preset.name_of_predefined_loadprofile == "CHR01 Couple both at Work"
    assert preset.data_acquisition_mode is LpgDataAcquisitionMode.USE_PREDEFINED_PROFILE
