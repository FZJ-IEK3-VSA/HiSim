"""Turning one renovated house into one runnable energy-system file, and saying what landed where.

``translate`` is pure: a house in, a file and a report out, and no input beyond one base file on
disk. That is what makes it testable exhaustively and what lets the capability document run
hundreds of probes in a second.

It picks the base file from the renovated generator (:class:`BaseFiles`), swaps the three
components that are parameterised by an identifier rather than by a variant onto their named
constructors, writes the request's own numbers into the config blocks the bindings name, selects
the electricity-management variant, dumps the file with the format's own canonical emitter and
loads the dumped text back as a structural self-check.

Decision D-D is why there are no translator-owned base files: the recorded grouped twins in
``energy_systems/`` are the base files, as they stand. What they lack -- a night-setback group,
an air conditioner, an EMS without a battery, solar thermal on a third generator, a car -- is
``not_implemented_yet`` with a note, and base-file work starts after the MVP runs. "No
photovoltaics" is therefore not a missing group but a zero-power pin on the array every twin
carries, which is verified to run and produce nothing.

What may be written is deliberately narrow: configuration values, the arguments of a named
constructor, and the selection of a variant. Authoring an ``inputs`` item, a ``sizing_sources``
block or a component entry is forbidden, because those are the reviewed content of the base file
and a library that writes them has quietly become a second, untested system description.
:class:`DiffRule` is that rule as a check rather than as a promise, and it runs on every
translation.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim.components.weather.config import LocationEnum
from hisim.energy_system.emitter import EnergySystemEmitter
from hisim.energy_system.loader import dump_energy_system, load_energy_system
from hisim.energy_system.model import (
    ComponentEntry,
    ConstructorCall,
    EnergySystemFile,
    Group,
    Variant,
    VariantOption,
)
from hisim.renovisor import TRANSLATOR_VERSION
from hisim.renovisor.apply import AppliedPackage
from hisim.renovisor.economics import EconomicContextBuilder
from hisim.renovisor.constants import (
    BatteryLaw,
    BoilerEfficiency,
    BuildingDefaults,
    DesignTemperatures,
    OccupancyMode,
    PredefinedHousehold,
    RoofDefaults,
    StorageDefaults,
)
from hisim.renovisor.report import MappingReport
from hisim.renovisor.request import House, Request
from hisim.renovisor.tabula import BuildingCode, BuildingCodeSelector
from hisim.renovisor.vocabulary import (
    HeatDistributionType,
    HeatGenerator,
    ReportStatus,
    ThermalElement,
)
from hisim.renovisor.whitelist import TranslatorError, Unmapped, Whitelist


class EditKind(str, Enum):
    """What kind of change one :class:`Edit` is.

    They are the three kinds the diff rule permits plus the document's own name and description,
    which carry no simulated meaning. The map page groups the YAML diff by them, and the diff
    check is written against the same list, so a fifth kind cannot appear in one without the
    other.
    """

    VARIANT_SELECTION = "VARIANT_SELECTION"
    GROUP_FLAG = "GROUP_FLAG"
    CONSTRUCTOR_SWAP = "CONSTRUCTOR_SWAP"
    CONFIG_VALUE = "CONFIG_VALUE"
    DOCUMENT = "DOCUMENT"


@dataclass(frozen=True)
class Edit:
    """One change the translator made, with what asked for it.

    Every edit carries its provenance because the mapping report and the map page both have to
    answer "why is this line different from the base file?", and the answer is always a request
    path or a translator default.

    Args:
        kind: Which of the permitted kinds of change this is.
        location: Where in the document it landed, dotted, e.g.
            ``components.Building.config.roof_u_value_in_watt_per_m2_per_kelvin``.
        value: The value written; ``None`` for a removal.
        source: The request path or the default that asked for it.
        note: One phrase a person can read.
    """

    kind: EditKind
    location: str
    value: Any
    source: str
    note: str


class TranslateError(TranslatorError):
    """A translation that produced a file the diff rule or the loader will not accept.

    It is a bug in this package, not in the request: exit 3 like every other translator error.
    """


class DocumentPaths:
    """The dotted spellings of the places in an energy-system document the translator touches.

    Spelled once so that an :class:`Edit`'s location, the diff check's messages and the map
    page's hunk annotations all name the same place the same way.
    """

    #: The top-level blocks.
    COMPONENTS: ClassVar[str] = "components"
    GROUPS: ClassVar[str] = "groups"
    VARIANTS: ClassVar[str] = "variants"

    #: The keys of one component entry.
    CONFIG: ClassVar[str] = "config"
    PRESET: ClassVar[str] = "preset"
    CONSTRUCTOR: ClassVar[str] = "constructor"

    #: The entry keys the translator must never touch: they are the base file's reviewed wiring.
    IMMUTABLE_ENTRY_KEYS: ClassVar[Tuple[str, ...]] = ("class", "inputs", "sizing_sources")

    @classmethod
    def config_field(cls, component: str, field_name: str) -> str:
        """Return the dotted location of one component's configuration field."""
        return f"{cls.COMPONENTS}.{component}.{cls.CONFIG}.{field_name}"

    @classmethod
    def constructor_of(cls, component: str) -> str:
        """Return the dotted location of one component's constructor call."""
        return f"{cls.COMPONENTS}.{component}.{cls.CONSTRUCTOR}"

    @classmethod
    def variant_selection(cls, variant: str) -> str:
        """Return the dotted location of one variant's selection."""
        return f"{cls.VARIANTS}.{variant}.selected"

    @classmethod
    def group_flag(cls, group: str) -> str:
        """Return the dotted location of one group's flag."""
        return f"{cls.GROUPS}.{group}.enabled"


class SystemEditor:
    """A loaded energy-system file, with the four kinds of permitted edit applied to a copy.

    The model is a frozen pydantic document, which is right for everything that reads it and
    awkward for the one stage that writes it, so this class keeps the mutable working copy in one
    place and rebuilds the frozen model once at the end. It also knows *where* a component lives —
    at the top level, inside a group, or inside one option of one variant — which is what lets the
    parametriser ask "does the selected file have a ``HeatDistributionController``?" without
    caring how the file is organised.

    Args:
        model: The base file as :func:`~hisim.energy_system.loader.load_energy_system` returned it.
    """

    def __init__(self, model: EnergySystemFile) -> None:
        """Take a mutable working copy of the document's three component containers."""
        self._model = model
        self._components: Dict[str, ComponentEntry] = dict(model.components)
        self._groups: Dict[str, Tuple[bool, Dict[str, ComponentEntry]]] = {
            name: (group.enabled, dict(group.components)) for name, group in model.groups.items()
        }
        self._variants: Dict[str, Tuple[str, Dict[str, Dict[str, ComponentEntry]]]] = {
            name: (
                variant.selected,
                {option: dict(body.components) for option, body in variant.options.items()},
            )
            for name, variant in model.variants.items()
        }
        self._name = model.name
        self._description = model.description

    def selected_component_names(self) -> Tuple[str, ...]:
        """Return the names of every component of the world the file currently selects, sorted.

        Returns:
            The top-level components, those of the enabled groups and those of the selected
            option of every variant.
        """
        return tuple(sorted(self._selected().keys()))

    def has(self, component: str) -> bool:
        """Return whether the currently selected world carries a component of that name."""
        return component in self._selected()

    def variant_names(self) -> Tuple[str, ...]:
        """Return the file's variant names, sorted."""
        return tuple(sorted(self._variants))

    def group_names(self) -> Tuple[str, ...]:
        """Return the file's group names, sorted."""
        return tuple(sorted(self._groups))

    def variant_selection(self, variant: str) -> str:
        """Return which option a variant currently selects.

        Args:
            variant: The variant's name.

        Returns:
            The selected option's name.

        Raises:
            KeyError: When the file has no such variant.
        """
        return self._variants[variant][0]

    def variant_options(self, variant: str) -> Tuple[str, ...]:
        """Return the option names of one variant, sorted.

        Raises:
            KeyError: When the file has no such variant.
        """
        return tuple(sorted(self._variants[variant][1]))

    def select_variant(self, variant: str, option: str) -> None:
        """Select one option of one variant.

        Args:
            variant: The variant's name.
            option: The option to select.

        Raises:
            KeyError: When the variant or the option does not exist; the parametriser turns that
                into a refusal naming the base file.
        """
        selected, options = self._variants[variant]
        del selected
        if option not in options:
            raise KeyError(option)
        self._variants[variant] = (option, options)

    def enable_group(self, group: str, enabled: bool) -> None:
        """Set one group's flag.

        Args:
            group: The group's name.
            enabled: Whether its components take part in the run.

        Raises:
            KeyError: When the file has no such group.
        """
        _, components = self._groups[group]
        self._groups[group] = (enabled, components)

    def entry(self, component: str) -> ComponentEntry:
        """Return the entry of one component of the selected world.

        Raises:
            KeyError: When the selected world has no component of that name.
        """
        return self._selected()[component]

    def set_config(self, component: str, field: str, value: Any) -> None:
        """Write one configuration value onto one component of the selected world.

        A value already present under that key is replaced; a new key is appended, which keeps
        the emitted order stable because the parametriser writes leaves in a fixed order.

        Args:
            component: The component's recorded key.
            field: The configuration field's HiSim name.
            value: The value, as the inventory carries it.

        Raises:
            KeyError: When the selected world has no component of that name.
        """
        entry = self._selected()[component]
        config = dict(entry.config)
        config[field] = value
        self._replace(component, entry.model_copy(update={"config": config}))

    def swap_constructor(
        self,
        component: str,
        constructor: str,
        arguments: Mapping[str, Any],
        removed_config_keys: Sequence[str],
    ) -> None:
        """Replace a component's preset by a named constructor and drop the keys it now supplies.

        Args:
            component: The component's recorded key.
            constructor: The constructor's name, e.g. ``"for_tabula_code"``.
            arguments: Its arguments, as plain values the file's codec can decode.
            removed_config_keys: Recorded ``config`` keys the constructor now supplies, which must
                not stay behind: a leftover value would override the constructor's own.

        Raises:
            KeyError: When the selected world has no component of that name.
        """
        entry = self._selected()[component]
        config = {key: value for key, value in entry.config.items() if key not in set(removed_config_keys)}
        self._replace(
            component,
            entry.model_copy(
                update={
                    "preset": None,
                    "constructor": ConstructorCall(name=constructor, arguments=dict(arguments)),
                    "config": config,
                }
            ),
        )

    def set_document(self, name: str, description: str) -> None:
        """Set the document's name and description, which carry no simulated meaning."""
        self._name = name
        self._description = description

    def build(self) -> EnergySystemFile:
        """Return the edited document as a frozen model again.

        Returns:
            A new :class:`~hisim.energy_system.model.EnergySystemFile`; the base model handed to
            the constructor is untouched.
        """
        return self._model.model_copy(
            update={
                "name": self._name,
                "description": self._description,
                "components": dict(self._components),
                "groups": {
                    name: Group(name=name, enabled=enabled, components=dict(components))
                    for name, (enabled, components) in self._groups.items()
                },
                "variants": {
                    name: Variant(
                        name=name,
                        selected=selected,
                        options={
                            option: VariantOption(name=option, components=dict(members))
                            for option, members in options.items()
                        },
                    )
                    for name, (selected, options) in self._variants.items()
                },
            }
        )

    def _selected(self) -> Dict[str, ComponentEntry]:
        """Return the components of the currently selected world, by name."""
        merged: Dict[str, ComponentEntry] = dict(self._components)
        for enabled, components in self._groups.values():
            if enabled:
                merged.update(components)
        for selected, options in self._variants.values():
            merged.update(options.get(selected, {}))
        return merged

    def _replace(self, component: str, entry: ComponentEntry) -> None:
        """Put an edited entry back wherever the component lives."""
        if component in self._components:
            self._components[component] = entry
            return
        for name, (enabled, components) in self._groups.items():
            if component in components:
                components[component] = entry
                self._groups[name] = (enabled, components)
                return
        for name, (selected, options) in self._variants.items():
            members = options.get(selected, {})
            if component in members:
                members[component] = entry
                options[selected] = members
                self._variants[name] = (selected, options)
                return
        raise KeyError(component)


