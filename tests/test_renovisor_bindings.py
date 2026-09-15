"""Check 5 of ``measures_v2_requirements.md`` §7.5: every binding resolves in every base file.

The bindings table is a list of claims about eleven YAML files and about the configuration classes
they name — "the component recorded as ``Building`` has a field called ``roof_area_in_m2``", "the
component recorded as ``Weather`` has a constructor ``for_location`` taking a ``location``". Every
one of those claims is checked here by loading the file and introspecting the class, so that a
HiSim rename or a re-recorded base file fails the build instead of failing a calculation.

The second half is the coverage claim (requirement R7, acceptance criterion A1): every leaf of the
contract's ``HomeInventoryInput``, plus every path
:class:`~hisim.renovisor.inventory.PendingContractPaths` knows about, has a stated fate in the
table. A contract revision that adds a field therefore fails here until somebody decides what
happens to it.
"""

from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

from hisim.config.introspection import ConfigDescription, describe_config
from hisim.energy_system.classes import ClassBinder
from hisim.energy_system.loader import load_energy_system
from hisim.energy_system.model import ComponentEntry, EnergySystemFile
from hisim.renovisor.base_files import BaseFiles
from hisim.renovisor.bindings import (
    BindingError,
    BindingKind,
    Bindings,
    GeneratorComponents,
    GeneratorKey,
    PathMatching,
    Target,
)
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.inventory import PendingContractPaths

pytestmark = pytest.mark.base

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class Placement:
    """Where one component was found inside an energy-system file.

    A component may sit at the top level, inside a group, or inside one option of one variant, and
    the bindings table cares about the difference: the battery exists only inside the
    ``ems_with_battery`` option, which is why its block rule carries ``requires_variant``.

    Args:
        entry: The component entry as the file declares it.
        variant: ``(variant name, option name)`` when the component sits inside a variant option,
            ``None`` otherwise.
        group: The group's name when the component sits inside a group, ``None`` otherwise.
    """

    def __init__(
        self,
        entry: ComponentEntry,
        variant: Optional[Tuple[str, str]] = None,
        group: Optional[str] = None,
    ) -> None:
        """Store the entry and where it was found."""
        self.entry = entry
        self.variant = variant
        self.group = group


def placements_of(model: EnergySystemFile) -> Dict[str, Placement]:
    """Return every component the file declares, by key, with where it was declared.

    Unselected variant options are included on purpose: a binding is a claim about the file, and
    the battery's option is part of the file whether or not it is the selected one today.

    Args:
        model: The loaded energy-system file.

    Returns:
        A mapping from component key to its :class:`Placement`. Where one key occurs in two
        options of one variant, the first is kept, because the check only reads the class.
    """
    found: Dict[str, Placement] = {}
    for name, entry in model.components.items():
        found.setdefault(name, Placement(entry))
    for group_name, group in model.groups.items():
        for name, entry in group.components.items():
            found.setdefault(name, Placement(entry, group=group_name))
    for variant_name, variant in model.variants.items():
        for option_name, option in variant.options.items():
            for name, entry in option.components.items():
                found.setdefault(name, Placement(entry, variant=(variant_name, option_name)))
    return found


def description_of(name: str, entry: ComponentEntry, cache: Dict[str, ConfigDescription]) -> ConfigDescription:
    """Return the introspected configuration description of one component, importing it once.

    Args:
        name: The component's recorded key, for the binder's message.
        entry: The component entry naming the class.
        cache: A per-session cache keyed by class path, so that eleven files that all wire a
            ``Building`` import and describe it once.

    Returns:
        The :class:`~hisim.config.introspection.ConfigDescription` of its configuration class.
    """
    if entry.class_path not in cache:
        config_class = ClassBinder.config_class_of(name, entry)
        cache[entry.class_path] = describe_config(config_class)
    return cache[entry.class_path]


