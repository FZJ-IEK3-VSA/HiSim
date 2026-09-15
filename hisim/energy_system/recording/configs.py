"""How one observed configuration becomes an entry's ``preset`` and ``config`` keys.

Two shapes exist and the choice between them is not a judgement. A configuration built through a
``@preset`` classmethod carries the preset's wire name as a stamp, so the entry names that preset
and states only what the setup changed afterwards; a configuration with no stamp is written as a
complete block, because nothing about it is recoverable and guessing a preset by matching values
against every preset of the class would be an inference the format's first principle forbids.

Inside a stamped entry a second judgement is made, field by field, and it is the one that makes a
twin reusable (A-P3.1). A resolved configuration carries a ``sizing_record`` naming, per field, the
law that computed it, the facts that law read and the value it produced, so the recorder can tell a
number a law computed from a number the setup typed. A computed field is **left out of the block
altogether** whenever the recorded system itself declares a provider for every fact that law reads:
the preset's own ``AUTO`` then stands, the file recomputes the number instead of repeating it, and
it recomputes a *different* number for a different building, which is what a base file has to do.
No line is written for such a field — ``preset: rooftop`` already says the array is sized from the
roof, and a line restating the preset's default in order to annotate it says nothing the loader or
a reader needs; the value that run produced lives in the realized record, per field with its law
and its provider. A field the setup assigned stays the concrete override it always was, and a
computed field whose fact nothing in this system provides yet stays concrete too, with a comment
naming the missing fact; it disappears on the re-record that follows that provider's conversion.

One case the decision did not name is pinned for a reason the comment states: a fact that several
recorded components declare cannot be left to the preset while a twin writes no ``sizing_sources``
block, because the file would then be ambiguous rather than reusable. A pinned line is real data,
so it keeps both its number and the comment saying why the number is there.

A field whose *preset* pinned a law of its own — the pellet boiler's "a twelfth of the maximum" —
needs no pin and gets none. Omission leaves the field holding the ``SizingLaw`` the preset put
there, and the resolver evaluates that object in preference to the class rule; only the bare word
``AUTO``, written into the file, would replace it with the class law. Leaving the line out is
therefore what preserves the preset's law, not what loses it.

Both branches encode through the record writer of :mod:`hisim.energy_system.record`, so the
portable ``${var}`` spelling of a path, the enum-by-name rule and the omission of the identity are
inherited rather than reimplemented, and a recorded block is the same text a realized record would
have written for the same configuration.
"""

# clean

from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Iterable, List, Mapping, Optional, Tuple

from hisim.config import AUTO, ConfigBuilder, SizingLaw, declared_facts_of, preset_provenance, presets_of
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemRecordingError
from hisim.energy_system.record import ConfigBlockWriter


class FactProviders:
    """Which components of one recorded system declare which sizing fact.

    A field may be written as ``AUTO`` only if the file it is written into can compute it again,
    and that is a property of the *system*, not of the field: the law reads facts, and a fact is
    answered by whichever component's configuration class declares it in ``SIZING_CONTRIBUTIONS``.
    This class is that lookup, built once per recording from the components the run actually
    registered, so the answer follows the conversion state of the fleet instead of a list somebody
    has to keep up to date.

    Providers are collected in registration order and never sorted, because the recorder's output
    has to be byte-identical between two runs and a set iteration would not be.
    """

    def __init__(self, providers: Mapping[str, Tuple[str, ...]]) -> None:
        """Stores the lookup.

        Args:
            providers: Fact name to the names of the components declaring it, in registration
                order.
        """
        self.providers = dict(providers)

    @classmethod
    def of(cls, components: Iterable[Any]) -> "FactProviders":
        """Reads the declarations off the configuration classes of one observed system.

        Args:
            components: The observed components, in registration order; each configuration class
                is asked for its declared facts through
                :func:`~hisim.config.contributions.declared_facts_of`, the one derivation the
                sizing engine's own provider table uses, so a class declaring a fact in two
                contributions counts once here exactly as it does there.

        Returns:
            The lookup for that system.
        """
        found: Dict[str, List[str]] = {}
        for component in components:
            for fact in declared_facts_of(type(component.config)):
                found.setdefault(fact, []).append(component.name)
        return cls({fact: tuple(names) for fact, names in found.items()})

    def of_fact(self, fact: str) -> Tuple[str, ...]:
        """Names the components declaring one fact.

        Args:
            fact: The sizing fact a law reads.

        Returns:
            The declaring components in registration order; empty when this system has none.
        """
        return self.providers.get(fact, ())


