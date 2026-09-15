"""Which component of a recorded energy-system file owns which field of the home inventory.

A *binding* answers one question: the translation layer has decided that
``building_config.envelope_details.roof_u_value_in_watt_per_m2_per_kelvin`` should become 0.16 —
where in the base file does that number go? Decision C3 makes the answer short: a contract field
name equals the HiSim config field name, so a binding names a *component*, never a renaming::

    Bindings.resolve("building_config.envelope_details.roof_u_value_in_watt_per_m2_per_kelvin")
    # LeafBinding(kind=CONFIG_OVERRIDE, targets=(Target("Building", "roof_u_value_...", None),))

There are no value maps here and there must never be any (decision C3): a value that needs
re-spelling is re-spelled once, in :class:`hisim.renovisor.catalogue.CatalogueSpellings`, on the
way in. What this module does carry is *field-name* exceptions, and only where a HiSim field or a
contract field still breaks the C3 convention; :class:`PendingRenames` lists every one of them so
that the contract PR of step 6 and HiSim's own P4 renaming batch can empty the list.

The table has two levels, which is what keeps it readable at 80-odd contract leaves:

* :attr:`Bindings.BLOCKS` — one rule per inventory block. "Everything under
  ``building_config.envelope_details`` is a config field of the component recorded as ``Building``."
* :attr:`Bindings.LEAVES` — the exceptions, one per leaf: a leaf that reaches a constructor
  argument rather than a config field, a leaf that reaches two components at once, a leaf whose
  HiSim name differs, and every leaf that reaches no component at all.

A leaf rule always wins over the block rule that would otherwise cover it, and every contract leaf
must be covered by one of the two, so that "nobody thought about this field" is a failing test
rather than a silent drop (requirement R7, acceptance criterion A1).
"""

from dataclasses import dataclass, replace
from enum import Enum
from typing import ClassVar, Dict, Optional, Tuple


class BindingError(Exception):
    """Raised when an inventory path is covered by neither a leaf rule nor a block rule.

    Carrying its own type rather than a ``KeyError`` matters because the answer is always the
    same: the path is real and the bindings table has not been told what to do with it, so the fix
    is a table entry rather than a caller change.

    Args:
        path: The dotted inventory path nothing covers.
    """

    def __init__(self, path: str) -> None:
        """Store the offending path and build the message that names the fix."""
        super().__init__(
            f"no binding covers the inventory path '{path}'; add a LeafBinding or a BlockBinding "
            "to hisim.renovisor.bindings.Bindings"
        )
        self.path = path


class BindingKind(str, Enum):
    """What kind of destination a binding names.

    ``CONFIG_OVERRIDE`` — the value is written into a component's ``config`` block under the same
    field name. ``CONSTRUCTOR_ARGUMENT`` — the value is an argument of one of the component's
    named constructors (a TABULA code is assembled from three inventory fields, so no single
    config field carries it). ``NON_SIMULATION`` — a legitimate input that no simulated component
    reads, because it belongs to the cost, scheduling or grant layer, or because the translation
    layer itself consumes it in a check or a sizing law rather than writing it anywhere.
    ``NO_CONSUMER`` — nothing at all reads it, and the translation report says ``ignored``.
    """

    CONFIG_OVERRIDE = "CONFIG_OVERRIDE"
    CONSTRUCTOR_ARGUMENT = "CONSTRUCTOR_ARGUMENT"
    NON_SIMULATION = "NON_SIMULATION"
    NO_CONSUMER = "NO_CONSUMER"


class GeneratorKey(str, Enum):
    """The sentinel component key standing for "whichever heat generator this file runs".

    Every base file wires exactly one heat generator, but each spells it differently:
    ``CondensingGasBoiler`` in the gas file, ``MoreAdvancedHeatPumpHPLib`` in the heat-pump one,
    ``DistrictHeating`` in the district-heating one. A binding that must reach "the generator"
    without naming eleven keys uses this sentinel, and the check resolves it per file by class.

    :attr:`GeneratorComponents.CLASS_PATHS` is the list it resolves against.
    """

    SELECTED = "<generator>"


