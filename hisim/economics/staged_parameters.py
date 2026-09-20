"""The ``staged`` command's parameter vocabulary: the document's own ``parameters`` block.

``python -m hisim.economics staged`` used to read its ``--parameters`` file as the engine's own
:class:`~hisim.economics.parameters.EconomicParameters` record, whose field names
(``observation_period_in_years``, ``apply_subsidies``, …) are a different vocabulary from the one
``economics_result.json`` publishes and the RenoVisor contract promises (``horizon_years``,
``perspective_id``, ``financing``, ``subsidy_mode``). Two vocabularies for one thing was the
defect: the backend's documented example block was rejected key by key, and a file that named no
country silently inherited the engine's ``"DE"`` default and priced an Irish house with German
prices.

This module is the single table of keys both directions share. :meth:`StagedParameters.from_mapping`
reads an input mapping in the *document's* shape onto the engine record, and
:meth:`StagedParameters.to_document_block` writes the block back out in the same shape, so a reader
can feed a document's assumptions back in unchanged and get the same run. Two rules hold throughout:

* **No default country.** The country is the one the stages were priced under; a ``country`` in the
  file is only *checked* against it. ``"DE"`` never appears unless a file or a stage wrote it.
* **Every fault at once.** Nothing raises on the first bad key. Faults are collected as
  :class:`ParameterProblem` rows, and the CLI writes all of them into ``problems.json`` with exit 2
  rather than a traceback.

Example::

    parsed = StagedParameters.from_mapping(
        {"horizon_years": 20, "interest_rate": 0.03, "perspective_id": "brownfield_net",
         "financing": {"kind": "cash"}, "subsidy_mode": "full"},
        stored=stage_parameters,
    )
    parameters, perspective = parsed.applied_to(bundle_perspective)

Specification: ``roadmap/renovisor/implementation/step13_staged_parameters.md`` §1, which settles
the accepted keys, the country rule and the refusal codes.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.financing import FinancingPlan
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import Perspective, SubsidyMode, SubsidyModeKind


class ParameterKeys:
    """Every key name the staged parameter block uses, on input and on output.

    One class rather than two lists, because the reader and the writer must not drift: a rename
    here changes what ``--parameters`` accepts and what ``economics_result.json`` publishes in the
    same edit, which is the property that makes a document's ``parameters`` block a legal input
    file. The names are the document's own (``horizon_years``, not
    ``observation_period_in_years``); the mapping onto the engine record's field names lives in
    :meth:`StagedParameters.from_mapping`.

    Example::

        ParameterKeys.HORIZON_YEARS in ParameterKeys.ACCEPTED
    """

    #: Observation period in years; maps to ``EconomicParameters.observation_period_in_years``.
    HORIZON_YEARS: ClassVar[str] = "horizon_years"

    #: Nominal calculation interest rate; maps to ``EconomicParameters.interest_rate``.
    INTEREST_RATE: ClassVar[str] = "interest_rate"

    #: ISO-2 country code deciding the price data and the subsidy catalogue.
    COUNTRY: ClassVar[str] = "country"

    #: Price basis year of the database lookups, or null for "the simulation year".
    PRICE_BASIS_YEAR: ClassVar[str] = "price_basis_year"

    #: Id of the perspective the plan is priced under.
    PERSPECTIVE_ID: ClassVar[str] = "perspective_id"

    #: Whether the subsidy catalogue is applied at all: ``full`` or ``none``.
    SUBSIDY_MODE: ClassVar[str] = "subsidy_mode"

    #: Cash or one annuity loan per buying stage.
    FINANCING: ClassVar[str] = "financing"

    #: The escalation rates, as a nested block.
    ESCALATION: ClassVar[str] = "escalation"

    #: Which catalogue produced a document. Accepted and ignored on input (see the class docstring
    #: of :class:`StagedParameters`): the catalogue comes from the shipped directory or from
    #: ``--subsidy-catalog``.
    SUBSIDY_CATALOG: ClassVar[str] = "subsidy_catalog"

    #: The year the stages were simulated in. Accepted and ignored on input: it is a fact of the
    #: stages, not an assumption a caller may state.
    SIMULATION_YEAR: ClassVar[str] = "simulation_year"

    #: General price escalation, inside :attr:`ESCALATION`.
    ESCALATION_GENERAL: ClassVar[str] = "general"

    #: Investment price escalation, inside :attr:`ESCALATION`.
    ESCALATION_INVESTMENT: ClassVar[str] = "investment"

    #: Feed-in remuneration escalation, inside :attr:`ESCALATION`.
    ESCALATION_FEED_IN: ClassVar[str] = "feed_in"

    #: Per-carrier energy price escalation, inside :attr:`ESCALATION`.
    ESCALATION_ENERGY: ClassVar[str] = "energy"

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
        SIMULATION_YEAR,
        PERSPECTIVE_ID,
        SUBSIDY_MODE,
        FINANCING,
        ESCALATION,
        SUBSIDY_CATALOG,
    )

    #: Every key the :attr:`ESCALATION` block accepts.
    ACCEPTED_ESCALATION: ClassVar[Tuple[str, ...]] = (
        ESCALATION_GENERAL,
        ESCALATION_INVESTMENT,
        ESCALATION_FEED_IN,
        ESCALATION_ENERGY,
    )

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


class SubsidyModeName(enum.Enum):
    """How a parameter file spells "apply the subsidy catalogue" and "do not".

    The input vocabulary of :attr:`ParameterKeys.SUBSIDY_MODE`. It is deliberately narrower than
    the engine's own :class:`~hisim.economics.perspectives.SubsidyModeKind`, which also has the two
    list-carrying kinds ``ONLY`` and ``EXCLUDE``: a RenoVisor caller chooses whether the catalogue
    runs, never which schemes it may contain — that is the catalogue's own eligibility question.

    Example::

        SubsidyModeName("full") is SubsidyModeName.FULL
    """

    FULL = "full"
    NONE = "none"


class FinancingKindName(enum.Enum):
    """How a parameter file spells "paid from cash" and "paid from one annuity loan".

    The discriminator of :attr:`ParameterKeys.FINANCING`. ``CASH`` means the perspective gets no
    :class:`~hisim.economics.financing.FinancingPlan` at all, which is what "cash purchase" is in
    the engine; ``LOAN`` means one annuity loan per buying stage, with the plan's remaining fields
    at the engine's documented defaults unless the file states them.

    Example::

        FinancingKindName("loan") is FinancingKindName.LOAN
    """

    CASH = "cash"
    LOAN = "loan"


class ParameterProblemCodes:
    """The five code shapes a refused parameter block reports, as format strings.

    Each takes the dotted ``path`` of the offending value, so one template serves the top-level
    block and its nested ``financing`` and ``escalation`` blocks alike:
    ``"parameters.horizon_years.invalid"`` and ``"parameters.financing.kind.invalid"`` come out of
    the same :attr:`INVALID`. They exist as a class rather than as literals at the call sites so
    the set a backend branches on can be read in one place.

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


