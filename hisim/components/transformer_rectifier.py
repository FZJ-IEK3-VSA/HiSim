"""Example Transformer."""
from __future__ import annotations

# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from dataclasses import dataclass
from typing import Optional, Tuple

from dataclasses_json import dataclass_json

# Import modules from HiSim
import pandas as pd

from hisim.config import ConfigBase, ComponentID, DisplayConfig
from hisim.component import (
    CapexCostDataClass,
    ComponentInput,
    ComponentOutput,
    OpexCostDataClass,
    SingleTimeStepValues,
    StatelessComponent,
)
from hisim import loadtypes as lt
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiHelperClass, KpiTagEnumClass
from hisim.simulationparameters import SimulationParameters
from hisim.economics.facts import CostRelevance


@dataclass_json
@dataclass
class TransformerConfig(ConfigBase):
    """Configuration of the Example Transformer.

    Attributes
    ----------
    efficiency : float
        Conversion efficiency of the transformer/rectifier, expressed as a
        dimensionless fraction in the range (0, 1] (e.g. ``0.95`` for 95 %).
        It is applied as a direct multiplicative scalar on the input power,
        so passing a percentage (e.g. ``95``) would silently scale the output
        by 100x — which is why the range is validated at construction: a
        percentage, a negative value or a zero is refused loudly instead of
        producing plausible but wrong outputs and negative loss indicators.
    rated_power_in_kilowatt : float
        Nameplate throughput of the unit in kW, i.e. the electrical power it is
        built to convert continuously. It does not enter the simulation at all —
        :meth:`Transformer.i_simulate` converts whatever it is fed — and exists
        because a transformer and rectifier are priced per kilowatt of rating:
        it is the only sizing figure :meth:`Transformer.get_cost_capex` can scale
        by. A unit rated at zero would therefore cost nothing and be reported as
        an answer, so a non-positive rating is refused at construction.
    device_co2_footprint_in_kg : Optional[float]
        CO2 emitted producing the device, in kg. ``None`` — the default — means
        postprocessing looks the figure up from the device database for the
        simulated year and country and scales it by ``rated_power_in_kilowatt``;
        a number here overrides that lookup. The five cost fields are read as a
        set: the lookup happens only when all five are ``None``.
    investment_costs_in_euro : Optional[float]
        Purchase cost of the device in EUR, or ``None`` for the database lookup.
    lifetime_in_years : Optional[float]
        Technical lifetime in years, over which the investment is written off,
        or ``None`` for the database lookup.
    maintenance_costs_in_euro_per_year : Optional[float]
        Yearly maintenance cost in EUR, or ``None`` for the database lookup.
    subsidy_as_percentage_of_investment_costs : Optional[float]
        Share of the investment covered by a subsidy, as a fraction, or ``None``
        for the database lookup.
    """

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the base class."""
        return str(Transformer.get_full_classname())

    # parameter_string: str
    # my_simulation_parameters: SimulationParameters
    component_id: ComponentID
    efficiency: float  # conversion efficiency as a fraction in (0, 1] (not a percentage)
    rated_power_in_kilowatt: float  # nameplate throughput in kW; the only figure the capex scales by
    #: CO2 footprint of investment in kg
    device_co2_footprint_in_kg: Optional[float] = None
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float] = None
    #: lifetime in years
    lifetime_in_years: Optional[float] = None
    #: maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float] = None
    #: subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float] = None

    def __post_init__(self) -> None:
        """Refuses an efficiency outside the fraction range (0, 1] and a non-positive rating.

        A percentage (``95``) would silently scale the output a hundredfold, a negative value
        would invert it, and a zero would make the conversion-loss indicator a division by zero —
        all three run happily as simulations and only surface as wrong numbers in a report, so
        the configuration is where they stop. A rating of zero or less is refused for the same
        reason one step later: the capex is the rating times a price per kilowatt, so an unrated
        unit would be reported as costing nothing, which reads exactly like a device that is free.

        Raises:
            ValueError: For an efficiency that is not in (0, 1], or a rated power that is not
                strictly positive.
        """
        if not 0.0 < self.efficiency <= 1.0:
            raise ValueError(
                f"The transformer efficiency must be a fraction in (0, 1], not {self.efficiency}. "
                "Write 0.95 for 95 %, never a percentage."
            )
        if self.rated_power_in_kilowatt <= 0.0:
            raise ValueError(
                f"The transformer rated power must be strictly positive, not {self.rated_power_in_kilowatt} kW. "
                "It is what the investment cost is scaled by, so an unrated unit would be costed as free."
            )

    @classmethod
    def get_default_transformer_config(cls) -> TransformerConfig:
        """Gets a default ``TransformerConfig`` instance.

        The default rating is the megawatt class, because the one setup that uses this component
        feeds a megawatt-scale electrolyzer; the five cost fields stay ``None`` so that
        postprocessing looks the figures up from the device database for the simulated year and
        country rather than freezing them into every caller.

        Returns:
            TransformerConfig: a 95 %-efficient, 1000 kW unit with unset cost fields.
        """
        return TransformerConfig(
            component_id=ComponentID(name="GenericTransformerAndRectifier"),
            efficiency=0.95,
            rated_power_in_kilowatt=1000.0,
        )


class Transformer(StatelessComponent):
    """The Example Transformer class.

    It is used to modify input values and return them as new output values.

    The single input (``TransformerInput``) is scaled by
    :attr:`TransformerConfig.efficiency` (a dimensionless fraction in [0, 1])
    to produce the single output (``TransformerOutput``). Both the input and
    output are declared with :attr:`lt.LoadTypes.ELECTRICITY` and
    :attr:`lt.Units.KILOWATT`, so the transformer carries an implicit unit
    contract: a caller must feed ``electricity_input`` in kW and read
    ``electricity_output`` in kW.

    Parameters
    ----------
    my_simulation_parameters : SimulationParameters
        Passed to initialize :py:class:`~hisim.component.Component`.

    config : TransformerConfig
        The :py:class:`TransformerConfig` object that holds the transformer
        configuration (building name, component name, and conversion
        ``efficiency`` expressed as a fraction in [0, 1]).

    my_display_config : DisplayConfig, optional
        A :py:class:`~hisim.config.DisplayConfig` object that controls
        how the component is displayed in the simulation results.
        Defaults to an empty :py:class:`~hisim.config.DisplayConfig`.

    """

    cost_relevance = CostRelevance.PRICED

    TransformerInput: str = "Input1"
    TransformerOutput: str = "MyTransformerOutput"

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
            Transformer.TransformerOutput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            postprocessing_flag=[lt.InandOutputType.ELECTRICITY_PRODUCTION],
            output_description="Electricity output",
        )

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Scale the electricity input by the configured efficiency to produce output.

        Reads the input power value (``electricity_input``) from ``stsv``, multiplies it by
        :attr:`TransformerConfig.efficiency`, and writes the result to ``electricity_output``.

        Args:
            timestep: The current simulation timestep index.
            stsv: The single-timestep values container holding inputs and outputs.
            force_convergence: Whether to force convergence (unused in this component).
        """
        input_power_in_kilowatt = stsv.get_input_value(self.electricity_input)
        # print(f"Input from CSV: {input_power_in_kilowatt}")
        efficiency = self.transformerconfig.efficiency
        # print(f"individual efficiency: {efficiency}")

        stsv.set_output_value(self.electricity_output, float(input_power_in_kilowatt * efficiency))

    def delivered_and_lost_energy_in_kilowatt_hour(
        self,
        all_outputs: list,
        postprocessing_results: pd.DataFrame,
    ) -> Tuple[float, float]:
        """Integrate the delivered electrical energy over the run and derive the conversion losses.

        The energy the unit delivered is the integral of its single output power column. The
        losses are not observable as a column of their own -- the unit has one input and one
        output port -- but the output is by construction the input scaled by the configured
        efficiency, so the loss follows exactly: ``delivered * (1/efficiency - 1)``. Both the
        KPI entries and the operating costs are these two numbers, which is why they are computed
        once here instead of twice with two chances to disagree.

        Args:
            all_outputs: every output column of the run, searched for this component's output
                by name.
            postprocessing_results: the per-timestep values of those columns.

        Returns:
            Tuple[float, float]: the delivered energy and the conversion losses, both in kWh,
            each rounded to three decimals.

        Raises:
            ValueError: if the output column is missing, empty or carries NaN — a value pandas
                would otherwise drop silently from the sum — so the two figures are either
                computed from complete values or refused, never reported wrongly in silence.
        """
        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        delivered_in_kilowatt_hour = None
        for index, output in enumerate(all_outputs):
            if output.component_name != self.component_name:
                continue
            if output.field_name == Transformer.TransformerOutput and output.unit == lt.Units.KILOWATT:
                column = postprocessing_results.iloc[:, index]
                if column.empty or bool(column.isna().any()):
                    raise ValueError(
                        f"The transformer output column of {self.component_name} is "
                        f"{'empty' if column.empty else 'carrying NaN'}; its KPIs would be "
                        "silently wrong rather than absent, so they are refused instead."
                    )
                # The output is in kilowatt and the shared conversion speaks watt, so the column
                # is scaled up rather than the conversion restated inline.
                delivered_in_kilowatt_hour = round(
                    KpiHelperClass.compute_total_energy_from_power_timeseries(
                        column * 1000.0, seconds_per_timestep
                    ),
                    3,
                )
        if delivered_in_kilowatt_hour is None:
            raise ValueError(
                f"The transformer output column was not found for {self.component_name}; its KPIs "
                "cannot be reported as absent silently."
            )
        losses_in_kilowatt_hour = round(
            delivered_in_kilowatt_hour * (1.0 / self.transformerconfig.efficiency - 1.0), 3
        )
        return delivered_in_kilowatt_hour, losses_in_kilowatt_hour

    def get_component_kpi_entries(
        self,
        all_outputs: list,
        postprocessing_results: pd.DataFrame,
    ) -> list[KpiEntry]:
        """Calculates KPIs for the transformer/rectifier and returns all KPI entries as a list.

        Two indicators describe what the unit did over the simulated period: the electrical
        energy it delivered and the conversion losses, both from
        :meth:`delivered_and_lost_energy_in_kilowatt_hour`.

        Args:
            all_outputs: every output column of the run, searched for this component's outputs
                by name.
            postprocessing_results: the per-timestep values of those columns.

        Returns:
            list[KpiEntry]: the two entries, tagged as Transformer.

        Raises:
            ValueError: if the output column is missing, empty or carries NaN — a value pandas
                would otherwise drop silently from the sum — so the KPIs are either computed from
                complete values or refused, never reported wrongly in silence.
        """
        delivered_in_kilowatt_hour, losses_in_kilowatt_hour = self.delivered_and_lost_energy_in_kilowatt_hour(
            all_outputs, postprocessing_results
        )
        return [
            KpiEntry(
                name="Electrical energy delivered",
                unit="kWh",
                value=delivered_in_kilowatt_hour,
                tag=KpiTagEnumClass.TRANSFORMER,
                description=self.component_name,
                name_of_source_component=self.component_name,
            ),
            KpiEntry(
                name="Conversion losses",
                unit="kWh",
                value=losses_in_kilowatt_hour,
                tag=KpiTagEnumClass.TRANSFORMER,
                description=self.component_name,
                name_of_source_component=self.component_name,
            ),
        ]

    @staticmethod
    def get_cost_capex(
        config: TransformerConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:
        """Return the unit's investment cost, embodied CO2, lifetime and maintenance cost.

        A transformer and rectifier are bought, rated and replaced as one unit, so they are one
        cost subject (``ComponentType.TRANSFORMER_AND_RECTIFIER``) priced per kilowatt of
        nameplate rating. The figures come from the device database for the simulated year and
        country unless all five cost fields on the configuration carry values, in which case
        those are used verbatim; that is the same rule every other costed component follows, and
        it is why the database entry -- not this method -- is where the numbers are stated and
        sourced.

        The unit is deliberately *not* declared as modelling no device: it is real industrial
        equipment with a real price, so answering zero here would understate the system total
        silently instead of naming what is missing.

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
        and carbon-accounted at the electricity factors for the simulated year and country, the
        same way every other electricity consumer in the library prices what it draws.

        Args:
            all_outputs: every output column of the run, searched for this component's output.
            postprocessing_results: the per-timestep values of those columns.

        Returns:
            OpexCostDataClass: the cost, CO2 and consumption of the conversion losses, plus the
            maintenance cost for the simulated period, tagged as Transformer.

        Raises:
            ValueError: if the output column is missing, empty or carries NaN, via
                :meth:`delivered_and_lost_energy_in_kilowatt_hour`.
        """
        _, losses_in_kilowatt_hour = self.delivered_and_lost_energy_in_kilowatt_hour(
            all_outputs, postprocessing_results
        )
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
