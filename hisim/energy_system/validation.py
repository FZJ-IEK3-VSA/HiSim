"""Structural validation: every rule an energy-system file must obey without its classes.

Validation of an energy system happens on two levels, and this module is the first of
them. It checks everything decidable from the document alone — that names are unique and
each component belongs to at most one group, that every reference resolves to a component
the file declares, that each entry says where its configuration comes from, that the input
items of one consumer do not contradict each other, and that no value smuggles in an
absolute filesystem path. The second level, which needs the component classes imported,
decides whether a preset exists, whether a config field is real and whether two ports can
be wired; nothing here reaches for it.

Keeping the split sharp has a practical payoff. An editor, a schema exporter or a
batch-authoring tool can check a file it is writing without importing HiSim's component
tree, and a file whose classes have not been written yet can still be verified for shape.
It also keeps the failure modes apart: a message from this module is always about the file,
never about the code.

Validation stops at the first problem. The format's contract is that a file is taken as
written or rejected, and the first violation is the one the author has to fix before the
rest of the checks say anything reliable — an unknown component name, for instance, makes
every other statement about that component meaningless.
"""

# clean

from __future__ import annotations

import re
from typing import Any, ClassVar, Dict, Mapping, Pattern, Set, Tuple

from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError
from hisim.energy_system.model import (
    AggregatorFeed,
    ComponentEntry,
    DefaultInputs,
    EnergySystemFile,
    ExplicitWire,
)


