"""The building derives where its technical room is from the cellar code of its TABULA row (bead hisim-device-location-820d.1).

An unheated cellar (``N``) gives an unheated space; a heated cellar (``C``), a partly heated one (``P``), no cellar
(``-``), an unheated cellar inside the insulated envelope (``NI``) and the synthetic averages (``0``) give the heated
zone. A code the table does not know is refused, and so is a technical room outdoors. Every preset leaves the field
``AUTO``, a file may pin it, and the building contributes the resolved value as the sizing fact
``technical_room_location``.
"""

from typing import ClassVar, Tuple

import pytest

from hisim import loadtypes as lt
from hisim.components.building import BuildingConfig, BuildingInformation, CellarCondition
from hisim.config import AUTO, Size, SizingContext

HEATED_ZONE = lt.InstallationLocation.HEATED_ZONE
UNHEATED_SPACE = lt.InstallationLocation.UNHEATED_SPACE


class GermanClasses:
    """The national German TABULA classes, each with the cellar code its rows state and the technical room it gives.

    Written out by hand from ``episcope-tabula.csv`` rather than derived from the table under test, so a changed
    mapping or a changed row fails here. A class is named by the code prefix of its rows; the first row of the prefix
    in the file is read. ``DE.N.TH.08`` states ``P`` in its first variants and ``N`` in ``002.003``, so that variant is
    listed by its full code.
    """

    #: (building code or prefix, cellar code, technical room).
    ROWS: ClassVar[Tuple[Tuple[str, str, lt.InstallationLocation], ...]] = (
        *((f"DE.N.SFH.{number:02d}.", "N", UNHEATED_SPACE) for number in range(1, 11)),
        ("DE.N.SFH.11.", "-", HEATED_ZONE),
        ("DE.N.SFH.12.", "-", HEATED_ZONE),
        ("DE.N.TH.02.", "C", HEATED_ZONE),
        *((f"DE.N.TH.{number:02d}.", "N", UNHEATED_SPACE) for number in range(3, 8)),
        ("DE.N.TH.08.Gen.ReEx.001.001", "P", HEATED_ZONE),
        ("DE.N.TH.08.Gen.ReEx.002.003", "N", UNHEATED_SPACE),
        ("DE.N.TH.09.", "N", UNHEATED_SPACE),
        ("DE.N.TH.10.", "P", HEATED_ZONE),
        ("DE.N.TH.11.", "NI", HEATED_ZONE),
        ("DE.N.TH.12.", "NI", HEATED_ZONE),
        ("DE.N.MFH.01.", "N", UNHEATED_SPACE),
        ("DE.N.MFH.02.", "C", HEATED_ZONE),
        ("DE.N.MFH.03.", "P", HEATED_ZONE),
        *((f"DE.N.MFH.{number:02d}.", "N", UNHEATED_SPACE) for number in range(4, 11)),
        ("DE.N.MFH.11.", "-", HEATED_ZONE),
        ("DE.N.MFH.12.", "-", HEATED_ZONE),
        *((f"DE.N.AB.{number:02d}.", "N", UNHEATED_SPACE) for number in range(2, 7)),
        ("DE.N.SFH-TH.01-06.", "0", HEATED_ZONE),
        ("DE.N.SFH-TH.07-08.", "0", HEATED_ZONE),
        ("DE.N.SFH-TH.09-10.", "0", HEATED_ZONE),
        ("DE.N.MFH-AB.01-06.", "0", HEATED_ZONE),
        ("DE.N.MFH-AB.07-08.", "0", HEATED_ZONE),
        ("DE.N.MFH-AB.09-10.", "0", HEATED_ZONE),
    )

    @staticmethod
    def first_code(prefix: str) -> str:
        """Return the first building code of the TABULA file that starts with ``prefix``."""
        codes = BuildingInformation.read_housing_reference_dataframe()["Code_BuildingVariant"].dropna()
        return str(codes[codes.str.startswith(prefix)].iloc[0])


def resolved(config: BuildingConfig) -> BuildingConfig:
    """Resolve a building configuration against the weather's two facts, as a system does."""
    return config.resolve(SizingContext(heating_reference_temperature_in_celsius=-7.0, weather_identity="weather"))


@pytest.mark.base
@pytest.mark.parametrize(
    ("cellar_code", "expected"),
    [("N", UNHEATED_SPACE), ("C", HEATED_ZONE), ("P", HEATED_ZONE), ("-", HEATED_ZONE), ("NI", HEATED_ZONE), ("0", HEATED_ZONE)],
)
def test_each_cellar_code_gives_its_technical_room(cellar_code: str, expected: lt.InstallationLocation) -> None:
    """Every row of the cellar-code table: an unheated cellar is an unheated space, every other code the heated zone.

    Catches: a code mapped to the wrong place, which would move every device of such a house to the other side of its
    envelope.
    """
    assert BuildingInformation.technical_room_location_of_cellar_code(cellar_code) is expected


