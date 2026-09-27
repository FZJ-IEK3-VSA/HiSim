"""The reader's quoted price per measure: ``investment_overrides`` (renovisorissues #53).

A homeowner with a quote ("EUR 11,800 for the air-source heat pump, installed") re-prices a plan
without a new simulation. The owner's rules (2026-09-26) are what these cases pin:

* the quote replaces the year-0 investment of the measure's **main** subject in its stage; the
  measure's other subjects there are bought at zero, and keep their lifetimes and their later,
  database-priced replacements;
* subsidies computed on eligible cost follow the quote, a lump sum does not;
* the quote is exact, and an unpriced measure becomes priced by it;
* the document echoes the quotes and says on each row where its investment came from.

The parameter block is checked on its own, the pricing on the synthetic plan of
``synthetic_stages``, and the refusals through the ``staged`` command, whose exit codes a backend
branches on.
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

import pytest

from hisim.economics.__main__ import StagedCli, main
from hisim.economics.evaluator import SubjectCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.staged import InvestmentOrigin, InvestmentOverride, StagedEvaluator, StagedResult
from hisim.economics.staged_document import StagedDocument
from hisim.economics.staged_parameters import ParameterKeys, StagedParameters, StatedQuote
from hisim.economics.subsidies import BenefitKind, LumpSumBenefit, PayoutKind
from hisim.economics.timeline import CostCategory
from hisim.loadtypes import ComponentType, Units
from hisim.renovisor.economics import MainSubjectError, MainSubjects, MeasureSubjects
from hisim.renovisor.request import CatalogueTable

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    always_eligible_catalog,
    always_eligible_scheme,
    baseline_stage,
    brownfield_perspective,
    device_facts,
    envelope_stage,
    heat_pump_stage,
    synthetic_catalog,
    write_database,
)
# `fixture_workspace` is the `workspace` fixture of the command's cases, reused as it is.
from tests.economics.test_staged_cli import _arguments, fixture_workspace  # noqa: F401 # pylint: disable=unused-import

pytestmark = pytest.mark.base

#: The quote of the issue's own example, in euro.
QUOTE = 11800.0

#: Its source, as a reader would state it.
SOURCE = "installer quote of 2026-09-20"

#: The buffer a heating_system measure installs beside the generator.
BUFFER_SUBJECT = "SimpleHotWaterStorage"

#: The buffer's database-like price, as an override of the synthetic fixture.
BUFFER_INVESTMENT_IN_EURO = 1000.0

#: The measure HiSim holds no price for.
LAGGING = "hot_water_tank_and_pipe_insulation"


def _quote(stage: int, measure_id: str, amount: float = QUOTE, source: str = SOURCE) -> Dict[str, Any]:
    """One ``investment_overrides`` entry, in the file's shape."""
    return {
        ParameterKeys.OVERRIDE_STAGE: stage,
        ParameterKeys.OVERRIDE_MEASURE_ID: measure_id,
        ParameterKeys.OVERRIDE_AMOUNT: amount,
        ParameterKeys.OVERRIDE_SOURCE: source,
    }


def _parse(block: Dict[str, Any]) -> StagedParameters:
    """Parse a block over stages that state the synthetic country and year."""
    return StagedParameters.from_mapping(
        block, stored=None, stored_country=SyntheticPlan.COUNTRY, stored_price_basis_year=SyntheticPlan.YEAR
    )


def _codes(parsed: StagedParameters) -> List[str]:
    """The problem codes of a parsed block."""
    return [problem.code for problem in parsed.problems]


