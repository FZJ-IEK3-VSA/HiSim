"""The wrapper that makes every number in ``result.json`` say where it came from.

Decision Q21 of ``roadmap/renovisor/challenges.md`` §9 settled the shape of a RenoVisor result:
not a bare number per field, but an object carrying the number, how it was obtained, and what it
was obtained from. A frontend that shows a heat-pump package next to a gas boiler has to be able
to tell a simulated kilowatt-hour from a constant standing in for a model nobody has written yet,
and a reviewer has to be able to find the line of HiSim that produced a figure without reading
the translation layer::

    from hisim.renovisor.provenance import ProvenancedValue, Range
    from hisim.renovisor.vocabulary import Provenance

    ProvenancedValue(
        value=1234.5,
        provenance=Provenance.SIMULATED,
        source="all_kpis.json: 'Purchased energy consumption for simulated period'",
    ).to_json()
    # {'value': 1234.5, 'provenance': 'SIMULATED', 'source': "all_kpis.json: 'Purchased …'"}

Three types live here and nothing else does. :class:`Period` states which stretch of the year a
value was measured over and what share of a year that is, so a one-day run's annual figure is
visibly an extrapolation (requirement A14). :class:`Range` is the ``{low, best_estimate, high}``
triple every monetary figure carries -- one value of one :class:`ProvenancedValue`, never three
separate fields, because a band with three provenances would be three different claims about one
number (decision A9/AC9, spelling per C3). :class:`MissingField` is the entry a field makes in
``result.json["missing"]`` when it cannot be produced at all: decision R8 forbids inventing a
placeholder, so an absent field is absent and says why.
"""

import datetime
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Mapping, Optional, Union

from hisim.renovisor.vocabulary import Provenance


@dataclass(frozen=True)
class Period:
    """The stretch of simulated time a value belongs to, and its share of a year.

    A RenoVisor calculation may be asked to run one January day (requirement A18 leaves the period
    to the caller), while the contract's KPIs are annual. Carrying the period beside the value is
    what lets a reader see that a "per year" figure came from 0.27 % of a year::

        Period.from_dates(datetime(2021, 1, 1), datetime(2021, 1, 2)).fraction_of_year
        # 0.0027379…

    Args:
        start: The simulation's start, as an ISO 8601 string.
        end: The simulation's end, as an ISO 8601 string.
        fraction_of_year: How much of a year the period is, ``(end - start) / 365.25 days``.
    """

    #: The length of a year used to turn a period into a fraction. The Julian year (365.25 days)
    #: rather than a flat 365, so that a full calendar year and a full leap year both land within
    #: a day and a half of 1.0 instead of one of them landing at 1.0027.
    DAYS_PER_YEAR: ClassVar[float] = 365.25

    start: str
    end: str
    fraction_of_year: float

    @classmethod
    def from_dates(cls, start: datetime.datetime, end: datetime.datetime) -> "Period":
        """Build a period from the two dates a simulation ran between.

        Args:
            start: The first simulated instant.
            end: The instant the simulation stopped at.

        Returns:
            The period, with ``fraction_of_year`` computed from :attr:`DAYS_PER_YEAR`.
        """
        days = (end - start).total_seconds() / (24.0 * 3600.0)
        return cls(start=start.isoformat(), end=end.isoformat(), fraction_of_year=days / cls.DAYS_PER_YEAR)

    @classmethod
    def from_parameters(cls, simulation_parameters: Any) -> "Period":
        """Build a period from a run's :class:`hisim.simulationparameters.SimulationParameters`.

        Args:
            simulation_parameters: The parameters the calculation ran with; only ``start_date``
                and ``end_date`` are read.

        Returns:
            The period of that run.
        """
        return cls.from_dates(simulation_parameters.start_date, simulation_parameters.end_date)

    def is_full_year(self, tolerance: float = 0.01) -> bool:
        """Return whether the period covers a year, within *tolerance*.

        A value measured over a full year needs no extrapolation and is therefore ``SIMULATED``
        rather than ``PARTIAL``; a shorter one is scaled and says so.

        Args:
            tolerance: How far from 1.0 the fraction may be and still count as a year. The
                default admits the day and a half by which a calendar year differs from a Julian
                one.

        Returns:
            ``True`` when the period is a year to within *tolerance*.
        """
        return abs(self.fraction_of_year - 1.0) <= tolerance

    def to_json(self) -> Dict[str, Any]:
        """Return the period as the JSON object ``result.json`` carries."""
        return {"start": self.start, "end": self.end, "fraction_of_year": self.fraction_of_year}