@pytest.mark.base
@pytest.mark.parametrize("cellar_code", ["X", "", "nan", "n", "PI"])
def test_a_cellar_code_the_table_does_not_know_is_refused(cellar_code: str) -> None:
    """A code outside the table fails loudly and names the known codes.

    Catches: a new TABULA export whose code would otherwise be given a meaning nobody decided.
    """
    with pytest.raises(ValueError, match="cellar code .* is unknown"):
        BuildingInformation.technical_room_location_of_cellar_code(cellar_code)


@pytest.mark.base
def test_the_table_maps_every_cellar_condition_and_never_outdoors() -> None:
    """Every cellar condition has a technical room, and none is outdoors.

    Catches: a condition added to the enum without a decision in the table, and a technical room outside the house.
    """
    table = BuildingInformation.TECHNICAL_ROOM_LOCATION_BY_CELLAR_CONDITION
    assert set(table) == set(CellarCondition)
    assert lt.InstallationLocation.OUTDOORS not in table.values()


@pytest.mark.base
@pytest.mark.parametrize(("code_or_prefix", "cellar_code", "expected"), GermanClasses.ROWS)
def test_each_german_class_gives_the_technical_room_of_its_cellar(
    code_or_prefix: str, cellar_code: str, expected: lt.InstallationLocation
) -> None:
    """Every national German class reads its row's cellar code and gives its technical room.

    Catches: a row read under the wrong column, and the older-house-with-a-cellar, newer-house-without rule of the
    German classes going wrong for one class.
    """
    building_code = GermanClasses.first_code(code_or_prefix)
    assert BuildingInformation.cellar_code_of(building_code) == cellar_code
    config = BuildingConfig.for_tabula_code("Building", building_code=building_code)
    assert resolved(config).technical_room_location is expected


@pytest.mark.base
def test_a_building_code_no_row_has_is_refused() -> None:
    """A code that names no row fails, naming the code.

    Catches: a missing row read as an unknown or an empty cellar code.
    """
    with pytest.raises(ValueError, match="No TABULA row has the building code 'DE.N.SFH.99.Gen.ReEx.001.001'"):
        BuildingInformation.cellar_code_of("DE.N.SFH.99.Gen.ReEx.001.001")


@pytest.mark.base
def test_the_preset_leaves_the_location_auto_and_resolving_derives_it_from_the_row() -> None:
    """The default house (``DE.N.SFH.05``, an unheated cellar) has its technical room in an unheated space.

    Catches: a preset that pins the location, so a house without a cellar would keep the default house's answer.
    """
    config = BuildingConfig.preset_german_single_family_home("Building")
    assert config.technical_room_location is AUTO
    assert resolved(config).technical_room_location is UNHEATED_SPACE
    newer = BuildingConfig.for_tabula_code("Building", building_code="DE.N.SFH.11.Gen.ReEx.001.001")
    assert resolved(newer).technical_room_location is HEATED_ZONE


@pytest.mark.base
def test_a_pinned_location_is_kept_whatever_the_row_says() -> None:
    """A file that pins the location keeps it: a house with an unheated cellar may have its boiler room heated.

    Catches: the law overwriting a pinned value.
    """
    config = BuildingConfig.preset_german_single_family_home("Building")
    config.technical_room_location = HEATED_ZONE
    assert resolved(config).technical_room_location is HEATED_ZONE


@pytest.mark.base
@pytest.mark.parametrize("spelling", ["heated_zone", "HEATED_ZONE"])
def test_a_file_spells_the_location_by_its_value_or_its_name(spelling: str) -> None:
    """The configuration decodes the location from either word an energy-system file may write.

    Catches: a wire codec that accepts only one of the two spellings.
    """
    wire = resolved(BuildingConfig.preset_german_single_family_home("Building")).to_dict()
    wire["technical_room_location"] = spelling
    assert BuildingConfig.from_dict(wire).technical_room_location is HEATED_ZONE


@pytest.mark.base
def test_a_technical_room_outdoors_is_refused_naming_the_field() -> None:
    """A technical room is under the house's roof, so ``outdoors`` is refused when the configuration is built.

    Catches: a building that would send every device's loss outdoors through its own field.
    """
    wire = resolved(BuildingConfig.preset_german_single_family_home("Building")).to_dict()
    wire["technical_room_location"] = "outdoors"
    with pytest.raises(ValueError, match="technical_room_location 'outdoors'"):
        BuildingConfig.from_dict(wire)


@pytest.mark.base
def test_the_building_contributes_its_resolved_location_as_a_sizing_fact() -> None:
    """The resolved location is the fact ``technical_room_location`` the devices size against.

    Catches: a contribution that hands on ``AUTO`` or the class default instead of the resolved value.
    """
    config = BuildingConfig.for_tabula_code("Building", building_code="DE.N.SFH.12.Gen.ReEx.001.001")
    facts = BuildingConfig.sizing_facts(resolved(config), SizingContext())
    assert facts["technical_room_location"] is HEATED_ZONE
    assert "technical_room_location" in {fact for entry in BuildingConfig.SIZING_CONTRIBUTIONS for fact in entry.facts}
    assert Size.TECHNICAL_ROOM_LOCATION.facts_read()[0][0] == "technical_room_location"