class TestTheParameterBlock:
    """``investment_overrides`` is read, echoed and refused by name, every fault at once."""

    def test_a_quote_is_read_and_echoed_as_it_was_stated(self) -> None:
        """The document's block states the quote in the shape the file does, so it reads back."""
        parsed = _parse({ParameterKeys.INVESTMENT_OVERRIDES: [_quote(1, "heating_system")]})
        assert not parsed.problems
        assert parsed.investment_overrides == (
            StatedQuote(stage=1, measure_id="heating_system", amount_in_euro=QUOTE, source=SOURCE),
        )
        assert parsed.parameters is not None
        block = StagedParameters.to_document_block(
            parameters=parsed.parameters,
            perspective=brownfield_perspective(),
            weather_year=None,
            subsidy_catalog=None,
            investment_overrides=[quote.to_json() for quote in parsed.investment_overrides],
        )
        assert block[ParameterKeys.INVESTMENT_OVERRIDES] == [_quote(1, "heating_system")]
        assert _parse(block).investment_overrides == parsed.investment_overrides

    def test_without_the_key_there_is_no_quote_and_the_echo_is_empty(self) -> None:
        """No quote is stated as an empty list, not as a missing key."""
        parsed = _parse({})
        assert parsed.investment_overrides == ()
        assert parsed.parameters is not None
        block = StagedParameters.to_document_block(parsed.parameters, brownfield_perspective(), None, None)
        assert block[ParameterKeys.INVESTMENT_OVERRIDES] == []

    @pytest.mark.parametrize(
        "entry, code",
        [
            (
                {**_quote(1, "heating_system"), "amount_in_euro": 0},
                "parameters.investment_overrides.amount_in_euro.invalid",
            ),
            (
                {**_quote(1, "heating_system"), "amount_in_euro": {"min": 1.0, "best": 2.0, "max": 3.0}},
                "parameters.investment_overrides.amount_in_euro.invalid",
            ),
            ({**_quote(1, "heating_system"), "stage": -1}, "parameters.investment_overrides.stage.invalid"),
            ({**_quote(1, "heating_system"), "stage": True}, "parameters.investment_overrides.stage.invalid"),
            ({**_quote(1, "heating_system"), "source": " "}, "parameters.investment_overrides.source.invalid"),
            ({**_quote(1, "heating_system"), "measure_id": 7}, "parameters.investment_overrides.measure_id.invalid"),
            ({**_quote(1, "heating_system"), "vat": 0.2}, "parameters.investment_overrides.unknown_key"),
            (
                {key: value for key, value in _quote(1, "heating_system").items() if key != "source"},
                "parameters.investment_overrides.source.missing",
            ),
            ("heating_system", "parameters.investment_overrides.invalid"),
        ],
    )
    def test_a_malformed_entry_is_refused_by_the_key_it_is_wrong_in(self, entry: Any, code: str) -> None:
        """Each fault names its key in the code and its entry in the path."""
        parsed = _parse({ParameterKeys.INVESTMENT_OVERRIDES: [_quote(0, "battery_system"), entry]})
        assert parsed.parameters is None
        assert _codes(parsed) == [code]
        assert parsed.problems[0].path.startswith("parameters.investment_overrides[1]")

    def test_a_block_that_is_not_a_list_is_refused(self) -> None:
        """The key holds a list of quotes."""
        parsed = _parse({ParameterKeys.INVESTMENT_OVERRIDES: _quote(1, "heating_system")})
        assert _codes(parsed) == ["parameters.investment_overrides.invalid"]

    def test_a_second_quote_for_one_measure_of_one_stage_is_refused(self) -> None:
        """One measure of one stage has one quote; the same measure in another stage is fine."""
        parsed = _parse(
            {
                ParameterKeys.INVESTMENT_OVERRIDES: [
                    _quote(1, "heating_system"),
                    _quote(2, "heating_system"),
                    _quote(1, "heating_system", amount=9000.0),
                ]
            }
        )
        assert _codes(parsed) == ["parameters.investment_overrides.duplicate"]
        assert parsed.problems[0].path == "parameters.investment_overrides[2]"

    def test_every_fault_is_reported_at_once(self) -> None:
        """Three bad entries, three rows."""
        parsed = _parse(
            {
                ParameterKeys.INVESTMENT_OVERRIDES: [
                    {**_quote(1, "heating_system"), "amount_in_euro": -1},
                    {**_quote(1, "battery_system"), "stage": "one"},
                    {**_quote(1, "photovoltaic_system"), "source": ""},
                ]
            }
        )
        assert len(parsed.problems) == 3


class TestTheQuotesAgainstTheStages:
    """What needs the stages: the stage exists, carries the measure out, and the measure costs."""

    STAGES = [(), ("external_insulation", LAGGING, "change_room_temperature"), ("heating_system",)]

    def _check(self, *quotes: StatedQuote) -> List[str]:
        return [
            problem.code
            for problem in StagedParameters.check_quotes(
                quotes, self.STAGES, tuple(MeasureSubjects.COSTLESS), CatalogueTable.ids()
            )
        ]

    def test_a_priced_measure_the_stage_carries_out_is_accepted(self) -> None:
        """A heat pump in its stage, an envelope measure in its stage, the unpriced lagging."""
        assert not self._check(
            StatedQuote(2, "heating_system", QUOTE, SOURCE),
            StatedQuote(1, "external_insulation", QUOTE, SOURCE, position=1),
            StatedQuote(1, LAGGING, 400.0, SOURCE, position=2),
        )

    def test_a_stage_the_plan_does_not_have_is_refused(self) -> None:
        """Stage 3 of a three-stage plan."""
        assert self._check(StatedQuote(3, "heating_system", QUOTE, SOURCE)) == [
            "parameters.investment_overrides.stage.unknown"
        ]

    def test_a_measure_the_stage_does_not_carry_out_is_refused(self) -> None:
        """The heat pump is stage 2's, not stage 1's."""
        assert self._check(StatedQuote(1, "heating_system", QUOTE, SOURCE)) == [
            "parameters.investment_overrides.measure_id.not_in_stage"
        ]

    def test_a_costless_measure_is_refused(self) -> None:
        """Changing the set point buys nothing, so no price can be replaced."""
        assert self._check(StatedQuote(1, "change_room_temperature", 50.0, SOURCE)) == [
            "parameters.investment_overrides.measure_id.costless"
        ]

    def test_a_measure_the_catalogue_does_not_have_is_refused(self) -> None:
        """An id outside the catalogue is refused with the ids that exist."""
        problems = StagedParameters.check_quotes(
            [StatedQuote(2, "heat_pump", QUOTE, SOURCE)], self.STAGES, (), CatalogueTable.ids()
        )
        assert [problem.code for problem in problems] == ["parameters.investment_overrides.measure_id.invalid"]
        assert "heating_system" in (problems[0].accepted or ())

    def test_a_later_stage_carries_out_only_the_measures_new_in_it(self) -> None:
        """A RenoVisor stage lists every measure of its package; only the new ones are its own."""
        stages = [
            replace(baseline_stage(), measures=()),
            replace(envelope_stage(0), measures=("external_insulation",)),
            replace(heat_pump_stage(4), measures=("external_insulation", "heating_system")),
        ]
        assert StagedCli.carried_out_by_stage(stages) == [(), ("external_insulation",), ("heating_system",)]


