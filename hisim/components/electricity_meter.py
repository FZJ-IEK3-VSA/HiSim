"""Electricity meter module should replace the sumbuilder. """

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, List, Mapping, Optional, Tuple

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import dynamic_component
from hisim import loadtypes as lt
from hisim.component import ComponentInput, OpexCostDataClass, CapexCostDataClass
from hisim.config import ConfigBase, ComponentID, DisplayConfig, preset
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.config.channels import DispatchRule, DynamicConnectionChannel
from hisim.energy_port import MeteredExchange
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.facts import (
    ComponentCostFacts,
    CostRelevance,
    EnergyFlowFacts,
    missing_meter_column_error,
)
from hisim.dynamic_component import (
    DynamicComponent,
    DynamicConnectionInput,
    DynamicConnectionOutput,
    DynamicComponentConnection,
)
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass, KpiHelperClass
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions


@dataclass_json
@dataclass
class ElectricityMeterConfig(ConfigBase):
    """Electricity Meter Config."""

    MAIN_CLASS = "hisim.components.electricity_meter.ElectricityMeter"

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

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "ElectricityMeterConfig":
        """The one electricity meter: a pure aggregator with nothing to vary.

        A meter has no technology, no rating and no operating mode — it sums what its
        participants produce and consume — so there is exactly one defensible configuration of
        it, and the preset adds nothing to the field defaults but the component's name.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            ElectricityMeterConfig: The preset configuration.
        """
        return cls(component_id=ComponentID(name=name))


