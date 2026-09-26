"""The ``staged`` command's parameter vocabulary, key by key (step 13 §1).

``--parameters`` is the document's own ``parameters`` block, so exactly two properties have to
hold and are what this file pins: every key means what the table of step 13 §1 says it means, and
the block a document publishes is a block the reader accepts back unchanged. Everything else here
is the refusal contract — one problem per offending key, all of them at once, and no default
country anywhere.

The CLI's own behaviour (exit codes, ``problems.json``, the written document) is
``test_staged_cli.py``; this file is the parser and the two directions of the key table.
"""

from dataclasses import replace
from typing import Any, Dict, List

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.financing import FinancingPlan
from hisim.economics.parameters import EconomicParameters, StatedEnergyPrice
from hisim.economics.perspectives import (
    InstallationContext,
    Perspective,
    SubsidyMode,
    SubsidyModeKind,
)
from hisim.economics.staged_parameters import (
    FinancingKindName,
    ParameterKeys,
    ParameterProblem,
    PlanYearBounds,
    StagedParameters,
    SubsidyModeName,
)
from hisim.economics.uncertainty import UncertainValue

pytestmark = pytest.mark.base


def _stored() -> EconomicParameters:
    """The assumptions a stage's finished job stored, as the parser's base.

    Returns:
        A record whose every field differs from the engine default that the tests below override,
        so "the stages' value survived" and "the default survived" cannot be confused.
    """
    return EconomicParameters(
        observation_period_in_years=15,
        interest_rate=0.04,
        country="IE",
        price_basis_year=2024,
        co2_price_scenario="none",
        cost_database_path="/somewhere/cost_database",
    )


def _codes(parsed: StagedParameters) -> List[str]:
    """Every refusal code the parse produced, in order."""
    return [problem.code for problem in parsed.problems]


class TestTheKeyTable:
    """Each accepted key maps onto the engine field step 13 §1 says it does."""

    def test_the_backend_example_block_is_accepted(self):
        """``economics-backend-spec.md`` §2.1's example, which the engine used to refuse."""
        parsed = StagedParameters.from_mapping(
            {
                "horizon_years": 20,
                "interest_rate": 0.03,
                "perspective_id": "brownfield_net",
                "financing": {"kind": "cash"},
                "subsidy_mode": "full",
            },
            _stored(),
        )
        assert not parsed.problems
        assert parsed.parameters is not None
        assert parsed.parameters.observation_period_in_years == 20
        assert parsed.parameters.interest_rate == 0.03
        assert parsed.perspective_id == "brownfield_net"
        assert parsed.financing_given and parsed.financing is None
        assert parsed.subsidy_mode is SubsidyModeName.FULL

    def test_what_the_file_does_not_state_stays_what_the_stages_were_priced_under(self):
        """The stored record is the base, not the engine defaults: an empty file changes nothing."""
        stored = _stored()
        parsed = StagedParameters.from_mapping({}, stored)
        assert parsed.parameters == stored

    def test_the_two_documentation_keys_are_accepted_and_ignored(self):
        """A document's own block carries them, and neither is an assumption a caller may state."""
        stored = _stored()
        parsed = StagedParameters.from_mapping(
            {ParameterKeys.WEATHER_YEAR: 1999, ParameterKeys.SUBSIDY_CATALOG: "IE@2026-09-19"},
            stored,
        )
        assert not parsed.problems
        assert parsed.parameters == stored

    def test_the_escalation_block_reaches_the_four_engine_rates(self):
        """Including the per-carrier rates, keyed the way the document writes them."""
        parsed = StagedParameters.from_mapping(
            {
                ParameterKeys.ESCALATION: {
                    "general": 0.021,
                    "investment": 0.022,
                    "feed_in": 0.0,
                    "energy": {EnergyCarrier.ELECTRICITY.value: 0.05},
                }
            },
            _stored(),
        )
        assert not parsed.problems
        assert parsed.parameters is not None
        assert parsed.parameters.general_price_escalation_rate == 0.021
        assert parsed.parameters.investment_price_escalation_rate == 0.022
        assert parsed.parameters.feed_in_escalation_rate == 0.0
        assert parsed.parameters.energy_price_escalation_rates == {EnergyCarrier.ELECTRICITY: 0.05}

    def test_a_loan_becomes_a_financing_plan_with_engine_defaults_for_the_rest(self):
        """The three fields the input vocabulary has; the repayment shape stays the engine's."""
        parsed = StagedParameters.from_mapping(
            {ParameterKeys.FINANCING: {"kind": "loan", "term_in_years": 10}}, _stored()
        )
        assert not parsed.problems
        assert parsed.financing is not None
        assert parsed.financing.term_in_years == 10
        assert parsed.financing.financed_share == FinancingPlan().financed_share
        assert parsed.financing.type == FinancingPlan().type


