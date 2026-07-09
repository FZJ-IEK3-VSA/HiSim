"""Ireland example: switching an example house from a gas boiler to a heat pump.

Two full-year simulations of the same assumed house — the *reference* keeps the existing gas
system (`household_gas_building_sizer` setup), the *variant* installs the heat-pump retrofit
(`household_heatpump_building_sizer` setup) — evaluated with **Irish** cost data
(price basis 2026: banded AI estimates, IE carbon-tax/ETS2 CO2 path, high Irish standing
charges; Ireland has no subsidy catalog yet, so the SEAI-like flat shares in the device data
apply, see cost_module_issues.md #25).

The assumed house: 1995 bungalow, 140 m² living area, owner-occupied, 20 kW gas boiler from
2012 that is still functional. The variant additionally gets a scenario analysis over gas/
electricity price escalations, interest and a cheaper heat pump.

Run it manually from the repository root:

    python system_setups/economic_example/economic_example_ireland_gas_to_heatpump.py

The last step re-evaluates both stored runs and writes the variant-vs-reference comparison
(section 8 of `lifecycle_report.html`: NPV delta waterfall per subject, discounted payback
curve with the uncertainty band, delta table) into the VARIANT's result directory.
"""

# clean

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hisim.economics import EconomicParameters, ExistingAsset, ExistingAssetRegister  # noqa: E402
from hisim.economics.__main__ import main as economics_cli  # noqa: E402
from hisim.economics.bridge import EconomicContext  # noqa: E402
from hisim.economics.carriers import EnergyCarrier  # noqa: E402
from hisim.economics.scenarios import ScenarioSet  # noqa: E402
from hisim.hisim_main import initialize_from_python  # noqa: E402
from hisim.loadtypes import ComponentType, Units  # noqa: E402
from hisim.postprocessingoptions import PostProcessingOptions  # noqa: E402
from hisim.simulationparameters import SimulationParameters  # noqa: E402

WEATHER_YEAR = 2021  # simulated physics
PRICE_BASIS_YEAR = 2026  # economic "today": IE banded data, IE CO2 path

SETUPS_DIR = Path(__file__).resolve().parents[1]

#: The existing 20 kW gas boiler of the assumed house (installed 2012, still functional).
OLD_GAS_BOILER = dict(
    asset_class=ComponentType.GAS_HEATER,
    size=20.0,
    size_unit=Units.KILOWATT,
    installation_year=2012,
    is_functional=True,
    energy_carrier=EnergyCarrier.NATURAL_GAS,
)


def irish_parameters() -> EconomicParameters:
    """Irish economics; no subsidy catalog ships for IE yet (flat SEAI-like shim applies)."""
    return EconomicParameters(country="IE", price_basis_year=PRICE_BASIS_YEAR)


def shared_context_fields() -> dict:
    """The assumed house, identical in both variants."""
    return dict(
        living_area_in_m2=140.0,
        heated_floor_area_in_m2=140.0,
        current_cold_rent_in_euro_per_m2_month=12.0,  # Irish rents, for the landlord/tenant view
        annual_heat_demand_in_kwh=16000.0,
    )


def reference_context() -> EconomicContext:
    """Keep the gas system: the boiler is registered and *kept* (no replacement declared)."""
    return EconomicContext(
        existing_assets=ExistingAssetRegister(assets=[ExistingAsset(**OLD_GAS_BOILER)]),
        # A gas house sits in a mid CO2KostAufG tier — relevant for the tenant/landlord split:
        building_specific_emissions_in_kg_per_m2_a=30.0,
        **shared_context_fields(),
    )


def variant_context() -> EconomicContext:
    """Switch to the heat pump: the same boiler, now replaced by the HEAT_PUMP measure."""
    return EconomicContext(
        existing_assets=ExistingAssetRegister(
            assets=[ExistingAsset(**OLD_GAS_BOILER, replaced_by_asset_classes=[ComponentType.HEAT_PUMP])]
        ),
        technical_attributes_by_subject={
            "MoreAdvancedHeatPumpHPLib": {"scop": 4.0, "refrigerant": "R290", "heat_source": "air"},
        },
        building_specific_emissions_in_kg_per_m2_a=9.0,  # after the switch: lowest tier
        scenario_set=ScenarioSet.from_json(
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
                        "name": "hp_price",
                        "field": "devices_IE.HEAT_PUMP.specific_investment",
                        "levels": {"cheap": {"min": 1000, "avg": 1350, "max": 1850}},
                    },
                ],
            }
        ),
        **shared_context_fields(),
    )


def run_simulation(setup_file: str, context: EconomicContext) -> str:
    """One full-year run with the lifecycle report; returns the result directory."""
    params = SimulationParameters.full_year(year=WEATHER_YEAR, seconds_per_timestep=60 * 15)
    params.post_processing_options.append(PostProcessingOptions.LIFECYCLE_COST_REPORT)
    params.logging_level = 3
    params.set_economic_parameters(irish_parameters())
    params.set_economic_context(context)
    my_sim = initialize_from_python(
        path_to_module=str(SETUPS_DIR / setup_file), my_simulation_parameters=params
    )
    my_sim.run_all_timesteps()
    return my_sim._simulation_parameters.result_directory  # noqa: SLF001


def main() -> None:
    """Reference (keep gas) vs. variant (heat pump), then the comparison report."""
    start = time.time()
    print("=== 1/3: reference simulation (keep the gas system) ===")
    reference_dir = run_simulation("household_gas_building_sizer.py", reference_context())
    print("=== 2/3: variant simulation (switch to the heat pump) ===")
    variant_dir = run_simulation("household_heatpump_building_sizer.py", variant_context())

    print("=== 3/3: variant-vs-reference comparison report ===")
    parameters_path = Path(variant_dir) / "economic_parameters_IE.json"
    with open(parameters_path, "w", encoding="utf-8") as file:
        json.dump(irish_parameters().to_dict(), file, indent=2)
    scenarios_path = Path(variant_dir) / "scenarios_IE.json"
    with open(scenarios_path, "w", encoding="utf-8") as file:
        json.dump(
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
                        "name": "hp_price",
                        "field": "devices_IE.HEAT_PUMP.specific_investment",
                        "levels": {"cheap": {"min": 1000, "avg": 1350, "max": 1850}},
                    },
                ],
            },
            file,
            indent=2,
        )
    economics_cli(
        [
            "report", str(variant_dir),
            "--compare", str(reference_dir),
            "--parameters", str(parameters_path),
            "--scenarios", str(scenarios_path),
        ]
    )

    print(f"\nDONE in {time.time() - start:.0f} s")
    print("REFERENCE (gas kept):   ", reference_dir)
    print("VARIANT (heat pump):    ", variant_dir)
    print("Open the variant's lifecycle_report.html — section 8 holds the gas-vs-heat-pump comparison.")


if __name__ == "__main__":
    main()