class TestTheMainSubject:
    """``MainSubjects`` decides which subject a quote prices, and never guesses."""

    def test_the_generator_is_the_main_subject_of_heating_system_and_the_buffer_is_not(self) -> None:
        """The buffer the measure installs with the generator is a further subject."""
        main_subject, others = MainSubjects.resolve(
            "heating_system",
            1,
            {"HeatPump": ComponentType.HEAT_PUMP, BUFFER_SUBJECT: ComponentType.SPACE_HEATING_STORAGE},
        )
        assert (main_subject, others) == ("HeatPump", (BUFFER_SUBJECT,))

    def test_an_envelope_measure_and_the_lagging_are_their_own_subjects(self) -> None:
        """Named by the measure id, with or without cost facts."""
        assert MainSubjects.resolve(
            "external_insulation", 1, {"external_insulation": ComponentType.WALL_EXTERNAL_INSULATION}
        ) == ("external_insulation", ())
        assert MainSubjects.resolve(LAGGING, 1, {LAGGING: None}) == (LAGGING, ())

    def test_a_measure_with_no_matching_subject_is_an_error_not_a_guess(self) -> None:
        """Zero or two candidates, or a measure the table does not know, raise."""
        with pytest.raises(MainSubjectError):
            MainSubjects.resolve("heating_system", 1, {BUFFER_SUBJECT: ComponentType.SPACE_HEATING_STORAGE})
        with pytest.raises(MainSubjectError):
            MainSubjects.resolve(
                "heating_system", 1, {"HeatPump": ComponentType.HEAT_PUMP, "Boiler": ComponentType.GAS_HEATER}
            )
        with pytest.raises(MainSubjectError):
            MainSubjects.resolve("external_insulation", 1, {"facade": ComponentType.WALL_EXTERNAL_INSULATION})
        with pytest.raises(MainSubjectError):
            MainSubjects.resolve("change_room_temperature", 1, {"change_room_temperature": None})

    def test_every_catalogue_measure_the_translator_acts_on_has_a_main_subject_or_costs_nothing(self) -> None:
        """Build failure, not a guess: a measure the translator can act on is in the table.

        A catalogue measure is either declared here -- named by its id, or by its main subject's
        asset classes -- or costs nothing, or is one the translator does not act on at all (a
        whole-measure ``not_implemented_yet.yaml`` entry). A new measure the translator starts to
        act on fails this test until its main subject is declared.
        """
        from hisim.renovisor.whitelist import Whitelist  # pylint: disable=import-outside-toplevel

        not_acted_on = {
            entry.item
            for entry in Whitelist.load().entries()
            if entry.kind.value == "measure" and "." not in entry.item and entry.when is None
        }
        declared = MainSubjects.NAMED_BY_MEASURE | set(MainSubjects.BY_ASSET_CLASS)
        for measure_id in CatalogueTable.ids():
            homes = [
                measure_id in declared,
                measure_id in MeasureSubjects.COSTLESS,
                measure_id in not_acted_on,
            ]
            assert homes.count(True) == 1, f"{measure_id}: {homes}"
        assert not MainSubjects.NAMED_BY_MEASURE & set(MainSubjects.BY_ASSET_CLASS)


# ---------------------------------------------------------------------------------------- pricing


def _parameters() -> EconomicParameters:
    """The synthetic plan's assumptions."""
    return EconomicParameters(
        observation_period_in_years=SyntheticPlan.HORIZON,
        interest_rate=SyntheticPlan.INTEREST_RATE,
        country=SyntheticPlan.COUNTRY,
        price_basis_year=SyntheticPlan.YEAR,
        co2_price_scenario="none",
    )


def _stages() -> list:
    """The synthetic plan, with a buffer bought beside the heat pump and the cylinder lagged."""
    heat_pump = heat_pump_stage(0)
    buffer = SubjectCostFacts(
        BUFFER_SUBJECT,
        replace(
            device_facts(ComponentType.SPACE_HEATING_STORAGE, 500.0, BUFFER_INVESTMENT_IN_EURO),
            size_unit=Units.LITER,
        ),
    )
    heat_pump = replace(
        heat_pump,
        inputs=replace(heat_pump.inputs, cost_facts=list(heat_pump.inputs.cost_facts) + [buffer]),
        measures=("heating_system", LAGGING),
    )
    return [baseline_stage(), envelope_stage(0), heat_pump]


#: The subject -> measure map a translator would write for the synthetic plan.
MEASURE_IDS = {
    SyntheticPlan.HEAT_PUMP_SUBJECT: "heating_system",
    BUFFER_SUBJECT: "heating_system",
    SyntheticPlan.ENVELOPE_SUBJECT: "external_insulation",
    LAGGING: LAGGING,
}

HEATING_QUOTE = InvestmentOverride(
    stage=2,
    measure_id="heating_system",
    amount_in_euro=QUOTE,
    source=SOURCE,
    main_subject=SyntheticPlan.HEAT_PUMP_SUBJECT,
    other_subjects=(BUFFER_SUBJECT,),
)

ENVELOPE_QUOTE = InvestmentOverride(
    stage=1,
    measure_id="external_insulation",
    amount_in_euro=9400.0,
    source="facade quote",
    main_subject=SyntheticPlan.ENVELOPE_SUBJECT,
)

