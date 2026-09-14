"""Assembling one observed system into the energy-system file that describes it.

This is the middle of the three recording stages and the only one that is a pure function: it takes
plain data in, returns a model out, touches no runtime object and writes no text. Everything
interesting therefore has a test that needs neither a simulator nor a filesystem, which is the
reason the stage exists separately at all.

What it produces is deliberately narrow. It carries no ``sizing_sources``, no ``groups`` and no
``variants``: the first is a claim about provenance that no observation can make, and the last two
are judgements about which parts of a household belong together, which is a person's decision and
not an inference from a single run. What is left is a flat list of components in registration
order, each stating its class, its configuration and where its inputs come from.

It does carry ``AUTO``, on exactly the fields a law computed and for which this system declares a
provider (A-P3.1). A twin is an authored energy-system file rather than a transcript of one run:
a field the file can compute again is written as the sentinel, with the run's value, the law and
the provider in a trailing comment, so that the same file re-sizes for a different building
instead of repeating one archetype's numbers. Which fields those are is decided in
:mod:`~hisim.energy_system.recording.configs`; this module builds the provider lookup that
decision needs, renders the decisions as the file's comments and refuses any other sentinel.

The two guards that live here are about portability rather than shape. An absolute filesystem path
that survived symbolisation is refused rather than written, because it would make the file
reproduce on one machine and fail on the next, and the freshness check that re-records every setup
would then differ for a reason nobody could see in the diff.
"""

# clean

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

from hisim.energy_system.errors import (
    EnergySystemErrorId,
    EnergySystemFormatError,
    EnergySystemRecordingError,
)
from hisim.energy_system.model import ComponentEntry, EnergySystemFile
from hisim.energy_system.path_resolver import PathResolver
from hisim.energy_system.record import ConfigBlockWriter, assert_no_sentinels
from hisim.energy_system.recording.configs import (
    EntryConfigWriter,
    FactProviders,
    SizedFieldDecision,
)
from hisim.energy_system.recording.inputs import InputItemWriter
from hisim.energy_system.recording.names import RecordedNames
from hisim.energy_system.recording.observe import ObservedComponent, RecordedSystem
from hisim.energy_system.validation import StructuralValidator


class PortablePathGuard:
    """Refuses a configuration block that still names a location this machine alone understands.

    Symbolisation turns every path below a registered root into its ``${var}`` spelling, so a value
    that is still absolute afterwards lies below no root at all — a cluster cache directory, a
    scratch folder, a checkout somewhere else. Writing it would produce a file that runs here and
    nowhere else, and, worse, one whose re-recording on another machine differs in a line that says
    nothing about the system.

    The scan is not a rule of its own: the guard runs the structural validator's own walk over the
    block it is about to write, so the recorder cannot come to disagree with the loader about what
    counts as an absolute path or about which keys name a location. Running it here rather than
    leaving it to load time is what lets the refusal name the setup and the component, which is
    what a person re-recording a fleet needs, instead of only the key path inside a file.
    """

    @classmethod
    def check(cls, block: Mapping[str, Any], name: str, setup: str, location: str = "config") -> None:
        """Walks one configuration block and refuses the first absolute path it names.

        Args:
            block: The encoded configuration block about to be written.
            name: The component's runtime name, for the message.
            setup: The setup module being recorded, for the message.
            location: Dotted key path of the block inside the entry, which the validator's own
                walk grows as it descends.

        Raises:
            EnergySystemRecordingError: ``EF-R3`` naming the setup, the component, the field and
                the path.
        """
        try:
            StructuralValidator.scan_for_absolute_paths(block, location)
        except EnergySystemFormatError as refusal:
            raise cls._error(name, setup, refusal) from refusal

    @classmethod
    def _error(
        cls, name: str, setup: str, refusal: EnergySystemFormatError
    ) -> EnergySystemRecordingError:
        """Translates the validator's load-time rejection into the recorder's own; caller raises.

        The validator's message names the key path and the value and nothing else, because at load
        time there is nothing else to name. Both are carried over verbatim so that the two
        refusals point at the same field, and the setup and the component are added in front.

        Args:
            name: The component's runtime name.
            setup: The setup module being recorded.
            refusal: The validator's ``EF-05``, whose location and problem are reused.

        Returns:
            The exception to raise.
        """
        return EnergySystemRecordingError(
            EnergySystemErrorId.RECORDED_ABSOLUTE_PATH,
            f"{setup}:{name}.{refusal.location}",
            f"{refusal.problem} It lies below no registered root, so it cannot be written portably.",
            remedy=(
                "Register the directory with the path resolver, or make the setup point the field "
                "at a location inside the HiSim tree."
            ),
        )


