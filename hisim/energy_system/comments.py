"""Writing a run record with its provenance rendered as end-of-line comments.

A realized record is a file of numbers. Every one of them was decided by something — a preset, a
law reading a building's heating load, an author's override — and the machine-readable account of
those decisions lives in the audit companion. But the person most likely to open a record is
someone checking a run, and asking them to hold two files side by side to answer "where does this
8558.8 come from" is asking for the question not to be asked. So the record answers it in place,
at the end of the line the number sits on.

The comments are a *rendering* of the audit, never a second source of truth. Nothing parses them
back, no fact exists only in a comment, and stripping every one of them changes nothing about
what the record does. That is why they are safe: a stale comment cannot mislead a machine, and a
regenerated record regenerates its comments wholesale rather than patching them. They appear only
in generated files — a hand-written energy system carries the author's own comments, and machine
comments on a file a person maintains would rot.

The one thing this module must never do is become a second YAML writer. It runs on a different
library from the canonical emitter, because the canonical one cannot attach comments, and two
libraries writing the same document is exactly how a format grows two styles. The defence is
mechanical: with no annotations to attach, this writer's output is required to equal the canonical
writer's byte for byte, and a test holds it to that. Everything below — the folding width, the
quoting rule, the spelling of a null — exists to keep that equality true.
"""

# clean

from __future__ import annotations

import io
from typing import Any, ClassVar, List, Optional, Tuple, Type

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.representer import RoundTripRepresenter

from hisim.config.introspection import SizableFieldKind
from hisim.energy_system.audit_records import AuditRecord, BuiltFrom, ComponentAudit
from hisim.energy_system.emitter import EnergySystemEmitter
from hisim.energy_system.loader import EnergySystemReader, dump_energy_system
from hisim.energy_system.model import ComponentEntry, EnergySystemFile
from hisim.energy_system.metadata import RunMetadata


class CanonicalRepresenter(RoundTripRepresenter):
    """The round-trip representer taught the two habits the canonical style has.

    Two spellings differ between the library that writes plain records and the library that
    writes annotated ones, and both are visible in every file. A null is written ``null`` rather
    than as an empty value, because an empty value reads as an omission. And a string whose
    unquoted spelling would come back as something else — ``yes`` under the boolean rules of the
    YAML version this format's reader uses — is quoted, which the round-trip representer does not
    do on its own because it follows a later version of the specification.

    Neither habit is invented here: the quoting question is put to the canonical writer's own
    resolver, so the two answers cannot drift apart as that resolver changes.
    """

    #: Tag under which a null is written.
    NULL_TAG: ClassVar[str] = "tag:yaml.org,2002:null"

    #: Spelling of a null, matching what the canonical writer emits.
    NULL_TEXT: ClassVar[str] = "null"

    #: Tag under which a string is written.
    STRING_TAG: ClassVar[str] = "tag:yaml.org,2002:str"

    #: Quote character used for a string that cannot be written plain.
    QUOTE_STYLE: ClassVar[str] = "'"

    @classmethod
    def configured(cls) -> Type["CanonicalRepresenter"]:
        """Registers the two overrides on this class and returns it.

        Registration happens here rather than at import time so that importing this module has
        no side effect, and it is idempotent: registering the same function for the same type
        twice is the same as registering it once.

        Returns:
            This class, ready to be handed to a YAML instance.
        """
        cls.add_representer(type(None), cls.represent_canonical_null)
        cls.add_representer(str, cls.represent_canonical_string)
        return cls

    @staticmethod
    def represent_canonical_null(representer: Any, data: Any) -> Any:
        """Writes a null as the word the canonical writer uses.

        Args:
            representer: The representer doing the work; passed by the dispatch table.
            data: The value, always ``None``.

        Returns:
            The scalar node for the null.
        """
        del data
        return representer.represent_scalar(
            CanonicalRepresenter.NULL_TAG, CanonicalRepresenter.NULL_TEXT
        )

    @staticmethod
    def represent_canonical_string(representer: Any, data: str) -> Any:
        """Writes a string plain, or quoted when the plain spelling would be read as something else.

        Args:
            representer: The representer doing the work; passed by the dispatch table.
            data: The string to write.

        Returns:
            The scalar node for the string.
        """
        style = (
            CanonicalRepresenter.QUOTE_STYLE if EnergySystemEmitter.must_quote(data) else None
        )
        return representer.represent_scalar(CanonicalRepresenter.STRING_TAG, data, style=style)