class TestTheCountryHasNoDefault:
    """Step 13 §1.2: the country is the stages', ``"DE"`` is never substituted."""

    def test_a_file_without_a_country_takes_the_stages(self):
        """The ordinary case, and the one that used to price an Irish house in German euros."""
        parsed = StagedParameters.from_mapping({}, _stored())
        assert parsed.parameters is not None
        assert parsed.parameters.country == "IE"

    def test_a_file_repeating_the_stages_country_is_accepted(self):
        """Repeating it is an assertion the parser is happy to check."""
        parsed = StagedParameters.from_mapping({ParameterKeys.COUNTRY: "IE"}, _stored())
        assert not parsed.problems

    def test_a_file_naming_another_country_is_refused_naming_both(self):
        """A plan is not re-priceable into another country's prices."""
        parsed = StagedParameters.from_mapping({ParameterKeys.COUNTRY: "DE"}, _stored())
        assert _codes(parsed) == ["parameters.country.mismatch"]
        assert "'DE'" in parsed.problems[0].message and "'IE'" in parsed.problems[0].message
        assert parsed.parameters is None

    def test_stages_without_a_country_and_a_file_without_one_are_refused(self):
        """No stored evaluation, no ``country`` key, and therefore no run.

        The basis year is missing for the same reason and is reported beside it, which is the
        point of collecting problems rather than raising on the first.
        """
        parsed = StagedParameters.from_mapping({"horizon_years": 20}, None)
        assert _codes(parsed) == ["parameters.country.missing", "parameters.price_basis_year.missing"]

    def test_stages_without_a_country_take_the_files(self):
        """A file may supply what the stages do not state — that is what the keys are for."""
        parsed = StagedParameters.from_mapping(
            {ParameterKeys.COUNTRY: "IE", ParameterKeys.PRICE_BASIS_YEAR: 2024}, None
        )
        assert not parsed.problems
        assert parsed.parameters is not None
        assert parsed.parameters.country == "IE"

    def test_a_stage_country_without_a_stored_record_prices_the_plan(self):
        """A backend's stage directory has the country in its extract and no stored evaluation.

        The record is then built from the overrides alone, so the country has to be one of them:
        leaving it out would let ``EconomicParameters.country``'s own default — ``"DE"`` — decide
        what an Irish plan is priced with.
        """
        parsed = StagedParameters.from_mapping({}, None, stored_country="IE", stored_price_basis_year=2024)
        assert not parsed.problems
        assert parsed.parameters is not None
        assert parsed.parameters.country == "IE"

    def test_a_file_contradicting_the_stage_country_is_refused_without_a_stored_record(self):
        """The extract's country binds exactly as a stored evaluation's does."""
        parsed = StagedParameters.from_mapping(
            {ParameterKeys.COUNTRY: "DE"}, None, stored_country="IE", stored_price_basis_year=2024
        )
        assert _codes(parsed) == ["parameters.country.mismatch"]

    def test_something_that_is_not_a_country_code_is_refused(self):
        """An ISO-3166 alpha-2 code, upper case, because that is how the data files are named."""
        parsed = StagedParameters.from_mapping({ParameterKeys.COUNTRY: "Ireland"}, _stored())
        assert _codes(parsed) == ["parameters.country.invalid"]


class TestThePriceBasisYear:
    """The stages were priced at theirs, so a file may repeat it but not change it."""

    def test_a_year_that_differs_from_the_stages_is_refused(self):
        """Re-basing stored inputs would need them re-run, not re-read."""
        parsed = StagedParameters.from_mapping({ParameterKeys.PRICE_BASIS_YEAR: 2019}, _stored())
        assert _codes(parsed) == ["parameters.price_basis_year.mismatch"]

    def test_the_stages_year_may_be_repeated(self):
        """Which is what feeding a document's own block back in does."""
        parsed = StagedParameters.from_mapping({ParameterKeys.PRICE_BASIS_YEAR: 2024}, _stored())
        assert not parsed.problems

    def test_a_null_says_nothing_and_keeps_the_stages(self):
        """The document writes ``null`` when there is none, so ``null`` has to round trip."""
        parsed = StagedParameters.from_mapping({ParameterKeys.PRICE_BASIS_YEAR: None}, _stored())
        assert not parsed.problems
        assert parsed.parameters is not None
        assert parsed.parameters.price_basis_year == 2024

    def test_a_stage_basis_year_without_a_stored_record_prices_the_plan(self):
        """A backend's stage directory states its basis year in its extract, and that is enough."""
        parsed = StagedParameters.from_mapping(
            {}, None, stored_country="IE", stored_price_basis_year=2026
        )
        assert not parsed.problems
        assert parsed.parameters is not None
        assert parsed.parameters.price_basis_year == 2026

    def test_stages_that_state_no_basis_year_are_refused_rather_than_re_derived(self):
        """Re-deriving it from the simulation year is the DE default in another costume.

        It would price the plan at a level none of its runs used, produce a complete-looking
        document and leave nothing downstream able to tell. The caller names the year instead.
        """
        parsed = StagedParameters.from_mapping({}, None, stored_country="IE")
        assert "parameters.price_basis_year.missing" in _codes(parsed)
        assert parsed.parameters is None

    def test_naming_it_is_how_a_plan_over_old_extracts_is_priced(self):
        """Extracts written before the key existed state none, and the file supplies it."""
        parsed = StagedParameters.from_mapping(
            {ParameterKeys.PRICE_BASIS_YEAR: 2024}, None, stored_country="IE"
        )
        assert not parsed.problems
        assert parsed.parameters is not None
        assert parsed.parameters.price_basis_year == 2024


