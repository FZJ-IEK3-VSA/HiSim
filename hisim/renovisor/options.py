"""Reading and validating the option values one package entry supplies for one measure.

A package entry is ``{"measure_id": "EXTERNAL_INSULATION", "options": {"material": "...",
"thickness_in_mm": 140}}`` (decision Q1). :class:`Options` is what a registry function reads that
entry through: it checks the supplied values against the measure's :class:`~hisim.renovisor.
catalogue.MeasureSpec`, fills in the defaults for the Experts options that most requests omit, and
records a report line for each (requirement M7)::

    material = options.enum("material")
    thickness = options.integer("thickness_in_mm", default=Default(80, "target-driven, Q11"))

Everything that can be wrong with an option is a :class:`~hisim.renovisor.reasons.ValidationError`
naming the JSON path of the offending value, so the caller is told which of its fields to fix:
an option the measure does not have, a value outside the catalogue's list, a value of the wrong
JSON type, or a required option that is missing.
"""

from dataclasses import dataclass
from typing import Any, ClassVar, Mapping, Optional

from hisim.renovisor.catalogue import MeasureSpec, OptionSpec
from hisim.renovisor.reasons import ReasonCode, ValidationError
from hisim.renovisor.report import MappingReport, ReportStatus


@dataclass(frozen=True)
class Default:
    """The value to use when an option is absent, and where that value comes from.

    Every Experts option needs one (requirement M7), and the source is not decoration: it is what
    the translation report prints on the ``DEFAULTED`` line, so a reader can check the number
    instead of trusting it.

    Args:
        value: The value to use, of the option's declared type.
        source: Where the number comes from — a regulation, a rule, or an explicitly provisional
            assumption. Appears verbatim in the report line.
    """

    value: Any
    source: str


