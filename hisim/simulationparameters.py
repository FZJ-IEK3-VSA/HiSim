from typing import List, Optional
from dataclasses_json import dataclass_json
from dataclasses import dataclass
import datetime
from hisim.utils import PostProcessingOptions

@dataclass_json
@dataclass()
class SystemConfig:
    def __init__( self,
                 predictive : bool = False,
                 prediction_horizon : int = 24 * 3600,
                 pv_included : bool = True,
                 smart_devices_included : bool = True,
                 boiler_included : Optional[ str ] = 'electricity',
                 heating_device_included : Optional[ str ] = 'heat_pump' ):
        self.predictive = predictive
        self.prediction_horizon = prediction_horizon
        self.pv_included = pv_included
        self.smart_devices_included = smart_devices_included
        self.boiler_included = boiler_included
        self.heating_device_included = heating_device_included

class SimulationParameters:
    def __init__(self, start_date, end_date, seconds_per_timestep, post_processing_options:List = []):
        self.start_date = start_date
        self.end_date = end_date
        self.seconds_per_timestep = seconds_per_timestep
        self.duration = end_date - start_date
        total_seconds = self.duration.total_seconds()
        self.timesteps: int = int(total_seconds/seconds_per_timestep)
        self.year = start_date.year
        self.post_processing_options = post_processing_options
        self.system_config = SystemConfig( )

    @classmethod
    def full_year(cls, year: int, seconds_per_timestep: int):
        return cls(datetime.date(year, 1, 1), datetime.date(year+1, 1, 1), seconds_per_timestep)

    def enable_all_options(self):
        for option in PostProcessingOptions:
            self.post_processing_options.append(option)

    @classmethod
    def full_year_all_options(cls, year: int, seconds_per_timestep: int):
        pars = cls(datetime.date(year, 1, 1), datetime.date(year + 1, 1, 1), seconds_per_timestep)
        pars.enable_all_options()
        return pars

    @classmethod
    def january_only(cls, year: int, seconds_per_timestep: int):
        return cls(datetime.date(year, 1, 1), datetime.date(year, 1, 31), seconds_per_timestep)
    
    @classmethod
    def one_week_only(cls, year: int, seconds_per_timestep: int):
        return cls(datetime.date(year, 1, 1), datetime.date(year, 1, 8), seconds_per_timestep)

    @classmethod
    def one_day_only(cls, year: int, seconds_per_timestep: int):
        return cls(datetime.date(year, 1, 1), datetime.date(year, 1, 2), seconds_per_timestep)

    def get_unique_key(self):
        return str(self.start_date) + "###" + str(self.end_date) + "###"  + str(self.seconds_per_timestep) + "###" + str(self.year) + "###" + str(self.timesteps)
    
    def reset_system_config( self, predictive : bool = False, prediction_horizon : int = 0, pv_included : bool = True, smart_devices_included : bool = True, 
                                   boiler_included : Optional[ str ] = 'electricity', heating_device_included : Optional[ str ] = 'heat_pump' ):
        self.system_config = SystemConfig( predictive = predictive,
                                           prediction_horizon = prediction_horizon,
                                           pv_included = pv_included, 
                                           smart_devices_included = smart_devices_included,
                                           boiler_included = boiler_included,
                                           heating_device_included = heating_device_included )
    
    



