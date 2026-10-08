"""The economics stage record: what a staged plan needs to know about one stage beyond its stored inputs.

`python -m hisim.economics staged` prices a plan out of stage directories. Each directory carries the stage's stored
inputs (`economic_inputs.json`) and a mapping report written by the translator that produced the stage. The
translator states, inside that report under the key `economics_stage`, one plain JSON record:

* which catalogue measures the stage carries out (`measures`, in catalogue order);
* which measure created which cost subject (`subjects`), which subjects have no price (`unpriced_subjects`), which
  cost nothing (`costless_subjects`), the note each subject's row carries (`subject_notes`) and which reference
  subjects each measure subject replaces (`replaces_subjects`);
* the translator's tables a reader's price quote is checked against (`catalogue`): every catalogue measure id, the
  measures that cost nothing, and per measure the rule that names the subject a quote prices.

Example::

    {"schema_version": 1,
     "measures": ["heating_system"],
     "subjects": {"HeatPump": "heating_system", "GenericBoiler": null},
     "unpriced_subjects": [], "costless_subjects": [], "subject_notes": {},
     "replaces_subjects": {"HeatPump": ["GenericBoiler"]},
     "catalogue": {"measure_ids": ["heating_system", "..."],
                   "costless_measure_ids": ["change_room_temperature"],
                   "main_subjects": {"heating_system": {"rule": "asset_class", "asset_classes": ["HeatPump"]}}}}

The format belongs to the cost engine, which reads it; the translator (`hisim.renovisor`) builds and writes it. The
engine therefore never imports the translator: the translator sits on top of the engine, not below it. The record
lives inside the mapping report because the report is the file a backend already copies into every stage
directory.
"""

import enum
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Tuple

from hisim.loadtypes import ComponentType


class StageRecordError(ValueError):
    """A mapping report whose economics stage record is missing, of an unknown version or malformed.

    The staged command turns it into a refused plan (exit 2 with a `problems.json`) naming the stage.
    """


class MainSubjectError(RuntimeError):
    """A quoted measure's main subject cannot be determined from the stage it is quoted for.

    The subject a reader's quote prices is decided by a `MainSubjectRule` the translator declared, never guessed. A
    measure without a rule, or a stage whose subjects for the measure match the rule zero or several times, is a
    translator or table defect, not a bad plan: the staged command exits 3 on it.
    """


class MainSubjectRuleKind(str, enum.Enum):
    """How a measure's main subject is found among the subjects it created in one stage.

    `NAMED_BY_MEASURE`: the subject whose name is the measure id (an envelope measure, a measure HiSim holds no price
    for). `ASSET_CLASS`: the one subject priced as one of the rule's asset classes (the generator of
    `heating_system`, not the buffer installed with it).
    """

    NAMED_BY_MEASURE = "named_by_measure"
    ASSET_CLASS = "asset_class"


