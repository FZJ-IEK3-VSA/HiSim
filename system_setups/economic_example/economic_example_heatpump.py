"""Worked lifecycle-cost example: heat-pump retrofit of a German single-family house.

Runs the `household_heatpump_building_sizer` system setup for a full year and evaluates the
FULL lifecycle cost perspective bundle (cost_spec.md §7.1) on top of it:

* brownfield gross/net, operating, owner_monthly (financed), landlord, tenant, macroeconomic
  — enabled by the existing-asset register below (old gas boiler + old envelope elements);
* the German subsidy engine (BEG EM heat pump with speed/income/efficiency bonuses, envelope
  schemes with iSFP bonus, §35c exclusion) via the applicant/building context;
* three envelope measures with realistic (AI-estimate, banded) costs: external wall
  insulation, top-ceiling insulation and new triple-glazed windows;
* a scenario analysis (§4.6) with different cost assumptions: interest 1 %/5 %, flat vs. high
  electricity price escalation, a 30 % cheaper heat pump (data overlay), and a high CO2 path.

Run it manually from the repository root:

    python system_setups/economic_example/economic_example_heatpump.py

Results land in system_setups/results/household_heatpump_building_sizer/default_config/;
open `lifecycle_report.html` (all perspectives, actor split, subsidy cards, scenario tornado)
and `cost_summary.md`. Everything is re-priceable offline:
`python -m hisim.economics evaluate <results_dir>` / `... explain ... --value "..."`.
"""

# clean

import sys
import time
from pathlib import Path

# Make the script runnable directly from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hisim.economics import (  # noqa: E402
    ComponentCostFacts,
    EconomicParameters,
    ExistingAsset,
    ExistingAssetRegister,
)
from hisim.economics.bridge import EconomicContext  # noqa: E402
from hisim.economics.carriers import EnergyCarrier  # noqa: E402
from hisim.economics.evaluator import SubjectCostFacts  # noqa: E402
from hisim.economics.scenarios import ScenarioSet  # noqa: E402
from hisim.economics.subsidies import (  # noqa: E402
    DEFAULT_SUBSIDY_CATALOG_PATH,
    ApplicantActor,
    ApplicantProfile,
    SubsidyBuildingContext,
    SubsidyContext,
)
from hisim.hisim_main import initialize_from_python  # noqa: E402
from hisim.loadtypes import ComponentType, Units  # noqa: E402
from hisim.postprocessingoptions import PostProcessingOptions  # noqa: E402
from hisim.simulationparameters import SimulationParameters  # noqa: E402

#: The simulated physics: weather/profiles of this year.
WEATHER_YEAR = 2021
#: The economic "today": price basis, scheme validity, CO2-path anchor, asset ages.
PRICE_BASIS_YEAR = 2026