class TestThePlanStartYear:
    """``plan_start_year``: the calendar year of the plan's year 0, the reader's (#57)."""

    def test_a_stated_year_is_read_and_changes_no_engine_field(self):
        """It dates the document; the stages' price basis year is untouched by it."""
        parsed = StagedParameters.from_mapping({ParameterKeys.PLAN_START_YEAR: 2026}, _stored())
        assert not parsed.problems
        assert parsed.plan_start_year == 2026
        assert parsed.parameters == _stored()

    def test_absent_or_null_is_no_start_year(self):
        """The document writes ``null`` when there is none, so ``null`` has to round trip."""
        for block in ({}, {ParameterKeys.PLAN_START_YEAR: None}):
            parsed = StagedParameters.from_mapping(block, _stored())
            assert not parsed.problems
            assert parsed.plan_start_year is None

    @pytest.mark.parametrize(
        "value",
        [PlanYearBounds.MINIMUM - 1, PlanYearBounds.MAXIMUM + 1, 26, 20260, 2026.0, "2026", True],
    )
    def test_a_year_that_is_no_calendar_year_is_refused(self, value):
        """A whole number within the bounds, like every other year of the block; typos are refused."""
        parsed = StagedParameters.from_mapping({ParameterKeys.PLAN_START_YEAR: value}, _stored())
        assert _codes(parsed) == ["parameters.plan_start_year.invalid"]
        assert parsed.parameters is None

    def test_the_bounds_themselves_are_accepted(self):
        """Inclusive at both ends."""
        for year in (PlanYearBounds.MINIMUM, PlanYearBounds.MAXIMUM):
            parsed = StagedParameters.from_mapping({ParameterKeys.PLAN_START_YEAR: year}, _stored())
            assert parsed.plan_start_year == year

    def test_it_is_the_basis_year_when_neither_stages_nor_file_state_one(self):
        """The plan's "today" is the year it starts in, never the weather year of its stages."""
        parsed = StagedParameters.from_mapping(
            {ParameterKeys.PLAN_START_YEAR: 2026}, None, stored_country="IE"
        )
        assert not parsed.problems
        assert parsed.parameters is not None
        assert parsed.parameters.price_basis_year == 2026

    def test_a_stated_basis_year_wins_over_it(self):
        """An explicit ``price_basis_year`` is the basis year; the start year only dates the rows."""
        parsed = StagedParameters.from_mapping(
            {ParameterKeys.PLAN_START_YEAR: 2030, ParameterKeys.PRICE_BASIS_YEAR: 2024}, None, stored_country="IE"
        )
        assert not parsed.problems
        assert parsed.parameters is not None
        assert parsed.parameters.price_basis_year == 2024
        assert parsed.plan_start_year == 2030

    def test_the_stages_basis_year_wins_over_it(self):
        """Stored inputs cannot be re-based, so a start year does not move the stages' basis year."""
        parsed = StagedParameters.from_mapping({ParameterKeys.PLAN_START_YEAR: 2030}, _stored())
        assert not parsed.problems
        assert parsed.parameters is not None
        assert parsed.parameters.price_basis_year == 2024

    def test_the_old_simulation_year_key_is_refused(self):
        """Schema version 5 renamed it ``weather_year``; the old spelling is an unknown key now."""
        parsed = StagedParameters.from_mapping({"simulation_year": 2019}, _stored())
        assert _codes(parsed) == ["parameters.unknown_key"]

    def test_the_document_block_echoes_it_and_the_weather_year(self):
        """Both are published, each under its own name, and the block reads back to the same year."""
        perspective = Perspective(id="brownfield_net", installation_context=InstallationContext.BROWNFIELD)
        block = StagedParameters.to_document_block(
            parameters=_stored(),
            perspective=perspective,
            weather_year=2019,
            subsidy_catalog=None,
            plan_start_year=2026,
        )
        assert block[ParameterKeys.PLAN_START_YEAR] == 2026
        assert block[ParameterKeys.WEATHER_YEAR] == 2019
        assert "simulation_year" not in block
        parsed = StagedParameters.from_mapping(block, _stored())
        assert not parsed.problems
        assert parsed.plan_start_year == 2026


