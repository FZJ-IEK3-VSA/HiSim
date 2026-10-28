"""Gas meter module providing a DynamicComponent-based gas meter.

The GasMeter tracks gas production and consumption per timestep, accumulates the
running totals in GasMeterState, and supplies OPEX/CAPEX and KPI data for the
post-processing cost and emission computation.
"""

from dataclasses import dataclass
from typing import ClassVar, List, Optional, Tuple

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import dynamic_component
from hisim import loadtypes as lt
from hisim.component import ComponentInput, ComponentOutput, OpexCostDataClass, CapexCostDataClass
from hisim.config import (
    ComponentID,
    ConfigBase,
    DisplayConfig,
    Sizable,
    Size,
    SizingLaw,
    concrete,
    preset,
    sized_field,
)
from hisim.config.channels import (
    DispatchRule,
    DynamicConnectionChannel,
    PortTypeCompatibility,
    ResolvedDynamicConnection,
)
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.dynamic_component import (
    DynamicComponent,
    DynamicConnectionInput,
    DynamicConnectionOutput,
)
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions
from hisim.economics.facts import CostRelevance
from hisim.energy_port import EnergyPort, MeteredExchange


@dataclass_json
@dataclass
class GasMeterConfig(ConfigBase):
    """Configuration dataclass for the GasMeter component.

    Holds the component identity, the gas this meter measures (``GAS`` or
    ``GREEN_HYDROGEN``) and the optional CAPEX/emission parameters the cost post-processing
    reads. The named default is :meth:`preset_standard`, and it leaves the carrier ``AUTO``:
    a meter measures whatever the generator beside it burns, so :data:`CARRIER_LAW` copies
    the carrier off that generator's configuration instead of the author stating it twice.
    Pinning ``gas_loadtype`` is still allowed and is what a system with no converted
    generator in it does.
    """

    MAIN_CLASS = "hisim.components.gas_meter.GasMeter"

    #: Sizing law of ``gas_loadtype``: the carrier is copied from the generator that feeds
    #: this meter, which contributes it as the ``energy_carrier`` fact. Named as a
    #: ClassVar so the field declaration reads as one line. A gas boiler beside a meter
    #: configured for green hydrogen is what the copy makes unstateable; the meter's own
    #: ``__init__`` still refuses a carrier it cannot account.
    CARRIER_LAW: ClassVar[SizingLaw] = Size.ENERGY_CARRIER

    component_id: ComponentID
    #: CO2 footprint of investment in kg. ``None`` lets postprocessing look the device up in
    #: the cost database, which is what every meter in the repository does.
    device_co2_footprint_in_kg: Optional[float] = None
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float] = None
    #: lifetime in years
    lifetime_in_years: Optional[float] = None
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float] = None
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float] = None
    #: The gas this meter measures. Sizable: left ``AUTO`` it is copied from the generator by
    #: :data:`CARRIER_LAW`.
    gas_loadtype: Sizable[lt.LoadTypes] = sized_field(rule=CARRIER_LAW, value_type=lt.LoadTypes)

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "GasMeterConfig":
        """The gas meter of a household, measuring whatever its generator burns.

        The only preset the class has, and it fixes nothing: the capex fields keep their
        ``None`` defaults, so post-processing looks the device up in the cost database, and
        ``gas_loadtype`` stays ``AUTO`` so :data:`CARRIER_LAW` copies it from the generator.
        There are deliberately no ``gas``/``hydrogen`` presets pinning a carrier, because a
        preset that pins one is a second place to state a fact the generator already states.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            GasMeterConfig: The preset configuration, with the carrier unsized.
        """
        return cls(component_id=ComponentID(name=name))


