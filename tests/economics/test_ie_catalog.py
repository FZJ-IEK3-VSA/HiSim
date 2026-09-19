"""The Irish catalogue is a marked placeholder that still computes the right euros (step 11).

`hisim/subsidy_catalog/IE.json` was transcribed from the SEAI web pages by an AI agent and no
person has checked it yet, so two different things are asserted here and both matter. The
**marking**: every scheme says in its display name and in its legal basis that it is an AI draft
needing examination, and every context field it conditions on has a question a user can answer in
both shipped languages — a placeholder nobody can see is worse than no placeholder at all. And
the **behaviour**: the amounts the solver actually awards for the cases the pages state outright,
including the two rules the catalogue language can only approximate (the stepped solar PV grant)
and the one pair of grants that shares an asset class and must never both fire (cavity fill
versus internal dry lining).

Everything here runs against the shipped catalogue rather than a fixture, because the point is
the file that ships.
"""

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.facts import ComponentCostFacts, ExistingAsset
from hisim.economics.subsidies import (
    ApplicantProfile,
    DwellingType,
    MeasureForSubsidy,
    SubsidyBuildingContext,
    SubsidyCatalog,
    SubsidyContext,
    scheme_context_fields,
    solve_cumulation,
)
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.economics.validation import ValidationConstants
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base


class IrishCatalogueCase:
    """The fixtures every test below is built from, and the marker strings §1 demands.

    A namespace rather than a value type: it holds the two marker prefixes/suffixes the data has
    to carry, the price basis year scheme validity is tested against, and the two builders that
    turn a handful of arguments into a measure and a context. Keeping them together is what lets
    each test read as one sentence about one grant.
    """

    #: What every scheme's `display_name` has to end with (step 11 §1.1).
    MARKER = " [AI draft — needs examination]"

    #: What every scheme's `legal_basis` has to start with (step 11 §1.2).
    LEGAL_BASIS_PREFIX = "AI-GENERATED PLACEHOLDER (2026-09-19), NEEDS TO BE EXAMINED — "

    #: The economic "today" scheme validity is tested against; every IE scheme is valid in it.
    YEAR = 2026

    @classmethod
    def catalog(cls) -> SubsidyCatalog:
        """The shipped Irish catalogue."""
        return SubsidyCatalog.load("IE")

    @classmethod
    def measure(
        cls,
        asset_class: ComponentType,
        cost_in_euro: float = 20000.0,
        measure_kind: str = "INSTALL",
        attributes=None,
    ) -> MeasureForSubsidy:
        """One measure to fund, expensive enough that no lump sum is clamped by its cost.

        Args:
            asset_class: What is being installed or replaced.
            cost_in_euro: The investment cost; a tenth of it is booked as planning cost, which is
                what the technical-assessment grant computes on.
            measure_kind: "INSTALL" or "REPLACE".
            attributes: The measure's technical attributes, which several conditions read.

        Returns:
            The measure record the solver takes.
        """
        return MeasureForSubsidy(
            subject=asset_class.value,
            facts=ComponentCostFacts(
                asset_class=asset_class,
                size=1.0,
                size_unit=Units.KILOWATT,
                technical_attributes=dict(attributes or {}),
            ),
            measure_kind=measure_kind,
            cost_by_category={
                CostCategory.INVESTMENT: UncertainValue.exact(cost_in_euro),
                CostCategory.PLANNING: UncertainValue.exact(cost_in_euro / 10.0),
                CostCategory.REMOVAL: UncertainValue.exact(0.0),
            },
            vat_rate=0.0,
        )

    @classmethod
    def context(cls, **answers) -> SubsidyContext:
        """An owner-occupier context, with the named answers filled in.

        Args:
            **answers: Field names of :class:`~hisim.economics.subsidies.ApplicantProfile` or of
                :class:`~hisim.economics.subsidies.SubsidyBuildingContext`; everything not named
                stays unanswered and therefore undetermined.

        Returns:
            The context the eligibility conditions resolve against.
        """
        applicant_fields = {
            "receives_means_tested_benefit",
            "first_time_buyer",
            "managed_full_retrofit",
        }
        applicant = {name: value for name, value in answers.items() if name in applicant_fields}
        building = {name: value for name, value in answers.items() if name not in applicant_fields}
        return SubsidyContext(
            applicant=ApplicantProfile(**applicant),
            building=SubsidyBuildingContext(**building),
        )

    @classmethod
    def awards(cls, measure: MeasureForSubsidy, context: SubsidyContext):
        """Scheme id -> awarded euro, for one measure under one context.

        Returns:
            A dict of the schemes the solver applied and the best-estimate amount of each.
        """
        decision = solve_cumulation(cls.catalog(), measure, context, cls.YEAR, lambda year: 1.0)
        return {award.scheme_id: award.upfront_amount.best_estimate for award in decision.applied}

    @classmethod
    def statuses(cls, measure: MeasureForSubsidy, context: SubsidyContext):
        """Scheme id -> "awarded" / "ineligible" / "undetermined", for one measure.

        Returns:
            One entry per scheme the solver looked at, so a test can say that a scheme was ruled
            out rather than merely not chosen.
        """
        decision = solve_cumulation(cls.catalog(), measure, context, cls.YEAR, lambda year: 1.0)
        result = {award.scheme_id: "awarded" for award in decision.applied}
        result.update({row["scheme_id"]: "ineligible" for row in decision.rejected})
        result.update({row["scheme_id"]: "undetermined" for row in decision.undetermined})
        return result

    @classmethod
    def oil_boiler(cls) -> ExistingAsset:
        """The oil boiler a Renewable Heat Bonus is paid for leaving behind."""
        return ExistingAsset(
            asset_class=ComponentType.OIL_HEATER,
            size=15.0,
            size_unit=Units.KILOWATT,
            installation_year=1995,
            energy_carrier=EnergyCarrier.HEATING_OIL,
        )

    @classmethod
    def existing_heat_pump(cls) -> ExistingAsset:
        """The heat pump whose replacement is not a decarbonisation and earns no bonus."""
        return ExistingAsset(
            asset_class=ComponentType.HEAT_PUMP,
            size=10.0,
            size_unit=Units.KILOWATT,
            installation_year=2015,
            energy_carrier=EnergyCarrier.ELECTRICITY,
        )


