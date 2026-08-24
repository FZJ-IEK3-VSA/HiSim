"""Layer 1 of the building cleanup harness: a characterization snapshot of BuildingInformation.

``hisim/components/building.py`` is about to be split and de-hazarded in a sequence of
cleanup commits that are defined to be behavior-identical (see
``roadmap/building_cleanup_spec.md``). ``BuildingInformation`` is where most of that
risk sits: roughly 690 lines that read a TABULA reference row and derive envelope areas,
U-values, conductances, capacities and apartment counts through a chain of mutating
``set_*`` methods whose order is a convention rather than a contract. This module pins the
result of that chain for **every** building code in the TABULA housing CSV, so any later
commit that moves a number -- rather than only moving code -- is caught immediately and by
name.

What is covered:

* Every ``Code_BuildingVariant`` value in the TABULA housing CSV is instantiated with a
  minimal config (TABULA code set, ``absolute_conditioned_floor_area_in_m2=None`` so the
  reference area is used, every other optional field ``None``) and all public
  scalar/list/dict attributes are compared against the golden with exact equality.
  Pandas-backed attributes (the reference row itself) and the config object are excluded;
  everything else must be encodable, so a newly added attribute of an unforeseen type
  fails loudly instead of dropping out of the net.
* Codes that currently make the class raise are pinned as ``"raises: <type>: <message>"``.
  Current breakage is recorded behavior: a later phase that accidentally fixes one of
  those crashes turns the golden red, and the fix moves into its own reviewed commit.
* Five representative codes are additionally swept under five config-override variants
  (explicit envelope U-values and areas, areas only, a scaled absolute conditioned floor
  area, a total base area instead of an absolute one, and an explicit apartment count plus
  maximum thermal demand), so the config-override branch of every ``set_*`` method and of
  the scaling/apartment helpers is inside the net as well.

The config used for the sweep is built field by field instead of through
``BuildingConfig.get_default_german_single_family_home()``. The harness must survive the
config-presets redesign that follows the cleanup and removes that factory, and per the
phase spec a harness that has to be edited mid-refactor proves nothing.

Speed: the production class re-reads the 3281-row housing CSV on every instantiation
(caching it is a phase-3 concern, and this harness must not touch production code). A
module-scoped fixture therefore memoizes ``pandas.read_csv`` for the housing path only,
for the duration of this module, which turns the full sweep from minutes into seconds.
The memoized frame is safe to share because the production code immediately takes a
``.copy()`` of the row it selects before mutating it; that was verified by generating the
golden both with and without copying and comparing the results byte for byte.

Regeneration: run the module with ``HISIM_REGENERATE_BUILDING_GOLDENS=1``, e.g.::

    HISIM_REGENERATE_BUILDING_GOLDENS=1 python -m pytest tests/test_building_characterization.py

which rewrites ``tests/goldens/building_information.json`` in full (the whole sweep is
regenerated regardless of any ``-k`` selection, so a partial run can never write a partial
golden). Without the variable a missing golden is a hard error rather than a silent
create. The golden's diff is part of the merge request, and per the phase spec the only
legal justification during the cleanup is a metadata change, never a number.
"""

# clean

import dataclasses
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas
import pytest

from hisim import utils
from hisim.config import ComponentID
from hisim.components.building import BuildingConfig, BuildingInformation
from tests import building_golden_support as golden_support


