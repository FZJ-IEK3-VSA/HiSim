"""Writing one dwelling's values into one recorded energy-system file.

This is the step that turns everything the pure layers decided into a file HiSim can run. It
starts from a *base file* — one of the recorded ``energy_systems/household_*.grouped.energy_system.
yaml`` documents, reviewed and wired by hand — and writes the dwelling's own numbers into it::

    parametrised = Parametriser(Path("energy_systems")).parametrise(result, estimator)
    Path("parametrised.energy_system.yaml").write_text(parametrised.yaml_text)

What it may write is deliberately narrow (requirement R4): configuration values, the arguments of
a named constructor, the selection of a variant and the flag of a group. It must never author an
``inputs`` item, a ``sizing_sources`` block or a component entry, because those are the reviewed
content of the base file and a library that writes them has quietly become a second, untested
system description. :meth:`ParametrisedSystem.assert_only_permitted_edits` is that rule as a
check rather than as a promise, and it runs on every parametrisation.

Four kinds of edit happen, in a fixed order:

1. **Switches** — the variants and groups the measures asked for.
2. **Constructor swaps** — three components are parameterised by an identifier rather than by a
   variant (a building by its TABULA code, a weather by its station, an occupancy by its household
   reference), so their recorded preset is replaced by the named constructor that takes it, and
   every recorded ``config`` key that constructor now supplies is removed. The removal matters
   most for ``weather_identity``: it is a *sized* field, a recorded value on it pins it, and a
   pinned Aachen identity on a building standing in Dublin would silence the very law that keeps
   the building's cache honest.
3. **Configuration values** — every inventory leaf the bindings map to a config field of a
   component the selected file has. A leaf whose component the file lacks is reported ``ignored``
   when it merely describes the dwelling, and refused when a measure asked for it.
4. **Sizing laws** — the values the measure layer left pending, resolved by
   :class:`~hisim.renovisor.laws.LawResolver` and written like any other configuration value.

The result is dumped with the format's own canonical emitter, so two runs of the same request
produce byte-identical text (requirement R10) and a reviewer diffing it against the base file sees
only the four kinds of change above.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import AbstractSet, Any, Callable, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple, Type

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
from hisim.renovisor.application import ApplicationResult
from hisim.renovisor.bindings import BindingKind, Bindings, LeafBinding, Target
from hisim.renovisor.envelope import InventoryThenTabula
from hisim.renovisor.inventory import Inventory
from hisim.renovisor.laws import DemandEstimator, LawResolver
from hisim.renovisor.occupancy import HouseholdMatch, HouseholdMatcher, HouseholdMatchReport
from hisim.renovisor.reasons import ReasonCode, RefusalDetail, RefusalError
from hisim.renovisor.report import MappingReport


class ParametriserError(Exception):
    """Raised when a parametrised file differs from its base file in a way requirement R4 forbids.

    It is never a caller's fault: the caller asked for a renovation, and the library produced a
    file that authored wiring, sizing sources or a component. It exists so that such a bug fails
    at the point it happens rather than as a puzzling simulation result three stages later.

    Args:
        difference: What differed, named by its place in the document.
    """

    def __init__(self, difference: str) -> None:
        """Store the offending difference and build the message that names requirement R4."""
        super().__init__(
            f"the parametrised file differs from its base file in {difference}; a parametrised file "
            "may differ only in config values, the constructor swaps of the three identifier-"
            "parameterised components, variant selections, group flags, its name and its "
            "description (requirement R4)"
        )
        self.difference = difference


class EditKind(str, Enum):
    """What kind of change one :class:`Edit` is.

    They are the four kinds requirement R4 permits plus the document's own name and description,
    which carry no simulated meaning. The trace page groups the YAML diff by them, and the diff
    check is written against the same list, so a fifth kind cannot appear in one without the other.
    """

    VARIANT_SELECTION = "VARIANT_SELECTION"
    GROUP_FLAG = "GROUP_FLAG"
    CONSTRUCTOR_SWAP = "CONSTRUCTOR_SWAP"
    CONFIG_VALUE = "CONFIG_VALUE"
    DOCUMENT = "DOCUMENT"


@dataclass(frozen=True)
class Edit:
    """One change the parametriser made, with what asked for it.

    Every edit carries its provenance because the translation report and the trace page both have
    to answer "why is this line different from the base file?", and the answer is always either an
    inventory path, a measure id or a sizing law.

    Args:
        kind: Which of the permitted kinds of change this is.
        location: Where in the document it landed, dotted, e.g.
            ``components.Building.config.roof_u_value_in_watt_per_m2_per_kelvin``.
        value: The value written; ``None`` for a removal.
        source: The inventory path, measure id or law name that asked for it.
        note: One phrase a person can read.
    """

    kind: EditKind
    location: str
    value: Any
    source: str
    note: str


class DocumentPaths:
    """The dotted spellings of the places in an energy-system document the parametriser touches.

    Spelled once so that an :class:`Edit`'s location, the diff check's messages and the trace
    page's hunk annotations all name the same place the same way.
    """

    #: The top-level blocks.
    COMPONENTS: ClassVar[str] = "components"
    GROUPS: ClassVar[str] = "groups"
    VARIANTS: ClassVar[str] = "variants"

    #: The per-entry blocks the parametriser writes into.
    CONFIG: ClassVar[str] = "config"
    PRESET: ClassVar[str] = "preset"
    CONSTRUCTOR: ClassVar[str] = "constructor"

    #: The per-entry blocks a parametrised file may never differ in: what a component *is*, where
    #: its inputs come from and where its sizing facts come from are the base file's reviewed
    #: content (requirement R4). :class:`DiffRule` refuses a difference in any of them.
    IMMUTABLE_ENTRY_KEYS: ClassVar[Tuple[str, ...]] = ("class", "inputs", "sizing_sources")

    @classmethod
    def config_field(cls, component: str, field: str) -> str:
        """Return the dotted location of one config field, e.g. ``components.Building.config.tilt``."""
        return f"{cls.COMPONENTS}.{component}.{cls.CONFIG}.{field}"

    @classmethod
    def constructor_of(cls, component: str) -> str:
        """Return the dotted location of one component's constructor block."""
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