def generator_keys_of(model: EnergySystemFile) -> Tuple[str, ...]:
    """Return the recorded keys of every heat generator the file's selected system wires.

    Args:
        model: The loaded energy-system file.

    Returns:
        The keys, sorted. A valid base file has exactly one.
    """
    return tuple(
        sorted(
            name
            for name, entry in model.all_components().items()
            if GeneratorComponents.is_generator(entry.class_path)
        )
    )


def schema_leaf_paths() -> Tuple[str, ...]:
    """Return the dotted path of every leaf of the contract's ``HomeInventoryInput`` schema.

    Object properties are walked recursively and array items are walked once, so a per-vehicle
    field appears as ``energy_system_config.vehicles.electric_vehicles.model`` — the index is not
    part of a binding, which is what :class:`~hisim.renovisor.bindings.PathMatching` strips.

    Returns:
        Every leaf path, sorted.
    """
    document = ContractFiles.openapi()
    schemas = document["components"]["schemas"]
    return tuple(sorted(_iter_schema_leaves(schemas["HomeInventoryInput"], schemas, "")))


def _iter_schema_leaves(node: Any, schemas: Dict[str, Any], prefix: str) -> Iterator[str]:
    """Yield every leaf path under one schema node."""
    if not isinstance(node, dict):
        return
    reference = node.get("$ref")
    if isinstance(reference, str):
        yield from _iter_schema_leaves(schemas[reference.split("/")[-1]], schemas, prefix)
        return
    properties = node.get("properties")
    if isinstance(properties, dict):
        for key, child in properties.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            children = list(_iter_schema_leaves(child, schemas, child_prefix))
            if children:
                yield from children
            else:
                yield child_prefix
        return
    items = node.get("items")
    if node.get("type") == "array" and isinstance(items, dict):
        yield from _iter_schema_leaves(items, schemas, prefix)


@pytest.fixture(name="base_file_models", scope="module")
def fixture_base_file_models() -> Dict[str, EnergySystemFile]:
    """Load every recorded base file the selection table names, once for the whole module."""
    directory = REPOSITORY_ROOT / BaseFiles.DIRECTORY
    return {name: load_energy_system(directory / name) for name in BaseFiles.file_names()}


@pytest.fixture(name="descriptions", scope="module")
def fixture_descriptions() -> Dict[str, ConfigDescription]:
    """A per-session cache of introspected configuration classes, keyed by class path."""
    return {}


def resolve_target(
    target: Target,
    placements: Dict[str, Placement],
    generator_key: str,
) -> Optional[Placement]:
    """Return the placement a target names in one file, or ``None`` when the file lacks it.

    A missing component is not a failure: ``SolarThermalSystem`` exists in two of the eleven files
    and ``Battery`` only inside one variant option, and a binding is checked wherever it applies.

    Args:
        target: The binding target.
        placements: Every component of the file, by key.
        generator_key: The recorded key of this file's heat generator, for the
            :attr:`~hisim.renovisor.bindings.GeneratorKey.SELECTED` sentinel.

    Returns:
        The placement, or ``None``.
    """
    key = generator_key if target.component_key == GeneratorKey.SELECTED.value else target.component_key
    return placements.get(key)


def check_target(
    target: Target,
    placement: Placement,
    descriptions: Dict[str, ConfigDescription],
) -> List[str]:
    """Return the complaints one target raises against one component, empty when it checks out.

    Args:
        target: The binding target.
        placement: Where the component was found and what it is.
        descriptions: The description cache.

    Returns:
        One sentence per problem: an unknown config field, an unknown constructor, or a
        constructor that does not take the named argument.
    """
    description = description_of(target.component_key, placement.entry, descriptions)
    if target.constructor is None:
        names = [field.name for field in description.fields]
        if target.field_or_argument not in names:
            return [
                f"{description.config_class_name} has no field '{target.field_or_argument}'; it has {sorted(names)}"
            ]
        return []
    for constructor in description.constructors:
        if constructor.name != target.constructor:
            continue
        parameters = [parameter.name for parameter in constructor.parameters]
        if target.field_or_argument not in parameters:
            return [
                f"{description.config_class_name}.{target.constructor} takes {parameters}, "
                f"not '{target.field_or_argument}'"
            ]
        return []
    return [
        f"{description.config_class_name} has no constructor '{target.constructor}'; it has "
        f"{[constructor.name for constructor in description.constructors]}"
    ]


