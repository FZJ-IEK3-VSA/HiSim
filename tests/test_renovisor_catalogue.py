"""Tests of the catalogue loader, the id derivation and the temporary spelling table.

The catalogue is the closed list a request may name, so three things have to hold: the derived ids
are the ones decision Q1 fixed and are unique, the option values a request may send are in HiSim's
spelling, and the spelling table shrinks rather than grows as the catalogue is revised.
"""

from typing import Tuple

import pytest

from hisim.renovisor.catalogue import (
    AccessLevel,
    Catalogue,
    CatalogueSpellings,
    IdDerivation,
    MaterialResolution,
    OptionValueType,
)

pytestmark = pytest.mark.base

EXPECTED_MEASURE_IDS: Tuple[str, ...] = (
    "EXTERNAL_INSULATION",
    "INTERNAL_DRY_LINING_INSULATION",
    "ADD_INTERNAL_DRY_LINING",
    "CAVITY_WALL_INSULATION",
    "WINDOW_REPLACEMENT",
    "OUTSIDE_SHADING",
    "DOOR_REPLACEMENT",
    "BASEMENT_CEILING_INSULATION",
    "BASEMENT_INTERNAL_INSULATION",
    "BASEMENT_EXTERNAL_INSULATION",
    "SOLID_GROUND_FLOOR_INSULATION",
    "SUSPENDED_GROUND_FLOOR_INSULATION",
    "WARM_ROOF_INSULATION",
    "RAFTER_INSULATION",
    "ROLLED_OUT_ATTIC_INSULATION",
    "TOP_FLOOR_CEILING_INSULATION",
    "VENTILATION_SYSTEM",
    "SHALLOW_AIR_TIGHTNESS_MEASURES",
    "HEATING_SYSTEM",
    "HEATING_INSTALLATION",
    "AIR_CONDITIONERS",
    "HOT_WATER_SYSTEM",
    "TEMPERATURE_CONTROL_SYSTEM",
    "REPLACE_WHITE_APPLIANCES",
    "INSTALL_NEW_LED_LIGHTS",
    "PHOTOVOLTAIC_SYSTEM",
    "BATTERY_SYSTEM",
    "SOLAR_THERMAL_SYSTEM",
    "ELECTRIC_VEHICLE",
    "CHANGE_ROOM_TEMPERATURE",
    "DIY_SEALING_OF_AIR_LEAKS",
    "THERMOCOVER_FOR_THE_WINDOWS",
    "OPTIMIZE_BEHAVIOUR_FOR_SELF_CONSUMPTION_OF_PV",
)


@pytest.fixture(name="catalogue", scope="module")
def fixture_catalogue() -> Catalogue:
    """The vendored catalogue, loaded once for the module."""
    return Catalogue.load()


@pytest.mark.parametrize(
    "display_name,expected",
    [
        ("external insulation", "EXTERNAL_INSULATION"),
        ("optimize behaviour for self-consumption of PV", "OPTIMIZE_BEHAVIOUR_FOR_SELF_CONSUMPTION_OF_PV"),
        ("EPS Foam", "EPS_FOAM"),
        ("  spaced  out  ", "SPACED_OUT"),
    ],
)
def test_upper_id_derivation(display_name: str, expected: str) -> None:
    """Non-alphanumeric runs become one underscore and the result is upper-cased."""
    assert IdDerivation.upper_id(display_name) == expected


@pytest.mark.parametrize(
    "display_name,expected",
    [
        ("type of system", "type_of_system"),
        ("thickness_in_mm", "thickness_in_mm"),
        ("size in percent of roof area", "size_in_percent_of_roof_area"),
        ("air barrier", "air_barrier"),
    ],
)
def test_lower_id_derivation(display_name: str, expected: str) -> None:
    """Option ids follow the same rule in lower case."""
    assert IdDerivation.lower_id(display_name) == expected


def test_the_thirty_three_measure_ids_are_the_expected_ones(catalogue: Catalogue) -> None:
    """The derivation produces exactly the ids decision Q1's convention names, in file order."""
    assert catalogue.measure_ids() == EXPECTED_MEASURE_IDS
    assert len(set(EXPECTED_MEASURE_IDS)) == 33


