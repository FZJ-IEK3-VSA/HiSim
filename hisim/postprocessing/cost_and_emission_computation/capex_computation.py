"""Module for capex and emission computation."""

from typing import Any, Dict, List, Optional, Tuple
from hisim.component import CapexCostDataClass, ConfigBase
from hisim.components.configuration import EmissionFactorsAndCostsForDevicesConfig
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.loadtypes import ComponentType, Units
from hisim.units import Quantity


#: The capex fields carried by every component config. A config may set any subset of them;
#: whatever it leaves as ``None`` is filled from the device database, field by field.
CAPEX_CONFIG_FIELD_NAMES = (
    "investment_costs_in_euro",
    "device_co2_footprint_in_kg",
    "lifetime_in_years",
    "maintenance_costs_in_euro_per_year",
    "subsidy_as_percentage_of_investment_costs",
)


class CapexDefaultsRegistry:
    """Collects the capex fields that were filled from the device database.

    Because a config may leave any subset of its capex fields unset, a number in the cost
    report is not visibly distinguishable from one the user actually chose. Entries pile up
    while the post processor computes costs and are reported in a single block at the end of
    the run, so the fallbacks are not lost among the rest of the log output.
    """

    _defaulted_fields_per_component: Dict[str, List[str]] = {}
    _sources: List[str] = []

    @classmethod
    def reset(cls) -> None:
        """Drop everything recorded so far. Called once at the start of post processing."""
        cls._defaulted_fields_per_component = {}
        cls._sources = []

    @classmethod
    def record(cls, component_name: str, field_name: str, source: str) -> None:
        """Note that ``field_name`` of ``component_name`` was taken from the device database."""
        recorded_fields = cls._defaulted_fields_per_component.setdefault(component_name, [])
        # get_cost_capex is called more than once per component (opex maintenance, capex table,
        # KPIs), so the same field must not be listed repeatedly.
        if field_name not in recorded_fields:
            recorded_fields.append(field_name)
        if source not in cls._sources:
            cls._sources.append(source)

    @classmethod
    def get_defaulted_fields_per_component(cls) -> Dict[str, List[str]]:
        """Return the recorded fields per component, in the order they were first seen."""
        return {component: list(fields) for component, fields in cls._defaulted_fields_per_component.items()}

    @classmethod
    def log_summary(cls) -> None:
        """Log one warning listing every capex value that fell back to a database default."""
        if not cls._defaulted_fields_per_component:
            return
        component_lines = [
            f"  {component_name}: {', '.join(field_names)}"
            for component_name, field_names in cls._defaulted_fields_per_component.items()
        ]
        log.warning(
            "The following default values had to be used because the component configurations left "
            "them unset:\n"
            + "\n".join(component_lines)
            + "\nThey were taken from EmissionFactorsAndCostsForDevicesConfig, looked up for "
            + f"{'; '.join(cls._sources)}."
        )