class TestTheRefusals:
    """One problem per offending key, all of them at once (step 13 §1.3)."""

    def test_an_unknown_key_is_refused_with_the_keys_that_exist(self):
        """A key nobody claims is a typo, and dropping it would price an unintended run."""
        parsed = StagedParameters.from_mapping({"discount_rate": 0.02}, _stored())
        assert _codes(parsed) == ["parameters.unknown_key"]
        assert parsed.problems[0].path == "parameters.discount_rate"
        assert parsed.problems[0].accepted == ParameterKeys.ACCEPTED

    def test_the_engine_vocabulary_is_no_longer_accepted(self):
        """``staged`` speaks one vocabulary now; ``evaluate``/``explain``/``report`` keep theirs."""
        parsed = StagedParameters.from_mapping(
            {"observation_period_in_years": 20, "apply_subsidies": True}, _stored()
        )
        assert _codes(parsed) == ["parameters.unknown_key", "parameters.unknown_key"]

    def test_every_fault_is_reported_in_one_pass(self):
        """Fixing a block one key per invocation is not a conversation a batch job can have."""
        parsed = StagedParameters.from_mapping(
            {
                "horizon_years": 0,
                "interest_rate": -1.0,
                "subsidy_mode": "partial",
                "financing": {"kind": "leasing"},
                "unknown": 1,
            },
            _stored(),
        )
        assert set(_codes(parsed)) == {
            "parameters.unknown_key",
            "parameters.horizon_years.invalid",
            "parameters.interest_rate.invalid",
            "parameters.subsidy_mode.invalid",
            "parameters.financing.kind.invalid",
        }
        assert parsed.parameters is None

    def test_a_boolean_is_not_a_number(self):
        """``True`` is an ``int`` in Python, and ``{"horizon_years": true}`` is a mistake."""
        parsed = StagedParameters.from_mapping({"horizon_years": True}, _stored())
        assert _codes(parsed) == ["parameters.horizon_years.invalid"]

    def test_a_parameters_document_that_is_not_an_object_is_refused(self):
        """A list of assumptions is not an assumption set, and is not a traceback either."""
        parsed = StagedParameters.from_mapping([1, 2, 3], _stored())
        assert _codes(parsed) == ["parameters.unreadable"]

    def test_a_loan_field_on_a_cash_purchase_is_refused(self):
        """Dropping it would price a plan the caller did not describe."""
        parsed = StagedParameters.from_mapping(
            {ParameterKeys.FINANCING: {"kind": "cash", "term_in_years": 15}}, _stored()
        )
        assert _codes(parsed) == ["parameters.financing.term_in_years.invalid"]

    def test_a_financing_block_without_a_kind_is_refused(self):
        """The block's whole job is to say how the investment is paid for."""
        parsed = StagedParameters.from_mapping({ParameterKeys.FINANCING: {}}, _stored())
        assert _codes(parsed) == ["parameters.financing.kind.missing"]
        assert parsed.problems[0].accepted == tuple(member.value for member in FinancingKindName)

    def test_an_unknown_key_inside_a_nested_block_is_refused_at_its_own_path(self):
        """The nested blocks are as closed as the top-level one."""
        parsed = StagedParameters.from_mapping(
            {ParameterKeys.ESCALATION: {"electricity": 0.05}}, _stored()
        )
        assert _codes(parsed) == ["parameters.escalation.unknown_key"]
        assert parsed.problems[0].path == "parameters.escalation.electricity"

    def test_an_unknown_energy_carrier_is_refused_with_the_carriers_that_exist(self):
        """A misspelled carrier would otherwise escalate nothing, silently."""
        parsed = StagedParameters.from_mapping(
            {ParameterKeys.ESCALATION: {"energy": {"COAL": 0.05}}}, _stored()
        )
        assert _codes(parsed) == ["parameters.escalation.energy.unknown_key"]
        assert EnergyCarrier.ELECTRICITY.value in (parsed.problems[0].accepted or ())

    def test_a_problem_row_is_the_shape_problems_json_carries(self):
        """Path, code, message and, where it helps, the values that would have been accepted."""
        row = ParameterProblem(path="parameters.country", code="parameters.country.missing", message="no.")
        assert row.to_json() == {
            "path": "parameters.country",
            "code": "parameters.country.missing",
            "message": "no.",
        }
        assert ParameterProblem("p", "c", "m", accepted=("a",)).to_json()["accepted"] == ["a"]