def test_measures_without_options_load_as_empty(catalogue: Catalogue) -> None:
    """The catalogue writes 'options:' and 'options: []' interchangeably; both mean no options."""
    assert catalogue.by_id("ADD_INTERNAL_DRY_LINING").options == ()
    assert catalogue.by_id("OUTSIDE_SHADING").options == ()


def test_option_kinds_and_access_levels(catalogue: Catalogue) -> None:
    """A measure's options carry their declared type and who is expected to supply them."""
    spec = catalogue.by_id("SUSPENDED_GROUND_FLOOR_INSULATION")
    assert spec.option("material").value_type is OptionValueType.ENUM
    assert spec.option("material").access_level is AccessLevel.EVERYONE
    assert spec.option("thickness_in_mm").value_type is OptionValueType.INTEGER
    assert spec.option("thickness_in_mm").access_level is AccessLevel.EXPERTS
    assert spec.option("air_barrier").value_type is OptionValueType.BOOLEAN


def test_enum_values_arrive_in_hisim_spelling(catalogue: Catalogue) -> None:
    """The spelling table is applied on load, so nothing downstream sees a RenoVisor spelling."""
    distribution = catalogue.by_id("HEATING_INSTALLATION").option("type_of_system")
    assert distribution.values == ("FLOORHEATING", "LOW_TEMPERATURE_RADIATOR", "RADIATOR")
    assert distribution.display_values == ("surface_heating", "low_temperature_radiator", "conventional_radiator")

    ventilation = catalogue.by_id("VENTILATION_SYSTEM").option("type_of_system")
    assert ventilation.values[0] == "MECHANICAL_EXTRACT"

    material = catalogue.by_id("EXTERNAL_INSULATION").option("material")
    assert material.values == ("polystyrene_eps_rigid_board", "extruded_polystyrene_xps")
    assert material.display_values == ("EPS", "XPS")


def test_integer_value_lists_arrive_as_strings(catalogue: Catalogue) -> None:
    """A listed integer option keeps its digits; only enum values are re-spelled."""
    assert catalogue.by_id("DOOR_REPLACEMENT").option("glazing_panes").values == ("0", "2", "3")


def test_unresolvable_values_keep_a_distinct_spelling(catalogue: Catalogue) -> None:
    """An option with two unresolvable materials still offers two distinct values to send."""
    values = catalogue.by_id("INTERNAL_DRY_LINING_INSULATION").option("material").values
    assert values == ("THERMAL_LAMINATE_DRYLINING_BOARD", "MINERAL_WOOL")
    for value in values:
        assert CatalogueSpellings.is_unresolved("INTERNAL_DRY_LINING_INSULATION", "material", value)


def test_the_spelling_table_carries_no_identity_entries() -> None:
    """The table shrinks as the catalogue adopts HiSim's spelling; an identity entry is dead weight."""
    for (measure_id, option_id), mapping in CatalogueSpellings.BY_OPTION.items():
        for catalogue_value, hisim_value in mapping.items():
            assert catalogue_value != hisim_value, (
                f"({measure_id}, {option_id}) maps '{catalogue_value}' onto itself; delete the entry"
            )


def test_the_spelling_table_only_names_real_measures_and_options(catalogue: Catalogue) -> None:
    """A table entry for a measure or option the catalogue dropped would never fire again."""
    for measure_id, option_id in CatalogueSpellings.BY_OPTION:
        assert option_id in catalogue.by_id(measure_id).option_ids()


def test_unknown_measure_id_raises(catalogue: Catalogue) -> None:
    """An id the catalogue does not have is a KeyError the application turns into a reason code."""
    with pytest.raises(KeyError):
        catalogue.by_id("INVENTED_MEASURE")


def test_unresolved_marker_round_trips() -> None:
    """The marker is a value like any other, so a caller can compare against it."""
    assert MaterialResolution.is_unresolved(MaterialResolution.UNRESOLVED.value)
    assert not MaterialResolution.is_unresolved("metac_glasswool")