class TestTheCatalogueSaysItIsADraft:
    """Step 11 §1: the marking is in the data, in every place a consumer could look."""

    def test_every_display_name_ends_with_the_marker(self) -> None:
        """The display name is what the report and the result document show a user."""
        for scheme in IrishCatalogueCase.catalog().schemes:
            assert scheme.display_name is not None, scheme.id
            assert scheme.display_name.endswith(IrishCatalogueCase.MARKER), scheme.id

    def test_every_legal_basis_starts_with_the_marker(self) -> None:
        """The legal basis is what a reviewer checking the scheme against the law reads first."""
        for scheme in IrishCatalogueCase.catalog().schemes:
            assert scheme.legal_basis.startswith(IrishCatalogueCase.LEGAL_BASIS_PREFIX), scheme.id

    def test_every_source_says_the_transcription_is_unverified(self) -> None:
        """The registry entries carry the retrieval date and what their page left out."""
        catalog = IrishCatalogueCase.catalog()
        seai_sources = [entry for entry in catalog.sources.values() if entry.source_id.startswith("src_seai_")]
        assert seai_sources, "the Irish schemes cite no SEAI source at all"
        for entry in seai_sources:
            assert entry.retrieved == "2026-09-19", entry.source_id
            assert entry.notes is not None and entry.notes.startswith(
                "AI-generated transcription of the page text, not legally verified; "
                "needs to be examined."
            ), entry.source_id

    def test_the_snapshot_and_review_status_are_the_ones_the_step_asked_for(self) -> None:
        """The file-level marking: when it was read, and that nobody has examined it yet."""
        import json  # local: this is the one assertion that reads the raw file rather than the load
        import os

        catalog = IrishCatalogueCase.catalog()
        assert catalog.snapshot_date == "2026-09-19"
        with open(os.path.join(catalog.base_path, "IE.json"), encoding="utf-8") as file:
            raw = json.load(file)
        assert raw["review_status"] == "AI_GENERATED_NEEDS_EXAMINATION"