class DiffRule:
    """The mechanical form of requirement R4: which differences a translated file may carry.

    It is written as a walk over two rendered documents rather than as a text diff because the
    question is structural — "did anything author an input item?" — and a text diff would answer
    it only as accurately as the formatting allows. The first difference that is not permitted
    raises, naming its place, so a bug in the parametriser reads as one sentence.
    """

    #: The top-level keys a translated document may differ in.
    MUTABLE_TOP_LEVEL_KEYS: ClassVar[Tuple[str, ...]] = ("name", "description")

    #: The entry keys a translated entry may differ in, for the three swapped components.
    SWAPPABLE_ENTRY_KEYS: ClassVar[Tuple[str, ...]] = ("preset", "constructor", "config")

    @classmethod
    def check(cls, base: Mapping[str, Any], translated: Mapping[str, Any]) -> None:
        """Compare two rendered documents and raise on the first forbidden difference.

        Args:
            base: The base file as :meth:`~hisim.energy_system.emitter.EnergySystemEmitter.
                to_document` renders it.
            translated: The translated file, rendered the same way.

        Raises:
            TranslateError: On the first difference requirement R4 does not permit.
        """
        cls._same_keys(base, translated, "the document's top level")
        for key in base:
            if key in cls.MUTABLE_TOP_LEVEL_KEYS:
                continue
            if key == DocumentPaths.COMPONENTS:
                cls._components(base[key], translated[key], DocumentPaths.COMPONENTS)
            elif key == DocumentPaths.GROUPS:
                cls._groups(base[key], translated[key])
            elif key == DocumentPaths.VARIANTS:
                cls._variants(base[key], translated[key])
            elif base[key] != translated[key]:
                raise TranslateError(f"the top-level key '{key}'")

    @classmethod
    def _components(
        cls, base: Mapping[str, Any], translated: Mapping[str, Any], location: str
    ) -> None:
        """Compare two component mappings: the same names, each entry differing only where allowed."""
        cls._same_keys(base, translated, location)
        for name in base:
            cls._entry(base[name], translated[name], f"{location}.{name}", name)

    @classmethod
    def _entry(
        cls, base: Mapping[str, Any], translated: Mapping[str, Any], location: str, name: str
    ) -> None:
        """Compare two rendered entries of one component."""
        swappable = name in ConstructorSwaps.component_keys()
        for key in sorted(set(base) | set(translated)):
            immutable = key in DocumentPaths.IMMUTABLE_ENTRY_KEYS
            if not immutable and key == DocumentPaths.CONFIG:
                continue
            if not immutable and key in cls.SWAPPABLE_ENTRY_KEYS and swappable:
                continue
            if base.get(key) != translated.get(key):
                raise TranslateError(f"'{location}.{key}'")

    @classmethod
    def _groups(cls, base: Mapping[str, Any], translated: Mapping[str, Any]) -> None:
        """Compare two group mappings: the same names, only the flags free to differ."""
        cls._same_keys(base, translated, DocumentPaths.GROUPS)
        for name in base:
            cls._components(
                base[name]["components"],
                translated[name]["components"],
                f"{DocumentPaths.GROUPS}.{name}.components",
            )

    @classmethod
    def _variants(cls, base: Mapping[str, Any], translated: Mapping[str, Any]) -> None:
        """Compare two variant mappings: the same names and options, only the selection free."""
        cls._same_keys(base, translated, DocumentPaths.VARIANTS)
        for name in base:
            options_location = f"{DocumentPaths.VARIANTS}.{name}.options"
            cls._same_keys(base[name]["options"], translated[name]["options"], options_location)
            for option in base[name]["options"]:
                cls._components(
                    base[name]["options"][option]["components"],
                    translated[name]["options"][option]["components"],
                    f"{options_location}.{option}.components",
                )

    @classmethod
    def _same_keys(cls, base: Mapping[str, Any], translated: Mapping[str, Any], location: str) -> None:
        """Raise when two mappings do not carry exactly the same keys."""
        added = sorted(set(translated) - set(base))
        removed = sorted(set(base) - set(translated))
        if added:
            raise TranslateError(f"{location}: it adds {', '.join(added)}")
        if removed:
            raise TranslateError(f"{location}: it removes {', '.join(removed)}")


class BaseFiles:
    """Which recorded grouped twin one heat generator runs, and what has to be re-typed in it.

    Decision D-D: the recorded ``energy_systems/household_*_building_sizer.grouped.energy_system.yaml``
    files are the base files, as they stand. Nine of the eleven are named here; the two the MVP
    never selects are the car twin, because an electric vehicle is ``not_implemented_yet``
    (decision D-C), and nothing else.

    Two generators run a twin whose boiler is of the other kind, which one config override fixes:
    ``conventional_gas_heating`` re-types the gas twin's condensing boiler as ``CONVENTIONAL``
    and ``condensing_oil_heating`` re-types the oil twin's conventional boiler as ``CONDENSING``.
    Every other generator that shares a twin with another is an approximation the whitelist
    carries a note for, not a config change.
    """

    #: Where the recorded files live, relative to the repository root.
    DIRECTORY: ClassVar[str] = "energy_systems"

    #: The suffix every recorded base file carries.
    SUFFIX: ClassVar[str] = ".energy_system.yaml"

    #: generator -> the twin it runs (§5.2 of the calculation-request specification).
    BY_GENERATOR: ClassVar[Dict[HeatGenerator, str]] = {
        HeatGenerator.CONDENSING_GAS_HEATING: "household_gas_building_sizer.grouped",
        HeatGenerator.CONVENTIONAL_GAS_HEATING: "household_gas_building_sizer.grouped",
        HeatGenerator.CONVENTIONAL_LPG_HEATING: "household_gas_building_sizer.grouped",
        HeatGenerator.CONDENSING_LPG_HEATING: "household_gas_building_sizer.grouped",
        HeatGenerator.CONVENTIONAL_OIL_HEATING: "household_oil_building_sizer.grouped",
        HeatGenerator.CONDENSING_OIL_HEATING: "household_oil_building_sizer.grouped",
        HeatGenerator.HVO_HEATING: "household_oil_building_sizer.grouped",
        HeatGenerator.PELLET_HEATING: "household_pellets_building_sizer.grouped",
        HeatGenerator.BIOMASS_HEATING: "household_pellets_building_sizer.grouped",
        HeatGenerator.SOLID_FUEL_HEATING: "household_pellets_building_sizer.grouped",
        HeatGenerator.WOODCHIP_HEATING: "household_wood_chips_building_sizer.grouped",
        HeatGenerator.HYDROGEN_HEATING: "household_hydrogen_boiler_building_sizer.grouped",
        HeatGenerator.AIR_SOURCE_HEAT_PUMP: "household_heatpump_building_sizer.grouped",
        HeatGenerator.GROUND_SOURCE_HEAT_PUMP: "household_heatpump_building_sizer.grouped",
        HeatGenerator.HYBRID_HEAT_PUMP: "household_heatpump_building_sizer.grouped",
        HeatGenerator.ELECTRIC_HEATING: "household_electric_heating_building_sizer.grouped",
        HeatGenerator.DISTRICT_HEATING: "household_district_heating_building_sizer.grouped",
    }

    #: generator -> the twin that also wires a solar thermal collector, for the two that have one.
    WITH_SOLAR_THERMAL: ClassVar[Dict[HeatGenerator, str]] = {
        HeatGenerator.CONDENSING_GAS_HEATING: "household_gas_solar_thermal_building_sizer.grouped",
        HeatGenerator.CONVENTIONAL_GAS_HEATING: "household_gas_solar_thermal_building_sizer.grouped",
        HeatGenerator.AIR_SOURCE_HEAT_PUMP: "household_heatpump_solar_thermal_building_sizer.grouped",
    }

    #: generator -> the ``boiler_type`` its twin has to be re-typed to.
    BOILER_TYPE: ClassVar[Dict[HeatGenerator, str]] = {
        HeatGenerator.CONVENTIONAL_GAS_HEATING: "CONVENTIONAL",
        HeatGenerator.CONVENTIONAL_LPG_HEATING: "CONVENTIONAL",
        HeatGenerator.CONDENSING_OIL_HEATING: "CONDENSING",
    }

    #: twin stem -> the recorded key of its heat generator, which the config overrides address.
    GENERATOR_COMPONENT: ClassVar[Dict[str, str]] = {
        "household_gas_building_sizer.grouped": "CondensingGasBoiler",
        "household_gas_solar_thermal_building_sizer.grouped": "CondensingGasBoiler",
        "household_oil_building_sizer.grouped": "ConventionalOilBoiler",
        "household_pellets_building_sizer.grouped": "ConventionalPelletBoiler",
        "household_wood_chips_building_sizer.grouped": "ConventionalWoodChipBoiler",
        "household_hydrogen_boiler_building_sizer.grouped": "CondensingHydrogenBoiler",
        "household_heatpump_building_sizer.grouped": "MoreAdvancedHeatPumpHPLib",
        "household_heatpump_solar_thermal_building_sizer.grouped": "MoreAdvancedHeatPumpHPLib",
        "household_electric_heating_building_sizer.grouped": "ElectricHeating",
        "household_district_heating_building_sizer.grouped": "DistrictHeating",
    }

    #: The generators whose twin is a heat pump, which alone has a flow temperature to write.
    HEAT_PUMPS: ClassVar[Tuple[HeatGenerator, ...]] = HeatGenerator.heat_pumps()

    #: The generators whose twin is a ``GenericBoiler``, which alone has efficiency bounds.
    BOILERS: ClassVar[Tuple[HeatGenerator, ...]] = (
        HeatGenerator.CONDENSING_GAS_HEATING,
        HeatGenerator.CONVENTIONAL_GAS_HEATING,
        HeatGenerator.CONVENTIONAL_LPG_HEATING,
        HeatGenerator.CONDENSING_LPG_HEATING,
        HeatGenerator.CONVENTIONAL_OIL_HEATING,
        HeatGenerator.CONDENSING_OIL_HEATING,
        HeatGenerator.HVO_HEATING,
        HeatGenerator.PELLET_HEATING,
        HeatGenerator.BIOMASS_HEATING,
        HeatGenerator.SOLID_FUEL_HEATING,
        HeatGenerator.WOODCHIP_HEATING,
        HeatGenerator.HYDROGEN_HEATING,
    )

    @classmethod
    def select(cls, generator: HeatGenerator, with_solar_thermal: bool) -> str:
        """Return the file name of the twin one house runs.

        Args:
            generator: The renovated house's heat generator.
            with_solar_thermal: Whether the house has a solar thermal collector *and* the
                generator is one of the two the twins wire a collector for.

        Returns:
            The file name, without a directory.
        """
        if with_solar_thermal and generator in cls.WITH_SOLAR_THERMAL:
            return cls.WITH_SOLAR_THERMAL[generator] + cls.SUFFIX
        return cls.BY_GENERATOR[generator] + cls.SUFFIX

    @classmethod
    def stem(cls, file_name: str) -> str:
        """Return a file name without the format's suffix, for the generator lookup."""
        return file_name[: -len(cls.SUFFIX)] if file_name.endswith(cls.SUFFIX) else file_name

    @classmethod
    def generator_component(cls, file_name: str) -> str:
        """Return the recorded key of one twin's heat generator."""
        return cls.GENERATOR_COMPONENT[cls.stem(file_name)]

    @classmethod
    def has_solar_thermal_wiring(cls, generator: HeatGenerator) -> bool:
        """Return whether a recorded twin wires a solar thermal collector for this generator."""
        return generator in cls.WITH_SOLAR_THERMAL

    @classmethod
    def file_names(cls) -> Tuple[str, ...]:
        """Return every twin the table names, sorted, for the test that checks they all exist."""
        names = {stem + cls.SUFFIX for stem in cls.BY_GENERATOR.values()}
        names |= {stem + cls.SUFFIX for stem in cls.WITH_SOLAR_THERMAL.values()}
        return tuple(sorted(names))