class StructuralValidator:
    """Runs every class-independent rule over one loaded energy-system file.

    The validator is constructed for one file, computes the file's namespace once and then
    runs its checks in the order in which a failure explains the most: names first, since
    every later message refers to one, then how each entry is configured, then the
    references between entries, then the shape agreements inside an input list, and finally
    the values of the configuration blocks.

    An instance is single-use and holds no state beyond the file and the derived name
    lookups. Callers normally use :func:`validate_structure` instead of building one.
    """

    #: Nouns that make a config key path-valued: the key is one of them or ends in one
    #: after an underscore. A path written in an energy-system file is symbolic —
    #: ``${inputs}/weather/...`` — so that the file resolves on another machine.
    PATH_KEY_NOUNS: ClassVar[Tuple[str, ...]] = ("path", "paths", "directory", "file", "filename")

    #: Matches a Windows drive prefix, which is absolute even though it starts with a
    #: letter rather than with a separator.
    WINDOWS_DRIVE_PATTERN: ClassVar[Pattern[str]] = re.compile(r"^[A-Za-z]:[\\/]")

    def __init__(self, model: EnergySystemFile) -> None:
        """Prepares the validator for one file by computing its component namespace.

        Args:
            model: The parsed energy system to check.
        """
        self.model = model
        self.components: Dict[str, ComponentEntry] = model.all_components()
        self.names: Tuple[str, ...] = tuple(self.components)

    def validate(self) -> None:
        """Runs every structural check, raising on the first violation.

        Raises:
            EnergySystemFormatError: Naming the offending component, group or key path,
                and listing the valid alternatives wherever the set of them is closed.
        """
        self._check_names()
        self._check_configuration_origin()
        self._check_reference_closure()
        self._check_input_shapes()
        self._check_no_absolute_paths()

    def _check_names(self) -> None:
        """Rejects a name used twice and a component listed in two groups.

        Component names are global: a group is a set with a flag, not a namespace, so a
        grouped entry may not reuse the name of an ungrouped one and two groups may not
        both contain the same name. Group names share that namespace as well, because a
        reference is a bare name and could otherwise mean either.

        Raises:
            EnergySystemFormatError: ``EF-51`` when two groups list the same component,
                ``EF-52`` when a name collides with another component or with a group.
        """
        owner: Dict[str, str] = {name: "components" for name in self.model.components}
        for group_name, group in self.model.groups.items():
            for member in group.components:
                previous = owner.get(member)
                if previous is not None and previous != "components":
                    raise EnergySystemFormatError(
                        EnergySystemErrorId.COMPONENT_IN_TWO_GROUPS,
                        f"groups.{group_name}.components.{member}",
                        f"component '{member}' is already listed in group '{previous}'.",
                        remedy="A component belongs to at most one group; groups are sets, not namespaces.",
                    )
                if previous is not None:
                    raise EnergySystemFormatError(
                        EnergySystemErrorId.DUPLICATE_NAME,
                        f"groups.{group_name}.components.{member}",
                        f"component '{member}' has the same name as an ungrouped component.",
                        remedy="Component names are global across the whole file, groups included.",
                    )
                owner[member] = group_name
        for group_name in self.model.groups:
            if group_name in self.components:
                raise EnergySystemFormatError(
                    EnergySystemErrorId.DUPLICATE_NAME,
                    f"groups.{group_name}",
                    f"group '{group_name}' has the same name as a component.",
                    remedy="A reference is a bare name, so a group and a component may not share one.",
                )

    def _check_configuration_origin(self) -> None:
        """Rejects an entry that names both a preset and a constructor, or neither.

        A preset and a named constructor are two ways of producing the same thing — the
        component's configuration before overrides — so naming both leaves it open which
        one runs. Naming neither is only acceptable when the ``config`` block stands on its
        own; whether it really is complete depends on the class, so the check made here is
        the weaker one that something is written at all.

        Raises:
            EnergySystemFormatError: ``EF-11`` when both are present, ``EF-12`` when
                neither is and ``config`` is empty as well.
        """
        for name, entry in self.components.items():
            location = f"components.{name}"
            if entry.preset is not None and entry.constructor is not None:
                raise EnergySystemFormatError(
                    EnergySystemErrorId.PRESET_AND_CONSTRUCTOR,
                    location,
                    f"'{name}' names both preset '{entry.preset}' and constructor "
                    f"'{entry.constructor.name}'.",
                    remedy="An entry carries exactly one of 'preset' and 'constructor'.",
                )
            if entry.preset is None and entry.constructor is None and not entry.config:
                raise EnergySystemFormatError(
                    EnergySystemErrorId.NO_CONFIGURATION_SOURCE,
                    location,
                    f"'{name}' says nothing about how it is configured.",
                    remedy="Write 'preset', or 'constructor', or a complete 'config' block.",
                )

    def _check_reference_closure(self) -> None:
        """Rejects a reference naming a component the file does not declare.

        Both kinds of reference are checked: the source of an input item and the provider
        of a sizing fact. A sizing reference additionally has to name the very fact it is
        written under, because the line answers where *that* fact comes from and a source
        cannot rename a fact on the way.

        Raises:
            EnergySystemFormatError: ``EF-20`` for an unknown input source, ``EF-40`` for
                an unknown sizing provider, ``EF-41`` when the reference's fact half
                differs from the key it is written under.
        """
        for name, entry in self.components.items():
            for index, item in enumerate(entry.inputs):
                if item.source not in self.components:
                    raise EnergySystemFormatError(
                        EnergySystemErrorId.UNKNOWN_SOURCE,
                        f"components.{name}.inputs[{index}]",
                        f"'{name}' declares an input from '{item.source}', which is not a component "
                        "of this energy system.",
                        alternatives=self.names,
                        alternatives_label="components",
                        offending_value=item.source,
                    )
            for fact, reference in entry.sizing_references():
                location = f"components.{name}.sizing_sources.{fact}"
                if reference.component not in self.components:
                    raise EnergySystemFormatError(
                        EnergySystemErrorId.UNKNOWN_SIZING_SOURCE,
                        location,
                        f"'{name}' takes '{fact}' from '{reference.component}', which is not a component "
                        "of this energy system.",
                        alternatives=self.names,
                        alternatives_label="components",
                        offending_value=reference.component,
                    )
                if reference.fact != fact:
                    raise EnergySystemFormatError(
                        EnergySystemErrorId.SIZING_FACT_MISMATCH,
                        location,
                        f"'{name}' maps '{fact}' onto '{reference.text}', which names the fact "
                        f"'{reference.fact}'.",
                        remedy=f"Write '{reference.component}.{fact}'; a source line cannot rename a fact.",
                    )

    def _check_input_shapes(self) -> None:
        """Rejects input lists whose items contradict each other.

        Three agreements are enforced per consumer. A pair of components is connected in
        one spelling only: a bare defaults item may accompany either explicit wires or
        aggregator feeds, but wires and feeds from the same source would describe the same
        relationship twice and in two different ways. A consumer names each source's
        defaults at most once. And within one consumer, no input is wired twice and no
        source output is fed twice, since either would leave the winner to list order.

        Raises:
            EnergySystemFormatError: ``EF-24`` for a mixed or repeated spelling per pair,
                ``EF-25`` for a repeated aggregator feed, ``EF-26`` for two wires into one
                input.
        """
        for name, entry in self.components.items():
            spellings: Dict[str, Set[str]] = {}
            wired_inputs: Set[str] = set()
            fed_outputs: Set[Tuple[str, str]] = set()
            for index, item in enumerate(entry.inputs):
                location = f"components.{name}.inputs[{index}]"
                kinds = spellings.setdefault(item.source, set())
                if isinstance(item, DefaultInputs):
                    self._require_new_spelling(kinds, "defaults", name, item.source, location)
                    kinds.add("defaults")
                elif isinstance(item, ExplicitWire):
                    self._require_new_spelling(kinds, "wires", name, item.source, location)
                    kinds.add("wires")
                    if item.input in wired_inputs:
                        raise EnergySystemFormatError(
                            EnergySystemErrorId.DUPLICATE_WIRE,
                            location,
                            f"'{name}' wires its input '{item.input}' twice.",
                            remedy="An input takes exactly one source.",
                        )
                    wired_inputs.add(item.input)
                elif isinstance(item, AggregatorFeed):
                    self._require_new_spelling(kinds, "feeds", name, item.source, location)
                    kinds.add("feeds")
                    feed_key = (item.source, item.output or "")
                    if feed_key in fed_outputs:
                        raise EnergySystemFormatError(
                            EnergySystemErrorId.DUPLICATE_FEED,
                            location,
                            f"'{name}' is fed twice from '{item.source}"
                            f"{'.' + item.output if item.output else ''}'.",
                            remedy="An aggregator accepts each source output once.",
                        )
                    fed_outputs.add(feed_key)

    @classmethod
    def _require_new_spelling(cls, kinds: Set[str], kind: str, consumer: str, source: str, location: str) -> None:
        """Rejects an item that mixes or repeats the spelling already used for this pair.

        Explicit wires and aggregator feeds are mutually exclusive between one pair of
        components, and a defaults item may appear only once per pair; wires and feeds
        themselves may repeat, since a consumer legitimately takes several ports or several
        flows from the same source.

        Raises:
            EnergySystemFormatError: ``EF-24`` naming the consumer, the source and the
                spelling that is already in use.
        """
        clash = kind == "defaults" and "defaults" in kinds
        clash = clash or (kind == "wires" and "feeds" in kinds) or (kind == "feeds" and "wires" in kinds)
        if not clash:
            return
        raise EnergySystemFormatError(
            EnergySystemErrorId.MIXED_INPUT_SPELLING,
            location,
            f"'{consumer}' already declares {' and '.join(sorted(kinds))} from '{source}'.",
            remedy=(
                "A (consumer, source) pair carries at most one defaults item plus either "
                "explicit wires or aggregator feeds, never both."
            ),
        )

    def _check_no_absolute_paths(self) -> None:
        """Rejects an absolute filesystem path written into a config or constructor value.

        A file has to resolve on a colleague's machine and inside a container, so a path
        it contains is written symbolically — ``${inputs}/weather/berlin`` — and expanded
        against the local directory layout when the component is built. An absolute path
        makes the file work on exactly one machine, which is a portability bug that is
        cheap to catch here and expensive to diagnose later.

        Raises:
            EnergySystemFormatError: ``EF-05`` naming the key path of the value.
        """
        for name, entry in self.components.items():
            self._scan_for_absolute_paths(entry.config, f"components.{name}.config")
            if entry.constructor is not None:
                self._scan_for_absolute_paths(
                    entry.constructor.arguments,
                    f"components.{name}.constructor.{entry.constructor.name}",
                )

    @classmethod
    def _scan_for_absolute_paths(cls, block: Mapping[str, Any], location: str) -> None:
        """Walks a config block and rejects an absolute path under a path-valued key.

        Only keys that name a location are inspected, because an arbitrary string field may
        legitimately start with a slash. Nested mappings and lists are followed so that a
        path buried in a sub-block is found as well.

        Raises:
            EnergySystemFormatError: ``EF-05`` naming the key path of the value.
        """
        for key, value in block.items():
            child = f"{location}.{key}"
            if isinstance(value, dict):
                cls._scan_for_absolute_paths(value, child)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, dict):
                        cls._scan_for_absolute_paths(item, f"{child}[{index}]")
                    elif cls._is_absolute_path(key, item):
                        raise cls._absolute_path_error(f"{child}[{index}]", item)
            elif cls._is_absolute_path(key, value):
                raise cls._absolute_path_error(child, value)

    @classmethod
    def _is_absolute_path(cls, key: str, value: Any) -> bool:
        """Decides whether one config value is an absolute path under a path-valued key.

        The key name is what makes a string a path: a field called ``source_path`` holds a
        location, a field called ``description`` does not, and guessing from the value
        alone would reject legitimate strings. Both POSIX and Windows spellings count.

        Returns:
            ``True`` when the key names a location and the value is an absolute path.
        """
        if not isinstance(value, str) or not value:
            return False
        if not any(key == noun or key.endswith("_" + noun) for noun in cls.PATH_KEY_NOUNS):
            return False
        return value.startswith("/") or value.startswith("\\") or cls.WINDOWS_DRIVE_PATTERN.match(value) is not None

    @classmethod
    def _absolute_path_error(cls, location: str, value: str) -> EnergySystemFormatError:
        """Builds the rejection for an absolute path, showing the symbolic form instead.

        Returns:
            The exception, which the caller raises so the traceback starts at the check.
        """
        return EnergySystemFormatError(
            EnergySystemErrorId.ABSOLUTE_PATH,
            location,
            f"'{value}' is an absolute filesystem path.",
            remedy="Write paths symbolically, e.g. '${inputs}/weather/berlin', so the file stays portable.",
        )


def validate_structure(model: EnergySystemFile) -> None:
    """Checks every rule of an energy-system file that needs no component class.

    This is what :func:`hisim.energy_system.loader.load_energy_system` runs after parsing,
    and what a tool holding a model it built itself should run before trusting it. On
    return the file is known to have unique names, legal groups, a closed reference graph,
    a stated configuration origin per entry, consistent input lists and no absolute paths.

    Nothing is said about the component classes: whether they can be imported, whether the
    presets and fields named exist, whether the ports can be wired and whether the sizing
    facts are provided are all decided by the class-bound level of validation, which runs
    later and reports separately.

    Args:
        model: The parsed energy system to check.

    Raises:
        EnergySystemFormatError: On the first violation, naming the offending element and
            listing the valid alternatives wherever the set of them is closed.
    """
    StructuralValidator(model).validate()