@dataclass(frozen=True)
class Range:
    """A figure with a low, a best estimate and a high bound, in HiSim's own slot names.

    Every euro figure in the payload is one of these, because the cost engine computes every
    number in three worlds at once and publishing only the middle one would throw away the part
    that says how much the middle one is worth (cost_spec §3.9). The slot names are HiSim's --
    ``low``, ``best_estimate``, ``high`` -- per decision C3; the cost engine spells the same three
    ``min``, ``best_estimate``, ``max`` internally, and :meth:`from_uncertain_value` is the one
    place that translation happens::

        Range.from_uncertain_value({"min": 1.0, "best_estimate": 2.0, "max": 4.0})
        Range.from_uncertain_value(7.0)          # a degenerate band: 7, 7, 7

    Args:
        low: The value in the optimistic world.
        best_estimate: The published middle figure.
        high: The value in the pessimistic world.
    """

    #: The key the cost engine's ``UncertainValue.to_json`` writes the low slot under.
    ENGINE_LOW_KEY: ClassVar[str] = "min"

    #: The key it writes the middle slot under; the same word HiSim's own spelling uses.
    ENGINE_BEST_KEY: ClassVar[str] = "best_estimate"

    #: The key it writes the high slot under.
    ENGINE_HIGH_KEY: ClassVar[str] = "max"

    low: float
    best_estimate: float
    high: float

    @classmethod
    def zero(cls) -> "Range":
        """Return the all-zero range, the neutral element of :meth:`plus`."""
        return cls(low=0.0, best_estimate=0.0, high=0.0)

    @classmethod
    def exact(cls, value: float) -> "Range":
        """Return the degenerate range whose three slots are all *value*."""
        return cls(low=value, best_estimate=value, high=value)

    @classmethod
    def from_bounds(cls, low: float, high: float) -> "Range":
        """Return the range spanning *low* to *high* with the midpoint as the best estimate.

        The material database gives a minimum and a maximum price per cubic metre and no third
        number, so the midpoint is the only best estimate available; decision Q8/Q9 leaves the
        real figure to the materials team's total-cost column.

        Args:
            low: The lower bound.
            high: The upper bound.

        Returns:
            The range, with ``best_estimate = (low + high) / 2``.
        """
        return cls(low=low, best_estimate=0.5 * (low + high), high=high)

    @classmethod
    def from_uncertain_value(cls, raw: Any) -> Optional["Range"]:
        """Build a range from the cost engine's serialized ``UncertainValue``.

        The engine writes a band as ``{"min": …, "best_estimate": …, "max": …}`` and a certain
        figure as a bare number, so both spellings have to be read.

        Args:
            raw: One value out of ``lifecycle_costs.json``.

        Returns:
            The range, or ``None`` when *raw* is ``None`` or is neither a number nor a band
            object -- an absent engine figure must become an absent field, never a zero.
        """
        if raw is None or isinstance(raw, bool):
            return None
        if isinstance(raw, (int, float)):
            return cls.exact(float(raw))
        if isinstance(raw, Mapping):
            try:
                return cls(
                    low=float(raw[cls.ENGINE_LOW_KEY]),
                    best_estimate=float(raw[cls.ENGINE_BEST_KEY]),
                    high=float(raw[cls.ENGINE_HIGH_KEY]),
                )
            except (KeyError, TypeError, ValueError):
                return None
        return None

    def plus(self, other: "Range") -> "Range":
        """Return the slot-wise sum of this range and *other*.

        Slot-wise is the cost engine's own convention: the optimistic world's total is the sum of
        the optimistic figures, so each of the three worlds stays internally consistent.

        Args:
            other: The range to add.

        Returns:
            The sum.
        """
        return Range(
            low=self.low + other.low,
            best_estimate=self.best_estimate + other.best_estimate,
            high=self.high + other.high,
        )

    def scaled(self, factor: float) -> "Range":
        """Return this range with every slot multiplied by *factor*.

        Args:
            factor: The multiplier, e.g. ``1 / 12`` to turn an annual cost into a monthly one.

        Returns:
            The scaled range.
        """
        return Range(low=self.low * factor, best_estimate=self.best_estimate * factor, high=self.high * factor)

    def to_json(self) -> Dict[str, float]:
        """Return the range in the contract's own slot names."""
        return {"low": self.low, "best_estimate": self.best_estimate, "high": self.high}