LAGGING_QUOTE = InvestmentOverride(
    stage=2, measure_id=LAGGING, amount_in_euro=400.0, source="plumber", main_subject=LAGGING
)


@pytest.fixture(name="database", scope="module")
def fixture_database(tmp_path_factory):
    """The synthetic cost database."""
    return write_database(str(tmp_path_factory.mktemp("quotes_database")))


def _price(database, catalog, overrides=()) -> StagedResult:
    """Price the synthetic plan, with subsidies, under the given quotes."""
    return StagedEvaluator(database).evaluate(
        _stages(), _parameters(), brownfield_perspective(subsidies=True), catalog, investment_overrides=overrides
    )


def _entries(result: StagedResult, subject: str, category: CostCategory) -> List[tuple]:
    """``(year, best estimate)`` of one subject's entries of one category on the plan."""
    return [
        (entry.year, entry.amount_in_euro.best_estimate)
        for entry in result.plan.timeline.entries
        if entry.subject == subject and entry.category == category
    ]


def _document(result: StagedResult, unpriced=()) -> Dict[str, Any]:
    """The validated document of a priced plan."""
    document = StagedDocument(
        result, _parameters(), brownfield_perspective(subsidies=True), measure_ids=MEASURE_IDS,
        unpriced_subjects=unpriced,
    ).to_json()
    StagedDocument.validate(document)
    StagedDocument.assert_bands_ordered(document)
    StagedDocument.assert_subsidies_reconciled(document)
    return document


def _rows(document: Dict[str, Any], variant: str = "plan") -> Dict[str, Dict[str, Any]]:
    """The ``by_subject`` rows of one evaluation, by subject."""
    return {row["subject"]: row for row in document[variant]["by_subject"]}


class TestAQuotePricesTheMainSubjectsPurchase:
    """The heating_system quote: the generator at the quote, the buffer at zero, the rest as it was."""

    @pytest.fixture(name="priced", scope="class")
    def fixture_priced(self, database):
        """The plan without and with the heat-pump quote, under a 30 % share-of-cost grant."""
        catalog = always_eligible_catalog(0.3)
        return _price(database, catalog), _price(database, catalog, [HEATING_QUOTE])

    def test_the_generators_year_0_investment_is_the_quote(self, priced) -> None:
        """Investment, planning and removal of the generator's purchase sum to the quote."""
        _plain, quoted = priced
        year_zero = sum(
            amount
            for category in StagedDocument.INVESTMENT_TOTAL_CATEGORIES
            for year, amount in _entries(quoted, SyntheticPlan.HEAT_PUMP_SUBJECT, category)
            if year == 0
        )
        assert year_zero == pytest.approx(QUOTE)

    def test_the_quote_is_exact(self, priced) -> None:
        """No band on the quoted purchase."""
        _plain, quoted = priced
        for entry in quoted.plan.timeline.entries:
            if entry.subject == SyntheticPlan.HEAT_PUMP_SUBJECT and entry.category == CostCategory.INVESTMENT:
                assert entry.amount_in_euro.minimum == entry.amount_in_euro.maximum == QUOTE

    def test_the_generators_replacement_stays_at_the_database_price(self, priced) -> None:
        """A quote is for today's job, not the one a service life later."""
        plain, quoted = priced
        replacements = _entries(quoted, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.REPLACEMENT)
        assert replacements
        assert replacements == _entries(plain, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.REPLACEMENT)

    def test_the_buffer_is_bought_at_zero_but_keeps_its_life_and_its_replacement(self, priced) -> None:
        """The quote covers the whole job; the buffer's next purchase is priced as before."""
        plain, quoted = priced
        assert _entries(quoted, BUFFER_SUBJECT, CostCategory.INVESTMENT) == [(0, 0.0)]
        assert _entries(plain, BUFFER_SUBJECT, CostCategory.INVESTMENT) == [(0, BUFFER_INVESTMENT_IN_EURO)]
        replacements = _entries(quoted, BUFFER_SUBJECT, CostCategory.REPLACEMENT)
        assert replacements and replacements == _entries(plain, BUFFER_SUBJECT, CostCategory.REPLACEMENT)
        assert quoted.life_of(BUFFER_SUBJECT, staged=True) == plain.life_of(BUFFER_SUBJECT, staged=True)

    def test_the_maintenance_is_not_the_quotes(self, priced) -> None:
        """Maintenance is a rate of the database's gross investment, quoted purchase or not."""
        plain, quoted = priced
        for subject in (SyntheticPlan.HEAT_PUMP_SUBJECT, BUFFER_SUBJECT):
            assert _entries(quoted, subject, CostCategory.MAINTENANCE) == _entries(
                plain, subject, CostCategory.MAINTENANCE
            )

    def test_a_share_of_cost_grant_follows_the_quote(self, priced) -> None:
        """30 % of the quote, not of the database price."""
        plain, quoted = priced
        assert _entries(plain, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.SUBSIDY) == [
            (0, pytest.approx(-0.3 * SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO))
        ]
        assert _entries(quoted, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.SUBSIDY) == [
            (0, pytest.approx(-0.3 * QUOTE))
        ]

    def test_the_rest_of_the_plan_is_untouched(self, priced) -> None:
        """The envelope stage and the energy are priced exactly as without the quote."""
        plain, quoted = priced
        for category in CostCategory:
            assert _entries(quoted, SyntheticPlan.ENVELOPE_SUBJECT, category) == _entries(
                plain, SyntheticPlan.ENVELOPE_SUBJECT, category
            )
        assert quoted.reference.total_npv_in_euro == plain.reference.total_npv_in_euro

    def test_the_document_says_where_each_investment_came_from(self, priced) -> None:
        """reader_quote on the generator, included_in_reader_quote on the buffer, the echo."""
        _plain, quoted = priced
        document = _document(quoted)
        rows = _rows(document)
        generator, buffer, envelope = (
            rows[SyntheticPlan.HEAT_PUMP_SUBJECT],
            rows[BUFFER_SUBJECT],
            rows[SyntheticPlan.ENVELOPE_SUBJECT],
        )
        assert (generator["investment_origin"], generator["investment_source"]) == (
            InvestmentOrigin.READER_QUOTE.value,
            SOURCE,
        )
        assert generator["investment_in_euro"]["best"] == pytest.approx(QUOTE)
        assert (buffer["investment_origin"], buffer["investment_source"]) == (
            InvestmentOrigin.INCLUDED_IN_READER_QUOTE.value,
            SOURCE,
        )
        assert buffer["investment_in_euro"] == {"min": 0.0, "best": 0.0, "max": 0.0}
        assert buffer["note"] == StagedDocument.INCLUDED_NOTE.format(measure_id="heating_system", stage=2)
        assert envelope["investment_origin"] == InvestmentOrigin.REQUEST.value
        assert rows["ELECTRICITY"]["investment_origin"] is None
        assert document["parameters"][ParameterKeys.INVESTMENT_OVERRIDES] == [_quote(2, "heating_system")]
        reference = _rows(document, "reference")
        assert reference[SyntheticPlan.BOILER_SUBJECT]["investment_origin"] == InvestmentOrigin.REQUEST.value

    def test_the_ledger_names_the_quote(self, priced) -> None:
        """cost_provenance.json records the quoted purchase with the reader's source."""
        _plain, quoted = priced
        assert quoted.ledger is not None
        details = [
            record.detail or ""
            for record in quoted.ledger.records
            if record.parameter == f"{SyntheticPlan.HEAT_PUMP_SUBJECT}.purchase_cost_override_in_euro"
        ]
        assert details and all(SOURCE in detail for detail in details)


