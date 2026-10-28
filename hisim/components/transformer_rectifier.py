"""Example Transformer."""
from __future__ import annotations

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Dict, Mapping, Optional

from dataclasses_json import dataclass_json

# Import modules from HiSim
import pandas as pd

from hisim.config import ConfigBase, ComponentID, DisplayConfig, preset
from hisim.component import (
    PhysicsComponent,
    CapexCostDataClass,
    ComponentInput,
    ComponentOutput,
    OpexCostDataClass,
    SingleTimeStepValues,
    StatelessComponent,
)
from hisim import loadtypes as lt
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.energy_port import EnergyPort
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiHelperClass, KpiTagEnumClass
from hisim.simulationparameters import SimulationParameters
from hisim.economics.facts import CostRelevance


@dataclass_json
@dataclass
class TransformerConfig(ConfigBase):
    """Configuration of a transformer and rectifier: how much it passes on, what it is rated for, and what it receives.

    The unit converts an electrical input and loses a fixed share of it on the way::

        TransformerConfig.preset_standard("TransformerAndRectifier")

    builds the 95 %-efficient megawatt-class unit the electrolyzer setup feeds, and a differently
    rated unit overrides ``rated_power_in_kilowatt`` on top of it. The efficiency is what the
    simulation multiplies by; the rating caps what the unit delivers and is what the investment
    cost is scaled by. ``received_carrier`` states where the electricity on its input comes from:
    ``ELECTRICITY`` that a meter counts, or ``WIND_ELECTRICITY``, the output of a wind turbine the
    run does not model, read from a series.

    The five cost fields are read as a set: postprocessing looks the figures up from the device
    database for the simulated year and country and scales them by ``rated_power_in_kilowatt``
    only while all five are ``None``; all five set are used verbatim instead, for a quoted unit.
    """

    MAIN_CLASS = "hisim.components.transformer_rectifier.Transformer"

    #: The boundary node of each carrier the unit may receive, ``None`` for a carrier whose port finds its peer
    #: through the wiring (read-only). Electricity on the input is metered: the electricity meter reads the port
    #: that books it. The electricity of a wind turbine the run does not model enters at the ``wind turbine`` node,
    #: as the sun enters a PV system (owner decision of 2026-10-28 on bead hisim-component-physics-4g9.69).
    BOUNDARY_OF_RECEIVED_CARRIER: ClassVar[Mapping[lt.EnergyBalanceCarrier, Optional[lt.EnvironmentNode]]] = (
        MappingProxyType(
            {
                lt.EnergyBalanceCarrier.ELECTRICITY: None,
                lt.EnergyBalanceCarrier.WIND_ELECTRICITY: lt.EnvironmentNode.WIND_TURBINE,
            }
        )
    )

    component_id: ComponentID
    #: Share of the input the unit passes on, as a dimensionless fraction in (0, 1] -- 0.95 for
    #: 95 %, never a percentage. It multiplies the input power directly, which is why
    #: :meth:`__post_init__` refuses anything outside that range.
    efficiency: float = 0.95
    #: Nameplate throughput of the unit in kW: the most it delivers. Input beyond what the unit
    #: can carry at this rating, ``rated_power_in_kilowatt / efficiency``, is not taken
    #: (:meth:`Transformer.i_simulate`). A transformer and rectifier are also priced per kilowatt of
    #: rating, so it is the sizing figure :meth:`Transformer.get_cost_capex` scales by.
    rated_power_in_kilowatt: float = 1000.0
    #: The carrier of what the unit receives on its input, a key of
    #: :attr:`BOUNDARY_OF_RECEIVED_CARRIER`: ``ELECTRICITY`` (a meter counts it) or
    #: ``WIND_ELECTRICITY`` (a wind turbine's output read from a series, from the ``wind turbine``
    #: boundary node).
    received_carrier: lt.EnergyBalanceCarrier = lt.EnergyBalanceCarrier.ELECTRICITY
    #: CO2 emitted producing the device, in kg, or ``None`` for the database lookup.
    device_co2_footprint_in_kg: Optional[float] = None
    #: Purchase cost of the device in EUR, or ``None`` for the database lookup.
    investment_costs_in_euro: Optional[float] = None
    #: Technical lifetime in years, over which the investment is written off, or ``None`` for
    #: the database lookup.
    lifetime_in_years: Optional[float] = None
    #: Yearly maintenance cost in EUR, or ``None`` for the database lookup.
    maintenance_costs_in_euro_per_year: Optional[float] = None
    #: Share of the investment covered by a subsidy, as a fraction, or ``None`` for the
    #: database lookup.
    subsidy_as_percentage_of_investment_costs: Optional[float] = None

    def __post_init__(self) -> None:
        """Refuses an efficiency outside the fraction range (0, 1], a non-positive rating and a carrier it cannot receive.

        A percentage (``95``) would silently scale the output a hundredfold, a negative value
        would invert it, and a zero would make the conversion-loss indicator a division by zero —
        all three run happily as simulations and only surface as wrong numbers in a report, so
        the configuration is where they stop. A rating of zero or less is refused for the same
        reason one step later: the unit would deliver nothing, and the capex is the rating times a
        price per kilowatt, so an unrated unit would be reported as costing nothing. A received
        carrier other than electricity or a wind turbine's electricity has no port the unit could
        book it on.

        Raises:
            ValueError: For an efficiency that is not in (0, 1], a rated power that is not
                strictly positive, or a received carrier that is no key of
                :attr:`BOUNDARY_OF_RECEIVED_CARRIER`.
        """
        self.received_carrier = lt.EnergyBalanceCarrier(self.received_carrier)
        if self.received_carrier not in self.BOUNDARY_OF_RECEIVED_CARRIER:
            raise ValueError(
                f"A transformer receives {' or '.join(c.value for c in self.BOUNDARY_OF_RECEIVED_CARRIER)}, "
                f"not {self.received_carrier.value}."
            )
        if not 0.0 < self.efficiency <= 1.0:
            raise ValueError(
                f"The transformer efficiency must be a fraction in (0, 1], not {self.efficiency}. "
                "Write 0.95 for 95 %, never a percentage."
            )
        if self.rated_power_in_kilowatt <= 0.0:
            raise ValueError(
                f"The transformer rated power must be strictly positive, not {self.rated_power_in_kilowatt} kW. "
                "It caps what the unit delivers and is what the investment cost is scaled by."
            )

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> TransformerConfig:
        """A 95 %-efficient unit of the megawatt class, the field defaults.

        The rating is the megawatt class because the one setup that uses this component feeds a
        megawatt-scale electrolyzer; the five cost fields stay ``None`` so that postprocessing
        looks the figures up from the device database for the simulated year and country rather
        than freezing them into every caller.

        Args:
            name: Instance name of the unit in the simulation.

        Returns:
            TransformerConfig: the configuration, fully concrete -- the class has no sizable field.
        """
        return cls(component_id=ComponentID(name=name))