@dataclass(frozen=True)
class ProvenancedValue:
    """One leaf of ``result.json``: a value, where it came from, and over which period.

    Every entry of the payload's ``kpis`` and ``costs`` blocks is one of these (decision Q21). The
    four fields answer four different questions a reader has, and none of them can be derived from
    the others: *what is the number*, *may I act on it*, *which HiSim figure is it*, and *how much
    of a year was actually simulated to produce it*.

    Args:
        value: The figure itself. A plain number for a scalar KPI, a :class:`Range` for a
            monetary figure, a dictionary for a structured mocked value such as the disruption
            days, a string for a graded assessment, and ``None`` for a field that exists in the
            contract but has no model behind it (decision A12).
        provenance: ``SIMULATED`` when HiSim computed it from the run, ``MOCKED`` when it is a
            constant standing in for a missing model, ``PARTIAL`` when a real computation used at
            least one estimated or extrapolated input.
        source: Where it came from, in enough detail to find it: the HiSim KPI name, the cost
            engine field, the contract schema path the mocked constant was read from, or the
            sentence explaining why there is no model.
        period: The simulated period behind the value, for figures that are period-bound; ``None``
            for a rate, a constant or an investment, none of which a period would say anything
            about.
    """

    value: Union[float, int, str, None, Range, Dict[str, Any]]
    provenance: Provenance
    source: str
    period: Optional[Period] = None

    def to_json(self) -> Dict[str, Any]:
        """Return the value as the JSON object ``result.json`` carries.

        A :class:`Range` value is written out as its own ``{low, best_estimate, high}`` object
        here, which is the one place the triple becomes JSON, so a band can never be published as
        three sibling fields.

        Returns:
            ``{"value": …, "provenance": …, "source": …}``, with ``"period"`` added when this
            value carries one.
        """
        value = self.value.to_json() if isinstance(self.value, Range) else self.value
        document: Dict[str, Any] = {
            "value": value,
            "provenance": self.provenance.value,
            "source": self.source,
        }
        if self.period is not None:
            document["period"] = self.period.to_json()
        return document


@dataclass(frozen=True)
class MissingField:
    """One entry of ``result.json["missing"]``: a contract field this run could not fill.

    Decision R8 forbids publishing a placeholder for a figure nobody computed, so a field with no
    source is simply not in the payload. That would leave a frontend guessing whether the field is
    gone or the run failed, which is what this record answers: it names the field by its full path
    in the payload and states, in one sentence, what is missing and where the decision that leaves
    it missing is written down.

    Args:
        field: The dotted path of the absent field, e.g. ``"costs.grant_in_euro"``.
        reason: One sentence saying why it is absent, naming the decision or the missing input.
    """

    field: str
    reason: str

    def to_json(self) -> Dict[str, str]:
        """Return the entry as the JSON object ``result.json`` carries."""
        return {"field": self.field, "reason": self.reason}