@dataclass(frozen=True)
class ConstructorSwap:
    """One component whose recorded preset is replaced by a named constructor.

    Three components of every twin are parameterised by an identifier out of an open space -- a
    TABULA code, a weather station, a LoadProfileGenerator household -- rather than by a handful
    of variants, which is why HiSim gives each of them a named constructor and why a preset
    cannot express what a particular dwelling needs.

    Args:
        component_key: The recorded key in the twin, e.g. ``"Building"``.
        constructor: The constructor's name, e.g. ``"for_tabula_code"``.
        removed_config_keys: Recorded ``config`` keys the constructor now supplies, which are
            dropped so that a recorded value cannot override the value this dwelling needs.
    """

    component_key: str
    constructor: str
    removed_config_keys: Tuple[str, ...]


class ConstructorSwaps:
    """The three swaps, and what each of them removes.

    Held as data rather than as three methods so that the diff check can ask "is this component
    allowed to have had its preset swapped?" from the same list the swaps are performed from.
    """

    #: The building: its TABULA code, floor area, apartment count and reference temperature are
    #: constructor arguments. Removing the recorded ``weather_identity`` is the point of the
    #: swap: it is a sized field, so a recorded value pins it, and a building in Dublin carrying
    #: Aachen's weather identity keys its solar-gain cache on a climate it is not simulated in.
    BUILDING: ClassVar[ConstructorSwap] = ConstructorSwap(
        component_key="Building",
        constructor="for_tabula_code",
        removed_config_keys=("weather_identity", "number_of_apartments"),
    )

    #: The weather: the recorded entry carries a complete configuration naming Aachen's files,
    #: and the station's own constructor knows where every catalogue station's data lives, so
    #: the recorded location, path and reader all go.
    WEATHER: ClassVar[ConstructorSwap] = ConstructorSwap(
        component_key="Weather",
        constructor="for_location",
        removed_config_keys=("location", "source_path", "data_source"),
    )

    #: The occupancy: the recorded preset is one fixed household read in the local-generator
    #: mode, and the MVP writes the predefined CHR01 profile explicitly so that HiSim's own
    #: fallback chain is never entered (decision D-C).
    OCCUPANCY: ClassVar[ConstructorSwap] = ConstructorSwap(
        component_key="UTSPConnector",
        constructor="for_household",
        removed_config_keys=("data_acquisition_mode",),
    )

    #: All three, in the order they are applied.
    ALL: ClassVar[Tuple[ConstructorSwap, ...]] = (BUILDING, WEATHER, OCCUPANCY)

    @classmethod
    def component_keys(cls) -> Tuple[str, ...]:
        """Return the recorded keys of the three swapped components, sorted."""
        return tuple(sorted(swap.component_key for swap in cls.ALL))


class BatteryCapacityLaw:
    """How ``battery.days_to_cover`` becomes a capacity in kilowatt hours.

    Decision D-C removed the invented per-resident table the frontend side's spec proposed: in
    the MVP every household runs the precomputed ``CHR01 Couple both at Work`` profile, so the
    daily electricity the battery is sized against is the daily electricity the simulation will
    actually consume. The profile is read once for a whole year at the run's own resolution and
    remembered on the class, because reading it is the most expensive thing this layer does and
    the answer does not depend on the request.

    Sizing over a fixed full year rather than over the requested period is deliberate: two runs
    of the same request with different ``--period`` values must not size two different
    batteries, and a winter week would size one for the worst fortnight of the year.
    """

    #: Watt seconds in a kilowatt hour, for turning a power profile into an energy.
    WATT_SECONDS_PER_KILOWATT_HOUR: ClassVar[float] = 3_600_000.0

    #: Days in the year the annual profile is divided by.
    DAYS_PER_YEAR: ClassVar[float] = 365.0

    #: The resolution and the year the profile is read at; the run's own (F-spec §2.3).
    SECONDS_PER_TIMESTEP: ClassVar[int] = 900
    YEAR: ClassVar[int] = 2019

    #: The remembered daily electricity of the CHR01 profile, in kilowatt hours per day.
    _daily_kilowatt_hours: ClassVar[Optional[float]] = None

    @classmethod
    def daily_kilowatt_hours(cls) -> float:
        """Return the CHR01 household's mean daily electricity use, in kilowatt hours.

        Returns:
            The annual sum of the shipped profile divided by 365, computed once per process.
        """
        if cls._daily_kilowatt_hours is None:
            from hisim.components.loadprofilegenerator_utsp_connector import (
                LpgDataAcquisitionMode,
                UtspLpgConnector,
                UtspLpgConnectorConfig,
            )
            from hisim.simulationparameters import SimulationParameters
            from utspclient.helpers.lpgdata import Households

            parameters = SimulationParameters.full_year(
                year=cls.YEAR, seconds_per_timestep=cls.SECONDS_PER_TIMESTEP
            )
            config = UtspLpgConnectorConfig.for_household(
                ConstructorSwaps.OCCUPANCY.component_key,
                household=Households.CHR01_Couple_both_at_Work,
                data_acquisition_mode=LpgDataAcquisitionMode.USE_PREDEFINED_PROFILE,
            )
            watts = UtspLpgConnector.electricity_consumption_of(config, parameters)
            energy = sum(watts) * cls.SECONDS_PER_TIMESTEP / cls.WATT_SECONDS_PER_KILOWATT_HOUR
            cls._daily_kilowatt_hours = energy / cls.DAYS_PER_YEAR
        return cls._daily_kilowatt_hours

    @classmethod
    def capacity_of(cls, days_to_cover: int) -> Tuple[float, str]:
        """Return the battery capacity for a number of days, and the note explaining it.

        Args:
            days_to_cover: How many days of household electricity the battery should hold.

        Returns:
            ``(capacity in kilowatt hours, the arithmetic with its numbers)``.
        """
        daily = cls.daily_kilowatt_hours()
        capacity = days_to_cover * daily
        note = (
            f"{days_to_cover} day(s) x {daily:.4g} kWh/day, the annual electricity of the "
            f"precomputed {PredefinedHousehold.NAME} profile this run also simulates, divided by "
            f"{cls.DAYS_PER_YEAR:g}; the vehicle and heat-pump terms of the earlier law are "
            "dropped for the MVP"
        )
        return capacity, note