class GeneratorComponents:
    """How the heat generator of a base file is recognised, and what its power field is called.

    The generator is found by its component class, not by its module: ``generic_boiler`` also
    holds ``GenericBoilerController`` and ``more_advanced_heat_pump_hplib`` two controllers, so a
    module-level rule would match three components in one file instead of one.

    :attr:`POWER_FIELD_BY_CLASS_PATH` exists because the four generator classes spell their rated
    power four different ways, which is exactly the kind of thing the contract's single
    ``heating_system.power_in_watt`` field hides. It is a *field-name* table, not a value map: no
    value is rewritten anywhere in this module (decision C3).
    """

    #: The component classes that count as a heat generator, one of which every base file has.
    CLASS_PATHS: ClassVar[Tuple[str, ...]] = (
        "hisim.components.generic_boiler.GenericBoiler",
        "hisim.components.more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib",
        "hisim.components.generic_electric_heating.ElectricHeating",
        "hisim.components.generic_district_heating.DistrictHeating",
    )

    #: Generator class path -> the config field carrying its rated power.
    POWER_FIELD_BY_CLASS_PATH: ClassVar[Dict[str, str]] = {
        "hisim.components.generic_boiler.GenericBoiler": "maximal_thermal_power_in_watt",
        "hisim.components.more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib": (
            "set_thermal_output_power_in_watt"
        ),
        "hisim.components.generic_electric_heating.ElectricHeating": "maximum_electric_power_w",
        "hisim.components.generic_district_heating.DistrictHeating": "connected_load_in_w",
    }

    #: The config field carrying "this generator also makes the domestic hot water". Three of the
    #: four generator classes have it; ``GenericBoiler`` has none, which is why the boiler files
    #: express the same thing through their recorded wiring instead.
    DHW_PREPARATION_FIELD: ClassVar[str] = "with_domestic_hot_water_preparation"

    @classmethod
    def is_generator(cls, class_path: str) -> bool:
        """Return whether a component's class path names one of the heat generator classes.

        Args:
            class_path: The dotted class path an energy-system entry writes under ``class``.

        Returns:
            ``True`` for exactly the four generator classes.
        """
        return class_path in cls.CLASS_PATHS


@dataclass(frozen=True)
class Target:
    """One place in a base file that an inventory leaf reaches.

    Args:
        component_key: The component's recorded key in the energy-system file, e.g. ``"Building"``,
            or :attr:`GeneratorKey.SELECTED` for the file's heat generator.
        field_or_argument: The HiSim name of the config field, or of the constructor argument when
            *constructor* is set.
        constructor: The named constructor the argument belongs to, e.g. ``"for_tabula_code"``.
            ``None`` means the target is a plain ``config`` field.
    """

    component_key: str
    field_or_argument: str
    constructor: Optional[str] = None

    def describe(self) -> str:
        """Return the target in one readable phrase, e.g. ``Building.for_tabula_code(building_code)``."""
        if self.constructor is None:
            return f"{self.component_key}.{self.field_or_argument}"
        return f"{self.component_key}.{self.constructor}({self.field_or_argument})"


@dataclass(frozen=True)
class BlockBinding:
    """The default rule for every leaf under one inventory block.

    Example: ``BlockBinding("building_config.envelope_details", "Building")`` says that
    ``building_config.envelope_details.roof_area_in_m2`` is the ``roof_area_in_m2`` config field of
    the component recorded as ``Building`` — and so is every other leaf of that block, which is
    what makes the table short enough to read.

    Args:
        inventory_block: The dotted prefix the rule covers.
        component_key: The recorded component key the block's leaves land on, or
            :attr:`GeneratorKey.SELECTED`.
        requires_variant: ``(variant name, option name)`` when the component exists only inside one
            option of one variant, e.g. ``("electricity_management", "ems_with_battery")`` for the
            battery. ``None`` when the component is a plain top-level one.
    """

    inventory_block: str
    component_key: str
    requires_variant: Optional[Tuple[str, str]] = None


