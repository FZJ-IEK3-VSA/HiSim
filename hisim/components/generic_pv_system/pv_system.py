"""The PV system component: the array around the cached series.

Part of the ``hisim.components.generic_pv_system`` package (see the package ``__init__`` for the
layout): this module holds the component, which simulates the electricity generation of a photovoltaic
installation from weather data and the array's peak power. Its configuration is in
:mod:`hisim.components.generic_pv_system.config`, and the calculation itself in
:mod:`hisim.components.generic_pv_system.calculation` -- a static producer whose cache key carries a
fingerprint of its own source and of the weather series it consumes, so a change to either can no longer
be served from a cache written before it (``roadmap/cache_service_spec.md`` §3).

What is left here is the component around that series: its inputs and outputs, the cache lookup, the
cost and KPI declarations and the predictive forecast.
"""

# clean

# Generic/Built-in
import datetime
from typing import Any, List, Optional

import pandas as pd

# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim import utils
from hisim.caching import CacheClient, CacheEntry, CacheKey
from hisim.component import OpexCostDataClass, CapexCostDataClass
from hisim.config import DisplayConfig
# The module object itself is what the cache key is fingerprinted from: ``CacheKey.for_producer`` walks
# its import closure and hashes the source of everything in it, so an edit to the calculation changes
# the key without anyone declaring anything.
from hisim.components.generic_pv_system import calculation
from hisim.components.generic_pv_system.calculation import (
    ARTIFACT_KIND,
    PVLibModuleAndInverterEnum,
    PvSeriesInputs,
    PvWeatherSeries,
)
from hisim.components.generic_pv_system.config import PVSystemConfig
from hisim.components.weather import Weather
from hisim.economics.facts import ComponentCostFacts, CostRelevance
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import (
    KpiTagEnumClass,
    KpiEntry,
)
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions


__authors__ = "Vitor Hugo Bellotto Zago, Kristina Dabrock"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt", "Kristina Dabrock"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Kristina Dabrock"
__email__ = "k.dabrock@fz-juelich.de"
__status__ = "development"

"""
The functions cited in this module are to some degree based on the
tsib project:

[tsib-kotzur]:
Kotzur, Leander, Detlef Stolten, and Hermann-Josef Wagner.
Future grid load of the residential building sector. No. RWTH-2018-231872.
Lehrstuhl für Brennstoffzellen (FZ Jülich), 2019.
ID: http://hdl.handle.net/2128/21115
    http://nbn-resolving.org/resolver?verb=redirect&identifier=urn:nbn:de:0001-2019020614

The implementation of the tsib project can be found under the following
repository: https://github.com/FZJ-IEK3-VSA/tsib

The CEC module and inverter database was downloaded from:
https://github.com/NREL/SAM/tree/patch/deploy/libraries
"""


