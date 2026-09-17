"""The dead gas-heater configuration of `configuration.py` (D-8).

Moved out of `hisim/components/configuration.py` by component sweep decision D-8
(2026-09-10), the way D-16 moved its legacy neighbours into `configuration_legacy.py` and
D-29 the hydrogen pair into `configuration_hydrogen.py`. `configuration.py` keeps everything
else -- `EmissionFactorsAndCostsForFuelsConfig`, `EmissionFactorsAndCostsForDevicesConfig`,
`PhysicsConfig` and the live `HouseholdWarmWaterDemandConfig`, which the sweep marks exempt as
data tables rather than component configurations.

`GasHeaterConfig` was verified to have zero references across `hisim/`, `tests/`,
`system_setups/`, `energy_systems/`, `scripts/` and `docs/` before the move; the only mentions
left in the tree are the sweep documents that decided its fate. It is a plain class of class
attributes rather than a `ConfigBase` dataclass, so it could carry no preset and reach no
energy-system file, and it was superseded by `hisim/components/generic_boiler.py`'s
`GenericBoilerConfig`, which models the same 12 kW modulating boiler as a converted
configuration: a 1 kW to 12 kW band at 0.60 to 0.90 efficiency is
`GenericBoilerConfig.preset_condensing_gas_12kw`.

The three siblings D-8 decided together with it -- `GasControllerConfig`,
`CHPControllerConfig` and `ExtendedControllerConfig` -- left in the same decision's first
commit and are in `configuration_fuel_cell_controller.py`, with the controller that read them,
`advanced_fuel_cell_controller.py`.

The class below is the file's own text, unchanged. Nothing here is maintained.
"""


class GasHeaterConfig:
    """Gas heater config class."""

    is_modulating = True
    P_th_min = 1_000  # [W]
    P_th_max = 12_000  # [W]
    eff_th_min = 0.60  # [-]
    eff_th_max = 0.90  # [-]
    delta_temperature = 25
    mass_flow_max = P_th_max / (4180 * delta_temperature)  # kg/s ## -> ~0.07
    temperature_max = 80  # [°C]