def test_every_base_file_wires_exactly_one_heat_generator(
    base_file_models: Dict[str, EnergySystemFile]
) -> None:
    """The ``GeneratorKey.SELECTED`` sentinel is only meaningful if it resolves uniquely."""
    for name, model in base_file_models.items():
        generators = generator_keys_of(model)
        assert len(generators) == 1, f"{name} wires {generators}, not exactly one heat generator"


def test_every_target_resolves_in_every_base_file_that_has_the_component(
    base_file_models: Dict[str, EnergySystemFile], descriptions: Dict[str, ConfigDescription]
) -> None:
    """Check 5: every leaf target names a real field or a real constructor argument."""
    complaints: List[str] = []
    for file_name, model in base_file_models.items():
        placements = placements_of(model)
        generator_key = generator_keys_of(model)[0]
        for leaf in Bindings.LEAVES:
            for target in leaf.targets:
                placement = resolve_target(target, placements, generator_key)
                if placement is None:
                    continue
                complaints.extend(
                    f"{file_name}: {leaf.inventory_path} -> {target.describe()}: {problem}"
                    for problem in check_target(target, placement, descriptions)
                )
    assert not complaints, "\n".join(complaints)


def test_every_block_rule_resolves_for_every_leaf_it_covers(
    base_file_models: Dict[str, EnergySystemFile], descriptions: Dict[str, ConfigDescription]
) -> None:
    """Every contract leaf that falls through to a block rule names a real config field."""
    complaints: List[str] = []
    leaves = schema_leaf_paths() + PendingContractPaths.paths()
    for file_name, model in base_file_models.items():
        placements = placements_of(model)
        generator_key = generator_keys_of(model)[0]
        for path in leaves:
            binding = Bindings.resolve(path)
            if not binding.note.startswith("the block rule"):
                continue
            for target in binding.targets:
                placement = resolve_target(target, placements, generator_key)
                if placement is None:
                    continue
                complaints.extend(
                    f"{file_name}: {path} -> {target.describe()}: {problem}"
                    for problem in check_target(target, placement, descriptions)
                )
    assert not complaints, "\n".join(complaints)


def test_the_battery_lives_only_inside_the_variant_option_the_block_names(
    base_file_models: Dict[str, EnergySystemFile]
) -> None:
    """The battery block declares ``requires_variant``; the recorded files must agree."""
    battery_block = next(block for block in Bindings.BLOCKS if block.component_key == "Battery")
    assert battery_block.requires_variant == ("electricity_management", "ems_with_battery")
    for file_name, model in base_file_models.items():
        placement = placements_of(model).get("Battery")
        assert placement is not None, f"{file_name} has no Battery at all"
        assert placement.variant == battery_block.requires_variant, (
            f"{file_name} declares Battery at {placement.variant or placement.group or 'top level'}"
        )


def test_every_contract_leaf_has_a_stated_fate() -> None:
    """Requirement R7 / A1: no inventory field is silently dropped."""
    unresolved: List[str] = []
    for path in schema_leaf_paths() + PendingContractPaths.paths():
        try:
            Bindings.resolve(path)
        except BindingError:
            unresolved.append(path)
    assert not unresolved, f"no binding covers: {unresolved}"


def test_a_path_under_no_rule_raises() -> None:
    """A made-up top-level block is a table gap, not a silently ignored field."""
    with pytest.raises(BindingError):
        Bindings.resolve("something_the_contract_never_had.a_field")


