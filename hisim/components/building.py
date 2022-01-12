# Generic/Built-in
import math
import pvlib
import pandas as pd
import numpy as np

# Owned
import globals
import component as cp
import loadtypes as lt

from components.configuration import PhysicsConfig
from components.configuration import LoadConfig
from functools import lru_cache



__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Dr. Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

@lru_cache(maxsize=16)
def calc_solar_gains(sun_azimuth,
                     DNI,
                     DHI,
                     GHI,
                     dni_extra,
                     apparent_zenith,
                     alititude_tilt,
                     azimuth_tilt,
                     reduction_factor_with_area):
    """
    Calculates the Solar Gains in the building zone through the set Window

    :param sun_altitude: Altitude Angle of the Sun in Degrees
    :type sun_altitude: float
    :param sun_azimuth: Azimuth angle of the sun in degrees
    :type sun_azimuth: float
    :param normal_direct_radiation: Normal Direct Radiation from weather file
    :type normal_direct_radiation: float
    :param horizontal_diffuse_radiation: Horizontal Diffuse Radiation from weather file
    :type horizontal_diffuse_radiation: float
    :return: self.incident_solar, Incident Solar Radiation on window
    :return: self.solar_gains - Solar gains in building after transmitting through the window
    :rtype: float
    """
    poa_irrad = pvlib.irradiance.get_total_irradiance(alititude_tilt,
                                     azimuth_tilt,
                                     apparent_zenith,
                                     sun_azimuth,
                                     DNI,
                                     GHI,
                                     DHI,
                                     dni_extra)

    if math.isnan(poa_irrad["poa_direct"]):
        return 0
    else:
        return poa_irrad["poa_direct"] * reduction_factor_with_area

class BuildingState:

    def __init__(self,
                 t_m,
                 c_m):
        self.t_m = t_m
        self.c_m = c_m

    def cal_stored_energy(self):
        return (self.t_m * self.c_m) / 3600

    def self_copy(self):
        return BuildingState(self.t_m, self.c_m)

