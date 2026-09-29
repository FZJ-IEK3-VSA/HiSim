"""A unit a measure adds beside a kept one is its own purchase (hisim-epc.28).

A photovoltaic measure on a house that already has an array adds a second array, ``PVSystemAdded``,
beside the house's ``PVSystem``. The register keeps the house's array under the same asset class,
so a same-class lookup would call the second array kept and charge nothing for it. The context's
``own_register_subjects`` marks it as bought on a register entry of its own
(``ComponentCostFacts.own_register_entry``).
"""

import pytest

from hisim.economics.bridge import EconomicContext, _merge_context
from hisim.economics.calculators.context_resolution import installation_verdict
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import ComponentCostFacts, ExistingAsset, ExistingAssetRegister
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
from hisim.economics.serialization import facts_from_json, facts_to_json
from hisim.economics.timeline import CostCategory
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base

#: The existing array and the one the measure adds beside it.
EXISTING_KW = 3.0
ADDED_KW = 3.63

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


def _inputs() -> EvaluationInputs:
    """The inputs of a run with the house's array and the added one, merged with the context."""
    inputs = EvaluationInputs(
        simulation_year=2019,
        simulated_period_fraction=1.0,
        cost_facts=[
            SubjectCostFacts("PVSystem", _array(EXISTING_KW)),
            SubjectCostFacts("PVSystemAdded", _array(ADDED_KW)),
        ],
    )
    _merge_context(inputs, EconomicContext(existing_assets=_register(), own_register_subjects=["PVSystemAdded"]))
    return inputs


class TestTheMarking:
    """The context marks the named subjects, and only them."""

    def test_only_the_added_array_is_bought_on_an_entry_of_its_own(self) -> None:
        """The house's array still matches the register's array; the added one does not."""
        facts = {entry.subject: entry.facts for entry in _inputs().cost_facts}
        assert not facts["PVSystem"].own_register_entry
        assert facts["PVSystemAdded"].own_register_entry

    def test_a_subject_the_run_did_not_extract_is_refused(self) -> None:
        """A purchase no component makes would silently stay kept, or be missing."""
        inputs = EvaluationInputs(simulation_year=2019, simulated_period_fraction=1.0, cost_facts=[])
        with pytest.raises(ValueError, match="did not extract: PVSystemAdded"):
            _merge_context(inputs, EconomicContext(own_register_subjects=["PVSystemAdded"]))

    def test_the_flag_survives_economic_inputs_json(self) -> None:
        """A staged plan reads the stage back from its file, and must see the unit as its own purchase."""
        added = next(entry.facts for entry in _inputs().cost_facts if entry.subject == "PVSystemAdded")
        assert facts_from_json(facts_to_json(added)).own_register_entry


class TestTheVerdict:
    """A subject with its own register entry does not see the unit of its class the house keeps."""

    def test_the_added_array_is_bought_although_an_array_is_kept(self) -> None:
        """A same-class lookup would call the new array the old one, and it would be free."""
        register = _register()
        kept = installation_verdict(ComponentType.PV, InstallationContext.BROWNFIELD, register, "PVSystem", False)
        added = installation_verdict(ComponentType.PV, InstallationContext.BROWNFIELD, register, "PVSystemAdded", True)
        assert (kept.is_new_investment, kept.kept_asset) == (False, register.assets[0])
        assert (added.is_new_investment, added.kept_asset, added.replaced_asset) == (True, None, None)

    def test_an_entry_bound_to_a_subject_is_seen_by_that_subject_alone(self) -> None:
        """A staged plan keeps the added unit on an entry of its own, which the house's unit never matches."""
        bound = ExistingAsset(
            asset_class=ComponentType.PV,
            size=ADDED_KW,
            size_unit=Units.KILOWATT,
            installation_year=2027,
            subject="PVSystemAdded",
        )
        register = ExistingAssetRegister(assets=[bound])
        assert installation_verdict(
            ComponentType.PV, InstallationContext.BROWNFIELD, register, "PVSystemAdded", True
        ).kept_asset is bound
        assert installation_verdict(
            ComponentType.PV, InstallationContext.BROWNFIELD, register, "PVSystem", False
        ).is_new_investment


class TestThePrice:
    """The added array is priced alone, as a new array of its own size, and the kept one is not bought."""

    def test_the_added_array_costs_what_a_new_array_of_its_size_costs(self, database: CostDatabase) -> None:
        """Same investment as the array bought alone into an empty house; nothing for the kept one."""
        evaluator = EconomicEvaluator(database, _parameters())
        both = evaluator.evaluate(_inputs(), BROWNFIELD)
        alone = evaluator.evaluate(
            EvaluationInputs(
                simulation_year=2019,
                simulated_period_fraction=1.0,
                cost_facts=[SubjectCostFacts("PVSystemAdded", _array(ADDED_KW))],
            ),
            GREENFIELD,
        )
        added = both.component_breakdowns["PVSystemAdded"].npv_by_category[CostCategory.INVESTMENT]
        expected = alone.component_breakdowns["PVSystemAdded"].npv_by_category[CostCategory.INVESTMENT]
        assert added.best_estimate == pytest.approx(expected.best_estimate)
        assert added.best_estimate > 0.0
        assert CostCategory.INVESTMENT not in both.component_breakdowns["PVSystem"].npv_by_category
