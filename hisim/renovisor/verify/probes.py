"""The probes of the harness: the capability probe set, each with the base it is measured from.

There is no second probe generator. The probes are :meth:`ProbeSet.build`'s, the same set the
capability document is aggregated from, and what this module adds to each is a **base** -- the
request the probe is diffed against -- chosen so that the diff is exactly the probe's own change.

The capability probes are all written as patches on one anchor, which is what the capability
document needs. Measuring every one of them from the anchor would not isolate the change: an
option probe of ``external_insulation`` also switches the measure on, a field probe of
``house.ventilation.air_tightness`` also adds the ventilation block, and a SCOP probe also turns the
gas boiler into a heat pump. So the base is chosen per kind (:meth:`ProbeBases.natural_base`):

========== =============================================================================
kind       base
========== =============================================================================
anchor     none: it is the root every other base descends from
bare       the anchor; the change is the optional blocks and the roof shape it removes
block      the anchor; the change is the block it adds
measure    the anchor; the change is the measure switched on with its required options
option     the probe ``measure:<id>``, the smallest package carrying the measure, so the
           change is the one option
field      the anchor with the field's *prelude* (``ProbeSet.prelude``) -- the block it needs
           present (``ProbeSet.FIELD_BLOCK``), the heat pump a SCOP needs
           (``ProbeSet.FIELD_PRELUDE``), the layer an ``added_insulation`` leaf lives in, the
           priced package entry a cost leaf lives in -- read from those tables rather than from
           the probe; mostly another probe of the set (``block:<name>``,
           ``field:heating.type_of_system=air_source_heat_pump``) or the anchor itself
pair       the anchor; a pair is the spec's §7 combination, two changes on purpose
========== =============================================================================

Where a probe sends the value its natural base already carries -- ``building_type=detached_sfh``
on an anchor that is detached, the first value of an option that ``measure:<id>`` already sends --
the diff would be empty, and the probe is measured from its **sibling** instead: the first probe
of the same kind and subject that sends a different value from the same natural base, and of those
the first the request validation accepts, where one does. The change
is then the probe's own path with the sibling's value on the left (``semi_detached_sfh ->
detached_sfh``), which is still one change. A probe with no such sibling keeps its natural base,
and its empty diff is reported as what it is. The ``material`` option is not such a probe any more:
it sends a second real row of ``materials.yaml`` (``MaterialRows.alternative``) where its base
carries the mockup's, so its change is the material object, conductivity and all.

Every base is a request, cached by hash like any other (:mod:`hisim.renovisor.verify.runner`), so
a probe whose base is another probe costs that probe nothing, and a base is named after the probe
that sends the same request wherever one does.
"""

import copy
from dataclasses import dataclass
from functools import cached_property
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from hisim.renovisor.capabilities import Probe, ProbeKind, ProbeSet, SchemaLeaves
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.request import CatalogueTable, Request, RequestError, ValueType
from hisim.renovisor.verify.leaves import ABSENT, Change, RequestLeaves, diff, same_value


@dataclass(frozen=True)
class VerificationProbe:
    """One capability probe, with the request it is measured from.

    Args:
        probe: The capability probe.
        document: The request it sends.
        base_name: The name of the probe that sends the base request, ``"anchor"``, or a
            description of the request when no probe sends it; ``None`` for the anchor itself.
        base_document: The base request; ``None`` for the anchor.
        changes: The request paths the probe declares it changes, in :class:`RequestLeaves`
            spelling; every leaf of the stage-1 diff has to lie under one of them.
        base_reason: One sentence on why this base was chosen.
    """

    probe: Probe
    document: Dict[str, Any]
    base_name: Optional[str]
    base_document: Optional[Dict[str, Any]]
    changes: Tuple[str, ...]
    base_reason: str

    @property
    def name(self) -> str:
        """Return the probe's name, which is the capability probe's."""
        return self.probe.name

    @property
    def category(self) -> str:
        """Return what the matrix groups and filters the probe by.

        A measure id for a measure or option probe, the top-level block for an inventory probe
        (``house.building``, ``house.heating``), ``combination`` for a pair and ``anchor`` for the
        two whole-request probes.
        """
        kind = self.probe.kind
        subject = self.probe.subject or ""
        if kind in (ProbeKind.MEASURE, ProbeKind.OPTION):
            return f"measure:{subject.split('.')[0]}"
        if subject.startswith(f"{RequestLeaves.MEASURES}[id="):
            return f"measure:{subject[len(RequestLeaves.MEASURES) + len('[id='):subject.index(']')]}"
        if kind in (ProbeKind.FIELD, ProbeKind.BLOCK):
            return ".".join(subject.split(".")[:2 if subject.startswith(ProbeSet.HOUSE_PREFIX) else 1])
        if kind is ProbeKind.PAIR:
            return "combination"
        return "anchor"

    @cached_property
    def request_hash(self) -> str:
        """Return the probe request's cache key, computed once (:meth:`Request.hash_of`)."""
        return Request.hash_of(self.document)

    @cached_property
    def base_request_hash(self) -> Optional[str]:
        """Return the base request's cache key, computed once; ``None`` for the anchor."""
        return None if self.base_document is None else Request.hash_of(self.base_document)

    @cached_property
    def stage_one(self) -> Tuple[Change, ...]:
        """Return the request diff from the base to the probe (stage 1), computed once.

        The runner's verdict and :class:`Completeness` both read it.
        """
        if self.base_document is None:
            return ()
        return diff(RequestLeaves.of(self.base_document), RequestLeaves.of(self.document))

    def stray_changes(self, changes: Sequence[Change]) -> Tuple[str, ...]:
        """Return the changed leaves that lie under none of the declared paths.

        A change *above* a declared path is not stray when it is an empty container appearing or
        disappearing: ``measures[id=battery_system].options`` is the leaf ``{}`` while the package
        carries no option, and stops being one when the probe sets the first.
        """
        return tuple(
            change.path
            for change in changes
            if not any(
                RequestLeaves.is_under(change.path, declared)
                or (RequestLeaves.is_under(declared, change.path) and change.is_empty_container)
                for declared in self.changes
            )
        )


