"""The in-memory model of an energy-system file: components, groups, variants and inputs.

An energy-system file is a YAML document describing one simulated household in a single
direction: every component entry states what the component is, how it is configured, where
its inputs come from and — only where that is ambiguous — where its sizing facts come from.
Nothing is ever declared at the source of a connection, so a component entry is the only
place anything about that component is written. The classes here are the faithful in-memory
form of that document: one class per block, frozen so a loaded file cannot be mutated behind
a caller's back, carrying nothing the document does not contain.

The models deliberately know nothing about HiSim components. A ``class`` value is kept as
the plain dotted string the author wrote, a preset is a plain name and a config block is an
untouched mapping of raw YAML values. Whether the class exists, whether it has that preset
and whether the field names are real can only be decided by importing the class, which
happens much later; keeping this module free of that knowledge is what lets an editor, a
schema exporter or a batch tool inspect a file cheaply and without side effects.

:class:`SourceReference` accompanies the models as the parsed form of the
``<component>.<fact>`` strings a ``sizing_sources`` block is made of; the identifier and
reference grammar both of them obey lives one module below, in
:mod:`hisim.energy_system.names`.
"""

# clean

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Dict, Literal, Mapping, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from hisim.energy_system.names import NameRules


class SourceReference(BaseModel):
    """One ``<component>.<fact>`` entry of a ``sizing_sources`` block.

    A component that reads a sizing fact — a heat pump's power band, a building's heating
    load — normally says nothing about where the number comes from, because exactly one
    component provides it. As soon as two do, the ambiguity is the author's to settle, and a
    source reference settles it: the consuming entry names the providing component and the
    fact it takes from it.

    The reference is kept split into its two halves because both are used separately later:
    the component half joins the file's reference graph and must name a declared component,
    and the fact half must equal the key the reference is written under, since a source line
    answers "where does *this* fact come from" and cannot rename it on the way.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    component: str
    fact: str

    @classmethod
    def parse(cls, value: Any, location: str) -> "SourceReference":
        """Parses one written reference into its component and fact halves.

        Args:
            value: The raw YAML value, expected to be a dotted string.
            location: Dotted key path of the value, used in the message.

        Returns:
            The parsed reference.

        Raises:
            EnergySystemFormatError: ``EF-06`` if the string is not a plain dotted pair
                of identifiers, in particular if it contains a wildcard or path syntax.
        """
        component, fact = NameRules.split_reference(value, location, require_member=True)
        assert fact is not None
        return cls(component=component, fact=fact)

    @property
    def text(self) -> str:
        """The reference in the dotted spelling a file uses.

        The emitter writes references back exactly as read, so the model needs one
        canonical rendering of the split form; because parsing rejects everything but
        ``<component>.<fact>``, joining the halves reproduces the author's string exactly.

        Returns:
            ``"<component>.<fact>"``.
        """
        return f"{self.component}{NameRules.REFERENCE_SEPARATOR}{self.fact}"


class ConstructorCall(BaseModel):
    """A named constructor of a config class together with its arguments.

    Some classes are parameterised by an open identifier space rather than a small set of
    variants — a weather station by its location, a building by its catalogue code, an
    occupancy by its household profile. A preset per identifier is impossible and writing
    the resulting configuration by hand means re-typing logic the class already implements,
    so an entry may instead name a declared constructor and pass it plain values.

    The call is stored as the constructor's name plus its argument mapping, with the
    arguments kept as raw YAML values: whether the names match the constructor's parameters,
    and whether the values decode, can only be answered once the class is imported.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    arguments: Mapping[str, Any] = Field(default_factory=dict)