class TestThePerspectiveIsNamedOnce:
    """``perspective_id`` in the file and ``--perspective`` on the command line."""

    def test_neither_gives_the_renovisor_default(self):
        """The frame a RenoVisor plan is priced under when nobody says otherwise."""
        problems: List[ParameterProblem] = []
        assert StagedParameters.reconciled_perspective_id(None, None, problems) == "brownfield_net"
        assert not problems

    def test_either_one_alone_wins(self):
        """Both spellings exist, and a caller uses whichever their tooling has."""
        problems: List[ParameterProblem] = []
        assert StagedParameters.reconciled_perspective_id("owner_monthly", None, problems) == "owner_monthly"
        assert StagedParameters.reconciled_perspective_id(None, "owner_monthly", problems) == "owner_monthly"
        assert not problems

    def test_a_disagreement_is_a_problem(self):
        """Two sources with two answers have no right answer."""
        problems: List[ParameterProblem] = []
        StagedParameters.reconciled_perspective_id("brownfield_net", "owner_monthly", problems)
        assert [problem.code for problem in problems] == ["parameters.perspective_id.mismatch"]


class TestWhatIsAppliedToThePerspective:
    """``financing`` and ``subsidy_mode`` are perspective dimensions, not record fields."""

    @staticmethod
    def _cash_perspective() -> Perspective:
        """A brownfield, subsidized, cash-buying perspective to override."""
        return Perspective(
            id="brownfield_net",
            installation_context=InstallationContext.BROWNFIELD,
            subsidy_mode=SubsidyMode.full(),
        )

    def test_a_loan_replaces_the_perspectives_cash_purchase(self):
        """``replace`` on the bundle's row, so the engine itself is untouched."""
        parsed = StagedParameters.from_mapping(
            {ParameterKeys.FINANCING: {"kind": "loan", "term_in_years": 7}}, _stored()
        )
        _parameters, priced_under = parsed.applied_to(self._cash_perspective())
        assert priced_under.financing is not None
        assert priced_under.financing.term_in_years == 7

    def test_cash_removes_a_perspectives_loan(self):
        """The other direction: ``{"kind": "cash"}`` means no plan at all."""
        loan_perspective = Perspective(
            id="owner_monthly",
            installation_context=InstallationContext.BROWNFIELD,
            financing=FinancingPlan(term_in_years=20),
        )
        parsed = StagedParameters.from_mapping(
            {ParameterKeys.FINANCING: {"kind": "cash"}}, _stored()
        )
        _parameters, priced_under = parsed.applied_to(loan_perspective)
        assert priced_under.financing is None

    def test_saying_nothing_leaves_the_perspective_alone(self):
        """A file that does not mention financing does not change it."""
        perspective = self._cash_perspective()
        parsed = StagedParameters.from_mapping({}, _stored())
        _parameters, priced_under = parsed.applied_to(perspective)
        assert priced_under == perspective

    def test_subsidy_mode_none_switches_the_catalogue_off_and_records_it(self):
        """The record's ``apply_subsidies`` follows the mode actually in force, both ways."""
        parsed = StagedParameters.from_mapping({ParameterKeys.SUBSIDY_MODE: "none"}, _stored())
        parameters, priced_under = parsed.applied_to(self._cash_perspective())
        assert priced_under.subsidy_mode.kind is SubsidyModeKind.NONE
        assert parameters.apply_subsidies is False

    def test_apply_subsidies_follows_the_perspective_when_the_file_is_silent(self):
        """So the document's echo of the mode is a statement about the run, not about the file."""
        gross = Perspective(
            id="brownfield_gross",
            installation_context=InstallationContext.BROWNFIELD,
            subsidy_mode=SubsidyMode.none(),
        )
        parsed = StagedParameters.from_mapping({}, _stored())
        parameters, _priced_under = parsed.applied_to(gross)
        assert parameters.apply_subsidies is False

    def test_a_refused_file_has_nothing_to_price_with(self):
        """Calling this on a refused parse is a defect in the caller, and says so."""
        parsed = StagedParameters.from_mapping({"unknown": 1}, _stored())
        with pytest.raises(ValueError, match="refused parameter file"):
            parsed.applied_to(self._cash_perspective())


