"""A unit a measure adds beside a kept one is its own purchase (hisim-epc.28).

A photovoltaic measure on a house that already has an array adds a second array, which the twin
simulates as one ``PVSystem`` of the summed power. The context's ``added_pieces`` splits that
subject into the kept unit and the added one, and the added one is bought new on a register entry
of its own (``ComponentCostFacts.own_register_entry``) although the register keeps an asset of
its class.
"""

import pytest

from hisim.economics.bridge import AddedPiece, EconomicContext, _merge_context
from hisim.economics.calculators.context_resolution import installation_verdict
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import ComponentCostFacts, ExistingAsset, ExistingAssetRegister
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
from hisim.economics.serialization import facts_from_json, facts_to_json
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base

#: The existing array, the one the measure adds, and the one simulated array of their sum.
EXISTING_KW = 3.0
ADDED_KW = 3.63
TOTAL_KW = EXISTING_KW + ADDED_KW

BROWNFIELD = Perspective(
    id="brownfield", installation_context=InstallationContext.BROWNFIELD, subsidy_mode=SubsidyMode.none()
)
GREENFIELD = Perspective(
    id="greenfield", installation_context=InstallationContext.GREENFIELD, subsidy_mode=SubsidyMode.none()
)


@pytest.fixture(name="database", scope="module")
def fixture_database() -> CostDatabase:
    """The shipped cost database, which prices the arrays by its own law."""
    return CostDatabase()


def _parameters() -> EconomicParameters:
    """All rates zero, so the figures are the undiscounted prices."""
    return EconomicParameters(
        observation_period_in_years=20,
        interest_rate=0.0,
        general_price_escalation_rate=0.0,
        investment_price_escalation_rate=0.0,
        co2_price_scenario="none",
        energy_price_escalation_rates={carrier: 0.0 for carrier in EnergyCarrier},
        feed_in_escalation_rate=0.0,
        country="IE",
        price_basis_year=2026,
    )


def _array(size: float) -> ComponentCostFacts:
    """A database-priced array of one size."""
    return ComponentCostFacts(asset_class=ComponentType.PV, size=size, size_unit=Units.KILOWATT)


def _register() -> ExistingAssetRegister:
    """The house's array, kept: the measure adds beside it and replaces nothing."""
    return ExistingAssetRegister(
        assets=[
            ExistingAsset(
                asset_class=ComponentType.PV, size=EXISTING_KW, size_unit=Units.KILOWATT, installation_year=2016
            )
        ]
    )


def _split_inputs() -> EvaluationInputs:
    """The inputs of a run whose one PVSystem is the sum, merged with the context that splits it."""
    inputs = EvaluationInputs(
        simulation_year=2019,
        simulated_period_fraction=1.0,
        cost_facts=[SubjectCostFacts("PVSystem", _array(TOTAL_KW))],
    )
    _merge_context(
        inputs,
        EconomicContext(
            existing_assets=_register(),
            added_pieces={"PVSystem": AddedPiece(subject="PVSystem#added", size=ADDED_KW)},
        ),
    )
    return inputs