class InputItem(BaseModel):
    """Base of the three shapes one entry in a component's ``inputs`` list can take.

    Every input item names the component it draws from, which is why ``source`` lives here
    rather than being repeated three times. What differs between the shapes is how much the
    author spells out: a bare source name delegates the port choice to the target's declared
    default connections, an explicit wire names both ports, and an aggregator feed hands the
    flow to a component that ranks and sums its participants.

    Each subclass pins ``item_kind`` to its own literal, making the union of the three a
    discriminated one. Without that discriminator, storing a subclass in a field annotated
    with the base would quietly drop the fields the base does not declare.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_kind: str
    source: str


class DefaultInputs(InputItem):
    """A bare source name: wire this source with the target's declared defaults.

    This is the shortest and by far the most common item. It says that the consuming
    component already declares how it wants to be connected to components of the source's
    class, and that those declared connections apply to this source instance. Nothing about
    ports appears in the file, so a component that grows an input does not force every file
    using it to be edited.

    Which ports that resolves to depends on the two classes and is decided long after
    loading; structurally the only requirement is that the source is a declared component.
    """

    item_kind: Literal["default_inputs"] = "default_inputs"


class ExplicitWire(InputItem):
    """One named input of the consumer, fed by one named output of the source.

    An explicit wire is written where the defaults do not apply or do not exist: a second,
    differently-purposed connection between the same pair of components, or a port that only
    this system wants connected. Both ends are named, so the item is complete on its own and
    needs no knowledge of either class to be read.

    The target port is written under ``input`` and the source port as the dotted half of
    ``from``. The asymmetry is intentional: the item already sits inside the consuming
    entry, so only the target's port is missing, while the source needs both halves.
    """

    item_kind: Literal["explicit_wire"] = "explicit_wire"

    input: str
    output: str


class DispatchSpec(BaseModel):
    """The optional back-channel an aggregator feed asks its aggregator to create.

    A participant an aggregator merely measures needs no back-channel; one the aggregator
    *controls* does, because the aggregator has to publish the power it wants that
    participant to draw. Writing ``dispatch`` on the feed asks for that output, so one item
    carries both directions of the relationship and their weights cannot drift apart.

    Both fields are optional and usually absent. ``target_input`` wires the dispatch output
    straight into an input of the participant; without it the output is created and recorded
    but read by nobody, which is normal for participants taking a differently-shaped signal.
    ``tags`` overrides the tag set the aggregator would otherwise give that output.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_input: Optional[str] = None
    tags: Tuple[str, ...] = ()


class AggregatorFeed(InputItem):
    """A participant handed to an aggregating component such as a meter or an EMS.

    An aggregator has no input per participant; it has channels described by tags and ranks
    the participants it accepts by a weight. A feed therefore says what kind of component the
    source is, what kind of flow its output carries and how important it is, and lets the
    aggregator derive the ports. The reserved weight 999 marks a participant that is only
    measured and never controlled.

    ``output`` is the dotted half of ``from`` and may be absent when the aggregator's
    defaults for the source's class already name the port; ``component_type`` may be absent
    when the tags alone identify the channel. Neither is interpreted here — both are checked
    against the aggregator's declared channels once its class is available.
    """

    item_kind: Literal["aggregator_feed"] = "aggregator_feed"

    output: Optional[str] = None
    component_type: Optional[str] = None
    tags: Tuple[str, ...] = ()
    weight: int = 0
    dispatch: Optional[DispatchSpec] = None


#: The three input shapes as one discriminated union. Annotating a field with this alias
#: rather than with the base class is what keeps a wire's ports and a feed's tags alive
#: through model construction.
AnyInputItem = Annotated[
    Union[DefaultInputs, ExplicitWire, AggregatorFeed],
    Field(discriminator="item_kind"),
]

#: One value of a ``sizing_sources`` block: a single provider for a field that reads one
#: fact, or a list of providers for a field whose law sums over many. An empty list is a
#: legal and explicit statement that no component feeds that fact.
AnySizingSource = Union[SourceReference, Tuple[SourceReference, ...]]