class ProvenanceComments:
    """The wording of every comment a record carries, in one place.

    Collected on one class because the comments are a small vocabulary that a reader learns once
    and then reads at a glance: a value either came from the system, from the author, or from the
    class's own default, and the sizing lines either the author wrote or the record added. Keeping
    the phrasings together is what stops that vocabulary from growing a synonym per call site.

    Every phrase is deliberately short. A comment shares its line with the value it explains, and
    a long one pushes the value out of sight in a narrow window.
    """

    #: The line at the top of every generated record. It carries the version but no timestamp:
    #: a timestamp would make every regeneration of an unchanged record a diff, which is the
    #: fastest way to teach readers to ignore the file.
    HEADER_TEMPLATE: ClassVar[str] = (
        "Generated by HiSim {version} — do not hand-edit; re-run to regenerate"
    )

    #: Comment on the ``config`` key of an entry built from a named default of its class.
    PRESET_TEMPLATE: ClassVar[str] = "built from preset: {name}"

    #: Comment on the ``config`` key of an entry built by a named constructor.
    CONSTRUCTOR_TEMPLATE: ClassVar[str] = "built from constructor: {name}({arguments})"

    #: Comment on the ``config`` key of an entry the file spelled out itself.
    CONFIG_TEMPLATE: ClassVar[str] = "written in full in the source file"

    #: Comment on a value a law computed from the rest of the system.
    SIZED_TEMPLATE: ClassVar[str] = "sized from {inputs}"

    #: Comment on a value a law produced without reading anything, taken from the introspection
    #: surface so that a record and ``describe`` call the same thing by the same name.
    CONSTANT_TEMPLATE: ClassVar[str] = SizableFieldKind.CONSTANT.explain()

    #: Comment on a value the file wrote on top of what its preset or constructor produced.
    OVERRIDE_TEMPLATE: ClassVar[str] = "overrides preset default {value}"

    #: Comment on a sizing source the author wrote, which the record carries over unchanged.
    WRITTEN_IN_SOURCE: ClassVar[str] = "written in source"

    #: Comment on a sizing source the record added because the run read the fact even though the
    #: author never had to name its provider.
    ADDED_FOR_REEXECUTION: ClassVar[str] = "unique provider, written for re-execution"

    #: Separator between the inputs of one sized value.
    INPUT_SEPARATOR: ClassVar[str] = ", "

    @classmethod
    def header(cls) -> str:
        """Builds the header line of a generated record.

        Returns:
            The header, naming the HiSim version that produced the file.
        """
        return cls.HEADER_TEMPLATE.format(version=RunMetadata.hisim_version())

    @classmethod
    def origin(cls, built_from: BuiltFrom) -> str:
        """Builds the comment stating where an entry's configuration came from.

        Args:
            built_from: The origin as the audit recorded it.

        Returns:
            The comment for the entry's ``config`` key.
        """
        if built_from.kind == BuiltFrom.PRESET:
            return cls.PRESET_TEMPLATE.format(name=built_from.name)
        if built_from.kind == BuiltFrom.CONSTRUCTOR:
            arguments = cls.INPUT_SEPARATOR.join(
                f"{key}={cls.value(value)}" for key, value in built_from.arguments.items()
            )
            return cls.CONSTRUCTOR_TEMPLATE.format(name=built_from.name, arguments=arguments)
        return cls.CONFIG_TEMPLATE

    @classmethod
    def sized(cls, inputs: Tuple[Tuple[str, Any], ...]) -> str:
        """Builds the comment stating where a computed value came from.

        Args:
            inputs: The ``(qualified source, value)`` pairs the law read, sibling fields of the
                same configuration included as ``self.<field>``.

        Returns:
            The comment for the value's line; the constant phrase when the law read nothing.
        """
        if not inputs:
            return cls.CONSTANT_TEMPLATE
        rendered = cls.INPUT_SEPARATOR.join(
            f"{source}={cls.value(value)}" for source, value in inputs
        )
        return cls.SIZED_TEMPLATE.format(inputs=rendered)

    @classmethod
    def override(cls, preset_default: Any) -> str:
        """Builds the comment stating what an overridden field would otherwise have held.

        Args:
            preset_default: The value the entry's preset or constructor had produced.

        Returns:
            The comment for the overriding line.
        """
        return cls.OVERRIDE_TEMPLATE.format(value=cls.value(preset_default))

    @classmethod
    def value(cls, value: Any) -> str:
        """Renders one value the way a comment quotes it.

        Comments quote values inline, so the rendering is the shortest unambiguous one rather
        than the one a YAML document would use: a string appears without quotes, a null as the
        word the record itself writes, and a number exactly as Python spells it, which is the
        spelling that round-trips a float.

        Args:
            value: The value to render.

        Returns:
            The rendered value.
        """
        if value is None:
            return CanonicalRepresenter.NULL_TEXT
        return str(value)


