"""Example Transformer."""
from __future__ import annotations

# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Import modules from HiSim
import pandas as pd

from hisim.config import ConfigBase, ComponentID, DisplayConfig
from hisim.component import StatelessComponent, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim import loadtypes as lt
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass
from hisim.simulationparameters import SimulationParameters


@dataclass_json
@dataclass
class TransformerConfig(ConfigBase):
    """Configuration of the Example Transformer.

    Attributes
    ----------
    efficiency : float
        Conversion efficiency of the transformer/rectifier, expressed as a
        dimensionless fraction in the range [0, 1] (e.g. ``0.95`` for 95 %).
        It is applied as a direct multiplicative scalar on the input power,
        so passing a percentage (e.g. ``95``) would silently scale the output
        by 100x. Use a fraction, not a percentage.
    """

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the base class."""
        return str(Transformer.get_full_classname())

    # parameter_string: str
    # my_simulation_parameters: SimulationParameters
    component_id: ComponentID
    efficiency: float  # conversion efficiency as a fraction in [0, 1] (not a percentage)

    @classmethod
    def get_default_transformer_config(cls) -> TransformerConfig:
        """Gets a default ``TransformerConfig`` instance."""
        return TransformerConfig(
            component_id=ComponentID(name="Generic Transformer and rectifier Unit"), efficiency=0.95
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

    def get_component_kpi_entries(
        self,
        all_outputs: list,
        postprocessing_results: pd.DataFrame,
    ) -> list[KpiEntry]:
        """Calculates KPIs for the transformer/rectifier and returns all KPI entries as a list.

        Two indicators describe what the unit did over the simulated period: the electrical
        energy it delivered, integrated from its output power, and the conversion losses. The
        losses are not observable as a column of their own -- the unit has one input and one
        output port -- but the output is by construction the input scaled by the configured
        efficiency, so the loss follows exactly: ``delivered * (1/efficiency - 1)``.

        Args:
            all_outputs: every output column of the run, searched for this component's by name.
            postprocessing_results: the per-timestep values of those columns.

        Returns:
            list[KpiEntry]: the two entries, tagged as Transformer.

        Raises:
            ValueError: if the output column is missing, so a renamed output cannot silently
                drop the KPIs from every future reference.
        """
        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        delivered_in_kilowatt_hour = None
        for index, output in enumerate(all_outputs):
            if output.component_name != self.component_name:
                continue
            if output.field_name == Transformer.TransformerOutput and output.unit == lt.Units.KILOWATT:
                column = postprocessing_results.iloc[:, index]
                delivered_in_kilowatt_hour = round(float(column.sum()) * seconds_per_timestep / 3600, 3)
        if delivered_in_kilowatt_hour is None:
            raise ValueError(
                f"The transformer output column was not found for {self.component_name}; its KPIs "
                "cannot be reported as absent silently."
            )
        losses_in_kilowatt_hour = round(
            delivered_in_kilowatt_hour * (1.0 / self.transformerconfig.efficiency - 1.0), 3
        )
        return [
            KpiEntry(
                name="Electrical energy delivered",
                unit="kWh",
                value=delivered_in_kilowatt_hour,
                tag=KpiTagEnumClass.TRANSFORMER,
                description=self.component_name,
            ),
            KpiEntry(
                name="Conversion losses",
                unit="kWh",
                value=losses_in_kilowatt_hour,
                tag=KpiTagEnumClass.TRANSFORMER,
                description=self.component_name,
            ),
        ]

    def write_to_report(self) -> list[str]:
        """Return report lines describing this transformer.

        Returns:
            A list containing a single string with the component name.
        """
        return [f"Transformer: {self.component_name}"]
