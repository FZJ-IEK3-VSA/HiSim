# -*- coding: utf-8 -*-
import enum

@enum.unique
class HeatingSystems(str, enum.Enum):

    """ To parse heating systems in simulation inputs. """

    HEAT_PUMP = "HeatPump"
    ELECTRIC_HEATING = "ELectricHeating"
    OIL_HEATING = "OilHeating"
    GAS_HEATING = "GasHeating"
    DISTRICT_HEATING = "DistrictHeating"