class TestTheDocumentBlock:
    """The output half of the key table: what a document publishes is a legal input file."""

    @staticmethod
    def _block(perspective: Perspective) -> Dict[str, Any]:
        """The document block of the stored assumptions under one perspective."""
        return StagedParameters.to_document_block(
            parameters=_stored(),
            perspective=perspective,
            weather_year=2021,
            subsidy_catalog="IE@2026-09-19",
        )

    def test_it_states_every_accepted_key(self):
        """Input and output are the same shape, which is what makes the round trip possible."""
        block = self._block(
            Perspective(id="brownfield_net", installation_context=InstallationContext.BROWNFIELD)
        )
        assert set(block) == set(ParameterKeys.ACCEPTED)

    def test_a_cash_perspective_states_cash_and_a_loan_states_its_fields(self):
        """The two shapes ``financing`` has, on the way out as on the way in."""
        cash = self._block(
            Perspective(id="brownfield_net", installation_context=InstallationContext.BROWNFIELD)
        )
        assert cash[ParameterKeys.FINANCING] == {"kind": "cash"}
        loan = self._block(
            Perspective(
                id="owner_monthly",
                installation_context=InstallationContext.BROWNFIELD,
                financing=FinancingPlan(financed_share=0.5, nominal_interest_rate=0.02, term_in_years=10),
            )
        )
        assert loan[ParameterKeys.FINANCING] == {
            "kind": "loan",
            "financed_share": 0.5,
            "nominal_interest_rate": 0.02,
            "term_in_years": 10,
        }

    def test_the_subsidy_mode_says_whether_the_catalogue_ran(self):
        """``none`` for a gross view, ``full`` for a net one."""
        gross = Perspective(
            id="brownfield_gross",
            installation_context=InstallationContext.BROWNFIELD,
            subsidy_mode=SubsidyMode.none(),
        )
        net = Perspective(
            id="brownfield_net",
            installation_context=InstallationContext.BROWNFIELD,
            subsidy_mode=SubsidyMode.full(),
        )
        assert self._block(gross)[ParameterKeys.SUBSIDY_MODE] == SubsidyModeName.NONE.value
        assert self._block(net)[ParameterKeys.SUBSIDY_MODE] == SubsidyModeName.FULL.value

    def test_the_block_is_accepted_back_unchanged(self):
        """The property the whole module exists for, at the parser's own level."""
        perspective = Perspective(
            id="brownfield_net",
            installation_context=InstallationContext.BROWNFIELD,
            subsidy_mode=SubsidyMode.full(),
        )
        block = self._block(perspective)
        parsed = StagedParameters.from_mapping(block, _stored())
        assert not parsed.problems
        parameters, priced_under = parsed.applied_to(perspective)
        assert (
            StagedParameters.to_document_block(
                parameters=parameters,
                perspective=priced_under,
                weather_year=2021,
                subsidy_catalog="IE@2026-09-19",
            )
            == block
        )


