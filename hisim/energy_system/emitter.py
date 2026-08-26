"""The canonical writer that turns a loaded energy system back into YAML text.

The format has exactly one written style, and this module is the only place that knows
it: keys in the order the format declares them, block sequences with one item per line,
no alphabetical sorting, and optional blocks omitted when they are empty. Fixing the
style is what lets a hand-written file and a machine-written one be compared line by
line, and what makes a program that loads, edits and rewrites a file leave everything it
did not touch alone.

The emitter works in two steps, and both are useful on their own. :meth:`
EnergySystemEmitter.to_document` rebuilds the plain nested dict that mirrors the file,
which is what a writer that wants to annotate values with where they came from starts
from; :meth:`EnergySystemEmitter.dump` renders that dict to text. Keeping the two apart
means the run record and the plain round-trip share one definition of the canonical shape
and cannot drift.

Comments are not carried through. This writer emits values only; rendering provenance as
end-of-line comments is the job of the record writer, which builds on the document this
one produces.
"""

# clean

from __future__ import annotations

from typing import Any, ClassVar, Dict

import yaml

from hisim.energy_system.model import (
    AggregatorFeed,
    ComponentEntry,
    DefaultInputs,
    EnergySystemFile,
    ExplicitWire,
    Group,
    InputItem,
)


class CanonicalDumper(yaml.SafeDumper):
    """The safe YAML dumper with block sequences indented under the key they belong to.

    PyYAML writes a block sequence flush with its parent key, which is legal YAML but reads
    badly in a file where nearly every component carries a list of inputs. Indenting the
    dashes one level, as people do when they write these files by hand, is the only change
    this dumper makes to the safe dumper.

    Getting the two styles to agree matters beyond taste: a generated file and a
    hand-maintained one are routinely diffed against each other, and a systematic
    indentation difference would make every such diff useless.
    """

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        """Indents block sequences instead of writing them flush with their parent key.

        PyYAML asks for an indentless level whenever it opens a block sequence inside a
        mapping. Answering that request with a normal indent is what moves the dashes one
        level in, and it is safe because the emitter never relies on the indentless form.

        Args:
            flow: Whether the collection being opened is written in flow style; passed
                through unchanged.
            indentless: PyYAML's request to skip the indent, which is always refused.
        """
        super().increase_indent(flow, False)