class EnergySystemBuilder:
    """Turns one observation into the model of the file that describes it.

    One builder serves one recording. It owns the two writers the entries are made of — the
    configuration writer and the input-item writer — so that every entry of one file is encoded
    against the same path resolver and reads the same wiring, which is what makes the result a
    function of the observation alone.

    It also owns the two by-products of the sizing decision, and they are by-products of *this*
    object rather than of the model because the model has nowhere to put them: :attr:`notes`, the
    comment each configuration line carries, which the emitter attaches, and :attr:`checks`, the
    ``AUTO`` fields whose claim the session verifies by resolving the file it just wrote.

    Nothing is sorted and nothing is looked up in a set on the way out: components are written in
    registration order, an entry's keys in the order the format declares them, and a configuration's
    fields in declaration order. That is not tidiness but requirement: a freshness check re-records
    every setup and fails on any diff, so a mapping whose order depended on a hash would fail it on
    a different machine for no reason a reader could see.
    """

    #: The one schema version a recorded file is written against.
    SCHEMA_VERSION: ClassVar[int] = EnergySystemFile.SUPPORTED_SCHEMA_VERSION

    def __init__(self, recorded: RecordedSystem, *, path_resolver: Optional[PathResolver] = None) -> None:
        """Prepares the builder for one observation.

        Args:
            recorded: The observation to write down.
            path_resolver: The registry turning absolute locations into ``${var}`` references; this
                machine's default registry when omitted.
        """
        self.recorded = recorded
        self.configs = EntryConfigWriter(
            ConfigBlockWriter(path_resolver or PathResolver.default()),
            FactProviders.of(recorded.components),
        )
        self.inputs = InputItemWriter(recorded)
        self.decisions: List[SizedFieldDecision] = []

    def build(self, name: str, description: Optional[str] = None) -> EnergySystemFile:
        """Builds the whole file.

        Args:
            name: The system's name, which is the setup's own stem rather than anything invented.
            description: One line saying what the system is, or ``None``.

        Returns:
            The model of the recorded file, ready to be emitted.

        Raises:
            EnergySystemRecordingError: ``EF-R1`` for an unwritable name, ``EF-R2`` for a qualified
                identity, ``EF-R3`` for an unportable path and ``EF-R4`` for a vanished preset.
            EnergySystemRecordError: ``EF-60`` if a value asks to be sized that the recorder did
                not deliberately write as ``AUTO``, which a component that was constructed at all
                cannot produce and which is therefore a broken promise rather than a bad setup.
        """
        self.decisions = []
        components: Dict[str, ComponentEntry] = {}
        for observed in self.recorded.components:
            key = RecordedNames.check_component_name(observed.name, self.recorded.setup)
            components[key] = self.entry(observed)
        recorded = EnergySystemFile(
            schema_version=self.SCHEMA_VERSION,
            name=name,
            description=description,
            components=components,
        )
        self.assert_every_sentinel_was_decided(recorded)
        return recorded

    def assert_every_sentinel_was_decided(self, recorded: EnergySystemFile) -> None:
        """Refuses an ``AUTO`` the sizing decision did not put into the file.

        The recorder writes the sentinel on purpose now, so the blanket refusal that used to guard
        the file would refuse its own output. What still has to hold is the narrower rule it stood
        for: every ``AUTO`` in a twin is one the recorder decided on and can name a law and a
        provider for, and any other one is a value that escaped a configuration unresolved. The
        check is the same walk over the same model with the decided fields taken out first, so the
        two cannot come to disagree about what a sentinel looks like.

        Args:
            recorded: The finished model, before it is emitted.

        Raises:
            EnergySystemRecordError: ``EF-60`` naming the entry and the field.
        """
        decided = {(decision.component, decision.field) for decision in self.decisions if decision.auto}
        assert_no_sentinels(
            recorded.model_copy(
                update={
                    "components": {
                        name: entry.model_copy(
                            update={
                                "config": {
                                    key: value
                                    for key, value in entry.config.items()
                                    if (name, key) not in decided
                                }
                            }
                        )
                        for name, entry in recorded.components.items()
                    }
                }
            )
        )

    @property
    def notes(self) -> Dict[str, Dict[str, str]]:
        """The trailing comment every decided configuration line carries, by component and field.

        Returns:
            One mapping per component that has at least one decided field; empty for a recording
            in which no law computed anything.
        """
        rendered: Dict[str, Dict[str, str]] = {}
        for decision in self.decisions:
            rendered.setdefault(decision.component, {})[decision.field] = decision.comment()
        return rendered

    @property
    def checks(self) -> Tuple[SizedFieldDecision, ...]:
        """The ``AUTO`` fields whose claim the written file has to make good on.

        Returns:
            One decision per field written as the sentinel, in the order the entries were built.
        """
        return tuple(decision for decision in self.decisions if decision.auto)

    def entry(self, observed: ObservedComponent) -> ComponentEntry:
        """Builds the entry of one component: what it is, how it is configured, what feeds it.

        Args:
            observed: The observed component.

        Returns:
            Its entry, carrying no sizing sources at all — which provider answers a fact is a
            decision the binding rule makes for itself, and a file that wrote it down would stop
            following the system it is put into.

        Raises:
            EnergySystemRecordingError: ``EF-R2``, ``EF-R3`` or ``EF-R4`` for this component.
        """
        RecordedNames.check_identity(observed.name, observed.config, self.recorded.setup)
        written = self.configs.fields(observed.name, observed.config, self.recorded.setup)
        self.decisions.extend(written.decisions)
        block = written.members.get(EntryConfigWriter.CONFIG_KEY) or {}
        PortablePathGuard.check(block, observed.name, self.recorded.setup)
        return ComponentEntry(
            name=observed.name,
            class_path=observed.class_path,
            preset=written.members.get(EntryConfigWriter.PRESET_KEY),
            config=block,
            inputs=self.inputs.items(observed),
        )


def build(recorded: RecordedSystem, name: str, description: Optional[str] = None) -> EnergySystemFile:
    """Builds the energy-system file one observation describes.

    The middle stage of the recording pipeline, kept as a function because that is how the pipeline
    reads: observe, build, emit. It is pure — the same observation always produces the same model —
    which is the property the freshness check depends on.

    Args:
        recorded: The observation, components in registration order.
        name: The system's name.
        description: One line saying what the system is, or ``None``.

    Returns:
        The model of the recorded file.

    Raises:
        EnergySystemRecordingError: For any of the ``EF-Rx`` conditions the observation trips.
    """
    return EnergySystemBuilder(recorded).build(name, description)