@dataclass(frozen=True)
class ConstructorSwap:
    """One component whose recorded preset is replaced by a named constructor.

    Three components of every base file are parameterised by an identifier out of an open space —
    a TABULA code, a weather station, a LoadProfileGenerator household — rather than by a handful
    of variants, which is why HiSim gives each of them a named constructor and why a preset cannot
    express what a particular dwelling needs.

    Args:
        component_key: The recorded key in the base file, e.g. ``"Building"``.
        constructor: The constructor's name, e.g. ``"for_tabula_code"``.
        consumed_inventory_paths: The inventory leaves the constructor's arguments are built from;
            they are reported as used here and not written again as config values.
        removed_config_keys: Recorded ``config`` keys the constructor now supplies, which are
            dropped so that a recorded value cannot override the value this dwelling needs.
    """

    component_key: str
    constructor: str
    consumed_inventory_paths: Tuple[str, ...]
    removed_config_keys: Tuple[str, ...]


class ConstructorSwaps:
    """The three swaps, and what each of them consumes and removes.

    Held as data rather than as three methods so that the diff check can ask "is this component
    allowed to have had its preset swapped?" from the same list the swaps are performed from.
    """

    #: The building: its TABULA code is assembled from three inventory facts, and its floor area,
    #: apartment count and thermal-mass class are constructor arguments beside it. Removing the
    #: recorded ``weather_identity`` is the point of the whole swap: it is a sized field, so a
    #: recorded value pins it, and a building in Dublin carrying Aachen's weather identity keys
    #: its solar-gain cache on a climate it is not being simulated in.
    BUILDING: ClassVar[ConstructorSwap] = ConstructorSwap(
        component_key="Building",
        constructor="for_tabula_code",
        consumed_inventory_paths=(
            "building_config.general.tabula_building_type",
            "building_config.general.construction_year",
            "building_config.general.retrofit_status",
            "building_config.general.conditioned_floor_area_m2",
            "building_config.general.number_of_apartments",
            "building_config.general.building_heat_capacity_class",
        ),
        removed_config_keys=("weather_identity", "number_of_apartments", "building_heat_capacity_class"),
    )

    #: The weather: the recorded entry carries a complete configuration naming Aachen's files, and
    #: the station's own constructor knows where every catalogue station's data lives, so the
    #: recorded location, path and reader all go.
    WEATHER: ClassVar[ConstructorSwap] = ConstructorSwap(
        component_key="Weather",
        constructor="for_location",
        consumed_inventory_paths=("location.country_code",),
        removed_config_keys=("location", "source_path", "data_source"),
    )

    #: The occupancy: the recorded preset is one fixed household, and this dwelling's residents
    #: pick another. The acquisition mode is passed to the constructor rather than left as an
    #: override alone, because the constructor reads it to decide whether a shipped profile can
    #: serve the household at all.
    OCCUPANCY: ClassVar[ConstructorSwap] = ConstructorSwap(
        component_key="UTSPConnector",
        constructor="for_household",
        consumed_inventory_paths=(
            "occupancy_config.residents_count",
            "occupancy_config.residents_type",
            "occupancy_config.residents_employment_status",
            "occupancy_config.travel_route_set",
        ),
        removed_config_keys=(),
    )

    #: All three, in the order they are applied.
    ALL: ClassVar[Tuple[ConstructorSwap, ...]] = (BUILDING, WEATHER, OCCUPANCY)

    @classmethod
    def component_keys(cls) -> Tuple[str, ...]:
        """Return the recorded keys of the three swapped components, sorted."""
        return tuple(sorted(swap.component_key for swap in cls.ALL))

    @classmethod
    def consumed_paths(cls) -> Tuple[str, ...]:
        """Return every inventory path the three constructors consume, sorted."""
        return tuple(sorted({path for swap in cls.ALL for path in swap.consumed_inventory_paths}))