class AnnotatedEmitter:
    """Writes a record in the canonical style with the audit's provenance as comments.

    The document is the canonical writer's own — this class never decides what a record contains,
    only what is said about it in the margin — so the two writers cannot disagree about key order,
    about which optional blocks are written, or about anything else that is a property of the
    format rather than of the library. What is added is a header line and one end-of-line comment
    per value whose origin the audit knows.

    Called without an audit the class is a plain writer, and that is not a convenience: it is the
    equality a test pins, and the reason the second library can never quietly grow a style of its
    own.
    """

    #: Indentation of a nested mapping.
    MAPPING_INDENT: ClassVar[int] = 2

    #: Indentation of a block sequence, and the offset of its dashes; together they indent the
    #: dashes one level under their key, which is what the canonical style does.
    SEQUENCE_INDENT: ClassVar[int] = 4

    #: Column of the dash inside a block sequence.
    SEQUENCE_OFFSET: ClassVar[int] = 2

    #: Column at which a comment is placed. Zero means "one space after the value", which keeps
    #: the placement independent of every other line and therefore reproducible.
    COMMENT_COLUMN: ClassVar[int] = 0

    #: The entry key whose value the origin comment is attached to.
    CONFIG_KEY: ClassVar[str] = "config"

    #: The entry key whose values carry the sizing-source comments.
    SIZING_SOURCES_KEY: ClassVar[str] = "sizing_sources"

    #: The top-level key holding the ungrouped components.
    COMPONENTS_KEY: ClassVar[str] = "components"

    #: The top-level key holding the groups.
    GROUPS_KEY: ClassVar[str] = "groups"

    #: File name of the realized record written into a results directory. It keeps the format's
    #: own suffix because it *is* an energy-system file, loadable and re-executable as it stands.
    RECORD_FILENAME: ClassVar[str] = "realized.energy_system.yaml"

    @classmethod
    def render(cls, model: EnergySystemFile, audit: Optional[AuditRecord] = None) -> str:
        """Renders one energy system, annotated when an audit is given and plain when not.

        Args:
            model: The record to write.
            audit: The audit of the same run, whose facts become the comments; omitted for the
                plain rendering, which must equal the canonical writer's output.

        Returns:
            The YAML document, ending in a newline.
        """
        document = cls._commented(EnergySystemEmitter.to_document(model))
        if audit is not None:
            cls._annotate(document, model, audit)
        stream = io.StringIO()
        cls._yaml().dump(document, stream)
        return stream.getvalue()

    @classmethod
    def _yaml(cls) -> YAML:
        """Builds the YAML instance configured to write the canonical style.

        Returns:
            A fresh instance; instances are cheap and sharing one would make the settings a
            piece of mutable state two callers could disagree about.
        """
        writer = YAML()
        writer.Representer = CanonicalRepresenter.configured()
        writer.default_flow_style = False
        writer.allow_unicode = True
        writer.width = EnergySystemEmitter.LINE_WIDTH
        writer.indent(
            mapping=cls.MAPPING_INDENT, sequence=cls.SEQUENCE_INDENT, offset=cls.SEQUENCE_OFFSET
        )
        return writer

    @classmethod
    def _commented(cls, value: Any) -> Any:
        """Rebuilds a plain document as the commentable containers the writer needs.

        Args:
            value: A node of the canonical document.

        Returns:
            The same structure with every mapping and every list replaced by the container that
            can carry a comment; scalars are returned unchanged.
        """
        if isinstance(value, dict):
            mapping = CommentedMap()
            for key, item in value.items():
                mapping[key] = cls._commented(item)
            return mapping
        if isinstance(value, list):
            sequence = CommentedSeq()
            for item in value:
                sequence.append(cls._commented(item))
            return sequence
        return value

    @classmethod
    def _annotate(cls, document: CommentedMap, model: EnergySystemFile, audit: AuditRecord) -> None:
        """Attaches the header and every entry's comments to a rendered document.

        Args:
            document: The commentable document, in the canonical shape.
            model: The record the document was rendered from, for the entries.
            audit: The audit whose facts the comments render.
        """
        document.yaml_set_start_comment(ProvenanceComments.header())
        components = document.get(cls.COMPONENTS_KEY) or CommentedMap()
        for name, entry in model.components.items():
            cls._annotate_entry(components[name], entry, audit.component(name))
        groups = document.get(cls.GROUPS_KEY) or CommentedMap()
        for group_name, group in model.groups.items():
            grouped = groups[group_name][cls.COMPONENTS_KEY]
            for name, entry in group.components.items():
                cls._annotate_entry(grouped[name], entry, audit.component(name))

    @classmethod
    def _annotate_entry(
        cls, block: CommentedMap, entry: ComponentEntry, audit: Optional[ComponentAudit]
    ) -> None:
        """Attaches the comments of one component entry.

        Args:
            block: The entry's rendered mapping.
            entry: The entry of the record, for the sizing-source keys it carries.
            audit: The audit of the same component, or ``None`` when the run had none — which
                happens only for a document that is not the record of that audit's run.
        """
        if audit is None:
            return
        if cls.CONFIG_KEY in block:
            block.yaml_add_eol_comment(
                ProvenanceComments.origin(audit.built_from), cls.CONFIG_KEY, column=cls.COMMENT_COLUMN
            )
            cls._annotate_config(block[cls.CONFIG_KEY], audit)
        if cls.SIZING_SOURCES_KEY in block:
            cls._annotate_sizing_sources(block[cls.SIZING_SOURCES_KEY], entry, audit)

    @classmethod
    def _annotate_config(cls, block: CommentedMap, audit: ComponentAudit) -> None:
        """Attaches one comment per configuration value whose origin the audit knows.

        A value a law computed is explained by the law's inputs; a value the file overrode is
        explained by what it replaced. A field that is both — an override re-opening a field so
        that a law fills it in again — is explained by the law, because that is what produced the
        number standing in the record.

        Args:
            block: The rendered ``config`` mapping.
            audit: The audit of the component the block belongs to.
        """
        explained: List[str] = []
        for sized in audit.sized_fields:
            if sized.field in block:
                block.yaml_add_eol_comment(
                    ProvenanceComments.sized(sized.inputs), sized.field, column=cls.COMMENT_COLUMN
                )
                explained.append(sized.field)
        for override in audit.overrides:
            if override.field in block and override.field not in explained:
                block.yaml_add_eol_comment(
                    ProvenanceComments.override(override.preset_default),
                    override.field,
                    column=cls.COMMENT_COLUMN,
                )

    @classmethod
    def _annotate_sizing_sources(
        cls, block: CommentedMap, entry: ComponentEntry, audit: ComponentAudit
    ) -> None:
        """Marks each sizing source as the author's own line or as one the record added.

        The distinction is the whole point of writing the block in full: a reader comparing the
        record with the file it came from would otherwise have to guess which lines they wrote.

        Args:
            block: The rendered ``sizing_sources`` mapping.
            entry: The entry of the record, whose keys the block mirrors.
            audit: The audit of the same component, naming the facts the record added.
        """
        del entry
        for fact in block:
            added = fact in audit.added_sizing_sources
            comment = (
                ProvenanceComments.ADDED_FOR_REEXECUTION
                if added
                else ProvenanceComments.WRITTEN_IN_SOURCE
            )
            block.yaml_add_eol_comment(comment, fact, column=cls.COMMENT_COLUMN)