class TestTheQuestionnaireIsComplete:
    """§5.7: a condition on a field nobody is asked about can never be settled."""

    def test_every_referenced_context_field_has_a_question_in_both_languages(self) -> None:
        """Including the three fields step 11 added; `measure.*` fields are not asked."""
        catalog = IrishCatalogueCase.catalog()
        referenced = {
            fieldname
            for scheme in catalog.schemes
            for fieldname in scheme_context_fields(scheme)
            if fieldname and not fieldname.startswith("measure.")
        }
        assert referenced, "no IE scheme conditions on anything"
        for fieldname in sorted(referenced):
            entry = catalog.questions.get(fieldname)
            assert entry is not None, f"no question for {fieldname}"
            for language in ValidationConstants.REQUIRED_QUESTION_LANGUAGES:
                assert language in entry.question, f"{fieldname} has no {language} question"

    def test_the_three_new_fields_are_among_them(self) -> None:
        """Step 11 §3.1-§3.3: the catalogue is the reason those context fields exist."""
        catalog = IrishCatalogueCase.catalog()
        referenced = {
            fieldname for scheme in catalog.schemes for fieldname in scheme_context_fields(scheme)
        }
        assert "building.dwelling_type" in referenced
        assert "applicant.receives_means_tested_benefit" in referenced
        assert "applicant.first_time_buyer" in referenced
        assert "applicant.managed_full_retrofit" in referenced


class TestTheAmountsThePagesState:
    """The euros, for the cases the SEAI pages state outright."""

    def test_the_detached_attic_grant_is_two_thousand_euro(self) -> None:
        """The attic page's first line of its grant table, end to end through the solver."""
        awards = IrishCatalogueCase.awards(
            IrishCatalogueCase.measure(ComponentType.TOP_CEILING_UPPER_INSULATION, 6000.0),
            IrishCatalogueCase.context(construction_year=1990, dwelling_type=DwellingType.DETACHED),
        )
        assert awards == {"IE_SEAI_ATTIC_DETACHED": 2000.0}

    def test_the_attic_grant_is_undetermined_without_a_dwelling_type(self) -> None:
        """§5.7: an unanswered band is a question, never a denial and never a guess."""
        statuses = IrishCatalogueCase.statuses(
            IrishCatalogueCase.measure(ComponentType.TOP_CEILING_UPPER_INSULATION, 6000.0),
            IrishCatalogueCase.context(construction_year=1990),
        )
        assert statuses["IE_SEAI_ATTIC_DETACHED"] == "undetermined"
        assert "awarded" not in statuses.values()

    def test_the_renewable_heat_bonus_is_paid_for_leaving_an_oil_boiler(self) -> None:
        """The bonus exists to reward decarbonisation, and an oil boiler is the case it names."""
        awards = IrishCatalogueCase.awards(
            IrishCatalogueCase.measure(ComponentType.HEAT_PUMP, 20000.0, measure_kind="REPLACE"),
            IrishCatalogueCase.context(
                construction_year=1990,
                dwelling_type=DwellingType.DETACHED,
                existing_heating=IrishCatalogueCase.oil_boiler(),
            ),
        )
        assert awards["IE_SEAI_RENEWABLE_HEAT_BONUS"] == 4000.0
        assert awards["IE_SEAI_HEAT_PUMP_UNIT_HOUSE"] == 6500.0

    def test_the_renewable_heat_bonus_is_refused_for_replacing_a_heat_pump(self) -> None:
        """Replacing a heat pump is not decarbonising, so the unit grant is all there is."""
        statuses = IrishCatalogueCase.statuses(
            IrishCatalogueCase.measure(ComponentType.HEAT_PUMP, 20000.0, measure_kind="REPLACE"),
            IrishCatalogueCase.context(
                construction_year=1990,
                dwelling_type=DwellingType.DETACHED,
                existing_heating=IrishCatalogueCase.existing_heat_pump(),
            ),
        )
        assert statuses["IE_SEAI_RENEWABLE_HEAT_BONUS"] == "ineligible"
        assert statuses["IE_SEAI_HEAT_PUMP_UNIT_HOUSE"] == "awarded"

    @pytest.mark.parametrize(
        "peak_power_in_kwp, expected_in_euro",
        [(2.0, 1400.0), (3.0, 1600.0), (4.0, 1800.0)],
    )
    def test_the_solar_pv_steps_hit_the_pages_own_examples(
        self, peak_power_in_kwp: float, expected_in_euro: float
    ) -> None:
        """The PV page prints exactly these three numbers, and the step encoding reproduces them."""
        awards = IrishCatalogueCase.awards(
            IrishCatalogueCase.measure(
                ComponentType.PV, 9000.0, attributes={"peak_power_in_kwp": peak_power_in_kwp}
            ),
            IrishCatalogueCase.context(construction_year=1990, dwelling_type=DwellingType.DETACHED),
        )
        assert sum(awards.values()) == expected_in_euro

    def test_a_tiny_array_gets_nothing(self) -> None:
        """Below one kilowatt-peak the step encoding pays nothing, which the README states."""
        awards = IrishCatalogueCase.awards(
            IrishCatalogueCase.measure(ComponentType.PV, 3000.0, attributes={"peak_power_in_kwp": 0.5}),
            IrishCatalogueCase.context(construction_year=1990, dwelling_type=DwellingType.DETACHED),
        )
        assert not awards