@dataclass(frozen=True)
class TranslatedSystem:
    """One twin with one renovated house written into it.

    Args:
        model: The edited document.
        base_file_name: The twin it came from.
        file_name: What the dumped file is called, ``renovisor_<hash>.energy_system.yaml``.
        yaml_text: The canonical text, byte-stable for one request.
        edits: Every change made, with its provenance.
        report: The mapping report, complete.
        economic_context: What the lifecycle cost engine has to be told about the dwelling that
            the simulation cannot tell it -- the existing-asset register, the envelope cost
            subjects and the applicant. ``run`` attaches it to the simulation parameters beside
            the economic parameters; ``None`` only for a translation built before the context
            existed, which nothing in the package does.
    """

    model: EnergySystemFile
    base_file_name: str
    file_name: str
    yaml_text: str
    edits: Tuple[Edit, ...]
    report: MappingReport
    economic_context: Optional[Any] = None

    def assert_only_permitted_edits(self, base: EnergySystemFile) -> None:
        """Raise when the translation changed something the diff rule does not permit."""
        DiffRule.check(
            EnergySystemEmitter.to_document(base), EnergySystemEmitter.to_document(self.model)
        )


class Targets:
    """The HiSim components and fields the translator writes, spelled once.

    This is the "Target" column of §3 of the calculation-request specification as data. Holding
    the strings here rather than at the call sites is what makes a HiSim rename a change in one
    place, and what lets the capability document name a measure's targets without running one.
    """

    #: The components every twin carries.
    BUILDING: ClassVar[str] = "Building"
    WEATHER: ClassVar[str] = "Weather"
    OCCUPANCY: ClassVar[str] = "UTSPConnector"
    PV: ClassVar[str] = "PVSystem"
    DHW_STORAGE: ClassVar[str] = "DHWStorage"

    #: The components only some twins carry.
    HEAT_DISTRIBUTION_CONTROLLER: ClassVar[str] = "HeatDistributionController"
    SOLAR_THERMAL: ClassVar[str] = "SolarThermalSystem"
    BATTERY: ClassVar[str] = "Battery"

    #: The variant every twin carries, and the two options the MVP selects between.
    ELECTRICITY_MANAGEMENT: ClassVar[str] = "electricity_management"
    WITH_BATTERY: ClassVar[str] = "ems_with_battery"
    METERED_DIRECTLY: ClassVar[str] = "metered_directly"

    #: Config fields, by their HiSim names.
    HEATING_SYSTEM: ClassVar[str] = "heating_system"
    SET_HEATING_TEMPERATURE: ClassVar[str] = "set_heating_temperature_in_celsius"
    SHARE_OF_ROOF: ClassVar[str] = "share_of_maximum_pv_potential"
    POWER_IN_WATT: ClassVar[str] = "power_in_watt"
    AZIMUTH: ClassVar[str] = "azimuth"
    TILT: ClassVar[str] = "tilt"
    LOCATION: ClassVar[str] = "location"
    STORAGE_VOLUME: ClassVar[str] = "volume_heating_water_storage_in_liter"
    STORAGE_HEAT_TRANSFER: ClassVar[str] = "heat_transfer_coefficient_in_watt_per_m2_per_kelvin"
    BATTERY_CAPACITY: ClassVar[str] = "custom_battery_capacity_generic_in_kilowatt_hour"
    BATTERY_INVERTER: ClassVar[str] = "custom_pv_inverter_power_generic_in_watt"
    COLLECTOR_AREA: ClassVar[str] = "area_m2"
    FLOW_TEMPERATURE: ClassVar[str] = "flow_temperature_in_celsius"
    STANDARDIZED_SCOP_W35: ClassVar[str] = "standardized_scop_en14825_w35"
    STANDARDIZED_SCOP_W55: ClassVar[str] = "standardized_scop_en14825_w55"
    EFFICIENCY_MAXIMUM: ClassVar[str] = "eff_th_max"
    EFFICIENCY_MINIMUM: ClassVar[str] = "eff_th_min"
    BOILER_TYPE: ClassVar[str] = "boiler_type"
    DOMESTIC_HOT_WATER: ClassVar[str] = "with_domestic_hot_water_preparation"

    #: The percentage a share of the roof is divided by to become a fraction.
    PERCENT: ClassVar[float] = 100.0

    @classmethod
    def element_u_value(cls, element: ThermalElement) -> str:
        """Return the ``Building`` config field carrying one element's U-value."""
        return f"{element.value}_u_value_in_watt_per_m2_per_kelvin"

    @classmethod
    def element_area(cls, element: ThermalElement) -> str:
        """Return the ``Building`` config field carrying one element's area."""
        return f"{element.value}_area_in_m2"

    @classmethod
    def describe(cls, component: str, field_name: str) -> str:
        """Return the ``Component.config.field`` string the mapping report carries as a target."""
        return f"{component}.config.{field_name}"

    @classmethod
    def describe_constructor(cls, component: str, constructor: str, argument: str) -> str:
        """Return the ``Component.constructor.name.argument`` string the report carries."""
        return f"{component}.constructor.{constructor}.{argument}"


class Translator:
    """Writes one renovated house into one recorded twin, and reports every leaf of the request.

    Args:
        base_files_directory: Where the recorded ``*.grouped.energy_system.yaml`` files live.
        whitelist: The parsed ``not_implemented_yet.yaml``; every leaf with no target is asked
            of it and a leaf it does not carry fails the build.
    """

    #: The name and description every translated document carries. Both are deterministic: two
    #: runs of the same request must produce the same bytes.
    NAME_TEMPLATE: ClassVar[str] = "renovisor_{hash}"
    DESCRIPTION_TEMPLATE: ClassVar[str] = (
        "RenoVisor translator {version} from base {base}"
    )

    #: The target the ``used`` lines of the economics-only leaves name. These leaves write no
    #: simulation component; they feed the economic context the engine evaluates.
    ECONOMICS_TARGET: ClassVar[str] = "the economic context the lifecycle engine evaluates"

    #: The note explaining the transmission adjustment factor a written U-value fixes.
    ADJUSTMENT_NOTE: ClassVar[str] = (
        "overriding this element's U-value also fixes its transmission adjustment factor "
        "(floor 0.5, others 1) instead of taking the TABULA row's"
    )

    def __init__(self, base_files_directory: Path, whitelist: Optional[Whitelist] = None) -> None:
        """Store the directory and the list; nothing is read until :meth:`translate`."""
        self._directory = Path(base_files_directory)
        self._whitelist = whitelist if whitelist is not None else Whitelist.load()

    def translate(self, request: Request, applied: AppliedPackage) -> TranslatedSystem:
        """Write one renovated house into its twin and return the file and the report.

        Args:
            request: The validated request, for the country, the content hash and the leaves the
                report has to account for.
            applied: What the package came to, from :func:`hisim.renovisor.apply.apply`.

        Returns:
            The translated system, its diff rule already asserted and its text already loaded
            back as a structural self-check.

        Raises:
            TranslatorError: When a request leaf has no target and ``not_implemented_yet.yaml``
                does not list it, when the diff rule is broken, or when the dumped text does not
                load. All three are exit 3.
        """
        house = House.from_dict(applied.house)
        raw = applied.house
        generator = house.heating.type_of_system
        with_solar_thermal = house.solar_thermal_system is not None and BaseFiles.has_solar_thermal_wiring(
            generator
        )
        base_file_name = BaseFiles.select(generator, with_solar_thermal)
        base = load_energy_system(self._directory / base_file_name)
        editor = SystemEditor(base)
        report = MappingReport(request.schema_version, request.country.value)
        report.base_file = base_file_name
        # The economics-only leaves are covered before the stages run: the fail-loud stage below
        # accounts for every leftover leaf, and the builder that reads these leaves needs the
        # translated model, which only exists after it.
        for path, value, note in EconomicContextBuilder.stated_leaves(request.document):
            report.used(path, self.ECONOMICS_TARGET, value=value, note=note)
        self._cost_blocks(request, raw, report)
        edits: List[Edit] = []
        state = _TranslationState(
            request=request,
            applied=applied,
            house=house,
            raw=raw,
            base_file_name=base_file_name,
            editor=editor,
            report=report,
            edits=edits,
            whitelist=self._whitelist,
        )

        _envelope(state)
        _weather(state)
        _occupancy(state)
        _heating(state)
        _hot_water(state)
        _comfort(state)
        _devices(state)
        _unmodelled(state)

        content_hash = request.content_hash()
        name = self.NAME_TEMPLATE.format(hash=content_hash)
        editor.set_document(
            name,
            self.DESCRIPTION_TEMPLATE.format(version=TRANSLATOR_VERSION, base=base_file_name),
        )
        edits.append(
            Edit(
                kind=EditKind.DOCUMENT,
                location="name",
                value=name,
                source="the request's content hash",
                note="the file is named after what it contains, so two identical requests are one file",
            )
        )
        model = editor.build()
        text = dump_energy_system(model)
        file_name = f"{name}{BaseFiles.SUFFIX}"
        report.energy_system_file = file_name
        report.set_measures([line.to_json() for line in applied.measures])
        # The economic context is built from the *edited* model, because its envelope subjects are
        # sized in square metres of the building the run will actually simulate.
        built = EconomicContextBuilder(
            request,
            applied,
            _building_config(model),
            generator_component=BaseFiles.generator_component(base_file_name),
            heating_reference_temperature_in_celsius=_design_temperature(model),
        ).build()
        report.set_subjects(built.subjects)
        report.set_unpriced_subjects(built.unpriced_subjects)
        for path, value, note in built.defaults:
            if not report.has(path):
                report.defaulted(path, value, note)
        for path, value, note in built.approximations:
            if not report.has(path):
                report.approximated(path, note, value=value)
        translated = TranslatedSystem(
            model=model,
            base_file_name=base_file_name,
            file_name=file_name,
            yaml_text=text,
            edits=tuple(edits),
            report=report,
            economic_context=built.context,
        )
        translated.assert_only_permitted_edits(base)
        self._self_check(text, file_name)
        report.assert_complete(request.document)
        return translated

    def _cost_blocks(self, request: Request, house: Mapping[str, Any], report: MappingReport) -> None:
        """Report every ``measures[i].cost`` block: used on an envelope measure, listed on any other.

        The fail-loud stage walks the house, not the package, so a block nobody reads would pass
        it silently. An envelope measure's block prices its cost subject; any other measure is
        priced from HiSim's cost database, and its block is asked of ``not_implemented_yet.yaml``
        like every other leaf the translator does not act on.

        Args:
            request: The validated request.
            house: The renovated house, which the list's conditions are evaluated on.
            report: The report being written.

        Raises:
            TranslatorError: When the list does not carry
                :attr:`~hisim.renovisor.economics.EconomicContextBuilder.UNREAD_COST_ITEM`.
        """
        for path, block, read in EconomicContextBuilder.cost_blocks(request.document):
            if read:
                report.used(path, self.ECONOMICS_TARGET, value=dict(block), note=EconomicContextBuilder.COST_USED_NOTE)
                continue
            entry = self._whitelist.require(Unmapped(EconomicContextBuilder.UNREAD_COST_ITEM), house)
            report.not_implemented_yet(path, entry.note, value=dict(block))

    @classmethod
    def _self_check(cls, text: str, file_name: str) -> None:
        """Load the dumped text back, so that a file HiSim will refuse never reaches a run.

        Raises:
            TranslateError: When the canonical text does not parse, which is a bug in this
                package rather than in the request.
        """
        try:
            load_energy_system(text)
        except Exception as error:  # pylint: disable=broad-except  # re-raised with its own code
            raise TranslateError(
                f"the translated {file_name} does not load back: {type(error).__name__}: {error}",
                "the self-check of §5.1 step 3 failed; the file was not written",
            ) from error