class ComponentEntry(BaseModel):
    """One component of the energy system: what it is and everything it depends on.

    An entry is self-contained by design. Its key in the enclosing mapping is the
    component's name and its whole identity — the string ``inputs``, ``sizing_sources``, the
    results and the audit all use — so an entry carries no identifier of its own. Its value
    states the class, where the configuration comes from (a preset, a named constructor or a
    complete ``config`` block), the sparse overrides, the inputs and the sizing sources.

    Exactly one of ``preset`` and ``constructor`` is present unless ``config`` is complete on
    its own: giving both is contradictory, giving neither says nothing. Whether a ``config``
    block really is complete depends on the class, so the check made from the file alone is
    the weaker one that at least one of the three is there.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The keys an entry may carry, in the canonical order the emitter writes them.
    ENTRY_KEYS: ClassVar[Tuple[str, ...]] = (
        "class",
        "preset",
        "constructor",
        "config",
        "inputs",
        "sizing_sources",
    )

    #: Wire spelling of the ``class_path`` field: ``class`` is a Python keyword.
    CLASS_KEY: ClassVar[str] = "class"

    name: str
    class_path: str
    preset: Optional[str] = None
    constructor: Optional[ConstructorCall] = None
    config: Mapping[str, Any] = Field(default_factory=dict)
    inputs: Tuple[AnyInputItem, ...] = ()
    sizing_sources: Mapping[str, AnySizingSource] = Field(default_factory=dict)

    def sizing_references(self) -> Tuple[Tuple[str, SourceReference], ...]:
        """Flattens ``sizing_sources`` into ``(fact, reference)`` pairs.

        Scalar and list values mean the same thing to every check that walks the file's
        reference graph; only the checks that care about cardinality tell them apart.
        Flattening here means those walks are written once rather than once per shape, and
        the fact each reference was written under travels along so a message can name it.

        Returns:
            One pair per reference, in the order the file lists them; a fact mapped to
            an empty list contributes nothing.
        """
        pairs = []
        for fact, value in self.sizing_sources.items():
            references = value if isinstance(value, tuple) else (value,)
            for reference in references:
                pairs.append((fact, reference))
        return tuple(pairs)


class Group(BaseModel):
    """A named set of components carrying one on/off flag.

    A group exists so that an add-on — a photovoltaic string, a battery with its energy
    management, an electric vehicle — can be switched off without deleting text or keeping a
    second copy of the file. Turning it off removes its components together with every input
    item and every sizing reference pointing at them, wherever written, and the result must
    equal the same file with the group deleted by hand.

    A group is emphatically not a namespace: component names stay global, references never
    mention the group, and repeated structures are told apart by a naming convention. Groups
    do not nest and a component belongs to at most one of them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The keys a group may carry, in canonical order. Both are required.
    GROUP_KEYS: ClassVar[Tuple[str, ...]] = ("enabled", "components")

    name: str
    enabled: bool
    components: Mapping[str, ComponentEntry] = Field(default_factory=dict)


class VariantOption(BaseModel):
    """One complete alternative world a variant can be resolved to.

    An option is not an override and not a patch: it is one of the shapes the system may
    have, written out in full. Whatever component the surrounding variant touches, every
    option that has that component spells the whole entry — its class, its configuration and
    its wiring — because the two worlds may wire the same component differently and there is
    nothing for a partial statement to be merged into.

    An option holds components and nothing else. Nesting a group or another variant inside
    one would make the exclusivity of the choice depend on a second switch, and the format's
    exclusivity is carried by the shape of the document rather than by a rule a loader solves.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The only key an option may carry. An option with an empty components block is legal
    #: and meaningful: it is how a file spells the world in which the variant adds nothing.
    OPTION_KEYS: ClassVar[Tuple[str, ...]] = ("components",)

    name: str
    components: Mapping[str, ComponentEntry] = Field(default_factory=dict)


class Variant(BaseModel):
    """An exclusive choice between named options, exactly one of which is live.

    Where a group is an independent on/off switch, a variant is a decision: a house has an
    energy management system with a battery, *or* a bare electricity meter wired straight to
    every participant. The two cases cannot be groups, because a group can add and remove
    components but cannot rewire one that survives, and they cannot be two flags either,
    since nothing in the format expresses "on exactly when the other is off".

    The exclusivity needs no constraint solver because the document can only ever name one
    option: ``selected`` holds a single name. That is also why one component name may repeat
    across the options of one variant — only one of them ever exists — while the same name
    appearing outside the variant, or in a second variant, is rejected at load.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The keys a variant may carry, in canonical order. Both are required: a variant
    #: without a selection decides nothing and one without options offers nothing.
    VARIANT_KEYS: ClassVar[Tuple[str, ...]] = ("selected", "options")

    name: str
    selected: str
    options: Mapping[str, VariantOption] = Field(default_factory=dict)

    def selected_components(self) -> Mapping[str, ComponentEntry]:
        """Returns the components of the option this variant is resolved to.

        Every stage that works with the running system asks for this rather than walking the
        options itself, so that "the live world" has one definition. A selection naming no
        option is rejected at load, which is what makes the lookup safe here.

        Returns:
            The selected option's components, or an empty mapping for the — already
            rejected — case of a selection that names no option.
        """
        option = self.options.get(self.selected)
        return option.components if option is not None else {}