def test_a_leaf_rule_wins_over_its_block_rule() -> None:
    """The battery's capacity is a rename exception; the battery block must not override it."""
    binding = Bindings.resolve("energy_system_config.battery_storage.capacity_in_kwh")
    assert binding.targets == (
        Target("Battery", "custom_battery_capacity_generic_in_kilowatt_hour", None),
    )
    wildcard = Bindings.resolve("energy_system_config.battery_storage.installation_year")
    assert wildcard.kind is BindingKind.NON_SIMULATION


def test_a_block_rule_covers_a_leaf_no_exception_names() -> None:
    """The envelope block is what keeps the table short; it has to actually apply."""
    binding = Bindings.resolve("building_config.envelope_details.roof_area_in_m2")
    assert binding.targets == (Target("Building", "roof_area_in_m2", None),)
    assert binding.kind is BindingKind.CONFIG_OVERRIDE


def test_array_indices_are_not_part_of_a_binding() -> None:
    """A per-vehicle field resolves the same with and without its index."""
    with_index = Bindings.resolve("energy_system_config.vehicles.electric_vehicles[0].model")
    without_index = Bindings.resolve("energy_system_config.vehicles.electric_vehicles.model")
    assert with_index.kind is without_index.kind is BindingKind.NON_SIMULATION
    assert PathMatching.segments("a.b[2].c") == ("a", "b", "c")


def test_every_pending_rename_is_used_by_a_binding() -> None:
    """The rename list is the contract PR's to-do list, so it may not name a path nothing binds."""
    for rename in Bindings.PENDING_RENAMES:
        binding = Bindings.resolve(rename.contract_path)
        assert binding.targets, f"{rename.contract_path} is listed as renamed but reaches nothing"


def test_the_bindings_table_carries_no_value_maps() -> None:
    """Decision C3: a binding names a component and a field, never a value translation."""
    for leaf in Bindings.LEAVES:
        for target in leaf.targets:
            assert isinstance(target.field_or_argument, str)
            assert not isinstance(target.field_or_argument, dict)
    assert not hasattr(Bindings, "VALUE_MAPS")


def test_which_base_files_lack_a_component_a_binding_names(
    base_file_models: Dict[str, EnergySystemFile]
) -> None:
    """The expected gaps are the solar collector, the district and the electric heating files.

    The check is a whitelist rather than a count: a new gap — a re-recorded file that dropped its
    hot-water storage, say — has to be looked at, because a binding that reaches nothing writes
    nothing. Generator keys are left out because they are file-specific by design; that they occur
    exactly once per file is the previous test.
    """
    expected: Dict[str, Tuple[str, ...]] = {
        "household_district_heating_building_sizer.grouped.energy_system.yaml": (
            "SimpleHotWaterStorage",
            "SolarThermalSystem",
        ),
        "household_electric_heating_building_sizer.grouped.energy_system.yaml": (
            "HeatDistributionController",
            "SimpleHotWaterStorage",
            "SolarThermalSystem",
        ),
        "household_gas_building_sizer.grouped.energy_system.yaml": ("SolarThermalSystem",),
        "household_gas_solar_thermal_building_sizer.grouped.energy_system.yaml": (),
        "household_heatpump_building_sizer.grouped.energy_system.yaml": ("SolarThermalSystem",),
        "household_heatpump_car_building_sizer.grouped.energy_system.yaml": ("SolarThermalSystem",),
        "household_heatpump_solar_thermal_building_sizer.grouped.energy_system.yaml": (),
        "household_hydrogen_boiler_building_sizer.grouped.energy_system.yaml": ("SolarThermalSystem",),
        "household_oil_building_sizer.grouped.energy_system.yaml": ("SolarThermalSystem",),
        "household_pellets_building_sizer.grouped.energy_system.yaml": ("SolarThermalSystem",),
        "household_wood_chips_building_sizer.grouped.energy_system.yaml": ("SolarThermalSystem",),
    }
    role_keys = Bindings.role_component_keys()
    actual = {
        file_name: tuple(sorted(key for key in role_keys if key not in placements_of(model)))
        for file_name, model in base_file_models.items()
    }
    assert actual == expected