@dataclass(frozen=True)
class MainSubjectRule:
    """Which cost subject a reader's quote for one measure prices: its main subject.

    A quote is an installed total for one measure. It replaces the year-0 investment of the measure's main subject in
    the quoted stage; the measure's other subjects there are bought at zero.

    Example::

        rule = MainSubjectRule(MainSubjectRuleKind.ASSET_CLASS, frozenset({ComponentType.HEAT_PUMP}))
        rule.resolve("heating_system", 1, {"HeatPump": ComponentType.HEAT_PUMP, "Buffer": None})
        # -> ("HeatPump", ("Buffer",))

    Args:
        kind: How the main subject is found.
        asset_classes: For `ASSET_CLASS`, the classes the main subject may be priced as; empty otherwise.
    """

    kind: MainSubjectRuleKind
    asset_classes: FrozenSet[ComponentType] = frozenset()

    #: The JSON keys of one rule.
    RULE_KEY: ClassVar[str] = "rule"
    ASSET_CLASSES_KEY: ClassVar[str] = "asset_classes"

    #: What an unresolvable quote is refused with.
    UNRESOLVED_MESSAGE: ClassVar[str] = (
        "the quote for {measure_id!r} in stage {stage} cannot be placed: {reason} The main subject a "
        "quote prices is declared in MainSubjects (hisim/renovisor/economics.py) and never guessed."
    )

    @classmethod
    def undeclared(cls, measure_id: str, stage: int) -> MainSubjectError:
        """Return the error for a quoted measure that has no rule at all.

        Args:
            measure_id: The quoted measure.
            stage: The stage it is quoted for, for the message.

        Returns:
            The error, for the caller to raise.
        """
        return MainSubjectError(
            cls.UNRESOLVED_MESSAGE.format(
                measure_id=measure_id, stage=stage, reason="the measure has no main subject declared."
            )
        )

    def resolve(
        self, measure_id: str, stage: int, subjects: Mapping[str, Optional[ComponentType]]
    ) -> Tuple[str, Tuple[str, ...]]:
        """Return the main subject of one quoted measure in one stage, and the measure's other subjects.

        Args:
            measure_id: The quoted measure.
            stage: The stage it is quoted for, for the message.
            subjects: Every subject the translator stamped with the measure among those the stage has -> its asset
                class, or None for a subject the stage holds no cost facts for (a measure-only subject).

        Returns:
            `(main subject, the measure's other subjects in the stage, sorted)`.

        Raises:
            MainSubjectError: If the stage's subjects do not single out exactly one main subject.
        """
        if self.kind is MainSubjectRuleKind.NAMED_BY_MEASURE:
            if measure_id not in subjects:
                raise MainSubjectError(
                    self.UNRESOLVED_MESSAGE.format(
                        measure_id=measure_id,
                        stage=stage,
                        reason=f"its subject is named by the measure id, and the stage has no subject {measure_id!r}.",
                    )
                )
            main = measure_id
        else:
            matching = sorted(
                subject for subject, asset_class in subjects.items() if asset_class in self.asset_classes
            )
            if len(matching) != 1:
                raise MainSubjectError(
                    self.UNRESOLVED_MESSAGE.format(
                        measure_id=measure_id,
                        stage=stage,
                        reason=(
                            f"{len(matching)} of the stage's subjects of the measure ({', '.join(matching) or 'none'}) "
                            f"are priced as {', '.join(sorted(item.value for item in self.asset_classes))}, and a "
                            "quote prices exactly one."
                        ),
                    )
                )
            main = matching[0]
        return main, tuple(sorted(subject for subject in subjects if subject != main))

    def to_json(self) -> Dict[str, Any]:
        """Return the rule as plain JSON: `{"rule": ...}`, plus the sorted `asset_classes` for an asset-class rule."""
        document: Dict[str, Any] = {self.RULE_KEY: self.kind.value}
        if self.kind is MainSubjectRuleKind.ASSET_CLASS:
            document[self.ASSET_CLASSES_KEY] = sorted(item.value for item in self.asset_classes)
        return document

    @classmethod
    def from_json(cls, raw: Any, path: str) -> "MainSubjectRule":
        """Read one rule back from `to_json`'s shape.

        Args:
            raw: The parsed rule.
            path: Where it sits in the record, for the message.

        Returns:
            The rule.

        Raises:
            StageRecordError: If the rule names an unknown kind or asset class, or is not an object.
        """
        if not isinstance(raw, dict):
            raise StageRecordError(f"{path} is not an object.")
        try:
            kind = MainSubjectRuleKind(raw.get(cls.RULE_KEY))
        except ValueError:
            raise StageRecordError(
                f"{path}.{cls.RULE_KEY} is {raw.get(cls.RULE_KEY)!r}, not one of "
                f"{', '.join(item.value for item in MainSubjectRuleKind)}."
            ) from None
        if kind is MainSubjectRuleKind.NAMED_BY_MEASURE:
            return cls(kind)
        names = StageRecordFields.strings(raw.get(cls.ASSET_CLASSES_KEY), f"{path}.{cls.ASSET_CLASSES_KEY}")
        try:
            return cls(kind, frozenset(ComponentType(name) for name in names))
        except ValueError as error:
            raise StageRecordError(f"{path}.{cls.ASSET_CLASSES_KEY} names an unknown asset class ({error}).") from None