class TabulaHousingCatalogue:
    """Read-once access to the TABULA housing CSV that drives the parametrization.

    The list of building codes is needed at collection time (it is the parametrization
    argument), and the very same DataFrame is later handed to the production class through
    a memoized ``pandas.read_csv``. Both therefore go through this class, which reads the
    file at most once per process and keeps it in a class attribute rather than in module
    state.
    """

    #: Column of the housing CSV holding the TABULA building variant codes.
    BUILDING_CODE_COLUMN: str = "Code_BuildingVariant"
    #: CSV dialect of the housing file, mirroring the production read in ``building.py``.
    CSV_READ_OPTIONS: Dict[str, Any] = {
        "decimal": ",",
        "sep": ";",
        "encoding": "cp1252",
        "low_memory": False,
    }
    _cached_dataframe: Optional[pandas.DataFrame] = None

    @classmethod
    def housing_csv_path(cls) -> str:
        """Return the path of the TABULA housing CSV as the production code resolves it."""
        return str(utils.HISIMPATH["housing"])

    @classmethod
    def dataframe(cls) -> pandas.DataFrame:
        """Return the housing CSV as a DataFrame, reading it at most once per process.

        The frame is shared with the production class through the memoizing fixture below.
        That is safe because ``BuildingInformation`` selects its row with ``.loc[...]`` and
        immediately ``.copy()``s it before any of the in-place TABULA corrections happen.
        """
        if cls._cached_dataframe is None:
            cls._cached_dataframe = pandas.read_csv(cls.housing_csv_path(), **cls.CSV_READ_OPTIONS)
        housing_dataframe: pandas.DataFrame = cls._cached_dataframe
        return housing_dataframe

    @classmethod
    def building_codes(cls) -> List[str]:
        """Return every distinct, non-empty building code in the CSV, sorted.

        Sorting makes the parametrization order (and therefore the test-id order and the
        golden's key order) independent of the row order in the input file.
        """
        code_column = cls.dataframe().loc[:, cls.BUILDING_CODE_COLUMN]
        return sorted(str(code) for code in code_column.dropna().unique())