class TestTheTwoWallGrantsThatShareAnAssetClass:
    """Cavity fill and internal dry lining are both `WALL_INTERNAL_INSULATION` (§3.4)."""

    def test_a_cavity_fill_gets_the_cavity_grant_and_not_the_dry_lining_one(self) -> None:
        """The placement attribute is the only thing that tells the two grants apart."""
        statuses = IrishCatalogueCase.statuses(
            IrishCatalogueCase.measure(
                ComponentType.WALL_INTERNAL_INSULATION,
                8000.0,
                attributes={"placement": "external_wall_cavity"},
            ),
            IrishCatalogueCase.context(construction_year=1990, dwelling_type=DwellingType.DETACHED),
        )
        assert statuses["IE_SEAI_CAVITY_WALL_DETACHED"] == "awarded"
        assert statuses["IE_SEAI_INTERNAL_WALL_DETACHED"] == "ineligible"

    def test_a_dry_lining_gets_the_internal_grant_and_not_the_cavity_one(self) -> None:
        """The other direction, so neither grant is the accidental default."""
        statuses = IrishCatalogueCase.statuses(
            IrishCatalogueCase.measure(
                ComponentType.WALL_INTERNAL_INSULATION,
                8000.0,
                attributes={"placement": "external_wall_internal"},
            ),
            IrishCatalogueCase.context(construction_year=1990, dwelling_type=DwellingType.DETACHED),
        )
        assert statuses["IE_SEAI_INTERNAL_WALL_DETACHED"] == "awarded"
        assert statuses["IE_SEAI_CAVITY_WALL_DETACHED"] == "ineligible"

    def test_an_unknown_placement_fires_neither(self) -> None:
        """A measure that does not say where it goes leaves both grants as questions."""
        statuses = IrishCatalogueCase.statuses(
            IrishCatalogueCase.measure(ComponentType.WALL_INTERNAL_INSULATION, 8000.0),
            IrishCatalogueCase.context(construction_year=1990, dwelling_type=DwellingType.DETACHED),
        )
        assert statuses["IE_SEAI_INTERNAL_WALL_DETACHED"] == "undetermined"
        assert statuses["IE_SEAI_CAVITY_WALL_DETACHED"] == "undetermined"
        assert "awarded" not in statuses.values()


class TestTheEnhancedAtticGrant:
    """The higher attic amount is paid for a welfare payment *or* for a first-time purchase."""

    @pytest.mark.parametrize(
        "answer", [{"receives_means_tested_benefit": True}, {"first_time_buyer": True}]
    )
    def test_either_answer_unlocks_the_higher_amount(self, answer) -> None:
        """2,500 EUR for a detached house, and the standard 2,000 EUR variant steps aside.

        The house is from 2008 rather than 1990 so that the Warmer Homes Scheme — which requires
        a home built before 2006 and pays 100 % of the cost — stays out of the comparison; the
        next test is the one that says what happens when it does not.
        """
        awards = IrishCatalogueCase.awards(
            IrishCatalogueCase.measure(ComponentType.TOP_CEILING_UPPER_INSULATION, 6000.0),
            IrishCatalogueCase.context(
                construction_year=2008, dwelling_type=DwellingType.DETACHED, **answer
            ),
        )
        assert awards == {"IE_SEAI_ATTIC_ENHANCED_DETACHED": 2500.0}

    def test_warmer_homes_wins_over_the_attic_grant_in_an_older_home(self) -> None:
        """A fully funded upgrade beats a fixed grant, and excludes it (§3.4, §3.8).

        The Warmer Homes Scheme is encoded as 100 % of the eligible cost for an owner-occupier on
        a welfare payment in a home built before 2006, and it excludes every other Irish scheme —
        so the solver picks it alone rather than stacking it with the attic grant.
        """
        awards = IrishCatalogueCase.awards(
            IrishCatalogueCase.measure(ComponentType.TOP_CEILING_UPPER_INSULATION, 6000.0),
            IrishCatalogueCase.context(
                construction_year=1990,
                dwelling_type=DwellingType.DETACHED,
                receives_means_tested_benefit=True,
            ),
        )
        assert list(awards) == ["IE_SEAI_WARMER_HOMES"]
