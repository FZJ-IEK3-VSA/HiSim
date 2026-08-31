"""The probe list: the authored set of module configurations one setup is recorded under.

A recording observes one run, so it can only ever describe the branch that ran. The forks a
module-configured setup takes — a battery that is there or is not, a metering that is wired
through an energy manager or straight to the participants, a photovoltaic share that is a number
somebody picks — are invisible to it. The probe list is how a person says which of those
configurations are worth recording, and it is the one axis the grouping table, the grouped file
and the migration parity rig all share, so that "the configurations this setup has" is written
down once and not three times.

The file is authored, not generated, and it lives in ``energy_systems/`` beside the recorded twin,
the grouping decision and the shared simulation-parameter files: it describes configurations of
the *system*, and the decision that every new file of this format belongs there rather than next
to the Python setup was already taken for the recorded files themselves. Nothing is ever written
into ``system_setups/``.

The first entry is the baseline and carries no overlay at all. That is not a convention but the
thing that makes the rest of the pass provable: the baseline is recorded with no module
configuration whatsoever, which is exactly how the plain recorder records a module-configured
setup, so the baseline column's flat file *is* the committed twin and the grouped file can be
checked against a file that already exists. Every other entry names the fields it changes, as
dotted paths into the module configuration, and the tool materialises the full configuration
document from the class defaults the list names.
"""

# clean

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from hisim.energy_system.document import RawDocument
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemRecordingError
from hisim.energy_system.repository import RepositoryLayout


@dataclass(frozen=True)
class ProbeConfiguration:
    """One module configuration a setup is recorded under, and the column it fills in the table.

    A probe is a column name, an optional sentence saying why the configuration is worth
    recording, and the module-configuration fields that make it differ from the class defaults.
    The fields are kept as the dotted paths the author wrote rather than as a nested document,
    because a probe is defined by what it *changes* and a table cell has room for that and not for
    a whole configuration.

    The baseline is the probe that changes nothing. It is told apart by having no fields at all,
    and it is recorded with no module configuration handed to the setup, which is the only way its
    recording can be the very file the plain recorder already produces.
    """

    column: str
    description: Optional[str] = None
    module_config: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_baseline(self) -> bool:
        """Whether this probe is the one that changes nothing about the module configuration.

        Returns:
            ``True`` when the probe carries no field overlay.
        """
        return not dict(self.module_config)

    def fields(self) -> Tuple[str, ...]:
        """The overlay as one ``field=value`` string per changed field, in the written order.

        The table shows this next to the column name, so that a reader of the ``configurations``
        sheet can see what produced a column without opening the probe list beside it.

        Returns:
            One rendered assignment per field; empty for the baseline.
        """
        return tuple(f"{key}={json.dumps(value)}" for key, value in dict(self.module_config).items())

    def label(self) -> str:
        """The one-line rendering of what this probe changes, for a report or a sheet cell.

        Returns:
            The rendered fields joined by commas, or a note that the probe is the baseline.
        """
        return ", ".join(self.fields()) or "the class defaults"