class EnergySystemFile(BaseModel):
    """A whole energy-system document as loaded from YAML.

    The document has a fixed, small top level: the schema version that pins the format, a
    name and description, the ungrouped components, the groups, the variants, and a metadata
    block only generated files carry. Simulation parameters — time range, resolution,
    post-processing — are deliberately not part of it: the same energy system is run over
    different periods, and mixing the two would force a copy of the system per run.

    Components live in three places — at the top level, inside groups and inside the options
    of variants — and all of them are equally components of the system; the place says only
    how the component can be switched. Two readings of the document follow from that and must
    not be confused. :meth:`all_components` is the *selected* world: what actually runs, and
    the set every reference resolves against. :meth:`declared_components` is everything the
    document writes down, the options nobody selected included, which is what the loader
    checks and what a reference into an unselected option is allowed to name — the reference
    is dropped when the option loses, exactly as a reference into a disabled group is.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The one schema version this package reads. A file naming another version is
    #: rejected rather than interpreted: a format change that mattered got a new number.
    SUPPORTED_SCHEMA_VERSION: ClassVar[int] = 3

    #: The keys the document may carry, in the canonical order the emitter writes them.
    TOP_LEVEL_KEYS: ClassVar[Tuple[str, ...]] = (
        "schema_version",
        "name",
        "description",
        "components",
        "groups",
        "variants",
        "metadata",
    )

    schema_version: int
    name: str
    description: Optional[str] = None
    components: Mapping[str, ComponentEntry] = Field(default_factory=dict)
    groups: Mapping[str, Group] = Field(default_factory=dict)
    variants: Mapping[str, Variant] = Field(default_factory=dict)
    metadata: Optional[Mapping[str, Any]] = None

    def all_components(self) -> Dict[str, ComponentEntry]:
        """Returns every component of the selected system, wherever it is written, by name.

        Names are global across the whole document, so this mapping is the file's namespace
        and the set every reference resolves against. It is selection-aware: a variant
        contributes the components of the option it selects and nothing from the options it
        does not, because those describe worlds this file is not. Ungrouped entries come
        first, then the groups and then the variants in document order, which makes the
        result stable enough to list in a message.

        Returns:
            A fresh mapping from component name to entry; mutating it does not affect
            the file.
        """
        merged: Dict[str, ComponentEntry] = dict(self.components)
        for group in self.groups.values():
            merged.update(group.components)
        for variant in self.variants.values():
            merged.update(variant.selected_components())
        return merged

    def declared_components(self) -> Dict[str, ComponentEntry]:
        """Returns every component the document writes down, unselected options included.

        The loader needs the wider set that :meth:`all_components` deliberately narrows. A
        reference from a surviving component into a variant option is legal whichever option
        wins — that is what lets a building list ``- ems`` while the metering variant may
        resolve to a world without one — so a reference is checked against what the document
        declares and dropped later if its target's option lost.

        Where one name occurs in several options of one variant, the last of them wins; the
        entries themselves are reached through :meth:`declared_entries`, which keeps all of
        them, so no option escapes the per-entry checks.

        Returns:
            A fresh mapping from component name to entry.
        """
        merged: Dict[str, ComponentEntry] = dict(self.components)
        for group in self.groups.values():
            merged.update(group.components)
        for variant in self.variants.values():
            for option in variant.options.values():
                merged.update(option.components)
        return merged

    def declared_entries(self) -> Tuple[ComponentEntry, ...]:
        """Returns every entry the document holds, including two options' takes on one name.

        The one place a component name can carry two different entries is two options of the
        same variant, and both of them have to obey the rules a single entry obeys: a file is
        wrong if the option nobody selected today is malformed, because tomorrow's file
        selects it. So the per-entry checks walk this sequence rather than a mapping.

        Returns:
            The entries in document order: the ungrouped ones, then the groups', then every
            option's, each entry carrying its own name.
        """
        entries = list(self.components.values())
        for group in self.groups.values():
            entries.extend(group.components.values())
        for variant in self.variants.values():
            for option in variant.options.values():
                entries.extend(option.components.values())
        return tuple(entries)

    def group_of(self, component_name: str) -> Optional[str]:
        """Returns the name of the group a component sits in, if any.

        Several rules need to name the group a component came from — the report of what an
        off switch removed, the message explaining why a reference dangles — and the model
        stores the containment the other way round. The first match is returned, which in a
        valid file is the only one, because a component belongs to at most one group.

        Args:
            component_name: The name to look for.

        Returns:
            The group's name, or ``None`` for an ungrouped or unknown component.
        """
        for group_name, group in self.groups.items():
            if component_name in group.components:
                return group_name
        return None
