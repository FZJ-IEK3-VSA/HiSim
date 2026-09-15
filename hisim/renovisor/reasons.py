"""The closed catalogue of reason codes and the two error types the translation layer raises.

Every way a RenoVisor request can fail to become a simulation has a code here, and the code is
what reaches the caller in ``errors.json`` (requirement R13.2.2). The catalogue has three groups,
which are three different things for the caller to do:

*validation* — the request is malformed and the caller must fix it (an option the catalogue does
not have, a measure named twice);
*refusal* — the request is well formed but this HiSim cannot simulate it, and the caller must ask
for something else (a material with no database row, a base file that does not exist);
*no effect* — the request is simulated as asked, but one measure changed nothing because HiSim has
no model for it, which the translation report states rather than hiding;
*crash* — the request was accepted and the calculation then failed, which is the third outcome
requirement R11 insists on telling apart from the other two.

Example::

    raise ValidationError(ReasonCode.UNKNOWN_OPTION_VALUE,
                          "package.measures[3].options.material",
                          "'PIR' is not one of EPS, XPS")

Decisions: C3, Q4, Q6, Q7, Q13, Q14, Q15.
"""

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Dict, Sequence, Tuple


class ReasonCode(str, Enum):
    """Why a request was rejected, refused, or simulated with one measure doing nothing.

    Member names are the wire spelling and the values equal the names, as decision C3 requires of
    every vocabulary the contract carries. :meth:`describe` returns the one-line description that
    ``errors.json`` carries, so the text has one source.
    """

    UNKNOWN_MEASURE = "UNKNOWN_MEASURE"
    UNKNOWN_OPTION = "UNKNOWN_OPTION"
    UNKNOWN_OPTION_VALUE = "UNKNOWN_OPTION_VALUE"
    OPTION_TYPE_MISMATCH = "OPTION_TYPE_MISMATCH"
    MISSING_OPTION = "MISSING_OPTION"
    UNKNOWN_KEY_IN_MEASURE = "UNKNOWN_KEY_IN_MEASURE"
    STAGE_ZERO_MEASURE = "STAGE_ZERO_MEASURE"
    DUPLICATE_MEASURE = "DUPLICATE_MEASURE"
    SCHEMA_VIOLATION = "SCHEMA_VIOLATION"

    MATERIAL_NOT_IN_DATABASE = "MATERIAL_NOT_IN_DATABASE"
    UNDEFINED_MEASURE_BUILDUP = "UNDEFINED_MEASURE_BUILDUP"
    UNSUPPORTED_SYSTEM = "UNSUPPORTED_SYSTEM"
    NO_BASE_FILE_FOR_COMBINATION = "NO_BASE_FILE_FOR_COMBINATION"
    CONTRADICTORY_MEASURES = "CONTRADICTORY_MEASURES"
    MEASURE_DOES_NOT_FIT_BUILDING = "MEASURE_DOES_NOT_FIT_BUILDING"
    CONFLICTING_WRITES = "CONFLICTING_WRITES"
    TOO_MANY_VEHICLES = "TOO_MANY_VEHICLES"
    NO_TABULA_ARCHETYPE = "NO_TABULA_ARCHETYPE"
    NO_WEATHER_FOR_COUNTRY = "NO_WEATHER_FOR_COUNTRY"

    NO_APPLIANCE_SUBMODEL = "NO_APPLIANCE_SUBMODEL"
    NO_INFILTRATION_MODEL = "NO_INFILTRATION_MODEL"
    NO_SHADING_MODEL = "NO_SHADING_MODEL"
    NO_VENTILATION_MODEL = "NO_VENTILATION_MODEL"
    NO_CONTROL_SCHEDULE_MODEL = "NO_CONTROL_SCHEDULE_MODEL"
    NO_BEHAVIOUR_MODEL = "NO_BEHAVIOUR_MODEL"

    SIMULATION_FAILED = "SIMULATION_FAILED"
    RESULT_DERIVATION_FAILED = "RESULT_DERIVATION_FAILED"

    def describe(self) -> str:
        """Return the one-line description of this code for ``errors.json``.

        Example: ``ReasonCode.TOO_MANY_VEHICLES.describe()`` is "No recorded base file carries
        more than one electric vehicle."

        Returns:
            The description from :class:`ReasonDescriptions`.

        Raises:
            KeyError: When a member was added without a description, which the reason-code test
                turns into a failing build.
        """
        return ReasonDescriptions.BY_CODE[self]

    def group(self) -> "ReasonGroup":
        """Return which of the three kinds of outcome this code belongs to.

        Returns:
            :class:`ReasonGroup` — ``VALIDATION`` for a malformed request, ``REFUSAL`` for a
            well-formed request this HiSim cannot simulate, ``NO_EFFECT`` for a measure that was
            accepted and changed nothing, ``CRASH`` for a calculation that failed after the
            request had been accepted.

        Raises:
            KeyError: When a member was added without a group.
        """
        return ReasonDescriptions.GROUP_BY_CODE[self]


