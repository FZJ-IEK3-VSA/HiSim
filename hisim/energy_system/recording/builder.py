"""Assembling one observed system into the energy-system file that describes it.

This is the middle of the three recording stages and the only one that is a pure function: it takes
plain data in, returns a model out, touches no runtime object and writes no text. Everything
interesting therefore has a test that needs neither a simulator nor a filesystem, which is the
reason the stage exists separately at all.

What it produces is deliberately narrow. A recording states what one run built, so it carries no
``AUTO``, no ``sizing_sources``, no ``groups`` and no ``variants``: the first would make the file
size itself again instead of reproducing the run, the second is a claim about provenance that no
observation can make, and the last two are judgements about which parts of a household belong
together, which is a person's decision and not an inference from a single run. What is left is a
flat list of components in registration order, each stating its class, its configuration and where
its inputs come from.

The two guards that live here are about portability rather than shape. An absolute filesystem path
that survived symbolisation is refused rather than written, because it would make the file
reproduce on one machine and fail on the next, and the freshness check that re-records every setup
would then differ for a reason nobody could see in the diff.
"""

# clean

from __future__ import annotations

from typing import Any, ClassVar, Dict, Mapping, Optional

from hisim.energy_system.errors import (
    EnergySystemErrorId,
    EnergySystemFormatError,
    EnergySystemRecordingError,
)
from hisim.energy_system.model import ComponentEntry, EnergySystemFile
from hisim.energy_system.path_resolver import PathResolver
from hisim.energy_system.record import ConfigBlockWriter, assert_no_sentinels
from hisim.energy_system.recording.configs import EntryConfigWriter
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
        self.configs = EntryConfigWriter(ConfigBlockWriter(path_resolver or PathResolver.default()))
        self.inputs = InputItemWriter(recorded)

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
            EnergySystemRecordError: ``EF-60`` if a value still asks to be sized, which a component
                that was constructed at all cannot produce and which is therefore a broken promise
                rather than a bad setup.
        """
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
        assert_no_sentinels(recorded)
        return recorded

    def entry(self, observed: ObservedComponent) -> ComponentEntry:
        """Builds the entry of one component: what it is, how it is configured, what feeds it.

        Args:
            observed: The observed component.

        Returns:
            Its entry, carrying no sizing sources at all — a recording states values, and where a
            value came from is not something one run can be asked about.

        Raises:
            EnergySystemRecordingError: ``EF-R2``, ``EF-R3`` or ``EF-R4`` for this component.
        """
        RecordedNames.check_identity(observed.name, observed.config, self.recorded.setup)
        fields = self.configs.fields(observed.name, observed.config, self.recorded.setup)
        block = fields.get(EntryConfigWriter.CONFIG_KEY) or {}
        PortablePathGuard.check(block, observed.name, self.recorded.setup)
        return ComponentEntry(
            name=observed.name,
            class_path=observed.class_path,
            preset=fields.get(EntryConfigWriter.PRESET_KEY),
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