class CharacterizationConfigs:
    """The config variants that the characterization sweep instantiates.

    Two kinds of configs are built here: the minimal one used for the full catalogue sweep,
    and a set of override variants applied to a few representative codes. The override
    variants exist to reach the ``else`` branch of every ``set_*`` method of
    ``BuildingInformation`` -- the branches that take a value from the config instead of
    from the TABULA row -- plus the scaling and apartment-count helpers, none of which the
    minimal config touches.
    """

    #: Codes that additionally get the override sweep: the default single-family home, a
    #: multi-family home, an East-German apartment block, a non-German (Austrian) block,
    #: and one district variant whose TABULA reference area and window areas are zero.
    OVERRIDE_SWEEP_BUILDING_CODES: Tuple[str, ...] = (
        "DE.N.SFH.05.Gen.ReEx.001.002",
        "DE.N.MFH.05.Gen.ReEx.001.001",
        "DE.East.AB.06.Gen.ReEx.001.001",
        "AT.N.AB.01.Gen.ReEx.001.001",
        "DE.DistrictMZLerch.G.EFH.SyAv.001.011",
    )
    #: Config field overrides per variant name, applied on top of the minimal config.
    OVERRIDE_VARIANTS: Dict[str, Dict[str, Any]] = {
        "explicit_envelope_u_values_and_areas": {
            "floor_u_value_in_watt_per_m2_per_kelvin": 0.25,
            "floor_area_in_m2": 100.0,
            "facade_u_value_in_watt_per_m2_per_kelvin": 0.28,
            "facade_area_in_m2": 140.0,
            "roof_u_value_in_watt_per_m2_per_kelvin": 0.2,
            "roof_area_in_m2": 150.0,
            "window_u_value_in_watt_per_m2_per_kelvin": 1.1,
            "window_area_in_m2": 25.0,
            "door_u_value_in_watt_per_m2_per_kelvin": 1.4,
            "door_area_in_m2": 2.5,
        },
        "explicit_envelope_areas_only": {
            "floor_area_in_m2": 100.0,
            "facade_area_in_m2": 140.0,
            "roof_area_in_m2": 150.0,
            "window_area_in_m2": 25.0,
            "door_area_in_m2": 2.5,
        },
        "scaled_absolute_conditioned_floor_area": {
            "absolute_conditioned_floor_area_in_m2": 250.0,
        },
        "total_base_area_instead_of_absolute": {
            "total_base_area_in_m2": 180.0,
        },
        "explicit_apartment_count_and_max_demand": {
            "number_of_apartments": 0.0,
            "max_thermal_building_demand_in_watt": 6000.0,
        },
    }
    #: Fixed non-optional fields of the minimal config, mirroring the shape the current
    #: default factory produces (but built explicitly, so the harness outlives it).
    HEATING_REFERENCE_TEMPERATURE_IN_CELSIUS: float = -7.0
    BUILDING_HEAT_CAPACITY_CLASS: str = "medium"
    INITIAL_INTERNAL_TEMPERATURE_IN_CELSIUS: float = 22.0
    SET_HEATING_TEMPERATURE_IN_CELSIUS: float = 20.0
    SET_COOLING_TEMPERATURE_IN_CELSIUS: float = 25.0
    COMPONENT_NAME: str = "Building"

    @classmethod
    def minimal(cls, building_code: str) -> BuildingConfig:
        """Build the minimal config for one building code.

        Every optional field is ``None``, so the class takes all areas, U-values, the
        conditioned floor area, the apartment count and the maximum thermal demand from the
        TABULA row -- which is what makes the full sweep a characterization of the TABULA
        derivation chain itself rather than of a particular parameterization.
        """
        return BuildingConfig(
            component_id=ComponentID(name=cls.COMPONENT_NAME),
            building_code=building_code,
            building_heat_capacity_class=cls.BUILDING_HEAT_CAPACITY_CLASS,
            initial_internal_temperature_in_celsius=cls.INITIAL_INTERNAL_TEMPERATURE_IN_CELSIUS,
            heating_reference_temperature_in_celsius=cls.HEATING_REFERENCE_TEMPERATURE_IN_CELSIUS,
            absolute_conditioned_floor_area_in_m2=None,
            total_base_area_in_m2=None,
            number_of_apartments=None,
            max_thermal_building_demand_in_watt=None,
            floor_u_value_in_watt_per_m2_per_kelvin=None,
            floor_area_in_m2=None,
            facade_u_value_in_watt_per_m2_per_kelvin=None,
            facade_area_in_m2=None,
            roof_u_value_in_watt_per_m2_per_kelvin=None,
            roof_area_in_m2=None,
            window_u_value_in_watt_per_m2_per_kelvin=None,
            window_area_in_m2=None,
            door_u_value_in_watt_per_m2_per_kelvin=None,
            door_area_in_m2=None,
            predictive=False,
            set_heating_temperature_in_celsius=cls.SET_HEATING_TEMPERATURE_IN_CELSIUS,
            set_cooling_temperature_in_celsius=cls.SET_COOLING_TEMPERATURE_IN_CELSIUS,
            enable_opening_windows=False,
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
        )

    @classmethod
    def override(cls, building_code: str, variant_name: str) -> BuildingConfig:
        """Build the minimal config for one code with one named override variant applied."""
        return dataclasses.replace(cls.minimal(building_code), **cls.OVERRIDE_VARIANTS[variant_name])

    @classmethod
    def override_entry_keys(cls) -> List[str]:
        """Return the golden keys of the override sweep, as ``"<code>::<variant>"``.

        A single flat key per (code, variant) pair keeps the golden's override section the
        same shape as its default section, and makes each pair its own parametrized test.
        """
        return sorted(
            f"{building_code}{BuildingInformationCharacterization.OVERRIDE_KEY_SEPARATOR}{variant_name}"
            for building_code in cls.OVERRIDE_SWEEP_BUILDING_CODES
            for variant_name in cls.OVERRIDE_VARIANTS
        )