def _building_config(model: EnergySystemFile) -> Mapping[str, Any]:
    """Every ``Building`` configuration field of a translated model, however it is written.

    Two things read this. The envelope cost subjects are sized in square metres of the element
    they cover, and the areas live in the component's ``config`` block: the translator writes them
    from the request, and where the request stated none the archetype derives them, in which case
    the field is absent and the subject ends up unpriced rather than sized by a guess. The design
    heat load the existing generator is sized from needs the archetype itself — the TABULA code,
    the conditioned floor area and the apartment count — and those are *constructor arguments*
    after the ``for_tabula_code`` swap rather than config keys.

    Both halves are the same configuration seen through two spellings of the file format, so they
    are merged into one mapping here; a constructor argument wins, because it is what the
    constructor will put on the config the run is built with.

    Args:
        model: The edited energy-system document.

    Returns:
        The merged mapping, or ``{}`` when the model has no building.
    """
    entry = model.components.get(Targets.BUILDING) if isinstance(model.components, Mapping) else None
    if entry is None:
        return {}
    config = getattr(entry, "config", None)
    merged: Dict[str, Any] = dict(config) if isinstance(config, Mapping) else {}
    constructor = getattr(entry, "constructor", None)
    arguments = getattr(constructor, "arguments", None) if constructor is not None else None
    if isinstance(arguments, Mapping):
        merged.update(arguments)
    return merged


def _design_temperature(model: EnergySystemFile) -> Optional[float]:
    """The outside design temperature the translated ``Weather`` carries, or ``None``.

    The design condition belongs to the place the building stands in, so ``WeatherConfig`` states
    it and the building reads it through the sizing engine (decision D-21). Nothing has run yet
    when the economic context is built, so the value is taken from the weather's own constructor
    argument, which is where the translator wrote the country's reviewed design temperature.

    Args:
        model: The edited energy-system document.

    Returns:
        The temperature in degrees Celsius, or ``None`` when the model has no weather or the
        weather states none.
    """
    entry = model.components.get(Targets.WEATHER) if isinstance(model.components, Mapping) else None
    constructor = getattr(entry, "constructor", None) if entry is not None else None
    arguments = getattr(constructor, "arguments", None) if constructor is not None else None
    if not isinstance(arguments, Mapping):
        return None
    value = arguments.get("heating_reference_temperature_in_celsius")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


@dataclass
class _TranslationState:
    """Everything one translation carries between its seven stages.

    A private dataclass rather than seven arguments on seven methods: the stages all read the
    same house and all write into the same editor, the same report and the same edit log, and
    threading them by hand made the signatures longer than the bodies.

    Args:
        request: The validated request.
        applied: What the package came to.
        house: The renovated house, typed.
        raw: The renovated house as plain dictionaries, which every ``when`` is evaluated on.
        base_file_name: The twin being written into.
        editor: The working copy of it.
        report: The mapping report being filled in.
        edits: The log of changes, appended to.
        whitelist: The list every unmapped leaf is asked of.
    """

    request: Request
    applied: AppliedPackage
    house: House
    raw: Mapping[str, Any]
    base_file_name: str
    editor: "SystemEditor"
    report: MappingReport
    edits: List[Edit]
    whitelist: Whitelist

    def write(self, component: str, field_name: str, value: Any, source: str, note: str) -> bool:
        """Write one config value, log the edit and say whether the twin carries the component.

        Args:
            component: The recorded key of the component.
            field_name: The config field's HiSim name.
            value: The value to write.
            source: The request path or default that asked for it.
            note: One phrase a person can read.

        Returns:
            ``True`` when the value landed; ``False`` when the selected twin has no such
            component, which the caller turns into a whitelist question.
        """
        if not self.editor.has(component):
            return False
        self.editor.set_config(component, field_name, value)
        self.edits.append(
            Edit(
                kind=EditKind.CONFIG_VALUE,
                location=DocumentPaths.config_field(component, field_name),
                value=value,
                source=source,
                note=note,
            )
        )
        return True

    def swap(self, swap: ConstructorSwap, arguments: Mapping[str, Any], source: str, note: str) -> bool:
        """Replace one component's preset by its named constructor and log the edit."""
        if not self.editor.has(swap.component_key):
            return False
        self.editor.swap_constructor(
            swap.component_key, swap.constructor, arguments, swap.removed_config_keys
        )
        self.edits.append(
            Edit(
                kind=EditKind.CONSTRUCTOR_SWAP,
                location=DocumentPaths.constructor_of(swap.component_key),
                value=dict(arguments),
                source=source,
                note=note,
            )
        )
        return True

    def select(self, variant: str, option: str, source: str, note: str) -> None:
        """Select one variant option and log the edit."""
        self.editor.select_variant(variant, option)
        self.edits.append(
            Edit(
                kind=EditKind.VARIANT_SELECTION,
                location=DocumentPaths.variant_selection(variant),
                value=option,
                source=source,
                note=note,
            )
        )

    def listed(self, path: str, value: Any = None) -> None:
        """Report one request leaf as accepted and acted on by nothing, with its whitelist note.

        Raises:
            TranslatorError: When ``not_implemented_yet.yaml`` does not carry the leaf, which
                fails the translator's build rather than the user's request (rule 6).
        """
        entry = self.whitelist.require(Unmapped(path, value), self.raw)
        self.report.not_implemented_yet(path, entry.note, value=value)

    def present(self, path: str) -> bool:
        """Return whether the *request* carried one leaf, by its report path.

        The report has to account for what the request sent, so a leaf a measure added is not
        reported as a request leaf and a leaf a measure removed still is.
        """
        current: Any = self.request.document
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                return False
            current = current[part]
        return True


def _envelope(state: _TranslationState) -> None:
    """Write the building: its TABULA archetype, its five elements and its room set point.

    The archetype is a constructor swap rather than a config override because a TABULA code is
    an identifier out of a space of hundreds, and because the swap is what drops the recorded
    ``weather_identity`` that would otherwise pin a Dublin building to Aachen's solar gains.
    """
    building = state.house.building
    code = BuildingCodeSelector.select(
        country=state.request.country.value,
        building_type=building.building_type,
        construction_year=building.construction_year,
        requested_code=building.tabula_building_code,
    )
    arguments: Dict[str, Any] = {
        "building_code": code.code,
        "absolute_conditioned_floor_area_in_m2": building.absolute_conditioned_floor_area_in_m2,
        "number_of_apartments": BuildingDefaults.NUMBER_OF_APARTMENTS,
    }
    state.swap(
        ConstructorSwaps.BUILDING,
        arguments,
        source="house.building",
        note=(
            f"the recorded preset became for_tabula_code({code.code}); the recorded "
            "weather_identity was removed so its sizing law recomputes it"
        ),
    )
    _report_archetype(state, code)
    for element in ThermalElement:
        _element(state, element)
    state.write(
        Targets.BUILDING,
        Targets.SET_HEATING_TEMPERATURE,
        building.set_heating_temperature_in_celsius,
        source="house.building.set_heating_temperature_in_celsius",
        note="the room set point, which propagates to the heat distribution controller as a fact",
    )
    state.report.used(
        "house.building.set_heating_temperature_in_celsius",
        Targets.describe(Targets.BUILDING, Targets.SET_HEATING_TEMPERATURE),
        value=building.set_heating_temperature_in_celsius,
    )


def _report_archetype(state: _TranslationState, code: BuildingCode) -> None:
    """Report the three leaves the TABULA code was derived from, and the apartment count."""
    target = Targets.describe_constructor(Targets.BUILDING, "for_tabula_code", "building_code")
    status = ReportStatus.APPROXIMATED if code.is_approximated() else ReportStatus.USED
    note = "; ".join(code.notes) if code.notes else None
    for path in ("house.building.building_type", "house.building.construction_year"):
        if state.present(path):
            state.report.field(path, status, target=target, value=code.code, note=note)
    if state.present("house.building.tabula_building_code"):
        state.report.used(
            "house.building.tabula_building_code", target, value=code.code,
            note="the expert override skips the derivation from country, type and year",
        )
    state.report.used(
        "house.building.absolute_conditioned_floor_area_in_m2",
        Targets.describe_constructor(
            Targets.BUILDING, "for_tabula_code", "absolute_conditioned_floor_area_in_m2"
        ),
        value=state.house.building.absolute_conditioned_floor_area_in_m2,
        note="it scales every area of the TABULA row",
    )
    state.report.defaulted(
        "house.building.number_of_apartments",
        BuildingDefaults.NUMBER_OF_APARTMENTS,
        note="the translator simulates one dwelling for every building type, the AB archetype included",
        target=Targets.describe_constructor(
            Targets.BUILDING, "for_tabula_code", "number_of_apartments"
        ),
    )