@dataclass(frozen=True)
class ProbeList:
    """A whole probe list: which setup, which class defaults, and the configurations to record.

    The list is the configuration axis of the grouping pass, so it is deliberately small and fully
    authored. ``defaults`` names the classmethod that builds the module configuration a non-
    baseline probe overlays its fields onto — the setups fall back to exactly such a builder when
    they are handed no configuration, and naming it rather than guessing it keeps the tool from
    inferring which heating system a setup is for.

    The invariant the reader enforces is that the first probe is the baseline and that no other
    probe is: a second empty overlay would record the same system twice under two column names,
    and a baseline with an overlay would break the identity between the baseline column and the
    committed twin that the whole verification rests on.
    """

    #: The suffix an authored probe list carries, appended to the setup's own stem.
    SUFFIX: ClassVar[str] = ".probes.yaml"

    #: The keys the document may carry. Anything else is a typo and is refused rather than ignored.
    DOCUMENT_KEYS: ClassVar[Tuple[str, ...]] = ("setup", "defaults", "probes")

    #: The keys one probe entry may carry.
    PROBE_KEYS: ClassVar[Tuple[str, ...]] = ("column", "description", "module_config")

    setup: str
    defaults: str
    probes: Tuple[ProbeConfiguration, ...]
    origin: str = "<text>"

    @property
    def baseline(self) -> ProbeConfiguration:
        """The first probe, which is the one recorded with no module configuration at all.

        Returns:
            The baseline probe.
        """
        return self.probes[0]

    @property
    def columns(self) -> Tuple[str, ...]:
        """The column names in the order the list writes them, baseline first.

        Returns:
            One name per probe.
        """
        return tuple(probe.column for probe in self.probes)

    def probe(self, column: str) -> ProbeConfiguration:
        """Looks one probe up by its column name.

        Args:
            column: The column name to find.

        Returns:
            The probe.

        Raises:
            EnergySystemRecordingError: ``EF-R9`` when no probe carries that name, listing the
                names that exist.
        """
        for probe in self.probes:
            if probe.column == column:
                return probe
        raise EnergySystemRecordingError(
            EnergySystemErrorId.PROBE_LIST_MALFORMED,
            f"{self.origin}:probes",
            f"no probe of this list is called '{column}'.",
            alternatives=self.columns,
            alternatives_label="columns",
            offending_value=column,
        )

    def axes(self) -> Dict[str, Tuple[Any, ...]]:
        """The module-configuration fields the list varies, each with the values it takes.

        This is what bounds the claim a grouped file makes. A probe list that toggles each fork on
        its own says nothing about two forks together, and the only way to say which combinations
        were never exercised is to know the axes and their probed values — which is exactly the
        overlay read across all probes, with the baseline contributing the default of every field
        some other probe changes.

        Returns:
            One entry per varied field, in the order the probes first mention it, holding the
            distinct values that field takes across the list in first-seen order.
        """
        axes: Dict[str, List[Any]] = {}
        for probe in self.probes:
            for name, value in dict(probe.module_config).items():
                axes.setdefault(name, [])
                if value not in axes[name]:
                    axes[name].append(value)
        return {name: tuple(values) for name, values in axes.items()}

    @classmethod
    def read(cls, source: Union[str, Path]) -> "ProbeList":
        """Reads and checks one authored probe list.

        Args:
            source: The ``*.probes.yaml`` file, or the YAML text itself.

        Returns:
            The parsed list.

        Raises:
            EnergySystemRecordingError: ``EF-R9`` for any way the document is not a probe list:
                an unknown key, a missing setup or defaults, an empty or duplicated column, a
                baseline carrying an overlay or a second probe carrying none.
        """
        document, origin = RawDocument.read(source)
        if isinstance(source, Path) or origin != RawDocument.TEXT_ORIGIN:
            origin = RepositoryLayout.relative(Path(source))
        cls._reject_unknown(document, cls.DOCUMENT_KEYS, origin, "the document")
        setup = cls._required_string(document, "setup", origin)
        defaults = cls._required_string(document, "defaults", origin)
        entries = document.get("probes")
        if not isinstance(entries, list) or not entries:
            raise cls._error(origin, "probes", "a probe list needs a non-empty 'probes' sequence.")
        probes = tuple(cls._probe(entry, index, origin) for index, entry in enumerate(entries))
        cls._check_columns(probes, origin)
        return cls(setup=setup, defaults=defaults, probes=probes, origin=origin)

    @classmethod
    def _probe(cls, entry: Any, index: int, origin: str) -> ProbeConfiguration:
        """Reads one entry of the ``probes`` sequence.

        Args:
            entry: The raw mapping.
            index: Its position, used in the message when it has no usable column name.
            origin: The file the entry came from.

        Returns:
            The probe.

        Raises:
            EnergySystemRecordingError: ``EF-R9`` when the entry is not a mapping, carries an
                unknown key, has no column name or has a non-mapping overlay.
        """
        location = f"probes[{index}]"
        if not isinstance(entry, dict):
            raise cls._error(origin, location, "a probe must be a mapping of column, description and fields.")
        cls._reject_unknown(entry, cls.PROBE_KEYS, origin, location)
        column = entry.get("column")
        if not isinstance(column, str) or not column.strip():
            raise cls._error(origin, location, "a probe needs a non-empty 'column' naming its table column.")
        overlay = entry.get("module_config") or {}
        if not isinstance(overlay, dict):
            raise cls._error(origin, f"{location}.module_config", "the overlay must be a mapping of dotted fields.")
        description = entry.get("description")
        if description is not None and not isinstance(description, str):
            raise cls._error(origin, f"{location}.description", "a description must be a single line of text.")
        return ProbeConfiguration(column=column.strip(), description=description, module_config=dict(overlay))

    @classmethod
    def _check_columns(cls, probes: Sequence[ProbeConfiguration], origin: str) -> None:
        """Enforces the two rules about which probe may be empty and which names may repeat.

        Args:
            probes: The probes in written order.
            origin: The file they came from.

        Raises:
            EnergySystemRecordingError: ``EF-R9`` for a repeated column name, a baseline with an
                overlay or a later probe without one.
        """
        seen: Dict[str, int] = {}
        for index, probe in enumerate(probes):
            if probe.column in seen:
                raise cls._error(
                    origin,
                    f"probes[{index}].column",
                    f"the column '{probe.column}' is already used by probe {seen[probe.column]}.",
                )
            seen[probe.column] = index
        if not probes[0].is_baseline:
            raise cls._error(
                origin,
                "probes[0].module_config",
                "the first probe is the baseline and must change nothing, so that its recording is "
                "the committed twin the grouped file is checked against.",
            )
        for index, probe in enumerate(probes[1:], start=1):
            if probe.is_baseline:
                raise cls._error(
                    origin,
                    f"probes[{index}].module_config",
                    f"'{probe.column}' changes no field, so it would record the baseline a second time.",
                )

    @classmethod
    def _required_string(cls, document: Mapping[str, Any], key: str, origin: str) -> str:
        """Reads one required single-line string from the document.

        Args:
            document: The parsed document.
            key: The key to read.
            origin: The file it came from.

        Returns:
            The string.

        Raises:
            EnergySystemRecordingError: ``EF-R9`` when the key is missing or is not a string.
        """
        value = document.get(key)
        if not isinstance(value, str) or not value.strip():
            raise cls._error(origin, key, f"a probe list needs a non-empty '{key}'.")
        return value.strip()

    @classmethod
    def _reject_unknown(cls, block: Mapping[str, Any], allowed: Sequence[str], origin: str, where: str) -> None:
        """Refuses the first key the block carries that the format does not declare.

        Args:
            block: The mapping to check.
            allowed: The keys it may carry.
            origin: The file it came from.
            where: How the message names the block.

        Raises:
            EnergySystemRecordingError: ``EF-R9`` naming the key and the ones that are valid.
        """
        for key in block:
            if key not in allowed:
                raise EnergySystemRecordingError(
                    EnergySystemErrorId.PROBE_LIST_MALFORMED,
                    f"{origin}:{where}",
                    f"'{key}' is not a key a probe list declares.",
                    alternatives=allowed,
                    alternatives_label="keys",
                    offending_value=str(key),
                )

    @classmethod
    def _error(cls, origin: str, location: str, problem: str) -> EnergySystemRecordingError:
        """Builds one probe-list rejection; the caller raises it.

        Args:
            origin: The file the problem is in.
            location: Dotted key path of the offending element.
            problem: One sentence naming what is wrong.

        Returns:
            The exception.
        """
        return EnergySystemRecordingError(
            EnergySystemErrorId.PROBE_LIST_MALFORMED, f"{origin}:{location}", problem
        )