@dataclass(frozen=True)
class LeafBinding:
    """One leaf that does not follow its block's rule, and where it goes instead.

    Args:
        inventory_path: The dotted path of the leaf. Three spellings are accepted: an exact leaf
            path; a block prefix, which covers every leaf under it (``condition_assessment``); and
            a wildcard ``*.<name>``, which covers a leaf of that name under any block
            (``*.installation_year``).
        kind: What kind of destination this is.
        targets: Every place in a base file the leaf reaches. Empty for the kinds that reach none,
            and for the handful of leaves that choose the base *file* rather than a field in it.
        note: Why this leaf is an exception to its block's rule, in one phrase, for the reader of
            the table and for the translation map.
    """

    inventory_path: str
    kind: BindingKind
    targets: Tuple[Target, ...]
    note: str

    def is_wildcard(self) -> bool:
        """Return whether this rule matches a leaf name under any block."""
        return self.inventory_path.startswith(PathMatching.WILDCARD_PREFIX)

    def wildcard_leaf(self) -> str:
        """Return the leaf name a wildcard rule matches, e.g. ``installation_year``."""
        return self.inventory_path[len(PathMatching.WILDCARD_PREFIX):]


@dataclass(frozen=True)
class PendingRename:
    """One field name that decision C3 has not reached yet, on either side of the boundary.

    Example: the contract calls the conditioned floor area ``conditioned_floor_area_m2`` while
    HiSim calls it ``absolute_conditioned_floor_area_in_m2``. Under C3 exactly one of the two names
    survives, and until the rename lands the binding carries both.

    Args:
        contract_path: The inventory path as the contract spells it today.
        hisim_name: The HiSim config field or constructor argument it reaches today.
        owner: Who performs the rename — ``"contract PR"`` for a contract-side fix, ``"P4"`` for a
            HiSim-side one.
        note: What is wrong with the current spelling, in one phrase.
    """

    contract_path: str
    hisim_name: str
    owner: str
    note: str


class PathMatching:
    """How a dotted inventory path is compared against the rules of the table.

    Two details make this more than a string comparison. A path that reaches into an array carries
    an index — ``energy_system_config.vehicles.electric_vehicles[0].model`` — and the rules never
    do, so indices are stripped before matching. And a rule matches a path only at a segment
    boundary, so that ``energy_system_config.water_storage`` never accidentally covers a block
    called ``energy_system_config.water_storage_pump``.
    """

    #: The separator between path segments.
    SEPARATOR: ClassVar[str] = "."

    #: The prefix marking a rule that matches a leaf name under any block.
    WILDCARD_PREFIX: ClassVar[str] = "*."

    #: The character opening an array index inside a segment.
    INDEX_OPEN: ClassVar[str] = "["

    @classmethod
    def segments(cls, path: str) -> Tuple[str, ...]:
        """Return the path's segments with any array index stripped from each.

        Args:
            path: A dotted path, possibly carrying ``[0]`` or ``[]`` on some segments.

        Returns:
            The segments, e.g. ``("energy_system_config", "vehicles", "electric_vehicles", "model")``.
        """
        cleaned = []
        for segment in path.split(cls.SEPARATOR):
            head = segment.split(cls.INDEX_OPEN, 1)[0]
            if head:
                cleaned.append(head)
        return tuple(cleaned)

    @classmethod
    def covers(cls, rule_path: str, path: str) -> bool:
        """Return whether *rule_path* is *path* itself or a block prefix of it.

        Args:
            rule_path: The rule's dotted path.
            path: The dotted inventory path being resolved.

        Returns:
            ``True`` when every segment of *rule_path* is a leading segment of *path*.
        """
        rule_segments = cls.segments(rule_path)
        path_segments = cls.segments(path)
        return len(rule_segments) <= len(path_segments) and path_segments[: len(rule_segments)] == rule_segments