@dataclass(frozen=True)
class ParameterProblem:
    """One reason a ``--parameters`` block was refused, at one path.

    The row ``problems.json`` carries, in the same three-or-four-field shape the translator's
    :class:`hisim.renovisor.request.Problem` uses, so a backend reads an economics refusal exactly
    as it reads a request refusal. It is a separate type because the codes are a different set:
    the translator's are the contract's §7 request codes, these are the ``parameters.*`` codes of
    step 13 §1.3.

    Args:
        path: The dotted path of the offending value, e.g. ``parameters.financing.kind``.
        code: Which kind of problem it is, one of :class:`ParameterProblemCodes` filled in.
        message: One sentence a person can read, naming the value and what is wrong with it.
        accepted: The values or keys that would have been accepted, or ``None`` when listing them
            would say nothing (a number out of range, a mismatch against the stages).
    """

    path: str
    code: str
    message: str
    accepted: Optional[Tuple[Any, ...]] = None

    def to_json(self) -> Dict[str, Any]:
        """The problem as one row of the ``problems.json`` document.

        Returns:
            ``{"path": …, "code": …, "message": …}``, plus ``"accepted"`` when the problem names
            the values it would have taken.
        """
        row: Dict[str, Any] = {"path": self.path, "code": self.code, "message": self.message}
        if self.accepted is not None:
            row["accepted"] = list(self.accepted)
        return row