class StaleLeaves:
    """Inventory leaves that the post-measure state has made meaningless, and why.

    Both entries here are the same shape of problem: the inventory describes the dwelling *as it
    is*, one leaf of it states a device size, and a measure has replaced the device that size
    belonged to. Writing the old size onto the new device would silently size the renovation from
    the thing it replaced, so the leaf is reported ``ignored`` instead, naming what superseded it.

    There is deliberately no number here and no rule about *what* size to use instead: the base
    file's own sizing law does that, which is the whole reason the recorded files are the base
    files (decision Q18).
    """

    #: The photovoltaic array's installed power, superseded when a measure states the share of the
    #: roof to cover, because the recorded file's ``rooftop`` law sizes the array from the share.
    PV_POWER: ClassVar[str] = "energy_system_config.photovoltaics.power_in_watt"
    PV_SHARE: ClassVar[str] = "energy_system_config.photovoltaics.share_of_maximum_pv_potential"

    #: The heat generator's rated power, superseded when a measure replaced the generator, because
    #: the recorded file's generator is sized for the building rather than for the old boiler.
    GENERATOR_POWER: ClassVar[str] = "energy_system_config.heating_system.power_in_watt"

    @classmethod
    def reason_for(
        cls, path: str, inventory: Inventory, generator_changed: bool
    ) -> Optional[str]:
        """Return why this leaf is stale, or ``None`` when it is not.

        Args:
            path: The inventory leaf being considered.
            inventory: The post-measure inventory.
            generator_changed: Whether a measure replaced the dwelling's heat generator.

        Returns:
            The note for the ``ignored`` report line, or ``None``.
        """
        if path == cls.PV_POWER and inventory.has(cls.PV_SHARE):
            return (
                "superseded by the requested share of the roof; the base file's rooftop sizing law "
                "computes the array's power from it"
            )
        if path == cls.GENERATOR_POWER and generator_changed:
            return (
                "superseded by the heating-system measure; it rates the generator that was "
                "replaced, and the base file sizes the new one from the building"
            )
        return None


@dataclass(frozen=True)
class ParametrisedSystem:
    """One base file with one dwelling's values written into it.

    Args:
        model: The edited document.
        base_file_name: The recorded file it came from, without a directory.
        yaml_text: The document as canonical YAML, which is what is written to disk and what makes
            two runs of the same request byte-identical (requirement R10).
        edits: Every change, with the inventory path, measure id or law that asked for it.
    """

    model: EnergySystemFile
    base_file_name: str
    yaml_text: str
    edits: Tuple[Edit, ...]

    def edits_of(self, kind: EditKind) -> Tuple[Edit, ...]:
        """Return every edit of one kind, in the order they were made."""
        return tuple(edit for edit in self.edits if edit.kind is kind)

    def assert_only_permitted_edits(self, base: EnergySystemFile) -> None:
        """Check the parametrised document against requirement R4, and raise if it breaks it.

        The check is mechanical and structural rather than textual: both documents are rendered
        into the plain nested mappings the emitter writes and walked side by side, so a difference
        cannot hide in formatting and a new kind of difference cannot pass for an old one.

        What may differ: a component's ``config`` values; the ``preset`` and ``constructor`` of
        the three components :class:`ConstructorSwaps` names; a variant's ``selected``; a group's
        ``enabled``; the document's ``name`` and ``description``. Everything else — the schema
        version, the metadata block, the set of components, every ``class``, every ``inputs``
        item, every ``sizing_sources`` block, the set of variants, groups and options — must be
        identical.

        Args:
            base: The base file as it was loaded, before any edit.

        Raises:
            ParametriserError: On the first difference the rule does not permit, naming where it
                is.
        """
        DiffRule.check(
            EnergySystemEmitter.to_document(base), EnergySystemEmitter.to_document(self.model)
        )