class GasMeter(DynamicComponent, cp.MeterComponent):
    """Gas meter class.

    It calculates the gas production and consumption dynamically for all components.
    So far only gas consumers are represented here but gas producers can be added here too.
    A consumer publishes the gas it burns either as an energy per step in Wh (a boiler's
    ``EnergyDemandSh``) or as a mass flow in kg/s (a CHP's ``GasDemandReal``); the meter counts a
    mass flow by the lower heating value of its gas, the same value the CHP's fuel port is counted
    by. For example, 0.001 kg/s of hydrogen over a 900 s step is 0.9 kg, which the meter counts as
    0.9 kg * 33.32 kWh/kg = 29,989 Wh.
    """

    cost_relevance = CostRelevance.METER

    # Outputs
    GasAvailable: ClassVar[str] = "GasAvailable"
    GasFromGrid: ClassVar[str] = "GasFromGrid"
    GasConsumption: ClassVar[str] = "GasConsumption"
    GasProduction: ClassVar[str] = "GasProduction"
    CumulativeConsumption: ClassVar[str] = "CumulativeConsumption"
    CumulativeProduction: ClassVar[str] = "CumulativeProduction"

    #: Stable key of the channel carrying gas a participant feeds into the system.
    PRODUCTION_CHANNEL: ClassVar[str] = "production"

    #: Stable key of the channel carrying gas a participant burned.
    CONSUMPTION_UNCONTROLLED_CHANNEL: ClassVar[str] = "consumption_uncontrolled"

    #: Stable key of the channel carrying gas a participant burns as a mass flow in kg/s, such as a CHP's fuel.
    CONSUMPTION_MASS_FLOW_CHANNEL: ClassVar[str] = "consumption_mass_flow"

    #: The two flows this meter understands, declared so that an energy-system file can address a
    #: gas burner or a gas producer at it. The consumption channel's tag, unit and monitored-only
    #: weight are the ones :meth:`get_default_connections_from_generic_gas_heater` already builds
    #: and :meth:`i_simulate` already queries; the production channel's are queried by
    #: :meth:`i_simulate` alone, since no default connection builds a producer and an explicit
    #: energy-system file is the only thing that wires one today. Declaring both adds no wiring
    #: and changes none.
    #:
    #: The load type is the wildcard on both, because which gas this meter measures is the
    #: meter's own ``gas_loadtype`` and the burner's own carrier — natural gas in one household,
    #: green hydrogen in the next — and not a property of the flow the channel describes. The
    #: unit stays strict: both sums are in watt-hours whichever gas it is.
    #:
    #: Dispatch is forbidden on both. A meter is a pure aggregator: it has no control authority
    #: and no per-participant output to send a signal on.
    #:
    #: A burner that publishes its fuel as a mass flow in kg/s feeds the third channel, tagged
    #: ``MASS_FLOW`` beside the consumption tag. Its tag set is the wider of the two consumption
    #: channels, so a feed carrying both tags selects it, and its unit is as strict as the others':
    #: kg/s only, which the meter converts by the gas's lower heating value.
    CHANNELS: Tuple[DynamicConnectionChannel, ...] = (
        DynamicConnectionChannel(
            key=PRODUCTION_CHANNEL,
            tags=frozenset({lt.InandOutputType.GAS_PRODUCTION}),
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.WATT_HOUR,
            dispatch=DispatchRule.FORBIDDEN,
        ),
        DynamicConnectionChannel(
            key=CONSUMPTION_UNCONTROLLED_CHANNEL,
            tags=frozenset({lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED}),
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.WATT_HOUR,
            dispatch=DispatchRule.FORBIDDEN,
        ),
        DynamicConnectionChannel(
            key=CONSUMPTION_MASS_FLOW_CHANNEL,
            tags=frozenset({lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED, lt.InandOutputType.MASS_FLOW}),
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.KG_PER_SEC,
            dispatch=DispatchRule.FORBIDDEN,
        ),
    )

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GasMeterConfig,
        my_display_config: DisplayConfig = DisplayConfig(display_in_webtool=True),
    ) -> None:
        """Initialize the component."""
        self.grid_energy_balancer_config = config
        self.name = self.grid_energy_balancer_config.component_id.name
        self.my_component_inputs: List[DynamicConnectionInput] = []
        self.my_component_outputs: List[DynamicConnectionOutput] = []
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_component_inputs=self.my_component_inputs,
            my_component_outputs=self.my_component_outputs,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        #: The carrier this meter accounts, read off the configuration once. The field is
        #: sizable, so a law may have filled it in; ``concrete`` is the read site's statement
        #: that it has been resolved by the time a component is built.
        self.gas_loadtype: lt.LoadTypes = concrete(self.config.gas_loadtype)
        # check if component has valid gas loadtype
        if self.gas_loadtype not in [lt.LoadTypes.GAS, lt.LoadTypes.GREEN_HYDROGEN]:
            raise ValueError(f"GasMeter {self.component_name} has invalid gas loadtype: {self.gas_loadtype}. "
                             f"Either use {lt.LoadTypes.GAS} or {lt.LoadTypes.GREEN_HYDROGEN} or add new gas_type")

        self.production_inputs: List[ComponentInput] = []
        self.consumption_uncontrolled_inputs: List[ComponentInput] = []
        #: The consumers that publish the gas they burn as a mass flow in kg/s (:attr:`CONSUMPTION_MASS_FLOW_CHANNEL`).
        self.consumption_mass_flow_inputs: List[ComponentInput] = []

        self.timestep_duration_in_s = self.my_simulation_parameters.seconds_per_timestep
        #: The Wh one step of a mass flow of 1 kg/s of this meter's gas carries, by its lower heating value: the factor
        #: the energy balance counts a fuel port on a mass flow by (:meth:`EnergyPort.kilowatt_hours_per_step`), in Wh.
        self.watt_hours_per_step_of_one_kilogram_per_second: float = 1e3 * EnergyPort.kilowatt_hours_per_step(
            lt.Units.KG_PER_SEC, self.timestep_duration_in_s, EnergyPort.carrier_of_fuel(self.gas_loadtype)
        )
        # Component has states
        self.state = GasMeterState(cumulative_production_in_watt_hour=0, cumulative_consumption_in_watt_hour=0)
        self.previous_state = self.state.self_copy()

        # Outputs
        self.gas_available_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.GasAvailable,
            load_type=self.gas_loadtype,
            unit=lt.Units.WATT,
            output_description=f"here a description for {self.GasAvailable} will follow.",
        )

        self.gas_from_grid_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.GasFromGrid,
            load_type=self.gas_loadtype,
            unit=lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.GasFromGrid} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )

        self.gas_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.GasConsumption,
            load_type=self.gas_loadtype,
            unit=lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.GasConsumption} will follow.",
        )

        self.gas_production_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.GasProduction,
            load_type=self.gas_loadtype,
            unit=lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.GasProduction} will follow.",
        )

        self.cumulative_gas_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeConsumption,
            load_type=self.gas_loadtype,
            unit=lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.CumulativeConsumption} will follow.",
            aggregation=lt.OutputAggregation.LAST,
        )

        self.cumulative_gas_production_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeProduction,
            load_type=self.gas_loadtype,
            unit=lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.CumulativeProduction} will follow.",
            aggregation=lt.OutputAggregation.LAST,
        )

        self.add_dynamic_default_connections(self.get_default_connections_from_generic_gas_heater())

    def resolve_dynamic_connections(self, connections: List[ResolvedDynamicConnection]) -> None:
        """Checks the carrier of every feed against this meter's own, then creates the ports.

        Both channels this meter declares wildcard the load type, because they are class-level
        declarations and a class cannot know which gas the instance in a given household was
        configured for. The instance does know: :meth:`get_cost_opex` prices and books emissions
        for ``gas_loadtype`` alone. Nothing between the two would notice a green-hydrogen
        feed arriving at a meter configured for natural gas — the numbers would be summed, priced
        as natural gas and reported without a word — so the mismatch is refused here, where the
        configured carrier and the feed's carrier are both in hand for the first time.

        The check runs over the whole batch before a single port is created, so a file this meter
        refuses leaves the component exactly as it was rather than half grown.

        Args:
            connections: The resolved feeds, already validated against this component's channels
                by the energy-system resolver.

        Raises:
            ValueError: If a feed carries a concrete load type this meter is not configured to
                measure. The resolver turns it into a located energy-system error.
        """
        self._check_feeds_carry_the_configured_gas(connections)
        super().resolve_dynamic_connections(connections)

    def _check_feeds_carry_the_configured_gas(
        self, connections: List[ResolvedDynamicConnection]
    ) -> None:
        """Refuses a feed whose carrier this meter would measure but price as something else.

        The legacy default connections type their source by this meter's own ``gas_loadtype``, so
        a setup building them by hand never produced this mismatch. An energy-system file
        addresses feeds at the meter by tag alone and can therefore hand it any gas burner or
        producer in the system, which is exactly the freedom that has to be paid for with this
        check. A source port typed as the wildcard stays accepted, because that is what the
        wildcard is for: the meter's configuration, not the port, decides the carrier.

        Args:
            connections: The resolved feeds to check, in resolution order.

        Raises:
            ValueError: On the first feed whose carrier disagrees with ``gas_loadtype``,
                naming the participant, its output and both carriers.
        """
        for connection in connections:
            carrier = connection.source_port.load_type
            if PortTypeCompatibility.load_types_agree(carrier, self.gas_loadtype):
                continue
            raise ValueError(
                f"the feed '{connection.source_name}.{connection.source_output}' carries "
                f"'{carrier.name}', but this meter is configured to measure and price "
                f"'{self.gas_loadtype.name}' (gas_loadtype); configure the meter for the "
                "carrier it meters, or feed it the matching source."
            )

    def get_default_connections_from_generic_gas_heater(
        self,
    ) -> List[dynamic_component.DynamicComponentConnection]:
        """Get gas heater default connections."""

        from hisim.components.generic_boiler import (  # pylint: disable=import-outside-toplevel
            GenericBoiler,
        )

        dynamic_connections = []
        gas_heater_class_name = GenericBoiler.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=GenericBoiler,
                source_class_name=gas_heater_class_name,
                source_component_field_name=GenericBoiler.EnergyDemandSh,
                source_load_type=self.gas_loadtype,
                source_unit=lt.Units.WATT_HOUR,
                source_tags=[lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED],
                source_weight=999,
            )
        )
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=GenericBoiler,
                source_class_name=gas_heater_class_name,
                source_component_field_name=GenericBoiler.EnergyDemandDhw,
                source_load_type=self.gas_loadtype,
                source_unit=lt.Units.WATT_HOUR,
                source_tags=[lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED],
                source_weight=999,
            )
        )
        return dynamic_connections

    def metered_exchanges(self) -> Tuple[MeteredExchange, ...]:
        """The gas this meter publishes as bought, in Wh: natural gas or hydrogen, as configured (spec §4.4).

        The energy balance draws the meter between the gas's supply node and the burners whose fuel ports it reads,
        and checks per step that ``GasFromGrid`` equals what they burn minus what producers feed in. The meter
        publishes no export: gas a producer feeds in beyond the consumption leaves no output.

        Returns:
            The one exchange of the configured gas.
        """
        return (MeteredExchange(EnergyPort.carrier_of_fuel(self.gas_loadtype), self.GasFromGrid),)

    def sort_inputs_by_channel(self) -> None:
        """Sort the connected inputs into production, consumption in Wh and consumption as a mass flow in kg/s.

        A feed of the mass-flow channel carries the consumption tag as well, so the consumption channel's query
        returns it too; it is summed once, converted, with the mass flows. Called at the first step, once the inputs
        are connected. For example, a meter fed by a boiler's ``EnergyDemandSh`` in Wh and a CHP's ``GasDemandReal``
        in kg/s, tagged ``MASS_FLOW`` beside the consumption tag, sums the first as it is and converts the second.

        Raises:
            ValueError: For an input whose unit is not the unit of its channel: a mass flow fed without the
                ``MASS_FLOW`` tag would otherwise be summed as watt-hours.
        """
        self.production_inputs = self.get_channel_inputs(self.PRODUCTION_CHANNEL)
        self.consumption_mass_flow_inputs = self.get_channel_inputs(self.CONSUMPTION_MASS_FLOW_CHANNEL)
        mass_flows = {id(component_input) for component_input in self.consumption_mass_flow_inputs}
        self.consumption_uncontrolled_inputs = [
            component_input
            for component_input in self.get_channel_inputs(self.CONSUMPTION_UNCONTROLLED_CHANNEL)
            if id(component_input) not in mass_flows
        ]
        for channel_key, inputs in (
            (self.PRODUCTION_CHANNEL, self.production_inputs),
            (self.CONSUMPTION_UNCONTROLLED_CHANNEL, self.consumption_uncontrolled_inputs),
            (self.CONSUMPTION_MASS_FLOW_CHANNEL, self.consumption_mass_flow_inputs),
        ):
            unit = self.get_channel(channel_key).unit
            for component_input in inputs:
                if component_input.unit != unit:
                    raise ValueError(
                        f"The gas meter '{self.component_name}' reads {component_input.src_object_name}."
                        f"{component_input.src_field_name} in {component_input.unit.value} on its channel "
                        f"'{channel_key}', which is in {unit.value}. Feed a mass flow in kg/s with the tags "
                        f"{lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED.name} and {lt.InandOutputType.MASS_FLOW.name}, "
                        "and an energy per step in Wh with the consumption or the production tag alone."
                    )

    def write_to_report(self) -> List[str]:
        """Writes relevant information to report."""
        # Note: despite the name, ConfigBase.get_string_dict() returns a List[str]
        # (one formatted "key: value" string per config field), not a dict.
        return self.grid_energy_balancer_config.get_string_dict()

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.self_copy()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.self_copy()

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks values."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the grid energy balancer."""

        if timestep == 0:
            self.sort_inputs_by_channel()

        # GAS #

        # get sum of production and consumption for all inputs for each iteration
        production_in_watt_hour = sum([stsv.get_input_value(component_input=elem) for elem in self.production_inputs])
        consumption_uncontrolled_in_watt_hour = sum(
            [stsv.get_input_value(component_input=elem) for elem in self.consumption_uncontrolled_inputs]
        )
        if self.consumption_mass_flow_inputs:
            consumption_uncontrolled_in_watt_hour += self.watt_hours_per_step_of_one_kilogram_per_second * sum(
                [stsv.get_input_value(component_input=elem) for elem in self.consumption_mass_flow_inputs]
            )
        # Production of Gas positve sign
        # Consumption of Gas negative sign
        difference_between_production_and_consumption_in_watt_hour = (
            production_in_watt_hour - consumption_uncontrolled_in_watt_hour
        )

        # calculate cumulative production and consumption
        cumulative_production_in_watt_hour = self.state.cumulative_production_in_watt_hour + production_in_watt_hour
        cumulative_consumption_in_watt_hour = (
            self.state.cumulative_consumption_in_watt_hour + consumption_uncontrolled_in_watt_hour
        )

        # consumption is bigger than production -> gas from grid is needed
        # change sign so that value becomes positive
        if difference_between_production_and_consumption_in_watt_hour < 0:
            gas_from_grid_in_watt_hour = -difference_between_production_and_consumption_in_watt_hour
        # production is bigger -> gas can be fed into grid
        elif difference_between_production_and_consumption_in_watt_hour > 0:
            gas_from_grid_in_watt_hour = 0.0

        # difference between production and consumption is zero
        else:
            gas_from_grid_in_watt_hour = 0.0

        # set outputs
        stsv.set_output_value(
            self.gas_available_channel,
            difference_between_production_and_consumption_in_watt_hour,
        )

        stsv.set_output_value(self.gas_from_grid_channel, gas_from_grid_in_watt_hour)
        stsv.set_output_value(
            self.gas_consumption_channel,
            consumption_uncontrolled_in_watt_hour,
        )

        stsv.set_output_value(
            self.gas_production_channel,
            production_in_watt_hour,
        )

        stsv.set_output_value(
            self.cumulative_gas_consumption_channel,
            cumulative_consumption_in_watt_hour,
        )

        stsv.set_output_value(
            self.cumulative_gas_production_channel,
            cumulative_production_in_watt_hour,
        )

        self.state.cumulative_production_in_watt_hour = cumulative_production_in_watt_hour
        self.state.cumulative_consumption_in_watt_hour = cumulative_consumption_in_watt_hour

    def get_cost_opex(
        self,
        all_outputs: List[ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of gas costs and revenues."""
        total_energy_from_grid_in_kwh: float = 0.0
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.GasFromGrid:
                    total_energy_from_grid_in_kwh = postprocessing_results.iloc[:, index].sum() * 1e-3

        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year, self.my_simulation_parameters.country
        )
        if self.gas_loadtype == lt.LoadTypes.GAS:
            co2_footprint_in_kg_per_kwh = emissions_and_cost_factors.gas_footprint_in_kg_per_kwh
            gas_cost_in_euro_per_kwh = emissions_and_cost_factors.gas_costs_in_euro_per_kwh
        elif self.gas_loadtype == lt.LoadTypes.GREEN_HYDROGEN:
            co2_footprint_in_kg_per_kwh = emissions_and_cost_factors.green_hydrogen_gas_footprint_in_kg_per_kwh
            gas_cost_in_euro_per_kwh = emissions_and_cost_factors.green_hydrogen_gas_costs_in_euro_per_kwh

        opex_cost_per_simulated_period_in_euro = total_energy_from_grid_in_kwh * gas_cost_in_euro_per_kwh
        co2_per_simulated_period_in_kg = total_energy_from_grid_in_kwh * co2_footprint_in_kg_per_kwh
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=opex_cost_per_simulated_period_in_euro,
            opex_maintenance_cost_in_euro=0,
            co2_footprint_in_kg=co2_per_simulated_period_in_kg,
            total_consumption_in_kwh=total_energy_from_grid_in_kwh,
            loadtype=self.gas_loadtype,
            kpi_tag=KpiTagEnumClass.GAS_METER
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List[ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        total_energy_from_grid_in_kwh: Optional[float] = None
        total_energy_consumption_in_kwh: Optional[float] = None
        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name and output.load_type == self.gas_loadtype and output.unit == lt.Units.WATT_HOUR:
                if output.field_name == self.GasFromGrid:
                    total_energy_from_grid_in_kwh = round(postprocessing_results.iloc[:, index].sum() * 1e-3, 1)
                elif output.field_name == self.GasConsumption:
                    total_energy_consumption_in_kwh = round(postprocessing_results.iloc[:, index].sum() * 1e-3, 1)

        total_energy_from_grid_in_kwh_entry = KpiEntry(
            name="Total gas demand from grid",
            unit="kWh",
            value=total_energy_from_grid_in_kwh,
            tag=KpiTagEnumClass.GAS_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_energy_from_grid_in_kwh_entry)
        total_energy_consumption_in_kwh_entry = KpiEntry(
            name="Total gas consumption",
            unit="kWh",
            value=total_energy_consumption_in_kwh,
            tag=KpiTagEnumClass.GAS_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_energy_consumption_in_kwh_entry)

        # try to get opex costs
        opex_costs = self.get_cost_opex(all_outputs=all_outputs, postprocessing_results=postprocessing_results)
        opex_costs_in_euro_entry = KpiEntry(
            name="Opex costs of gas consumption from grid",
            unit="Euro",
            value=opex_costs.opex_energy_cost_in_euro,
            tag=KpiTagEnumClass.GAS_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(opex_costs_in_euro_entry)
        co2_footprint_in_kg_entry = KpiEntry(
            name="CO2 footprint of gas consumption from grid",
            unit="kg",
            value=opex_costs.co2_footprint_in_kg,
            tag=KpiTagEnumClass.GAS_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(co2_footprint_in_kg_entry)

        return list_of_kpi_entries

    @staticmethod
    def get_cost_capex(config: GasMeterConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        component_type = lt.ComponentType.GAS_METER
        kpi_tag = (
            KpiTagEnumClass.GAS_METER
        )
        unit = lt.Units.ANY
        size_of_energy_system = 1

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
        simulation_parameters=simulation_parameters,
        component_type=component_type,
        unit=unit,
        size_of_energy_system=size_of_energy_system,
        config=config,
        kpi_tag=kpi_tag
        )
        config = CapexComputationHelperFunctions.overwrite_config_values_with_new_capex_values(config=config, capex_cost_data_class=capex_cost_data_class)

        return capex_cost_data_class


@dataclass
class GasMeterState:
    """Mutable state for the GasMeter, tracking cumulative gas production and consumption.

    Attributes:
        cumulative_production_in_watt_hour: Total gas produced across all timesteps.
        cumulative_consumption_in_watt_hour: Total gas consumed across all timesteps.
    """

    cumulative_production_in_watt_hour: float
    cumulative_consumption_in_watt_hour: float

    def self_copy(
        self,
    ) -> "GasMeterState":
        """Copy the GasMeterState."""
        return GasMeterState(
            self.cumulative_production_in_watt_hour,
            self.cumulative_consumption_in_watt_hour,
        )