class Building(cp.Component):
    """
    Building class provides multiple typologies of residences based on the
    EPISCOPE/TABULA project database. EPISCOPE/TABULA project involves a
    collection of data from 12 European countries, listing among others,
    heat coefficient, area, volumes, light transmissibility, house heat capacity
    for various residence construction elements. These typologies are categorized
    by year of construction, residence type and degree of refurbishment. For
    information, please access site: https://episcope.eu/building-typology/webtool/

    Parameters
    ----------
    building_code :str
        Code reference to a specific residence typology list in EPISCOPE/TABULA database
    bClass: str
        Heat capacity of residence defined using one of the following terms:
            - very light
            - light
            - medium
            - heavy
            - very heavy
    initial_temperature : float
        Initial internal temperature of residence in Celsius
    sim_params : Simulator
        Simulator object used to carry the simulation using this class
    seconds_per_timestep : int
        Number of seconds per simulation timestep
    """

    # Inputs
    ThermalEnergyDelivered = "ThermalEnergyDelivered"
    MassInput = "MassInput"
    TemperatureInput = "TemperatureInput"

    Altitude = "Altitude"
    Azimuth = "Azimuth"
    ApparentZenith = "ApparentZenith"
    DirectNormalIrradiance = "DirectNormalIrradiance"
    DirectNormalIrradianceExtra = "DirectNormalIrradianceExtra"
    DiffuseHorizontalIrradiance = "DiffuseHorizontalIrradiance"
    GlobalHorizontalIrradiance = "GlobalHorizontalIrradiance"
    TemperatureOutside = "TemperatureOutside"
    HeatingByResidents = "HeatingByResidents"
    SolarGainThroughWindows = "SolarGainThroughWindows"

    # Outputs
    TemperatureMean = "Residence Temperature"
    TemperatureAir = "TemperatureAir"
    TotalEnergyToResidence = "TotalEnergyToResidence"
    StoredEnergyVariation = "StoredEnergyVariation"
    InternalLoss = "InternalLoss"
    OldStoredEnergy = "OldStoredEnergy"
    CurrentStoredEnergy = "CurrentStoredEnergy"
    MassOutput = "MassOutput"
    TemperatureOutput = "TemperatureOutput"
    # Similar components to connect to:
    # 1. Weather
    # 2. Occupancy
    # 3. HeaterComponent (HeatPump,...)

    def __init__(self,
                 building_code="DE.N.SFH.05.Gen.ReEx.001.002",
                 bClass="medium",
                 initial_temperature=23,
                 seconds_per_timestep=60,
                 sim_params=None):
        super().__init__(name="Building")

        self.build(bClass, building_code, seconds_per_timestep, sim_params)

        self.state = BuildingState(t_m=initial_temperature, c_m=self.c_m)
        self.previous_state = self.state.self_copy()

    #===================================================================================================================

        self.thermal_energy_deliveredC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                                        self.ThermalEnergyDelivered,
                                                                        lt.LoadTypes.Heating,
                                                                        lt.Units.Watt,
                                                                        False)
        self.mass_inputC: cp.ComponentInput = self.add_input(self.ComponentName,
                                                         self.MassInput,
                                                         lt.LoadTypes.WarmWater,
                                                         lt.Units.kg_per_sec,
                                                         False)
        self.temperature_inputC: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                self.TemperatureInput,
                                                                lt.LoadTypes.WarmWater,
                                                                lt.Units.Celsius,
                                                                False)

        self.altitudeC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                         self.Altitude,
                                                         lt.LoadTypes.Any,
                                                         lt.Units.Degrees,
                                                         True)
        self.azimuthC : cp.ComponentInput  = self.add_input(self.ComponentName,
                                                         self.Azimuth,
                                                         lt.LoadTypes.Any,
                                                         lt.Units.Degrees,
                                                         True)
        self.DNIC : cp.ComponentInput      = self.add_input(self.ComponentName,
                                                            self.DirectNormalIrradiance,
                                                            lt.LoadTypes.Irradiance,
                                                            lt.Units.Wm2,
                                                            True)
        self.DHIC : cp.ComponentInput      = self.add_input(self.ComponentName,
                                                        self.DiffuseHorizontalIrradiance,
                                                        lt.LoadTypes.Irradiance,
                                                        lt.Units.Wm2,
                                                        True)
        self.GHIC: cp.ComponentInput = self.add_input(self.ComponentName,
                                                   self.GlobalHorizontalIrradiance,
                                                   lt.LoadTypes.Irradiance,
                                                   lt.Units.Wm2,
                                                   True)
        self.DNIextraC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                         self.DirectNormalIrradianceExtra,
                                                         lt.LoadTypes.Irradiance,
                                                         lt.Units.Wm2,
                                                         True)
        self.apparent_zenithC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                                self.ApparentZenith,
                                                                lt.LoadTypes.Any,
                                                                lt.Units.Degrees,
                                                                True)
        self.t_outC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                      self.TemperatureOutside,
                                                      lt.LoadTypes.Temperature,
                                                      lt.Units.Celsius,
                                                      True)

        self.occupancy_heat_gainC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                                    self.HeatingByResidents,
                                                                    lt.LoadTypes.Heating,
                                                                    lt.Units.Watt,
                                                                    True)

        self.t_mC : cp.ComponentOutput = self.add_output(self.ComponentName,
                                                      self.TemperatureMean,
                                                      lt.LoadTypes.Temperature,
                                                      lt.Units.Celsius)

        #self.t_airC : cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                self.TemperatureAir,
        #                                                lt.LoadTypes.Temperature,
        #                                                lt.Units.Celsius)
        self.total_energy_to_residenceC : cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                           self.TotalEnergyToResidence,
                                                                           lt.LoadTypes.Any,
                                                                           lt.Units.Any)
        self.solar_gain_through_windowsC : cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                             self.SolarGainThroughWindows,
                                                                             lt.LoadTypes.Heating,
                                                                             lt.Units.Watt)

        #self.internal_lossC : cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                        self.InternalLoss,
        #                                                        lt.LoadTypes.Heating,
        #                                                        lt.Units.Watt)
        #self.old_stored_energyC : cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                        self.OldStoredEnergy,
        #                                                        lt.LoadTypes.Any,
        #                                                        lt.Units.Any)
        #self.current_stored_energyC : cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                        self.CurrentStoredEnergy,
        #                                                        lt.LoadTypes.Any,
        #                                                        lt.Units.Any)
        #self.stored_energy_variationC : cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                                 self.StoredEnergyVariation,
        #                                                                 lt.LoadTypes.Any,
        #                                                                 lt.Units.Any)
        #
        #self.mass_output: cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                    self.MassOutput,
        #                                                    lt.LoadTypes.WarmWater,
        #                                                    lt.Units.kg_per_sec)
        #self.temperature_output: cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                           self.TemperatureOutput,
        #                                                           lt.LoadTypes.WarmWater,
        #                                                           lt.Units.Celsius)

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        #if timestep >=10392 and force_convergence:
        #    print("Stop herj!")
        #if force_convergence:
        #    return

        # Gets inputs
        if hasattr(self, "solar_gain_through_windows") is False:
            altitude = stsv.get_input_value(self.altitudeC)
            azimuth = stsv.get_input_value(self.azimuthC)
            DNI = stsv.get_input_value(self.DNIC) #/ self.seconds_per_timestep
            DHI = stsv.get_input_value(self.DHIC) #/ self.seconds_per_timestep
            GHI = stsv.get_input_value(self.GHIC) #/ self.seconds_per_timestep
            dni_extra = stsv.get_input_value(self.DNIextraC) #/ self.seconds_per_timestep
            apparent_zenith = stsv.get_input_value(self.apparent_zenithC)

        #occupancy_heat_gain = stsv.get_input_value(self.occupancy_heat_gainC) / self.seconds_per_timestep
        #I guess you wanted to transfer W to Wh
        occupancy_heat_gain = stsv.get_input_value(self.occupancy_heat_gainC) * self.seconds_per_timestep / 3600
        t_out = stsv.get_input_value(self.t_outC)


        # With TES [In Development]
        if self.mass_inputC.SourceOutput is not None:
            if force_convergence:
                return
            heat_demand = stsv.get_input_value(self.thermal_energy_deliveredC)  # W
            mass_input_sec = stsv.get_input_value(self.mass_input)  # kg/s
            #mass_input = mass_input_sec * self.seconds_per_timestep  # kg
            mass_input = mass_input_sec
            temperature_input = stsv.get_input_value(self.temperature_input)  # °C

            if heat_demand > 0 and (mass_input == 0 and temperature_input == 0):
                """first iteration --> random numbers"""
                temperature_input = 40.456
                #mass_input = 0.0123 * self.seconds_per_timestep
                mass_input = 0.0123

            if heat_demand > 0:
                # massflow by configuration class is given in kg/s

                # J = W * s
                massflows_possible = LoadConfig.possible_massflows_load  # kg/s
                mass_flow_level = 0
                # K = W / (W/kgK * kg/s)
                temperature_delta_heat = heat_demand / (
                    PhysicsConfig.water_specific_heat_capacity * massflows_possible[mass_flow_level])
                while temperature_delta_heat > LoadConfig.delta_T:
                    mass_flow_level += 1
                    temperature_delta_heat = heat_demand / (
                        PhysicsConfig.water_specific_heat_capacity * massflows_possible[mass_flow_level])

                # kg/timestep = kg/s * seconds_per_timestep
                mass_input_load = massflows_possible[mass_flow_level] * self.seconds_per_timestep

                # mass_input_load = LoadConfig.massflow_load * self.seconds_per_timestep
                energy_demand = heat_demand * self.seconds_per_timestep
                enthalpy_slice = mass_input_load * temperature_input * PhysicsConfig.water_specific_heat_capacity
                enthalpy_new = enthalpy_slice - energy_demand
                temperature_new = enthalpy_new / (mass_input_load * PhysicsConfig.water_specific_heat_capacity)


            else:
                # no water is flowing
                temperature_new = temperature_input
                mass_input_load = 0


            mass_output_load = mass_input_load / self.seconds_per_timestep  # kg/timestep --> kg/s
            self.test_new_temperature = temperature_new

            #stsv.set_output_value(self.mass_output, mass_output_load)
            #stsv.set_output_value(self.temperature_output, temperature_new)

        # Only with HeatPump
        elif self.thermal_energy_deliveredC.SourceOutput is not None:
            #thermal_energy_delivered = stsv.get_input_value(self.thermal_energy_deliveredC) / seconds_per_timestep
            #I guess you wanted to transfer W to Wh
            thermal_energy_delivered = stsv.get_input_value(self.thermal_energy_deliveredC) * self.seconds_per_timestep / 3600
        else:
            thermal_energy_delivered = 10

        t_m_prev = self.state.t_m
        #old_stored_energy = self.state.cal_stored_energy()

        # Performs calculations
        if hasattr(self, "solar_gain_through_windows") is False:
            solar_gain_through_windows = self.get_solar_gain_through_windows(altitude=altitude,
                                                                             azimuth=azimuth,
                                                                             DNI=DNI,
                                                                             DHI=DHI,
                                                                             GHI=GHI,
                                                                             dni_extra=dni_extra,
                                                                             apparent_zenith=apparent_zenith) / self.seconds_per_timestep
        else:
            solar_gain_through_windows = self.solar_gain_through_windows[timestep]

        t_m, t_air, t_s, phi_loss = self.calc_temperatures_crank_nicolson(energy_demand=thermal_energy_delivered,
                                              internal_gains=occupancy_heat_gain,
                                              solar_gains=solar_gain_through_windows,
                                              t_out=t_out,
                                              t_m_prev=t_m_prev)

        self.state.t_m = t_m
        #self.state.t_m = t_m
        #current_stored_energy = self.state.cal_stored_energy()

        #stored_energy_variation = current_stored_energy - old_stored_energy
        total_energy_to_residence = solar_gain_through_windows + occupancy_heat_gain + thermal_energy_delivered
        #internal_loss = total_energy_to_residence - stored_energy_variation

        # Returns outputs
        #stsv.set_output_value(self.t_mC, t_air)
        stsv.set_output_value(self.t_mC, t_m)
        #stsv.set_output_value(self.t_airC, t_air)
        stsv.set_output_value(self.total_energy_to_residenceC, phi_loss)
        stsv.set_output_value(self.solar_gain_through_windowsC, solar_gain_through_windows*seconds_per_timestep)

        #stsv.set_output_value(self.internal_lossC, internal_loss)
        #stsv.set_output_value(self.stored_energy_variationC, stored_energy_variation)
        #stsv.set_output_value(self.current_stored_energyC, current_stored_energy)
        #stsv.set_output_value(self.old_stored_energyC, old_stored_energy)

        # Saves solar gains cache
        if hasattr(self, "solar_gain_through_windows") is False:
            self.cache[timestep] = solar_gain_through_windows
            if timestep + 1 == self.cache_length:
                database = pd.DataFrame(self.cache, columns=["solar_gain_through_windows"])
                database.to_csv(self.cache_path, sep=",", decimal=".", index=False)

    def i_save_state(self):
        self.previous_state = self.state.self_copy()

    def i_restore_state(self):
        self.state = self.previous_state.self_copy()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def build(self, bClass, buildingcode, seconds_per_timestep, sim_params):
        if sim_params is not None:
            #self.time_correction_factor = 1 / sim_params.seconds_per_timestep
            self.time_correction_factor = sim_params.seconds_per_timestep / 3600
            self.seconds_per_timestep = sim_params.seconds_per_timestep
            self.timesteps = sim_params.timesteps
        else:
            self.seconds_per_timestep = seconds_per_timestep
            #self.time_correction_factor = 1/seconds_per_timestep
            #I guess you wanted to transfer W to Wh
            self.time_correction_factor = seconds_per_timestep / 3600
            self.timesteps = int(1E3)

        self.parameters = [ bClass, buildingcode ]

        # CONSTANTS
        self.h_ms = 9.1         # Heat transfer coefficient between nodes "m" and "s" (12.2.2 E64 P79)
        self.lambda_at = 4.5    # Dimensionless ratio between surfaces and the useful surfaces (7.2.2.2 E9 P36)
        self.h_is = 3.45        # Heat transfer coefficient between nodes "air" and "s" (7.2.2.2 E9 P35)
        self.bClass_f_a = {
            "very light": 2.5,
            "light": 2.5,
            "medium": 2.5,
            "heavy": 3.0,
            "very heavy": 3.5,
        }
        self.bClass_f_c = {
            "very light": 8e4,
            "light": 1.1e5,
            "medium": 1.65e5,
            "heavy": 2.6e5,
            "very heavy": 3.7e5,
        }
        self.bClass = bClass
        self.ven_method = "EPISCOPE"

        # Imports EPISCODE/TABULA building sets database
        self.get_building(buildingcode)

        # Get physical parameters
        self.get_physical_param()

        # Gets conductances
        self.get_conducs()

        self.set_time_correction()
        self.set_time_correction(self.time_correction_factor)
        #self.calc_solar_gains_jit = jit(nopython=True)(calc_solar_gains)

    def __str__(self):
        entire = str()
        lines = self.write_to_report()
        for index, line in enumerate(lines):
            if index == 0:
                entire = line
            else:
                entire = "{}\n{}".format(entire,line)
        return entire

    def write_to_report(self):
        lines = []
        lines.append("Name: {}".format(self.ComponentName))
        lines.append("Code: {}".format(self.buildingcode))

        lines.append("")
        lines.append("Conductances:")
        lines.append("h_tr_w [W/K]: {:4.2f}".format(self.h_tr_w))
        lines.append("h_tr_em [W/K]: {:4.2f}".format(self.h_tr_em))
        lines.append("h_tr_is [W/K]: {:4.2f}".format(self.h_tr_is))
        lines.append("h_tr_ms [W/K]: {:4.2f}".format(self.h_tr_ms))
        lines.append("h_ve_adj [W/K]: {:4.2f}".format(self.h_ve_adj))
        lines.append("H_Ventilation [kWh/a]: {}".format(self.h_ve_adj_ref))

        lines.append(" ")
        lines.append("Areas:")
        lines.append("A_f [m^2]: {:4.1f}".format(self.A_f))
        lines.append("A_m [m^2]: {:4.1f}".format(self.A_m))
        lines.append("A_t [m^2]: {:4.1f}".format(self.A_t))
        lines.append("Room volume [m^3]: {}".format(self.room_vol))

        lines.append(" ")
        lines.append("Capacitance:")
        lines.append("Capacitance [Wh/m^2.K]: {:4.2f}".format(self.c_m*3600/self.A_f))
        lines.append("Capacitance [Wh/K]: {:4.2f}".format((self.c_m*3600)))
        lines.append("Capacitance [J/K]: {:4.2f}".format(self.c_m))

        lines.append("Capacitance Ref [Wh/m^2.K]: {:4.2f}".format(self.c_m_ref))
        lines.append("Capacitance Ref [Wh/K]: {:4.2f}".format(self.c_m_ref * self.A_f))
        lines.append("Capacitance Ref [J/K]: {:4.2f}".format((self.c_m_ref * self.A_f) /3600))

        lines.append(" ")
        lines.append("Heat Transfers:")
        lines.append("Annual losses heating [kWh/m^2.a]: {}".format(self.q_ht_ref))
        lines.append("Annual losses heating [kWh/a]: {}".format(self.q_ht_ref * self.A_f))
        lines.append("Q_int [kWh/m^2.a]: {}".format(self.q_int_ref))
        lines.append("Q_int [kWh/a]: {}".format(self.q_int_ref * self.A_f))
        lines.append("Q_sol [kWh/m^2.a]: {}".format(self.q_sol_ref))
        lines.append("Q_sol [kWh/(a)]: {}".format(self.q_sol_ref * self.A_f))
        lines.append("=============== REFERENCE ===============")
        lines.append("Balance Heating Demand Ref [kWh/m^2.a]: {}".format(self.q_h_nd_ref))
        lines.append("Balance Heating Demand Ref [kWh/a]: {}".format(self.q_h_nd_ref * self.A_f))
        return lines

    @property
    def h_tr_1(self):
        """
        Definition to simplify calc_phi_m_tot
        # (C.6) in [C.3 ISO 13790]
        """
        return 1.0 / (1.0 / self.h_ve_adj + 1.0 / self.h_tr_is)

    @property
    def h_tr_2(self):
        """
        Definition to simplify calc_phi_m_tot
        # (C.7) in [C.3 ISO 13790]
        """
        return self.h_tr_1 + self.h_tr_w

    @property
    def h_tr_3(self):
        """
        Definition to simplify calc_phi_m_tot
        # (C.8) in [C.3 ISO 13790]
        """
        return 1.0 / (1.0 / self.h_tr_2 + 1.0 / self.h_tr_ms)

    @property
    def t_opperative(self):
        """
        The opperative temperature is a weighted average of the air and mean radiant temperatures.
        It is not used in any further calculation at this stage
        # (C.12) in [C.3 ISO 13790]
        """
        return 0.3 * self.t_air + 0.7 * self.t_s

    def get_h_tr_w(self):
        # h_tr_w: conductance between exterior temperature and surface temperature
        # Objects: Doors, windows, curtain walls and windowed walls ISO 7.2.2.2
        ws = ["Window_1", "Window_2", "Door_1"]
        #ws = ["Window_1", "Window_2"]

        self.h_tr_w = 0.0
        for w in ws:
            self.h_tr_w += float(self.buildingdata["H_Transmission_" + w].values[0])

    def get_h_tr_em(self):
        # Conductance of opaque surfaces to exterior [W/K]
        #opaque_walls = ["Wall_1", "Wall_2", "Wall_3"
        opaque_walls = ["Wall_1", "Wall_2", "Wall_3", "Roof_1", "Roof_2","Floor_1","Floor_2"]
        # h_tr_em_inv = 0.0
        # for ow in opaque_walls:
        #     h_tr_em_obj = float(self.buildingdata["H_Transmission_" + ow].values[0])
        #     if h_tr_em_obj != 0.0:
        #         h_tr_em_inv += 1/h_tr_em_obj
        # self.h_tr_em = 1/h_tr_em_inv

        # Version 1
        #self.h_tr_em = 0.0
        #for ow in opaque_walls:
        #    self.h_tr_em += float(self.buildingdata["H_Transmission_" + ow].values[0])

        # Version 2
        self.h_tr_op = 0.0
        for ow in opaque_walls:
            self.h_tr_op += float(self.buildingdata["H_Transmission_" + ow].values[0])

        self.h_tr_em = 1 / ( (1/self.h_tr_op) - (1/self.h_tr_ms) )



    def get_h_tr_is(self):
        # h_tr_is: conductance between air temperature and surface temperature
        self.h_tr_is = self.A_t * self.h_is

    def get_h_tr_ms(self):
        self.h_tr_ms = self.A_m * self.h_ms

    def get_h_ve_adj(self):
        # Ventilation
        if self.ven_method == "RC_BuildingSimulator":
            ach_vent = 1.5
            ach_infl = 0.5
            ventilation_efficiency = 0.6
            # Determine the ventilation conductance
            ach_tot = ach_infl + ach_vent  # Total Air Changes Per Hour
            # temperature adjustment factor taking ventilation and infiltration
            # [ISO: E -27]
            b_ek = (1 - (ach_vent / (ach_tot) ) * ventilation_efficiency)
            self.h_ve_adj = float(1200 * b_ek * self.room_vol * (ach_tot / 3600)) # Conductance through ventilation [W/M]
        elif self.ven_method == "EPISCOPE":
            #cp = 0.00028378 * 1E3  # [Wh/m3K]
            cp = 0.34
            self.h_ve_adj = cp * float(self.buildingdata["n_air_use"] + self.buildingdata["n_air_infiltration"]) * \
                            self.A_f * float(self.buildingdata["h_room"])

    def get_physical_param(self):
        # Windows area
        self.get_windows()

        # Reference area [m^2] (TABULA: Reference floor area )Ref: ISO standard 7.2.2.2
        self.A_f = float(self.buildingdata["A_C_Ref"].values[0])
        # total_internal_area = buildingdata["A_Estim_Floor"][1]

        self.A_m = self.A_f * self.bClass_f_a[self.bClass]
        self.A_t = self.A_f * self.lambda_at

        # Room Capacitance [J/K] (TABULA: Internal heat capacity) Ref: ISO standard 12.3.1.2
        self.c_m = self.bClass_f_c[self.bClass] * self.A_f

        # Building volume (TABULA: Conditioned building volume)
        self.room_vol = float(self.buildingdata["V_C"].values[0])


        # Reference properties from TABULA, but not used in the model
        self.q_sol_ref = float((self.buildingdata["q_sol"].values[0]))               # Floor area related heat load during heating season
        self.q_int_ref = float(self.buildingdata["q_int"].values[0])                 # Floor area related internal heat sources during heating season
        self.q_ht_ref = float(self.buildingdata["q_ht"].values[0])                   # Floor area related annual losses
        self.q_h_nd_ref = float(self.buildingdata["q_h_nd"].values[0])               # Energy need for heating

        # Internal heat capacity [Wh/(m^2.K)] (TABULA: Internal heat capacity)
        self.c_m_ref = float(self.buildingdata["c_m"].values[0])                         # internal heat capacity per m2 reference area

        # References properties
        self.h_ve_adj_ref = float(self.buildingdata["h_Ventilation"].values[0]) * self.A_f

    def set_time_correction(self, factor=1):
        if factor == 1:
            self.HasBeenConverted = False
        if self.HasBeenConverted is True:
            raise Exception("It has been already converted!")
        self.h_tr_w = self.h_tr_w * factor
        self.h_tr_em = self.h_tr_em * factor
        self.h_tr_is = self.h_tr_is * factor
        self.h_tr_ms = self.h_tr_ms * factor
        self.h_ve_adj = self.h_ve_adj * factor
        self.c_m = self.c_m #* factor
        # for i_gain in range(len(self.gain_by_occupancy)):
        #    self.gain_by_occupancy[i_gain] = self.gain_by_occupancy[i_gain] * factor
        #    self.internal_gains[i_gain] = self.internal_gains[i_gain] * factor
        if factor != 1:
            self.HasBeenConverted = True

    def get_conducs(self):
        """
        Calculates the conductances from the norm ISO 13970

        :key
        """
        self.get_h_tr_w()
        self.get_h_tr_ms()
        self.get_h_tr_em()
        self.get_h_tr_is()
        self.get_h_ve_adj()

    def get_building(self, buildingcode):
        df = pd.read_csv(globals.HISIMPATH["housing"],
                         decimal=",",
                         sep=";",
                         encoding="cp1252",
                         low_memory=False)
                         #error_bad_lines=False)


        # Gets parameters from chosen building
        self.buildingdata = df.loc[df["Code_BuildingVariant"] == buildingcode]
        self.buildingcode = buildingcode

    def get_windows(self):
        """
        Retrieves data about windows sizes

        :return:
        """
        # south_angle = 0
        # east_angle = south_angle - 90
        # north_angle = south_angle + 180
        # west_angle = south_angle + 90

        self.windows = []
        self.windows_area = 0.0
        south_angle = 180
        #windows_angles = {"South": south_angle,
        #                  "East": south_angle - 90,
        #                  "North": south_angle + 180,
        #                  "West": south_angle + 90}
        windows_angles = {"South": south_angle,
                          "East": south_angle - 90,
                          "North": south_angle - 180,
                          "West": south_angle + 90}

        windows_directions = ["South",
                              "East",
                              "North",
                              "West"]
        F_w = self.buildingdata["F_w"].values[0]
        F_f = self.buildingdata["F_f"].values[0]
        F_sh_vertical = self.buildingdata["F_sh_vert"].values[0]
        g_gln = self.buildingdata["g_gl_n"].values[0]
        for windows_direction in windows_directions:
            area = float(self.buildingdata["A_Window_" + windows_direction])
            if area != 0.0:
                self.windows.append(Window(azimuth_tilt=windows_angles[windows_direction],
                                           area=area,
                                           frame_area_fraction_reduction_factor=F_f,
                                           glass_solar_transmittance=g_gln,
                                           nonperpendicular_reduction_factor=F_w,
                                           external_shading_vertical_reduction_factor=F_sh_vertical))
                self.windows_area += area


        cache_filepath = globals.get_cache(classname=self.ComponentName, parameters=self.parameters)
        if cache_filepath is not None:
            self.solar_gain_through_windows = pd.read_csv(cache_filepath, sep=',', decimal='.')['solar_gain_through_windows'].tolist()
        else:
            self.cache = [0] * self.timesteps
            self.cache_path = globals.save_cache(self.ComponentName, self.parameters)
            self.cache_length = self.timesteps

    #@cached(cache=LRUCache(maxsize=16))
    def get_solar_gain_through_windows(self, altitude, azimuth, DNI, DHI, GHI, dni_extra, apparent_zenith):
        """
        Calculates the thermal solar gain passed to
        the building through the windows

        :return:
        """
        solar_gains = 0.0
        #parameters = [altitude, azimuth, DNI, DHI, GHI, dni_extra, apparent_zenith]
        #def calc_solar_gainxs(window):
        #    return window.calc_solar_gains(*parameters)


        #windows = self.windows
        if (DNI == 0 and DHI == 0 and GHI == 0) is False:
        #if (DNI == 0 and DHI == 0 and GHI == 0 and dni_extra == 0) is False:
            #a_pool = Pool()
            #result = a_pool.starmap(calc_solar_gainxs, windows)
            for index, window in enumerate(self.windows):
                #solar_gains = windows.calc_solar_gains(sun_altitude=altitude,
                #                         sun_azimuth=azimuth,
                #                         DNI=DNI,
                #                         DHI=DHI,
                #                         GHI=GHI,
                #                         dni_extra=dni_extra,
                #                         apparent_zenith=apparent_zenith)
                solar_gain = calc_solar_gains(sun_azimuth=azimuth,
                                              DNI=DNI,
                                              DHI=DHI,
                                              GHI=GHI,
                                              dni_extra=dni_extra,
                                              apparent_zenith=apparent_zenith,
                                              alititude_tilt=window.alititude_tilt,
                                              azimuth_tilt=window.azimuth_tilt,
                                              reduction_factor_with_area=window.reduction_factor_with_area)
                solar_gains += solar_gain
        return solar_gains

    def calc_temperatures_crank_nicolson(self, energy_demand, internal_gains, solar_gains, t_out, t_m_prev):
        """
        Determines node temperatures and computes derivation to determine the new node temperatures
        Used in: has_demand(), solve_energy(), calc_energy_demand()
        # section C.3 in [C.3 ISO 13790]
        """

        # Updates flows
        phi_loss = self.calc_heat_flow(t_out, internal_gains, solar_gains, energy_demand)

        # Updates total flow
        self.calc_phi_m_tot(t_out)

        # calculates the new bulk temperature POINT from the old one # CHECKED Requires t_m_prev
        self.calc_t_m_next(t_m_prev)

        # calculates the AVERAGE bulk temperature used for the remaining
        t_m = self.calc_t_m(t_m_prev)

        # Updates t_s
        t_s = self.calc_t_s(t_out, t_m)

        # Updates t_w
        t_air = self.calc_t_air(t_out, t_s)

        return t_m,  t_air, t_s, phi_loss
        #return t_m, t_air, t_s
        #return self.t_m, self.t_air, self.t_opperative, self.t_s

    def calc_heat_flow(self, t_out, internal_gains, solar_gains, energy_demand):
        """
        Calculates the heat flow from the solar gains, heating/cooling system, and internal gains into the building

        The input of the building is split into the air node, surface node, and thermal mass node based on
        on the following equations

        #C.1 - C.3 in [C.3 ISO 13790]

        Note that this equation has diverged slightly from the standard
        as the heating/cooling node can enter any node depending on the
        emission system selected

        """

        # Calculates the heat flows to various points of the building based on the breakdown in section C.2, formulas C.1-C.3
        # Heat flow to the air node
        self.phi_ia = 0.5 * internal_gains + energy_demand
        # Heat flow to the surface node
        self.phi_st = (1 - (self.A_m / self.A_t) -
                       (self.h_tr_w / (9.1 * self.A_t))) * (0.5 * internal_gains + solar_gains)
        # Heatflow to the thermal mass node
        self.phi_m = (self.A_m / self.A_t) * \
            (0.5 * internal_gains + solar_gains)

        self.phi_loss = ( self.h_tr_w / (9.1 * self.A_t) ) * (0.5 * internal_gains + solar_gains)
        return self.phi_loss

    def calc_t_m_next(self,t_m_prev):
        """
        Primary Equation, calculates the temperature of the next time step
        # (C.4) in [C.3 ISO 13790]
        """

        self.t_m_next = ((t_m_prev * ((self.c_m / 3600.0) - 0.5 * (self.h_tr_3 + self.h_tr_em))) +
                         self.phi_m_tot) / ((self.c_m / 3600.0) + 0.5 * (self.h_tr_3 + self.h_tr_em))


    def calc_phi_m_tot(self, t_out):
        """
        Calculates a global heat transfer. This is a definition used to simplify equation
        calc_t_m_next so it's not so long to write out
        # (C.5) in [C.3 ISO 13790]
        # h_ve = h_ve_adj and t_supply = t_out [9.3.2 ISO 13790]
        """

        t_supply = t_out  # ASSUMPTION: Supply air comes straight from the outside air

        self.phi_m_tot = self.phi_m + self.h_tr_em * t_out + \
            self.h_tr_3 * ( self.phi_st + self.h_tr_w * t_out + self.h_tr_1 *
                           ((self.phi_ia / self.h_ve_adj) + t_supply)) / self.h_tr_2


    def calc_t_m(self, t_m_prev):
        """
        Temperature used for the calculations, average between newly calculated and previous bulk temperature
        # (C.9) in [C.3 ISO 13790]
        """
        return (t_m_prev + self.t_m_next)/2

    def calc_t_s(self, t_out, t_m):
        """
        Calculate the temperature of the inside room surfaces
        # (C.10) in [C.3 ISO 13790]
        # h_ve = h_ve_adj and t_supply = t_out [9.3.2 ISO 13790]
        """

        t_supply = t_out  # ASSUMPTION: Supply air comes straight from the outside air

        return (self.h_tr_ms * t_m + self.phi_st + self.h_tr_w * t_out + self.h_tr_1 *
                    (t_supply + self.phi_ia / self.h_ve_adj)) / \
                   (self.h_tr_ms + self.h_tr_w + self.h_tr_1)

    def calc_t_air(self, t_out, t_s):
        """
        Calculate the temperature of the air node
        # (C.11) in [C.3 ISO 13790]
        # h_ve = h_ve_adj and t_supply = t_out [9.3.2 ISO 13790]
        """

        t_supply = t_out

        # Calculate the temperature of the inside air
        return (self.h_tr_is * t_s + self.h_ve_adj *
                      t_supply + self.phi_ia) / (self.h_tr_is + self.h_ve_adj)

