"""Result Path Provider Module."""

# clean
import os
import datetime
import enum
from typing import Any, Optional

from hisim.sim_repository_singleton import SingletonMeta


class ResultPathProviderSingleton(metaclass=SingletonMeta):

    """ResultPathProviderSingleton class.

    According to your storting options and your input information a result path is created.
    """

    def __init__(
        self,
        module_directory: str,
        model_name: str,
        sorting_option: Any,
        variant_name: Optional[str],
        time_resolution_in_seconds: int,
        simulation_duration_in_days: int,
    ):
        """Initialize the class."""

        self.base_path: str = os.path.join(module_directory, "results")
        self.model_name: str = model_name
        self.datetime_string: str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.sorting_option: str = sorting_option

        if variant_name is None:
            variant_name = ""

        self.time_resolution_in_seconds = str(time_resolution_in_seconds) + "_seconds"
        self.variant_name = variant_name
        self.simulation_duration_in_days = str(simulation_duration_in_days) + "_days"

    def set_model_name(self, name):
        """Set model name."""
        self.model_name = name

    def set_sorting_option(self, sorting_option):
        """Set sorting option."""
        self.sorting_option = sorting_option

    def get_result_directory_name(self):  # *args
        """Get the result directory path."""

        if self.sorting_option == SortingOptionEnum.DEEP:
            path = os.path.join(
                self.base_path, self.model_name, self.variant_name, self.datetime_string
            )
        elif self.sorting_option == SortingOptionEnum.MASS_SIMULATION:
            # schauen ob verzeichnis schon da und aufsteigende nummer anängen
            idx = 1
            path = os.path.join(
                self.base_path, self.model_name, self.variant_name + "_" + str(idx)
            )
            while os.path.exists(path):
                idx = idx + 1
                path = os.path.join(
                    self.base_path, self.model_name, self.variant_name + "_" + str(idx)
                )
        elif self.sorting_option == SortingOptionEnum.FLAT:
            path = os.path.join(
                self.base_path,
                self.model_name
                + "_"
                + self.variant_name
                + "_"
                + self.time_resolution_in_seconds
                + "_"
                + self.simulation_duration_in_days
                + "_"
                + self.datetime_string,
            )
        return path


class SortingOptionEnum(enum.Enum):

    """A SortingOptionEnum class."""

    DEEP = 1
    MASS_SIMULATION = 2
    FLAT = 3