class ModuleConfigMaterialiser:
    """Turns one probe's dotted overlay into the complete module-configuration document to hand over.

    A setup reads its module configuration from a JSON file and expects a whole document; a probe
    states only what it changes. This class closes that gap by building the class defaults the
    probe list names, applying the overlay onto the dumped document and writing the result. The
    overlay is applied against the *dumped* form rather than against the objects, because that is
    the form the setup reads back and the only one in which a dotted path means one thing.

    A path that names no field is refused rather than added. A misspelled field would otherwise
    make a probe silently identical to the baseline, and a column that records the baseline twice
    is the one failure this pass cannot detect from its own output — every cell would read ``=``
    and the table would look like a proof.
    """

    #: Separator of a dotted overlay path.
    SEPARATOR: ClassVar[str] = "."

    #: How a materialised configuration file is named inside the work directory.
    FILE_NAME: ClassVar[str] = "{column}.module_config.json"

    @classmethod
    def defaults(cls, dotted: str, origin: str) -> Any:
        """Imports and calls the builder of the module configuration a probe overlays.

        Args:
            dotted: The fully qualified ``module.Class.classmethod`` the probe list names.
            origin: The probe list, for the message.

        Returns:
            The configuration object the builder returned.

        Raises:
            EnergySystemRecordingError: ``EF-R9`` when the name cannot be imported or does not
                answer to being called.
        """
        owner_path, _, builder_name = dotted.rpartition(cls.SEPARATOR)
        module_name, _, class_name = owner_path.rpartition(cls.SEPARATOR)
        try:
            owner = getattr(importlib.import_module(module_name), class_name)
            return getattr(owner, builder_name)()
        except (ImportError, AttributeError, TypeError) as refusal:
            raise EnergySystemRecordingError(
                EnergySystemErrorId.PROBE_LIST_MALFORMED,
                f"{origin}:defaults",
                f"'{dotted}' is not a callable that builds a module configuration: {refusal}",
                remedy="Name the classmethod the setup itself falls back to, fully qualified.",
            ) from refusal

    @classmethod
    def document(cls, probe_list: ProbeList, probe: ProbeConfiguration) -> Dict[str, Any]:
        """Builds the complete module-configuration document one probe stands for.

        Args:
            probe_list: The list the probe belongs to, for the defaults builder and the message.
            probe: The probe whose overlay is applied.

        Returns:
            The document, ready to be written as JSON.

        Raises:
            EnergySystemRecordingError: ``EF-R9`` when an overlay path names no field.
        """
        document: Dict[str, Any] = dict(cls.defaults(probe_list.defaults, probe_list.origin).to_dict())
        for path, value in dict(probe.module_config).items():
            cls._assign(document, path, value, probe, probe_list.origin)
        return document

    @classmethod
    def _assign(
        cls, document: Dict[str, Any], path: str, value: Any, probe: ProbeConfiguration, origin: str
    ) -> None:
        """Sets one dotted path of the document, refusing a path that names no field.

        Args:
            document: The dumped configuration, modified in place.
            path: The dotted field path.
            value: The value to set.
            probe: The probe the overlay belongs to, for the message.
            origin: The probe list, for the message.

        Raises:
            EnergySystemRecordingError: ``EF-R9`` naming the path, the probe and the fields the
                block it points into really has.
        """
        keys = path.split(cls.SEPARATOR)
        block: Any = document
        for key in keys[:-1]:
            if not isinstance(block, dict) or key not in block or not isinstance(block[key], dict):
                raise cls._unknown_field(block, path, key, probe, origin)
            block = block[key]
        leaf = keys[-1]
        if not isinstance(block, dict) or leaf not in block:
            raise cls._unknown_field(block, path, leaf, probe, origin)
        block[leaf] = value

    @classmethod
    def _unknown_field(
        cls, block: Any, path: str, key: str, probe: ProbeConfiguration, origin: str
    ) -> EnergySystemRecordingError:
        """Builds the rejection of an overlay path that names no field; the caller raises it.

        Args:
            block: The mapping the path had reached, whose keys are the alternatives.
            path: The whole dotted path.
            key: The segment that does not exist.
            probe: The probe the overlay belongs to.
            origin: The probe list.

        Returns:
            The exception.
        """
        return EnergySystemRecordingError(
            EnergySystemErrorId.PROBE_LIST_MALFORMED,
            f"{origin}:probe '{probe.column}'.module_config.{path}",
            f"the module configuration has no field '{key}' at this point.",
            alternatives=sorted(block) if isinstance(block, dict) else (),
            alternatives_label="fields",
            offending_value=key,
        )

    @classmethod
    def write(cls, probe_list: ProbeList, probe: ProbeConfiguration, directory: Path) -> Optional[Path]:
        """Writes the module-configuration file one probe needs, or nothing for the baseline.

        The baseline is deliberately handed no file at all: a setup with no module configuration
        falls back to its own class defaults, and that fallback is what the committed twin records.
        Writing a file that happens to hold the same values would go through a different code path
        in the setup and would put the tool in the position of asserting the two agree.

        Args:
            probe_list: The list the probe belongs to.
            probe: The probe to materialise.
            directory: Where the file goes; created when it does not exist.

        Returns:
            The written path, or ``None`` for the baseline.

        Raises:
            EnergySystemRecordingError: ``EF-R9`` when the overlay cannot be applied.
        """
        if probe.is_baseline:
            return None
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / cls.FILE_NAME.format(column=probe.column)
        path.write_text(json.dumps(cls.document(probe_list, probe), indent=1, sort_keys=False), encoding="utf-8")
        return path