def _element(state: _TranslationState, element: ThermalElement) -> None:
    """Write one envelope element's U-value and area, and report both plus its extra fields."""
    block = state.house.element(element.value)
    u_path = f"house.building.{element.value}.u_value_in_watt_per_m2_per_kelvin"
    state.write(
        Targets.BUILDING,
        Targets.element_u_value(element),
        block.u_value_in_watt_per_m2_per_kelvin,
        source=u_path,
        note=Translator.ADJUSTMENT_NOTE,
    )
    layer_note = state.applied.element_note(element)
    state.report.used(
        u_path,
        Targets.describe(Targets.BUILDING, Targets.element_u_value(element)),
        value=block.u_value_in_watt_per_m2_per_kelvin,
        note=f"{layer_note}; {Translator.ADJUSTMENT_NOTE}" if layer_note else Translator.ADJUSTMENT_NOTE,
    )
    area_path = f"house.building.{element.value}.area_in_m2"
    if block.area_in_m2 is not None:
        state.write(
            Targets.BUILDING,
            Targets.element_area(element),
            block.area_in_m2,
            source=area_path,
            note="the request's own area, instead of the TABULA row's scaled one",
        )
        state.report.used(
            area_path,
            Targets.describe(Targets.BUILDING, Targets.element_area(element)),
            value=block.area_in_m2,
        )
    else:
        state.report.defaulted(
            area_path,
            "TABULA",
            note=(
                "absent from the request; the archetype's own area, scaled to the conditioned "
                "floor area, is used"
            ),
        )
    for name in ("outside_shading", "thermocover", "frame_material", "glazing_panes",
                 "low_emissivity_coating"):
        path = f"house.building.{element.value}.{name}"
        if state.present(path):
            state.listed(path, getattr(block, name))
    shape_path = f"house.building.{element.value}.shape"
    if element is ThermalElement.ROOF:
        shape = block.shape.value if block.shape is not None else RoofDefaults.SHAPE
        if state.present(shape_path):
            state.report.used(
                shape_path,
                Targets.describe(Targets.PV, Targets.TILT),
                value=RoofDefaults.TILT_BY_ROOF_SHAPE[shape],
                note="the roof's shape sets the tilt of anything mounted on it",
            )
        else:
            state.report.defaulted(
                shape_path,
                RoofDefaults.SHAPE,
                note="absent from the request; a pitched roof is assumed, which sets the tilt",
            )


def _weather(state: _TranslationState) -> None:
    """Point the weather at the dwelling's own country, and label the array with it too.

    The recorded entry carries a complete configuration naming Aachen's files; the station's own
    constructor knows where every catalogue station's data lives, so the recorded location, path
    and reader all go. The photovoltaic array carries the same name as a label, which is written
    so that a Dublin array does not read ``AACHEN`` in the file that ran.
    """
    country = state.request.country.value
    if country not in LocationEnum.__members__:
        raise TranslateError(
            f"no weather station of LocationEnum is named '{country}'",
            "the request schema and HiSim's station catalogue disagree, which the request "
            "validation should have caught",
        )
    design_temperature = DesignTemperatures.BY_COUNTRY.get(country)
    if design_temperature is None:
        raise TranslateError(
            f"no reviewed outside design temperature for country '{country}'",
            "Weather.for_location requires one since the weather owns that sizing fact (HiSim #771); add the "
            "country to DesignTemperatures.BY_COUNTRY with its source rather than sizing for Aachen's -7 °C",
        )
    state.swap(
        ConstructorSwaps.WEATHER,
        {"location": country, "heating_reference_temperature_in_celsius": design_temperature},
        source="location.country",
        note=(
            f"the recorded Aachen configuration became for_location({country}); its location, "
            "source_path and data_source were removed"
        ),
    )
    state.write(
        Targets.PV,
        Targets.LOCATION,
        country,
        source="location.country",
        note="the array's own label, which the recorded file carried as AACHEN",
    )
    state.report.defaulted(
        "house.building.heating_reference_temperature_in_celsius",
        design_temperature,
        note=(
            f"the reviewed outside design temperature of {country} (DesignTemperatures.BY_COUNTRY, to be "
            "reviewed), handed to the weather, which owns that sizing fact and hands it to the building"
        ),
        target=Targets.describe_constructor(
            Targets.WEATHER, "for_location", "heating_reference_temperature_in_celsius"
        ),
    )
    state.report.used(
        "location.country",
        Targets.describe_constructor(Targets.WEATHER, "for_location", "location"),
        value=country,
        note="one weather station per country; the NSRDB 15-minute data set of 2019",
    )
    if state.present("location.postcode"):
        state.listed("location.postcode", state.request.postcode)


def _occupancy(state: _TranslationState) -> None:
    """Write the one household the MVP image ships a profile for, explicitly (decision D-C).

    Explicitly, and never by leaving the recorded preset in place, because the connector's own
    ``USE_UTSP -> USE_LOCAL_LPG -> USE_PREDEFINED_PROFILE`` fallback chain would otherwise
    decide which household was simulated. Writing the mode and the household together means the
    file says what ran.
    """
    state.swap(
        ConstructorSwaps.OCCUPANCY,
        {
            "household": PredefinedHousehold.reference(),
            "data_acquisition_mode": OccupancyMode.PREDEFINED_CHR01.acquisition_mode,
        },
        source="the MVP occupancy mode",
        note=(
            f"every household is simulated as the precomputed {PredefinedHousehold.NAME} profile "
            "until the LoadProfileGenerator is in the image"
        ),
    )
    for name in ("number_of_residents", "home_office_days_per_week", "pv_self_consumption_optimised"):
        path = f"house.occupancy.{name}"
        if state.present(path):
            state.listed(path, getattr(state.house.occupancy, name))
    if state.present("house.appliances.white_appliances"):
        state.listed(
            "house.appliances.white_appliances",
            state.house.white_appliances.value if state.house.white_appliances else None,
        )


def _heating(state: _TranslationState) -> None:
    """Report the generator that selected the twin, and write what the twin can still be told."""
    generator = state.house.heating.type_of_system
    heating = state.house.heating
    entry = state.whitelist.match(Unmapped("house.heating.type_of_system", generator.value), state.raw)
    if entry is not None:
        state.report.not_implemented_yet("house.heating.type_of_system", entry.note, value=generator.value)
    else:
        approximated = _generator_note(state, generator)
        if approximated is not None:
            state.report.approximated(
                "house.heating.type_of_system", approximated, target=state.base_file_name,
                value=generator.value,
            )
        else:
            state.report.used(
                "house.heating.type_of_system", state.base_file_name, value=generator.value
            )
    boiler_type = BaseFiles.BOILER_TYPE.get(generator)
    if boiler_type is not None:
        state.write(
            BaseFiles.generator_component(state.base_file_name),
            Targets.BOILER_TYPE,
            boiler_type,
            source="house.heating.type_of_system",
            note=f"the twin's boiler is re-typed as {boiler_type} for this generator",
        )
    _heat_distribution(state, generator)
    _flow_temperature(state, generator, heating)
    _standardized_scop(state, generator, heating)
    _efficiency(state, generator, heating)
    for name in ("cooking_range", "secondary"):
        path = f"house.heating.{name}"
        if state.present(path):
            state.listed(path, getattr(heating, name))


def _generator_note(state: _TranslationState, generator: HeatGenerator) -> Optional[str]:
    """Return why a generator is an approximation of its twin, or ``None`` when it is exact."""
    del state
    if generator is HeatGenerator.HVO_HEATING:
        return "the oil twin burns heating oil; HVO has the same combustion model and a different CO2 factor"
    if generator in (HeatGenerator.BIOMASS_HEATING, HeatGenerator.SOLID_FUEL_HEATING):
        return "simulated on the pellet twin, which is the nearest recorded solid-fuel boiler"
    return None


class EmitterSubstitution:
    """The emitter the translator writes when the requested one cannot be carried through.

    HiSim models all three emitters of ``heat_distribution.type_of_system``, but the lifecycle
    cost database has no row for the low-temperature radiator, and decision D7 of the cost
    specification aborts a whole evaluation on a subject it cannot price. A heat-pump package
    asking for the emitter a heat-pump retrofit naturally installs would therefore end at exit 5
    with nothing written, which is a worse answer than a stated approximation.

    So that one value is written as surface heating -- ``FLOORHEATING``, the heat pump's other
    low-temperature emitter, which is priced -- and the run reports it as ``not_implemented_yet``
    with the whitelist's sentence beside it. The substitution lives here and the sentence lives
    in ``not_implemented_yet.yaml``; the day ``hisim/economics/adapter.py`` gains the missing
    cost row, both go away together.
    """

    #: The emitter each substituted request value is written as. An emitter absent from this
    #: table is written as itself.
    WRITTEN_AS: ClassVar[Dict[HeatDistributionType, HeatDistributionType]] = {
        HeatDistributionType.LOW_TEMPERATURE_RADIATOR: HeatDistributionType.SURFACE_HEATING,
    }

    @classmethod
    def written_as(cls, requested: HeatDistributionType) -> HeatDistributionType:
        """Return the emitter written into the energy-system file for *requested*.

        Args:
            requested: The emitter the renovated house carries.

        Returns:
            The substitute when there is one, otherwise *requested* itself.
        """
        return cls.WRITTEN_AS.get(requested, requested)

    @classmethod
    def is_substituted(cls, requested: HeatDistributionType) -> bool:
        """Return whether *requested* is written as a different emitter than itself."""
        return requested in cls.WRITTEN_AS


def _heat_distribution(state: _TranslationState, generator: HeatGenerator) -> None:
    """Tell the heat distribution controller which emitters the dwelling has.

    One of the three request values is written as another (:class:`EmitterSubstitution`): the
    file gets the substitute so the run is priced and completes, and the report gets the
    whitelist's ``not_implemented_yet`` line so nobody reads the result as the emitter they
    asked for.
    """
    del generator
    path = "house.heat_distribution.type_of_system"
    requested = state.house.heat_distribution
    member = EmitterSubstitution.written_as(requested).hisim_member
    if not state.write(
        Targets.HEAT_DISTRIBUTION_CONTROLLER,
        Targets.HEATING_SYSTEM,
        member.name,
        source=path,
        note="the emitter and the heat pump's space-heating controller both read it as a fact",
    ):
        state.listed(path, requested.value)
        return
    if EmitterSubstitution.is_substituted(requested):
        state.listed(path, requested.value)
        return
    state.report.used(
        path,
        Targets.describe(Targets.HEAT_DISTRIBUTION_CONTROLLER, Targets.HEATING_SYSTEM),
        value=member.name,
    )