class TestAnEnvelopeQuote:
    """An envelope measure's quote prices its one subject."""

    def test_the_facade_is_bought_at_the_quote_and_its_grant_follows(self, database) -> None:
        """Investment and a share-of-cost grant, both from the quote."""
        quoted = _price(database, always_eligible_catalog(0.3), [ENVELOPE_QUOTE])
        assert _entries(quoted, SyntheticPlan.ENVELOPE_SUBJECT, CostCategory.INVESTMENT) == [(0, 9400.0)]
        assert _entries(quoted, SyntheticPlan.ENVELOPE_SUBJECT, CostCategory.SUBSIDY) == [
            (0, pytest.approx(-0.3 * 9400.0))
        ]
        row = _rows(_document(quoted))[SyntheticPlan.ENVELOPE_SUBJECT]
        assert (row["investment_origin"], row["investment_source"]) == ("reader_quote", "facade quote")

    def test_an_unpriced_envelope_subject_becomes_priced(self, database) -> None:
        """The request carried no price for it; the quote is one, and the row says so."""
        quoted = _price(database, always_eligible_catalog(0.3), [ENVELOPE_QUOTE])
        row = _rows(_document(quoted, unpriced=[SyntheticPlan.ENVELOPE_SUBJECT]))[SyntheticPlan.ENVELOPE_SUBJECT]
        assert row["unpriced"] is False
        assert row["note"] == StagedDocument.QUOTED_UNPRICED_NOTE
        plain_result = _price(database, always_eligible_catalog(0.3))
        plain = _rows(_document(plain_result, unpriced=[SyntheticPlan.ENVELOPE_SUBJECT]))
        assert plain[SyntheticPlan.ENVELOPE_SUBJECT]["unpriced"] is True
        assert plain[SyntheticPlan.ENVELOPE_SUBJECT]["investment_origin"] is None


class TestTheUnpricedLaggingBecomesPriced:
    """A measure HiSim holds no price and no asset class for is bought once at its quote."""

    @pytest.fixture(name="quoted", scope="class")
    def fixture_quoted(self, database) -> StagedResult:
        """The plan with the heat-pump quote and the lagging's."""
        return _price(database, always_eligible_catalog(0.3), [HEATING_QUOTE, LAGGING_QUOTE])

    def test_it_is_one_investment_in_its_stage_and_nothing_else(self, quoted) -> None:
        """No replacement, no maintenance, no residual value: HiSim holds no lifetime for it."""
        assert _entries(quoted, LAGGING, CostCategory.INVESTMENT) == [(0, 400.0)]
        for category in (CostCategory.REPLACEMENT, CostCategory.MAINTENANCE, CostCategory.RESIDUAL_VALUE):
            assert not _entries(quoted, LAGGING, category)

    def test_its_row_is_priced_and_says_how(self, quoted) -> None:
        """Priced, stamped with its stage, its note says it is bought once."""
        row = _rows(_document(quoted, unpriced=[LAGGING]))[LAGGING]
        assert (row["unpriced"], row["stage"], row["measure_id"]) == (False, 2, LAGGING)
        assert row["investment_in_euro"]["best"] == pytest.approx(400.0)
        assert (row["investment_origin"], row["investment_source"]) == ("reader_quote", "plumber")
        assert row["note"] == StagedDocument.QUOTED_PURCHASE_NOTE
        assert row["service_life_years"] is None

    def test_the_plan_total_carries_it(self, database, quoted) -> None:
        """The quoted lagging is in the year-0 investment of the plan."""
        heat_pump_only = _price(database, always_eligible_catalog(0.3), [HEATING_QUOTE])
        difference = _document(quoted)["plan"]["totals"]["investment_year0_in_euro"]["best"] - _document(
            heat_pump_only
        )["plan"]["totals"]["investment_year0_in_euro"]["best"]
        assert difference == pytest.approx(400.0)