class Window(object):
    """docstring for Window"""
    def __init__(self,
                 azimuth_tilt=None,
                 alititude_tilt=90,
                 area=None,
                 glass_solar_transmittance=0.6,
                 frame_area_fraction_reduction_factor=0.3,
                 external_shading_vertical_reduction_factor=1.0,
                 nonperpendicular_reduction_factor=0.9
                 ):
        # Angles
        self.alititude_tilt = alititude_tilt
        self.azimuth_tilt = azimuth_tilt
        self.alititude_tilt_rad = math.radians(alititude_tilt)
        self.azimuth_tilt_rad = math.radians(azimuth_tilt)

        # Area
        self.area = area

        # Transmittance
        self.glass_solar_transmittance = glass_solar_transmittance

        # Reduction factors
        self.nonperpendicular_reduction_factor = nonperpendicular_reduction_factor
        self.external_shading_vertical_reduction_factor = external_shading_vertical_reduction_factor
        self.frame_area_fraction_reduction_factor = frame_area_fraction_reduction_factor

        self.reduction_factor = glass_solar_transmittance \
                      * nonperpendicular_reduction_factor * external_shading_vertical_reduction_factor \
                      * (1 - frame_area_fraction_reduction_factor)

        self.reduction_factor_with_area = self.reduction_factor * area

    #@cached(cache=LRUCache(maxsize=5))
    #@lru_cache
    def calc_solar_gains(self, sun_altitude, sun_azimuth, DNI, DHI, GHI, dni_extra, apparent_zenith):
        """
        Calculates the Solar Gains in the building zone through the set Window

        :param sun_altitude: Altitude Angle of the Sun in Degrees
        :type sun_altitude: float
        :param sun_azimuth: Azimuth angle of the sun in degrees
        :type sun_azimuth: float
        :param normal_direct_radiation: Normal Direct Radiation from weather file
        :type normal_direct_radiation: float
        :param horizontal_diffuse_radiation: Horizontal Diffuse Radiation from weather file
        :type horizontal_diffuse_radiation: float
        :return: self.incident_solar, Incident Solar Radiation on window
        :return: self.solar_gains - Solar gains in building after transmitting through the window
        :rtype: float
        """
        albedo = 0.4
        # automatic pd time series in future pvlib version
        # calculate airmass
        airmass = pvlib.atmosphere.get_relative_airmass(apparent_zenith)
        # use perez model to calculate the plane of array diffuse sky radiation
        poa_sky_diffuse = pvlib.irradiance.perez(
            self.alititude_tilt,
            self.azimuth_tilt,
            DHI,
            np.float64(DNI),
            dni_extra,
            apparent_zenith,
            sun_azimuth,
            airmass,
        )
        # calculate ground diffuse with specified albedo
        poa_ground_diffuse = pvlib.irradiance.get_ground_diffuse(
            self.alititude_tilt, GHI, albedo=albedo
        )
        # calculate angle of incidence
        aoi = pvlib.irradiance.aoi(self.alititude_tilt, self.azimuth_tilt, apparent_zenith, sun_azimuth)
        # calculate plane of array irradiance
        poa_irrad = pvlib.irradiance.poa_components(aoi, np.float64(DNI), poa_sky_diffuse, poa_ground_diffuse)

        if math.isnan(poa_irrad["poa_direct"]):
            self.incident_solar = 0
        else:
            self.incident_solar = (poa_irrad["poa_direct"])* self.area

        solar_gains = self.incident_solar * self.glass_solar_transmittance \
                                       * self.nonperpendicular_reduction_factor * self.external_shading_vertical_reduction_factor \
                                       * ( 1 - self.frame_area_fraction_reduction_factor)
        return solar_gains

    def calc_direct_solar_factor(self, sun_altitude, sun_azimuth, apparent_zenith):
        """
        Calculates the cosine of the angle of incidence on the window
        """
        sun_altitude_rad = math.radians(sun_altitude)
        sun_azimuth_rad = math.radians(sun_azimuth)

        aoi = pvlib.irradiance.aoi(self.alititude_tilt, self.azimuth_tilt, apparent_zenith, sun_azimuth)

        direct_factor = math.cos(aoi) / (math.sin(sun_altitude_rad))



        """
        Proportion of the radiation incident on the window (cos of the incident ray)
        ref:Quaschning, Volker, and Rolf Hanitsch. "Shade calculations in photovoltaic systems."
        ISES Solar World Conference, Harare. 1995.
        """
        # Solution from the above mentioned study
        #direct_factor = (math.cos(sun_altitude_rad) * math.sin(self.alititude_tilt_rad) * \
        #                math.cos(sun_azimuth_rad - self.azimuth_tilt_rad) + \
        #                math.sin(sun_altitude_rad) * math.cos(self.alititude_tilt_rad)) / (math.sin(sun_altitude_rad))

        #direct_factor = max(0, direct_factor)

        ## Solution from GitHub repository
        #direct_factor = math.cos(sun_altitude_rad) * math.sin(self.alititude_tilt_rad) * \
        #                math.cos(sun_azimuth_rad - self.azimuth_tilt_rad) + \
        #                math.sin(sun_altitude_rad) * math.cos(self.alititude_tilt_rad)


        ## If the sun is in front of the window surface
        #if(math.degrees(math.acos(direct_factor)) > 90):
        #    direct_factor = 0
        #else:
        #    pass

        return direct_factor

    def calc_diffuse_solar_factor(self):
        """Calculates the proportion of diffuse radiation"""
        # Proportion of incident light on the window surface
        return (1 + math.cos(self.alititude_tilt_rad)) / 2