class ReasonGroup(str, Enum):
    """The three kinds of outcome a reason code can stand for.

    A caller reads the group to decide what to do: fix the request (``VALIDATION``), ask for
    something else (``REFUSAL``), accept a result in which one measure did nothing
    (``NO_EFFECT``), or retry and report the failure (``CRASH``).
    """

    VALIDATION = "VALIDATION"
    REFUSAL = "REFUSAL"
    NO_EFFECT = "NO_EFFECT"
    CRASH = "CRASH"


class ReasonDescriptions:
    """The description and the group of every :class:`ReasonCode`, in one table.

    Kept beside the enum rather than inside it because a plain dictionary in an ``Enum`` body
    would be read as another member. ``errors.json`` and the translation map both read this table,
    so a code's text exists once.
    """

    BY_CODE: ClassVar[Dict[ReasonCode, str]] = {
        ReasonCode.UNKNOWN_MEASURE: "The package names a measure the catalogue does not have.",
        ReasonCode.UNKNOWN_OPTION: "The package sets an option the measure does not have.",
        ReasonCode.UNKNOWN_OPTION_VALUE: "The package sets an option to a value the catalogue does not allow.",
        ReasonCode.OPTION_TYPE_MISMATCH: "An option value has the wrong JSON type for its declared value type.",
        ReasonCode.MISSING_OPTION: "A required option is absent and the measure has no default for it.",
        ReasonCode.UNKNOWN_KEY_IN_MEASURE: "A package entry carries a key other than measure_id and options.",
        ReasonCode.STAGE_ZERO_MEASURE: "A package carries a stage-0 measure; the base state comes from the "
                                       "inventory alone.",
        ReasonCode.DUPLICATE_MEASURE: "The package names the same measure twice.",
        ReasonCode.SCHEMA_VIOLATION: "The inventory does not satisfy the contract's HomeInventoryInput schema.",
        ReasonCode.MATERIAL_NOT_IN_DATABASE: "The chosen material has no row in the insulation-material database.",
        ReasonCode.UNDEFINED_MEASURE_BUILDUP: "The measure carries no options and no build-up is defined for it.",
        ReasonCode.UNSUPPORTED_SYSTEM: "No HiSim component models the requested system.",
        ReasonCode.NO_BASE_FILE_FOR_COMBINATION: "No recorded energy-system file exists for this combination.",
        ReasonCode.CONTRADICTORY_MEASURES: "Two measures on one envelope element describe different constructions.",
        ReasonCode.MEASURE_DOES_NOT_FIT_BUILDING: "The measure does not fit the building's recorded construction.",
        ReasonCode.CONFLICTING_WRITES: "Two measures write different values to the same inventory field.",
        ReasonCode.TOO_MANY_VEHICLES: "No recorded base file carries more than one electric vehicle.",
        ReasonCode.NO_TABULA_ARCHETYPE: "No TABULA archetype exists for this country and building type.",
        ReasonCode.NO_WEATHER_FOR_COUNTRY: "No weather station of the catalogue is named after this country code.",
        ReasonCode.NO_APPLIANCE_SUBMODEL: "HiSim has no separable appliance or lighting load to change.",
        ReasonCode.NO_INFILTRATION_MODEL: "HiSim has no infiltration or air-tightness model for the MVP.",
        ReasonCode.NO_SHADING_MODEL: "HiSim has no external shading model for the MVP.",
        ReasonCode.NO_VENTILATION_MODEL: "HiSim has no mechanical ventilation model for the MVP.",
        ReasonCode.NO_CONTROL_SCHEDULE_MODEL: "HiSim has no heating control schedule model for the MVP.",
        ReasonCode.NO_BEHAVIOUR_MODEL: "HiSim has no occupant behaviour model to change.",
        ReasonCode.SIMULATION_FAILED: "The request was accepted and the calculation then failed.",
        ReasonCode.RESULT_DERIVATION_FAILED: "The simulation finished and its result payload could not be "
                                             "derived.",
    }

    GROUP_BY_CODE: ClassVar[Dict[ReasonCode, ReasonGroup]] = {
        ReasonCode.UNKNOWN_MEASURE: ReasonGroup.VALIDATION,
        ReasonCode.UNKNOWN_OPTION: ReasonGroup.VALIDATION,
        ReasonCode.UNKNOWN_OPTION_VALUE: ReasonGroup.VALIDATION,
        ReasonCode.OPTION_TYPE_MISMATCH: ReasonGroup.VALIDATION,
        ReasonCode.MISSING_OPTION: ReasonGroup.VALIDATION,
        ReasonCode.UNKNOWN_KEY_IN_MEASURE: ReasonGroup.VALIDATION,
        ReasonCode.STAGE_ZERO_MEASURE: ReasonGroup.VALIDATION,
        ReasonCode.DUPLICATE_MEASURE: ReasonGroup.VALIDATION,
        ReasonCode.SCHEMA_VIOLATION: ReasonGroup.VALIDATION,
        ReasonCode.MATERIAL_NOT_IN_DATABASE: ReasonGroup.REFUSAL,
        ReasonCode.UNDEFINED_MEASURE_BUILDUP: ReasonGroup.REFUSAL,
        ReasonCode.UNSUPPORTED_SYSTEM: ReasonGroup.REFUSAL,
        ReasonCode.NO_BASE_FILE_FOR_COMBINATION: ReasonGroup.REFUSAL,
        ReasonCode.CONTRADICTORY_MEASURES: ReasonGroup.REFUSAL,
        ReasonCode.MEASURE_DOES_NOT_FIT_BUILDING: ReasonGroup.REFUSAL,
        ReasonCode.CONFLICTING_WRITES: ReasonGroup.REFUSAL,
        ReasonCode.TOO_MANY_VEHICLES: ReasonGroup.REFUSAL,
        ReasonCode.NO_TABULA_ARCHETYPE: ReasonGroup.REFUSAL,
        ReasonCode.NO_WEATHER_FOR_COUNTRY: ReasonGroup.REFUSAL,
        ReasonCode.NO_APPLIANCE_SUBMODEL: ReasonGroup.NO_EFFECT,
        ReasonCode.NO_INFILTRATION_MODEL: ReasonGroup.NO_EFFECT,
        ReasonCode.NO_SHADING_MODEL: ReasonGroup.NO_EFFECT,
        ReasonCode.NO_VENTILATION_MODEL: ReasonGroup.NO_EFFECT,
        ReasonCode.NO_CONTROL_SCHEDULE_MODEL: ReasonGroup.NO_EFFECT,
        ReasonCode.NO_BEHAVIOUR_MODEL: ReasonGroup.NO_EFFECT,
        ReasonCode.SIMULATION_FAILED: ReasonGroup.CRASH,
        ReasonCode.RESULT_DERIVATION_FAILED: ReasonGroup.CRASH,
    }


