"""Line numbers for the values of a YAML document, so a message can point at the line that caused it.

The model of an energy-system or an assembly file holds values, not positions: a frozen pydantic
model is the right shape for checking and expanding a file and the wrong one for remembering where
each key was written. The expansion of imports (``assemblies_spec.md`` §2.3, §9.2) needs both,
because everything it produces carries a **source map entry** naming the file and line it came
from, and a nested assembly's failure is only findable from its message when the message names
those lines.

:class:`LineIndex` is the side table: the document is composed into PyYAML's node tree, which
carries a start mark on every node, and every key path is mapped to the 1-based line its key (or,
in a sequence, its item) starts on. The values themselves are still read by
:class:`hisim.energy_system.document.RawDocument`; this module only remembers where they were.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Union

import yaml

from hisim.energy_system.document import RawDocument, StrictYamlLoader

#: One step of a key path: a mapping key, or the index of a sequence item.
PathStep = Union[str, int]


@dataclass(frozen=True)
class SourceLocation:
    """A file and a 1-based line in it.

    Attributes:
        file: How the file is named in messages: an assembly by its library path
            (``pv/array.assembly.yaml``), an energy-system file by its name.
        line: The line, 1-based; ``0`` when the position is not known.
    """

    file: str
    line: int

    @property
    def text(self) -> str:
        """``file:line``, or the bare file name when the line is not known."""
        return f"{self.file}:{self.line}" if self.line else self.file


class LineIndex:
    """The line every key path of one YAML document starts on.

    Built once per file from its text; :meth:`location` answers for a key path, falling back to
    the closest enclosing key that was written when the exact path was not (a defaulted value, an
    item the expansion produced), so a location is never invented and never missing.
    """

    def __init__(self, origin: str, lines: Dict[Tuple[PathStep, ...], int]) -> None:
        """Keeps the table of one document.

        Args:
            origin: The file's name as messages print it.
            lines: Key path to 1-based line.
        """
        self.origin = origin
        self.lines = lines

    @classmethod
    def from_text(cls, text: str, origin: str) -> "LineIndex":
        """Indexes a YAML document.

        Args:
            text: The document.
            origin: The file's name as messages print it.

        Returns:
            The index.

        Raises:
            EnergySystemFormatError: ``EF-03`` for text that is not YAML, as the reader refuses it:
                an index without lines would silently drop every source-map line.
        """
        try:
            node = yaml.compose(text, Loader=StrictYamlLoader)
        except yaml.YAMLError as problem:
            raise RawDocument.not_yaml(origin, problem) from problem
        lines: Dict[Tuple[PathStep, ...], int] = {}
        cls._walk(node, (), lines)
        return cls(origin, lines)

    @classmethod
    def empty(cls, origin: str) -> "LineIndex":
        """An index that knows no line, for a document that exists only in memory."""
        return cls(origin, {})

    @classmethod
    def _walk(
        cls, node: Optional[yaml.Node], path: Tuple[PathStep, ...], lines: Dict[Tuple[PathStep, ...], int]
    ) -> None:
        """Records the start line of every key and sequence item below ``node``."""
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                child = path + (str(key_node.value),)
                lines[child] = key_node.start_mark.line + 1
                cls._walk(value_node, child, lines)
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                child = path + (index,)
                lines[child] = item.start_mark.line + 1
                cls._walk(item, child, lines)

    def line(self, *path: PathStep) -> int:
        """The line of a key path, or of its closest written ancestor; ``0`` if none was written."""
        steps: Tuple[PathStep, ...] = tuple(path)
        while steps:
            found = self.lines.get(steps)
            if found is not None:
                return found
            steps = steps[:-1]
        return 0

    def location(self, *path: PathStep) -> SourceLocation:
        """The location of a key path in this file."""
        return SourceLocation(self.origin, self.line(*path))