class BuildingInformationCharacterization:
    """Snapshot construction, golden layout and per-entry lookup for layer 1.

    The class holds the whole snapshot contract in one place: which attributes are excluded
    from a snapshot, how a crashing code is recorded, how the sweep is laid out inside the
    golden file, and how one entry is read back out for comparison. Keeping the layout
    knowledge next to the code that writes it is what allows the golden to stay compact --
    attribute names are stored once, per-entry values as aligned lists -- without the
    comparison losing the ability to report a difference by attribute name.
    """

    #: Bare file name of the committed golden.
    GOLDEN_FILE_NAME: str = "building_information.json"
    #: Golden section holding the attribute names shared by all value rows.
    ATTRIBUTE_NAMES_SECTION: str = "attribute_names"
    #: Golden section holding the full-catalogue sweep with the minimal config.
    DEFAULT_VARIANT_SECTION: str = "tabula_default_variants"
    #: Golden section holding the config-override sweep.
    OVERRIDE_VARIANT_SECTION: str = "config_override_variants"
    #: Separator between building code and variant name in an override key.
    OVERRIDE_KEY_SEPARATOR: str = "::"
    #: Attribute names that are never part of a snapshot. ``buildingdata_ref`` is the
    #: pandas reference row and ``buildingconfig`` is the input, not a derived result.
    EXCLUDED_ATTRIBUTE_NAMES: Tuple[str, ...] = ("buildingconfig", "buildingdata_ref")

    @classmethod
    def snapshot_of(cls, information: BuildingInformation) -> Dict[str, Any]:
        """Snapshot all public derived attributes of one ``BuildingInformation`` instance.

        Private attributes and the two excluded ones are skipped; every remaining
        attribute is encoded, so an attribute of a type the harness cannot encode raises
        rather than silently dropping out of the regression net.
        """
        snapshot: Dict[str, Any] = {}
        for attribute_name, attribute_value in vars(information).items():
            if attribute_name.startswith("_") or attribute_name in cls.EXCLUDED_ATTRIBUTE_NAMES:
                continue
            snapshot[attribute_name] = golden_support.encode_value(attribute_value)
        return snapshot

    @classmethod
    def entry_for(cls, config: BuildingConfig) -> Union[str, Dict[str, Any]]:
        """Instantiate ``BuildingInformation`` for one config and return its snapshot entry.

        A successful instantiation yields the attribute mapping; a failing one yields the
        pinned ``"raises: ..."`` string. Only ``Exception`` is caught (not
        ``BaseException``), so interrupts and memory errors still abort the sweep.
        """
        try:
            information = BuildingInformation(config=config)
        except Exception as error:  # pylint: disable=broad-except
            return golden_support.GoldenPolicy.describe_exception(error)
        return cls.snapshot_of(information)

    @classmethod
    def build_payload(cls) -> Dict[str, Any]:
        """Run the whole sweep and lay the results out as the golden payload.

        Both sections store one row per entry, aligned with the shared
        ``attribute_names`` list, which keeps the committed file at a few megabytes
        instead of the ten-plus megabytes a per-entry attribute mapping would need.
        Entries that record an exception stay plain strings.
        """
        default_entries = {
            building_code: cls.entry_for(CharacterizationConfigs.minimal(building_code))
            for building_code in TabulaHousingCatalogue.building_codes()
        }
        override_entries = {}
        for entry_key in CharacterizationConfigs.override_entry_keys():
            building_code, variant_name = entry_key.split(cls.OVERRIDE_KEY_SEPARATOR, 1)
            override_entries[entry_key] = cls.entry_for(
                CharacterizationConfigs.override(building_code, variant_name)
            )

        attribute_names = sorted(
            {
                attribute_name
                for entry in list(default_entries.values()) + list(override_entries.values())
                if isinstance(entry, dict)
                for attribute_name in entry
            }
        )
        return {
            cls.ATTRIBUTE_NAMES_SECTION: attribute_names,
            cls.DEFAULT_VARIANT_SECTION: cls.as_rows(default_entries, attribute_names),
            cls.OVERRIDE_VARIANT_SECTION: cls.as_rows(override_entries, attribute_names),
        }

    @classmethod
    def as_rows(
        cls,
        entries: Dict[str, Union[str, Dict[str, Any]]],
        attribute_names: List[str],
    ) -> Dict[str, Union[str, List[Any]]]:
        """Convert snapshot entries into the golden's aligned-row layout.

        A mapping entry becomes a list of values in ``attribute_names`` order, using the
        absent-attribute sentinel where an entry lacks an attribute that other entries
        have; a pinned exception string is stored as-is.
        """
        rows: Dict[str, Union[str, List[Any]]] = {}
        for entry_key, entry in entries.items():
            if isinstance(entry, str):
                rows[entry_key] = entry
            else:
                rows[entry_key] = [
                    entry.get(attribute_name, cls.absent_sentinel()) for attribute_name in attribute_names
                ]
        return rows

    @classmethod
    def absent_sentinel(cls) -> str:
        """Return the marker stored for an attribute that one entry does not have.

        Every code currently produces the same attribute set, so this is a safety net: if a
        later change makes an attribute conditional, the golden records its absence
        explicitly instead of silently shifting the aligned row.
        """
        return "<absent>"

    @classmethod
    def expected_entry(
        cls, golden_payload: Dict[str, Any], section_name: str, entry_key: str
    ) -> Union[str, Dict[str, Any]]:
        """Read one entry back out of the golden as a name-to-value mapping.

        Args:
            golden_payload: the parsed golden file.
            section_name: which sweep section to read from.
            entry_key: building code, or ``"<code>::<variant>"`` for the override sweep.

        Returns:
            The pinned exception string, or the attribute mapping with absent attributes
            omitted again.

        Raises:
            AssertionError: if the golden has no entry for the key. That means the sweep
                now covers an input the golden does not know about (a new TABULA code, for
                instance), which is a deliberate-regeneration decision, not a pass.
        """
        section = golden_payload[section_name]
        assert entry_key in section, (
            f"The golden '{cls.GOLDEN_FILE_NAME}' has no entry '{entry_key}' in section "
            f"'{section_name}'. If this input is new, regenerate the golden deliberately with "
            f"{golden_support.GoldenPolicy.REGENERATION_ENVIRONMENT_VARIABLE}=1."
        )
        row = section[entry_key]
        if isinstance(row, str):
            return row
        attribute_names = golden_payload[cls.ATTRIBUTE_NAMES_SECTION]
        return {
            attribute_name: value
            for attribute_name, value in zip(attribute_names, row)
            if value != cls.absent_sentinel()
        }


