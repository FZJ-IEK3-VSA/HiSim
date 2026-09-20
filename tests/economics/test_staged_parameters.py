"""The ``staged`` command's parameter vocabulary, key by key (step 13 §1).

``--parameters`` is the document's own ``parameters`` block, so exactly two properties have to
hold and are what this file pins: every key means what the table of step 13 §1 says it means, and
the block a document publishes is a block the reader accepts back unchanged. Everything else here
is the refusal contract — one problem per offending key, all of them at once, and no default
country anywhere.

The CLI's own behaviour (exit codes, ``problems.json``, the written document) is
``test_staged_cli.py``; this file is the parser and the two directions of the key table.
"""

from typing import Any, Dict, List

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.financing import FinancingPlan
from hisim.economics.parameters import EconomicParameters
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
    StagedParameters,
    SubsidyModeName,
)

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
            {ParameterKeys.SIMULATION_YEAR: 1999, ParameterKeys.SUBSIDY_CATALOG: "IE@2026-09-19"},
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
        """No stored evaluation, no ``country`` key, and therefore no run."""
        parsed = StagedParameters.from_mapping({"horizon_years": 20}, None)
        assert _codes(parsed) == ["parameters.country.missing"]

    def test_stages_without_a_country_take_the_files(self):
        """A file may supply what the stages do not state — that is what the key is for."""
        parsed = StagedParameters.from_mapping({ParameterKeys.COUNTRY: "IE"}, None)
        assert parsed.parameters is not None
        assert parsed.parameters.country == "IE"

    def test_a_stage_country_without_a_stored_record_prices_the_plan(self):
        """A backend's stage directory has the country in its extract and no stored evaluation.

        The record is then built from the overrides alone, so the country has to be one of them:
        leaving it out would let ``EconomicParameters.country``'s own default — ``"DE"`` — decide
        what an Irish plan is priced with.
        """
        parsed = StagedParameters.from_mapping({}, None, stored_country="IE")
        assert not parsed.problems
        assert parsed.parameters is not None
        assert parsed.parameters.country == "IE"

    def test_a_file_contradicting_the_stage_country_is_refused_without_a_stored_record(self):
        """The extract's country binds exactly as a stored evaluation's does."""
        parsed = StagedParameters.from_mapping({ParameterKeys.COUNTRY: "DE"}, None, stored_country="IE")
        assert _codes(parsed) == ["parameters.country.mismatch"]

    def test_something_that_is_not_a_country_code_is_refused(self):
        """An ISO-3166 alpha-2 code, upper case, because that is how the data files are named."""
        parsed = StagedParameters.from_mapping({ParameterKeys.COUNTRY: "Ireland"}, None)
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
            simulation_year=2021,
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
                simulation_year=2021,
                subsidy_catalog="IE@2026-09-19",
            )
            == block
        )