def build_economic_context() -> EconomicContext:
    """The house: a 1978 single-family home, owner-occupied, gas boiler from 2011."""
    old_gas_boiler = ExistingAsset(
        asset_class=ComponentType.GAS_HEATER,
        size=15.0,
        size_unit=Units.KILOWATT,
        installation_year=2011,  # 15 a old of 18 a life at the 2026 price basis
        is_functional=True,
        energy_carrier=EnergyCarrier.NATURAL_GAS,
        replaced_by_asset_classes=[ComponentType.HEAT_PUMP],
    )
    # Envelope elements due for renovation; each is replaced by the like-for-like measure
    # below, which triggers the anyway-cost logic (5 a threshold for envelope, Q7).
    old_windows = ExistingAsset(
        asset_class=ComponentType.WINDOWS,
        size=28.0,
        size_unit=Units.SQUARE_METER,
        installation_year=1993,
        replaced_by_asset_classes=[ComponentType.WINDOWS],
    )
    old_wall = ExistingAsset(
        asset_class=ComponentType.WALL_INSULATION,
        size=140.0,
        size_unit=Units.SQUARE_METER,
        installation_year=1988,  # facade render at end of life
        replaced_by_asset_classes=[ComponentType.WALL_INSULATION],
    )
    old_top_ceiling = ExistingAsset(
        asset_class=ComponentType.TOP_CEILING_INSULATION,
        size=90.0,
        size_unit=Units.SQUARE_METER,
        installation_year=1988,
        replaced_by_asset_classes=[ComponentType.TOP_CEILING_INSULATION],
    )

    # The three envelope measures (costs come banded from the cost database; the U-values
    # feed the BEG technical minimum requirements).
    envelope_measures = [
        SubjectCostFacts(
            subject="Envelope.WallInsulation",
            facts=ComponentCostFacts(
                asset_class=ComponentType.WALL_INSULATION,
                size=140.0,
                size_unit=Units.SQUARE_METER,
                technical_attributes={"u_value": 0.18, "thickness_cm": 16},
            ),
        ),
        SubjectCostFacts(
            subject="Envelope.TopCeilingInsulation",
            facts=ComponentCostFacts(
                asset_class=ComponentType.TOP_CEILING_INSULATION,
                size=90.0,
                size_unit=Units.SQUARE_METER,
                technical_attributes={"u_value": 0.13, "thickness_cm": 24},
            ),
        ),
        SubjectCostFacts(
            subject="Envelope.Windows",
            facts=ComponentCostFacts(
                asset_class=ComponentType.WINDOWS,
                size=28.0,
                size_unit=Units.SQUARE_METER,
                technical_attributes={"u_value": 0.90},
            ),
        ),
    ]

    subsidy_context = SubsidyContext(
        applicant=ApplicantProfile(
            actor=ApplicantActor.OWNER_OCCUPIER,
            taxable_household_income_in_euro=38000.0,  # unlocks the BEG income bonus
            household_size=3,
            main_residence=True,
        ),
        building=SubsidyBuildingContext(
            construction_year=1978,
            dwelling_units=1,
            heated_floor_area_in_m2=150.0,
            residential_floor_area_in_m2=150.0,
            commercial_floor_area_in_m2=0.0,
            has_isfp=True,  # +5 % on envelope measures
            existing_heating=old_gas_boiler,  # functional fossil boiler -> speed bonus
        ),
    )

    # Scenario analysis (§4.6): different cost assumptions around the central case.
    scenario_set = ScenarioSet.from_json(
        {
            "base": "central",
            "mode": "ONE_AT_A_TIME",
            "axes": [
                {"name": "interest", "field": "interest_rate", "levels": {"low": 0.01, "high": 0.05}},
                {
                    "name": "electricity_escalation",
                    "field": "energy_price_escalation_rates.ELECTRICITY",
                    "levels": {"flat": 0.0, "high": 0.05},
                },
                {
                    # Data overlay: a 30 % cheaper heat pump (learning-curve storyline).
                    "name": "hp_price",
                    "field": "devices_DE.HEAT_PUMP.specific_investment",
                    "levels": {"cheap": {"min": 800, "avg": 1050, "max": 1450}},
                },
                {"name": "co2", "field": "co2_price_scenario", "levels": {"high": "high"}},
            ],
        }
    )

    return EconomicContext(
        existing_assets=ExistingAssetRegister(
            assets=[old_gas_boiler, old_windows, old_wall, old_top_ceiling]
        ),
        subsidy_context=subsidy_context,
        extra_cost_facts=envelope_measures,
        # The adapter cannot know these from the config; the BEG conditions need them:
        technical_attributes_by_subject={
            "MoreAdvancedHeatPumpHPLib": {"scop": 4.1, "refrigerant": "R290", "heat_source": "air"},
        },
        living_area_in_m2=150.0,
        heated_floor_area_in_m2=150.0,
        current_cold_rent_in_euro_per_m2_month=8.50,  # for the landlord/tenant view
        building_specific_emissions_in_kg_per_m2_a=10.0,  # heat-pump house: lowest CO2 tier
        annual_heat_demand_in_kwh=15000.0,  # for the levelized cost of heat
        scenario_set=scenario_set,
    )


def main() -> None:
    """Runs the full-year simulation with the complete economic evaluation."""
    params = SimulationParameters.full_year(year=WEATHER_YEAR, seconds_per_timestep=60 * 15)
    params.post_processing_options.append(PostProcessingOptions.LIFECYCLE_COST_REPORT)
    params.logging_level = 3
    params.set_economic_parameters(
        EconomicParameters(
            country="DE",
            price_basis_year=PRICE_BASIS_YEAR,
            subsidy_catalog_path=DEFAULT_SUBSIDY_CATALOG_PATH,  # activates the BEG engine
        )
    )
    params.set_economic_context(build_economic_context())

    start = time.time()
    my_sim = initialize_from_python(
        path_to_module=str(
            Path(__file__).resolve().parents[1] / "household_heatpump_building_sizer.py"
        ),
        my_simulation_parameters=params,
    )
    my_sim.run_all_timesteps()
    result_directory = my_sim._simulation_parameters.result_directory  # noqa: SLF001
    print(f"\nDONE in {time.time() - start:.0f} s")
    print("RESULT_DIRECTORY:", result_directory)
    print("Open lifecycle_report.html and cost_summary.md in that directory.")


if __name__ == "__main__":
    main()