class EnergySystemEmitter:
    """Writes a loaded energy system back out in the format's one canonical style.

    The style is fixed so that hand-written and generated files diff cleanly against each
    other: keys appear in the order the format declares them, lists are written as block
    sequences with one item per line, and nothing is sorted alphabetically. Optional blocks
    that are empty are dropped rather than written as ``{}``, which keeps a file that says
    little short.

    Comments are not preserved; this is the plain-value writer used for round-tripping and
    for machine-authored files. A generated run record, which annotates each value with
    where it came from, is written by a separate emitter that renders those annotations as
    comments and reuses the document produced here.
    """

    #: Column at which the emitter would fold a long scalar onto a second line, set high
    #: enough that it never does. Folding is the one place where two YAML libraries writing
    #: the same document legitimately disagree — they choose different break points — and the
    #: annotated writer of a run record runs on a different library by necessity, so a folded
    #: scalar would make the two styles drift apart for reasons no reader would understand.
    #: Never folding also keeps a long description or a long path greppable.
    LINE_WIDTH: ClassVar[int] = 1_000_000

    #: The tag PyYAML's implicit resolver gives a plain scalar that really is a string. Any
    #: other answer means the unquoted spelling would be read back as a number, a boolean or
    #: a date, which is what makes the quotes necessary.
    STRING_TAG: ClassVar[str] = "tag:yaml.org,2002:str"

    #: The other reason a string cannot be written plain: it spans lines, which the canonical
    #: style writes as a quoted scalar folded over the following lines.
    LINE_BREAK: ClassVar[str] = "\n"

    @classmethod
    def must_quote(cls, text: str) -> bool:
        """Whether the canonical style writes this string in quotes rather than plain.

        Two things stop a string from being written plain. Its unquoted spelling would be read
        back as something else — ``yes`` and ``on`` under YAML 1.1's boolean rules, ``12`` under
        its number rules — which is answered by PyYAML's own implicit resolver rather than by a
        list of special words, so the rule cannot fall behind the resolver that reads these
        files. Or it spans lines, which the canonical style writes as a quoted scalar folded
        over the following lines.

        The annotated writer of a run record uses a different YAML library, which answers both
        questions differently: its resolver follows a later version of the specification, and it
        prefers double quotes for a string that spans lines. Asking this method instead of that
        library's own judgement is what keeps the two writers producing the same bytes.

        Args:
            text: The string about to be written.

        Returns:
            ``True`` when the string needs quoting to survive a round trip.
        """
        if cls.LINE_BREAK in text:
            return True
        resolver = yaml.resolver.Resolver()
        return bool(resolver.resolve(yaml.nodes.ScalarNode, text, (True, False)) != cls.STRING_TAG)

    @classmethod
    def dump(cls, model: EnergySystemFile) -> str:
        """Renders a whole energy system as canonical YAML text.

        Args:
            model: The file to write.

        Returns:
            The YAML document, ending in a newline.
        """
        rendered = yaml.dump(
            cls.to_document(model),
            Dumper=CanonicalDumper,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
            width=cls.LINE_WIDTH,
        )
        return str(rendered)

    @classmethod
    def to_document(cls, model: EnergySystemFile) -> Dict[str, Any]:
        """Turns the model back into the plain nested dict a YAML dumper accepts.

        Args:
            model: The file to convert.

        Returns:
            The document with its keys in canonical order and every optional empty block
            omitted.
        """
        document: Dict[str, Any] = {"schema_version": model.schema_version, "name": model.name}
        if model.description is not None:
            document["description"] = model.description
        document["components"] = {name: cls._entry(entry) for name, entry in model.components.items()}
        if model.groups:
            document["groups"] = {name: cls._group(group) for name, group in model.groups.items()}
        if model.metadata is not None:
            document["metadata"] = dict(model.metadata)
        return document

    @classmethod
    def _group(cls, group: Group) -> Dict[str, Any]:
        """Renders one group: its flag first, then the components it switches.

        Returns:
            The group's mapping in canonical key order.
        """
        return {
            "enabled": group.enabled,
            "components": {name: cls._entry(entry) for name, entry in group.components.items()},
        }

    @classmethod
    def _entry(cls, entry: ComponentEntry) -> Dict[str, Any]:
        """Renders one component entry with its keys in the canonical order.

        The order — what it is, how it is configured, where its inputs come from, where its
        sizing facts come from — is the order in which the entry answers the questions a
        reader asks about a component, which is why the format fixes it.

        Returns:
            The entry's mapping, without the blocks it does not use.
        """
        document: Dict[str, Any] = {ComponentEntry.CLASS_KEY: entry.class_path}
        if entry.preset is not None:
            document["preset"] = entry.preset
        if entry.constructor is not None:
            document["constructor"] = {entry.constructor.name: dict(entry.constructor.arguments)}
        if entry.config:
            document["config"] = dict(entry.config)
        if entry.inputs:
            document["inputs"] = [cls._input_item(item) for item in entry.inputs]
        if entry.sizing_sources:
            document["sizing_sources"] = {
                fact: ([reference.text for reference in value] if isinstance(value, tuple) else value.text)
                for fact, value in entry.sizing_sources.items()
            }
        return document

    @classmethod
    def _input_item(cls, item: InputItem) -> Any:
        """Renders one input item in the shape it was classified as.

        A defaults item becomes the bare source name again, and the two mapping shapes are
        written with their keys in the order the format declares, so that a reader meets
        ``from`` before the tags that qualify it.

        Returns:
            A bare string for a defaults item, a mapping for the other two shapes.
        """
        if isinstance(item, DefaultInputs):
            return item.source
        if isinstance(item, ExplicitWire):
            return {"input": item.input, "from": f"{item.source}.{item.output}"}
        assert isinstance(item, AggregatorFeed)
        feed: Dict[str, Any] = {"from": f"{item.source}.{item.output}" if item.output else item.source}
        if item.component_type is not None:
            feed["component_type"] = item.component_type
        feed["tags"] = list(item.tags)
        feed["weight"] = item.weight
        if item.dispatch is not None:
            dispatch: Dict[str, Any] = {}
            if item.dispatch.target_input is not None:
                dispatch["target_input"] = item.dispatch.target_input
            if item.dispatch.tags:
                dispatch["tags"] = list(item.dispatch.tags)
            feed["dispatch"] = dispatch
        return feed