class TestALumpSumDoesNotFollowTheQuote:
    """A fixed amount pays the same whatever the reader was quoted."""

    def test_the_lump_sum_is_unchanged(self, database) -> None:
        """2,000 EUR with and without the quote."""
        catalog = synthetic_catalog(
            [
                always_eligible_scheme(
                    "LUMP", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=2000.0), PayoutKind.UPFRONT_GRANT
                )
            ]
        )
        plain, quoted = _price(database, catalog), _price(database, catalog, [HEATING_QUOTE])
        expected = [(0, -2000.0)]
        assert _entries(plain, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.SUBSIDY) == expected
        assert _entries(quoted, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.SUBSIDY) == expected


class TestAQuoteInALaterStage:
    """A quote is taken exactly as stated, whatever year its stage starts in (owner, 2026-09-27).

    Whoever quotes a price for a given year has already accounted for inflation, so the plan books
    the quote nominal in its stage's year; the database price it replaces is escalated to that year.
    """

    FROM_YEAR = 4
    RATE = 0.02

    @pytest.fixture(name="later", scope="class")
    def fixture_later(self, database):
        """The plan with its heat-pump stage in year 4 under a 2 % investment escalation."""
        stages = _stages()
        stages[2] = replace(stages[2], from_year=self.FROM_YEAR)
        parameters = replace(_parameters(), investment_price_escalation_rate=self.RATE)
        evaluator = StagedEvaluator(database)
        catalog = always_eligible_catalog(0.3)
        perspective = brownfield_perspective(subsidies=True)
        plain = evaluator.evaluate(stages, parameters, perspective, catalog)
        quoted = evaluator.evaluate(stages, parameters, perspective, catalog, investment_overrides=[HEATING_QUOTE])
        return plain, quoted

    def test_a_later_stage_quote_books_exactly_its_amount(self, later) -> None:
        """11,800 nominal in year 4, where the database price is escalated to year 4."""
        plain, quoted = later
        factor = (1.0 + self.RATE) ** self.FROM_YEAR
        assert _entries(plain, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.INVESTMENT) == [
            (self.FROM_YEAR, pytest.approx(SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO * factor))
        ]
        assert _entries(quoted, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.INVESTMENT) == [
            (self.FROM_YEAR, QUOTE)
        ]
        assert _entries(quoted, BUFFER_SUBJECT, CostCategory.INVESTMENT) == [(self.FROM_YEAR, 0.0)]

    def test_its_share_of_cost_grant_is_on_the_quote_as_stated(self, later) -> None:
        """30 % of 11,800, not of 11,800 escalated."""
        _plain, quoted = later
        assert _entries(quoted, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.SUBSIDY) == [
            (self.FROM_YEAR, pytest.approx(-0.3 * QUOTE))
        ]

    def test_its_replacements_still_escalate(self, later) -> None:
        """The next purchase is a database price, escalated as without the quote."""
        plain, quoted = later
        replacements = _entries(quoted, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.REPLACEMENT)
        assert replacements == _entries(plain, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.REPLACEMENT)

    def test_the_document_states_the_quote_and_its_cap_unescalated(self, later) -> None:
        """investment_by_stage is the quote; the grant row's cap equals its amount."""
        _plain, quoted = later
        document = StagedDocument(quoted, _parameters(), brownfield_perspective(subsidies=True)).to_json()
        row = _rows(document)[SyntheticPlan.HEAT_PUMP_SUBJECT]
        assert row["investment_by_stage"] == [
            {"stage": 2, "investment_in_euro": {"min": QUOTE, "best": QUOTE, "max": QUOTE}}
        ]
        grants = [subsidy for subsidy in document["plan"]["subsidies"] if subsidy["stage"] == 2]
        assert grants
        for grant in grants:
            assert grant["max_amount_in_euro"]["best"] == pytest.approx(grant["amount_in_euro"]["best"])

    def test_a_year_0_quote_is_unchanged(self, database) -> None:
        """A stage in year 0 books its quote as it did before: nothing to escalate."""
        parameters = replace(_parameters(), investment_price_escalation_rate=self.RATE)
        quoted = StagedEvaluator(database).evaluate(
            _stages(), parameters, brownfield_perspective(), None, investment_overrides=[HEATING_QUOTE]
        )
        assert _entries(quoted, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.INVESTMENT) == [(0, QUOTE)]