def render_record(model: EnergySystemFile, audit: Optional[AuditRecord] = None) -> str:
    """Renders a realized record as annotated YAML text.

    Args:
        model: The record to write.
        audit: The audit of the same run; without it the rendering is plain and equals the
            canonical writer's output exactly.

    Returns:
        The document, ending in a newline.
    """
    return AnnotatedEmitter.render(model, audit)


def write_record(model: EnergySystemFile, audit: Optional[AuditRecord], path: str) -> str:
    """Writes a realized record to a file.

    Args:
        model: The record to write.
        audit: The audit of the same run, whose facts become the comments.
        path: Where to write it.

    Returns:
        The path written.
    """
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(render_record(model, audit))
    return path


def strip_comments(text: str) -> str:
    """Returns the same record with every comment removed, for a caller proving they do not matter.

    Reading the document and writing it back through the canonical writer is what removes them:
    the reader never sees a comment in the first place, so what comes back is the record's data
    and nothing else. Used to demonstrate that a record with its provenance stripped still
    produces the same run, which is the guarantee that makes comments safe to carry.

    Args:
        text: The annotated record.

    Returns:
        The same document without a single comment.

    Raises:
        EnergySystemError: For any condition of the error catalogue, since the text has to be a
            valid record to be rewritten.
    """
    return dump_energy_system(EnergySystemReader.read(text))