@pytest.fixture(name="memoized_housing_csv_read", scope="module")
def fixture_memoized_housing_csv_read():
    """Memoize the housing-CSV read for the duration of this module.

    ``BuildingInformation`` re-reads the 3281-row TABULA CSV on every instantiation, which
    would turn a 2974-code sweep into minutes of file parsing. Caching that read inside the
    production class is a phase-3 concern and this harness must not change production code,
    so the read is memoized here instead -- for the housing path only, and undone at module
    teardown so no other test module sees a patched ``pandas.read_csv``.
    """
    housing_dataframe = TabulaHousingCatalogue.dataframe()
    housing_csv_path = TabulaHousingCatalogue.housing_csv_path()
    real_read_csv = pandas.read_csv

    def memoized_read_csv(*args: Any, **kwargs: Any) -> pandas.DataFrame:
        """Return the cached housing frame for the housing path, else read normally."""
        if args and str(args[0]) == housing_csv_path:
            return housing_dataframe
        return real_read_csv(*args, **kwargs)

    patcher = pytest.MonkeyPatch()
    patcher.setattr(pandas, "read_csv", memoized_read_csv)
    yield
    patcher.undo()


@pytest.fixture(name="building_information_golden", scope="module")
def fixture_building_information_golden(memoized_housing_csv_read) -> Dict[str, Any]:
    """Provide the golden payload, regenerating the whole file first if asked to.

    Regeneration runs the complete sweep here rather than accumulating it across the
    parametrized tests, so a run narrowed with ``-k`` can never write a partial golden.
    The fixture is module-scoped, which also means the (re)write happens before the
    per-test stray-file guard from ``tests/conftest.py`` takes its snapshot.
    """
    del memoized_housing_csv_read
    if golden_support.GoldenPolicy.regeneration_requested():
        golden_support.write_golden(
            BuildingInformationCharacterization.GOLDEN_FILE_NAME,
            BuildingInformationCharacterization.build_payload(),
        )
    return golden_support.load_golden(BuildingInformationCharacterization.GOLDEN_FILE_NAME)