class CapexComputationHelperFunctions:
    """Helper functions for capex and emission computation."""

    @staticmethod
    def get_plain_value_of_config_field(value: Any, config: ConfigBase, field_name: str) -> Optional[float]:
        """Return the plain number behind a capex config field, or ``None`` when it is unset.

        Args:
            value: The raw field value — a number, a ``Quantity``, or ``None``.
            config: The config the field belongs to, used for the error message only.
            field_name: Name of the field, used for the error message only.

        Returns:
            The field as a float, or ``None`` if the config does not specify it.

        Raises:
            ValueError: If the field holds something other than a number, ``Quantity`` or ``None``.
        """
        if value is None:
            return None
        if isinstance(value, Quantity):
            return float(value.value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        raise ValueError(
            f"Config field {config.get_main_classname()}.{field_name} has the unsupported type "
            f"{type(value)}. Expected a number, a Quantity or None."
        )

    @staticmethod
    def get_investment_and_co2_factors_for_unit(
        emissions_and_cost_factors_for_devices: EmissionFactorsAndCostsForDevicesConfig,
        unit: Units,
    ) -> Tuple[Optional[float], Optional[float]]:
        """Pick the per-size-unit investment cost and CO2 footprint matching ``unit``.

        Either value may be ``None`` when the database has no factor in that unit; that only
        matters if the config leaves the corresponding field unset, so it is reported by the
        caller rather than here.

        Raises:
            ValueError: If ``unit`` is not one of the supported units.
        """
        factors = emissions_and_cost_factors_for_devices
        if unit == Units.KILOWATT:
            # Size of energy system is in kW
            return factors.investment_costs_in_euro_per_kw, factors.co2_footprint_in_kg_per_kw
        if unit == Units.KWH:
            # Size of energy system is in kWh
            return factors.investment_costs_in_euro_per_kwh, factors.co2_footprint_in_kg_per_kwh
        if unit == Units.LITER:
            # Size of energy system is in l
            return factors.investment_costs_in_euro_per_liter, factors.co2_footprint_in_kg_per_liter
        if unit == Units.SQUARE_METER:
            # Size of energy system is in m2
            return factors.investment_costs_in_euro_per_m2, factors.co2_footprint_in_kg_per_m2
        if unit == Units.ANY:
            # Size of energy system has no unit
            return factors.investment_costs_in_euro, factors.co2_footprint_in_kg
        raise ValueError(f"Unit {unit} of the energy system is not valid or not implemented yet.")

    @staticmethod
    def get_resolved_value(
        capex_values: Dict[str, Optional[float]], field_name: str, component_name: str
    ) -> float:
        """Read a capex value that must have been resolved by now.

        Raises:
            ValueError: If the field is still ``None``, which would mean a fallback was missed.
        """
        value = capex_values[field_name]
        if value is None:
            raise ValueError(f"Capex value {field_name} of {component_name} could not be determined.")
        return value

    @staticmethod
    def compute_capex_costs_and_emissions(
        simulation_parameters: SimulationParameters,
        component_type: ComponentType,
        unit: Units,
        size_of_energy_system: float,
        config: ConfigBase,  # these are component configs
        kpi_tag: Optional[KpiTagEnumClass] = None,
    ) -> CapexCostDataClass:
        """Compute CAPEX costs and CO2 emissions for a component over the simulated period.

        Every capex field ``config`` specifies — as a plain number or a ``Quantity`` — is used as
        given. Each field left ``None`` is filled individually from
        ``EmissionFactorsAndCostsForDevicesConfig`` for the simulation year and country, scaled by
        ``size_of_energy_system`` according to ``unit`` where the database factor is per size unit.
        A config may therefore mix its own values with defaults, and every field that fell back is
        recorded in ``CapexDefaultsRegistry`` so the post processor can report them together.

        Args:
            simulation_parameters: Provides year, country, and simulated duration used
                for yearly-cost proration and device-factor lookup.
            component_type: The component type whose device cost/emission factors are
                retrieved for the fields the config leaves unset.
            unit: Unit of ``size_of_energy_system`` (e.g. ``Units.KILOWATT``, ``Units.KWH``,
                ``Units.LITER``, ``Units.SQUARE_METER``, ``Units.ANY``); selects the
                per-unit cost/emission factor.
            size_of_energy_system: Capacity or size of the component in the given unit.
            config: Component config supplying whichever capex fields it has values for.
            kpi_tag: Optional KPI category tag attached to the returned data class.

        Returns:
            A ``CapexCostDataClass`` with total and per-simulated-period investment cost,
            CO2 footprint, maintenance cost, lifetime, and subsidy percentage (rounded to
            2 decimals).

        Raises:
            ValueError: If ``unit`` is not one of the supported units.
            ValueError: If a capex field is unset in the config and the device database holds no
                value for it either.
            ValueError: If a capex field holds something other than a number, a ``Quantity``
                or ``None``.
        """
        component_name = config.name or config.get_main_classname()
        capex_values: Dict[str, Optional[float]] = {
            field_name: CapexComputationHelperFunctions.get_plain_value_of_config_field(
                value=getattr(config, field_name), config=config, field_name=field_name
            )
            for field_name in CAPEX_CONFIG_FIELD_NAMES
        }
        unset_field_names = [field_name for field_name, value in capex_values.items() if value is None]

        if unset_field_names:
            # Only the unset fields are looked up. A config that specifies everything never needs
            # the database, so components without an entry there keep working.
            log.debug(
                f"Using EmissionFactorsAndCostsForDevicesConfig defaults for {config.get_main_classname()} "
                f"fields: {', '.join(unset_field_names)}."
            )
            emissions_and_cost_factors_for_devices = EmissionFactorsAndCostsForDevicesConfig.get_values_for_year(
                year=simulation_parameters.year, device=component_type, country=simulation_parameters.country
            )
            (
                investment_costs_in_euro_per_size_unit,
                co2_footprint_in_kg_per_size_unit,
            ) = CapexComputationHelperFunctions.get_investment_and_co2_factors_for_unit(
                emissions_and_cost_factors_for_devices=emissions_and_cost_factors_for_devices, unit=unit
            )
            source = f"country {simulation_parameters.country}, year {simulation_parameters.year}"

            def fill_from_database(field_name: str, default_value: Optional[float]) -> float:
                """Fill one unset field, or keep the config's own value, and return the result."""
                value_from_config = capex_values[field_name]
                if value_from_config is not None:
                    return value_from_config
                if default_value is None:
                    raise ValueError(
                        f"{field_name} of {component_name} is not set in the config and the device "
                        f"database holds no value for component {component_type} with unit {unit}."
                    )
                capex_values[field_name] = default_value
                CapexDefaultsRegistry.record(
                    component_name=component_name, field_name=field_name, source=source
                )
                return default_value

            investment_costs_maintenance_is_based_on = fill_from_database(
                "investment_costs_in_euro",
                None
                if investment_costs_in_euro_per_size_unit is None
                else investment_costs_in_euro_per_size_unit * size_of_energy_system,
            )
            fill_from_database(
                "device_co2_footprint_in_kg",
                None
                if co2_footprint_in_kg_per_size_unit is None
                else co2_footprint_in_kg_per_size_unit * size_of_energy_system,
            )
            # These values are independent of the size unit of the energy system.
            fill_from_database(
                "lifetime_in_years", emissions_and_cost_factors_for_devices.technical_lifetime_in_years
            )
            fill_from_database(
                "subsidy_as_percentage_of_investment_costs",
                emissions_and_cost_factors_for_devices.subsidy_as_percentage_of_investment_costs,
            )
            # The database expresses maintenance as a share of the investment, so it follows
            # whichever investment cost is in use — the config's own one if it has any.
            maintenance_costs_as_percentage_of_investment_per_year = (
                emissions_and_cost_factors_for_devices.maintenance_costs_as_percentage_of_investment_per_year
            )
            fill_from_database(
                "maintenance_costs_in_euro_per_year",
                None
                if maintenance_costs_as_percentage_of_investment_per_year is None
                else investment_costs_maintenance_is_based_on * maintenance_costs_as_percentage_of_investment_per_year,
            )
        else:
            log.debug(f"Using config values for {config.get_main_classname()} capex calculation.")

        # Every field is resolved by now; the lookups keep mypy from seeing Optionals downstream.
        capex_investment_cost_in_euro = CapexComputationHelperFunctions.get_resolved_value(
            capex_values, "investment_costs_in_euro", component_name
        )
        device_co2_footprint_in_kg = CapexComputationHelperFunctions.get_resolved_value(
            capex_values, "device_co2_footprint_in_kg", component_name
        )
        technical_lifetime_in_years = CapexComputationHelperFunctions.get_resolved_value(
            capex_values, "lifetime_in_years", component_name
        )
        maintenance_costs_in_euro = CapexComputationHelperFunctions.get_resolved_value(
            capex_values, "maintenance_costs_in_euro_per_year", component_name
        )
        subsidy_as_percentage_of_investment_costs = CapexComputationHelperFunctions.get_resolved_value(
            capex_values, "subsidy_as_percentage_of_investment_costs", component_name
        )

        # Calculate values per simulated period
        seconds_per_year = 365 * 24 * 60 * 60
        capex_per_simulated_period = (capex_investment_cost_in_euro / technical_lifetime_in_years) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )
        device_co2_footprint_per_simulated_period = (device_co2_footprint_in_kg / technical_lifetime_in_years) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )
        maintenance_costs_per_simulated_period_in_euro = (maintenance_costs_in_euro / technical_lifetime_in_years) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )
        capex_cost_data_class = CapexCostDataClass(
            capex_investment_cost_in_euro=round(capex_investment_cost_in_euro, 2),
            device_co2_footprint_in_kg=round(device_co2_footprint_in_kg, 2),
            lifetime_in_years=round(technical_lifetime_in_years, 2),
            capex_investment_cost_for_simulated_period_in_euro=round(capex_per_simulated_period, 2),
            device_co2_footprint_for_simulated_period_in_kg=round(device_co2_footprint_per_simulated_period, 2),
            maintenance_costs_in_euro=round(maintenance_costs_in_euro, 2),
            maintenance_cost_per_simulated_period_in_euro=round(maintenance_costs_per_simulated_period_in_euro, 2),
            subsidy_as_percentage_of_investment_costs=round(subsidy_as_percentage_of_investment_costs, 2),
            kpi_tag=kpi_tag,
        )
        return capex_cost_data_class

    @staticmethod
    def overwrite_config_values_with_new_capex_values(config: ConfigBase, capex_cost_data_class: CapexCostDataClass) -> ConfigBase:
        """Overwrite capex-related fields on ``config`` with values from a ``CapexCostDataClass``.

        Args:
            config: The component config to mutate in place.
            capex_cost_data_class: Source of the new investment cost, CO2 footprint,
                lifetime, maintenance cost, and subsidy percentage values.

        Returns:
            The same ``config`` object, with updated capex fields.
        """
        log.debug(f"Overwriting {config.get_main_classname()} config values with new capex values.")
        config.investment_costs_in_euro = capex_cost_data_class.capex_investment_cost_in_euro
        config.device_co2_footprint_in_kg = capex_cost_data_class.device_co2_footprint_in_kg
        config.lifetime_in_years = capex_cost_data_class.lifetime_in_years
        config.maintenance_costs_in_euro_per_year = capex_cost_data_class.maintenance_costs_in_euro
        config.subsidy_as_percentage_of_investment_costs = (
            capex_cost_data_class.subsidy_as_percentage_of_investment_costs
        )
        return config