@dataclass(frozen=True)
class RefusalDetail:
    """One reason a well-formed request cannot be simulated, as it appears in ``errors.json``.

    A refusal is not an exception on its own: the application collects every refusal a package
    produces so the caller learns all of them at once instead of one per round trip. Only when the
    collection is non-empty does the application raise :class:`RefusalError` carrying it.

    Args:
        reason: The reason code, from the refusal group of :class:`ReasonCode`.
        path: The JSON path of the offending input, e.g. ``package.measures[2].options.material``.
        detail: One sentence naming the concrete value and what is missing.
        measure_id: The catalogue measure this refusal came from, or ``""`` when it came from the
            combination of several measures rather than one.
    """

    reason: ReasonCode
    path: str
    detail: str
    measure_id: str = ""

    def to_dict(self) -> Dict[str, str]:
        """Return the refusal as the JSON-ready dictionary ``errors.json`` carries."""
        return {
            "reason": self.reason.value,
            "description": self.reason.describe(),
            "path": self.path,
            "detail": self.detail,
            "measure_id": self.measure_id,
        }


class ValidationError(Exception):
    """Raised when the request is malformed and the caller has to change it.

    Example: a package entry carrying ``"stage": 0`` raises
    ``ValidationError(ReasonCode.STAGE_ZERO_MEASURE, "package.measures[0].stage", ...)``.

    Args:
        reason: A reason code from the validation group.
        path: The JSON path of the offending value.
        detail: One sentence naming what is wrong.
    """

    def __init__(self, reason: ReasonCode, path: str, detail: str) -> None:
        """Store the three fields and build the message ``str(error)`` shows."""
        super().__init__(f"{reason.value} at {path}: {detail}")
        self.reason = reason
        self.path = path
        self.detail = detail

    def to_dict(self) -> Dict[str, str]:
        """Return the error as the JSON-ready dictionary ``errors.json`` carries."""
        return {
            "reason": self.reason.value,
            "description": self.reason.describe(),
            "path": self.path,
            "detail": self.detail,
        }