class TestTheEnergyPrices:
    """``energy_prices``: the year-1 terms a plan states per carrier (renovisorissues #52)."""

    @staticmethod
    def _parse(block: Dict[str, Any]) -> StagedParameters:
        """Parse one ``energy_prices`` block over the stored assumptions."""
        return StagedParameters.from_mapping({ParameterKeys.ENERGY_PRICES: block}, _stored())

    def test_a_number_and_a_band_both_state_a_price(self):
        """A bare number is exact; a band is the document's own ``{min, best, max}``."""
        parsed = self._parse(
            {
                "ELECTRICITY": {
                    "working_price_in_euro_per_kwh": {"min": 0.28, "best": 0.31, "max": 0.35},
                    "standing_charge_in_euro_per_year": 180,
                },
                "ELECTRICITY_FEED_IN": {"working_price_in_euro_per_kwh": 0.0},
            }
        )
        assert not parsed.problems
        assert parsed.parameters is not None
        stated = parsed.parameters.energy_prices
        assert stated[EnergyCarrier.ELECTRICITY] == StatedEnergyPrice(
            working_price_in_euro_per_kwh=UncertainValue(best_estimate=0.31, minimum=0.28, maximum=0.35),
            standing_charge_in_euro_per_year=UncertainValue.exact(180.0),
        )
        assert stated[EnergyCarrier.ELECTRICITY_FEED_IN] == StatedEnergyPrice(
            working_price_in_euro_per_kwh=UncertainValue.exact(0.0)
        )

    def test_one_field_may_be_stated_alone(self):
        """The other keeps the database's value, so it is simply absent here."""
        parsed = self._parse({"NATURAL_GAS": {"standing_charge_in_euro_per_year": 0}})
        assert not parsed.problems
        assert parsed.parameters is not None
        assert parsed.parameters.energy_prices == {
            EnergyCarrier.NATURAL_GAS: StatedEnergyPrice(standing_charge_in_euro_per_year=UncertainValue.exact(0.0))
        }

    def test_a_file_without_the_block_keeps_the_stages_stated_prices(self):
        """What the file does not state is what the stages were priced under, as for every key."""
        stored = replace(
            _stored(),
            energy_prices={EnergyCarrier.PELLETS: StatedEnergyPrice(UncertainValue.exact(0.07))},
        )
        parsed = StagedParameters.from_mapping({}, stored)
        assert parsed.parameters == stored

    @pytest.mark.parametrize(
        "carrier, key, value",
        [
            ("ELECTRICITY", "working_price_in_euro_per_kwh", 0),
            ("ELECTRICITY", "working_price_in_euro_per_kwh", 2.5),
            ("HEATING_OIL", "working_price_in_euro_per_kwh", -0.1),
            ("ELECTRICITY", "standing_charge_in_euro_per_year", -1),
            ("ELECTRICITY", "standing_charge_in_euro_per_year", 5000.5),
            ("ELECTRICITY_FEED_IN", "working_price_in_euro_per_kwh", -0.01),
            ("ELECTRICITY_FEED_IN", "working_price_in_euro_per_kwh", 1.5),
            ("ELECTRICITY", "working_price_in_euro_per_kwh", True),
            ("ELECTRICITY", "working_price_in_euro_per_kwh", "0.30"),
            ("ELECTRICITY", "working_price_in_euro_per_kwh", {"min": 0.3, "best": 0.2, "max": 0.4}),
            ("ELECTRICITY", "working_price_in_euro_per_kwh", {"min": 0.3, "best": 0.35}),
            ("ELECTRICITY", "working_price_in_euro_per_kwh", {"min": 0.0, "best": 0.3, "max": 0.4}),
        ],
    )
    def test_a_value_outside_its_bounds_is_refused_at_its_own_path(self, carrier, key, value):
        """Typo guards: a price in cents, per MWh, negative, unordered or not a number at all."""
        parsed = self._parse({carrier: {key: value}})
        assert _codes(parsed) == [f"parameters.energy_prices.{carrier}.{key}.invalid"]
        assert parsed.problems[0].path == f"parameters.energy_prices.{carrier}.{key}"
        assert parsed.parameters is None

    def test_the_bounds_themselves_are_accepted(self):
        """2 EUR/kWh, 1 EUR/kWh of feed-in and 5,000 EUR/a are the largest values, not refusals."""
        parsed = self._parse(
            {
                "HYDROGEN": {"working_price_in_euro_per_kwh": 2, "standing_charge_in_euro_per_year": 5000},
                "ELECTRICITY_FEED_IN": {"working_price_in_euro_per_kwh": 1},
            }
        )
        assert not parsed.problems

    def test_an_unknown_carrier_is_refused_with_the_carriers_that_exist(self):
        """A misspelled carrier would otherwise price nothing, silently."""
        parsed = self._parse({"COAL": {"working_price_in_euro_per_kwh": 0.05}})
        assert _codes(parsed) == ["parameters.energy_prices.unknown_key"]
        assert parsed.problems[0].path == "parameters.energy_prices.COAL"
        assert EnergyCarrier.ELECTRICITY_FEED_IN.value in (parsed.problems[0].accepted or ())

    def test_an_unknown_term_is_refused_with_the_two_that_exist(self):
        """``grid_fee`` is not a term a plan states."""
        parsed = self._parse({"ELECTRICITY": {"grid_fee": 0.1}})
        assert _codes(parsed) == ["parameters.energy_prices.ELECTRICITY.unknown_key"]
        assert parsed.problems[0].accepted == ParameterKeys.ACCEPTED_ENERGY_PRICE

    def test_a_standing_charge_for_the_feed_in_carrier_is_refused(self):
        """Feed-in is a remuneration per kWh sold; the charge belongs to the electricity contract."""
        parsed = self._parse(
            {
                "ELECTRICITY_FEED_IN": {
                    "working_price_in_euro_per_kwh": 0.08,
                    "standing_charge_in_euro_per_year": 50,
                }
            }
        )
        assert _codes(parsed) == [
            "parameters.energy_prices.ELECTRICITY_FEED_IN.standing_charge_in_euro_per_year.invalid"
        ]

    def test_a_carrier_stating_nothing_is_refused(self):
        """An empty object would bill under a stated contract that states nothing."""
        parsed = self._parse({"ELECTRICITY": {}})
        assert _codes(parsed) == ["parameters.energy_prices.ELECTRICITY.missing"]

    def test_a_carrier_that_is_not_an_object_is_refused(self):
        """``{"ELECTRICITY": 0.3}`` does not say which term 0.3 is."""
        parsed = self._parse({"ELECTRICITY": 0.3})
        assert _codes(parsed) == ["parameters.energy_prices.ELECTRICITY.invalid"]

    def test_a_feed_in_escalation_rate_is_refused_because_it_is_never_read(self):
        """The remuneration is fixed, then follows ``escalation.feed_in``; a carrier rate says nothing."""
        parsed = StagedParameters.from_mapping(
            {ParameterKeys.ESCALATION: {"energy": {"ELECTRICITY_FEED_IN": 0.01, "ELECTRICITY": 0.03}}},
            _stored(),
        )
        assert _codes(parsed) == ["parameters.escalation.energy.ELECTRICITY_FEED_IN.invalid"]

    def test_origins_are_accepted_and_ignored(self):
        """A document's own ``origins`` describe its run; read back they change nothing."""
        stored = _stored()
        parsed = StagedParameters.from_mapping(
            {
                ParameterKeys.ORIGINS: {
                    "escalation": {"energy": {"ELECTRICITY": "country_default"}},
                    "energy_prices": {"ELECTRICITY": {"working_price_in_euro_per_kwh": "database"}},
                }
            },
            stored,
        )
        assert not parsed.problems
        assert parsed.parameters == stored

    def test_every_fault_of_the_block_is_reported_at_once(self):
        """Four faults in two carriers, four rows."""
        parsed = self._parse(
            {
                "COAL": {},
                "ELECTRICITY": {"working_price_in_euro_per_kwh": 30, "standing_charge_in_euro_per_year": -5},
                "ELECTRICITY_FEED_IN": {"standing_charge_in_euro_per_year": 1},
            }
        )
        assert sorted(_codes(parsed)) == sorted(
            [
                "parameters.energy_prices.unknown_key",
                "parameters.energy_prices.ELECTRICITY.working_price_in_euro_per_kwh.invalid",
                "parameters.energy_prices.ELECTRICITY.standing_charge_in_euro_per_year.invalid",
                "parameters.energy_prices.ELECTRICITY_FEED_IN.standing_charge_in_euro_per_year.invalid",
            ]
        )


