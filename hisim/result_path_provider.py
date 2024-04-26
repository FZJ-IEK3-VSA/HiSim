"""Result Path Provider Module."""

# clean
import sys
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
    ):
        """Initialize the class."""
        self.base_path: Optional[str] = None
        self.model_name: Optional[str] = None
        self.variant_name: Optional[str] = None
        self.scenario_hash_string: Optional[str] = None
        self.further_result_folder_description: Optional[str] = None
        self.sorting_option: Any = SortingOptionEnum.FLAT
        self.time_resolution_in_seconds: Optional[int] = None
        self.simulation_duration_in_days: Optional[int] = None
        self.datetime_string: str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    def set_important_result_path_information(
        self,
        module_directory: str,
        model_name: str,
        variant_name: Optional[str],
        scenario_hash_string: Optional[str],
        sorting_option: Any,
        further_result_folder_description: Optional[str] = None,
    ) -> None:
        """Set important result path information."""
        self.set_base_path(module_directory=module_directory)
        self.set_model_name(model_name=model_name)
        self.set_variant_name(variant_name=variant_name)
        self.set_sorting_option(sorting_option=sorting_option)
        self.set_scenario_hash_string(scenario_hash_string=scenario_hash_string)
        self.set_further_result_folder_description(further_result_folder_description=further_result_folder_description)

    def set_base_path(self, module_directory: str) -> None:
        """Set base path."""
        self.base_path = os.path.join(module_directory, "results")

    def set_model_name(self, model_name: str) -> None:
        """Set model name."""
        self.model_name = model_name

    def set_variant_name(self, variant_name: Optional[str]) -> None:
        """Set variant name."""
        if variant_name is None:
            variant_name = ""
        self.variant_name = variant_name

    def set_scenario_hash_string(self, scenario_hash_string: Optional[str]) -> None:
        """Set scenario hash string."""
        if scenario_hash_string is None:
            scenario_hash_string = ""

        self.scenario_hash_string = scenario_hash_string

    def set_further_result_folder_description(self, further_result_folder_description: Optional[str]) -> None:
        """Set further result folder description."""
        self.further_result_folder_description = further_result_folder_description

    def set_sorting_option(self, sorting_option: Any) -> None:
        """Set sorting option."""
        self.sorting_option = sorting_option

    def set_time_resolution(self, time_resolution_in_seconds: int) -> None:
        """Set time resolution."""
        self.time_resolution_in_seconds = time_resolution_in_seconds

    def set_simulation_duration(self, simulation_duration_in_days: int) -> None:
        """Set simulation duration."""
        self.simulation_duration_in_days = simulation_duration_in_days

    def get_result_directory_name(self) -> Any:
        """Get the result directory path."""

        if (
            self.base_path is not None
            and self.model_name is not None
            and self.variant_name is not None
            and self.datetime_string is not None
            and self.scenario_hash_string is not None
        ):
            if self.sorting_option == SortingOptionEnum.DEEP:
                path = os.path.join(
                    self.base_path,
                    self.model_name,
                    self.variant_name,
                    self.datetime_string,
                )
            elif self.sorting_option == SortingOptionEnum.MASS_SIMULATION_WITH_INDEX_ENUMERATION:
                # schauen ob verzeichnis schon da und aufsteigende nummer anhängen
                idx = 1

                if self.further_result_folder_description is not None:
                    path = os.path.join(
                        self.base_path,
                        self.model_name,
                        self.further_result_folder_description,
                        self.variant_name + "_" + str(idx),
                    )
                    while os.path.exists(path):
                        idx = idx + 1
                        path = os.path.join(
                            self.base_path,
                            self.model_name,
                            self.further_result_folder_description,
                            self.variant_name + "_" + str(idx),
                        )
                else:
                    path = os.path.join(
                        self.base_path,
                        self.model_name,
                        self.variant_name + "_" + str(idx),
                    )
                    while os.path.exists(path):
                        idx = idx + 1
                        path = os.path.join(
                            self.base_path,
                            self.model_name,
                            self.variant_name + "_" + str(idx),
                        )
            elif self.sorting_option == SortingOptionEnum.MASS_SIMULATION_WITH_HASH_ENUMERATION:
                if self.further_result_folder_description is not None:
                    path = os.path.join(
                        self.base_path,
                        self.model_name,
                        self.further_result_folder_description,
                        self.variant_name + "_" + self.scenario_hash_string,
                    )
                else:
                    path = os.path.join(
                        self.base_path,
                        self.model_name,
                        self.variant_name + "_" + self.scenario_hash_string,
                    )

            elif self.sorting_option == SortingOptionEnum.FLAT:
                path = os.path.join(
                    self.base_path,
                    self.model_name + "_" + self.variant_name + self.datetime_string,
                )

            check_path_length(path=path)
            return path

        return None


def check_path_length(path: str) -> None:
    """Make sure that path name does not get too long for Windows."""

    character_limit_according_to_windows = 256
    # check if the system is windows
    is_windows = sys.platform.startswith("win")
    if is_windows and len(path) >= character_limit_according_to_windows:
        raise NameError(
            f"The path {path} exceeds the limit of 256 characters which is the limit for Windows. Please make your path shorter."
        )


class SortingOptionEnum(enum.Enum):

    """A SortingOptionEnum class."""

    DEEP = 1
    MASS_SIMULATION_WITH_INDEX_ENUMERATION = 2
    MASS_SIMULATION_WITH_HASH_ENUMERATION = 3
    FLAT = 4