class RefusalError(Exception):
    """Raised when a well-formed package cannot be simulated, carrying every refusal at once.

    The application collects refusals from the registry, from the effect resolver and from the
    exclusivity and fit checks, then raises this once. The caller writes
    :attr:`refusals` into ``errors.json``, so a package with three problems is reported three
    times rather than fixed three times.

    Args:
        refusals: Every refusal the package produced; must not be empty.
    """

    #: How many refusals the message lists before abbreviating, so a long list stays readable.
    MESSAGE_LIMIT: ClassVar[int] = 3

    def __init__(self, refusals: Sequence[RefusalDetail]) -> None:
        """Store the refusals and build a message naming the first :attr:`MESSAGE_LIMIT` of them."""
        if not refusals:
            raise ValueError("RefusalError needs at least one refusal.")
        listed = ", ".join(f"{item.reason.value} at {item.path}" for item in refusals[: self.MESSAGE_LIMIT])
        suffix = "" if len(refusals) <= self.MESSAGE_LIMIT else f" (+{len(refusals) - self.MESSAGE_LIMIT} more)"
        super().__init__(f"package refused: {listed}{suffix}")
        self.refusals: Tuple[RefusalDetail, ...] = tuple(refusals)

    @property
    def reason(self) -> ReasonCode:
        """Return the reason code of the first refusal, for callers that only log one."""
        return self.refusals[0].reason

    @property
    def path(self) -> str:
        """Return the JSON path of the first refusal, for callers that only log one."""
        return self.refusals[0].path

    @property
    def detail(self) -> str:
        """Return the detail sentence of the first refusal, for callers that only log one."""
        return self.refusals[0].detail

    def to_list(self) -> Tuple[Dict[str, str], ...]:
        """Return every refusal as JSON-ready dictionaries, in the order they were found."""
        return tuple(item.to_dict() for item in self.refusals)


class ResultDerivationError(Exception):
    """Raised when the simulation finished and its result payload could not be assembled.

    It is a crash and not a refusal: the request was accepted, the run happened, and its records,
    its report and its raw results are on disk -- what failed is the step that turns them into the
    contract's ``kpis`` and ``costs`` blocks. Telling it apart from a failed *simulation* matters
    to whoever gets paged, because the two have different causes and different fixes: a simulation
    that dies is a physics or a data problem in the run, while a payload that cannot be derived is
    a problem in the translation layer over a run that worked.

    Args:
        detail: One sentence naming what could not be derived and why.
    """

    def __init__(self, detail: str) -> None:
        """Store the detail and build the message ``str(error)`` shows."""
        super().__init__(f"{ReasonCode.RESULT_DERIVATION_FAILED.value}: {detail}")
        self.reason = ReasonCode.RESULT_DERIVATION_FAILED
        self.detail = detail