class DiffRule:
    """The mechanical form of requirement R4: which differences a parametrised file may carry.

    It is written as a walk over two rendered documents rather than as a text diff because the
    question is structural — "did anything author an input item?" — and a text diff would answer
    it only as accurately as the formatting allows. The first difference that is not permitted
    raises, naming its place, so a bug in the parametriser reads as one sentence.
    """

    #: The top-level keys a parametrised document may differ in.
    MUTABLE_TOP_LEVEL_KEYS: ClassVar[Tuple[str, ...]] = ("name", "description")

    #: The entry keys a parametrised entry may differ in, for the three swapped components.
    SWAPPABLE_ENTRY_KEYS: ClassVar[Tuple[str, ...]] = ("preset", "constructor", "config")

    @classmethod
    def check(cls, base: Mapping[str, Any], parametrised: Mapping[str, Any]) -> None:
        """Compare two rendered documents and raise on the first forbidden difference.

        Args:
            base: The base file as :meth:`~hisim.energy_system.emitter.EnergySystemEmitter.
                to_document` renders it.
            parametrised: The parametrised file, rendered the same way.

        Raises:
            ParametriserError: On the first difference requirement R4 does not permit.
        """
        cls._same_keys(base, parametrised, "the document's top level")
        for key in base:
            if key in cls.MUTABLE_TOP_LEVEL_KEYS:
                continue
            if key == DocumentPaths.COMPONENTS:
                cls._components(base[key], parametrised[key], DocumentPaths.COMPONENTS)
            elif key == DocumentPaths.GROUPS:
                cls._groups(base[key], parametrised[key])
            elif key == DocumentPaths.VARIANTS:
                cls._variants(base[key], parametrised[key])
            elif base[key] != parametrised[key]:
                raise ParametriserError(f"the top-level key '{key}'")

    @classmethod
    def _components(
        cls, base: Mapping[str, Any], parametrised: Mapping[str, Any], location: str
    ) -> None:
        """Compare two component mappings: the same names, each entry differing only where allowed."""
        cls._same_keys(base, parametrised, location)
        for name in base:
            cls._entry(base[name], parametrised[name], f"{location}.{name}", name)

    @classmethod
    def _entry(
        cls, base: Mapping[str, Any], parametrised: Mapping[str, Any], location: str, name: str
    ) -> None:
        """Compare two rendered entries of one component."""
        swappable = name in ConstructorSwaps.component_keys()
        for key in sorted(set(base) | set(parametrised)):
            immutable = key in DocumentPaths.IMMUTABLE_ENTRY_KEYS
            if not immutable and key == DocumentPaths.CONFIG:
                continue
            if not immutable and key in cls.SWAPPABLE_ENTRY_KEYS and swappable:
                continue
            if base.get(key) != parametrised.get(key):
                raise ParametriserError(f"'{location}.{key}'")

    @classmethod
    def _groups(cls, base: Mapping[str, Any], parametrised: Mapping[str, Any]) -> None:
        """Compare two group mappings: the same names, only the flags free to differ."""
        cls._same_keys(base, parametrised, DocumentPaths.GROUPS)
        for name in base:
            cls._components(
                base[name]["components"],
                parametrised[name]["components"],
                f"{DocumentPaths.GROUPS}.{name}.components",
            )

    @classmethod
    def _variants(cls, base: Mapping[str, Any], parametrised: Mapping[str, Any]) -> None:
        """Compare two variant mappings: the same names and options, only the selection free."""
        cls._same_keys(base, parametrised, DocumentPaths.VARIANTS)
        for name in base:
            options_location = f"{DocumentPaths.VARIANTS}.{name}.options"
            cls._same_keys(base[name]["options"], parametrised[name]["options"], options_location)
            for option in base[name]["options"]:
                cls._components(
                    base[name]["options"][option]["components"],
                    parametrised[name]["options"][option]["components"],
                    f"{options_location}.{option}.components",
                )

    @classmethod
    def _same_keys(cls, base: Mapping[str, Any], parametrised: Mapping[str, Any], location: str) -> None:
        """Raise when two mappings do not carry exactly the same keys."""
        added = sorted(set(parametrised) - set(base))
        removed = sorted(set(base) - set(parametrised))
        if added:
            raise ParametriserError(f"{location}: it adds {', '.join(added)}")
        if removed:
            raise ParametriserError(f"{location}: it removes {', '.join(removed)}")


