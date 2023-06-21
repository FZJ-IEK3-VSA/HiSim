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
    ):
        """Initialize the class."""
        self.base_path: Optional[str] = None
        self.model_name: Optional[str] = None
        self.variant_name: Optional[str] = None
        self.sorting_option: Any = SortingOptionEnum.FLAT
        self.time_resolution_in_seconds: Optional[int] = None
        self.simulation_duration_in_days: Optional[int] = None
        self.datetime_string: str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    def set_important_result_path_information(
        self,
        module_directory: str,
        model_name: str,
        variant_name: Optional[str],
        sorting_option: Any,
    ) -> None:
        """Set important result path information."""
        self.set_base_path(module_directory=module_directory)
        self.set_model_name(model_name=model_name)
        self.set_variant_name(variant_name=variant_name)
        self.set_sorting_option(sorting_option=sorting_option)

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

    def set_sorting_option(self, sorting_option: Any) -> None:
        """Set sorting option."""
        self.sorting_option = sorting_option

    def set_time_resolution(self, time_resolution_in_seconds: int) -> None:
        """Set time resolution."""
        self.time_resolution_in_seconds = time_resolution_in_seconds

    def set_simulation_duration(self, simulation_duration_in_days: int) -> None:
        """Set simulation duration."""
        self.simulation_duration_in_days = simulation_duration_in_days

    def get_result_directory_name(self) -> Any:  # *args
        """Get the result directory path."""
        if None in (
            self.base_path,
            self.model_name,
            self.variant_name,
        ):
            # The variables must be given a str-value, otherwise a result path can not be created.
            return None


        if [
            isinstance(x, str)
            for x in [
                self.base_path,
                self.model_name,
                self.variant_name,
                self.datetime_string,
            ]
        ]:
            if self.sorting_option == SortingOptionEnum.DEEP:
                path = os.path.join(
                    self.base_path,  # type: ignore
                    self.model_name,  # type: ignore
                    self.variant_name,  # type: ignore
                    self.datetime_string,  # type: ignore
                )
            elif self.sorting_option == SortingOptionEnum.MASS_SIMULATION:
                # schauen ob verzeichnis schon da und aufsteigende nummer anängen
                idx = 1
                path = os.path.join(
                    self.base_path, self.model_name, self.variant_name + "_" + str(idx)  # type: ignore
                )
                while os.path.exists(path):
                    idx = idx + 1
                    path = os.path.join(
                        self.base_path,  # type: ignore
                        self.model_name,  # type: ignore
                        self.variant_name + "_" + str(idx),  # type: ignore
                    )
            elif self.sorting_option == SortingOptionEnum.FLAT:
                path = os.path.join(
                    self.base_path,  # type: ignore
                    self.model_name  # type: ignore
                    + "_"
                    + self.variant_name  # type: ignore
                    + "_"
                    + self.datetime_string,  # type: ignore
                )

            return path

        raise TypeError("The types of base_path, model_name, variant_name and datetime_string should be str.")


class SortingOptionEnum(enum.Enum):

    """A SortingOptionEnum class."""

    DEEP = 1
    MASS_SIMULATION = 2
    FLAT = 3