class TwinComments:
    """The wording of every comment a recorded twin carries, in one place.

    A twin's comments are one sentence with two endings — this number is pinned, and here is which
    of the two reasons pinned it — and collecting the phrasings on one class is what stops that
    vocabulary from growing a synonym per call site. Nothing reads them back: they are a rendering
    of the ``sizing_record``, and stripping every one of them changes nothing about what the file
    does.

    A field the file re-sizes has no comment because it has no line: it is omitted from the block
    and the preset's own ``AUTO`` stands (A-P3.1, revised in review of #745). Only a pinned line is
    annotated, and only because that line is real data whose reason a reader cannot otherwise see.

    Every phrase is one line and depends on nothing but the observation, because the freshness job
    re-records the fleet and compares bytes.
    """

    #: How a pinned line opens; the reason follows.
    PINNED: ClassVar[str] = "pinned: {reason}"

    #: Reason for a value a law computed from a fact this system has no provider for yet. The line
    #: disappears on the re-record that follows that provider's conversion.
    NO_PROVIDER: ClassVar[str] = "no provider of {fact} in this system yet"

    #: Reason for a value a law computed from a fact several recorded components declare. Naming
    #: one of them would be a guess and naming none would make the file ambiguous, so the number
    #: stays until a twin may carry a ``sizing_sources`` block.
    SEVERAL_PROVIDERS: ClassVar[str] = "{fact} is declared by {providers}, and a twin writes no sizing_sources"

    #: Separator between the several providers of one fact.
    SEPARATOR: ClassVar[str] = ", "


@dataclass(frozen=True)
class SizedFieldDecision:
    """What the recorder decided about one field a law computed, and why.

    One of these is produced for every entry of a stamped configuration's ``sizing_record`` that
    reaches the recorded block, and it carries the two outcomes the block itself no longer shows.
    A field left to the preset writes no line at all, so this object is the only place the run's
    :attr:`value` and the :attr:`sources` that made the omission safe are still stated, and it is
    what the session holds the written file to: omitting the field claims the file recomputes
    :attr:`value`, and that claim is verified by resolving the file rather than trusted.

    ``reason`` is what separates the two outcomes. ``None`` means the field was left out of the
    block for the preset's own ``AUTO`` to answer; anything else is the sentence explaining why the
    number stayed, rendered after ``pinned:`` on the line that kept it.

    Example: the PV array's ``power_in_watt``, computed by the rooftop law from the building's roof
    area, produces ``SizedFieldDecision("PVSystem", "power_in_watt", "_rooftop_power_in_watt",
    22272.28, ("Building.roof_area_in_m2",))`` — no ``reason``, hence no line in the twin and one
    entry in the session's check list.
    """

    component: str
    field: str
    law: str
    value: Any
    sources: Tuple[str, ...] = ()
    reason: Optional[str] = None

    @property
    def auto(self) -> bool:
        """Whether the field was left to the preset's ``AUTO`` rather than written as its number.

        Returns:
            ``True`` for a field the file re-sizes, which the block therefore omits; ``False`` for
            a pinned one, which the block states with a comment.
        """
        return self.reason is None

    def comment(self) -> Optional[str]:
        """Builds the trailing comment the field's line carries, when it has a line at all.

        Returns:
            The reason the number stayed, rendered after ``pinned:``, for a pinned field; ``None``
            for a field left to the preset, which the twin does not write and so cannot annotate.
        """
        if self.auto:
            return None
        return TwinComments.PINNED.format(reason=self.reason)


@dataclass(frozen=True)
class EntryConfiguration:
    """The configuration half of one entry, and what the recorder decided about its sized fields.

    The two travel together because they are one judgement seen from two sides: ``members`` is what
    the file says, ``decisions`` is why it says it — including, for a field left to the preset, why
    ``members`` says nothing at all. The builder splices the first into the entry, renders the
    pinned decisions as the entry's comments and hands the omitted ones on to the session, which
    resolves the written file and holds every one of them to the value the run produced.
    """

    members: Dict[str, Any]
    decisions: Tuple[SizedFieldDecision, ...] = ()