class Bindings:
    """The table saying where every home-inventory leaf lands in a recorded energy-system file.

    Example::

        Bindings.resolve("occupancy_config.residents_count").targets
        # (Target("UTSPConnector", "household", "for_household"),)
        Bindings.resolve("location.region").kind
        # BindingKind.NO_CONSUMER

    The table is checked, not trusted: ``tests/test_renovisor_bindings.py`` loads all eleven
    recorded base files, resolves every target against the component's real configuration class,
    and walks every leaf of the contract's ``HomeInventoryInput`` schema to assert that this table
    has a fate for each of them.
    """

    BLOCKS: ClassVar[Tuple[BlockBinding, ...]] = (
        BlockBinding("building_config.envelope_details", "Building"),
        BlockBinding("building_config.general", "Building"),
        BlockBinding("energy_system_config.photovoltaics", "PVSystem"),
        BlockBinding(
            "energy_system_config.battery_storage", "Battery", ("electricity_management", "ems_with_battery")
        ),
        BlockBinding("energy_system_config.water_storage.hot_water_storage", "SimpleHotWaterStorage"),
        BlockBinding("energy_system_config.water_storage.domestic_hot_water_storage", "DHWStorage"),
        BlockBinding("energy_system_config.solar_thermal_system", "SolarThermalSystem"),
        BlockBinding("energy_system_config.heating_system", GeneratorKey.SELECTED.value),
        BlockBinding("occupancy_config", "UTSPConnector"),
        BlockBinding("location", "Weather"),
    )

    #: The recorded key of the heat generator in each base file, for the leaves whose HiSim field
    #: name depends on which generator class the file runs. Listing the keys rather than using
    #: :attr:`GeneratorKey.SELECTED` is what lets one leaf name four different fields; the check
    #: accepts that only some of these keys exist in any one file.
    BOILER_KEYS: ClassVar[Tuple[str, ...]] = (
        "CondensingGasBoiler",
        "ConventionalOilBoiler",
        "ConventionalPelletBoiler",
        "ConventionalWoodChipBoiler",
        "CondensingHydrogenBoiler",
    )

    LEAVES: ClassVar[Tuple[LeafBinding, ...]] = (
        # ---------------------------------------------------------------- constructor arguments
        LeafBinding(
            "building_config.general.tabula_building_type",
            BindingKind.CONSTRUCTOR_ARGUMENT,
            (Target("Building", "building_code", "for_tabula_code"),),
            "one of the three parts of the TABULA code, which no single config field carries",
        ),
        LeafBinding(
            "building_config.general.construction_year",
            BindingKind.CONSTRUCTOR_ARGUMENT,
            (Target("Building", "building_code", "for_tabula_code"),),
            "picks the TABULA age band of the code",
        ),
        LeafBinding(
            "building_config.general.retrofit_status",
            BindingKind.CONSTRUCTOR_ARGUMENT,
            (Target("Building", "building_code", "for_tabula_code"),),
            "picks the TABULA variant .001 / .002 / .003 of the code",
        ),
        LeafBinding(
            "building_config.general.conditioned_floor_area_m2",
            BindingKind.CONSTRUCTOR_ARGUMENT,
            (Target("Building", "absolute_conditioned_floor_area_in_m2", "for_tabula_code"),),
            "the contract name predates C3; the contract PR renames it (PendingRenames)",
        ),
        LeafBinding(
            "location.country_code",
            BindingKind.CONSTRUCTOR_ARGUMENT,
            (Target("Weather", "location", "for_location"), Target("PVSystem", "location", None)),
            "picks the weather station and, separately, the photovoltaic model's location field",
        ),
        LeafBinding(
            "occupancy_config.residents_count",
            BindingKind.CONSTRUCTOR_ARGUMENT,
            (Target("UTSPConnector", "household", "for_household"),),
            "with residents_type and employment: the nearest of the LPG catalogue households (A3)",
        ),
        LeafBinding(
            "occupancy_config.residents_type",
            BindingKind.CONSTRUCTOR_ARGUMENT,
            (Target("UTSPConnector", "household", "for_household"),),
            "with residents_count and employment: the nearest of the LPG catalogue households (A3)",
        ),
        LeafBinding(
            "occupancy_config.residents_employment_status",
            BindingKind.CONSTRUCTOR_ARGUMENT,
            (Target("UTSPConnector", "household", "for_household"),),
            "with residents_count and type: the nearest of the LPG catalogue households (A3)",
        ),
        LeafBinding(
            "occupancy_config.travel_route_set",
            BindingKind.CONSTRUCTOR_ARGUMENT,
            (Target("UTSPConnector", "travel_route_set", "for_household"),),
            "an LPG reference the occupancy constructor takes beside the household",
        ),
        # ---------------------------------------------------------------- two-target overrides
        LeafBinding(
            "building_config.general.set_heating_temperature_in_celsius",
            BindingKind.CONFIG_OVERRIDE,
            (
                Target("Building", "set_heating_temperature_in_celsius", None),
                Target("HeatDistributionController", "set_heating_temperature_for_building_in_celsius", None),
            ),
            "one inventory field, two HiSim fields: the building and its heating controller (M11)",
        ),
        LeafBinding(
            "building_config.general.set_cooling_temperature_in_celsius",
            BindingKind.CONFIG_OVERRIDE,
            (
                Target("Building", "set_cooling_temperature_in_celsius", None),
                Target("HeatDistributionController", "set_cooling_temperature_for_building_in_celsius", None),
            ),
            "one inventory field, two HiSim fields: the building and its heating controller (M11)",
        ),
        # ---------------------------------------------------------------- the heating system
        LeafBinding(
            "energy_system_config.heating_system.system",
            BindingKind.CONSTRUCTOR_ARGUMENT,
            (),
            "selects the base file itself (base_files.py); no component in the file carries it",
        ),
        LeafBinding(
            "energy_system_config.heating_system.dhw_supply",
            BindingKind.CONSTRUCTOR_ARGUMENT,
            (),
            "selects the base file itself; the recorded wiring is what makes hot water (Q14)",
        ),
        LeafBinding(
            "energy_system_config.heating_system.heat_distribution_system",
            BindingKind.CONFIG_OVERRIDE,
            (Target("HeatDistributionController", "heating_system", None),),
            "the emitter lives on the heating controller, not on the generator",
        ),
        LeafBinding(
            "energy_system_config.heating_system.heatpump_flow_temperature_in_celsius",
            BindingKind.CONFIG_OVERRIDE,
            (Target("MoreAdvancedHeatPumpHPLib", "flow_temperature_in_celsius", None),),
            "only the three heat-pump files have a generator with a flow temperature",
        ),
        LeafBinding(
            "energy_system_config.heating_system.power_in_watt",
            BindingKind.CONFIG_OVERRIDE,
            (
                Target("CondensingGasBoiler", "maximal_thermal_power_in_watt", None),
                Target("ConventionalOilBoiler", "maximal_thermal_power_in_watt", None),
                Target("ConventionalPelletBoiler", "maximal_thermal_power_in_watt", None),
                Target("ConventionalWoodChipBoiler", "maximal_thermal_power_in_watt", None),
                Target("CondensingHydrogenBoiler", "maximal_thermal_power_in_watt", None),
                Target("MoreAdvancedHeatPumpHPLib", "set_thermal_output_power_in_watt", None),
                Target("ElectricHeating", "maximum_electric_power_w", None),
                Target("DistrictHeating", "connected_load_in_w", None),
            ),
            "the four generator classes spell their rated power four ways; P4 unifies them",
        ),
        LeafBinding(
            "energy_system_config.heating_system.with_dhw_preparation",
            BindingKind.CONFIG_OVERRIDE,
            (
                Target("MoreAdvancedHeatPumpHPLib", "with_domestic_hot_water_preparation", None),
                Target("ElectricHeating", "with_domestic_hot_water_preparation", None),
                Target("DistrictHeating", "with_domestic_hot_water_preparation", None),
            ),
            "the boiler class has no such flag; the boiler files express it in their wiring",
        ),
        # ---------------------------------------------------------------- renamed config fields
        LeafBinding(
            "energy_system_config.water_storage.hot_water_storage.volume_in_liters",
            BindingKind.CONFIG_OVERRIDE,
            (Target("SimpleHotWaterStorage", "volume_heating_water_storage_in_liter", None),),
            "the two names differ in number and in unit spelling (PendingRenames)",
        ),
        LeafBinding(
            "energy_system_config.water_storage.domestic_hot_water_storage.volume_in_liters",
            BindingKind.CONFIG_OVERRIDE,
            (Target("DHWStorage", "volume_heating_water_storage_in_liter", None),),
            "the two names differ in number and in unit spelling (PendingRenames)",
        ),
        LeafBinding(
            "energy_system_config.photovoltaics.azimuth_in_degree",
            BindingKind.CONFIG_OVERRIDE,
            (Target("PVSystem", "azimuth", None),),
            "the HiSim field omits its unit; P4 renames it (PendingRenames)",
        ),
        LeafBinding(
            "energy_system_config.photovoltaics.tilt_in_degree",
            BindingKind.CONFIG_OVERRIDE,
            (Target("PVSystem", "tilt", None),),
            "the HiSim field omits its unit; P4 renames it (PendingRenames)",
        ),
        LeafBinding(
            "energy_system_config.battery_storage.capacity_in_kwh",
            BindingKind.CONFIG_OVERRIDE,
            (Target("Battery", "custom_battery_capacity_generic_in_kilowatt_hour", None),),
            "the HiSim field is named after its override mechanism; P4 renames it (PendingRenames)",
        ),
        LeafBinding(
            "energy_system_config.solar_thermal_system.collector_area_in_m2",
            BindingKind.CONFIG_OVERRIDE,
            (Target("SolarThermalSystem", "area_m2", None),),
            "the HiSim field breaks HiSim's own unit convention; P4 renames it (PendingRenames)",
        ),
        LeafBinding(
            "energy_system_config.solar_thermal_system.azimuth_in_degree",
            BindingKind.CONFIG_OVERRIDE,
            (Target("SolarThermalSystem", "azimuth", None),),
            "the HiSim field omits its unit; P4 renames it (PendingRenames)",
        ),
        LeafBinding(
            "energy_system_config.solar_thermal_system.tilt_in_degree",
            BindingKind.CONFIG_OVERRIDE,
            (Target("SolarThermalSystem", "tilt", None),),
            "the HiSim field omits its unit; P4 renames it (PendingRenames)",
        ),
        LeafBinding(
            "energy_system_config.solar_thermal_system.used_for_space_heating",
            BindingKind.CONSTRUCTOR_ARGUMENT,
            (),
            "selects the base file itself: what a collector feeds is recorded wiring, not a field",
        ),
        LeafBinding(
            "energy_system_config.solar_thermal_system.used_for_dhw",
            BindingKind.CONSTRUCTOR_ARGUMENT,
            (),
            "selects the base file itself: what a collector feeds is recorded wiring, not a field",
        ),
        # ---------------------------------------------------------------- non-simulation inputs
        LeafBinding(
            "*.installation_year",
            BindingKind.NON_SIMULATION,
            (),
            "an age, which drives the condition assessment and the cost layer, not the physics",
        ),
        LeafBinding(
            "building_config.general.floor_construction",
            BindingKind.NON_SIMULATION,
            (),
            "read by the fit-to-building check (Q3); no component carries the construction type",
        ),
        LeafBinding(
            "building_config.general.wall_construction",
            BindingKind.NON_SIMULATION,
            (),
            "read by the fit-to-building check (Q3); no component carries the construction type",
        ),
        LeafBinding(
            "building_config.general.roof_form",
            BindingKind.NON_SIMULATION,
            (),
            "an input of the rooftop photovoltaic sizing law (Q16), not a config field of its own",
        ),
        LeafBinding(
            "building_config.general.roof_orientation_in_degree",
            BindingKind.NON_SIMULATION,
            (),
            "an input of the rooftop photovoltaic sizing law (Q16), not a config field of its own",
        ),
        LeafBinding(
            "energy_system_config.vehicles",
            BindingKind.NON_SIMULATION,
            (),
            "the vehicle count selects the base file (Q18); the per-vehicle fields are step 5",
        ),
        LeafBinding(
            "condition_assessment",
            BindingKind.NON_SIMULATION,
            (),
            "the scheduling layer reads it; nothing in a simulation does",
        ),
        LeafBinding(
            "selected_grant_schemes",
            BindingKind.NON_SIMULATION,
            (),
            "the subsidy layer reads it (Q24); nothing in a simulation does",
        ),
        # ---------------------------------------------------------------- nothing consumes these
        LeafBinding(
            "location.region",
            BindingKind.NO_CONSUMER,
            (),
            "one weather station per country, so a region cannot change the weather",
        ),
        LeafBinding(
            "location.eircode_or_postcode",
            BindingKind.NO_CONSUMER,
            (),
            "no HiSim input is addressed more finely than the weather station",
        ),
        LeafBinding(
            "energy_system_config.photovoltaics.remaining_performance_in_percent",
            BindingKind.NO_CONSUMER,
            (),
            "HiSim has no photovoltaic degradation model",
        ),
        LeafBinding(
            "energy_system_config.battery_storage.power_in_watt",
            BindingKind.NO_CONSUMER,
            (),
            "the battery component has an inverter power, which is not the same number",
        ),
        LeafBinding(
            "energy_system_config.battery_storage.state_of_health_in_percent",
            BindingKind.NO_CONSUMER,
            (),
            "HiSim has no capacity-fade model",
        ),
        LeafBinding(
            "energy_system_config.ventilation_system.system",
            BindingKind.NO_CONSUMER,
            (),
            "HiSim has no air-change or heat-recovery input for the MVP (Q15)",
        ),
    )

    #: Every field name that decision C3 has not reached yet. Step 6 empties this table; until
    #: then it is the list the contract PR and the next P4 renaming batch work from.
    PENDING_RENAMES: ClassVar[Tuple[PendingRename, ...]] = (
        PendingRename(
            "building_config.general.conditioned_floor_area_m2",
            "absolute_conditioned_floor_area_in_m2",
            "contract PR",
            "the contract name has no unit suffix of HiSim's form and drops 'absolute'",
        ),
        PendingRename(
            "energy_system_config.water_storage.hot_water_storage.volume_in_liters",
            "volume_heating_water_storage_in_liter",
            "contract PR",
            "HiSim spells the unit singular and names the storage it belongs to",
        ),
        PendingRename(
            "energy_system_config.water_storage.domestic_hot_water_storage.volume_in_liters",
            "volume_heating_water_storage_in_liter",
            "contract PR",
            "HiSim spells the unit singular and names the storage it belongs to",
        ),
        PendingRename(
            "energy_system_config.photovoltaics.azimuth_in_degree",
            "azimuth",
            "P4",
            "the HiSim field carries no unit, which HiSim's own convention requires",
        ),
        PendingRename(
            "energy_system_config.photovoltaics.tilt_in_degree",
            "tilt",
            "P4",
            "the HiSim field carries no unit, which HiSim's own convention requires",
        ),
        PendingRename(
            "energy_system_config.battery_storage.capacity_in_kwh",
            "custom_battery_capacity_generic_in_kilowatt_hour",
            "P4",
            "the HiSim field is named after the override mechanism rather than the quantity",
        ),
        PendingRename(
            "energy_system_config.solar_thermal_system.collector_area_in_m2",
            "area_m2",
            "P4",
            "the HiSim field breaks HiSim's own unit convention (_in_m2)",
        ),
        PendingRename(
            "energy_system_config.solar_thermal_system.azimuth_in_degree",
            "azimuth",
            "P4",
            "the HiSim field carries no unit, which HiSim's own convention requires",
        ),
        PendingRename(
            "energy_system_config.solar_thermal_system.tilt_in_degree",
            "tilt",
            "P4",
            "the HiSim field carries no unit, which HiSim's own convention requires",
        ),
        PendingRename(
            "energy_system_config.heating_system.power_in_watt",
            "maximal_thermal_power_in_watt / set_thermal_output_power_in_watt / "
            "maximum_electric_power_w / connected_load_in_w",
            "P4",
            "the four generator classes spell one quantity four ways, two of them without a unit",
        ),
        PendingRename(
            "energy_system_config.heating_system.with_dhw_preparation",
            "with_domestic_hot_water_preparation",
            "contract PR",
            "the contract abbreviates what HiSim spells out",
        ),
    )

    @classmethod
    def resolve(cls, inventory_path: str) -> LeafBinding:
        """Return the binding that covers one inventory path.

        A leaf rule always wins over a block rule, and the most specific rule of a kind wins over a
        less specific one, so ``energy_system_config.battery_storage.installation_year`` resolves
        to the ``*.installation_year`` rule and not to the battery block.

        Args:
            inventory_path: The dotted path, with or without array indices.

        Returns:
            A :class:`LeafBinding`. When a block rule produced it, its
            :attr:`LeafBinding.inventory_path` is the queried path, its targets are the block's
            component with the path's last segment as the field name, and its note says which
            block it came from.

        Raises:
            BindingError: When neither a leaf rule nor a block rule covers the path.
        """
        exact = cls._exact_leaf(inventory_path)
        if exact is not None:
            return exact
        prefix = cls._prefix_leaf(inventory_path)
        if prefix is not None:
            return replace(prefix, inventory_path=inventory_path)
        wildcard = cls._wildcard_leaf(inventory_path)
        if wildcard is not None:
            return replace(wildcard, inventory_path=inventory_path)
        block = cls.block_for(inventory_path)
        if block is not None:
            return cls._from_block(block, inventory_path)
        raise BindingError(inventory_path)

    @classmethod
    def block_for(cls, inventory_path: str) -> Optional[BlockBinding]:
        """Return the most specific block rule covering a path, or ``None``.

        Args:
            inventory_path: The dotted path.

        Returns:
            The block whose prefix is the longest one matching, so that
            ``energy_system_config.water_storage.hot_water_storage`` beats a shorter block that
            also matched.
        """
        candidates = [
            block
            for block in cls.BLOCKS
            if PathMatching.covers(block.inventory_block, inventory_path)
            and len(PathMatching.segments(block.inventory_block)) < len(PathMatching.segments(inventory_path))
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda block: len(PathMatching.segments(block.inventory_block)))

    @classmethod
    def leaf_paths(cls) -> Tuple[str, ...]:
        """Return the path of every leaf rule, in table order, for the map and for the tests."""
        return tuple(leaf.inventory_path for leaf in cls.LEAVES)

    @classmethod
    def component_keys(cls) -> Tuple[str, ...]:
        """Return every recorded component key the table names, sorted.

        Includes the keys reached through a leaf rule and the keys a block rule lands on, so that
        the check has one list to look for in every base file.
        """
        keys = {block.component_key for block in cls.BLOCKS}
        for leaf in cls.LEAVES:
            keys.update(target.component_key for target in leaf.targets)
        return tuple(sorted(keys))

    @classmethod
    def generator_component_keys(cls) -> Tuple[str, ...]:
        """Return the component keys that name one particular generator, sorted.

        These are the keys that exist in one or three of the eleven base files by design, because
        each names a generator class rather than a role: a check asking "which files lack a
        component some rule binds to" has to leave them out or every file answers with ten of
        them.
        """
        keys = set(cls.BOILER_KEYS)
        keys.update(
            {
                "MoreAdvancedHeatPumpHPLib",
                "ElectricHeating",
                "DistrictHeating",
                GeneratorKey.SELECTED.value,
            }
        )
        return tuple(sorted(keys))

    @classmethod
    def role_component_keys(cls) -> Tuple[str, ...]:
        """Return every component key that names a role rather than one generator class, sorted.

        The role keys are the ones a base file is expected to carry whatever heats it — the
        building, the weather, the occupancy, the storages, the photovoltaic array. Which files
        nevertheless lack one is a fact worth asserting, so this is what the check iterates.
        """
        return tuple(key for key in cls.component_keys() if key not in cls.generator_component_keys())

    @classmethod
    def _exact_leaf(cls, inventory_path: str) -> Optional[LeafBinding]:
        """Return the leaf rule whose path equals *inventory_path*, or ``None``."""
        segments = PathMatching.segments(inventory_path)
        for leaf in cls.LEAVES:
            if leaf.is_wildcard():
                continue
            if PathMatching.segments(leaf.inventory_path) == segments:
                return leaf
        return None

    @classmethod
    def _prefix_leaf(cls, inventory_path: str) -> Optional[LeafBinding]:
        """Return the most specific leaf rule that is a block prefix of *inventory_path*."""
        candidates = [
            leaf
            for leaf in cls.LEAVES
            if not leaf.is_wildcard() and PathMatching.covers(leaf.inventory_path, inventory_path)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda leaf: len(PathMatching.segments(leaf.inventory_path)))

    @classmethod
    def _wildcard_leaf(cls, inventory_path: str) -> Optional[LeafBinding]:
        """Return the wildcard rule matching the path's last segment, or ``None``."""
        segments = PathMatching.segments(inventory_path)
        if not segments:
            return None
        for leaf in cls.LEAVES:
            if leaf.is_wildcard() and leaf.wildcard_leaf() == segments[-1]:
                return leaf
        return None

    @classmethod
    def _from_block(cls, block: BlockBinding, inventory_path: str) -> LeafBinding:
        """Return the binding a block rule implies for one of its leaves."""
        field = PathMatching.segments(inventory_path)[-1]
        return LeafBinding(
            inventory_path=inventory_path,
            kind=BindingKind.CONFIG_OVERRIDE,
            targets=(Target(block.component_key, field, None),),
            note=f"the block rule for '{block.inventory_block}'",
        )