class TestTheEnergyEchoWithoutAPricedPlan:
    """The block of parameters alone echoes what they state, and reads back unchanged."""

    @staticmethod
    def _stated() -> EconomicParameters:
        """Stored assumptions stating a rate and three price terms."""
        return replace(
            _stored(),
            energy_price_escalation_rates={EnergyCarrier.NATURAL_GAS: 0.04},
            energy_prices={
                EnergyCarrier.NATURAL_GAS: StatedEnergyPrice(
                    working_price_in_euro_per_kwh=UncertainValue(best_estimate=0.12, minimum=0.1, maximum=0.15),
                    standing_charge_in_euro_per_year=UncertainValue.exact(95.0),
                ),
                EnergyCarrier.ELECTRICITY_FEED_IN: StatedEnergyPrice(UncertainValue.exact(0.09)),
            },
        )

    def _block(self) -> Dict[str, Any]:
        """The document block of the stated assumptions."""
        return StagedParameters.to_document_block(
            parameters=self._stated(),
            perspective=Perspective(id="brownfield_net", installation_context=InstallationContext.BROWNFIELD),
            weather_year=2021,
            subsidy_catalog=None,
        )

    def test_it_states_the_stated_terms_as_bands_with_their_origins(self):
        """Every stated field, as a band, and every origin ``stated``."""
        block = self._block()
        assert block[ParameterKeys.ESCALATION]["energy"] == {"NATURAL_GAS": 0.04}
        assert block[ParameterKeys.ENERGY_PRICES] == {
            "ELECTRICITY_FEED_IN": {"working_price_in_euro_per_kwh": {"min": 0.09, "best": 0.09, "max": 0.09}},
            "NATURAL_GAS": {
                "working_price_in_euro_per_kwh": {"min": 0.1, "best": 0.12, "max": 0.15},
                "standing_charge_in_euro_per_year": {"min": 95.0, "best": 95.0, "max": 95.0},
            },
        }
        assert block[ParameterKeys.ORIGINS] == {
            "escalation": {"energy": {"NATURAL_GAS": "stated"}},
            "energy_prices": {
                "ELECTRICITY_FEED_IN": {"working_price_in_euro_per_kwh": "stated"},
                "NATURAL_GAS": {
                    "working_price_in_euro_per_kwh": "stated",
                    "standing_charge_in_euro_per_year": "stated",
                },
            },
        }

    def test_the_block_reads_back_to_the_same_parameters(self):
        """Bands come back as bands, and ``origins`` is ignored."""
        parsed = StagedParameters.from_mapping(self._block(), _stored())
        assert not parsed.problems
        assert parsed.parameters is not None
        assert parsed.parameters.energy_prices == self._stated().energy_prices
        assert parsed.parameters.energy_price_escalation_rates == {EnergyCarrier.NATURAL_GAS: 0.04}
