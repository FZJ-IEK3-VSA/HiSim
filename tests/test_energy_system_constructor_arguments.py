"""Tests that a named constructor called from a file gets decoded arguments.

An energy-system file may configure a component through a named constructor, writing the
call as a mapping under ``constructor:``. Everything in that mapping arrives as plain YAML —
a string, a number, a mapping, a list — while the classmethod underneath expects an enum
member, a nested dataclass or a typed scalar, exactly as a configuration field does.

The defect these tests pin is that the arguments used to be forwarded verbatim. A weather
location written as ``AACHEN`` reached ``WeatherConfig.for_location`` as the string, which
did ``location.value`` on it and failed with ``'str' object has no attribute 'value'`` — a
message naming neither the argument nor the spellings that would have worked. The LPG
occupancy failed the same way on its household references, and only the all-scalar TABULA
constructor worked at all, by accident of taking nothing but strings and numbers.

So the four constructors HiSim ships are each called from a file here, with the four kinds of
value the decoder knows — an enum by member name, an enum by member value, a mapping onto a
nested dataclass and a list of them — and the configurations they produce are compared with
the ones the same call produces in Python. Two more tests pin the messages a wrong value now
gets, and the last one pins the import-time rule that keeps the two halves honest: a
constructor may only ask for something a written value can become.
"""

# clean

from typing import Any, Callable, Dict, List

import pytest
from utspclient.helpers.lpgdata import Households

from hisim import loadtypes as lt
from hisim.components.building.config import BuildingConfig
from hisim.components.generic_car import CarConfig
from hisim.components.loadprofilegenerator_utsp_connector import (
    LpgDataAcquisitionMode,
    UtspLpgConnectorConfig,
)
from hisim.components.weather import LocationEnum, WeatherConfig, WeatherDataSourceEnum
from hisim.config import ComponentID, ConfigBase, constructor
from hisim.energy_system import EnergySystemBindingError, EnergySystemErrorId, expand_groups
from hisim.energy_system.configure import configure_energy_system
from hisim.energy_system.document import RawDocument
from hisim.energy_system.loader import EnergySystemReader

#: The one household reference every LPG fixture below uses. A file spells it as a mapping of
#: the field names ``JsonReference`` and ``StrGuid`` declare; Python spells it as this
#: catalogue member, and the two have to produce the same configuration.
HOUSEHOLD = Households.CHR01_Couple_both_at_Work

#: A weather built from a catalogue station named by its member name, with the optional
#: reader named the same way. ``LocationEnum`` spells its values as tuples, so the member
#: name is the only spelling a file has for it.
WEATHER_ENTRY = """  Weather:
    class: hisim.components.weather.Weather
    constructor:
      for_location:
        location: AACHEN
        data_source: DWD_TRY
"""

#: A building from a TABULA code: the all-scalar constructor, which worked before this change
#: and has to keep working after it.
BUILDING_ENTRY = """  Building:
    class: hisim.components.building.Building
    constructor:
      for_tabula_code:
        building_code: DE.N.SFH.05.Gen.ReEx.001.002
        absolute_conditioned_floor_area_in_m2: 121.2
"""

#: One LPG household as a mapping onto ``JsonReference``, in the predefined mode that reads
#: the profile from disk.
OCCUPANCY_ENTRY = """  Occupancy:
    class: hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector
    constructor:
      for_household:
        household:
          Name: CHR01 Couple both at Work
          Guid:
            StrVal: 516a33ab-79e1-4221-853b-967fc11cc85a
"""

#: Two apartments as a list of the same mapping. The predefined mode reads one profile from
#: disk and refuses a list, so a multi-apartment household is only meaningful in a computing
#: mode — which is what makes this the fixture for the list case.
MULTI_APARTMENT_ENTRY = """  Occupancy:
    class: hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector
    constructor:
      for_household:
        data_acquisition_mode: USE_UTSP
        household:
          - Name: CHR01 Couple both at Work
            Guid:
              StrVal: 516a33ab-79e1-4221-853b-967fc11cc85a
          - Name: CHR01 Couple both at Work
            Guid:
              StrVal: 516a33ab-79e1-4221-853b-967fc11cc85a
"""

#: A car of that occupancy, whose fuel is written as the enum's *value* (``Diesel``) rather
#: than as its member name (``DIESEL``). Both spellings are legal and they differ here, which
#: is what makes this the fixture for the by-value case.
CAR_ENTRY = """  Car:
    class: hisim.components.generic_car.Car
    constructor:
      for_household:
        household_name: CHR01
        car_name: Car1
        fuel: Diesel
        source_weight: 2
"""


def origins_of(entries: str) -> Dict[str, Any]:
    """Configures one inline file and returns each entry's configuration by name.

    The origins rather than the finished configurations, because an origin is precisely what
    the builder produced: the value a Python call to the same constructor has to match, before
    any override or any sized field enters into it.

    Args:
        entries: The body of the ``components`` block, indented by two spaces.

    Returns:
        A mapping from component name to the configuration its builder produced.
    """
    text = f"schema_version: 3\nname: constructor arguments\ncomponents:\n{entries}"
    model = EnergySystemReader.build(RawDocument.parse_text(text, "inline"), "inline")
    expanded, _ = expand_groups(model)
    return dict(configure_energy_system(expanded).origins)


@pytest.mark.base
def test_an_enum_argument_reaches_the_constructor_as_the_member() -> None:
    """Catches the weather location arriving as the string that spells it.

    The defect this pins: ``location: AACHEN`` was forwarded verbatim, so ``for_location``
    did ``location.value`` on a ``str`` and the author was told about an attribute rather than
    about the station they had named.
    """
    origins = origins_of(WEATHER_ENTRY)

    assert origins["Weather"] == WeatherConfig.for_location(
        "Weather", location=LocationEnum.AACHEN, data_source=WeatherDataSourceEnum.DWD_TRY
    )