class Transformer(StatelessComponent, PhysicsComponent):
    """A transformer and rectifier: it passes on a fixed share of what it receives, up to its rating, and loses the rest.

    It reads the power offered on its input (``TransformerInput``, in kW) and takes as much of it as it can carry:
    it delivers ``min(input * efficiency, rating)`` and receives ``delivered / efficiency``
    (:attr:`TransformerConfig.efficiency`, :attr:`TransformerConfig.rated_power_in_kilowatt`). The input beyond that is
    not taken: ``CurtailedInputInWatt``. For example, a 1,000 kW unit at 95 % offered 5,000 kW delivers 1,000 kW,
    receives 1,052.6 kW, loses 52.6 kW and leaves 3,947.4 kW curtailed; offered 500 kW, it delivers 475 kW and
    curtails nothing.

    Its energy ports, all in W: the electricity it delivers ``ElectricityDeliveredInWatt`` (``PRODUCTION`` of
    electricity, which the electricity meter reads), what it receives ``ElectricityReceivedInWatt`` (``CONSUMPTION``
    of :attr:`TransformerConfig.received_carrier`) and the difference ``ConversionLossInWatt`` (``LOSS`` of
    electricity). Received electricity is booked on an output of its own because a metered carrier is booked on an
    output, never on the input that reads it; a wind turbine's electricity comes from the ``wind turbine`` boundary
    node. The curtailed input is never generated (the turbine sheds it), so it carries no port.

    Parameters
    ----------
    my_simulation_parameters : SimulationParameters
        Passed to initialize :py:class:`~hisim.component.Component`.

    config : TransformerConfig
        The :py:class:`TransformerConfig` object that holds the transformer
        configuration (component name, conversion ``efficiency`` as a fraction in (0, 1],
        rating and the carrier it receives).

    my_display_config : DisplayConfig, optional
        A :py:class:`~hisim.config.DisplayConfig` object that controls
        how the component is displayed in the simulation results.
        Defaults to an empty :py:class:`~hisim.config.DisplayConfig`.

    """

    cost_relevance = CostRelevance.PRICED

    #: Watts per kilowatt: the input is offered in kW, every output is published in W.
    WATTS_PER_KILOWATT: ClassVar[float] = 1000.0

    TransformerInput: str = "Input1"
    #: The electricity the unit delivers, in W.
    ElectricityDeliveredInWatt: str = "ElectricityDeliveredInWatt"
    #: What the unit receives on its input, in W: what it can carry of what is offered.
    ElectricityReceivedInWatt: str = "ElectricityReceivedInWatt"
    #: The electricity the unit loses in the step, in W: what it receives minus what it delivers.
    ConversionLossInWatt: str = "ConversionLossInWatt"
    #: The input the unit cannot carry at its rating, in W: what is offered minus what it receives.
    CurtailedInputInWatt: str = "CurtailedInputInWatt"

    #: The name of the KPI each integrated power output reports, keyed by the output's name (read-only).
    ENERGY_KPIS: ClassVar[Mapping[str, str]] = MappingProxyType(
        {
            ElectricityDeliveredInWatt: "Electrical energy delivered",
            ConversionLossInWatt: "Conversion losses",
            CurtailedInputInWatt: "Curtailed input energy",
        }
    )

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: TransformerConfig,
        my_display_config: DisplayConfig | None = None,
    ) -> None:
        """Constructs all the necessary attributes."""
        self.transformerconfig = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        if my_display_config is None:
            my_display_config = DisplayConfig()
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.electricity_input: ComponentInput = self.add_input(
            self.component_name,
            Transformer.TransformerInput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            True,
        )

        self.electricity_output: ComponentOutput = self.add_output(
            self.component_name,
            Transformer.ElectricityDeliveredInWatt,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            postprocessing_flag=[lt.InandOutputType.ELECTRICITY_PRODUCTION],
            output_description=(
                "The electricity the unit delivers, in W, >= 0: what it receives times its efficiency, at most its rating."
            ),
            energy_port=EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ELECTRICITY),
        )
        # no consumption flag: the KPIs would count the electricity the unit receives as the household's own
        # consumption, beside the delivered electricity they count as production
        self.electricity_received_channel: ComponentOutput = self.add_output(
            self.component_name,
            Transformer.ElectricityReceivedInWatt,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description=(
                "What the unit receives on its input, in W, >= 0: what is offered, at most what it can carry at its "
                "rating."
            ),
            energy_port=EnergyPort(
                lt.EnergyRole.CONSUMPTION,
                config.received_carrier,
                environment=TransformerConfig.BOUNDARY_OF_RECEIVED_CARRIER[config.received_carrier],
            ),
        )
        self.conversion_loss_channel: ComponentOutput = self.add_output(
            self.component_name,
            Transformer.ConversionLossInWatt,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description=(
                "The electricity the unit loses in the step, in W, >= 0: what it receives minus what it delivers."
            ),
            energy_port=EnergyPort(lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.ELECTRICITY),
        )
        self.curtailed_input_channel: ComponentOutput = self.add_output(
            self.component_name,
            Transformer.CurtailedInputInWatt,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description=(
                "The power offered on the input that the unit cannot carry at its rating, in W, >= 0: never "
                "generated, so no energy port books it."
            ),
        )

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Take what the unit can carry of the offered input, deliver it times the efficiency, and publish the rest.

        The unit delivers ``min(offered * efficiency, rating)`` and receives ``delivered / efficiency``; the loss is
        the difference of the two and the curtailed input what is offered beyond what it receives. For example, at
        95 % and 1,000 kW, an offer of 2,000 kW gives 1,000 kW delivered, 1,052.6 kW received, 52.6 kW lost and
        947.4 kW curtailed.

        Args:
            timestep: The current simulation timestep index.
            stsv: The single-timestep values container holding inputs and outputs.
            force_convergence: Whether to force convergence (unused in this component).
        """
        offered_in_watt = float(stsv.get_input_value(self.electricity_input)) * self.WATTS_PER_KILOWATT
        efficiency = self.transformerconfig.efficiency
        rating_in_watt = self.transformerconfig.rated_power_in_kilowatt * self.WATTS_PER_KILOWATT

        received_in_watt = min(offered_in_watt, rating_in_watt / efficiency)
        delivered_in_watt = received_in_watt * efficiency
        stsv.set_output_value(self.electricity_output, delivered_in_watt)
        stsv.set_output_value(self.electricity_received_channel, received_in_watt)
        stsv.set_output_value(self.conversion_loss_channel, received_in_watt - delivered_in_watt)
        stsv.set_output_value(self.curtailed_input_channel, offered_in_watt - received_in_watt)

    def energies_in_kilowatt_hour(
        self,
        all_outputs: list,
        postprocessing_results: pd.DataFrame,
    ) -> Dict[str, float]:
        """Integrate the delivered, lost and curtailed power over the run.

        Each output of :attr:`ENERGY_KPIS` is a power column in W; its energy is the sum of the column times the step.
        The KPI entries and the operating costs both read these numbers, so they are computed once here.

        Args:
            all_outputs: every output column of the run, searched for this component's outputs by name.
            postprocessing_results: the per-timestep values of those columns.

        Returns:
            Dict[str, float]: the energy of each output of :attr:`ENERGY_KPIS`, in kWh, rounded to three decimals.

        Raises:
            ValueError: if one of the columns is missing, empty or carries NaN — a value pandas would otherwise drop
                silently from the sum — so the figures are either computed from complete values or refused, never
                reported wrongly in silence.
        """
        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        energies: Dict[str, float] = {}
        for index, output in enumerate(all_outputs):
            if output.component_name != self.component_name or output.field_name not in self.ENERGY_KPIS:
                continue
            column = postprocessing_results.iloc[:, index]
            if column.empty or bool(column.isna().any()):
                raise ValueError(
                    f"The transformer output column {output.field_name} of {self.component_name} is "
                    f"{'empty' if column.empty else 'carrying NaN'}; its KPIs would be "
                    "silently wrong rather than absent, so they are refused instead."
                )
            energies[output.field_name] = round(
                KpiHelperClass.compute_total_energy_from_power_timeseries(column, seconds_per_timestep), 3
            )
        missing = sorted(set(self.ENERGY_KPIS) - set(energies))
        if missing:
            raise ValueError(
                f"The transformer output columns {', '.join(missing)} were not found for {self.component_name}; its "
                "KPIs cannot be reported as absent silently."
            )
        return energies

    def get_component_kpi_entries(
        self,
        all_outputs: list,
        postprocessing_results: pd.DataFrame,
    ) -> list[KpiEntry]:
        """Calculates KPIs for the transformer/rectifier and returns all KPI entries as a list.

        Three indicators describe what the unit did over the simulated period: the electrical energy it delivered,
        its conversion losses and the input energy it could not carry at its rating, all from
        :meth:`energies_in_kilowatt_hour`.

        Args:
            all_outputs: every output column of the run, searched for this component's outputs
                by name.
            postprocessing_results: the per-timestep values of those columns.

        Returns:
            list[KpiEntry]: the three entries, tagged as Transformer.

        Raises:
            ValueError: if a column is missing, empty or carries NaN, via :meth:`energies_in_kilowatt_hour`.
        """
        energies = self.energies_in_kilowatt_hour(all_outputs, postprocessing_results)
        return [
            KpiEntry(
                name=name,
                unit="kWh",
                value=energies[field_name],
                tag=KpiTagEnumClass.TRANSFORMER,
                description=self.component_name,
            )
            for field_name, name in self.ENERGY_KPIS.items()
        ]

    @staticmethod
    def get_cost_capex(
        config: TransformerConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:
        """Return the unit's investment cost, embodied CO2, lifetime and maintenance cost.

        A transformer and rectifier are bought, rated and replaced as one unit, so they are one
        cost subject (``ComponentType.TRANSFORMER_AND_RECTIFIER``) priced per kilowatt of
        nameplate rating, which is what the figures scale by. Where they come from is the
        all-or-nothing rule stated on :class:`TransformerConfig`: the device database unless all
        five cost fields carry values.

        Args:
            config: the transformer configuration, read for its rating and its cost fields.
            simulation_parameters: the simulated year, country and duration, which decide which
                database row applies and what share of the investment falls in the run.

        Returns:
            CapexCostDataClass: the investment cost and embodied CO2, both in total and prorated
            over the simulated period, tagged as Transformer.
        """
        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
            simulation_parameters=simulation_parameters,
            component_type=lt.ComponentType.TRANSFORMER_AND_RECTIFIER,
            unit=lt.Units.KILOWATT,
            size_of_energy_system=config.rated_power_in_kilowatt,
            config=config,
            kpi_tag=KpiTagEnumClass.TRANSFORMER,
        )
        CapexComputationHelperFunctions.overwrite_config_values_with_new_capex_values(
            config=config, capex_cost_data_class=capex_cost_data_class
        )
        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: list,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Return the unit's operating cost: its conversion losses, plus maintenance.

        The energy this unit consumes is exactly its conversion losses: everything else it passes
        on, and the consumer downstream reports that share as its own. The two figures are
        therefore disjoint and can be added, which is what the system total does -- reporting the
        throughput here instead would count the same kilowatt-hours twice. The losses are priced
        and carbon-accounted at the electricity factors for the simulated year and country.

        Args:
            all_outputs: every output column of the run, searched for this component's output.
            postprocessing_results: the per-timestep values of those columns.

        Returns:
            OpexCostDataClass: the cost, CO2 and consumption of the conversion losses, plus the
            maintenance cost for the simulated period, tagged as Transformer.

        Raises:
            ValueError: if an output column is missing, empty or carries NaN, via
                :meth:`energies_in_kilowatt_hour`.
        """
        losses_in_kilowatt_hour = self.energies_in_kilowatt_hour(all_outputs, postprocessing_results)[
            self.ConversionLossInWatt
        ]
        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year, self.my_simulation_parameters.country
        )
        return OpexCostDataClass(
            opex_energy_cost_in_euro=losses_in_kilowatt_hour
            * emissions_and_cost_factors.electricity_costs_in_euro_per_kwh,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=losses_in_kilowatt_hour
            * emissions_and_cost_factors.electricity_footprint_in_kg_per_kwh,
            total_consumption_in_kwh=losses_in_kilowatt_hour,
            loadtype=lt.LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.TRANSFORMER,
        )

    def write_to_report(self) -> list[str]:
        """Return report lines describing this transformer.

        Returns:
            A list containing a single string with the component name.
        """
        return [f"Transformer: {self.component_name}"]