class ProbeBases:
    """Chooses each probe's base, per the table of the module docstring."""

    @classmethod
    def build(
        cls, probes: Optional[Sequence[Probe]] = None, anchor: Optional[Mapping[str, Any]] = None
    ) -> Tuple[VerificationProbe, ...]:
        """Return every probe with its base.

        Args:
            probes: The probes to verify; the whole capability set when omitted. A subset is
                measured from the same bases as the whole set, because bases are named and
                siblings are found in the whole set either way.
            anchor: The anchor request; :meth:`ProbeSet.anchor` when omitted.

        Returns:
            One :class:`VerificationProbe` per probe, in the given order.
        """
        root = dict(anchor) if anchor is not None else ProbeSet.anchor()
        everything = ProbeSet.build()
        chosen = tuple(probes) if probes is not None else everything
        # The whole set, so a subset finds the same named bases and the same siblings; a given
        # probe replaces the set's probe of the same name, so an injected probe is the one measured.
        known: Dict[str, Probe] = {probe.name: probe for probe in everything}
        known.update({probe.name: probe for probe in chosen})
        documents = {name: probe.document(root) for name, probe in known.items()}
        names_by_hash: Dict[str, str] = {}
        for name in (probe.name for probe in (*everything, *chosen)):
            names_by_hash.setdefault(Request.hash_of(documents[name]), name)
        return tuple(cls._one(probe, root, known, documents, names_by_hash) for probe in chosen)

    @classmethod
    def _one(
        cls,
        probe: Probe,
        anchor: Mapping[str, Any],
        known: Mapping[str, Probe],
        documents: Mapping[str, Dict[str, Any]],
        names_by_hash: Mapping[str, str],
    ) -> VerificationProbe:
        """Choose one probe's base: its natural base, or a sibling where that is a no-op."""
        document = documents[probe.name]
        if probe.kind is ProbeKind.ANCHOR:
            return VerificationProbe(
                probe=probe, document=document, base_name=None, base_document=None, changes=(),
                base_reason="the anchor is the root every base descends from",
            )
        base, reason = cls.natural_base(probe, anchor)
        if Request.hash_of(base) == Request.hash_of(document):
            sibling = cls._sibling(probe, anchor, known, documents)
            if sibling is not None:
                base = documents[sibling]
                reason = (
                    f"it sends the value its natural base ({reason}) already carries, so it is "
                    f"measured from its sibling {sibling}"
                )
            else:
                reason = f"{reason}; no sibling sends another value, so the probe changes nothing"
        name = names_by_hash.get(Request.hash_of(base), f"({reason})")
        return VerificationProbe(
            probe=probe,
            document=document,
            base_name=name,
            base_document=copy.deepcopy(base),
            changes=cls.declared_changes(probe),
            base_reason=reason,
        )

    @classmethod
    def natural_base(cls, probe: Probe, anchor: Mapping[str, Any]) -> Tuple[Dict[str, Any], str]:
        """Return the request a probe is measured from before siblings are considered, and why.

        Args:
            probe: A probe that is not the anchor.
            anchor: The anchor request.

        Returns:
            ``(request, reason)``.
        """
        if probe.kind is ProbeKind.OPTION:
            measure_id = str(probe.subject).split(".", maxsplit=1)[0]
            base = Probe(name=f"measure:{measure_id}", kind=ProbeKind.MEASURE, measures=[ProbeSet.package(measure_id)])
            return base.document(anchor), f"measure:{measure_id}, the measure with its required options"
        if probe.kind is ProbeKind.FIELD:
            # The prelude is read from the tables that declare it, never from the probe: a probe
            # that slipped a second change into its own patch would otherwise carry it into its
            # base, and stage 1 would not see it.
            prelude = ProbeSet.prelude(str(probe.subject))
            parts = [*prelude.house]
            parts.extend(RequestLeaves.measure_path(str(entry["id"])) for entry in prelude.measures or [])
            return prelude.document(anchor), (
                f"the anchor with {', '.join(parts)} set first, which the field needs" if parts else "the anchor"
            )
        return copy.deepcopy(dict(anchor)), "the anchor"

    @classmethod
    def _sibling(
        cls,
        probe: Probe,
        anchor: Mapping[str, Any],
        known: Mapping[str, Probe],
        documents: Mapping[str, Dict[str, Any]],
    ) -> Optional[str]:
        """Return the first probe of the same kind and subject that differs only in the probe's own value.

        A sibling the request validation accepts is preferred to one it refuses, which would be a
        base that does not translate and leave stage 3 nothing to compare with
        (``location.country=NL`` rather than ``=ES`` for ``location.country=IE``, since ES has no
        TABULA typology). A refused sibling is taken only when every sibling is refused -- the
        ``added_insulation`` probes, which the semantic checks refuse on purpose.
        """
        own_base = Request.hash_of(cls.natural_base(probe, anchor)[0])
        own = Request.hash_of(documents[probe.name])
        siblings = [
            name
            for name, other in known.items()
            if name != probe.name
            and other.kind is probe.kind
            and other.subject == probe.subject
            and Request.hash_of(documents[name]) != own
            and Request.hash_of(cls.natural_base(other, anchor)[0]) == own_base
        ]
        return next((name for name in siblings if cls._accepted(documents[name])), siblings[0] if siblings else None)

    @staticmethod
    def _accepted(document: Mapping[str, Any]) -> bool:
        """Return whether the request validation accepts a request."""
        try:
            Request.parse(document)
        except RequestError:
            return False
        return True

    @classmethod
    def declared_changes(cls, probe: Probe) -> Tuple[str, ...]:
        """Return the request paths a probe changes relative to its base.

        Args:
            probe: The probe.

        Returns:
            The paths, in :class:`RequestLeaves` spelling; a field probe's prelude is not among
            them, because it is part of the base.
        """
        subject = probe.subject or ""
        if probe.kind in (ProbeKind.FIELD, ProbeKind.BLOCK):
            return (subject,)
        if probe.kind is ProbeKind.MEASURE:
            return (RequestLeaves.measure_path(subject),)
        if probe.kind is ProbeKind.OPTION:
            measure_id, option = subject.split(".", 1)
            return (f"{RequestLeaves.measure_path(measure_id)}.options.{option}",)
        paths: List[str] = [f"{ProbeSet.HOUSE_PREFIX}{path}" for path in probe.house]
        paths.extend(RequestLeaves.measure_path(str(entry["id"])) for entry in probe.measures or [])
        paths.extend(f"location.{key}" for key in probe.location)
        paths.extend(f"applicant.{key}" for key in probe.applicant)
        return tuple(paths)