class ParameterReader:
    """Reads one parameter block key by key, collecting faults instead of raising on the first.

    A parameter file is written by hand or assembled by a frontend, so reporting one fault per
    invocation would make fixing a five-key block a five-round conversation. Every accessor
    therefore returns ``None`` for a key that is absent *or* wrong and appends a
    :class:`ParameterProblem` in the second case; the caller checks the problem list once, at the
    end. Booleans are rejected wherever a number is expected, because ``True`` is an ``int`` in
    Python and ``{"horizon_years": true}`` is a mistake, not a horizon of one year.

    Example::

        problems: List[ParameterProblem] = []
        reader = ParameterReader({"horizon_years": 20}, ParameterKeys.ROOT_PATH,
                                 ParameterKeys.ACCEPTED, problems)
        reader.refuse_unknown_keys()
        horizon = reader.integer(ParameterKeys.HORIZON_YEARS, minimum=1)

    Args:
        raw: The block as it was parsed from JSON.
        path: The dotted path of the block itself, e.g. ``parameters`` or ``parameters.financing``.
        accepted: The keys this block accepts, for :meth:`refuse_unknown_keys` and its message.
        problems: The list every fault is appended to; shared with the other blocks of one file.
        noun: What one key of this block is, for the unknown-key message ("energy carrier" for the
            per-carrier escalation rates, whose keys are carrier names rather than parameters).
    """

    def __init__(
        self,
        raw: Mapping[str, Any],
        path: str,
        accepted: Sequence[str],
        problems: List[ParameterProblem],
        noun: str = "parameter of a staged plan",
    ) -> None:
        """Store the block, its path, its accepted keys, the shared problem list and the noun."""
        self.raw = raw
        self.path = path
        self.accepted = tuple(accepted)
        self.problems = problems
        self.noun = noun

    def path_of(self, key: str) -> str:
        """The dotted path one key of this block is reported under.

        Args:
            key: The key name.

        Returns:
            ``"<this block's path>.<key>"``.
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
            code: A template from :class:`ParameterProblemCodes`, still holding ``{path}``.
            message: The sentence the caller reads.
            accepted: The values that would have been accepted, if listing them helps.
            code_path: The path the *code* is built from, when it differs from the path the
                problem is reported at — an unknown key is ``parameters.unknown_key``, one code
                for the block, while the row still points at the key nobody claimed.
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

        A key nobody claims is a typo in an assumption file, and ignoring it would price the run
        with a value the author believed they had overridden — the failure mode this whole module
        exists to remove.
        """
        for key in sorted(set(self.raw) - set(self.accepted)):
            self.refuse(
                key,
                ParameterProblemCodes.UNKNOWN_KEY,
                f"{key!r} is no {self.noun}.",
                accepted=self.accepted,
                code_path=self.path,
            )

    def has(self, key: str) -> bool:
        """Whether the block states the key at all, however it is spelled.

        Args:
            key: The key name.

        Returns:
            True when the key is present, even with a null or a wrong value.
        """
        return key in self.raw

    def integer(self, key: str, minimum: Optional[int] = None) -> Optional[int]:
        """One integer value, or None when the key is absent or its value is refused.

        Args:
            key: The key name.
            minimum: The smallest accepted value, or None for no lower bound.

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
        """One nested object, or None when the key is absent or its value is refused.

        A ``null`` is refused rather than read as "the default": a file that states a key states
        something, and "no financing block" is written by leaving the key out.

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
class StagedParameters:
    """A parsed ``--parameters`` file of ``python -m hisim.economics staged``.

    What the staged command needs out of one file: the engine parameter record to price with, the
    perspective the caller named, the financing and subsidy overrides they asked for, and every
    fault found along the way. :attr:`parameters` is ``None`` exactly when :attr:`problems` is
    non-empty — a refused file produces no half-built assumption set — and the CLI turns the
    problem list into ``problems.json`` with exit 2.

    Accepted keys are :class:`ParameterKeys`; two of them are accepted and ignored.
    :attr:`ParameterKeys.SIMULATION_YEAR` is a fact of the stages rather than an assumption, and
    :attr:`ParameterKeys.SUBSIDY_CATALOG` documents which catalogue produced a document while the
    catalogue actually used comes from the shipped directory or from ``--subsidy-catalog``. Both
    are accepted so that a document's own ``parameters`` block is a legal input file.

    Example::

        parsed = StagedParameters.from_mapping({"horizon_years": 20}, stored=stage_parameters)
        parameters, priced_under = parsed.applied_to(perspective)

    Args:
        parameters: The engine record to price with, or None when the file was refused.
        perspective_id: The perspective the file named, or None when it named none.
        financing: The financing plan the file asked for: a plan for ``loan``, None for ``cash``
            *and* None when the file said nothing — :attr:`financing_given` tells the two apart.
        financing_given: Whether the file stated :attr:`ParameterKeys.FINANCING` at all.
        subsidy_mode: The subsidy mode the file asked for, or None when it said nothing.
        problems: Every fault found, in the order they were found.
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

        The whole input contract of ``staged`` in one call. Every accepted key of
        :class:`ParameterKeys` is optional: what a file does not state is what the stages were
        priced under (``stored``), and what the stages do not state either is the engine's
        documented default — with one exception, the country, which has no default here at all.

        The country rule (step 13 §1.2) is the reason this function takes ``stored`` rather than
        just a mapping. The plan's country is the one the stages were priced under; a ``country``
        in the file must equal it or the run is refused naming both; a file without ``country``
        over stages without a stored one is refused too. ``price_basis_year`` follows the same
        rule, because the stored inputs were priced at the stages' basis year. Nothing here ever
        substitutes ``"DE"``.

        Example::

            StagedParameters.from_mapping({"country": "IE"}, stored=None).parameters.country == "IE"

        Args:
            raw: The parsed ``--parameters`` document; anything that is not a JSON object is one
                problem rather than an exception.
            stored: The parameters the stages were priced under (``read_stored_parameters`` of the
                first stage that carries a stored evaluation), or None when no stage carries any.
            stored_country: The one country the stages were priced for, resolved by the caller
                over every source a stage has (its stored evaluation *and* its stored inputs,
                which carry the country since step 13). None when no stage states one; when None
                and ``stored`` is given, ``stored.country`` is used, since a stored evaluation is
                itself a stage's statement of its country.
            stored_price_basis_year: The one price basis year the stages were priced at, resolved
                the same way over the same two sources, with the same fallback to ``stored``.

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

        reader = ParameterReader(raw, ParameterKeys.ROOT_PATH, ParameterKeys.ACCEPTED, problems)
        reader.refuse_unknown_keys()
        overrides: Dict[str, Any] = {}
        cls._read_horizon_and_interest(reader, overrides)
        cls._read_country(reader, cls._stages_country(stored, stored_country), overrides)
        cls._read_price_basis_year(
            reader, cls._stages_price_basis_year(stored, stored_price_basis_year), overrides
        )
        cls._read_escalation(reader, problems, overrides)
        perspective_id = reader.text(ParameterKeys.PERSPECTIVE_ID)
        subsidy_mode = cls._read_subsidy_mode(reader)
        financing_given, financing = cls._read_financing(reader, problems)

        if problems:
            return cls(
                parameters=None,
                perspective_id=perspective_id,
                financing=financing,
                financing_given=financing_given,
                subsidy_mode=subsidy_mode,
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
        )

    @classmethod
    def _read_horizon_and_interest(cls, reader: ParameterReader, overrides: Dict[str, Any]) -> None:
        """Read ``horizon_years`` and ``interest_rate`` onto the two engine fields they rename.

        Both bounds are the engine record's own (a horizon below one year and an interest rate at
        or below -100 % make the annuity and discounting formulas meaningless), checked here so the
        caller is told which key is wrong instead of receiving the record's bare ``ValueError``.

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
        """The country the stages were priced for, out of the caller's two ways of saying it.

        Args:
            stored: The stage parameters, whose ``country`` is itself a stage's statement.
            stored_country: The country the caller resolved over every stage source, if any.

        Returns:
            The resolved country, or None when no stage states one.
        """
        if stored_country is not None:
            return stored_country
        return stored.country if stored is not None else None

    @classmethod
    def _read_country(
        cls, reader: ParameterReader, stored_country: Optional[str], overrides: Dict[str, Any]
    ) -> None:
        """Resolve the country against the stages, with no default anywhere.

        The plan's price data and subsidy catalogue follow one country, and it is the one the
        stages were priced under. A file may repeat it — that is a useful assertion — but may not
        change it: a plan is not re-priceable into another country's prices, and the run that
        silently did so published German costs for an Irish house.

        Args:
            reader: The top-level block's reader.
            stored_country: The country the stages were priced for, or None when none states one.
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
        """The price basis year the stages were priced at, out of the caller's two ways of saying it.

        Args:
            stored: The stage parameters, whose ``price_basis_year`` is itself a stage's statement
                — the *resolved* one, since a stored evaluation records what it actually used.
            stored_price_basis_year: The year the caller resolved over every stage source, if any.

        Returns:
            The resolved year, or None when no stage states one.
        """
        if stored_price_basis_year is not None:
            return stored_price_basis_year
        return stored.price_basis_year if stored is not None else None

    @classmethod
    def _read_price_basis_year(
        cls, reader: ParameterReader, stored_year: Optional[int], overrides: Dict[str, Any]
    ) -> None:
        """Resolve the price basis year against the stages, with no re-derivation anywhere.

        The stages were priced at one price level and a plan out of them is priced at that same
        level: a file may repeat the year but not change it, because the stored inputs cannot be
        re-based without being re-run. A ``null`` says nothing and takes the stages', so a
        document whose block is fed back in round trips.

        When no stage states a year, the run is **refused** rather than re-derived from the
        simulation year. Re-deriving is the same class of silent difference as a defaulted
        country: it produces a complete-looking plan priced at a year none of its runs used, and
        nothing downstream can tell. A caller who has only such stages — extracts written before
        the key existed — names ``price_basis_year`` in the file, or re-runs the jobs.

        Args:
            reader: The top-level block's reader.
            stored_year: The year the stages were priced at, or None when none states one.
            overrides: The engine-field overrides being assembled; written in place.
        """
        states_a_year = (
            reader.has(ParameterKeys.PRICE_BASIS_YEAR)
            and reader.raw[ParameterKeys.PRICE_BASIS_YEAR] is not None
        )
        if not states_a_year:
            if stored_year is None:
                reader.refuse(
                    ParameterKeys.PRICE_BASIS_YEAR,
                    ParameterProblemCodes.MISSING,
                    "no stage says which price basis year it was priced at — neither its stored "
                    "evaluation nor its stored inputs — and re-deriving one from the simulation "
                    "year would price the plan at a level none of its runs used: name "
                    "`price_basis_year` in the --parameters file, or re-run the jobs.",
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
        """Read ``subsidy_mode``, the two-valued input spelling of "apply the catalogue".

        Args:
            reader: The top-level block's reader.

        Returns:
            The mode, or None when the file states none — in which case the perspective's own
            subsidy mode stands.
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
        """Read the ``financing`` block: a cash purchase, or one annuity loan per buying stage.

        The three loan fields are refused on a cash purchase rather than ignored: a file stating
        ``{"kind": "cash", "term_in_years": 15}`` means something the engine cannot do, and
        dropping the term would price a plan the caller did not describe.

        Args:
            reader: The top-level block's reader.
            problems: The shared problem list, for the nested block's own reader.

        Returns:
            ``(whether the file stated the key, the plan)``; the plan is None both for a cash
            purchase and for a refused block, which is why the flag is returned beside it.
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
        """Build the annuity loan plan out of a ``financing`` block of kind ``loan``.

        Every field the input vocabulary does not carry — the repayment shape, a soft-loan scheme
        id, a repayment grant — keeps the engine's default, because those are decided by the
        subsidy catalogue rather than by a caller.

        Args:
            nested: The financing block's own reader, already checked for unknown keys.

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
        """Read the ``escalation`` block onto the four escalation fields of the engine record.

        ``energy`` is a mapping of carrier to rate, keyed the way the document writes it (the
        :class:`~hisim.economics.carriers.EnergyCarrier` member value); an empty mapping is the
        statement "no per-carrier override", so every carrier falls back to the country's defaults
        file and then to the general rate.

        Args:
            reader: The top-level block's reader.
            problems: The shared problem list, for the nested block's own reader.
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
            rate = energy.number(spelling, above=-1.0)
            if rate is not None:
                rates[EnergyCarrier(spelling)] = rate
        overrides["energy_price_escalation_rates"] = rates

    @classmethod
    def reconciled_perspective_id(
        cls, in_file: Optional[str], on_command_line: Optional[str], problems: List[ParameterProblem]
    ) -> str:
        """The one perspective id, out of the file's and the command line's.

        Both spellings exist because the backend hands the frontend's economics block through
        verbatim while the CLI has had a ``--perspective`` flag since before that block did. Two
        sources that disagree have no right answer, so the run is refused rather than one of them
        silently winning.

        Args:
            in_file: :attr:`ParameterKeys.PERSPECTIVE_ID` as the file stated it, or None.
            on_command_line: The ``--perspective`` flag, or None.
            problems: The shared problem list; a disagreement is appended to it.

        Returns:
            The id to price under: whichever source named one, or
            :attr:`DEFAULT_PERSPECTIVE_ID` when neither did. On a disagreement the file's id is
            returned, but the run is refused by the problem that was appended.
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
        """The engine record and the perspective the plan is actually priced under.

        Two of the accepted keys are properties of the *perspective* rather than of the parameter
        record — ``financing`` and ``subsidy_mode`` — so they are applied here, by copying the
        bundle's perspective with those two dimensions replaced. ``apply_subsidies`` on the record
        is then set from whichever subsidy mode ends up in force, which is what keeps the
        document's echo of it a statement about the run rather than about the file.

        Args:
            perspective: The perspective named by id, as the shipped bundle defines it.

        Returns:
            ``(parameters, perspective)``, both ready to hand to the staged evaluator.

        Raises:
            ValueError: If the file was refused, i.e. this result carries problems and no
                parameters. Calling this then is a programming error in the CLI.
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
        """How a document spells the subsidy mode a perspective actually ran under.

        The document's vocabulary has two values, so this says whether the catalogue was applied at
        all. The engine's two list-carrying kinds (``ONLY`` and ``EXCLUDE``, which the shipped
        bundle does not use and no parameter file can ask for) admit some schemes and are therefore
        reported as ``full``.

        Args:
            perspective: The perspective the plan was priced under.

        Returns:
            :attr:`SubsidyModeName.NONE` when nothing was admitted, :attr:`SubsidyModeName.FULL`
            otherwise.
        """
        if perspective.subsidy_mode.kind is SubsidyModeKind.NONE:
            return SubsidyModeName.NONE
        return SubsidyModeName.FULL

    @classmethod
    def financing_block(cls, plan: Optional[FinancingPlan]) -> Dict[str, Any]:
        """How a document states the financing a plan ran under.

        Args:
            plan: The perspective's financing plan, or None for a cash purchase.

        Returns:
            ``{"kind": "cash"}``, or ``{"kind": "loan", …}`` with the three loan fields the input
            vocabulary carries. A plan's repayment shape and any soft-loan scheme behind it are
            not stated: they are the catalogue's, not the caller's.
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
        simulation_year: Optional[int],
        subsidy_catalog: Optional[str],
    ) -> Dict[str, Any]:
        """The ``parameters`` block ``economics_result.json`` publishes.

        The output half of the one table of keys: every key here is one
        :meth:`from_mapping` accepts, so a reader can copy this block out of a document, hand it
        back as ``--parameters`` over the same stages, and get the same run. The two keys that are
        statements about the stages rather than assumptions — ``simulation_year`` and
        ``subsidy_catalog`` — are published for the reader and ignored when read back.

        Args:
            parameters: The engine record the plan was priced with.
            perspective: The perspective it was priced under, after any override was applied.
            simulation_year: The calendar year of the stages' simulations, or None.
            subsidy_catalog: The catalogue id in force, or None when the plan ran with none.

        Returns:
            The block, with the keys in the order the document writes them.
        """
        return {
            ParameterKeys.HORIZON_YEARS: parameters.observation_period_in_years,
            ParameterKeys.INTEREST_RATE: parameters.interest_rate,
            ParameterKeys.COUNTRY: parameters.country,
            ParameterKeys.PRICE_BASIS_YEAR: parameters.price_basis_year,
            ParameterKeys.SIMULATION_YEAR: simulation_year,
            ParameterKeys.PERSPECTIVE_ID: perspective.id,
            ParameterKeys.SUBSIDY_MODE: cls.subsidy_mode_of(perspective).value,
            ParameterKeys.FINANCING: cls.financing_block(perspective.financing),
            ParameterKeys.ESCALATION: {
                ParameterKeys.ESCALATION_GENERAL: parameters.general_price_escalation_rate,
                ParameterKeys.ESCALATION_INVESTMENT: parameters.investment_price_escalation_rate,
                ParameterKeys.ESCALATION_FEED_IN: parameters.feed_in_escalation_rate,
                ParameterKeys.ESCALATION_ENERGY: {
                    carrier.value: rate
                    for carrier, rate in sorted(
                        parameters.energy_price_escalation_rates.items(), key=lambda item: item[0].value
                    )
                },
            },
            ParameterKeys.SUBSIDY_CATALOG: subsidy_catalog,
        }