class Options:
    """The option values one package entry supplies for one measure, validated against its spec.

    Example::

        options = Options(spec, {"material": "metac_glasswool"}, report, "package.measures[0]")
        options.is_supplied("thickness_in_mm")   # False
        options.enum("material")                 # 'metac_glasswool'

    Construction validates the supplied keys; the readers validate the values, one at a time, as
    the registry function asks for them. That split is deliberate: a measure that refuses before
    reading an option (an air conditioner, for which no base file exists) should refuse rather
    than complain about a missing option.

    Args:
        spec: The catalogue spec of the measure this entry names.
        supplied: The request's ``options`` object for this entry.
        report: The translation report that ``used`` and ``defaulted`` lines are written into.
        path: The JSON path of the package entry, e.g. ``package.measures[3]``; every error and
            every report line is addressed under it.

    Raises:
        ValidationError: ``UNKNOWN_OPTION`` when *supplied* carries a key the measure does not
            have.
    """

    #: The path segment under which option values sit inside a package entry.
    OPTIONS_SEGMENT: ClassVar[str] = "options"

    #: The brackets a package entry's path carries its position in, e.g. ``package.measures[3]``.
    INDEX_OPEN: ClassVar[str] = "["
    INDEX_CLOSE: ClassVar[str] = "]"

    def __init__(self, spec: MeasureSpec, supplied: Mapping[str, Any], report: MappingReport, path: str) -> None:
        """Store the spec and the supplied values, and reject keys the measure does not have."""
        self._spec = spec
        self._supplied = dict(supplied)
        self._report = report
        self._path = path
        known = set(spec.option_ids())
        for key in self._supplied:
            if key not in known:
                raise ValidationError(
                    ReasonCode.UNKNOWN_OPTION,
                    self.option_path(key),
                    f"measure '{spec.measure_id}' has no option '{key}'; it has {sorted(known) or 'none'}",
                )

    @property
    def spec(self) -> MeasureSpec:
        """Return the catalogue spec of the measure these options belong to."""
        return self._spec

    @property
    def path(self) -> str:
        """Return the JSON path of the package entry, e.g. ``package.measures[3]``."""
        return self._path

    def option_path(self, option_id: str) -> str:
        """Return the JSON path of one option value, e.g. ``package.measures[3].options.material``."""
        return f"{self._path}.{self.OPTIONS_SEGMENT}.{option_id}"

    def is_supplied(self, option_id: str) -> bool:
        """Return whether the request carries a value for this option."""
        return option_id in self._supplied

    def note(self, status: ReportStatus, note: str, rule: Optional[str] = None) -> None:
        """Write the measure-level report line for this package entry.

        Registry functions use it to say what they did and which rule produced it — the line the
        translation map reads. The application adds a derived line only for measures that wrote
        none.

        Args:
            status: One of :class:`~hisim.renovisor.report.ReportStatus`.
            note: What the measure did, in one sentence.
            rule: The law, table or formula behind the number, when there is one.
        """
        self._report.measure(self._spec.measure_id, status, note, rule, index=self.index())

    def index(self) -> int:
        """Return this package entry's position, read off its own path.

        The position is what the report keys a measure line by, and reading it off the path is
        what keeps a measure that writes a line and one that does not from ending up at the same
        position: the path was built from the package order in the first place.

        Returns:
            The zero-based position, or ``0`` for a path carrying no index, which only a caller
            constructing an ``Options`` by hand can produce.
        """
        head, _, tail = self._path.partition(self.INDEX_OPEN)
        del head
        digits = tail.partition(self.INDEX_CLOSE)[0]
        return int(digits) if digits.isdigit() else 0

    def enum(self, option_id: str) -> str:
        """Return one enum option's value in HiSim spelling.

        Args:
            option_id: The option's snake_case id.

        Returns:
            The supplied value, which is one of the option's :attr:`~hisim.renovisor.catalogue.
            OptionSpec.values`.

        Raises:
            ValidationError: ``UNKNOWN_OPTION`` when the measure has no such option,
                ``MISSING_OPTION`` when the request omits it, ``OPTION_TYPE_MISMATCH`` when the
                value is not a string, ``UNKNOWN_OPTION_VALUE`` when it is not in the list.
        """
        option = self._option(option_id)
        raw = self._require(option_id)
        if not isinstance(raw, str):
            raise ValidationError(
                ReasonCode.OPTION_TYPE_MISMATCH,
                self.option_path(option_id),
                f"option '{option_id}' takes one of {list(option.values)}, not {type(raw).__name__}",
            )
        if raw not in option.values:
            raise ValidationError(
                ReasonCode.UNKNOWN_OPTION_VALUE,
                self.option_path(option_id),
                f"'{raw}' is not one of {list(option.values)}",
            )
        self._report.used(self.option_path(option_id), f"option '{option.display_name}' = {raw}")
        return raw

    def integer(
        self,
        option_id: str,
        default: Optional[Default] = None,
        minimum: Optional[int] = None,
        maximum: Optional[int] = None,
    ) -> int:
        """Return one integer option's value, defaulting and range-checking it.

        Args:
            option_id: The option's snake_case id.
            default: What to use when the request omits the option, with its source. ``None``
                makes an absent option a validation error.
            minimum: Smallest acceptable value, when the measure imposes one (a room temperature
                below 15 °C is not a renovation).
            maximum: Largest acceptable value, when the measure imposes one.

        Returns:
            The supplied value, or the default's value.

        Raises:
            ValidationError: ``MISSING_OPTION`` when the option is absent and no default was
                given, ``OPTION_TYPE_MISMATCH`` when the value is not a JSON integer,
                ``UNKNOWN_OPTION_VALUE`` when it is outside the catalogue's list or the given
                range.
        """
        option = self._option(option_id)
        if not self.is_supplied(option_id):
            value = self._apply_default(option_id, default)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValidationError(
                    ReasonCode.OPTION_TYPE_MISMATCH,
                    self.option_path(option_id),
                    f"the default for '{option_id}' is {value!r}, which is not an integer",
                )
            return value
        raw = self._supplied[option_id]
        if not isinstance(raw, int) or isinstance(raw, bool):
            raise ValidationError(
                ReasonCode.OPTION_TYPE_MISMATCH,
                self.option_path(option_id),
                f"option '{option_id}' takes a whole number, not {type(raw).__name__}",
            )
        if option.values and str(raw) not in option.values:
            raise ValidationError(
                ReasonCode.UNKNOWN_OPTION_VALUE,
                self.option_path(option_id),
                f"{raw} is not one of {list(option.values)}",
            )
        if minimum is not None and raw < minimum:
            raise ValidationError(
                ReasonCode.UNKNOWN_OPTION_VALUE,
                self.option_path(option_id),
                f"{raw} is below the smallest value this measure accepts ({minimum})",
            )
        if maximum is not None and raw > maximum:
            raise ValidationError(
                ReasonCode.UNKNOWN_OPTION_VALUE,
                self.option_path(option_id),
                f"{raw} is above the largest value this measure accepts ({maximum})",
            )
        self._report.used(self.option_path(option_id), f"option '{option.display_name}' = {raw}")
        return raw

    def boolean(self, option_id: str, default: Optional[Default] = None) -> bool:
        """Return one boolean option's value.

        Args:
            option_id: The option's snake_case id.
            default: What to use when the request omits the option, with its source.

        Returns:
            The supplied value, or the default's value.

        Raises:
            ValidationError: ``MISSING_OPTION`` when the option is absent and no default was
                given, ``OPTION_TYPE_MISMATCH`` when the value is not a JSON boolean. Strings are
                never accepted: ``"no"`` is a true string in every JSON reader.
        """
        option = self._option(option_id)
        if not self.is_supplied(option_id):
            value = self._apply_default(option_id, default)
            if not isinstance(value, bool):
                raise ValidationError(
                    ReasonCode.OPTION_TYPE_MISMATCH,
                    self.option_path(option_id),
                    f"the default for '{option_id}' is {value!r}, which is not a boolean",
                )
            return value
        raw = self._supplied[option_id]
        if not isinstance(raw, bool):
            raise ValidationError(
                ReasonCode.OPTION_TYPE_MISMATCH,
                self.option_path(option_id),
                f"option '{option_id}' takes true or false, not {type(raw).__name__}",
            )
        self._report.used(self.option_path(option_id), f"option '{option.display_name}' = {raw}")
        return raw

    def _option(self, option_id: str) -> OptionSpec:
        """Return the option's spec, or raise ``ValidationError(UNKNOWN_OPTION)``."""
        try:
            return self._spec.option(option_id)
        except KeyError as error:
            raise ValidationError(
                ReasonCode.UNKNOWN_OPTION,
                self.option_path(option_id),
                f"measure '{self._spec.measure_id}' has no option '{option_id}'",
            ) from error

    def _require(self, option_id: str) -> Any:
        """Return the supplied value, or raise ``ValidationError(MISSING_OPTION)``."""
        if option_id not in self._supplied:
            raise ValidationError(
                ReasonCode.MISSING_OPTION,
                self.option_path(option_id),
                f"measure '{self._spec.measure_id}' needs option '{option_id}'",
            )
        return self._supplied[option_id]

    def _apply_default(self, option_id: str, default: Optional[Default]) -> Any:
        """Return the default's value and record its report line, or raise ``MISSING_OPTION``."""
        if default is None:
            raise ValidationError(
                ReasonCode.MISSING_OPTION,
                self.option_path(option_id),
                f"measure '{self._spec.measure_id}' needs option '{option_id}' and has no default for it",
            )
        self._report.defaulted(
            self.option_path(option_id),
            f"absent from the request; using {default.value}",
            rule=default.source,
        )
        return default.value