class TestALaterStagesLoanAndFixedGrants:
    """A later stage's loan finances what the stage books; a fixed-amount grant is never escalated.

    Owner decisions of 2026-09-27: the loan principal is the financed share of the quote as stated
    (database prices escalated as before), and a grant of EUR 2,000 is EUR 2,000 when it is paid,
    quoted or not, while a share-of-cost grant follows the cost it is a share of.
    """

    FROM_YEAR = 4
    RATE = 0.02
    FINANCED_SHARE = 0.8
    TERM = 6  # years: a year-4 loan is repaid in full inside the 12-year horizon
    LUMP_SUM = 2000.0

    def _later(self, database, perspective, catalog, overrides=()) -> StagedResult:
        """The plan with its heat-pump stage in year 4 under a 2 % investment escalation."""
        stages = _stages()
        stages[2] = replace(stages[2], from_year=self.FROM_YEAR)
        parameters = replace(_parameters(), investment_price_escalation_rate=self.RATE)
        return StagedEvaluator(database).evaluate(
            stages, parameters, perspective, catalog, investment_overrides=overrides
        )

    def _financed(self, subsidies: bool = False):
        """The brownfield perspective with 80 % of the year-0 net investment financed."""
        from hisim.economics.financing import FinancingPlan  # pylint: disable=import-outside-toplevel

        return replace(
            brownfield_perspective(subsidies=subsidies),
            financing=FinancingPlan(financed_share=self.FINANCED_SHARE, term_in_years=self.TERM),
        )

    def _lump_sum_catalog(self):
        """One lump sum of EUR 2,000 every synthetic measure qualifies for."""
        return synthetic_catalog(
            [
                always_eligible_scheme(
                    "LUMP", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=self.LUMP_SUM), PayoutKind.UPFRONT_GRANT
                )
            ]
        )

    @staticmethod
    def _principal(result: StagedResult, year: int) -> float:
        """The loan disbursed in one plan year, as a positive best estimate."""
        (principal,) = [
            amount for at, amount in _entries(result, "financing", CostCategory.LOAN_DISBURSEMENT) if at == year
        ]
        return float(-principal)

    def test_a_quoted_heat_pumps_loan_finances_the_quote_as_stated(self, database) -> None:
        """80 % of 11,800, not of 11,800 escalated to year 4 (the buffer is bought within the quote)."""
        quoted = self._later(database, self._financed(), None, [HEATING_QUOTE])
        assert self._principal(quoted, self.FROM_YEAR) == pytest.approx(self.FINANCED_SHARE * QUOTE)

    def test_an_unquoted_measures_loan_is_escalated_as_before(self, database) -> None:
        """80 % of the database prices escalated to year 4."""
        plain = self._later(database, self._financed(), None)
        factor = (1.0 + self.RATE) ** self.FROM_YEAR
        booked = SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO + BUFFER_INVESTMENT_IN_EURO
        assert self._principal(plain, self.FROM_YEAR) == pytest.approx(self.FINANCED_SHARE * booked * factor)

    def test_the_debt_service_repays_exactly_the_principal(self, database) -> None:
        """The later stage's schedule repays its own principal, from the year after it is taken out."""
        quoted = self._later(database, self._financed(), None, [HEATING_QUOTE])
        repaid = [
            (entry.year, entry.amount_in_euro.best_estimate)
            for entry, stage in zip(quoted.plan.timeline.entries, quoted.stage_by_entry)
            if stage == 2 and entry.category is CostCategory.LOAN_PRINCIPAL
        ]
        assert min(year for year, _amount in repaid) == self.FROM_YEAR + 1
        assert sum(amount for _year, amount in repaid) == pytest.approx(self.FINANCED_SHARE * QUOTE)

    @pytest.mark.parametrize("overrides", [(), (HEATING_QUOTE,)], ids=["unquoted", "quoted"])
    def test_a_lump_sum_in_a_later_stage_books_exactly_its_amount(self, database, overrides) -> None:
        """EUR 2,000 in year 4, whether the heat pump is quoted or priced from the database."""
        result = self._later(database, brownfield_perspective(subsidies=True), self._lump_sum_catalog(), overrides)
        assert _entries(result, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.SUBSIDY) == [
            (self.FROM_YEAR, -self.LUMP_SUM)
        ]

    def test_the_loan_is_net_of_the_nominal_lump_sum(self, database) -> None:
        """80 % of (11,800 - 2,000): the grant the stage books is the one the loan is net of."""
        quoted = self._later(database, self._financed(subsidies=True), self._lump_sum_catalog(), [HEATING_QUOTE])
        assert self._principal(quoted, self.FROM_YEAR) == pytest.approx(
            self.FINANCED_SHARE * (QUOTE - self.LUMP_SUM)
        )

    def test_the_lump_sums_maximum_is_not_escalated_either(self, database) -> None:
        """The row's cap is EUR 2,000, its amount (both signed as a credit)."""
        result = self._later(database, brownfield_perspective(subsidies=True), self._lump_sum_catalog())
        document = StagedDocument(result, _parameters(), brownfield_perspective(subsidies=True)).to_json()
        (row,) = [
            subsidy
            for subsidy in document["plan"]["subsidies"]
            if subsidy["stage"] == 2 and subsidy["status"] == "awarded"
        ]
        assert row["amount_in_euro"]["best"] == pytest.approx(-self.LUMP_SUM)
        assert row["max_amount_in_euro"]["best"] == pytest.approx(-self.LUMP_SUM)

    def test_a_share_of_cost_grant_on_an_unquoted_measure_still_escalates(self, database) -> None:
        """30 % of the database price escalated to year 4."""
        result = self._later(database, brownfield_perspective(subsidies=True), always_eligible_catalog(0.3))
        factor = (1.0 + self.RATE) ** self.FROM_YEAR
        assert _entries(result, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.SUBSIDY) == [
            (self.FROM_YEAR, pytest.approx(-0.3 * SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO * factor))
        ]


