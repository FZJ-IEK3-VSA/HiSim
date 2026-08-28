"""How one observed configuration becomes an entry's ``preset`` and ``config`` keys.

Two shapes exist and the choice between them is not a judgement. A configuration built through a
``@preset`` classmethod carries the preset's wire name as a stamp, so the entry names that preset
and states only what the setup changed afterwards; a configuration with no stamp is written as a
complete block, because nothing about it is recoverable and guessing a preset by matching values
against every preset of the class would be an inference the format's first principle forbids.

That is the half of the earlier template creator that survives. Its other half de-resolved a sized
value back to ``AUTO``, which a recorded file may not contain: a recording states what a run built,
and a sentinel would make the file size itself again on the next run instead of reproducing this
one. The rules that went with it — omit a field the preset's own law re-sizes, keep a field the
author pinned over a sizable one — went with it too, for the same reason. What is left is a plain
field-wise diff of two encoded blocks.

Both branches encode through the record writer of :mod:`hisim.energy_system.record`, so the
portable ``${var}`` spelling of a path, the enum-by-name rule and the omission of the identity are
inherited rather than reimplemented, and a recorded block is the same text a realized record would
have written for the same configuration.
"""

# clean

from __future__ import annotations

from typing import Any, ClassVar, Dict

from hisim.config import ConfigBuilder, preset_provenance, presets_of
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemRecordingError
from hisim.energy_system.record import ConfigBlockWriter


class EntryConfigWriter:
    """Writes the configuration half of one component entry: a preset reference or a full block.

    Constructed once per recording over the block writer that owns the path resolver, and asked per
    component. Keeping it an object rather than a set of functions is what lets every entry of one
    recording share one resolver, which matters because two entries symbolising the same directory
    against two different registries would produce a file that is portable in one half and not in
    the other.

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

    def __init__(self, writer: ConfigBlockWriter) -> None:
        """Prepares the writer for one recording.

        Args:
            writer: The record's block writer, carrying the path resolver every block is
                symbolised against.
        """
        self.writer = writer

    def fields(self, name: str, config: Any, setup: str) -> Dict[str, Any]:
        """Builds the entry members one configuration writes: ``preset`` and/or ``config``.

        Args:
            name: The component's runtime name, which is also the entry's key.
            config: The live configuration object as the observation holds it.
            setup: The setup module being recorded, for the message.

        Returns:
            A mapping to splice into the entry, holding ``config`` alone for an unstamped
            configuration, ``preset`` alone when a preset reproduces it exactly, and both when the
            setup changed something after building it.

        Raises:
            EnergySystemRecordingError: ``EF-R4`` when the stamp names a preset the class no longer
                declares.
        """
        stamp = preset_provenance(config)
        if stamp is None:
            return {self.CONFIG_KEY: self.writer.block(name, config)}
        overrides = self.overrides(name, config, stamp, setup)
        if not overrides:
            return {self.PRESET_KEY: stamp}
        return {self.PRESET_KEY: stamp, self.CONFIG_KEY: overrides}

    def overrides(self, name: str, config: Any, stamp: str, setup: str) -> Dict[str, Any]:
        """Diffs one stamped configuration against a fresh build of its preset, field by field.

        The baseline is a fresh instance of the same preset under the same instance name, encoded
        by the same writer, so a field that survived the preset untouched produces no line and a
        field the setup assigned afterwards produces exactly one. Comparing the *encoded* forms
        rather than the objects is what makes an enum that dumps to the same member name and a
        path that symbolises to the same reference count as unchanged, which is the property that
        keeps a recorded diff readable.

        Args:
            name: The component's runtime name; the preset builds its identity from it, so the
                baseline has to be built under the same name or every entry would deviate in its
                identity field alone.
            config: The live configuration object.
            stamp: The preset's wire name, as the provenance records it.
            setup: The setup module being recorded, for the message.

        Returns:
            The sparse block, in the configuration's own field order; empty when the preset
            reproduces the configuration exactly.

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
        baseline = self.writer.block(name, builder.build(name))
        current = self.writer.block(name, config)
        return {key: value for key, value in current.items() if key not in baseline or baseline[key] != value}