@pytest.mark.base
def test_an_enum_argument_may_be_written_as_the_members_value() -> None:
    """Catches the by-value spelling of an enum being refused for a constructor argument.

    A ``config`` block accepts both the member name and the member value, because HiSim's
    enums spell the two alike often enough that an author cannot know which one applies. A
    constructor argument has to accept both for the same reason: ``fuel: Diesel`` is the value
    of ``LoadTypes.DIESEL`` and reads more naturally than the shouted member name.
    """
    origins = origins_of(OCCUPANCY_ENTRY + CAR_ENTRY)

    assert origins["Car"] == CarConfig.for_household(
        "Car", household_name="CHR01", car_name="Car1", fuel=lt.LoadTypes.DIESEL, source_weight=2
    )


@pytest.mark.base
def test_a_mapping_argument_is_rebuilt_by_the_class_it_is_written_onto() -> None:
    """Catches an LPG household reference staying a plain ``dict``.

    ``for_household`` takes a ``JsonReference``, which the LoadProfileGenerator's own code
    reads attribute by attribute. A mapping forwarded as written would reach the occupancy as
    a ``dict`` and fail inside the profile lookup, far from the line that wrote it.
    """
    origins = origins_of(OCCUPANCY_ENTRY)

    assert origins["Occupancy"] == UtspLpgConnectorConfig.for_household(
        "Occupancy", household=HOUSEHOLD
    )


@pytest.mark.base
def test_a_list_argument_is_decoded_item_by_item() -> None:
    """Catches a multi-apartment household, the one argument that is a list of objects.

    One reference per apartment is how the format states a multi-apartment building, so the
    decoder has to walk a list against the ``List[X]`` half of the parameter's union instead
    of treating the whole list as one value.
    """
    origins = origins_of(MULTI_APARTMENT_ENTRY)

    assert origins["Occupancy"] == UtspLpgConnectorConfig.for_household(
        "Occupancy",
        household=[HOUSEHOLD, HOUSEHOLD],
        data_acquisition_mode=LpgDataAcquisitionMode.USE_UTSP,
    )


@pytest.mark.base
def test_a_scalar_argument_reaches_an_all_scalar_constructor_unharmed() -> None:
    """Catches a regression in the one constructor that worked before arguments were decoded.

    ``for_tabula_code`` takes nothing but strings and numbers, which is why it was the only
    one a file could call at all. Decoding must leave it exactly where it was.
    """
    origins = origins_of(WEATHER_ENTRY + BUILDING_ENTRY)

    assert origins["Building"] == BuildingConfig.for_tabula_code(
        "Building",
        building_code="DE.N.SFH.05.Gen.ReEx.001.002",
        absolute_conditioned_floor_area_in_m2=121.2,
    )


@pytest.mark.base
def test_a_misspelled_enum_member_is_refused_with_the_members_and_a_suggestion() -> None:
    """Catches a mistyped station name reported as a missing Python attribute.

    The message a constructor argument gets is the message a ``config`` value gets: it names
    the argument, prints the members of the enum and suggests the one the author meant.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        origins_of(WEATHER_ENTRY.replace("AACHEN", "AACHN"))

    message = str(raised.value)
    assert raised.value.error_id is EnergySystemErrorId.UNDECODABLE_VALUE
    assert "components.Weather.constructor.for_location.location" in message
    assert "Did you mean: AACHEN?" in message
    assert "POTSDAM" in message and "LocationEnum" in message


@pytest.mark.base
def test_an_argument_of_the_wrong_scalar_type_is_refused_naming_the_type() -> None:
    """Catches a word written where a number belongs, which no builder would report clearly.

    A floor area of ``"large"`` would reach ``for_tabula_code``, be stored, and surface as a
    wrong building rather than as a refused file.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        origins_of(WEATHER_ENTRY + BUILDING_ENTRY.replace("121.2", "large"))

    message = str(raised.value)
    assert raised.value.error_id is EnergySystemErrorId.UNDECODABLE_VALUE
    assert (
        "components.Building.constructor.for_tabula_code."
        "absolute_conditioned_floor_area_in_m2" in message
    )
    assert "'large'" in message and "float" in message


@pytest.mark.base
def test_a_constructor_asking_for_something_undecodable_is_refused_at_declaration() -> None:
    """Catches a constructor no file could ever call, declared without anyone noticing.

    A named constructor is part of the file format, so every parameter of one must be
    something a written value can become. A callable or a plain object cannot be, and saying
    so while the class body runs points at the offending method instead of at the first file
    that tries to call it.
    """

    class _Callback:
        """A plain class with no ``from_dict``, so no mapping can be decoded into it."""

    with pytest.raises(ValueError, match="decoded into"):

        class _TakesACallable(ConfigBase):
            """A constructor asking for a callable, which no YAML value can be."""

            @constructor
            @classmethod
            def for_callback(cls, name: str, callback: Callable[[], None]) -> "_TakesACallable":
                """A constructor whose parameter type is under test, not its body."""
                del callback
                return cls(component_id=ComponentID(name=name))

    with pytest.raises(ValueError, match="decoded into"):

        class _TakesAPlainObject(ConfigBase):
            """A constructor asking for a class that cannot rebuild itself from a mapping."""

            @constructor
            @classmethod
            def for_callback(cls, name: str, callback: List[_Callback]) -> "_TakesAPlainObject":
                """A constructor whose parameter type is under test, not its body."""
                del callback
                return cls(component_id=ComponentID(name=name))