@dataclass(frozen=True)
class StageCatalogue:
    """The translator's measure tables, which a reader's price quotes are checked against.

    The same in every stage a single translator produced; a plan whose stages disagree about them is refused.

    Args:
        measure_ids: Every catalogue measure id, in catalogue order.
        costless_measure_ids: The measures that cost nothing to carry out, so take no quote.
        main_subjects: Measure id -> the rule naming the subject a quote for it prices.
    """

    measure_ids: Tuple[str, ...]
    costless_measure_ids: Tuple[str, ...]
    main_subjects: Mapping[str, MainSubjectRule]

    #: The JSON keys of the catalogue block.
    MEASURE_IDS_KEY: ClassVar[str] = "measure_ids"
    COSTLESS_MEASURE_IDS_KEY: ClassVar[str] = "costless_measure_ids"
    MAIN_SUBJECTS_KEY: ClassVar[str] = "main_subjects"

    def to_json(self) -> Dict[str, Any]:
        """Return the block as plain JSON, `main_subjects` sorted by measure id."""
        return {
            self.MEASURE_IDS_KEY: list(self.measure_ids),
            self.COSTLESS_MEASURE_IDS_KEY: list(self.costless_measure_ids),
            self.MAIN_SUBJECTS_KEY: {
                measure_id: self.main_subjects[measure_id].to_json() for measure_id in sorted(self.main_subjects)
            },
        }

    @classmethod
    def from_json(cls, raw: Any, path: str) -> "StageCatalogue":
        """Read the block back from `to_json`'s shape.

        Args:
            raw: The parsed block.
            path: Where it sits in the record, for the message.

        Returns:
            The catalogue.

        Raises:
            StageRecordError: If a key is missing or of the wrong type.
        """
        if not isinstance(raw, dict):
            raise StageRecordError(f"{path} is not an object.")
        rules = raw.get(cls.MAIN_SUBJECTS_KEY)
        if not isinstance(rules, dict):
            raise StageRecordError(f"{path}.{cls.MAIN_SUBJECTS_KEY} is not an object.")
        return cls(
            measure_ids=StageRecordFields.strings(raw.get(cls.MEASURE_IDS_KEY), f"{path}.{cls.MEASURE_IDS_KEY}"),
            costless_measure_ids=StageRecordFields.strings(
                raw.get(cls.COSTLESS_MEASURE_IDS_KEY), f"{path}.{cls.COSTLESS_MEASURE_IDS_KEY}"
            ),
            main_subjects={
                str(measure_id): MainSubjectRule.from_json(rule, f"{path}.{cls.MAIN_SUBJECTS_KEY}.{measure_id}")
                for measure_id, rule in rules.items()
            },
        )


class StageRecordFields:
    """Type checks for the record's plain JSON values, each raising `StageRecordError` with the value's path."""

    @staticmethod
    def strings(raw: Any, path: str) -> Tuple[str, ...]:
        """Return a list of strings as a tuple.

        Raises:
            StageRecordError: If the value is not a list of strings.
        """
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise StageRecordError(f"{path} is not a list of strings.")
        return tuple(raw)

    @staticmethod
    def string_map(raw: Any, path: str, nullable: bool) -> Dict[str, Any]:
        """Return an object whose values are strings (or null, when `nullable`).

        Raises:
            StageRecordError: If the value is not such an object.
        """
        if not isinstance(raw, dict) or not all(
            isinstance(value, str) or (nullable and value is None) for value in raw.values()
        ):
            kind = "strings or null" if nullable else "strings"
            raise StageRecordError(f"{path} is not an object of {kind}.")
        return dict(raw)

    @classmethod
    def string_lists(cls, raw: Any, path: str) -> Dict[str, List[str]]:
        """Return an object whose values are lists of strings.

        Raises:
            StageRecordError: If the value is not such an object.
        """
        if not isinstance(raw, dict):
            raise StageRecordError(f"{path} is not an object.")
        return {str(key): list(cls.strings(value, f"{path}.{key}")) for key, value in raw.items()}