def _flow_temperature(state: _TranslationState, generator: HeatGenerator, heating: Any) -> None:
    """Write the design flow temperature, which only the heat pump has a field for."""
    path = "house.heating.flow_temperature_in_celsius"
    if not state.present(path):
        return
    if heating.flow_temperature_in_celsius is None:
        state.report.approximated(
            path,
            "the heating_system measure replaced the generator this value described; the new "
            "generator keeps its own default",
        )
        return
    if generator in BaseFiles.HEAT_PUMPS:
        component = BaseFiles.generator_component(state.base_file_name)
        state.write(
            component,
            Targets.FLOW_TEMPERATURE,
            heating.flow_temperature_in_celsius,
            source=path,
            note="the heat pump's design flow temperature",
        )
        state.report.used(
            path,
            Targets.describe(component, Targets.FLOW_TEMPERATURE),
            value=heating.flow_temperature_in_celsius,
        )
        return
    state.listed(path, heating.flow_temperature_in_celsius)


def _standardized_scop(state: _TranslationState, generator: HeatGenerator, heating: Any) -> None:
    """Write the heat pump's rated SCOP onto hplib, which calibrates its fit to it (hisim-4g9.15).

    The request's checks guarantee a stated SCOP sits on a heat pump, and a ``heating_system``
    measure removes it with the rest of the old generator's description: a new heat pump keeps
    hplib's generic fit, any other new generator has no SCOP to take. Every heat pump runs the same
    twin, whose hplib machine is air/water, so a ground-source or hybrid unit's rating calibrates
    that air machine and is reported approximated (:func:`_scop_note`). Every heat-pump generator
    selects a twin with the hplib component; one without it would be asked of the whitelist, which
    lists neither rating, and so fail the translator's build.
    """
    for request_key, target in (
        ("heatpump_scop_en14825_w35", Targets.STANDARDIZED_SCOP_W35),
        ("heatpump_scop_en14825_w55", Targets.STANDARDIZED_SCOP_W55),
    ):
        path = f"house.heating.{request_key}"
        if not state.present(path):
            continue
        value = getattr(heating, request_key)
        if value is None:
            state.report.approximated(
                path,
                "the heating_system measure replaced the heat pump this rating described; "
                + (
                    "the new heat pump keeps hplib's generic fit"
                    if generator in BaseFiles.HEAT_PUMPS
                    else f"the new {generator.value} is not a heat pump and has no SCOP to calibrate"
                ),
            )
            continue
        component = BaseFiles.generator_component(state.base_file_name)
        if not state.write(component, target, value, source=path, note="calibrates hplib's fit to the stated SCOP"):
            state.listed(path, value)
            continue
        note = _scop_note(generator)
        if note is None:
            state.report.used(path, Targets.describe(component, target), value=value)
        else:
            state.report.approximated(path, note, target=Targets.describe(component, target), value=value)


def _scop_note(generator: HeatGenerator) -> Optional[str]:
    """Return why a heat pump's rated SCOP only approximates its unit, or ``None`` when it is exact.

    The heat-pump twin's hplib machine is air/water (group 1). A ground-source unit's rating was
    measured with brine entering at 0 °C, and a hybrid unit's heat pump works beside a boiler the
    twin does not have; either rating still calibrates the air machine, whose temperature
    dependence across the season is the air fit's.
    """
    if generator is HeatGenerator.GROUND_SOURCE_HEAT_PUMP:
        return "the rating calibrates hplib's air/water model; the twin has no brine/water machine yet (hisim-ztt7)"
    if generator is HeatGenerator.HYBRID_HEAT_PUMP:
        return (
            "the rating calibrates hplib's air/water model, which heats alone; the twin has no hybrid "
            "heat pump and boiler yet"
        )
    return None


def _efficiency(state: _TranslationState, generator: HeatGenerator, heating: Any) -> None:
    """Write a stated seasonal efficiency onto the boiler's two efficiency bounds."""
    path = "house.heating.seasonal_efficiency_in_percent"
    if not state.present(path):
        return
    if heating.seasonal_efficiency_in_percent is None:
        state.report.approximated(
            path,
            "the heating_system measure replaced the generator this value described; the new "
            "generator keeps its own defaults",
        )
        return
    if generator not in BaseFiles.BOILERS:
        state.listed(path, heating.seasonal_efficiency_in_percent)
        return
    component = BaseFiles.generator_component(state.base_file_name)
    maximum = heating.seasonal_efficiency_in_percent / BoilerEfficiency.PERCENT
    minimum = max(maximum - BoilerEfficiency.SPREAD, BoilerEfficiency.MINIMUM)
    state.write(component, Targets.EFFICIENCY_MAXIMUM, maximum, source=path, note="the stated efficiency")
    state.write(
        component,
        Targets.EFFICIENCY_MINIMUM,
        minimum,
        source=path,
        note=f"the part-load bound, {BoilerEfficiency.SPREAD:g} below the full-load one",
    )
    state.report.approximated(
        path,
        f"a seasonal figure becomes the boiler's full-load efficiency {maximum:.4g} and a "
        f"part-load bound {minimum:.4g}; HiSim interpolates between the two",
        target=Targets.describe(component, Targets.EFFICIENCY_MAXIMUM),
        value=maximum,
    )


def _hot_water(state: _TranslationState) -> None:
    """Write the hot-water storage, and report how the water is made.

    Every twin already prepares domestic hot water on its own generator, so
    ``together_with_heating_system`` -- and ``separate_heat_pump`` on a heat-pump house, which
    is the same machine -- is ``used`` without anything being written. The other combinations
    have no component and are on the list.
    """
    hot_water = state.house.hot_water
    generator = state.house.heating.type_of_system
    component = BaseFiles.generator_component(state.base_file_name)
    if hot_water is None or hot_water.supply is None:
        state.report.defaulted(
            "house.hot_water",
            {"supply": "together_with_heating_system"},
            note="absent from the request; the generator that heats the rooms makes the hot water",
            target=Targets.describe(component, Targets.DOMESTIC_HOT_WATER),
        )
        if hot_water is None:
            return
        _hot_water_storage(state, hot_water)
        return
    entry = state.whitelist.match(Unmapped("house.hot_water.supply", hot_water.supply.value), state.raw)
    if entry is not None:
        state.report.not_implemented_yet(
            "house.hot_water.supply", entry.note, value=hot_water.supply.value
        )
    else:
        state.report.used(
            "house.hot_water.supply",
            Targets.describe(component, Targets.DOMESTIC_HOT_WATER),
            value=hot_water.supply.value,
            note=(
                "the twin's generator already prepares the domestic hot water"
                if generator not in BaseFiles.HEAT_PUMPS
                else "the heat pump prepares the domestic hot water itself"
            ),
        )
    _hot_water_storage(state, hot_water)


def _hot_water_storage(state: _TranslationState, hot_water: Any) -> None:
    """Write the storage volume and the tank insulation, which are the block's other two fields."""
    if hot_water.volume_heating_water_storage_in_liter is not None:
        state.write(
            Targets.DHW_STORAGE,
            Targets.STORAGE_VOLUME,
            hot_water.volume_heating_water_storage_in_liter,
            source="house.hot_water.volume_heating_water_storage_in_liter",
            note="the stated storage volume pins the field HiSim would otherwise size",
        )
        state.report.used(
            "house.hot_water.volume_heating_water_storage_in_liter",
            Targets.describe(Targets.DHW_STORAGE, Targets.STORAGE_VOLUME),
            value=hot_water.volume_heating_water_storage_in_liter,
        )
    if hot_water.tank_and_pipe_insulated:
        state.write(
            Targets.DHW_STORAGE,
            Targets.STORAGE_HEAT_TRANSFER,
            StorageDefaults.TANK_INSULATED_HEAT_TRANSFER,
            source="house.hot_water.tank_and_pipe_insulated",
            note="half the class default; the pipe losses have no HiSim parameter",
        )
    if state.present("house.hot_water.tank_and_pipe_insulated"):
        state.report.approximated(
            "house.hot_water.tank_and_pipe_insulated",
            "the storage's heat transfer coefficient is halved; the pipe losses have no target",
            target=Targets.describe(Targets.DHW_STORAGE, Targets.STORAGE_HEAT_TRANSFER),
            value=StorageDefaults.TANK_INSULATED_HEAT_TRANSFER if hot_water.tank_and_pipe_insulated else None,
        )


def _comfort(state: _TranslationState) -> None:
    """Report ventilation, the heating control, air conditioning and the appliances.

    None of the four reaches a twin in the MVP. Ventilation and airtightness have no ``Building``
    parameter at all; the night-setback and air-conditioner groups the frontend side's spec
    wanted are base-file work that starts after the MVP runs (decision D-D). The values that
    *are* implemented -- natural ventilation, the as-built envelope, traditional thermostats --
    are ``used``, because they describe exactly what the twin already simulates.
    """
    if state.present("house.ventilation.type_of_system"):
        value = state.house.ventilation_type.value if state.house.ventilation_type else None
        entry = state.whitelist.match(Unmapped("house.ventilation.type_of_system", value), state.raw)
        if entry is None:
            state.report.used(
                "house.ventilation.type_of_system",
                "Building (the TABULA row's air-change rate)",
                value=value,
                note="natural ventilation is what the archetype's air-change rate already describes",
            )
        else:
            state.report.not_implemented_yet("house.ventilation.type_of_system", entry.note, value=value)
    if state.present("house.ventilation.air_tightness"):
        value = state.house.air_tightness.value if state.house.air_tightness else None
        entry = state.whitelist.match(Unmapped("house.ventilation.air_tightness", value), state.raw)
        if entry is None:
            state.report.used(
                "house.ventilation.air_tightness",
                "Building (the TABULA row's infiltration)",
                value=value,
                note="the as-built envelope is what the archetype already describes",
            )
        else:
            state.report.not_implemented_yet("house.ventilation.air_tightness", entry.note, value=value)
    if state.present("house.temperature_control.type_of_system"):
        value = state.house.temperature_control.value if state.house.temperature_control else None
        entry = state.whitelist.match(
            Unmapped("house.temperature_control.type_of_system", value), state.raw
        )
        if entry is None:
            state.report.used(
                "house.temperature_control.type_of_system",
                "Building (the constant set point)",
                value=value,
                note="a plain thermostat is what a constant set point already describes",
            )
        else:
            state.report.not_implemented_yet(
                "house.temperature_control.type_of_system", entry.note, value=value
            )
    if state.present("house.air_conditioning"):
        state.listed("house.air_conditioning.power_in_watt", state.house.air_conditioning_power_in_watt)