def assert_entry_matches_golden(
    label: str,
    expected_entry: Union[str, Dict[str, Any]],
    actual_entry: Union[str, Dict[str, Any]],
) -> None:
    """Fail with a readable, attribute-level report if a snapshot entry drifted.

    The report names the entry (the TABULA code, plus the variant for the override sweep)
    and every attribute whose value differs, because a behavior change in the derivation
    chain typically moves a group of derived values together and the group identifies the
    cause far faster than a single failed equality would.
    """
    differences = golden_support.describe_entry_differences(label, expected_entry, actual_entry)
    if differences is not None:
        raise AssertionError(
            f"{differences}\n\n"
            f"This harness pins current behavior of BuildingInformation. If the change is intended, "
            f"regenerate with {golden_support.GoldenPolicy.REGENERATION_ENVIRONMENT_VARIABLE}=1 and "
            f"justify the golden's diff in the commit message."
        )


@pytest.mark.base
@pytest.mark.parametrize("building_code", TabulaHousingCatalogue.building_codes())
def test_building_information_for_every_tabula_code(
    building_code: str,
    building_information_golden: Dict[str, Any],
    memoized_housing_csv_read: None,
) -> None:
    """Verify the derived attributes for one TABULA building code against the golden.

    One test per building code in the TABULA housing CSV, each instantiating
    ``BuildingInformation`` with the minimal config and comparing every public derived
    attribute exactly. Codes that currently raise are compared against their pinned
    ``"raises: ..."`` entry, so both working and broken behavior are held fixed.
    """
    del memoized_housing_csv_read
    expected_entry = BuildingInformationCharacterization.expected_entry(
        building_information_golden,
        BuildingInformationCharacterization.DEFAULT_VARIANT_SECTION,
        building_code,
    )
    actual_entry = BuildingInformationCharacterization.entry_for(CharacterizationConfigs.minimal(building_code))
    assert_entry_matches_golden(building_code, expected_entry, actual_entry)


@pytest.mark.base
@pytest.mark.parametrize("entry_key", CharacterizationConfigs.override_entry_keys())
def test_building_information_for_config_override_variants(
    entry_key: str,
    building_information_golden: Dict[str, Any],
    memoized_housing_csv_read: None,
) -> None:
    """Verify one (building code, config-override variant) pair against the golden.

    These variants reach the config branches the full-catalogue sweep never touches: the
    explicit envelope U-values and areas of all five ``set_*`` element methods, the two
    floor-area scaling paths, and the explicit apartment count and maximum thermal demand.
    """
    del memoized_housing_csv_read
    building_code, variant_name = entry_key.split(BuildingInformationCharacterization.OVERRIDE_KEY_SEPARATOR, 1)
    expected_entry = BuildingInformationCharacterization.expected_entry(
        building_information_golden,
        BuildingInformationCharacterization.OVERRIDE_VARIANT_SECTION,
        entry_key,
    )
    actual_entry = BuildingInformationCharacterization.entry_for(
        CharacterizationConfigs.override(building_code, variant_name)
    )
    assert_entry_matches_golden(entry_key, expected_entry, actual_entry)


@pytest.mark.base
def test_characterization_sweep_covers_the_whole_tabula_catalogue(
    building_information_golden: Dict[str, Any],
    memoized_housing_csv_read: None,
) -> None:
    """Verify the golden covers exactly the catalogue and override sweep, and nothing else.

    The per-code tests would silently stop guarding a code that disappeared from the golden
    only if it also disappeared from the CSV; and a golden holding stale entries for codes
    that no longer exist would rot unnoticed. This test pins the key sets themselves, so
    the sweep's extent is as much a part of the reference as the values are.
    """
    del memoized_housing_csv_read
    assert set(building_information_golden[BuildingInformationCharacterization.DEFAULT_VARIANT_SECTION]) == set(
        TabulaHousingCatalogue.building_codes()
    ), "The golden's default-variant section no longer matches the TABULA catalogue."
    assert set(building_information_golden[BuildingInformationCharacterization.OVERRIDE_VARIANT_SECTION]) == set(
        CharacterizationConfigs.override_entry_keys()
    ), "The golden's override section no longer matches the configured override sweep."