@dataclass(frozen=True)
class EconomicsStageRecord:
    """One stage's economics stage record, as the translator wrote it into the stage's mapping report.

    See the module docstring for the JSON shape. `KEY` is where the record sits in the mapping report.

    Args:
        measures: The catalogue measures the stage carries out, in catalogue order. For a RenoVisor stage this
            includes the measures of earlier stages its package repeats.
        subjects: Cost subject -> the measure that created it, or None for a subject that was already in the house.
        unpriced_subjects: The subjects with no price behind them.
        costless_subjects: The subjects of a measure that costs nothing to carry out.
        subject_notes: Subject -> the sentence its `by_subject` row carries as `note`.
        replaces_subjects: Measure subject -> the reference subjects it replaces.
        catalogue: The translator's measure tables.
    """

    measures: Tuple[str, ...]
    subjects: Mapping[str, Optional[str]]
    unpriced_subjects: Tuple[str, ...]
    costless_subjects: Tuple[str, ...]
    subject_notes: Mapping[str, str]
    replaces_subjects: Mapping[str, List[str]]
    catalogue: StageCatalogue

    #: The key of the record inside the mapping report.
    KEY: ClassVar[str] = "economics_stage"

    #: The one version of the record this engine reads and the translator writes.
    SCHEMA_VERSION: ClassVar[int] = 1

    #: The JSON keys of the record.
    SCHEMA_VERSION_KEY: ClassVar[str] = "schema_version"
    MEASURES_KEY: ClassVar[str] = "measures"
    SUBJECTS_KEY: ClassVar[str] = "subjects"
    UNPRICED_SUBJECTS_KEY: ClassVar[str] = "unpriced_subjects"
    COSTLESS_SUBJECTS_KEY: ClassVar[str] = "costless_subjects"
    SUBJECT_NOTES_KEY: ClassVar[str] = "subject_notes"
    REPLACES_SUBJECTS_KEY: ClassVar[str] = "replaces_subjects"
    CATALOGUE_KEY: ClassVar[str] = "catalogue"

    #: What a mapping report without a record is refused with.
    MISSING_MESSAGE: ClassVar[str] = (
        "it carries no {key!r} record, which says which measures the stage carries out and which cost "
        "subjects they created. The report was written by a translator older than the record; translate "
        "the stage again with this image."
    )

    #: What a record of another version is refused with.
    VERSION_MESSAGE: ClassVar[str] = (
        "its {key!r} record has schema version {found!r}, and this engine reads version {expected}. "
        "Translate the stage again with this image."
    )

    def to_json(self) -> Dict[str, Any]:
        """Return the record as plain JSON, maps sorted by key, ready to be written into the mapping report."""
        return {
            self.SCHEMA_VERSION_KEY: self.SCHEMA_VERSION,
            self.MEASURES_KEY: list(self.measures),
            self.SUBJECTS_KEY: {subject: self.subjects[subject] for subject in sorted(self.subjects)},
            self.UNPRICED_SUBJECTS_KEY: list(self.unpriced_subjects),
            self.COSTLESS_SUBJECTS_KEY: list(self.costless_subjects),
            self.SUBJECT_NOTES_KEY: {subject: self.subject_notes[subject] for subject in sorted(self.subject_notes)},
            self.REPLACES_SUBJECTS_KEY: {
                subject: list(self.replaces_subjects[subject]) for subject in sorted(self.replaces_subjects)
            },
            self.CATALOGUE_KEY: self.catalogue.to_json(),
        }

    @classmethod
    def from_report(cls, report: Any) -> "EconomicsStageRecord":
        """Read the record out of a parsed mapping report.

        Args:
            report: The parsed `mapping_report.json`.

        Returns:
            The record.

        Raises:
            StageRecordError: If the report has no record, the record's `schema_version` is not `SCHEMA_VERSION`, or a
                key is missing or of the wrong type. The message is one sentence fragment saying which.
        """
        raw = report.get(cls.KEY) if isinstance(report, dict) else None
        if raw is None:
            raise StageRecordError(cls.MISSING_MESSAGE.format(key=cls.KEY))
        if not isinstance(raw, dict):
            raise StageRecordError(f"its {cls.KEY!r} record is not an object.")
        found = raw.get(cls.SCHEMA_VERSION_KEY)
        if found != cls.SCHEMA_VERSION or isinstance(found, bool):
            raise StageRecordError(cls.VERSION_MESSAGE.format(key=cls.KEY, found=found, expected=cls.SCHEMA_VERSION))
        return cls(
            measures=StageRecordFields.strings(raw.get(cls.MEASURES_KEY), cls._path(cls.MEASURES_KEY)),
            subjects=StageRecordFields.string_map(raw.get(cls.SUBJECTS_KEY), cls._path(cls.SUBJECTS_KEY), True),
            unpriced_subjects=StageRecordFields.strings(
                raw.get(cls.UNPRICED_SUBJECTS_KEY), cls._path(cls.UNPRICED_SUBJECTS_KEY)
            ),
            costless_subjects=StageRecordFields.strings(
                raw.get(cls.COSTLESS_SUBJECTS_KEY), cls._path(cls.COSTLESS_SUBJECTS_KEY)
            ),
            subject_notes=StageRecordFields.string_map(
                raw.get(cls.SUBJECT_NOTES_KEY), cls._path(cls.SUBJECT_NOTES_KEY), False
            ),
            replaces_subjects=StageRecordFields.string_lists(
                raw.get(cls.REPLACES_SUBJECTS_KEY), cls._path(cls.REPLACES_SUBJECTS_KEY)
            ),
            catalogue=StageCatalogue.from_json(raw.get(cls.CATALOGUE_KEY), cls._path(cls.CATALOGUE_KEY)),
        )

    @classmethod
    def _path(cls, key: str) -> str:
        """Return how one key of the record is named in a refusal, e.g. `economics_stage.measures`."""
        return f"{cls.KEY}.{key}"