class PVSystem(cp.Component):
    """Simulates PV Output based on weather data and peak power.

    Parameters
    ----------
    time : int, optional
        Simulation timeline. The default is 2019.
    location : str, optional
        Object Location with temperature and solar data.
        The default is "Aachen".
    power : float, optional
        Power in kWp to be provided by the PV System.
        The default is 10E3.
    load_module_data : bool
        Access the PV data base (True) or not (False).
        The default is False
    module_name : str, optional
        The default is "Trina Solar TSM-435NE09RC.05"
    integrate_inverter, bool, optional
        Consider inverter efficiency in the calculation (True) or not (False).
        The default is True.
    inverter_name : str, optional
        The default is "Enphase Energy Inc : IQ8P-3P-72-E-DOM-US [208V]".
    azimuth : float, optional
        Panel azimuth from north in °. The default is 180°.
    tilt : float, optional
        Panel tilt from horizontal. The default is 90°.
    source_weight : int, optional
        Weight of component, relevant if there is more than one PV System,
        defines hierachy in control. The default is 1.
    name : str, optional
        Name of pv panel within simulation. The default is 'PVSystem'

    """

    # Lifecycle cost engine declaration (cost_spec.md §9.2).
    cost_relevance = CostRelevance.PRICED

    # Inputs
    TemperatureOutside = "TemperatureOutside"
    DirectNormalIrradiance = "DirectNormalIrradiance"
    DirectNormalIrradianceExtra = "DirectNormalIrradianceExtra"
    DiffuseHorizontalIrradiance = "DiffuseHorizontalIrradiance"
    GlobalHorizontalIrradiance = "GlobalHorizontalIrradiance"
    Azimuth = "Azimuth"
    ApparentZenith = "ApparentZenith"
    WindSpeed = "WindSpeed"

    # Outputs
    ElectricityOutput = "ElectricityOutput"
    # Additional output channels must not contain 'ElectricityOutput' or
    # dynamic components will fail.
    ElectricityEnergyOutput = "ElectricityEnergyOutput"

    # Similar components to connect to:
    # 1. Weather
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: PVSystemConfig,
        my_display_config: DisplayConfig = DisplayConfig(display_in_webtool=True),
    ) -> None:
        """Initialize the class."""
        self.my_simulation_parameters = my_simulation_parameters
        self.pvconfig = config
        self.ac_power_ratios_for_all_timesteps_output: List = []
        self.coordinates: Any
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.t_out_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureOutside,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )

        self.dni_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.DirectNormalIrradiance,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            True,
        )

        self.dni_extra_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.DirectNormalIrradianceExtra,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            True,
        )

        self.dhi_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.DiffuseHorizontalIrradiance,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            True,
        )

        self.ghi_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.GlobalHorizontalIrradiance,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            True,
        )

        self.azimuth_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.Azimuth,
            lt.LoadTypes.ANY,
            lt.Units.DEGREES,
            True,
        )

        self.apparent_zenith_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ApparentZenith,
            lt.LoadTypes.ANY,
            lt.Units.DEGREES,
            True,
        )

        self.wind_speed_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.WindSpeed,
            lt.LoadTypes.SPEED,
            lt.Units.METER_PER_SECOND,
            True,
        )

        self.electricity_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=[
                lt.InandOutputType.ELECTRICITY_PRODUCTION,
                lt.ComponentType.PV,
            ],
            output_description="Electricity output of the PV system.",
        )

        self.electricity_energy_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityEnergyOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            postprocessing_flag=[
                lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
            ],
            output_description=f"Here a description for PV {self.ElectricityEnergyOutput} will follow.",
        )

        self.add_default_connections(self.get_default_connections_from_weather())

    @staticmethod
    def get_cost_capex(config: PVSystemConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        component_type = lt.ComponentType.PV
        kpi_tag = KpiTagEnumClass.ROOFTOP_PV
        unit = lt.Units.KILOWATT
        size_of_energy_system = config.power_in_watt * 1e-3

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
            simulation_parameters=simulation_parameters,
            component_type=component_type,
            unit=unit,
            size_of_energy_system=size_of_energy_system,
            config=config,
            kpi_tag=kpi_tag,
        )
        config = CapexComputationHelperFunctions.overwrite_config_values_with_new_capex_values(
            config=config, capex_cost_data_class=capex_cost_data_class
        )

        return capex_cost_data_class

    def get_cost_facts(self) -> ComponentCostFacts:
        """Cost facts for the lifecycle cost engine (cost_spec.md §3.3, §9.1).

        Declares the PV array as one priced subject: a `PV` entry of the cost database, sized by
        its **installed module power in kW**. The config holds that power in *watts*, so the
        `1e-3` is the conversion into the kilowatts the database prices in (EUR/kW); without it a
        10 kW array would be costed as if it were 10 MW. Note this is the module power, not an
        area — PV is never sized in m² here, even though the sizing helper starts from roof area.

        Investment, lifetime and embodied CO2 are looked up from the versioned database unless the
        config carries explicit values (building sizer / RenoVisor request), which are then passed
        through as per-field overrides with a mandatory `override_source`.

        Returns:
            The facts for this PV system; never None, since the class declares `PRICED`.
        """
        config = self.config
        return ComponentCostFacts(
            asset_class=lt.ComponentType.PV,
            size=config.power_in_watt * 1e-3,
            size_unit=lt.Units.KILOWATT,
            kpi_tag=KpiTagEnumClass.ROOFTOP_PV,
            investment_cost_override_in_euro=config.investment_costs_in_euro,
            lifetime_override_in_years=config.lifetime_in_years,
            embodied_co2_override_in_kg=config.device_co2_footprint_in_kg,
            # Any of the three overrides makes the facts overridden, so any of them needs the
            # provenance §3.10 requires: keying only on the investment left a config-declared
            # lifetime or embodied-CO2 override sourceless, which strict mode rejects and the
            # provenance ledger records as unattributed.
            override_source=(
                "component config (e.g. building_sizer / RenoVisor request)"
                if (
                    config.investment_costs_in_euro is not None
                    or config.lifetime_in_years is not None
                    or config.device_co2_footprint_in_kg is not None
                )
                else None
            ),
        )

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs for PV."""
        production_in_kwh: float = 0.0
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.config.component_id.name
                and output.load_type == lt.LoadTypes.ELECTRICITY
                and output.field_name == self.ElectricityEnergyOutput
                and output.unit == lt.Units.WATT_HOUR
            ):
                production_in_kwh = sum(postprocessing_results.iloc[:, index]) * 1e-3

        # for production use negative value (co2 and revenue is handled by electricity meter)
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            total_consumption_in_kwh=(-1) * production_in_kwh,
            loadtype=lt.LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.ROOFTOP_PV,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """PV System KPIs.

        Calculates KPIs for the respective component and return
        all KPI entries as list.
        """
        return []

    def get_default_connections_from_weather(self):
        """Get default connections from weather."""

        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            cp.ComponentConnection(
                PVSystem.TemperatureOutside,
                weather_classname,
                Weather.TemperatureOutside,
            )
        )
        connections.append(
            cp.ComponentConnection(
                PVSystem.DirectNormalIrradiance,
                weather_classname,
                Weather.DirectNormalIrradiance,
            )
        )
        connections.append(
            cp.ComponentConnection(
                PVSystem.DirectNormalIrradianceExtra,
                weather_classname,
                Weather.DirectNormalIrradianceExtra,
            )
        )
        connections.append(
            cp.ComponentConnection(
                PVSystem.DiffuseHorizontalIrradiance,
                weather_classname,
                Weather.DiffuseHorizontalIrradiance,
            )
        )
        connections.append(
            cp.ComponentConnection(
                PVSystem.GlobalHorizontalIrradiance,
                weather_classname,
                Weather.GlobalHorizontalIrradiance,
            )
        )
        connections.append(cp.ComponentConnection(PVSystem.Azimuth, weather_classname, Weather.Azimuth))
        connections.append(
            cp.ComponentConnection(
                PVSystem.ApparentZenith,
                weather_classname,
                Weather.ApparentZenith,
            )
        )
        connections.append(cp.ComponentConnection(PVSystem.WindSpeed, weather_classname, Weather.WindSpeed))
        return connections

    def i_simulate(
        self,
        timestep: int,
        stsv: cp.SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Simulate the component by looking up the precomputed power ratio.

        The AC power ratios for the whole simulation period are computed in one
        vectorized pvlib run (or loaded from the cache file) during
        ``i_prepare_simulation``, so simulating a timestep reduces to indexing
        into that array and scaling by the configured peak power. The weather
        input channels declared in ``__init__`` are therefore not read here;
        they remain wired to document and enforce the dependency on the weather
        component. When predictive control is active, the rolling forecast over
        the prediction horizon is additionally published to the dynamic
        simulation repository for the controller to consume.
        """
        ac_power_in_watt = self.ac_power_ratios_for_all_timesteps_output[timestep] * self.pvconfig.power_in_watt

        stsv.set_output_value(self.electricity_output_channel, ac_power_in_watt)
        stsv.set_output_value(
            self.electricity_energy_output_channel,
            ac_power_in_watt * self.my_simulation_parameters.seconds_per_timestep / 3600,
        )

        if self.pvconfig.predictive_control and self.pvconfig.prediction_horizon is not None:
            last_forecast_timestep = int(
                timestep + self.pvconfig.prediction_horizon / self.my_simulation_parameters.seconds_per_timestep
            )
            if last_forecast_timestep > len(self.ac_power_ratios_for_all_timesteps_output):
                last_forecast_timestep = len(self.ac_power_ratios_for_all_timesteps_output)
            pvforecast = [
                self.ac_power_ratios_for_all_timesteps_output[t] * self.pvconfig.power_in_watt
                for t in range(timestep, last_forecast_timestep)
            ]
            self.simulation_repository.set_dynamic_entry(
                component_type=lt.ComponentType.PV,
                source_weight=self.pvconfig.source_weight,
                entry=pvforecast,
            )

    def i_save_state(self) -> None:
        """Saves the state."""
        pass

    def i_restore_state(self) -> None:
        """Restores the state."""
        pass

    def write_to_report(self):
        """Write to the report."""
        return self.pvconfig.get_string_dict()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepare the component by computing or loading the whole simulation period's PV output.

        On a cache hit, the AC power ratios for every timestep are read from the
        cache CSV. On a cache miss, the producer computes them from the yearly
        weather arrays the weather component published, in one vectorized pvlib
        run, and the result is written to the cache file immediately, so even
        simulations that are interrupted later still populate the cache. After
        that, ``i_simulate`` only performs array lookups.

        What the entry is filed under changed with the producer
        (``roadmap/cache_service_spec.md`` §3): the key is built from the
        calculation's own code and inputs rather than from this component's
        configuration JSON, and it carries the weather's key by reference, so an
        edit to either calculation -- or to a weather file, or to a module
        database -- files the series somewhere else by itself.
        """
        calculation_inputs = self.build_calculation_inputs()
        entry = self.cache_entry(calculation_inputs)

        if entry.exists:
            log.information("Get PV results from cache.")
            # float_precision="round_trip" is what makes a cached run and an uncached one the
            # same run. pandas' default CSV reader uses a fast, inexact float parser and loses
            # the last bit of a sixth of the values, whatever precision they were written with;
            # measured on this data: 317 of 2000 with the default write format, 358 with
            # "%.17g", 542 with "%.20g", zero with this argument. The loss is in the reader,
            # not the digits on disk, which is why writing more of them does not help.
            self.ac_power_ratios_for_all_timesteps_output = pd.read_csv(
                entry.path, sep=",", decimal=".", float_precision="round_trip"
            )[calculation.OUTPUT_COLUMN].tolist()

            if len(self.ac_power_ratios_for_all_timesteps_output) != self.my_simulation_parameters.timesteps:
                raise ValueError(
                    f"Reading the cached PV values seems to have failed. "
                    f"Expected {self.my_simulation_parameters.timesteps} values, "
                    f"but got {len(self.ac_power_ratios_for_all_timesteps_output)}"
                )
        else:
            log.information(f"PV series cache miss: computing it and filing it at {entry.path}")
            database = calculation.produce_pv_series(calculation_inputs)
            self.ac_power_ratios_for_all_timesteps_output = database[
                calculation.OUTPUT_COLUMN
            ].tolist()

            if len(self.ac_power_ratios_for_all_timesteps_output) != self.my_simulation_parameters.timesteps:
                raise ValueError(
                    f"The computed PV values have the wrong length. "
                    f"Expected {self.my_simulation_parameters.timesteps} values, "
                    f"but got {len(self.ac_power_ratios_for_all_timesteps_output)}. "
                    f"The yearly weather arrays in this simulation's sim repository "
                    f"do not match the simulation parameters."
                )

            # write the cache right away so that even interrupted simulations
            # profit from the computation on the next run
            with entry.writing() as temporary_cache_filepath:
                database.to_csv(temporary_cache_filepath, sep=",", decimal=".", index=False)

    def build_calculation_inputs(self) -> PvSeriesInputs:
        """Build the DTO the PV producer is a pure function of.

        Everything the calculation depends on is extracted here from the configuration, the
        simulation parameters and the weather component's publications, and nothing else: the
        module and inverter databases enter as the hashes of their contents rather than as their
        paths, and the weather enters as the key of the series it published plus the series
        themselves as payload, which is how the two keys chain (spec §3.1). What the component
        keeps to itself -- its name, the array's peak power and the share it was sized with, the
        predictive-control flag, cost, CO2 and display settings -- is not part of the calculation
        and therefore not part of its key.

        Returns:
            PvSeriesInputs: the producer's single argument.
        """
        module_database_path = self.database_path(self.pvconfig.module_database)
        inverter_database_path = self.database_path(self.pvconfig.inverter_database)
        return PvSeriesInputs(
            weather_artifact_key=self.weather_artifact_key(),
            module_database=self.pvconfig.module_database,
            module_name=self.pvconfig.module_name,
            inverter_database=self.pvconfig.inverter_database,
            inverter_name=self.pvconfig.inverter_name,
            integrate_inverter=self.pvconfig.integrate_inverter,
            load_module_data=self.pvconfig.load_module_data,
            module_database_content_hash=calculation.content_hash(module_database_path),
            inverter_database_content_hash=calculation.content_hash(inverter_database_path),
            # float() rather than the field as it stands: the geometry is declared as a float, but a
            # Python setup writing ``tilt=30`` hands over an int, while the same system read from a
            # YAML file arrives as 30.0. Canonical JSON renders the two differently, so without this
            # the identical array would be computed twice and filed under two keys.
            tilt_in_degrees=float(self.pvconfig.tilt),
            azimuth_in_degrees=float(self.pvconfig.azimuth),
            number_of_timesteps=self.my_simulation_parameters.timesteps,
            module_database_path=module_database_path,
            inverter_database_path=inverter_database_path,
            weather_series=self.read_weather_series(),
        )

    def database_path(self, database: PVLibModuleAndInverterEnum) -> Optional[str]:
        """Return the bundled file a module or inverter database is read from on this machine.

        The mapping from a database to its file is the producer's (it decides what the calculation
        reads); resolving that file to a path under ``hisim/inputs`` is this component's, because
        ``HISIMPATH`` is component-layer knowledge a producer may not import.

        Args:
            database: the module or inverter database the configuration names.

        Returns:
            Optional[str]: the path, or ``None`` when the parameters are fetched from pvlib online
                (``load_module_data``) or the database has no bundled file at all -- in which case the
                producer refuses it, as it always has.
        """
        if self.pvconfig.load_module_data:
            return None
        file_key = calculation.DATABASE_FILE_KEYS.get(database)
        if file_key is None:
            return None
        return str(utils.HISIMPATH["photovoltaic"][file_key])

    def weather_artifact_key(self) -> str:
        """Return the identity of the weather series this run computes from.

        The weather component publishes the digest of its series' cache key when it prepares; it is
        key material here, and it is what makes the PV key change when anything about the weather
        changes (spec §3.1). Its absence means the same thing the missing yearly arrays mean, and is
        reported the same way.

        Returns:
            str: the weather's artifact key.

        Raises:
            KeyError: if the weather component has not prepared before this one.
        """
        if not self.simulation_repository.entry_exists(Weather.SERIES_ARTIFACT_KEY):
            raise KeyError(
                "The weather series' artifact key was not found in the sim "
                "repository. Please check in your system setup that the "
                "weather component is added to the simulator before the pv "
                "system; its i_prepare_simulation publishes this key."
            )
        return str(self.simulation_repository.get_entry(Weather.SERIES_ARTIFACT_KEY))

    def read_weather_series(self) -> PvWeatherSeries:
        """Take the weather arrays the producer computes from out of the singleton repository.

        The weather component always publishes arrays covering the whole
        year at the simulation's resolution, while the simulation itself
        may span only part of it (e.g. one day or one week). Both index
        their series by timestep from the same start, so truncating to
        the simulated period yields exactly the values the old
        per-timestep computation produced.

        Returns:
            PvWeatherSeries: the eight series, cut to the simulated period.

        Raises:
            KeyError: if the weather component has not prepared before this one.
            ValueError: if the published arrays are shorter than the simulated period.
        """
        # The Weather publishes its full-year series into this simulation's repository in its
        # own i_prepare_simulation. prepare_calculation walks the components in the order the
        # setup added them, so a Weather added after this component has not published yet and
        # the lookup below would fail with a bare key name. Say what to do instead.
        if not self.simulation_repository.entry_exists(Weather.YEARLY_DIRECT_NORMAL_IRRADIANCE):
            raise KeyError(
                "The yearly weather arrays were not found in this simulation's "
                "sim repository. Please check in your system setup that the "
                "weather component is added to the simulator before the pv "
                "system; its i_prepare_simulation publishes these arrays."
            )

        dni_extra = self.simulation_repository.get_entry(Weather.YEARLY_DIRECT_NORMAL_IRRADIANCE_EXTRA)
        dni = self.simulation_repository.get_entry(Weather.YEARLY_DIRECT_NORMAL_IRRADIANCE)
        dhi = self.simulation_repository.get_entry(Weather.YEARLY_DIFFUSE_HORIZONTAL_IRRADIANCE)
        ghi = self.simulation_repository.get_entry(Weather.YEARLY_GLOBAL_HORIZONTAL_IRRADIANCE)
        azimuth = self.simulation_repository.get_entry(Weather.YEARLY_AZIMUTH)
        apparent_zenith = self.simulation_repository.get_entry(Weather.YEARLY_APPARENT_ZENITH)
        temperature = self.simulation_repository.get_entry(Weather.YEARLY_TEMPERATURE_OUTSIDE)
        wind_speed = self.simulation_repository.get_entry(Weather.YEARLY_WIND_SPEED)

        number_of_timesteps = self.my_simulation_parameters.timesteps
        if len(dni) < number_of_timesteps:
            raise ValueError(
                f"The yearly weather arrays in this simulation's sim repository "
                f"hold {len(dni)} values but the simulation needs "
                f"{number_of_timesteps}. The arrays do not match the "
                f"simulation parameters (wrong resolution or duration)."
            )
        return PvWeatherSeries.of(
            dni_extra=dni_extra[:number_of_timesteps],
            dni=dni[:number_of_timesteps],
            dhi=dhi[:number_of_timesteps],
            ghi=ghi[:number_of_timesteps],
            azimuth=azimuth[:number_of_timesteps],
            apparent_zenith=apparent_zenith[:number_of_timesteps],
            temperature=temperature[:number_of_timesteps],
            wind_speed=wind_speed[:number_of_timesteps],
        )

    def cache_entry(self, calculation_inputs: PvSeriesInputs) -> CacheEntry:
        """Look the produced series up in the cache under the key of the producer that makes it.

        The key is ``sha256(artifact kind : code fingerprint : third-party fingerprint : DTO JSON)``
        (``roadmap/cache_service_spec.md`` §3). The two fingerprints are read from the producer
        module's import closure, so an edit to the calculation invalidates the entry by itself, and
        the DTO carries the weather's own key, so an edit to the weather invalidates it too.

        Args:
            calculation_inputs: the DTO from :meth:`build_calculation_inputs`.

        Returns:
            CacheEntry: where the entry is or will be, and whether it is there.
        """
        key = CacheKey.for_producer(ARTIFACT_KIND, calculation, calculation_inputs)
        return CacheClient.from_environment().lookup_producer(key, self.my_simulation_parameters.cache_dir_path)

    def interpolate(self, pd_database: Any, year: Any) -> Any:
        """Interpolates."""
        lastday = pd.Series(
            pd_database[-1],
            index=[pd.to_datetime(datetime.datetime(year, 12, 31, 22, 59), utc=True).tz_convert("Europe/Berlin")],
        )

        pd_database = pd_database.append(lastday)
        pd_database = pd_database.sort_index()
        return pd_database.resample("1min").asfreq().interpolate(method="linear").tolist()