class EntryConfigWriter:
    """Writes the configuration half of one component entry: a preset reference or a full block.

    Constructed once per recording over the block writer that owns the path resolver, and asked per
    component. Keeping it an object rather than a set of functions is what lets every entry of one
    recording share one resolver, which matters because two entries symbolising the same directory
    against two different registries would produce a file that is portable in one half and not in
    the other. It shares the fact lookup for the same reason: whether a field may be left to the
    preset is a question about the whole recorded system, and every entry has to answer it the
    same way.

    The class deliberately knows nothing about the rest of an entry. It returns the two keys it
    owns and the builder splices them in, so that the decision "preset or full block" has exactly
    one implementation and the entry assembly has none of it.
    """

    #: Key an entry carries when the configuration came from a ``@preset`` classmethod, holding
    #: that preset's wire name.
    PRESET_KEY: ClassVar[str] = "preset"

    #: Key holding the configuration itself: the complete block for an unstamped configuration, the
    #: sparse deviation from a fresh preset for a stamped one, and absent when that deviation is
    #: empty because the entry is then already complete.
    CONFIG_KEY: ClassVar[str] = "config"

    def __init__(self, writer: ConfigBlockWriter, providers: Optional[FactProviders] = None) -> None:
        """Prepares the writer for one recording.

        Args:
            writer: The record's block writer, carrying the path resolver every block is
                symbolised against.
            providers: Which component of the recorded system declares which sizing fact; an empty
                lookup when omitted, under which no field reading a fact can be left to the
                preset, because nothing in the system would answer its law.
        """
        self.writer = writer
        self.providers = providers if providers is not None else FactProviders({})

    def fields(self, name: str, config: Any, setup: str) -> EntryConfiguration:
        """Builds the entry members one configuration writes: ``preset`` and/or ``config``.

        Args:
            name: The component's runtime name, which is also the entry's key.
            config: The live configuration object as the observation holds it.
            setup: The setup module being recorded, for the message.

        Returns:
            The members to splice into the entry — ``config`` alone for an unstamped
            configuration, ``preset`` alone when a preset reproduces it exactly, and both when the
            setup changed something after building it — together with the decisions made about its
            sized fields, which are empty for an unstamped configuration.

        Raises:
            EnergySystemRecordingError: ``EF-R4`` when the stamp names a preset the class no longer
                declares.
        """
        stamp = preset_provenance(config)
        if stamp is None:
            return EntryConfiguration({self.CONFIG_KEY: self.writer.block(name, config)})
        written = self.overrides(name, config, stamp, setup)
        if not written.members:
            return EntryConfiguration({self.PRESET_KEY: stamp}, written.decisions)
        return EntryConfiguration(
            {self.PRESET_KEY: stamp, self.CONFIG_KEY: written.members}, written.decisions
        )

    def overrides(self, name: str, config: Any, stamp: str, setup: str) -> EntryConfiguration:
        """Diffs one stamped configuration against a fresh build of its preset, field by field.

        The baseline is a fresh instance of the same preset under the same instance name, encoded
        by the same writer, so a field that survived the preset untouched produces no line and a
        field the setup assigned afterwards produces exactly one. Comparing the *encoded* forms
        rather than the objects is what makes an enum that dumps to the same member name and a
        path that symbolises to the same reference count as unchanged, which is the property that
        keeps a recorded diff readable.

        Every line the diff produces is then put to :meth:`decide`, and a line the file can
        compute again is **dropped**: the field is left out of the block so that the preset's own
        ``AUTO`` answers it, which is what ``preset: rooftop`` already promised. A field the diff
        did not produce is never reached: the preset already states it, and a preset's own value is
        not the recorder's to re-open.

        Args:
            name: The component's runtime name; the preset builds its identity from it, so the
                baseline has to be built under the same name or every entry would deviate in its
                identity field alone.
            config: The live configuration object.
            stamp: The preset's wire name, as the provenance records it.
            setup: The setup module being recorded, for the message.

        Returns:
            The sparse block, in the configuration's own field order, and the per-field decisions;
            the block is empty when the preset reproduces the configuration exactly, and also when
            every line the diff produced was a field the preset can size again.

        Raises:
            EnergySystemRecordingError: ``EF-R4`` when the class declares no preset of that name.
        """
        builder = presets_of(type(config)).get(stamp)
        if builder is None:
            raise EnergySystemRecordingError(
                EnergySystemErrorId.RECORDED_PRESET_GONE,
                f"{setup}:{name}",
                f"the configuration of '{name}' is stamped with the preset '{stamp}', which "
                f"{type(config).__name__} no longer declares.",
                alternatives=sorted(presets_of(type(config))),
                alternatives_label="presets",
                offending_value=stamp,
                remedy=(
                    f"The stamp is set by {ConfigBuilder.PRESET_PREFIX}* classmethods alone; a "
                    "renamed preset has to keep its wire name or the recordings have to be redone."
                ),
            )
        fresh = builder.build(name)
        baseline = self.writer.block(name, self.unresolved(fresh))
        current = self.writer.block(name, config)
        block = {key: value for key, value in current.items() if key not in baseline or baseline[key] != value}
        decisions = self.decide(name, config, block)
        for decision in decisions:
            if decision.auto:
                block.pop(decision.field, None)
        return EntryConfiguration(block, decisions)

    def decide(self, name: str, config: Any, block: Mapping[str, Any]) -> Tuple[SizedFieldDecision, ...]:
        """Decides, field by field, which computed values the file may compute again (A-P3.1).

        The run's ``sizing_record`` is the input: it exists only on a configuration a law was
        actually run over, and it names the field, the law, the facts and the value. A field the
        record names but whose current value is no longer the one the law produced was assigned by
        the setup after resolving, so it is not a computed field at all and keeps its number
        without a comment.

        The comparison is of values, so a post-resolve assignment that *equals* what the law
        produced is treated as the law's: the value is the law's value, and a file that recomputes
        it lands on the same number. That is deliberate rather than a gap. A setup writing a pin
        means one of two things, and both survive it — a pin that is meant to differ from the law
        does differ, and is written out; a pin that is meant to hold whatever the author typed is
        assigned *before* ``resolve()``, where the sizing record never names the field at all
        because the law was never run over it.

        Args:
            name: The component's runtime name.
            config: The live configuration object, whose current value says whether the setup
                assigned the field again after resolving it.
            block: The sparse block as the diff produced it, before any field is dropped.

        Returns:
            One decision per computed field the block states, in resolution order.
        """
        decisions: List[SizedFieldDecision] = []
        for entry in getattr(config, "sizing_record", ()) or ():
            if entry.field not in block:
                continue
            value = ConfigBlockWriter.plain(entry.value, name, entry.field)
            if ConfigBlockWriter.plain(getattr(config, entry.field, None), name, entry.field) != value:
                continue
            decisions.append(self.field_decision(name, entry, value))
        return tuple(decisions)

    def field_decision(self, name: str, entry: Any, value: Any) -> SizedFieldDecision:
        """Decides about one computed field: left to the preset, or concrete with why it stayed.

        Only the facts decide. Two conditions pin a value, and each produces a sentence rather than
        silence, because a pinned line in a twin is a statement about the fleet's conversion state
        and a reader has to be able to tell which of them they are looking at: a fact has no
        provider in this system yet, or a fact has more than one. The third outcome — the field is
        left to the preset — produces no line and therefore no sentence.

        Whose law computed the value does not enter into it. ``entry.law`` is the *effective* law,
        the preset's own where a preset pinned one, and omitting the field is what keeps that law
        in place: the preset builds the field holding its ``SizingLaw`` object and the resolver
        prefers it to the class rule. A law the preset pinned reads whatever it reads — the pellet
        boiler's reads a sibling field and therefore no fact at all — and is judged on that alone.

        Args:
            name: The component's runtime name.
            entry: The field's :class:`~hisim.config.SizingRecordEntry`.
            value: The value the run produced, already reduced to plain data.

        Returns:
            The decision, carrying the sources of an omitted field or the reason of a pinned one.
        """
        sources: List[str] = []
        for fact in entry.facts_read:
            declaring = self.providers.of_fact(fact)
            if not declaring:
                return SizedFieldDecision(
                    name, entry.field, entry.law, value,
                    reason=TwinComments.NO_PROVIDER.format(fact=fact),
                )
            if len(declaring) > 1:
                return SizedFieldDecision(
                    name, entry.field, entry.law, value,
                    reason=TwinComments.SEVERAL_PROVIDERS.format(
                        fact=fact, providers=TwinComments.SEPARATOR.join(declaring)
                    ),
                )
            sources.append(f"{declaring[0]}.{fact}")
        return SizedFieldDecision(name, entry.field, entry.law, value, tuple(sources))

    @classmethod
    def unresolved(cls, baseline: Any) -> Any:
        """Renders a fresh preset's own sizing laws the way an unsized field is already rendered.

        A preset may pin a field to a law of its own rather than leave it to the class rule — the
        pellet boiler's "a twelfth of the maximum" is the standing example — and such a field
        still holds the law object when the preset has only been built and not yet resolved. The
        block writer refuses an object, and rightly so for a value that is about to be written;
        but the baseline is never written, only compared, and the honest reading of a field that
        has not been computed yet is the same sentinel every other unsized field carries.

        Substituting it keeps the comparison total and keeps it correct: a sentinel can never
        equal the concrete number the run produced, so a field the preset sizes always lands in
        the deviation block, where :meth:`decide` then states what became of it.

        Args:
            baseline: A freshly built, unresolved configuration.

        Returns:
            A shallow copy with every law-valued field replaced by the unsized sentinel; the
            argument itself when it holds no laws.
        """
        laws = [
            field.name
            for field in dataclasses.fields(baseline)
            if isinstance(getattr(baseline, field.name, None), SizingLaw)
        ]
        if not laws:
            return baseline
        substituted = copy.copy(baseline)
        for field_name in laws:
            setattr(substituted, field_name, AUTO)
        return substituted