class Parametriser:
    """Writes one :class:`~hisim.renovisor.application.ApplicationResult` into its base file.

    Args:
        base_files_directory: Where the recorded ``*.grouped.energy_system.yaml`` files live.
        bindings: The table saying which component owns which inventory leaf. The committed table
            unless a test hands in another.
        tabula: How a building's TABULA archetype is selected from an inventory; the committed
            rule unless a test hands in another.
        matcher: How the dwelling's residents become a LoadProfileGenerator household.
        laws: How a pending sizing law becomes a number.
    """

    #: The name every parametrised document carries, with the base file's stem filled in. It has
    #: no timestamp and no hash, because two runs of the same request must produce the same bytes
    #: (requirement R10).
    NAME_TEMPLATE: ClassVar[str] = "renovisor {stem}"

    #: The description, likewise fixed.
    DESCRIPTION_TEMPLATE: ClassVar[str] = (
        "The recorded {stem} system with this dwelling's inventory values and its renovation "
        "package written into it by the RenoVisor translation layer."
    )

    #: The suffix every recorded base file carries, stripped to get the stem for the two templates.
    FILE_SUFFIX: ClassVar[str] = ".energy_system.yaml"

    #: The config field the occupancy's acquisition mode is recorded under, which is read off the
    #: base file and handed to the constructor as well: the constructor consults it to decide
    #: whether a shipped profile can serve the chosen household.
    ACQUISITION_MODE_FIELD: ClassVar[str] = "data_acquisition_mode"

    def __init__(
        self,
        base_files_directory: Path,
        bindings: Type[Bindings] = Bindings,
        tabula: Optional[Callable[[Inventory], InventoryThenTabula]] = None,
        matcher: Optional[HouseholdMatcher] = None,
        laws: Type[LawResolver] = LawResolver,
    ) -> None:
        """Store the directory and the four collaborators, defaulting each to the committed one."""
        self._directory = Path(base_files_directory)
        self._bindings = bindings
        self._tabula = tabula or InventoryThenTabula
        self._matcher = matcher or HouseholdMatcher()
        self._laws = laws

    def parametrise(self, result: ApplicationResult, estimator: DemandEstimator) -> ParametrisedSystem:
        """Write one application result into its base file.

        Args:
            result: What applying the package produced: the post-measure inventory, the base file,
                the switches, the pending laws and the report.
            estimator: Where the daily demands a sizing law reads come from.

        Returns:
            The :class:`ParametrisedSystem`, its diff rule already asserted.

        Raises:
            RefusalError: When the selected base file cannot carry something the package asked
                for — an unknown variant or option, a component a requested measure needs, or a
                country with no weather station.
            ParametriserError: When the parametrisation produced a difference requirement R4 does
                not permit, which is a bug in this class rather than in the request.
        """
        base = load_energy_system(self._directory / result.base_file_name)
        editor = SystemEditor(base)
        edits: List[Edit] = []
        refusals: List[RefusalDetail] = []
        report = result.report

        self._apply_switches(result, editor, edits, refusals)
        if refusals:
            raise RefusalError(refusals)
        match = self._apply_constructors(result, editor, edits, refusals, report)
        if refusals:
            raise RefusalError(refusals)
        self._apply_config_values(result, editor, edits, refusals, report)
        self._apply_laws(result, editor, edits, report, estimator)
        if refusals:
            raise RefusalError(refusals)
        del match

        stem = self._stem(result.base_file_name)
        editor.set_document(
            self.NAME_TEMPLATE.format(stem=stem), self.DESCRIPTION_TEMPLATE.format(stem=stem)
        )
        edits.append(
            Edit(
                kind=EditKind.DOCUMENT,
                location="name",
                value=self.NAME_TEMPLATE.format(stem=stem),
                source="the translation layer",
                note="a fixed name, carrying nothing that varies per request",
            )
        )
        model = editor.build()
        parametrised = ParametrisedSystem(
            model=model,
            base_file_name=result.base_file_name,
            yaml_text=dump_energy_system(model),
            edits=tuple(edits),
        )
        parametrised.assert_only_permitted_edits(base)
        return parametrised

    @classmethod
    def _stem(cls, base_file_name: str) -> str:
        """Return a base file's name without the format's suffix, for the name and description."""
        if base_file_name.endswith(cls.FILE_SUFFIX):
            return base_file_name[: -len(cls.FILE_SUFFIX)]
        return Path(base_file_name).stem

    def _apply_switches(
        self,
        result: ApplicationResult,
        editor: SystemEditor,
        edits: List[Edit],
        refusals: List[RefusalDetail],
    ) -> None:
        """Select the variants and enable the groups the measures asked for.

        A variant or option the base file does not have is a refusal rather than an error: the
        package asked for something this recorded file cannot be, which is exactly what
        ``NO_BASE_FILE_FOR_COMBINATION`` means.
        """
        for variant, option in sorted(result.variant_selections.items()):
            try:
                editor.select_variant(variant, option)
            except KeyError:
                refusals.append(
                    RefusalDetail(
                        reason=ReasonCode.NO_BASE_FILE_FOR_COMBINATION,
                        path="package.measures",
                        detail=(
                            f"base file {result.base_file_name} has no variant '{variant}' with an "
                            f"option '{option}'"
                        ),
                    )
                )
                continue
            edits.append(
                Edit(
                    kind=EditKind.VARIANT_SELECTION,
                    location=DocumentPaths.variant_selection(variant),
                    value=option,
                    source="package.measures",
                    note=f"a measure selected the '{option}' world of '{variant}'",
                )
            )
        for group in sorted(result.enabled_groups):
            try:
                editor.enable_group(group, True)
            except KeyError:
                refusals.append(
                    RefusalDetail(
                        reason=ReasonCode.NO_BASE_FILE_FOR_COMBINATION,
                        path="package.measures",
                        detail=f"base file {result.base_file_name} has no group '{group}'",
                    )
                )
                continue
            edits.append(
                Edit(
                    kind=EditKind.GROUP_FLAG,
                    location=DocumentPaths.group_flag(group),
                    value=True,
                    source="package.measures",
                    note=f"a measure switched the '{group}' group on",
                )
            )

    def _apply_constructors(
        self,
        result: ApplicationResult,
        editor: SystemEditor,
        edits: List[Edit],
        refusals: List[RefusalDetail],
        report: MappingReport,
    ) -> HouseholdMatch:
        """Swap the three identifier-parameterised components onto their named constructors."""
        inventory = result.inventory
        self._swap_building(inventory, editor, edits, report)
        self._swap_weather(inventory, editor, edits, refusals, report)
        match = self._swap_occupancy(inventory, editor, edits, report)
        self._apply_constructor_side_fields(inventory, editor, edits)
        return match

    def _apply_constructor_side_fields(
        self, inventory: Inventory, editor: SystemEditor, edits: List[Edit]
    ) -> None:
        """Write the plain config fields that a constructor-argument leaf also reaches.

        One leaf reaches a constructor of one component and a plain field of another: the country
        code picks the weather station *and* labels the photovoltaic array. The swap above handled
        the constructor half, and this handles the other, so that the file never keeps the recorded
        Aachen label on an array standing in Dublin.

        A target on a swapped component itself is deliberately skipped: its constructor has just
        supplied that value, and writing it again as an override would say the same thing twice in
        the file and make the swap look like it had not taken.

        Args:
            inventory: The post-measure inventory.
            editor: The file being edited.
            edits: The running list of changes, appended to.
        """
        swapped = set(ConstructorSwaps.component_keys())
        for path in ConstructorSwaps.consumed_paths():
            value = inventory.get(path)
            if value is None:
                continue
            binding = self._bindings.resolve(path)
            for target in binding.targets:
                if target.constructor is not None or target.component_key in swapped:
                    continue
                if not self._target_is_live(target, binding, editor):
                    continue
                editor.set_config(target.component_key, target.field_or_argument, value)
                edits.append(
                    Edit(
                        kind=EditKind.CONFIG_VALUE,
                        location=DocumentPaths.config_field(
                            target.component_key, target.field_or_argument
                        ),
                        value=value,
                        source=path,
                        note=binding.note or f"the binding of '{path}'",
                    )
                )

    def _swap_building(
        self,
        inventory: Inventory,
        editor: SystemEditor,
        edits: List[Edit],
        report: MappingReport,
    ) -> None:
        """Replace the building's preset by its TABULA constructor and drop the pinned weather."""
        swap = ConstructorSwaps.BUILDING
        if not editor.has(swap.component_key):
            return
        archetype = self._tabula(inventory)
        arguments: Dict[str, Any] = {"building_code": archetype.building_code}
        for path, argument in (
            ("building_config.general.conditioned_floor_area_m2", "absolute_conditioned_floor_area_in_m2"),
            ("building_config.general.number_of_apartments", "number_of_apartments"),
            ("building_config.general.building_heat_capacity_class", "building_heat_capacity_class"),
        ):
            value = inventory.get(path)
            if value is not None:
                arguments[argument] = value
        editor.swap_constructor(
            swap.component_key, swap.constructor, arguments, swap.removed_config_keys
        )
        edits.append(
            Edit(
                kind=EditKind.CONSTRUCTOR_SWAP,
                location=DocumentPaths.constructor_of(swap.component_key),
                value=dict(arguments),
                source="building_config.general",
                note=(
                    f"the recorded preset became for_tabula_code({archetype.building_code}); the "
                    "recorded weather_identity was removed so its sizing law recomputes it"
                ),
            )
        )
        for path in swap.consumed_inventory_paths:
            if inventory.has(path):
                report.used(
                    path,
                    f"-> {swap.component_key}.{swap.constructor}",
                    rule=f"TABULA archetype {archetype.building_code}",
                )
        for note in archetype.selection_notes:
            report.approximated(
                "building_config.general.construction_year",
                note,
                rule=f"Q19: nearest usable TABULA row, {archetype.building_code}",
            )

    def _swap_weather(
        self,
        inventory: Inventory,
        editor: SystemEditor,
        edits: List[Edit],
        refusals: List[RefusalDetail],
        report: MappingReport,
    ) -> None:
        """Replace the recorded Aachen weather by the station of the dwelling's own country."""
        swap = ConstructorSwaps.WEATHER
        if not editor.has(swap.component_key):
            return
        path = "location.country_code"
        country = str(inventory.get(path) or "").upper()
        if country not in LocationEnum.__members__:
            refusals.append(
                RefusalDetail(
                    reason=ReasonCode.NO_WEATHER_FOR_COUNTRY,
                    path=path,
                    detail=(
                        f"no weather station of LocationEnum is named '{country}'; the stations "
                        f"named by country code are {', '.join(sorted(LocationEnum.__members__))}"
                    ),
                )
            )
            return
        editor.swap_constructor(
            swap.component_key, swap.constructor, {"location": country}, swap.removed_config_keys
        )
        edits.append(
            Edit(
                kind=EditKind.CONSTRUCTOR_SWAP,
                location=DocumentPaths.constructor_of(swap.component_key),
                value={"location": country},
                source=path,
                note=(
                    f"the recorded Aachen configuration became for_location({country}); its "
                    "location, source_path and data_source were removed"
                ),
            )
        )
        report.used(path, f"-> {swap.component_key}.{swap.constructor}(location={country})")

    def _swap_occupancy(
        self,
        inventory: Inventory,
        editor: SystemEditor,
        edits: List[Edit],
        report: MappingReport,
    ) -> HouseholdMatch:
        """Replace the recorded household by the nearest one to this dwelling's residents."""
        swap = ConstructorSwaps.OCCUPANCY
        match = self._matcher.match(inventory)
        if not editor.has(swap.component_key):
            return match
        entry = editor.entry(swap.component_key)
        arguments: Dict[str, Any] = {"household": self._reference(match.household)}
        mode = entry.config.get(self.ACQUISITION_MODE_FIELD)
        if mode is not None:
            arguments[self.ACQUISITION_MODE_FIELD] = mode
        if match.travel_route_set is not None:
            arguments["travel_route_set"] = self._reference(match.travel_route_set)
        editor.swap_constructor(
            swap.component_key, swap.constructor, arguments, swap.removed_config_keys
        )
        edits.append(
            Edit(
                kind=EditKind.CONSTRUCTOR_SWAP,
                location=DocumentPaths.constructor_of(swap.component_key),
                value=dict(arguments),
                source="occupancy_config",
                note=f"the recorded preset became for_household({match.household.Name})",
            )
        )
        for path, note in HouseholdMatchReport.lines(match).items():
            if inventory.has(path):
                report.approximated(path, note, rule=HouseholdMatchReport.RULE)
        if match.travel_route_set is not None:
            report.used(
                HouseholdMatcher.TRAVEL_ROUTE_SET,
                f"-> {swap.component_key}.{swap.constructor}(travel_route_set)",
            )
        else:
            report.defaulted(
                HouseholdMatcher.TRAVEL_ROUTE_SET,
                "the inventory names none; the occupancy constructor's own commuting profile is used",
                rule="the LoadProfileGenerator's ten-kilometre commuting route set",
            )
        return match

    @classmethod
    def _reference(cls, reference: Any) -> Dict[str, Any]:
        """Render a LoadProfileGenerator reference as the plain mapping the file's codec reads.

        Args:
            reference: A ``JsonReference`` from the LoadProfileGenerator catalogue.

        Returns:
            ``{"Name": …, "Guid": {"StrVal": …}}``, which the constructor-argument codec rebuilds
            into the reference again. The name is what makes the file readable; the GUID is what
            the LoadProfileGenerator actually resolves.
        """
        guid = getattr(reference, "Guid", None)
        rendered: Dict[str, Any] = {"Name": reference.Name}
        if guid is not None:
            rendered["Guid"] = {"StrVal": guid.StrVal}
        return rendered

    def _apply_config_values(
        self,
        result: ApplicationResult,
        editor: SystemEditor,
        edits: List[Edit],
        refusals: List[RefusalDetail],
        report: MappingReport,
    ) -> None:
        """Write every inventory leaf that reaches a config field of the selected file.

        Every leaf of the post-measure inventory is walked, in a fixed order, so that the report
        accounts for all of them (requirement R7) and so that the emitted file is byte-stable.
        """
        inventory = result.inventory
        generator_changed = self._generator_changed(result)
        consumed = set(ConstructorSwaps.consumed_paths())
        measure_written = set(result.measure_written_paths)
        for path in inventory.leaf_paths():
            if path in consumed:
                continue
            value = inventory.get(path)
            binding = self._bindings.resolve(path)
            if binding.kind is BindingKind.NON_SIMULATION:
                report.non_simulation(path, binding.note)
                continue
            if binding.kind is BindingKind.NO_CONSUMER:
                report.ignored(path, binding.note)
                continue
            if binding.kind is BindingKind.CONSTRUCTOR_ARGUMENT and not binding.targets:
                report.used(path, f"selected the base file {result.base_file_name}")
                continue
            if value is None:
                report.ignored(path, "the inventory carries no value for this field")
                continue
            stale = StaleLeaves.reason_for(path, inventory, generator_changed)
            if stale is not None:
                report.ignored(path, stale)
                continue
            self._write_leaf(
                path, value, binding, result, editor, edits, refusals, report, measure_written
            )

    def _write_leaf(
        self,
        path: str,
        value: Any,
        binding: LeafBinding,
        result: ApplicationResult,
        editor: SystemEditor,
        edits: List[Edit],
        refusals: List[RefusalDetail],
        report: MappingReport,
        measure_written: AbstractSet[str],
    ) -> None:
        """Write one leaf onto every target the selected file has, and report the ones it lacks."""
        reached: List[Target] = []
        missing: List[Target] = []
        for target in binding.targets:
            if target.constructor is not None:
                continue
            if self._target_is_live(target, binding, editor):
                reached.append(target)
            else:
                missing.append(target)
        for target in reached:
            editor.set_config(target.component_key, target.field_or_argument, value)
            edits.append(
                Edit(
                    kind=EditKind.CONFIG_VALUE,
                    location=DocumentPaths.config_field(
                        target.component_key, target.field_or_argument
                    ),
                    value=value,
                    source=path,
                    note=binding.note or f"the binding of '{path}'",
                )
            )
        if reached:
            report.used(path, "-> " + ", ".join(target.describe() for target in reached))
            return
        detail = (
            f"base file {result.base_file_name} has no "
            f"{', '.join(sorted({target.component_key for target in missing}))}"
        )
        if path in measure_written:
            refusals.append(
                RefusalDetail(
                    reason=ReasonCode.NO_BASE_FILE_FOR_COMBINATION,
                    path=path,
                    detail=f"a measure asked for this field, but {detail}",
                )
            )
            return
        report.ignored(path, detail)

    def _target_is_live(self, target: Target, binding: LeafBinding, editor: SystemEditor) -> bool:
        """Return whether a target's component takes part in the world the file selects.

        Args:
            target: The component and field the leaf reaches.
            binding: The leaf's binding, whose block may require a variant option.
            editor: The file being edited.

        Returns:
            ``True`` when the selected world carries the component and, where the block rule names
            a variant option, that option is the one selected.
        """
        block = self._bindings.block_for(binding.inventory_path)
        if block is not None and block.requires_variant is not None:
            variant, option = block.requires_variant
            if variant not in editor.variant_names() or editor.variant_selection(variant) != option:
                return False
        return editor.has(target.component_key)

    @classmethod
    def _generator_changed(cls, result: ApplicationResult) -> bool:
        """Return whether a measure replaced the dwelling's heat generator.

        The post-measure inventory's own ``heating_system.system`` is what the base-file key was
        merged from, so the two agree; what says a measure changed it is that the file the key
        selects is not the one the dwelling's recorded system would have selected. The simplest
        statement of that is the measure-written path itself.
        """
        return "energy_system_config.heating_system.system" in set(result.measure_written_paths)

    def _apply_laws(
        self,
        result: ApplicationResult,
        editor: SystemEditor,
        edits: List[Edit],
        report: MappingReport,
        estimator: DemandEstimator,
    ) -> None:
        """Resolve every pending sizing law and write its value like any other config value."""
        for path, law in sorted(result.pending_laws.items()):
            resolved = self._laws.resolve(law, result.inventory, estimator)
            binding = self._bindings.resolve(path)
            written = False
            for target in binding.targets:
                if target.constructor is not None or not self._target_is_live(target, binding, editor):
                    continue
                editor.set_config(target.component_key, target.field_or_argument, resolved.value)
                edits.append(
                    Edit(
                        kind=EditKind.CONFIG_VALUE,
                        location=DocumentPaths.config_field(
                            target.component_key, target.field_or_argument
                        ),
                        value=resolved.value,
                        source=law.law.value,
                        note=resolved.rule,
                    )
                )
                written = True
            if written:
                report.approximated(path, f"sized to {resolved.value:.4g}", rule=resolved.rule)
            else:
                report.ignored(
                    path,
                    f"base file {result.base_file_name} has no component for this sized field",
                )


def parametrise(
    result: ApplicationResult,
    estimator: DemandEstimator,
    base_files_directory: Path,
) -> ParametrisedSystem:
    """Parametrise one application result with the committed tables, in one call.

    The shorthand the ``calculate`` command and the trace page both use, so that neither has to
    repeat which bindings, which TABULA rule and which matcher are the standard ones.

    Args:
        result: What applying the package produced.
        estimator: Where the daily demands a sizing law reads come from.
        base_files_directory: Where the recorded energy-system files live.

    Returns:
        The parametrised system.

    Raises:
        RefusalError: When the base file cannot carry what the package asked for.
        ParametriserError: When the result breaks requirement R4.
    """
    return Parametriser(base_files_directory).parametrise(result, estimator)