class ElectricityMeter(DynamicComponent, cp.MeterComponent):
    """The electricity meter: it reads every electricity port of the house and bills the grid exchange itself.

    The meter reads physics ports only, and nothing in the simulation reads the meter (component design principles,
    kind "Meter"; owner decision of 2026-10-10, bead hisim-component-physics-4g9.39). Its default connections are
    found by port role, not by class (:meth:`default_connections_for`): every output of any component that declares
    an electricity energy port, a ``CONSUMPTION`` port as consumption and a ``PRODUCTION`` port as production. Per step it sums
    them and publishes the grid import ``max(consumption - production, 0)`` and the export
    ``max(production - consumption, 0)``; ``ElectricityConsumption`` and ``ElectricityProduction`` carry the two
    totals. For example, a heat pump taking 2 kW, the residents 0.5 kW and a PV system giving 1 kW make a
    consumption of 2.5 kW, a production of 1 kW and an import of 1.5 kW. A port published with the opposite sign of
    its role (``EnergyPort.negated``, the battery's discharging power) is read with its sign turned.
    """

    # Lifecycle cost engine declaration (roadmap/cost_spec.md §9.2): energy is billed at this
    # carrier boundary; the meter hardware itself is additionally priced via get_cost_facts().
    cost_relevance = CostRelevance.METER

    # Outputs
    ElectricityAvailable = "ElectricityAvailable"
    ElectricityToAndFromGrid = "ElectricityToAndFromGrid"
    ElectricityToGrid = "ElectricityToGrid"
    ElectricityFromGrid = "ElectricityFromGrid"
    ElectricityConsumption = "ElectricityConsumption"
    ElectricityProduction = "ElectricityProduction"
    CumulativeConsumption = "CumulativeConsumption"
    CumulativeProduction = "CumulativeProduction"
    ElectricityToGridInWatt = "ElectricityToGridInWatt"
    ElectricityFromGridInWatt = "ElectricityFromGridInWatt"
    ElectricityProductionInWatt = "ElectricityProductionInWatt"
    ElectricityConsumptionInWatt = "ElectricityConsumptionInWatt"
    ElectricityConsumptionOfBuildingsInWatt = "ElectricityConsumptionOfBuildingsInWatt"
    SurplusUnusedFromBuildingEMSOutput = "SurplusUnusedFromBuildingEMSOutput"

    #: Stable key of the channel carrying electricity a participant produces.
    PRODUCTION_CHANNEL = "production"

    #: Stable key of the channel carrying electricity a participant consumes.
    CONSUMPTION_UNCONTROLLED_CHANNEL = "consumption_uncontrolled"

    #: The flows this meter understands. Until this declaration its accepted tags existed only
    #: implicitly, inside the tag queries of :meth:`i_simulate`, which meant a participant whose
    #: tags matched neither query wired cleanly and was then never summed — a wrong grid balance
    #: that still looked plausible.
    #:
    #: Both channels forbid dispatch: a meter is a pure aggregator with no control authority, and
    #: none of its outputs is a per-participant signal. The district-level queries of
    #: :meth:`i_simulate` add a buildings tag to these same two tag sets; they need no channel of
    #: their own, because subset matching lets a feed carrying the extra tag select the channel
    #: here while the tag itself survives verbatim on the created port and stays visible to the
    #: narrower query.
    #: The flow tag a feed of an electricity port carries, by the port's role: what a component takes in is the
    #: meter's consumption, what it gives out its production.
    FLOW_TAG_OF_ROLE: ClassVar[Mapping[lt.EnergyRole, lt.InandOutputType]] = MappingProxyType(
        {
            lt.EnergyRole.CONSUMPTION: lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
            lt.EnergyRole.PRODUCTION: lt.InandOutputType.ELECTRICITY_PRODUCTION,
        }
    )

    CHANNELS: Tuple[DynamicConnectionChannel, ...] = (
        DynamicConnectionChannel(
            key=PRODUCTION_CHANNEL,
            tags=frozenset({lt.InandOutputType.ELECTRICITY_PRODUCTION}),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.FORBIDDEN,
        ),
        DynamicConnectionChannel(
            key=CONSUMPTION_UNCONTROLLED_CHANNEL,
            tags=frozenset({lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED}),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.FORBIDDEN,
        ),
    )

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ElectricityMeterConfig,
        my_display_config: DisplayConfig = DisplayConfig(display_in_webtool=True),
    ):
        """Initialize the component."""
        self.grid_energy_balancer_config = config
        self.name = self.grid_energy_balancer_config.component_id.name
        self.my_component_inputs: List[DynamicConnectionInput] = []
        self.my_component_outputs: List[DynamicConnectionOutput] = []
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            self.my_component_inputs,
            self.my_component_outputs,
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        #: What the meter reads as production and as consumption: each input with the factor its value is read with,
        #: -1 for a port published with the opposite sign of its role. Sorted at the first step, once the inputs are
        #: connected (:meth:`readings_of_channel`).
        self.production_readings: List[Tuple[ComponentInput, float]] = []
        self.consumption_readings: List[Tuple[ComponentInput, float]] = []
        self.readings_sorted = False
        self.production_inputs_building: List[ComponentInput] = []
        self.consumption_inputs_building: List[ComponentInput] = []

        self.seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        # Component has states
        self.state = ElectricityMeterState(cumulative_production_in_watt_hour=0, cumulative_consumption_in_watt_hour=0)
        self.previous_state = self.state.self_copy()

        # Outputs
        self.electricity_to_grid_in_watt_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityToGridInWatt,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            output_description="The export to the grid, in W: production minus consumption where positive, else zero.",
        )
        self.electricity_from_grid_in_watt_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityFromGridInWatt,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            output_description=(
                "The import from the grid, in W: consumption minus production where positive, else zero."
            ),
        )
        self.electricity_production_in_watt_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityProductionInWatt,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            output_description=(
                "The electricity the ports the meter reads give out (their PRODUCTION ports), in W; zero or positive."
            ),
        )
        if lt.DistrictNames.is_district(config.component_id.building):
            self.surplus_electricity_unused_to_district_ems_from_building_ems_output: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.SurplusUnusedFromBuildingEMSOutput,
                load_type=lt.LoadTypes.ELECTRICITY,
                unit=lt.Units.WATT,
                output_description=f"here a description for {self.SurplusUnusedFromBuildingEMSOutput} will follow.",
                postprocessing_flag=(
                    [
                        lt.InandOutputType.ELECTRICITY_PRODUCTION,
                        lt.ComponentType.BUILDINGS,
                        lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
                    ]
                    if lt.DistrictNames.is_district(config.component_id.building)
                    else []
                ),
            )
            self.electricity_consumption_building_uncontrolled_in_watt_channel: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.ElectricityConsumptionOfBuildingsInWatt,
                load_type=lt.LoadTypes.ELECTRICITY,
                unit=lt.Units.WATT,
                output_description=f"here a description for {self.ElectricityConsumptionOfBuildingsInWatt} will follow.",
                postprocessing_flag=(
                    [
                        lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                        lt.ComponentType.BUILDINGS,
                        lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
                    ]
                    if lt.DistrictNames.is_district(config.component_id.building)
                    else []
                ),
            )
        self.electricity_consumption_uncontrolled_in_watt_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityConsumptionInWatt,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            output_description=(
                "The electricity the ports the meter reads take in (their CONSUMPTION ports), in W; zero or positive."
            ),
            postprocessing_flag=(
                [
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                    lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
                ]
                if lt.DistrictNames.is_district(config.component_id.building)
                else []
            ),
        )
        self.electricity_available_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityAvailable,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            output_description=f"here a description for {self.ElectricityAvailable} will follow.",
        )
        self.electricity_to_and_from_grid_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityToAndFromGrid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ElectricityToAndFromGrid} will follow.",
        )
        self.electricity_to_grid_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityToGrid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            output_description=(
                "The export to the grid over the step, in Wh: production minus consumption where positive, else zero."
            ),
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.electricity_from_grid_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityFromGrid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            output_description=(
                "The import from the grid over the step, in Wh: consumption minus production where positive, else "
                "zero."
            ),
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )

        self.electricity_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityConsumption,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            output_description=(
                "The electricity the ports the meter reads take in over the step, in Wh; zero or positive."
            ),
        )

        self.electricity_production_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityProduction,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            output_description=(
                "The electricity the ports the meter reads give out over the step, in Wh; zero or positive."
            ),
        )

        self.cumulative_electricity_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeConsumption,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.CumulativeConsumption} will follow.",
            aggregation=lt.OutputAggregation.LAST,
        )

        self.cumulative_electricity_production_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeProduction,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.CumulativeProduction} will follow.",
            aggregation=lt.OutputAggregation.LAST,
        )
        self.add_dynamic_default_connections(self.get_default_connections_from_electric_car())

    def declares_default_connections(self) -> bool:
        """Whether the meter declares default connections: always, since it reads every electricity port it finds."""
        return True

    def default_connections_for(self, source_component: cp.Component) -> List[DynamicComponentConnection]:
        """What the meter reads of one component: every electricity port it declares, by the port's role.

        An output of the source that declares an :class:`~hisim.energy_port.EnergyPort` of the electricity carrier
        with the role ``CONSUMPTION`` is fed as consumption, one with the role ``PRODUCTION`` as production (:attr:`FLOW_TAG_OF_ROLE`),
        each at the monitored-only weight: the meter ranks nothing. A component type the output itself is flagged
        with (the PV system's ``PV``) goes with the feed, so that a selection by component type finds it. Nothing
        depends on the source's class, so a consumer no energy manager reads is billed like any other: a heat pump's
        cooling and brine-pump electricity, for example. Losses and stored changes are not read; they never cross the
        meter. The meter never reads itself.

        One class is still read by name: the electric car's charge controller, whose
        ``BatteryChargingPowerToEMS`` is the car's charging until the car battery declares its ports
        (complete Sankey spec, §9, PR 5b).

        Args:
            source_component: A component of the run.

        Returns:
            The meter's connections from it, in the order of its outputs.
        """
        if source_component is self:
            return []
        connections = super().default_connections_for(source_component)
        for output in source_component.outputs:
            port = output.energy_port
            if port is None or port.carrier != lt.EnergyBalanceCarrier.ELECTRICITY:
                continue
            if port.role not in self.FLOW_TAG_OF_ROLE:
                continue
            connections.append(
                DynamicComponentConnection(
                    source_component_class=type(source_component),
                    source_class_name=source_component.get_classname(),
                    source_component_field_name=output.field_name,
                    source_load_type=output.load_type,
                    source_unit=output.unit,
                    source_tags=[
                        *(tag for tag in output.postprocessing_flag or [] if isinstance(tag, lt.ComponentType)),
                        self.FLOW_TAG_OF_ROLE[port.role],
                    ],
                    source_weight=DynamicConnectionChannel.MONITORED_ONLY_WEIGHT,
                )
            )
        return connections

    def get_default_connections_from_electric_car(
        self,
    ) -> List[DynamicComponentConnection]:
        """The electric car's charging, read from its charge controller until the car battery declares its ports.

        The car battery (``advanced_ev_battery_bslib``) declares no energy port yet (complete Sankey spec, §9,
        PR 5b), so the meter cannot find its charging by role; the charge controller publishes it as
        ``BatteryChargingPowerToEMS``. Once the car battery declares its electricity port, the meter reads that
        port by role and this declaration goes.
        """

        from hisim.components.controller_l1_generic_ev_charge import L1Controller  # pylint: disable=import-outside-toplevel

        dynamic_connections: List[DynamicComponentConnection] = []
        electric_car_charger_class_name = L1Controller.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=L1Controller,
                source_class_name=electric_car_charger_class_name,
                source_component_field_name=L1Controller.BatteryChargingPowerToEMS,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.CAR_BATTERY, lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
                source_weight=DynamicConnectionChannel.MONITORED_ONLY_WEIGHT,
            )
        )
        return dynamic_connections

    def metered_exchanges(self) -> Tuple[MeteredExchange, ...]:
        """The grid import and export this meter publishes, in W (complete Sankey spec, §4.4).

        The energy balance draws the meter between "grid import" and "grid export" and the devices whose electricity
        ports it reads, and checks per step that the import minus the export equals what they take minus what they
        give.

        Returns:
            The one electricity exchange.
        """
        return (
            MeteredExchange(
                lt.EnergyBalanceCarrier.ELECTRICITY, self.ElectricityFromGridInWatt, self.ElectricityToGridInWatt
            ),
        )

    def write_to_report(self):
        """Writes relevant information to report."""
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

    def readings_of_channel(self, channel_key: str) -> List[Tuple[ComponentInput, float]]:
        """The inputs of one channel, each with the factor the meter reads its value with.

        The port an input reads decides: an electricity ``CONSUMPTION`` port is consumption, a ``PRODUCTION`` port production
        (:attr:`FLOW_TAG_OF_ROLE`), and a port published with the opposite sign of its role (``negated``, the
        battery's ``DischargingPower``, published <= 0) is read with the factor -1, so that both totals are
        positive. An input whose output declares no energy port (an undeclared device, such as the car's charge
        controller) is read as its channel says, with the factor 1. Called once the inputs are connected, at the
        first step.

        Args:
            channel_key: :attr:`PRODUCTION_CHANNEL` or :attr:`CONSUMPTION_UNCONTROLLED_CHANNEL`.

        Returns:
            The channel's inputs with their factors, in the order the channel sums them.

        Raises:
            ValueError: For an input whose output declares a port the meter does not read (not electricity, or a
                loss or a stored change), or an electricity port fed through the channel of the other role.
        """
        role_of_channel = {
            self.PRODUCTION_CHANNEL: lt.EnergyRole.PRODUCTION,
            self.CONSUMPTION_UNCONTROLLED_CHANNEL: lt.EnergyRole.CONSUMPTION,
        }[channel_key]
        readings: List[Tuple[ComponentInput, float]] = []
        for component_input in self.get_channel_inputs(channel_key):
            source = component_input.source_output
            port = source.energy_port if source is not None else None
            if port is None:
                readings.append((component_input, 1.0))
                continue
            if port.carrier != lt.EnergyBalanceCarrier.ELECTRICITY or port.role not in self.FLOW_TAG_OF_ROLE:
                raise ValueError(
                    f"The electricity meter '{self.component_name}' reads {component_input.src_object_name}."
                    f"{component_input.src_field_name}, a {port.carrier.value} {port.role.value} port. A meter reads "
                    "the electricity a component takes in or gives out (its CONSUMPTION and PRODUCTION ports of electricity), never a "
                    "loss, a stored change or another carrier."
                )
            if port.role != role_of_channel:
                raise ValueError(
                    f"The electricity meter '{self.component_name}' reads {component_input.src_object_name}."
                    f"{component_input.src_field_name}, an electricity {port.role.value} port, as "
                    f"{self.FLOW_TAG_OF_ROLE[role_of_channel].name}; its role makes it "
                    f"{self.FLOW_TAG_OF_ROLE[port.role].name}. Feed it with that tag."
                )
            readings.append((component_input, -1.0 if port.negated else 1.0))
        return readings

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Sum what the meter reads into consumption and production, and publish the grid import and export."""

        if not self.readings_sorted:
            self.production_readings = self.readings_of_channel(self.PRODUCTION_CHANNEL)
            self.consumption_readings = self.readings_of_channel(self.CONSUMPTION_UNCONTROLLED_CHANNEL)
            self.readings_sorted = True
            # The building-tagged subsets are as constant over a run as the two lists above, so
            # they are resolved here once rather than re-filtered and re-sorted on every step of
            # every convergence iteration in the district branch below. Literal on purpose:
            # subset matching, see the CHANNELS docstring.
            self.production_inputs_building = self.get_dynamic_inputs(
                tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION, lt.ComponentType.BUILDINGS]
            )
            self.consumption_inputs_building = self.get_dynamic_inputs(
                tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED, lt.ComponentType.BUILDINGS]
            )

        # ELECTRICITY #

        # Summed exactly rounded (math.fsum), so the totals do not depend on the order the inputs were wired in: a
        # Python setup, its recorded twin and its composed file wire them in different orders, and a plain sum would
        # make their grid columns differ in the last bits.
        production_in_watt = math.fsum(
            factor * stsv.get_input_value(component_input=elem) for elem, factor in self.production_readings
        )
        consumption_uncontrolled_in_watt = math.fsum(
            factor * stsv.get_input_value(component_input=elem) for elem, factor in self.consumption_readings
        )

        if lt.DistrictNames.is_district(self.config.component_id.building):
            building_electricity_surplus_unused = (
                sum([stsv.get_input_value(component_input=elem) for elem in self.production_inputs_building]))

            stsv.set_output_value(
                self.surplus_electricity_unused_to_district_ems_from_building_ems_output,
                building_electricity_surplus_unused,
            )

            consumption_of_buildings = (
                sum([stsv.get_input_value(component_input=elem) for elem in self.consumption_inputs_building]))

            stsv.set_output_value(
                self.electricity_consumption_building_uncontrolled_in_watt_channel,
                consumption_of_buildings,
            )

        # Production of Electricity positve sign
        # Consumption of Electricity negative sign
        difference_between_production_and_consumption_in_watt = production_in_watt - consumption_uncontrolled_in_watt

        # transform watt to watthour
        production_in_watt_hour = production_in_watt * self.seconds_per_timestep / 3600
        consumption_uncontrolled_in_watt_hour = consumption_uncontrolled_in_watt * self.seconds_per_timestep / 3600
        difference_between_production_and_consumption_in_watt_hour = (
            production_in_watt_hour - consumption_uncontrolled_in_watt_hour
        )

        # calculate cumulative production and consumption
        cumulative_production_in_watt_hour = self.state.cumulative_production_in_watt_hour + production_in_watt_hour
        cumulative_consumption_in_watt_hour = (
            self.state.cumulative_consumption_in_watt_hour + consumption_uncontrolled_in_watt_hour
        )

        # consumption is bigger than production -> electricity from grid is needed
        # change sign so that value becomes positive
        if difference_between_production_and_consumption_in_watt_hour < 0:
            electricity_from_grid_in_watt_hour = -difference_between_production_and_consumption_in_watt_hour
            electricity_to_grid_in_watt_hour = 0.0
        # production is bigger -> electricity can be fed into grid
        elif difference_between_production_and_consumption_in_watt_hour > 0:
            electricity_to_grid_in_watt_hour = difference_between_production_and_consumption_in_watt_hour
            electricity_from_grid_in_watt_hour = 0.0

        # difference between production and consumption is zero
        else:
            electricity_to_grid_in_watt_hour = 0.0
            electricity_from_grid_in_watt_hour = 0.0

        # set outputs
        stsv.set_output_value(
            self.electricity_to_grid_in_watt_channel,
            (
                difference_between_production_and_consumption_in_watt
                if difference_between_production_and_consumption_in_watt > 0
                else 0
            ),
        )
        stsv.set_output_value(
            self.electricity_from_grid_in_watt_channel,
            (
                -difference_between_production_and_consumption_in_watt
                if difference_between_production_and_consumption_in_watt < 0
                else 0
            ),
        )

        stsv.set_output_value(
            self.electricity_production_in_watt_channel,
            production_in_watt,
        )

        stsv.set_output_value(
            self.electricity_consumption_uncontrolled_in_watt_channel,
            consumption_uncontrolled_in_watt,
        )

        stsv.set_output_value(
            self.electricity_available_channel,
            difference_between_production_and_consumption_in_watt,
        )
        stsv.set_output_value(
            self.electricity_to_and_from_grid_channel,
            difference_between_production_and_consumption_in_watt_hour,
        )
        stsv.set_output_value(self.electricity_to_grid_channel, electricity_to_grid_in_watt_hour)
        stsv.set_output_value(self.electricity_from_grid_channel, electricity_from_grid_in_watt_hour)
        stsv.set_output_value(
            self.electricity_consumption_channel,
            consumption_uncontrolled_in_watt_hour,
        )

        stsv.set_output_value(
            self.electricity_production_channel,
            production_in_watt_hour,
        )

        stsv.set_output_value(
            self.cumulative_electricity_consumption_channel,
            cumulative_consumption_in_watt_hour,
        )

        stsv.set_output_value(
            self.cumulative_electricity_production_channel,
            cumulative_production_in_watt_hour,
        )

        self.state.cumulative_production_in_watt_hour = cumulative_production_in_watt_hour
        self.state.cumulative_consumption_in_watt_hour = cumulative_consumption_in_watt_hour

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        total_energy_to_grid_in_kwh: float
        total_energy_from_grid_in_kwh: float

        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.ElectricityToGrid and output.unit == lt.Units.WATT_HOUR:
                    # Todo: check component name from system_setups: find another way of using the correct outputs
                    total_energy_to_grid_in_kwh = round(postprocessing_results.iloc[:, index].sum() * 1e-3, 2)

                elif output.field_name == self.ElectricityFromGrid and output.unit == lt.Units.WATT_HOUR:
                    total_energy_from_grid_in_kwh = round(postprocessing_results.iloc[:, index].sum() * 1e-3, 2)

        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year, self.my_simulation_parameters.country
        )
        co2_per_unit = emissions_and_cost_factors.electricity_footprint_in_kg_per_kwh
        euro_per_unit = emissions_and_cost_factors.electricity_costs_in_euro_per_kwh
        revenue_euro_per_unit = emissions_and_cost_factors.electricity_to_grid_revenue_in_euro_per_kwh

        opex_cost_per_simulated_period_in_euro = (
            total_energy_from_grid_in_kwh * euro_per_unit - total_energy_to_grid_in_kwh * revenue_euro_per_unit
        )
        co2_per_simulated_period_in_kg = total_energy_from_grid_in_kwh * co2_per_unit
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=opex_cost_per_simulated_period_in_euro,
            opex_maintenance_cost_in_euro=0,
            co2_footprint_in_kg=co2_per_simulated_period_in_kg,
            total_consumption_in_kwh=total_energy_from_grid_in_kwh,
            loadtype=lt.LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.ELECTRICITY_METER
        )

        return opex_cost_data_class

    def get_cost_facts(self) -> ComponentCostFacts:
        """Cost facts of the meter hardware for the lifecycle cost engine (roadmap/cost_spec.md §3.3).

        A meter is `METER`-relevant for *energy*, but it is also a physical device that had to be
        bought, so it declares facts like any other priced subject. It has no meaningful capacity,
        hence `size=1.0` with `Units.ANY`: the database entry for `ELECTRICITY_METER` is priced
        per device, and `count` stays at its default of one. Reading the size as kW or kWh here
        would be a category error, not just a scaling one.

        Note the strict separation from `get_energy_flow_facts` below: this method describes the
        box on the wall, that one describes the kWh that flowed through it. Nothing about energy
        prices appears in either.

        Returns:
            The facts for this meter device; never None, since the class declares `METER`.
        """
        return ComponentCostFacts(
            asset_class=lt.ComponentType.ELECTRICITY_METER,
            size=1.0,
            size_unit=lt.Units.ANY,
            kpi_tag=KpiTagEnumClass.ELECTRICITY_METER,
            investment_cost_override_in_euro=self.config.investment_costs_in_euro,
            lifetime_override_in_years=self.config.lifetime_in_years,
            # Either override needs the provenance, not just the investment one (§3.10).
            override_source=(
                "component config"
                if (
                    self.config.investment_costs_in_euro is not None
                    or self.config.lifetime_in_years is not None
                )
                else None
            ),
        )

    def get_energy_flow_facts(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> EnergyFlowFacts:
        """Carrier flows at the grid boundary for the lifecycle cost engine (roadmap/cost_spec.md §3.4).

        Integrates the two directions of the grid connection over the whole simulated period:
        `ElectricityFromGrid` becomes the energy bought, `ElectricityToGrid` the energy sold.
        This meter is the *only* boundary at which electricity enters the cost model — every
        consumer inside the building is billed implicitly through it — which is what makes double
        counting structurally impossible rather than a thing to watch out for.

        This is the §3.4 declaration of that boundary, and it is the path the pipeline actually
        uses: `hisim.economics.bridge` asks this hook first and falls back to reading the columns
        itself, guided by `adapter.get_meter_spec`, only for meters that have not adopted it. The
        peak series for capacity charges, which `EnergyFlowFacts` cannot carry, still comes from
        that `MeterSpec`, so the two paths must stay in agreement about which outputs constitute
        the boundary.

        Both source outputs are in **watt-hours** per timestep, so the summed column is scaled by
        `1e-3` into the kilowatt-hours the tariff engine prices; the unit filter on
        `lt.Units.WATT_HOUR` makes sure a same-named power output in watts can never be summed
        here by accident. Outputs are located positionally, because the results frame carries no
        names the engine could rely on. The two directions are reported separately rather than as
        one signed net flow, because they are priced at different rates; the engine's sign
        convention (cost positive, revenue negative) is applied later, when the bill is booked
        onto the timeline.

        Args:
            all_outputs: All component outputs, aligned with the columns of the results frame.
            postprocessing_results: The simulation results frame.

        Returns:
            The electricity bought and sold at this meter, in kWh over the simulated period.

        Raises:
            CostDataError: If either declared column is not among this run's outputs. Both are
                declarations of this class, so a run without one is a broken extraction, not an
                unused direction — and the two of them are the only place electricity enters the
                cost model. Reporting 0.0 instead published a grid bill of zero and a feed-in
                revenue of zero as if they had been measured; the bridge's `MeterSpec` fallback has
                always refused the same run, and it is the same refusal (`missing_meter_column_error`)
                so the hook and the fallback cannot disagree about what a missing column means.
        """
        totals = {}
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name and output.unit == lt.Units.WATT_HOUR:
                if output.field_name in (self.ElectricityFromGrid, self.ElectricityToGrid):
                    totals[output.field_name] = float(postprocessing_results.iloc[:, index].sum()) * 1e-3
        for field_name, role in ((self.ElectricityFromGrid, "bought energy"), (self.ElectricityToGrid, "sold energy")):
            if field_name not in totals:
                raise missing_meter_column_error(self.component_name, field_name, role)
        return EnergyFlowFacts(
            carrier=EnergyCarrier.ELECTRICITY,
            energy_bought_in_kwh=totals[self.ElectricityFromGrid],
            energy_sold_in_kwh=totals[self.ElectricityToGrid],
        )

    @staticmethod
    def get_cost_capex(config: ElectricityMeterConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        component_type = lt.ComponentType.ELECTRICITY_METER
        kpi_tag = (
            KpiTagEnumClass.ELECTRICITY_METER
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

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        total_energy_from_grid_in_kwh: float
        total_energy_to_grid_in_kwh: float
        total_power_from_grid_in_kw: float
        total_power_to_grid_in_kw: float

        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name and output.load_type == lt.LoadTypes.ELECTRICITY:
                if output.field_name == self.ElectricityFromGrid:
                    total_energy_from_grid_in_kwh = postprocessing_results.iloc[:, index].sum() * 1e-3
                elif output.field_name == self.ElectricityToGrid:
                    total_energy_to_grid_in_kwh = postprocessing_results.iloc[:, index].sum() * 1e-3
                elif output.field_name == self.ElectricityFromGridInWatt:
                    total_power_from_grid_in_kw = postprocessing_results.iloc[:, index] * 1e-3
                elif output.field_name == self.ElectricityToGridInWatt:
                    total_power_to_grid_in_kw = postprocessing_results.iloc[:, index] * 1e-3

        (mean_total_power_from_grid_in_kw,
        max_total_power_from_grid_in_kw,
        min_total_power_from_grid_in_kw,
         ) = KpiHelperClass.compute_mean_max_min_values(list_or_pandas_series=total_power_from_grid_in_kw)

        (mean_total_power_to_grid_in_kw,
        max_total_power_to_grid_in_kw,
        min_total_power_to_grid_in_kw,
         ) = KpiHelperClass.compute_mean_max_min_values(list_or_pandas_series=total_power_to_grid_in_kw)

        total_energy_from_grid_in_kwh_entry = KpiEntry(
            name="Total energy from grid",
            unit="kWh",
            value=total_energy_from_grid_in_kwh,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_energy_from_grid_in_kwh_entry)

        total_energy_to_grid_in_kwh_entry = KpiEntry(
            name="Total energy to grid",
            unit="kWh",
            value=total_energy_to_grid_in_kwh,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_energy_to_grid_in_kwh_entry)

        mean_total_power_from_grid_in_kw_entry = KpiEntry(
            name="Mean power from grid",
            unit="kW",
            value=mean_total_power_from_grid_in_kw,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(mean_total_power_from_grid_in_kw_entry)

        max_total_power_from_grid_in_kw_entry = KpiEntry(
            name="Max power from grid",
            unit="kW",
            value=max_total_power_from_grid_in_kw,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(max_total_power_from_grid_in_kw_entry)

        min_total_power_from_grid_in_kw_entry = KpiEntry(
            name="Min power from grid",
            unit="kW",
            value=min_total_power_from_grid_in_kw,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(min_total_power_from_grid_in_kw_entry)

        mean_total_power_to_grid_in_kw_entry = KpiEntry(
            name="Mean power to grid",
            unit="kW",
            value=mean_total_power_to_grid_in_kw,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(mean_total_power_to_grid_in_kw_entry)

        max_total_power_to_grid_in_kw_entry = KpiEntry(
            name="Max power to grid",
            unit="kW",
            value=max_total_power_to_grid_in_kw,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(max_total_power_to_grid_in_kw_entry)

        min_total_power_to_grid_in_kw_entry = KpiEntry(
            name="Min power to grid",
            unit="kW",
            value=min_total_power_to_grid_in_kw,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(min_total_power_to_grid_in_kw_entry)

        # get opex costs
        opex_costs = self.get_cost_opex(all_outputs=all_outputs, postprocessing_results=postprocessing_results)
        opex_costs_in_euro_entry = KpiEntry(
            name="Opex costs of electricity consumption from grid",
            unit="Euro",
            value=opex_costs.opex_energy_cost_in_euro,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(opex_costs_in_euro_entry)
        co2_footprint_in_kg_entry = KpiEntry(
            name="CO2 footprint of electricity consumption from grid",
            unit="kg",
            value=opex_costs.co2_footprint_in_kg,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(co2_footprint_in_kg_entry)

        return list_of_kpi_entries


@dataclass
class ElectricityMeterState:
    """ElectricityMeterState class."""

    cumulative_production_in_watt_hour: float
    cumulative_consumption_in_watt_hour: float

    def self_copy(
        self,
    ):
        """Copy the ElectricityMeterState."""
        return ElectricityMeterState(
            self.cumulative_production_in_watt_hour,
            self.cumulative_consumption_in_watt_hour,
        )
