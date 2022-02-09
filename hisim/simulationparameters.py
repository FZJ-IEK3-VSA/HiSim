from typing import List
import datetime

class SimulationParameters:
    def __init__(self, start_date, end_date, seconds_per_timestep, year=None, post_processing_options:List = []):
        self.start_date = start_date
        self.end_date = end_date
        self.seconds_per_timestep = seconds_per_timestep
        self.duration = end_date - start_date
        total_seconds = self.duration.total_seconds()
        self.timesteps: int = int(total_seconds/seconds_per_timestep)
        self.year = year
        self.post_processing_options = post_processing_options

    @classmethod
    def full_year(cls, year: int, seconds_per_timestep: int):
        return cls(datetime.date(year, 1, 1), datetime.date(year+1, 1, 1), seconds_per_timestep, year)

    @classmethod
    def january_only(cls, year: int, seconds_per_timestep: int):
        return cls(datetime.date(year, 1, 1), datetime.date(year, 1, 31), seconds_per_timestep)

    @classmethod
    def one_day_only(cls, year: int, seconds_per_timestep: int):
        return cls(datetime.date(year, 1, 1), datetime.date(year, 1, 2), seconds_per_timestep)
