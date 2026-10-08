"""The staged command's parameter vocabulary: the `parameters` block of `economics_result.json`, read and written.

`StagedParameters.from_mapping` reads a `--parameters` file in the document's own key names (`horizon_years`,
`perspective_id`, `financing`, `subsidy_mode`, ...) onto the engine's `EconomicParameters`, and
`StagedParameters.to_document_block` writes the block back in the same shape, so a document's block fed back in gives
the same run. There is no default country: the country is the one the stages were priced under. Faults are collected as
`ParameterProblem` rows rather than raised one at a time; the CLI writes them to `problems.json` with exit 2.

Example::

    parsed = StagedParameters.from_mapping({"horizon_years": 20, "subsidy_mode": "full"}, stored=stage_parameters)
    parameters, perspective = parsed.applied_to(bundle_perspective)
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.financing import FinancingPlan
from hisim.economics.parameters import EconomicParameters, StatedEnergyPrice
from hisim.economics.perspectives import Perspective, SubsidyMode, SubsidyModeKind
from hisim.economics.results import RateOrigin
from hisim.economics.uncertainty import UncertainValue


class ParameterKeys:
    """Every key name the staged parameter block uses, on input and on output.

    One class serves the reader and the writer, so a rename changes what `--parameters` accepts and what
    `economics_result.json` publishes together. The mapping onto the engine record's field names lives in
    `StagedParameters.from_mapping`.

    Example::

        ParameterKeys.HORIZON_YEARS in ParameterKeys.ACCEPTED
    """

    #: Observation period in years; maps to ``EconomicParameters.observation_period_in_years``.
    HORIZON_YEARS: ClassVar[str] = "horizon_years"

    #: Nominal calculation interest rate; maps to ``EconomicParameters.interest_rate``.
    INTEREST_RATE: ClassVar[str] = "interest_rate"

    #: ISO-2 country code deciding the price data and the subsidy catalogue.
    COUNTRY: ClassVar[str] = "country"

    #: Price basis year of the database lookups. A null says nothing: the stages' year when they
    #: state one, else the plan's start year (:attr:`PLAN_START_YEAR`) clamped to the earliest year
    #: the country's device data covers (see :meth:`StagedParameters._read_price_basis_year` and
    #: :func:`~hisim.economics.evaluator.effective_price_basis_year`).
    PRICE_BASIS_YEAR: ClassVar[str] = "price_basis_year"

    #: The calendar year the plan starts in: year 0 of the horizon. Every
    #: calendar year the document publishes is this plus the relative year, and none is published
    #: without it. An assumption of the reader, not a fact of the stages.
    PLAN_START_YEAR: ClassVar[str] = "plan_start_year"

    #: Id of the perspective the plan is priced under.
    PERSPECTIVE_ID: ClassVar[str] = "perspective_id"

    #: Whether the subsidy catalogue is applied at all: ``full`` or ``none``.
    SUBSIDY_MODE: ClassVar[str] = "subsidy_mode"

    #: Cash or one annuity loan per buying stage.
    FINANCING: ClassVar[str] = "financing"

    #: The reader's quoted prices, one per measure of a stage: a list of
    #: ``{"stage", "measure_id", "amount_in_euro", "source"}``.
    INVESTMENT_OVERRIDES: ClassVar[str] = "investment_overrides"

    #: The stage index a quote is for, inside one :attr:`INVESTMENT_OVERRIDES` entry.
    OVERRIDE_STAGE: ClassVar[str] = "stage"

    #: The catalogue measure a quote is for.
    OVERRIDE_MEASURE_ID: ClassVar[str] = "measure_id"

    #: The quoted total in euro, installed: a positive number, exact.
    OVERRIDE_AMOUNT: ClassVar[str] = "amount_in_euro"

    #: Where the quote comes from, as the reader states it; echoed on the priced subject.
    OVERRIDE_SOURCE: ClassVar[str] = "source"

    #: The escalation rates, as a nested block.
    ESCALATION: ClassVar[str] = "escalation"

    #: The year-1 price terms stated per carrier, as a nested block.
    ENERGY_PRICES: ClassVar[str] = "energy_prices"

    #: Where each echoed energy rate and price came from. Written by a document, accepted and
    #: ignored on input: it describes the run, it is not an assumption a caller may state.
    ORIGINS: ClassVar[str] = "origins"

    #: Under :attr:`ORIGINS`: the years every amount of the document was escalated between, written
    #: only when ``plan_start_year`` differs from ``price_basis_year``.
    PRICE_LEVEL: ClassVar[str] = "price_level"

    #: Under :attr:`PRICE_LEVEL`: the year the prices were read at, the price basis year.
    PRICE_LEVEL_FROM: ClassVar[str] = "from_year"

    #: Under :attr:`PRICE_LEVEL`: the year whose money the amounts are in, the plan's start year.
    PRICE_LEVEL_TO: ClassVar[str] = "to_year"

    #: Which catalogue produced a document. Accepted and ignored on input (see the class docstring
    #: of :class:`StagedParameters`): the catalogue comes from the shipped directory or from
    #: ``--subsidy-catalog``.
    SUBSIDY_CATALOG: ClassVar[str] = "subsidy_catalog"

    #: The year of the weather the stages were simulated with (the ``simulation_year`` of their
    #: ``economic_inputs.json``). Accepted and ignored on input: it is a fact of the stages, not an
    #: assumption a caller may state. It dates nothing in the document; the plan's calendar is
    #: :attr:`PLAN_START_YEAR`.
    WEATHER_YEAR: ClassVar[str] = "weather_year"

    #: General price escalation, inside :attr:`ESCALATION`.
    ESCALATION_GENERAL: ClassVar[str] = "general"

    #: Investment price escalation, inside :attr:`ESCALATION`.
    ESCALATION_INVESTMENT: ClassVar[str] = "investment"

    #: Feed-in remuneration escalation, inside :attr:`ESCALATION`.
    ESCALATION_FEED_IN: ClassVar[str] = "feed_in"

    #: Per-carrier energy price escalation, inside :attr:`ESCALATION`.
    ESCALATION_ENERGY: ClassVar[str] = "energy"

    #: The all-in year-1 working price in EUR/kWh, inside one carrier of :attr:`ENERGY_PRICES`;
    #: for ``ELECTRICITY_FEED_IN`` the feed-in remuneration per kWh sold.
    PRICE_WORKING: ClassVar[str] = StatedEnergyPrice.WORKING_PRICE_KEY

    #: The fixed annual charge in EUR/a, inside one carrier of :attr:`ENERGY_PRICES`.
    PRICE_STANDING: ClassVar[str] = StatedEnergyPrice.STANDING_CHARGE_KEY

    #: The three keys of a price band, in the document's spelling.
    BAND_KEYS: ClassVar[Tuple[str, ...]] = ("min", "best", "max")

    #: Cash or loan, inside :attr:`FINANCING`.
    FINANCING_KIND: ClassVar[str] = "kind"

    #: Share of the year-0 net investment that is borrowed, inside :attr:`FINANCING`.
    FINANCING_SHARE: ClassVar[str] = "financed_share"

    #: Nominal loan interest rate, inside :attr:`FINANCING`.
    FINANCING_INTEREST_RATE: ClassVar[str] = "nominal_interest_rate"

    #: Loan term in years, inside :attr:`FINANCING`.
    FINANCING_TERM: ClassVar[str] = "term_in_years"

    #: Every key the top-level block accepts, in the order the document writes them.
    ACCEPTED: ClassVar[Tuple[str, ...]] = (
        HORIZON_YEARS,
        INTEREST_RATE,
        COUNTRY,
        PRICE_BASIS_YEAR,
        PLAN_START_YEAR,
        WEATHER_YEAR,
        PERSPECTIVE_ID,
        SUBSIDY_MODE,
        FINANCING,
        ESCALATION,
        ENERGY_PRICES,
        INVESTMENT_OVERRIDES,
        SUBSIDY_CATALOG,
        ORIGINS,
    )

    #: Former key names of the top-level block, with the sentence that says what became of each.
    #: They are refused as ``parameters.unknown_key`` with that sentence as the message.
    RENAMED: ClassVar[Mapping[str, str]] = {
        "simulation_year": (
            "'simulation_year' was renamed `weather_year` in schema version 5 (renovisorissues #57): "
            "it is the year of the stages' weather, accepted and ignored on input. The calendar "
            "years of the rows are dated by `plan_start_year`."
        ),
    }
    #: Every key one :attr:`INVESTMENT_OVERRIDES` entry has, all of them required.
    ACCEPTED_OVERRIDE: ClassVar[Tuple[str, ...]] = (
        OVERRIDE_STAGE,
        OVERRIDE_MEASURE_ID,
        OVERRIDE_AMOUNT,
        OVERRIDE_SOURCE,
    )

    #: Every key the :attr:`ESCALATION` block accepts.
    ACCEPTED_ESCALATION: ClassVar[Tuple[str, ...]] = (
        ESCALATION_GENERAL,
        ESCALATION_INVESTMENT,
        ESCALATION_FEED_IN,
        ESCALATION_ENERGY,
    )

    #: Every key one carrier of the :attr:`ENERGY_PRICES` block accepts.
    ACCEPTED_ENERGY_PRICE: ClassVar[Tuple[str, ...]] = (PRICE_WORKING, PRICE_STANDING)

    #: Every key the :attr:`FINANCING` block accepts; the three loan fields are meaningless for a
    #: cash purchase and are refused there.
    ACCEPTED_FINANCING: ClassVar[Tuple[str, ...]] = (
        FINANCING_KIND,
        FINANCING_SHARE,
        FINANCING_INTEREST_RATE,
        FINANCING_TERM,
    )

    #: The dotted path the top-level block is reported under in ``problems.json``.
    ROOT_PATH: ClassVar[str] = "parameters"


class StatedPriceBounds:
    """The ranges a stated energy price must lie in: typo guards, not market limits.

    Each bound is wide enough for any household bill in the shipped countries and narrow enough to catch a price in
    cents instead of euros, a price per MWh, or a monthly charge typed as annual. A value outside is refused as
    `parameters.energy_prices.<CARRIER>.<field>.invalid`.

    Example::

        StatedPriceBounds.WORKING_PRICE_MAXIMUM_IN_EURO_PER_KWH == 2.0
    """

    #: A working price is strictly positive: a free purchase is not a price anyone pays.
    WORKING_PRICE_ABOVE_IN_EURO_PER_KWH: ClassVar[float] = 0.0

    #: And at most 2 EUR/kWh, several times the dearest retail price in the shipped data.
    WORKING_PRICE_MAXIMUM_IN_EURO_PER_KWH: ClassVar[float] = 2.0

    #: A feed-in rate may be zero (export unpaid) but not negative.
    FEED_IN_MINIMUM_IN_EURO_PER_KWH: ClassVar[float] = 0.0

    #: And at most 1 EUR/kWh.
    FEED_IN_MAXIMUM_IN_EURO_PER_KWH: ClassVar[float] = 1.0

    #: A standing charge may be zero but not negative.
    STANDING_CHARGE_MINIMUM_IN_EURO_PER_YEAR: ClassVar[float] = 0.0

    #: And at most 5,000 EUR a year.
    STANDING_CHARGE_MAXIMUM_IN_EURO_PER_YEAR: ClassVar[float] = 5000.0


class PlanYearBounds:
    """The range `plan_start_year` must lie in: a typo guard, not a statement about plans.

    It catches a two-digit year, a year with a digit too many and a relative year typed where a calendar year belongs.
    A value outside is refused as `parameters.plan_start_year.invalid`.

    Example::

        PlanYearBounds.MINIMUM <= 2026 <= PlanYearBounds.MAXIMUM
    """

    #: The earliest calendar year a plan may start in.
    MINIMUM: ClassVar[int] = 1900

    #: The latest calendar year a plan may start in.
    MAXIMUM: ClassVar[int] = 2100


class EchoOrigin(enum.Enum):
    """Where an echoed energy rate or price came from, as `parameters.origins` spells it.

    A per-carrier escalation rate is `stated` (the plan's assumptions name it), `country_default` (the country's
    `escalation_defaults_<COUNTRY>.json`) or `general` (the general escalation rate). A price field is `stated` or
    `database` (the price entry at the price basis year). `plan_start_year` appears only under
    `origins.price_basis_year`, when neither the stages nor the parameters stated a price basis year and the plan's
    start year supplied it.

    Example::

        EchoOrigin.of_rate(RateOrigin.COUNTRY_DEFAULTS) is EchoOrigin.COUNTRY_DEFAULT
    """

    STATED = "stated"
    COUNTRY_DEFAULT = "country_default"
    GENERAL = "general"
    DATABASE = "database"
    PLAN_START_YEAR = "plan_start_year"

    @classmethod
    def of_rate(cls, origin: RateOrigin) -> "EchoOrigin":
        """Return the echo spelling of one step of the escalation fallback chain.

        Args:
            origin: The step that produced the rate.

        Returns:
            `STATED` for a configured rate, `COUNTRY_DEFAULT` for the defaults file, `GENERAL` for the general-rate
                fallback.
        """
        return {
            RateOrigin.CONFIGURATION: cls.STATED,
            RateOrigin.COUNTRY_DEFAULTS: cls.COUNTRY_DEFAULT,
            RateOrigin.GENERAL_FALLBACK: cls.GENERAL,
        }[origin]


@dataclass(frozen=True)
class EchoedPrice:
    """The year-1 price terms one carrier was priced at, as the document echoes them.

    Args:
        working_price_in_euro_per_kwh: The all-in year-1 working price (carbon included), or the feed-in rate for
            `ELECTRICITY_FEED_IN`; None when unknown.
        working_price_origin: Where the working price came from.
        standing_charge_in_euro_per_year: The fixed annual charge; None for the feed-in carrier and when unknown.
        standing_charge_origin: Where the standing charge came from.
    """

    working_price_in_euro_per_kwh: Optional[UncertainValue] = None
    working_price_origin: Optional[EchoOrigin] = None
    standing_charge_in_euro_per_year: Optional[UncertainValue] = None
    standing_charge_origin: Optional[EchoOrigin] = None

    def fields(self) -> List[Tuple[str, UncertainValue, EchoOrigin]]:
        """The known fields, as ``(key, value, origin)`` in the order the document writes them."""
        known: List[Tuple[str, UncertainValue, EchoOrigin]] = []
        for key, value, origin in (
            (ParameterKeys.PRICE_WORKING, self.working_price_in_euro_per_kwh, self.working_price_origin),
            (ParameterKeys.PRICE_STANDING, self.standing_charge_in_euro_per_year, self.standing_charge_origin),
        ):
            if value is not None and origin is not None:
                known.append((key, value, origin))
        return known


@dataclass(frozen=True)
class EnergyEcho:
    """The per-carrier escalation rates and year-1 prices a plan was priced with.

    This is what `parameters.escalation.energy`, `parameters.energy_prices` and `parameters.origins` publish: the
    values actually used for every carrier a stage bills or the plan names, and where each came from. The staged
    evaluator resolves it against the cost database; `stated_only` is the fallback for a caller with parameters but no
    priced plan.

    Args:
        rates: Carrier -> `(nominal annual rate, origin)`.
        prices: Carrier -> the echoed price terms.
    """

    rates: Mapping[EnergyCarrier, Tuple[float, EchoOrigin]]
    prices: Mapping[EnergyCarrier, EchoedPrice]

    @classmethod
    def stated_only(cls, parameters: EconomicParameters) -> "EnergyEcho":
        """Return the echo of what the parameters state, without a database to resolve the rest.

        Args:
            parameters: The assumptions.

        Returns:
            The stated per-carrier rates and price fields, each with origin `stated`.
        """
        prices: Dict[EnergyCarrier, EchoedPrice] = {}
        for carrier, stated in parameters.energy_prices.items():
            working = stated.working_price_in_euro_per_kwh
            standing = stated.standing_charge_in_euro_per_year
            prices[carrier] = EchoedPrice(
                working_price_in_euro_per_kwh=working,
                working_price_origin=EchoOrigin.STATED if working is not None else None,
                standing_charge_in_euro_per_year=standing,
                standing_charge_origin=EchoOrigin.STATED if standing is not None else None,
            )
        return cls(
            rates={
                carrier: (rate, EchoOrigin.STATED)
                for carrier, rate in parameters.energy_price_escalation_rates.items()
            },
            prices=prices,
        )


class SubsidyModeName(enum.Enum):
    """How a parameter file spells "apply the subsidy catalogue" (`full`) and "do not" (`none`).

    It is narrower than the engine's `SubsidyModeKind`, which also has `ONLY` and `EXCLUDE`: a RenoVisor caller chooses
    whether the catalogue runs, not which schemes it contains.

    Example::

        SubsidyModeName("full") is SubsidyModeName.FULL
    """

    FULL = "full"
    NONE = "none"


class FinancingKindName(enum.Enum):
    """How a parameter file spells "paid from cash" and "paid from one annuity loan".

    `CASH` gives the perspective no `FinancingPlan` at all. `LOAN` means one annuity loan per buying stage, with fields
    the file does not state at the engine's defaults.

    Example::

        FinancingKindName("loan") is FinancingKindName.LOAN
    """

    CASH = "cash"
    LOAN = "loan"


class ParameterProblemCodes:
    """The code templates a refused parameter block reports, as format strings taking the dotted `path`.

    One template serves every block: `"parameters.horizon_years.invalid"` and `"parameters.financing.kind.invalid"`
    both come from `INVALID`. Keeping them in one class shows the set a backend branches on.

    Example::

        ParameterProblemCodes.INVALID.format(path="parameters.interest_rate")
    """

    #: A key the block does not accept. The path is the block's, not the key's, and the problem's
    #: ``accepted`` lists the keys that would have been accepted.
    UNKNOWN_KEY: ClassVar[str] = "{path}.unknown_key"

    #: A value of the wrong type or outside its range.
    INVALID: ClassVar[str] = "{path}.invalid"

    #: A value that contradicts what the stages were priced under.
    MISMATCH: ClassVar[str] = "{path}.mismatch"

    #: A value neither the file nor the stages state, and that has no default.
    MISSING: ClassVar[str] = "{path}.missing"

    #: The ``--parameters`` file itself: not there, not JSON, or not a JSON object.
    UNREADABLE: ClassVar[str] = "{path}.unreadable"

    #: A value naming something the plan does not have: a quote's stage index.
    UNKNOWN: ClassVar[str] = "{path}.unknown"

    #: A quote for a measure the stage does not carry out -- not among its measures, or carried
    #: over from an earlier stage so that the stage buys nothing for it.
    NOT_IN_STAGE: ClassVar[str] = "{path}.not_in_stage"

    #: A quote for a measure that costs nothing to carry out (a setting, not a purchase).
    COSTLESS: ClassVar[str] = "{path}.costless"

    #: A second quote for the same measure of the same stage.
    DUPLICATE: ClassVar[str] = "{path}.duplicate"


@dataclass(frozen=True)
class ParameterProblem:
    """One reason a `--parameters` block was refused, at one path.

    It is one row of `problems.json`, in the same shape as the translator's `hisim.renovisor.request.Problem`, so a
    backend reads both refusals alike; the codes are the `parameters.*` set.

    Args:
        path: The dotted path of the offending value, e.g. `parameters.financing.kind`.
        code: A filled-in `ParameterProblemCodes` template.
        message: One readable sentence naming the value and what is wrong with it.
        accepted: The values or keys that would have been accepted, or None when a list would not help (a number out of
            range, a mismatch against the stages).
    """

    path: str
    code: str
    message: str
    accepted: Optional[Tuple[Any, ...]] = None

    def to_json(self) -> Dict[str, Any]:
        """Return the problem as one row of `problems.json`.

        Returns:
            `{"path": ..., "code": ..., "message": ...}`, plus `"accepted"` when the problem names accepted values.
        """
        row: Dict[str, Any] = {"path": self.path, "code": self.code, "message": self.message}
        if self.accepted is not None:
            row["accepted"] = list(self.accepted)
        return row


class ParameterReader:
    """Reads one parameter block key by key, collecting faults instead of raising on the first.

    Every accessor returns None for a key that is absent or wrong and appends a `ParameterProblem` when it is wrong;
    the caller checks the problem list once at the end, so a file with five faults is reported in one run. Booleans are
    refused wherever a number is expected (`True` is an `int` in Python).

    Example::

        problems: List[ParameterProblem] = []
        reader = ParameterReader({"horizon_years": 20}, ParameterKeys.ROOT_PATH, ParameterKeys.ACCEPTED, problems)
        reader.refuse_unknown_keys()
        horizon = reader.integer(ParameterKeys.HORIZON_YEARS, minimum=1)

    Args:
        raw: The block as parsed from JSON.
        path: The dotted path of the block, e.g. `parameters` or `parameters.financing`.
        accepted: The keys this block accepts.
        problems: The list every fault is appended to, shared by all blocks of one file.
        noun: What one key of this block is, for the unknown-key message (e.g. "energy carrier").
        renamed: Former keys and the message saying what became of each; they are refused with that message.
    """

    def __init__(
        self,
        raw: Mapping[str, Any],
        path: str,
        accepted: Sequence[str],
        problems: List[ParameterProblem],
        noun: str = "parameter of a staged plan",
        renamed: Optional[Mapping[str, str]] = None,
    ) -> None:
        """Store the block, its path, its accepted keys, the shared problem list and the noun."""
        self.raw = raw
        self.path = path
        self.accepted = tuple(accepted)
        self.problems = problems
        self.noun = noun
        self.renamed: Mapping[str, str] = renamed if renamed is not None else {}

    def path_of(self, key: str) -> str:
        """Return the dotted path one key of this block is reported under.

        Args:
            key: The key name.

        Returns:
            `"<block path>.<key>"`.
        """
        return f"{self.path}.{key}"

    def refuse(
        self,
        key: str,
        code: str,
        message: str,
        accepted: Optional[Tuple[Any, ...]] = None,
        code_path: Optional[str] = None,
    ) -> None:
        """Append one problem about one key of this block.

        Args:
            key: The offending key, or the empty string for the block itself.
            code: A `ParameterProblemCodes` template, still holding `{path}`.
            message: The sentence the caller reads.
            accepted: The values that would have been accepted, if listing them helps.
            code_path: The path the code is built from when it differs from the reported path; an unknown key gets the
                block's code `parameters.unknown_key` while the row points at the key.
        """
        path = self.path_of(key) if key else self.path
        self.problems.append(
            ParameterProblem(
                path=path,
                code=code.format(path=code_path if code_path is not None else path),
                message=message,
                accepted=accepted,
            )
        )

    def refuse_unknown_keys(self) -> None:
        """Refuse every key of this block that is not in its accepted set, one problem each.

        An unknown key is usually a typo; ignoring it would price the run with a value the author believed they had
        overridden.
        """
        for key in sorted(set(self.raw) - set(self.accepted)):
            self.refuse(
                key,
                ParameterProblemCodes.UNKNOWN_KEY,
                self.renamed.get(key, f"{key!r} is no {self.noun}."),
                accepted=self.accepted,
                code_path=self.path,
            )

    def has(self, key: str) -> bool:
        """Return whether the block states the key at all.

        Args:
            key: The key name.

        Returns:
            True when the key is present, even with a null or wrong value.
        """
        return key in self.raw

    def integer(self, key: str, minimum: Optional[int] = None, maximum: Optional[int] = None) -> Optional[int]:
        """One integer value, or None when the key is absent or its value is refused.

        Args:
            key: The key name.
            minimum: The smallest accepted value, or None for no lower bound.
            maximum: The largest accepted value, or None for no upper bound.

        Returns:
            The integer, or None.
        """
        if key not in self.raw:
            return None
        value = self.raw[key]
        if isinstance(value, bool) or not isinstance(value, int):
            self.refuse(key, ParameterProblemCodes.INVALID, f"{value!r} is not a whole number.")
            return None
        if minimum is not None and value < minimum:
            self.refuse(
                key, ParameterProblemCodes.INVALID, f"{value!r} is below the smallest accepted value {minimum}."
            )
            return None
        if maximum is not None and value > maximum:
            self.refuse(
                key, ParameterProblemCodes.INVALID, f"{value!r} is above the largest accepted value {maximum}."
            )
            return None
        return value

    def number(
        self,
        key: str,
        above: Optional[float] = None,
        at_least: Optional[float] = None,
        at_most: Optional[float] = None,
    ) -> Optional[float]:
        """One number, or None when the key is absent or its value is refused.

        Args:
            key: The key name.
            above: An exclusive lower bound, or None for no exclusive lower bound.
            at_least: An inclusive lower bound, or None for no inclusive lower bound.
            at_most: An inclusive upper bound, or None for no upper bound.

        Returns:
            The number as a float, or None.
        """
        if key not in self.raw:
            return None
        value = self.raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.refuse(key, ParameterProblemCodes.INVALID, f"{value!r} is not a number.")
            return None
        if above is not None and value <= above:
            self.refuse(key, ParameterProblemCodes.INVALID, f"{value!r} is not above {above}.")
            return None
        if at_least is not None and value < at_least:
            self.refuse(
                key, ParameterProblemCodes.INVALID, f"{value!r} is below the smallest accepted value {at_least}."
            )
            return None
        if at_most is not None and value > at_most:
            self.refuse(key, ParameterProblemCodes.INVALID, f"{value!r} is above the largest accepted value {at_most}.")
            return None
        return float(value)

    def band(
        self,
        key: str,
        above: Optional[float] = None,
        at_least: Optional[float] = None,
        at_most: Optional[float] = None,
    ) -> Optional[UncertainValue]:
        """Read one amount stated as a number or as a band `{min, best, max}`; None when absent or refused.

        A band is the document's own spelling of an amount (minimum, best estimate, maximum), so an echoed price reads
        back unchanged. Every slot is held to the same bounds as a bare number, and the slots must be ordered.

        Args:
            key: The key name.
            above: An exclusive lower bound, or None.
            at_least: An inclusive lower bound, or None.
            at_most: An inclusive upper bound, or None.

        Returns:
            The amount (exact for a bare number), or None.
        """
        if key not in self.raw:
            return None
        value = self.raw[key]
        if isinstance(value, Mapping):
            if set(value) != set(ParameterKeys.BAND_KEYS):
                self.refuse(
                    key,
                    ParameterProblemCodes.INVALID,
                    f"{value!r} is not a band: a band states exactly min, best and max.",
                    accepted=ParameterKeys.BAND_KEYS,
                )
                return None
            slots = ParameterReader(value, self.path_of(key), ParameterKeys.BAND_KEYS, [])
            numbers = [slots.number(slot, above, at_least, at_most) for slot in ParameterKeys.BAND_KEYS]
            if slots.problems:
                self.refuse(key, ParameterProblemCodes.INVALID, " ".join(
                    f"{problem.path.rsplit('.', 1)[-1]}: {problem.message}" for problem in slots.problems
                ))
                return None
            low, best, high = (float(number) for number in numbers if number is not None)
            if not low <= best <= high:
                self.refuse(key, ParameterProblemCodes.INVALID, f"{value!r} is not ordered min <= best <= max.")
                return None
            return UncertainValue(best_estimate=best, minimum=low, maximum=high)
        number = self.number(key, above, at_least, at_most)
        return UncertainValue.exact(number) if number is not None else None

    def text(self, key: str) -> Optional[str]:
        """One string value, or None when the key is absent or its value is refused.

        Args:
            key: The key name.

        Returns:
            The string, or None.
        """
        if key not in self.raw:
            return None
        value = self.raw[key]
        if not isinstance(value, str):
            self.refuse(key, ParameterProblemCodes.INVALID, f"{value!r} is not a string.")
            return None
        return value

    def block(self, key: str) -> Optional[Mapping[str, Any]]:
        """Read one nested object, or None when the key is absent or its value is refused.

        A `null` is refused rather than read as the default; leaving the key out is how a file says "no block".

        Args:
            key: The key name.

        Returns:
            The nested mapping, or None.
        """
        if key not in self.raw:
            return None
        value = self.raw[key]
        if not isinstance(value, Mapping):
            self.refuse(
                key,
                ParameterProblemCodes.INVALID,
                f"{value!r} is not an object. Leave the key out to keep what the stages were priced under.",
            )
            return None
        return value


@dataclass(frozen=True)
class StatedQuote:
    """One entry of `investment_overrides`: a reader's quoted price for one measure of one stage.

    `StagedParameters.from_mapping` checks its structure; `StagedParameters.check_quotes` checks it against the stages,
    after which the staged command resolves the measure to the subject it prices.

    Args:
        stage: The stage index.
        measure_id: The catalogue measure.
        amount_in_euro: The quoted installed total in euro, positive and exact.
        source: Where the quote comes from.
        position: The entry's index in the list, for problem paths.
    """

    stage: int
    measure_id: str
    amount_in_euro: float
    source: str
    position: int = 0

    def to_json(self) -> Dict[str, Any]:
        """Return the entry as the document echoes it, which is also how the file states it."""
        return {
            ParameterKeys.OVERRIDE_STAGE: self.stage,
            ParameterKeys.OVERRIDE_MEASURE_ID: self.measure_id,
            ParameterKeys.OVERRIDE_AMOUNT: self.amount_in_euro,
            ParameterKeys.OVERRIDE_SOURCE: self.source,
        }


@dataclass(frozen=True)
class StagedParameters:
    """A parsed `--parameters` file of `python -m hisim.economics staged`.

    It holds the engine parameter record to price with, the perspective the caller named, the financing and subsidy
    overrides, and every fault found. `parameters` is None exactly when `problems` is non-empty; the CLI then writes
    `problems.json` and exits 2. Three accepted keys are ignored when read: `weather_year` (a fact of the stages),
    `subsidy_catalog` (the catalogue actually used comes from the shipped directory or `--subsidy-catalog`) and
    `origins`. They are accepted so a document's own `parameters` block is a legal input file.

    Example::

        parsed = StagedParameters.from_mapping({"horizon_years": 20}, stored=stage_parameters)
        parameters, priced_under = parsed.applied_to(perspective)

    Args:
        parameters: The engine record to price with, or None when the file was refused.
        perspective_id: The perspective the file named, or None.
        financing: The financing plan for `loan`; None for `cash` and when the file said nothing (`financing_given`
            tells these apart).
        financing_given: Whether the file stated `financing` at all.
        subsidy_mode: The subsidy mode the file asked for, or None.
        plan_start_year: The calendar year the plan starts in, or None, in which case the document dates nothing.
        investment_overrides: The reader's quotes, structurally checked; empty when none are stated.
        problems: Every fault found, in the order found.
    """

    #: The perspective a RenoVisor plan is priced under when neither the file nor ``--perspective``
    #: names one: existing assets in the register, subsidies applied where a catalogue says so,
    #: cash financing.
    DEFAULT_PERSPECTIVE_ID: ClassVar[str] = "brownfield_net"

    parameters: Optional[EconomicParameters]
    perspective_id: Optional[str] = None
    financing: Optional[FinancingPlan] = None
    financing_given: bool = False
    subsidy_mode: Optional[SubsidyModeName] = None
    plan_start_year: Optional[int] = None
    investment_overrides: Tuple[StatedQuote, ...] = ()
    problems: Tuple[ParameterProblem, ...] = ()

    @classmethod
    def from_mapping(
        cls,
        raw: Any,
        stored: Optional[EconomicParameters],
        stored_country: Optional[str] = None,
        stored_price_basis_year: Optional[int] = None,
    ) -> "StagedParameters":
        """Read one parameter mapping in the document's shape onto the engine's record.

        Every key is optional: what the file does not state comes from what the stages were priced under (`stored`),
        and otherwise from the engine's default, except the country, which has no default. A `country` in the file must
        equal the stages' country or the run is refused naming both; no country anywhere is refused too.
        `price_basis_year` follows the same rule, except that when neither the stages nor the file state one, a stated
        `plan_start_year` supplies it (left unset on the record for `StagedEvaluator.evaluate` to resolve and clamp).

        Example::

            StagedParameters.from_mapping({"country": "IE"}, stored=None).parameters.country == "IE"

        Args:
            raw: The parsed `--parameters` document; anything other than a JSON object is one problem, not an
                exception.
            stored: The parameters the stages were priced under (from the first stage with a stored evaluation), or
                None.
            stored_country: The one country the stages were priced for, resolved by the caller over every stage source;
                when None and `stored` is given, `stored.country` is used.
            stored_price_basis_year: The one price basis year the stages were priced at, resolved the same way.

        Returns:
            The parsed result, carrying either the engine parameters or the problems.
        """
        problems: List[ParameterProblem] = []
        if not isinstance(raw, Mapping):
            problems.append(
                ParameterProblem(
                    path=ParameterKeys.ROOT_PATH,
                    code=ParameterProblemCodes.UNREADABLE.format(path=ParameterKeys.ROOT_PATH),
                    message=f"the parameters must be a JSON object of assumptions, not {type(raw).__name__}.",
                    accepted=ParameterKeys.ACCEPTED,
                )
            )
            return cls(parameters=None, problems=tuple(problems))

        reader = ParameterReader(
            raw, ParameterKeys.ROOT_PATH, ParameterKeys.ACCEPTED, problems, renamed=ParameterKeys.RENAMED
        )
        reader.refuse_unknown_keys()
        overrides: Dict[str, Any] = {}
        cls._read_horizon_and_interest(reader, overrides)
        cls._read_country(reader, cls._stages_country(stored, stored_country), overrides)
        plan_start_year = cls._read_plan_start_year(reader)
        cls._read_price_basis_year(
            reader,
            cls._stages_price_basis_year(stored, stored_price_basis_year),
            overrides,
            plan_start_year,
        )
        cls._read_escalation(reader, problems, overrides)
        cls._read_energy_prices(reader, problems, overrides)
        perspective_id = reader.text(ParameterKeys.PERSPECTIVE_ID)
        subsidy_mode = cls._read_subsidy_mode(reader)
        financing_given, financing = cls._read_financing(reader, problems)
        quotes = cls._read_investment_overrides(reader, problems)

        if problems:
            return cls(
                parameters=None,
                perspective_id=perspective_id,
                financing=financing,
                financing_given=financing_given,
                subsidy_mode=subsidy_mode,
                plan_start_year=plan_start_year,
                investment_overrides=quotes,
                problems=tuple(problems),
            )
        # With no stored parameters the record is built from the overrides alone, so no field
        # default can stand in for a country: `_read_country` has already refused a file that
        # names none, and `country` is therefore in `overrides` on this branch.
        parameters = EconomicParameters(**overrides) if stored is None else replace(stored, **overrides)
        return cls(
            parameters=parameters,
            perspective_id=perspective_id,
            financing=financing,
            financing_given=financing_given,
            subsidy_mode=subsidy_mode,
            plan_start_year=plan_start_year,
            investment_overrides=quotes,
        )

    #: The path a list entry's problem is reported at, and the path its code is built from: the
    #: row names the entry, the code the key, so a backend branches on one code per fault.
    OVERRIDE_ENTRY_PATH: ClassVar[str] = f"{ParameterKeys.ROOT_PATH}.{ParameterKeys.INVESTMENT_OVERRIDES}"

    @classmethod
    def _read_investment_overrides(
        cls, reader: ParameterReader, problems: List[ParameterProblem]
    ) -> Tuple[StatedQuote, ...]:
        """Read `investment_overrides`, the reader's quoted prices.

        A list of entries with exactly the keys `stage` (a whole number from 0), `measure_id` (a string),
        `amount_in_euro` (a number above 0; a quote is exact, so a band is refused) and `source` (a non-empty string).
        A second entry for the same stage and measure is refused as `parameters.investment_overrides.duplicate`. A
        problem's path names the entry (`parameters.investment_overrides[1].amount_in_euro`); its code names the key
        (`parameters.investment_overrides.amount_in_euro.invalid`). Whether the stage and measure exist is checked
        later by `check_quotes`.

        Args:
            reader: The top-level block's reader.
            problems: The shared problem list.

        Returns:
            The entries that read, in file order; empty when the key is absent.
        """
        if not reader.has(ParameterKeys.INVESTMENT_OVERRIDES):
            return ()
        raw = reader.raw[ParameterKeys.INVESTMENT_OVERRIDES]
        if not isinstance(raw, list):
            reader.refuse(
                ParameterKeys.INVESTMENT_OVERRIDES,
                ParameterProblemCodes.INVALID,
                f"{raw!r} is not a list of quotes; each quote is an object with "
                f"{', '.join(ParameterKeys.ACCEPTED_OVERRIDE)}.",
            )
            return ()
        quotes: List[StatedQuote] = []
        seen: Dict[Tuple[int, str], int] = {}
        for position, entry in enumerate(raw):
            entry_path = f"{cls.OVERRIDE_ENTRY_PATH}[{position}]"
            if not isinstance(entry, Mapping):
                problems.append(
                    ParameterProblem(
                        path=entry_path,
                        code=ParameterProblemCodes.INVALID.format(path=cls.OVERRIDE_ENTRY_PATH),
                        message=f"{entry!r} is not a quote; a quote is an object with "
                        f"{', '.join(ParameterKeys.ACCEPTED_OVERRIDE)}.",
                        accepted=ParameterKeys.ACCEPTED_OVERRIDE,
                    )
                )
                continue
            quote = cls._read_quote(entry, entry_path, position, problems)
            if quote is None:
                continue
            key = (quote.stage, quote.measure_id)
            if key in seen:
                problems.append(
                    ParameterProblem(
                        path=entry_path,
                        code=ParameterProblemCodes.DUPLICATE.format(path=cls.OVERRIDE_ENTRY_PATH),
                        message=f"a second quote for {quote.measure_id!r} in stage {quote.stage} "
                        f"(the first is entry {seen[key]}); one measure of one stage has one quote.",
                    )
                )
                continue
            seen[key] = position
            quotes.append(quote)
        return tuple(quotes)

    @classmethod
    def _read_quote(
        cls,
        entry: Mapping[str, Any],
        entry_path: str,
        position: int,
        problems: List[ParameterProblem],
    ) -> Optional[StatedQuote]:
        """Read one `investment_overrides` entry, or None when any of its keys is refused.

        Args:
            entry: The entry as parsed.
            entry_path: Its path, `parameters.investment_overrides[<position>]`.
            position: Its index.
            problems: The shared problem list.

        Returns:
            The quote, or None.
        """
        local: List[ParameterProblem] = []
        fields = ParameterReader(entry, entry_path, ParameterKeys.ACCEPTED_OVERRIDE, local, noun="key of a quote")
        fields.refuse_unknown_keys()
        for key in ParameterKeys.ACCEPTED_OVERRIDE:
            if key not in entry:
                fields.refuse(key, ParameterProblemCodes.MISSING, f"a quote states its {key}.")
        stage = fields.integer(ParameterKeys.OVERRIDE_STAGE, minimum=0)
        measure_id = fields.text(ParameterKeys.OVERRIDE_MEASURE_ID)
        amount = fields.number(ParameterKeys.OVERRIDE_AMOUNT, above=0.0)
        source = fields.text(ParameterKeys.OVERRIDE_SOURCE)
        if source is not None and not source.strip():
            fields.refuse(
                ParameterKeys.OVERRIDE_SOURCE,
                ParameterProblemCodes.INVALID,
                "a quote names where it comes from; the source is echoed beside the price it sets.",
            )
            source = None
        # The codes name the key, not the entry: `parameters.investment_overrides.stage.invalid`.
        # An unknown key is a fault of the entry, the others of one key.
        for problem in local:
            suffix = problem.code.rsplit(".", 1)[-1]
            key = problem.path[len(entry_path) + 1:] if problem.path != entry_path else ""
            if key and suffix != "unknown_key":
                code = f"{cls.OVERRIDE_ENTRY_PATH}.{key}.{suffix}"
            else:
                code = f"{cls.OVERRIDE_ENTRY_PATH}.{suffix}"
            problems.append(replace(problem, code=code))
        if local or stage is None or measure_id is None or amount is None or source is None:
            return None
        return StatedQuote(stage=stage, measure_id=measure_id, amount_in_euro=amount, source=source, position=position)

    @classmethod
    def check_quotes(
        cls,
        quotes: Sequence[StatedQuote],
        stage_measures: Sequence[Sequence[str]],
        costless_measures: Sequence[str],
        catalogue_measures: Sequence[str],
    ) -> List[ParameterProblem]:
        """Check the quotes against the stages of the plan they are for.

        The codes are `parameters.investment_overrides.stage.unknown` for a stage index the plan lacks,
        `...measure_id.invalid` for a measure the catalogue lacks, `...measure_id.not_in_stage` for a measure the stage
        does not carry out, and `...measure_id.costless` for a measure that costs nothing. Every priced catalogue
        measure a stage carries out accepts a quote, an unpriced one included.

        Args:
            quotes: The structurally checked quotes.
            stage_measures: Per stage, in order, the measures it carries out (for a RenoVisor plan the measures new in
                that stage; stage 0's own).
            costless_measures: The measures that cost nothing to carry out.
            catalogue_measures: Every measure id of the catalogue.

        Returns:
            One problem per refused quote, in file order; empty when every quote fits.
        """
        problems: List[ParameterProblem] = []
        for quote in quotes:
            entry_path = f"{cls.OVERRIDE_ENTRY_PATH}[{quote.position}]"
            if quote.stage >= len(stage_measures):
                problems.append(
                    ParameterProblem(
                        path=f"{entry_path}.{ParameterKeys.OVERRIDE_STAGE}",
                        code=ParameterProblemCodes.UNKNOWN.format(
                            path=f"{cls.OVERRIDE_ENTRY_PATH}.{ParameterKeys.OVERRIDE_STAGE}"
                        ),
                        message=f"the plan has {len(stage_measures)} stage(s), 0 to {len(stage_measures) - 1}; "
                        f"there is no stage {quote.stage} to quote for.",
                        accepted=tuple(range(len(stage_measures))),
                    )
                )
                continue
            measure_path = f"{entry_path}.{ParameterKeys.OVERRIDE_MEASURE_ID}"
            code_path = f"{cls.OVERRIDE_ENTRY_PATH}.{ParameterKeys.OVERRIDE_MEASURE_ID}"
            if quote.measure_id not in catalogue_measures:
                problems.append(
                    ParameterProblem(
                        path=measure_path,
                        code=ParameterProblemCodes.INVALID.format(path=code_path),
                        message=f"{quote.measure_id!r} is no measure of the catalogue.",
                        accepted=tuple(catalogue_measures),
                    )
                )
                continue
            if quote.measure_id in costless_measures:
                problems.append(
                    ParameterProblem(
                        path=measure_path,
                        code=ParameterProblemCodes.COSTLESS.format(path=code_path),
                        message=f"{quote.measure_id!r} costs nothing to carry out (a setting, not a purchase), "
                        "so there is no price a quote could replace.",
                    )
                )
                continue
            carried_out = tuple(stage_measures[quote.stage])
            if quote.measure_id not in carried_out:
                problems.append(
                    ParameterProblem(
                        path=measure_path,
                        code=ParameterProblemCodes.NOT_IN_STAGE.format(path=code_path),
                        message=f"stage {quote.stage} does not carry out {quote.measure_id!r}, so it buys nothing "
                        "a quote could price.",
                        accepted=carried_out,
                    )
                )
        return problems

    @classmethod
    def _read_plan_start_year(cls, reader: ParameterReader) -> Optional[int]:
        """Read `plan_start_year`, the calendar year of the plan's year 0 (the year the first stage is bought).

        A `null` states nothing, so a document without a start year reads back. A stated year must be a whole number
        within `PlanYearBounds`, else it is refused as `parameters.plan_start_year.invalid`.

        Args:
            reader: The top-level block's reader.

        Returns:
            The year, or None when the file states none or a refused one.
        """
        if reader.raw.get(ParameterKeys.PLAN_START_YEAR) is None:
            return None
        return reader.integer(
            ParameterKeys.PLAN_START_YEAR, minimum=PlanYearBounds.MINIMUM, maximum=PlanYearBounds.MAXIMUM
        )

    @classmethod
    def _read_horizon_and_interest(cls, reader: ParameterReader, overrides: Dict[str, Any]) -> None:
        """Read `horizon_years` and `interest_rate` onto the engine record's horizon and interest fields.

        The bounds are the engine record's own (horizon at least one year, interest rate above -100 %), checked here so
        the problem names the key instead of surfacing the record's bare `ValueError`.

        Args:
            reader: The top-level block's reader.
            overrides: The engine-field overrides being assembled; written in place.
        """
        horizon = reader.integer(ParameterKeys.HORIZON_YEARS, minimum=1)
        if horizon is not None:
            overrides["observation_period_in_years"] = horizon
        interest = reader.number(ParameterKeys.INTEREST_RATE, above=-1.0)
        if interest is not None:
            overrides["interest_rate"] = interest

    @classmethod
    def _stages_country(cls, stored: Optional[EconomicParameters], stored_country: Optional[str]) -> Optional[str]:
        """Return the country the stages were priced for, from either of the caller's two sources.

        Args:
            stored: The stage parameters, whose `country` is itself a stage's statement.
            stored_country: The country the caller resolved over every stage source, if any.

        Returns:
            The country, or None when no stage states one.
        """
        if stored_country is not None:
            return stored_country
        return stored.country if stored is not None else None

    @classmethod
    def _read_country(
        cls, reader: ParameterReader, stored_country: Optional[str], overrides: Dict[str, Any]
    ) -> None:
        """Resolve the country against the stages, with no default.

        A plan's price data and subsidy catalogue follow the country its stages were priced under. A file may repeat
        that country but may not change it, since stored inputs cannot be re-priced into another country.

        Args:
            reader: The top-level block's reader.
            stored_country: The country the stages were priced for, or None.
            overrides: The engine-field overrides being assembled; written in place.
        """
        if not reader.has(ParameterKeys.COUNTRY):
            if stored_country is None:
                reader.refuse(
                    ParameterKeys.COUNTRY,
                    ParameterProblemCodes.MISSING,
                    "no stage says which country it was priced for — neither its stored evaluation "
                    "nor its stored inputs — and there is no default country: name `country` in "
                    "the --parameters file.",
                )
                return
            # Written out even though it usually equals the stored record's own country: when the
            # stages carry no stored evaluation at all, that record is built from these overrides,
            # and leaving the country out would let the field default -- "DE" -- decide it.
            overrides["country"] = stored_country
            return
        country = reader.text(ParameterKeys.COUNTRY)
        if country is None:
            return
        if not (len(country) == 2 and country.isalpha() and country.isupper()):
            reader.refuse(
                ParameterKeys.COUNTRY,
                ParameterProblemCodes.INVALID,
                f"{country!r} is not an upper-case ISO-3166 alpha-2 country code.",
            )
            return
        if stored_country is not None and country != stored_country:
            reader.refuse(
                ParameterKeys.COUNTRY,
                ParameterProblemCodes.MISMATCH,
                f"the parameters name {country!r} but the stages were priced for {stored_country!r}; "
                "one plan is one country's price data, and the stages decide which.",
            )
            return
        overrides["country"] = country

    @classmethod
    def _stages_price_basis_year(
        cls, stored: Optional[EconomicParameters], stored_price_basis_year: Optional[int]
    ) -> Optional[int]:
        """Return the price basis year the stages were priced at, from either of the caller's two sources.

        Args:
            stored: The stage parameters, whose `price_basis_year` is the resolved year a stored evaluation used.
            stored_price_basis_year: The year the caller resolved over every stage source, if any.

        Returns:
            The year, or None when no stage states one.
        """
        if stored_price_basis_year is not None:
            return stored_price_basis_year
        return stored.price_basis_year if stored is not None else None

    @classmethod
    def _read_price_basis_year(
        cls,
        reader: ParameterReader,
        stored_year: Optional[int],
        overrides: Dict[str, Any],
        plan_start_year: Optional[int] = None,
    ) -> None:
        """Resolve the price basis year (the year whose price level the inputs are read at) against the stages.

        A file may repeat the stages' year but not change it, because stored inputs cannot be re-based without
        re-running. A `null` states nothing and takes the stages' year. When no stage and not the file states a year, a
        stated `plan_start_year` supplies it: the record's year is left unset and `StagedEvaluator.evaluate` resolves
        it via `effective_price_basis_year`, clamped to the earliest year of the country's device data. Without a start
        year either, the run is refused rather than using the simulation (weather) year, which says nothing about price
        levels. Stages that state a year keep it whatever `plan_start_year` says.

        Args:
            reader: The top-level block's reader.
            stored_year: The year the stages were priced at, or None.
            overrides: The engine-field overrides being assembled; written in place.
            plan_start_year: The calendar year the file says the plan starts in, or None.
        """
        states_a_year = (
            reader.has(ParameterKeys.PRICE_BASIS_YEAR)
            and reader.raw[ParameterKeys.PRICE_BASIS_YEAR] is not None
        )
        if not states_a_year:
            if stored_year is None and plan_start_year is not None:
                # Left unset on purpose: the staged evaluator resolves it from the start year with
                # the one clamp every entry point shares.
                return
            if stored_year is None:
                reader.refuse(
                    ParameterKeys.PRICE_BASIS_YEAR,
                    ParameterProblemCodes.MISSING,
                    "no stage says which price basis year it was priced at — neither its stored "
                    "evaluation nor its stored inputs — and re-deriving one from the weather "
                    "year would price the plan at a level none of its runs used: name "
                    "`price_basis_year` or `plan_start_year` in the --parameters file, or re-run "
                    "the jobs.",
                )
                return
            # Written out for the same reason as the country: with no stored record the engine
            # parameters are built from these overrides alone, and the field's own default is
            # None, which is "re-derive it downstream" — exactly what this rule forbids.
            overrides["price_basis_year"] = stored_year
            return
        year = reader.integer(ParameterKeys.PRICE_BASIS_YEAR)
        if year is None:
            return
        if stored_year is not None and year != stored_year:
            reader.refuse(
                ParameterKeys.PRICE_BASIS_YEAR,
                ParameterProblemCodes.MISMATCH,
                f"the parameters name {year} but the stages were priced at {stored_year}; the stored "
                "inputs cannot be re-based without re-running them.",
            )
            return
        overrides["price_basis_year"] = year

    @classmethod
    def _read_subsidy_mode(cls, reader: ParameterReader) -> Optional[SubsidyModeName]:
        """Read `subsidy_mode`, the two-valued input spelling of "apply the catalogue".

        Args:
            reader: The top-level block's reader.

        Returns:
            The mode, or None when the file states none (the perspective's own subsidy mode then stands).
        """
        spelling = reader.text(ParameterKeys.SUBSIDY_MODE)
        if spelling is None:
            return None
        try:
            return SubsidyModeName(spelling)
        except ValueError:
            reader.refuse(
                ParameterKeys.SUBSIDY_MODE,
                ParameterProblemCodes.INVALID,
                f"{spelling!r} is no subsidy mode.",
                accepted=tuple(member.value for member in SubsidyModeName),
            )
            return None

    @classmethod
    def _read_financing(
        cls, reader: ParameterReader, problems: List[ParameterProblem]
    ) -> Tuple[bool, Optional[FinancingPlan]]:
        """Read the `financing` block: a cash purchase, or one annuity loan per buying stage.

        Loan fields on a cash purchase are refused rather than ignored, since dropping them would price a plan the
        caller did not describe.

        Args:
            reader: The top-level block's reader.
            problems: The shared problem list, for the nested block's reader.

        Returns:
            `(whether the file stated the key, the plan)`; the plan is None for a cash purchase and for a refused
                block.
        """
        if not reader.has(ParameterKeys.FINANCING):
            return False, None
        raw = reader.block(ParameterKeys.FINANCING)
        if raw is None:
            return True, None
        nested = ParameterReader(
            raw,
            reader.path_of(ParameterKeys.FINANCING),
            ParameterKeys.ACCEPTED_FINANCING,
            problems,
        )
        nested.refuse_unknown_keys()
        spelling = nested.text(ParameterKeys.FINANCING_KIND)
        if spelling is None:
            if not nested.has(ParameterKeys.FINANCING_KIND):
                nested.refuse(
                    ParameterKeys.FINANCING_KIND,
                    ParameterProblemCodes.MISSING,
                    "a financing block says how the investment is paid for.",
                    accepted=tuple(member.value for member in FinancingKindName),
                )
            return True, None
        try:
            kind = FinancingKindName(spelling)
        except ValueError:
            nested.refuse(
                ParameterKeys.FINANCING_KIND,
                ParameterProblemCodes.INVALID,
                f"{spelling!r} is no financing kind.",
                accepted=tuple(member.value for member in FinancingKindName),
            )
            return True, None
        loan_fields = {
            ParameterKeys.FINANCING_SHARE,
            ParameterKeys.FINANCING_INTEREST_RATE,
            ParameterKeys.FINANCING_TERM,
        }
        if kind is FinancingKindName.CASH:
            for key in sorted(loan_fields & set(raw)):
                nested.refuse(
                    key,
                    ParameterProblemCodes.INVALID,
                    f"{key!r} describes a loan, and this block buys for cash.",
                )
            return True, None
        return True, cls._read_loan(nested)

    @classmethod
    def _read_loan(cls, nested: ParameterReader) -> Optional[FinancingPlan]:
        """Build the annuity loan plan from a `financing` block of kind `loan`.

        Fields the input vocabulary does not carry (repayment shape, soft-loan scheme id, repayment grant) keep the
        engine's defaults; the subsidy catalogue decides them.

        Args:
            nested: The financing block's reader, already checked for unknown keys.

        Returns:
            The plan, or None when one of its fields was refused.
        """
        before = len(nested.problems)
        share = nested.number(ParameterKeys.FINANCING_SHARE, at_least=0.0, at_most=1.0)
        rate = nested.number(ParameterKeys.FINANCING_INTEREST_RATE, above=-1.0)
        term = nested.integer(ParameterKeys.FINANCING_TERM, minimum=1)
        if len(nested.problems) > before:
            return None
        default = FinancingPlan()
        return FinancingPlan(
            financed_share=default.financed_share if share is None else share,
            nominal_interest_rate=default.nominal_interest_rate if rate is None else rate,
            term_in_years=default.term_in_years if term is None else term,
        )

    @classmethod
    def _read_escalation(
        cls, reader: ParameterReader, problems: List[ParameterProblem], overrides: Dict[str, Any]
    ) -> None:
        """Read the `escalation` block onto the four escalation fields of the engine record.

        `energy` maps carrier (the `EnergyCarrier` value) to rate. An empty mapping states "no per-carrier override",
        so every carrier falls back to the country's defaults file and then to the general rate.

        Args:
            reader: The top-level block's reader.
            problems: The shared problem list, for the nested block's reader.
            overrides: The engine-field overrides being assembled; written in place.
        """
        if not reader.has(ParameterKeys.ESCALATION):
            return
        raw = reader.block(ParameterKeys.ESCALATION)
        if raw is None:
            return
        nested = ParameterReader(
            raw, reader.path_of(ParameterKeys.ESCALATION), ParameterKeys.ACCEPTED_ESCALATION, problems
        )
        nested.refuse_unknown_keys()
        for key, field_name in (
            (ParameterKeys.ESCALATION_GENERAL, "general_price_escalation_rate"),
            (ParameterKeys.ESCALATION_INVESTMENT, "investment_price_escalation_rate"),
            (ParameterKeys.ESCALATION_FEED_IN, "feed_in_escalation_rate"),
        ):
            rate = nested.number(key, above=-1.0)
            if rate is not None:
                overrides[field_name] = rate
        if not nested.has(ParameterKeys.ESCALATION_ENERGY):
            return
        by_carrier = nested.block(ParameterKeys.ESCALATION_ENERGY)
        if by_carrier is None:
            return
        energy = ParameterReader(
            by_carrier,
            nested.path_of(ParameterKeys.ESCALATION_ENERGY),
            tuple(member.value for member in EnergyCarrier),
            problems,
            noun="energy carrier",
        )
        energy.refuse_unknown_keys()
        rates: Dict[EnergyCarrier, float] = {}
        for spelling in by_carrier:
            if spelling not in {member.value for member in EnergyCarrier}:
                continue  # already refused as an unknown key, with the carriers that exist
            if spelling == EnergyCarrier.ELECTRICITY_FEED_IN.value:
                # The per-carrier table is read for what a carrier *costs*; the feed-in
                # remuneration escalates with `escalation.feed_in` once its fixed period is over,
                # so a rate here would be accepted and never read.
                energy.refuse(
                    spelling,
                    ParameterProblemCodes.INVALID,
                    "the feed-in remuneration is never escalated at a per-carrier rate: it is held "
                    "fixed for its contract period and then follows `escalation.feed_in`.",
                )
                continue
            rate = energy.number(spelling, above=-1.0)
            if rate is not None:
                rates[EnergyCarrier(spelling)] = rate
        overrides["energy_price_escalation_rates"] = rates

    @classmethod
    def _read_energy_prices(
        cls, reader: ParameterReader, problems: List[ParameterProblem], overrides: Dict[str, Any]
    ) -> None:
        """Read the `energy_prices` block onto `EconomicParameters.energy_prices`.

        One entry per carrier (keyed by the `EnergyCarrier` value) states the all-in year-1
        `working_price_in_euro_per_kwh` and/or `standing_charge_in_euro_per_year`, each a number or a band `{min, best,
        max}` within `StatedPriceBounds`. `ELECTRICITY_FEED_IN` states only a working price, the feed-in rate. A
        present block replaces the stages' stated prices as a whole.

        Args:
            reader: The top-level block's reader.
            problems: The shared problem list, for the nested blocks' readers.
            overrides: The engine-field overrides being assembled; written in place.
        """
        if not reader.has(ParameterKeys.ENERGY_PRICES):
            return
        raw = reader.block(ParameterKeys.ENERGY_PRICES)
        if raw is None:
            return
        by_carrier = ParameterReader(
            raw,
            reader.path_of(ParameterKeys.ENERGY_PRICES),
            tuple(member.value for member in EnergyCarrier),
            problems,
            noun="energy carrier",
        )
        by_carrier.refuse_unknown_keys()
        stated: Dict[EnergyCarrier, StatedEnergyPrice] = {}
        for spelling in raw:
            if spelling not in {member.value for member in EnergyCarrier}:
                continue  # already refused as an unknown key, with the carriers that exist
            carrier = EnergyCarrier(spelling)
            terms = by_carrier.block(spelling)
            if terms is None:
                continue
            read = cls._read_carrier_price(
                carrier, ParameterReader(terms, by_carrier.path_of(spelling), ParameterKeys.ACCEPTED_ENERGY_PRICE,
                                         problems, noun="stated price term")
            )
            if read is not None:
                stated[carrier] = read
        overrides["energy_prices"] = stated

    @classmethod
    def _read_carrier_price(cls, carrier: EnergyCarrier, terms: ParameterReader) -> Optional[StatedEnergyPrice]:
        """Read one carrier's stated price terms, within `StatedPriceBounds`.

        Args:
            carrier: The carrier the terms are stated for.
            terms: The carrier's own reader.

        Returns:
            The stated terms, or None when one was refused or none was stated.
        """
        before = len(terms.problems)
        terms.refuse_unknown_keys()
        feed_in = carrier == EnergyCarrier.ELECTRICITY_FEED_IN
        if feed_in:
            working = terms.band(
                ParameterKeys.PRICE_WORKING,
                at_least=StatedPriceBounds.FEED_IN_MINIMUM_IN_EURO_PER_KWH,
                at_most=StatedPriceBounds.FEED_IN_MAXIMUM_IN_EURO_PER_KWH,
            )
            standing = None
            if terms.has(ParameterKeys.PRICE_STANDING):
                terms.refuse(
                    ParameterKeys.PRICE_STANDING,
                    ParameterProblemCodes.INVALID,
                    "the feed-in carrier is a remuneration per kWh sold and has no standing charge; "
                    "state it for ELECTRICITY, whose contract the feed-in rate belongs to.",
                )
        else:
            working = terms.band(
                ParameterKeys.PRICE_WORKING,
                above=StatedPriceBounds.WORKING_PRICE_ABOVE_IN_EURO_PER_KWH,
                at_most=StatedPriceBounds.WORKING_PRICE_MAXIMUM_IN_EURO_PER_KWH,
            )
            standing = terms.band(
                ParameterKeys.PRICE_STANDING,
                at_least=StatedPriceBounds.STANDING_CHARGE_MINIMUM_IN_EURO_PER_YEAR,
                at_most=StatedPriceBounds.STANDING_CHARGE_MAXIMUM_IN_EURO_PER_YEAR,
            )
        if len(terms.problems) > before:
            return None
        if working is None and standing is None:
            terms.refuse(
                "",
                ParameterProblemCodes.MISSING,
                "a carrier in energy_prices states at least one price; leave the carrier out to "
                "price it from the database.",
                accepted=(ParameterKeys.PRICE_WORKING,) if feed_in else ParameterKeys.ACCEPTED_ENERGY_PRICE,
            )
            return None
        return StatedEnergyPrice(working_price_in_euro_per_kwh=working, standing_charge_in_euro_per_year=standing)

    @classmethod
    def reconciled_perspective_id(
        cls, in_file: Optional[str], on_command_line: Optional[str], problems: List[ParameterProblem]
    ) -> str:
        """Return the one perspective id, reconciling the file's `perspective_id` and the `--perspective` flag.

        Two sources that disagree are refused rather than one silently winning.

        Args:
            in_file: `perspective_id` as the file stated it, or None.
            on_command_line: The `--perspective` flag, or None.
            problems: The shared problem list; a disagreement is appended to it.

        Returns:
            Whichever source named an id, or `DEFAULT_PERSPECTIVE_ID` when neither did. On a disagreement the file's id
                is returned and the appended problem refuses the run.
        """
        if in_file is not None and on_command_line is not None and in_file != on_command_line:
            path = f"{ParameterKeys.ROOT_PATH}.{ParameterKeys.PERSPECTIVE_ID}"
            problems.append(
                ParameterProblem(
                    path=path,
                    code=ParameterProblemCodes.MISMATCH.format(path=path),
                    message=f"the parameters name perspective {in_file!r} and --perspective names "
                    f"{on_command_line!r}; name one of them, or the same one twice.",
                )
            )
        if in_file is not None:
            return in_file
        if on_command_line is not None:
            return on_command_line
        return cls.DEFAULT_PERSPECTIVE_ID

    def applied_to(self, perspective: Perspective) -> Tuple[EconomicParameters, Perspective]:
        """Return the engine record and the perspective the plan is actually priced under.

        `financing` and `subsidy_mode` are perspective dimensions, so they are applied by copying the bundle's
        perspective with those two replaced. `apply_subsidies` on the record is then set from the subsidy mode in
        force, so the document's echo describes the run.

        Args:
            perspective: The perspective named by id, as the shipped bundle defines it.

        Returns:
            `(parameters, perspective)`, ready for the staged evaluator.

        Raises:
            ValueError: If the file was refused (this result has problems and no parameters).
        """
        if self.parameters is None:
            raise ValueError("a refused parameter file has no parameters to price with.")
        priced_under = perspective
        if self.financing_given:
            priced_under = replace(priced_under, financing=self.financing)
        if self.subsidy_mode is not None:
            mode = SubsidyMode.full() if self.subsidy_mode is SubsidyModeName.FULL else SubsidyMode.none()
            priced_under = replace(priced_under, subsidy_mode=mode)
        applies_catalog = priced_under.subsidy_mode.kind is not SubsidyModeKind.NONE
        return replace(self.parameters, apply_subsidies=applies_catalog), priced_under

    @classmethod
    def subsidy_mode_of(cls, perspective: Perspective) -> SubsidyModeName:
        """Return how a document spells the subsidy mode a perspective ran under.

        The document's vocabulary says only whether the catalogue was applied, so the engine's `ONLY` and `EXCLUDE`
        kinds (which no parameter file can ask for) are reported as `full`.

        Args:
            perspective: The perspective the plan was priced under.

        Returns:
            `SubsidyModeName.NONE` when nothing was admitted, `SubsidyModeName.FULL` otherwise.
        """
        if perspective.subsidy_mode.kind is SubsidyModeKind.NONE:
            return SubsidyModeName.NONE
        return SubsidyModeName.FULL

    @classmethod
    def financing_block(cls, plan: Optional[FinancingPlan]) -> Dict[str, Any]:
        """Return how a document states the financing a plan ran under.

        Args:
            plan: The perspective's financing plan, or None for a cash purchase.

        Returns:
            `{"kind": "cash"}`, or `{"kind": "loan", ...}` with the three loan fields of the input vocabulary; the
                repayment shape and any soft-loan scheme are not stated.
        """
        if plan is None:
            return {ParameterKeys.FINANCING_KIND: FinancingKindName.CASH.value}
        return {
            ParameterKeys.FINANCING_KIND: FinancingKindName.LOAN.value,
            ParameterKeys.FINANCING_SHARE: plan.financed_share,
            ParameterKeys.FINANCING_INTEREST_RATE: plan.nominal_interest_rate,
            ParameterKeys.FINANCING_TERM: plan.term_in_years,
        }

    @classmethod
    def to_document_block(
        cls,
        parameters: EconomicParameters,
        perspective: Perspective,
        weather_year: Optional[int],
        subsidy_catalog: Optional[str],
        energy: Optional[EnergyEcho] = None,
        plan_start_year: Optional[int] = None,
        price_basis_year_origin: Optional[EchoOrigin] = None,
        investment_overrides: Sequence[Mapping[str, Any]] = (),
    ) -> Dict[str, Any]:
        """Return the `parameters` block `economics_result.json` publishes.

        Every key is one `from_mapping` accepts, so the block handed back as `--parameters` over the same stages gives
        the same run. `weather_year` (the year of the stages' weather; it dates nothing), `subsidy_catalog` and
        `origins` describe the run and are ignored when read back; `plan_start_year` is an assumption (`null` when the
        plan named none). `escalation.energy` and `energy_prices` state the per-carrier rates and year-1 prices
        actually used for every carrier a stage bills or the plan named, so a second run fed this block prices the same
        numbers with every `origins` entry `stated`.

        Args:
            parameters: The engine record the plan was priced with.
            perspective: The perspective it was priced under, after overrides.
            weather_year: The year of the weather the stages were simulated with, or None.
            subsidy_catalog: The catalogue id in force, or None.
            energy: The rates and prices as the staged evaluator resolved them; None echoes only what `parameters`
                states.
            plan_start_year: The calendar year the plan starts in, or None.
            price_basis_year_origin: `EchoOrigin.PLAN_START_YEAR` when the start year supplied the price basis year
                (written as `origins.price_basis_year`); None otherwise. `origins.price_level` is written whenever the
                basis year and the start year differ.
            investment_overrides: The reader's quotes, each as `StatedQuote.to_json` writes it; empty without any.

        Returns:
            The block, keys in the order the document writes them.
        """
        echo = energy if energy is not None else EnergyEcho.stated_only(parameters)
        rates = sorted(echo.rates.items(), key=lambda item: item[0].value)
        prices = sorted(echo.prices.items(), key=lambda item: item[0].value)
        origins: Dict[str, Any] = {
            ParameterKeys.ESCALATION: {
                ParameterKeys.ESCALATION_ENERGY: {carrier.value: origin.value for carrier, (_rate, origin) in rates}
            },
            ParameterKeys.ENERGY_PRICES: {
                carrier.value: {key: origin.value for key, _value, origin in price.fields()}
                for carrier, price in prices
            },
        }
        if price_basis_year_origin is not None:
            origins[ParameterKeys.PRICE_BASIS_YEAR] = price_basis_year_origin.value
        level = cls.price_level(parameters.price_basis_year, plan_start_year)
        if level is not None:
            origins[ParameterKeys.PRICE_LEVEL] = level
        return {
            ParameterKeys.HORIZON_YEARS: parameters.observation_period_in_years,
            ParameterKeys.INTEREST_RATE: parameters.interest_rate,
            ParameterKeys.COUNTRY: parameters.country,
            ParameterKeys.PRICE_BASIS_YEAR: parameters.price_basis_year,
            ParameterKeys.PLAN_START_YEAR: plan_start_year,
            ParameterKeys.WEATHER_YEAR: weather_year,
            ParameterKeys.PERSPECTIVE_ID: perspective.id,
            ParameterKeys.SUBSIDY_MODE: cls.subsidy_mode_of(perspective).value,
            ParameterKeys.FINANCING: cls.financing_block(perspective.financing),
            ParameterKeys.ESCALATION: {
                ParameterKeys.ESCALATION_GENERAL: parameters.general_price_escalation_rate,
                ParameterKeys.ESCALATION_INVESTMENT: parameters.investment_price_escalation_rate,
                ParameterKeys.ESCALATION_FEED_IN: parameters.feed_in_escalation_rate,
                ParameterKeys.ESCALATION_ENERGY: {carrier.value: rate for carrier, (rate, _origin) in rates},
            },
            ParameterKeys.ENERGY_PRICES: {
                carrier.value: {key: cls._band_of(value) for key, value, _origin in price.fields()}
                for carrier, price in prices
            },
            ParameterKeys.INVESTMENT_OVERRIDES: [dict(quote) for quote in investment_overrides],
            ParameterKeys.SUBSIDY_CATALOG: subsidy_catalog,
            ParameterKeys.ORIGINS: origins,
        }

    @staticmethod
    def price_level(price_basis_year: Optional[int], plan_start_year: Optional[int]) -> Optional[Dict[str, int]]:
        """Return `origins.price_level`: the years the document's amounts were escalated between.

        Prices are read at `price_basis_year`; when the plan starts in another year every amount is escalated (or
        de-escalated, for an earlier start) to `plan_start_year` at its own escalation rate, except a reader's quote
        and a fixed-amount grant. The echoed year-1 prices in `energy_prices` stay at the price basis year, so the
        block reads back into the same run.

        Args:
            price_basis_year: The year the plan was priced at.
            plan_start_year: The calendar year of the plan's year 0, or None.

        Returns:
            `{"from_year": price_basis_year, "to_year": plan_start_year}`, or None when nothing was escalated (no start
                year, or the same year).
        """
        if price_basis_year is None or plan_start_year is None or plan_start_year == price_basis_year:
            return None
        return {ParameterKeys.PRICE_LEVEL_FROM: price_basis_year, ParameterKeys.PRICE_LEVEL_TO: plan_start_year}

    @staticmethod
    def _band_of(value: UncertainValue) -> Dict[str, float]:
        """One amount as the document writes a band, and as :meth:`ParameterReader.band` reads it."""
        return {"min": value.minimum, "best": value.best_estimate, "max": value.maximum}