class TestTheEngineGuards:
    """The evaluator refuses a quote it cannot place, whoever built it."""

    @pytest.mark.parametrize(
        "overrides",
        [
            [replace(HEATING_QUOTE, stage=7)],
            [HEATING_QUOTE, replace(HEATING_QUOTE, amount_in_euro=1.0)],
            [replace(HEATING_QUOTE, amount_in_euro=0.0)],
        ],
    )
    def test_an_unplaceable_quote_is_refused(self, database, overrides) -> None:
        """A stage the plan does not have, a second quote, a quote of nothing."""
        from hisim.economics.staged import StagedEvaluationError  # pylint: disable=import-outside-toplevel

        with pytest.raises(StagedEvaluationError):
            _price(database, None, overrides)


# ------------------------------------------------------------------------------------ the command


def _report_measures(workspace: Path) -> None:
    """Give the workspace's stage reports the measure lines a RenoVisor job writes.

    The stage-2 report lists the envelope measure again, as a RenoVisor package that carries every
    measure before it does, so the command has to tell the stage's own measures apart.
    """
    from hisim.renovisor.report import MappingReport  # pylint: disable=import-outside-toplevel

    for name, measures in (
        ("envelope", ["external_insulation", "change_room_temperature"]),
        ("heat_pump", ["external_insulation", "heating_system"]),
    ):
        path = workspace / name / StagedCli.MAPPING_REPORT_FILE_NAME
        report = json.loads(path.read_text(encoding="utf-8"))
        report[MappingReport.MEASURES_FIELD] = [{"id": measure, "status": "used"} for measure in measures]
        if "change_room_temperature" in measures:
            report[MappingReport.SUBJECTS_FIELD]["change_room_temperature"] = "change_room_temperature"
            report[MappingReport.COSTLESS_SUBJECTS_FIELD] = ["change_room_temperature"]
            report[MappingReport.SUBJECT_NOTES_FIELD] = {"change_room_temperature": "a setting"}
        path.write_text(json.dumps(report), encoding="utf-8")


def _run_with(workspace: Path, quotes: List[Dict[str, Any]]) -> int:
    """Run the three-stage plan with the given quotes in its parameter block."""
    _report_measures(workspace)
    parameters_path = workspace / "parameters.json"
    block = json.loads(parameters_path.read_text(encoding="utf-8"))
    block[ParameterKeys.INVESTMENT_OVERRIDES] = quotes
    parameters_path.write_text(json.dumps(block), encoding="utf-8")
    return main(_arguments(workspace, workspace / "economics_result.json"))


def _problems(workspace: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = json.loads(
        (workspace / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
    )["problems"]
    return rows


class TestTheCommand:
    """``staged --parameters`` with quotes: exit 0 and the echo, exit 2 named, exit 3 unplaceable."""

    def test_a_heat_pump_quote_prices_the_plan_and_is_echoed(self, workspace) -> None:
        """The generator of stage 2 at the quote, the quote in the block."""
        assert _run_with(workspace, [_quote(2, "heating_system")]) == 0
        document = json.loads((workspace / "economics_result.json").read_text(encoding="utf-8"))
        assert document["schema_version"] == 6
        assert document["parameters"][ParameterKeys.INVESTMENT_OVERRIDES] == [_quote(2, "heating_system")]
        row = _rows(document)[SyntheticPlan.HEAT_PUMP_SUBJECT]
        assert (row["investment_origin"], row["investment_source"]) == ("reader_quote", SOURCE)
        assert all(
            subsidy["max_amount_in_euro"] is None or set(subsidy["max_amount_in_euro"]) == {"min", "best", "max"}
            for subsidy in document["plan"]["subsidies"]
        )

    @pytest.mark.parametrize(
        "quote, code",
        [
            (_quote(5, "heating_system"), "parameters.investment_overrides.stage.unknown"),
            (_quote(2, "external_insulation"), "parameters.investment_overrides.measure_id.not_in_stage"),
            (_quote(1, "change_room_temperature", 50.0), "parameters.investment_overrides.measure_id.costless"),
            (_quote(2, "heat_pump"), "parameters.investment_overrides.measure_id.invalid"),
        ],
    )
    def test_a_quote_that_does_not_fit_the_plan_is_exit_2_named(self, workspace, quote, code) -> None:
        """Every such fault is a problems.json row, and no document is written."""
        assert _run_with(workspace, [quote]) == StagedCli.PLAN_REFUSED
        assert [problem["code"] for problem in _problems(workspace)] == [code]
        assert not (workspace / "economics_result.json").exists()

    def test_a_duplicate_is_exit_2(self, workspace) -> None:
        """Two quotes for the heat pump of stage 2."""
        assert _run_with(workspace, [_quote(2, "heating_system"), _quote(2, "heating_system", 9000.0)]) == 2
        assert [problem["code"] for problem in _problems(workspace)] == ["parameters.investment_overrides.duplicate"]

    def test_a_main_subject_that_cannot_be_determined_is_exit_3(self, workspace, capsys) -> None:
        """The synthetic envelope subject is not named by its measure id: no guess, an engine failure."""
        assert _run_with(workspace, [_quote(1, "external_insulation")]) == StagedCli.ENGINE_FAILED
        assert "external_insulation" in capsys.readouterr().err
        assert not (workspace / "economics_result.json").exists()