@dataclass(frozen=True)
class MissingProbe:
    """One settable thing no probe changes.

    Args:
        path: The request path, or ``measures[id=<id>].options.<name>`` for a catalogue option.
        wanted: What is missing: a value, ``"any value"``, ``"a second value"`` or ``"on"``.
    """

    path: str
    wanted: str

    def message(self) -> str:
        """Return the one line the failure list shows."""
        return f"no probe changes {self.path} to {self.wanted}"


class Completeness:
    """Every settable leaf, enum value, range end, measure and option value has a probe (spec §3 rule 2).

    Checked against the two contracts the request is written against, never against the probe
    set's own tables: the vendored request JSON Schema for ``location``, ``house``, ``applicant``
    and a measure's ``cost`` block, and the frozen catalogue table (which T-CAT keeps equal to
    ``measures.yaml``) for the measures and their options.

    A leaf counts as probed at a value when some probe's stage-1 diff changes it *to* that value;
    a probe that merely carries it does not count. What each leaf needs:

    * an enumeration -- every value; a boolean -- both;
    * a number -- the schema's inclusive ``minimum`` and ``maximum`` where it declares them, and in
      any case two different values, which is what "both ends" is for an end the schema leaves
      open or bounds exclusively;
    * a string -- any value;
    * a measure -- on; a catalogue option -- every listed value, both booleans, two numeric values,
      and for a ``material`` any change of the material at all.
    """

    #: How the cost block of any measure is spelled, since it is the same leaf on every measure.
    COST_PATH: ClassVar[str] = SchemaLeaves.COST_PATH

    @classmethod
    def missing(cls, probes: Sequence[VerificationProbe]) -> Tuple[MissingProbe, ...]:
        """Return every settable thing no probe changes, in schema then catalogue order.

        Args:
            probes: The probes with their bases.

        Returns:
            One :class:`MissingProbe` per gap.
        """
        seen: Dict[str, List[Any]] = {}
        for probe in probes:
            for change in probe.stage_one:
                if change.after is ABSENT:
                    continue
                seen.setdefault(cls._generic(change.path), []).append(change.after)
        gaps: List[MissingProbe] = []
        for path, leaf in SchemaLeaves.settable(ContractFiles.request_schema()):
            gaps.extend(cls._leaf_gaps(path, leaf, seen))
        gaps.extend(cls._catalogue_gaps(probes, seen))
        return tuple(gaps)

    @classmethod
    def _generic(cls, path: str) -> str:
        """Return a leaf path with a measure's cost block spelled for any measure."""
        prefix, marker, rest = path.partition("].cost")
        if marker and prefix.startswith(f"{RequestLeaves.MEASURES}[id="):
            return f"{cls.COST_PATH}{rest}"
        return path

    @classmethod
    def _leaf_gaps(cls, path: str, leaf: Mapping[str, Any], seen: Mapping[str, List[Any]]) -> List[MissingProbe]:
        """Return what one schema leaf lacks."""
        values = seen.get(path, [])
        if "const" in leaf:
            return []
        if "enum" in leaf:
            return [
                MissingProbe(path, repr(value))
                for value in leaf["enum"]
                if not any(same_value(seen, value) for seen in values)
            ]
        types = leaf.get("type")
        types = set(types) if isinstance(types, list) else {types}
        if "boolean" in types:
            return [
                MissingProbe(path, repr(value))
                for value in (True, False)
                if not any(same_value(seen, value) for seen in values)
            ]
        if types & {"number", "integer"}:
            gaps = [
                MissingProbe(path, f"its {keyword} {leaf[keyword]!r}")
                for keyword in ("minimum", "maximum")
                if keyword in leaf and not any(same_value(seen, leaf[keyword]) for seen in values)
            ]
            distinct = {float(value) for value in values if isinstance(value, (int, float))}
            open_end = "minimum" not in leaf or "maximum" not in leaf
            if open_end and len(distinct) < 2:
                gaps.append(MissingProbe(path, "a value at each end" if not distinct else "a value at its other end"))
            return gaps
        return [] if values else [MissingProbe(path, "any value")]

    @classmethod
    def _catalogue_gaps(
        cls, probes: Sequence[VerificationProbe], seen: Mapping[str, List[Any]]
    ) -> List[MissingProbe]:
        """Return the measures never switched on and the option values never sent."""
        switched_on: Set[str] = set()
        for probe in probes:
            for change in probe.stage_one:
                if change.before is ABSENT and change.path.startswith(f"{RequestLeaves.MEASURES}[id="):
                    switched_on.add(change.path.split("]")[0] + "]")
        gaps: List[MissingProbe] = []
        for measure_id in CatalogueTable.ids():
            prefix = RequestLeaves.measure_path(measure_id)
            if prefix not in switched_on:
                gaps.append(MissingProbe(prefix, "on"))
            for option in CatalogueTable.options_of(measure_id):
                path = f"{prefix}.options.{option.name}"
                values = seen.get(path, [])
                if option.value_type is ValueType.MATERIAL:
                    if not any(RequestLeaves.is_under(key, path) for key in seen):
                        gaps.append(MissingProbe(path, "any other material"))
                elif option.values or option.value_type is ValueType.BOOLEAN:
                    wanted = option.values or (True, False)
                    gaps.extend(
                        MissingProbe(path, repr(value))
                        for value in wanted
                        if not any(same_value(seen, value) for seen in values)
                    )
                elif len({repr(value) for value in values}) < 2:
                    gaps.append(MissingProbe(path, "two different values"))
        return gaps