class TestTheSplit:
    """The context splits the simulated subject into the kept unit and the added one."""

    def test_the_sum_becomes_the_kept_unit_and_the_added_piece(self) -> None:
        """Sizes add up to the simulated one; only the piece has a register entry of its own."""
        facts = {entry.subject: entry.facts for entry in _split_inputs().cost_facts}
        assert set(facts) == {"PVSystem", "PVSystem#added"}
        assert facts["PVSystem"].size == pytest.approx(EXISTING_KW)
        assert facts["PVSystem#added"].size == pytest.approx(ADDED_KW)
        assert not facts["PVSystem"].own_register_entry
        assert facts["PVSystem#added"].own_register_entry

    def test_a_whole_figure_override_is_split_in_proportion_to_size(self) -> None:
        """An investment override states the sum's price and no law, so each piece carries its share."""
        facts = ComponentCostFacts(
            asset_class=ComponentType.PV,
            size=10.0,
            size_unit=Units.KILOWATT,
            investment_cost_override_in_euro=UncertainValue.exact(10000.0),
            override_source="unit test",
        )
        inputs = EvaluationInputs(
            simulation_year=2019, simulated_period_fraction=1.0, cost_facts=[SubjectCostFacts("PVSystem", facts)]
        )
        _merge_context(inputs, EconomicContext(added_pieces={"PVSystem": AddedPiece("PVSystem#added", 4.0)}))
        prices = {entry.subject: entry.facts.investment_cost_override_in_euro for entry in inputs.cost_facts}
        assert {subject: price.best_estimate if price is not None else None for subject, price in prices.items()} == {
            "PVSystem": pytest.approx(6000.0),
            "PVSystem#added": pytest.approx(4000.0),
        }

    @pytest.mark.parametrize("size", [0.0, TOTAL_KW, TOTAL_KW + 1.0])
    def test_a_piece_that_is_nothing_or_everything_is_refused(self, size: float) -> None:
        """A piece of zero prices nothing and a piece of the whole leaves no unit for the house to keep."""
        inputs = EvaluationInputs(
            simulation_year=2019,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("PVSystem", _array(TOTAL_KW))],
        )
        with pytest.raises(ValueError, match="more than nothing and less than the whole"):
            _merge_context(inputs, EconomicContext(added_pieces={"PVSystem": AddedPiece("PVSystem#added", size)}))

    def test_a_piece_of_a_subject_the_run_did_not_extract_is_refused(self) -> None:
        """Splitting nothing would price a purchase no component makes."""
        inputs = EvaluationInputs(simulation_year=2019, simulated_period_fraction=1.0, cost_facts=[])
        with pytest.raises(ValueError, match="did not extract: PVSystem"):
            _merge_context(inputs, EconomicContext(added_pieces={"PVSystem": AddedPiece("PVSystem#added", 1.0)}))

    def test_the_flag_survives_economic_inputs_json(self) -> None:
        """A staged plan reads the stage back from its file, and must see the piece as its own purchase."""
        piece = next(entry.facts for entry in _split_inputs().cost_facts if entry.subject == "PVSystem#added")
        assert facts_from_json(facts_to_json(piece)).own_register_entry


class TestTheVerdict:
    """A subject with its own register entry does not see the unit of its class the house keeps."""

    def test_the_added_piece_is_bought_although_an_array_is_kept(self) -> None:
        """A same-class lookup would call the new array the old one, and it would be free."""
        register = _register()
        kept = installation_verdict(ComponentType.PV, InstallationContext.BROWNFIELD, register, "PVSystem", False)
        added = installation_verdict(ComponentType.PV, InstallationContext.BROWNFIELD, register, "PVSystem#added", True)
        assert (kept.is_new_investment, kept.kept_asset) == (False, register.assets[0])
        assert (added.is_new_investment, added.kept_asset, added.replaced_asset) == (True, None, None)

    def test_an_entry_bound_to_a_subject_is_seen_by_that_subject_alone(self) -> None:
        """A staged plan keeps the piece on an entry of its own, which the house's unit never matches."""
        bound = ExistingAsset(
            asset_class=ComponentType.PV,
            size=ADDED_KW,
            size_unit=Units.KILOWATT,
            installation_year=2027,
            subject="PVSystem#added",
        )
        register = ExistingAssetRegister(assets=[bound])
        assert installation_verdict(
            ComponentType.PV, InstallationContext.BROWNFIELD, register, "PVSystem#added", True
        ).kept_asset is bound
        assert installation_verdict(
            ComponentType.PV, InstallationContext.BROWNFIELD, register, "PVSystem", False
        ).is_new_investment


class TestThePrice:
    """The added array is priced alone, as a new array of its own size, and the kept one is not bought."""

    def test_the_added_array_costs_what_a_new_array_of_its_size_costs(self, database: CostDatabase) -> None:
        """Same investment as the array bought alone into an empty house; nothing for the kept one."""
        evaluator = EconomicEvaluator(database, _parameters())
        split = evaluator.evaluate(_split_inputs(), BROWNFIELD)
        alone = evaluator.evaluate(
            EvaluationInputs(
                simulation_year=2019,
                simulated_period_fraction=1.0,
                cost_facts=[SubjectCostFacts("PVSystem#added", _array(ADDED_KW))],
            ),
            GREENFIELD,
        )
        added = split.component_breakdowns["PVSystem#added"].npv_by_category[CostCategory.INVESTMENT]
        expected = alone.component_breakdowns["PVSystem#added"].npv_by_category[CostCategory.INVESTMENT]
        assert added.best_estimate == pytest.approx(expected.best_estimate)
        assert added.best_estimate > 0.0
        assert CostCategory.INVESTMENT not in split.component_breakdowns["PVSystem"].npv_by_category