def _devices(state: _TranslationState) -> None:
    """Write the four optional devices: the array, the battery, the collector and the cars."""
    _photovoltaics(state)
    _battery(state)
    _solar_thermal(state)
    if state.present("house.electric_vehicles"):
        state.listed("house.electric_vehicles.number", state.house.electric_vehicles.number
                     if state.house.electric_vehicles else None)
        for name in ("commuting_distance_in_km", "charging_power_in_watt"):
            path = f"house.electric_vehicles.{name}"
            if state.present(path):
                state.listed(path, getattr(state.house.electric_vehicles, name))


def _photovoltaics(state: _TranslationState) -> None:
    """Size, orient and, when the house has no array, silence the array every twin carries.

    Decision D-D: no twin has a ``pv`` group, so "no photovoltaics" is a zero-power pin on the
    array rather than a group that is switched off. A recorded value on a sized field pins it,
    so the array stays in the file and produces nothing, which is verified to run.
    """
    array = state.house.pv_system
    roof = state.house.building.roof
    shape = roof.shape.value if roof.shape is not None else RoofDefaults.SHAPE
    tilt = array.tilt if array is not None and array.tilt is not None else RoofDefaults.TILT_BY_ROOF_SHAPE[shape]
    azimuth = array.azimuth if array is not None and array.azimuth is not None else RoofDefaults.AZIMUTH
    state.write(Targets.PV, Targets.AZIMUTH, azimuth, source="house.pv_system.azimuth",
                note="180 degrees is due south")
    state.write(Targets.PV, Targets.TILT, tilt, source="house.pv_system.tilt",
                note=f"the tilt of a {shape} roof")
    if array is None:
        state.write(
            Targets.PV,
            Targets.POWER_IN_WATT,
            0,
            source="house.pv_system",
            note="the house has no array and no twin has a pv group, so the array is pinned to zero",
        )
        state.report.defaulted(
            "house.pv_system",
            0,
            note=(
                "the request carries no photovoltaic array and no recorded base file has a pv "
                "group, so the array every twin carries is pinned to zero watt and produces nothing"
            ),
            target=Targets.describe(Targets.PV, Targets.POWER_IN_WATT),
        )
        return
    if array.size_in_percent_of_roof_area is not None:
        share = array.size_in_percent_of_roof_area / Targets.PERCENT
        state.write(Targets.PV, Targets.SHARE_OF_ROOF, share, source="house.pv_system.size_in_percent_of_roof_area",
                    note="HiSim sizes the array from the roof area and this share")
        state.report.used(
            "house.pv_system.size_in_percent_of_roof_area",
            Targets.describe(Targets.PV, Targets.SHARE_OF_ROOF),
            value=share,
            note="the power stays AUTO and moves with the roof",
        )
    if array.power_in_watt is not None:
        state.write(Targets.PV, Targets.POWER_IN_WATT, array.power_in_watt,
                    source="house.pv_system.power_in_watt", note="the stated peak power pins the array")
        state.report.used(
            "house.pv_system.power_in_watt",
            Targets.describe(Targets.PV, Targets.POWER_IN_WATT),
            value=array.power_in_watt,
        )
    for name, value in (("azimuth", azimuth), ("tilt", tilt)):
        path = f"house.pv_system.{name}"
        target = Targets.describe(Targets.PV, name)
        if state.present(path):
            state.report.used(path, target, value=value)
        else:
            state.report.defaulted(
                path, value,
                note=("due south" if name == "azimuth" else f"the tilt of a {shape} roof"),
                target=target,
            )


def _battery(state: _TranslationState) -> None:
    """Select the electricity-management variant, and pin the battery when the house has one.

    A house without a battery runs ``metered_directly``: the twins' default is
    ``ems_with_battery``, whose battery sizes itself from the array, and with an array pinned to
    zero the battery library divides by zero. That is the fix of ``c02bc801``, carried over.
    """
    battery = state.house.battery
    if battery is None:
        state.select(
            Targets.ELECTRICITY_MANAGEMENT,
            Targets.METERED_DIRECTLY,
            source="house.battery",
            note="the house has no battery, so the meter sees the array and the household directly",
        )
        state.report.defaulted(
            "house.battery",
            Targets.METERED_DIRECTLY,
            note=(
                "the request carries no battery, so the electricity management is the plain "
                "meter rather than the twin's default energy management system with a battery"
            ),
            target=DocumentPaths.variant_selection(Targets.ELECTRICITY_MANAGEMENT),
        )
        return
    state.select(
        Targets.ELECTRICITY_MANAGEMENT,
        Targets.WITH_BATTERY,
        source="house.battery",
        note="the house has a battery, so the energy management system runs with one",
    )
    if battery.custom_battery_capacity_generic_in_kilowatt_hour is not None:
        capacity = battery.custom_battery_capacity_generic_in_kilowatt_hour
        path = "house.battery.custom_battery_capacity_generic_in_kilowatt_hour"
        state.report.used(
            path, Targets.describe(Targets.BATTERY, Targets.BATTERY_CAPACITY), value=capacity
        )
    else:
        capacity, note = BatteryCapacityLaw.capacity_of(int(battery.days_to_cover or 0))
        state.report.approximated(
            "house.battery.days_to_cover",
            note,
            target=Targets.describe(Targets.BATTERY, Targets.BATTERY_CAPACITY),
            value=capacity,
        )
    state.write(Targets.BATTERY, Targets.BATTERY_CAPACITY, capacity, source="house.battery",
                note="the usable capacity")
    state.write(
        Targets.BATTERY,
        Targets.BATTERY_INVERTER,
        capacity * BatteryLaw.INVERTER_WATT_PER_KILOWATT_HOUR,
        source="house.battery",
        note=(
            f"{BatteryLaw.INVERTER_WATT_PER_KILOWATT_HOUR:g} W per kWh, the class's own C-rate of "
            "0.5, pinned so that the two move together"
        ),
    )


def _solar_thermal(state: _TranslationState) -> None:
    """Write the collector when the twin wires one, and report it when the twin does not."""
    collector = state.house.solar_thermal_system
    if collector is None:
        return
    if not BaseFiles.has_solar_thermal_wiring(state.house.heating.type_of_system):
        state.listed("house.solar_thermal_system", None)
        return
    for name, value in (
        ("supplies", collector.supplies.value),
        ("collector_type", collector.collector_type.value if collector.collector_type else None),
    ):
        path = f"house.solar_thermal_system.{name}"
        if not state.present(path) and name != "supplies":
            continue
        entry = state.whitelist.match(Unmapped(path, value), state.raw)
        if entry is not None:
            state.report.not_implemented_yet(path, entry.note, value=value)
        else:
            state.report.used(
                path,
                f"{Targets.SOLAR_THERMAL} (the twin's recorded wiring and preset)",
                value=value,
                note="the collector feeds the hot-water storage, which is what the twin wires",
            )
    if collector.area_m2 is not None:
        state.write(Targets.SOLAR_THERMAL, Targets.COLLECTOR_AREA, collector.area_m2,
                    source="house.solar_thermal_system.area_m2", note="the stated absorber area")
        state.report.used(
            "house.solar_thermal_system.area_m2",
            Targets.describe(Targets.SOLAR_THERMAL, Targets.COLLECTOR_AREA),
            value=collector.area_m2,
        )
    if collector.storage_volume_in_liter is not None:
        state.write(
            Targets.DHW_STORAGE,
            Targets.STORAGE_VOLUME,
            collector.storage_volume_in_liter,
            source="house.solar_thermal_system.storage_volume_in_liter",
            note="the same storage the hot-water block names",
        )
        state.report.used(
            "house.solar_thermal_system.storage_volume_in_liter",
            Targets.describe(Targets.DHW_STORAGE, Targets.STORAGE_VOLUME),
            value=collector.storage_volume_in_liter,
        )
    roof = state.house.building.roof
    shape = roof.shape.value if roof.shape is not None else RoofDefaults.SHAPE
    state.write(Targets.SOLAR_THERMAL, Targets.AZIMUTH, RoofDefaults.AZIMUTH,
                source="house.building.roof", note="due south, as for the array")
    state.write(Targets.SOLAR_THERMAL, Targets.TILT, RoofDefaults.TILT_BY_ROOF_SHAPE[shape],
                source="house.building.roof.shape", note=f"the tilt of a {shape} roof")


def _unmodelled(state: _TranslationState) -> None:
    """Report the leaves no stage claimed: the schema version and everything on the list.

    This is the fail-loud rule in one place. Every leaf the request carried that still has no
    line is asked of ``not_implemented_yet.yaml``, and a leaf the list does not carry fails the
    translator's build. Nothing is dropped, and nothing is refused.
    """
    state.report.used(
        "schema_version",
        "the translator itself",
        value=state.request.schema_version,
        note=f"this build implements calculation-request schema version {state.request.schema_version}",
    )
    for path in MappingReport.request_leaves(state.request.document):
        if state.report.has(path):
            continue
        if any(path.startswith(f"{recorded}.") for recorded in ("house.air_conditioning",)):
            continue
        state.listed(path, _leaf_value(state.request.document, path))


def _leaf_value(document: Mapping[str, Any], path: str) -> Any:
    """Return the value a request carries at one dotted path, or ``None`` when it carries none."""
    current: Any = document
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def translate(
    request: Request,
    applied: AppliedPackage,
    base_files_directory: Path,
    whitelist: Optional[Whitelist] = None,
) -> TranslatedSystem:
    """Translate one renovated house with the committed tables, in one call.

    Args:
        request: The validated request.
        applied: What applying the package came to.
        base_files_directory: Where the recorded twins live.
        whitelist: The parsed list; the committed one when omitted.

    Returns:
        The translated system.

    Raises:
        TranslatorError: For an unmapped and unlisted leaf, a broken diff rule or a file that
            does not load back.
    """
    return Translator(base_files_directory, whitelist).translate(request, applied)
