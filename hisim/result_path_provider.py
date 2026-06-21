"""Result Path Provider Module.

Single source of truth for all result and artifact paths in the project.

A run is configured once via :meth:`ResultPathProviderSingleton.configure` (or the legacy
:meth:`ResultPathProviderSingleton.set_important_result_path_information`). The :class:`RunMode`
selects a sensible default directory layout, and individual artifacts (csv files, plots, KPIs,
reports, ...) are addressed through the typed :class:`ArtifactCategory` enum so callers no longer
hand-join path strings.
"""

# clean
import sys
import os
import re
import datetime
import enum
from pathlib import Path
from typing import Any, Optional, Union

from hisim.sim_repository_singleton import SingletonMeta


class RunMode(enum.Enum):

    """Determines the context a HiSim run is executed in.

    The run mode selects a sensible default directory layout (see ``DEFAULT_SORTING_OPTION_FOR_MODE``):
    - ``SINGLE``: a single interactive/CLI calculation -> ``FLAT``.
    - ``MASS``:   a parametric sweep / building sizer  -> hash enumeration.
    - ``TEST``:   unit and system-setup tests          -> ``FLAT``, no timestamp, test name in path.
    """

    SINGLE = 1
    MASS = 2
    TEST = 3


class SortingOptionEnum(enum.Enum):

    """A SortingOptionEnum class describing the directory naming/layout scheme."""

    DEEP = 1
    MASS_SIMULATION_WITH_INDEX_ENUMERATION = 2
    MASS_SIMULATION_WITH_HASH_ENUMERATION = 3
    FLAT = 4


# Default directory layout chosen per run mode. The run mode picks the layout; callers may still
# override it explicitly when configuring the provider.
DEFAULT_SORTING_OPTION_FOR_MODE = {
    RunMode.SINGLE: SortingOptionEnum.FLAT,
    RunMode.MASS: SortingOptionEnum.MASS_SIMULATION_WITH_HASH_ENUMERATION,
    RunMode.TEST: SortingOptionEnum.FLAT,
}

# Descriptor arguments of configure() that MUST be provided for each run mode.
REQUIRED_CONFIGURE_ARGS_FOR_MODE = {
    RunMode.SINGLE: ("model_name", "variant_name"),
    RunMode.MASS: ("model_name",),
    RunMode.TEST: ("test_name",),
}

# Descriptor arguments of configure() that MAY additionally be provided for each run mode.
# Anything provided that is neither required nor optional for the mode is rejected.
# (``base_path`` is intentionally not listed here: it is a root-location override allowed in any mode.)
OPTIONAL_CONFIGURE_ARGS_FOR_MODE = {
    RunMode.SINGLE: (),
    RunMode.MASS: (
        "variant_name",
        "scenario_hash_string",
        "sorting_option",
        "further_result_folder_description",
    ),
    RunMode.TEST: (),
}


class ArtifactCategory(enum.Enum):

    """Typed sub-directories for the different kinds of artifacts a run produces."""

    CSV = "csv"
    PLOTS = "plots"
    KPIS = "kpis"
    REPORTS = "reports"
    LOGS = "logs"
    CACHE = "cache"


class ResultPathProviderSingleton(metaclass=SingletonMeta):

    """ResultPathProviderSingleton class.

    According to your storing options and your input information a result path is created.
    It is the single source of truth for the run's root directory as well as for all artifact
    sub-paths below it.
    """

    def __init__(
        self,
    ):
        """Initialize the class."""
        # Default base path is the package directory (e.g. hisim-privat), the parent of the hisim/ package.
        self.base_path: Optional[Path] = Path(__file__).resolve().parent.parent / "results"
        self.run_mode: RunMode = RunMode.SINGLE
        self.test_name: Optional[str] = None
        self.model_name: Optional[str] = None
        self.variant_name: Optional[str] = None
        self.scenario_hash_string: Optional[str] = None
        self.further_result_folder_description: Optional[str] = None
        self.sorting_option: Any = SortingOptionEnum.FLAT
        self.time_resolution_in_seconds: Optional[int] = None
        self.simulation_duration_in_days: Optional[int] = None
        self.datetime_string: str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    @classmethod
    def reset(cls) -> None:
        """Drop the singleton instance so the next access starts fresh.

        Useful for unit tests to avoid global state leaking between test cases.
        """
        SingletonMeta._instances.pop(cls, None)  # pylint: disable=protected-access

    def configure(
        self,
        run_mode: RunMode,
        model_name: Optional[str] = None,
        variant_name: Optional[str] = None,
        scenario_hash_string: Optional[str] = None,
        sorting_option: Optional[SortingOptionEnum] = None,
        further_result_folder_description: Optional[str] = None,
        base_path: Optional[Union[str, Path]] = None,
        test_name: Optional[str] = None,
    ) -> None:
        """Configure the provider for a run.

        Each run mode only accepts the descriptor arguments that are meaningful for it; providing
        anything else (or omitting a required one) raises a ``ValueError``:

        - ``RunMode.SINGLE``: requires ``model_name`` and ``variant_name``; nothing else.
        - ``RunMode.MASS``:   requires ``model_name``; ``variant_name``, ``scenario_hash_string``,
          ``sorting_option`` and ``further_result_folder_description`` are optional (up to the user).
        - ``RunMode.TEST``:   requires ``test_name`` only.

        The ``run_mode`` picks the directory layout (overridable in ``MASS`` via ``sorting_option``).
        ``base_path`` is a root-location override allowed in any mode (e.g. for tests pointing at a
        temporary directory) and is not part of the per-mode descriptor validation.
        """
        self._validate_configure_arguments(
            run_mode=run_mode,
            provided_arguments={
                "model_name": model_name,
                "variant_name": variant_name,
                "scenario_hash_string": scenario_hash_string,
                "sorting_option": sorting_option,
                "further_result_folder_description": further_result_folder_description,
                "test_name": test_name,
            },
        )

        self.run_mode = run_mode
        if base_path is not None:
            self.base_path = Path(base_path)
        self.set_model_name(model_name=model_name)
        self.set_variant_name(variant_name=variant_name)
        self.set_scenario_hash_string(scenario_hash_string=scenario_hash_string)
        self.set_further_result_folder_description(
            further_result_folder_description=further_result_folder_description
        )
        if sorting_option is None:
            sorting_option = DEFAULT_SORTING_OPTION_FOR_MODE[run_mode]
        self.set_sorting_option(sorting_option=sorting_option)
        if run_mode == RunMode.TEST:
            self.test_name = sanitize_path_component(test_name)  # type: ignore[arg-type]

    @staticmethod
    def _validate_configure_arguments(run_mode: RunMode, provided_arguments: dict) -> None:
        """Ensure that exactly the arguments meaningful for ``run_mode`` are provided."""
        if run_mode not in REQUIRED_CONFIGURE_ARGS_FOR_MODE:
            raise ValueError(f"Unknown run mode {run_mode!r}.")

        required = set(REQUIRED_CONFIGURE_ARGS_FOR_MODE[run_mode])
        allowed = required | set(OPTIONAL_CONFIGURE_ARGS_FOR_MODE[run_mode])
        provided = {name for name, value in provided_arguments.items() if value is not None}

        missing = required - provided
        if missing:
            raise ValueError(
                f"Run mode {run_mode.name} requires {sorted(required)} to be set, "
                f"but {sorted(missing)} {'is' if len(missing) == 1 else 'are'} missing."
            )

        not_allowed = provided - allowed
        if not_allowed:
            raise ValueError(
                f"Run mode {run_mode.name} does not accept {sorted(not_allowed)}; "
                f"allowed arguments are {sorted(allowed)} (plus base_path)."
            )

    def set_important_result_path_information(
        self,
        module_directory: str,
        model_name: str,
        variant_name: Optional[str],
        scenario_hash_string: Optional[str],
        sorting_option: Any,
        further_result_folder_description: Optional[str] = None,
    ) -> None:
        """Set important result path information.

        Legacy entry point kept for backward compatibility. Prefer :meth:`configure`.
        """
        self.set_base_path(module_directory=module_directory)
        self.set_model_name(model_name=model_name)
        self.set_variant_name(variant_name=variant_name)
        self.set_sorting_option(sorting_option=sorting_option)
        self.set_scenario_hash_string(scenario_hash_string=scenario_hash_string)
        self.set_further_result_folder_description(further_result_folder_description=further_result_folder_description)

    def set_base_path(self, module_directory: Union[str, Path]) -> None:
        """Set base path."""
        self.base_path = Path(module_directory) / "results"

    def set_model_name(self, model_name: Optional[str]) -> None:
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

    def get_run_directory(self) -> Any:
        """Get the run's root directory (alias for :meth:`get_result_directory_name`)."""
        return self.get_result_directory_name()

    def get_result_directory_name(self) -> Optional[str]:
        """Get the result directory path."""

        if self.run_mode == RunMode.TEST:
            return self._get_test_directory_name()

        if (
            self.base_path is not None
            and self.model_name is not None
            and self.variant_name is not None
            and self.datetime_string is not None
            and self.scenario_hash_string is not None
        ):
            if self.sorting_option == SortingOptionEnum.DEEP:
                path = self.base_path / self.model_name / self.variant_name / self.datetime_string
            elif self.sorting_option == SortingOptionEnum.MASS_SIMULATION_WITH_INDEX_ENUMERATION:
                # schauen ob verzeichnis schon da und aufsteigende nummer anhängen
                idx = 1

                if self.further_result_folder_description is not None:
                    path = self.base_path / self.model_name / self.further_result_folder_description / (self.variant_name + "_" + str(idx))
                    while path.exists():
                        idx = idx + 1
                        path = self.base_path / self.model_name / self.further_result_folder_description / (self.variant_name + "_" + str(idx))
                else:
                    path = self.base_path / self.model_name / (self.variant_name + "_" + str(idx))
                    while path.exists():
                        idx = idx + 1
                        path = self.base_path / self.model_name / (self.variant_name + "_" + str(idx))
            elif self.sorting_option == SortingOptionEnum.MASS_SIMULATION_WITH_HASH_ENUMERATION:
                if self.further_result_folder_description is not None:
                    path = self.base_path / self.model_name / self.further_result_folder_description / (self.variant_name + "_" + self.scenario_hash_string)
                else:
                    path = self.base_path / self.model_name / (self.variant_name + "_" + self.scenario_hash_string)

            elif self.sorting_option == SortingOptionEnum.FLAT:
                path = self.base_path / (self.model_name + "_" + self.variant_name + self.datetime_string)

            check_path_length(path=str(path))
            return str(path)

        return None

    def _get_test_directory_name(self) -> Optional[str]:
        """Build the result directory for a test run, identified by the test name alone."""
        if self.base_path is None or self.test_name is None:
            return None
        path = self.base_path / "test" / self.test_name
        check_path_length(path=str(path))
        return str(path)

    def get_artifact_directory(self, category: ArtifactCategory) -> str:
        """Get (and create) the directory for a category of artifacts below the run directory."""
        run_directory = self.get_result_directory_name()
        if run_directory is None:
            raise ValueError(
                "Cannot build an artifact path because the result path provider is not configured yet. "
                "Call configure(...) or set_important_result_path_information(...) first."
            )
        artifact_directory = Path(run_directory) / category.value
        artifact_directory.mkdir(parents=True, exist_ok=True)
        check_path_length(path=str(artifact_directory))
        return str(artifact_directory)

    def get_artifact_path(self, category: ArtifactCategory, filename: str) -> str:
        """Get the full path of a single artifact file in the given category."""
        path = Path(self.get_artifact_directory(category)) / filename
        check_path_length(path=str(path))
        return str(path)


def detect_test_name() -> str:
    """Best-effort detection of the currently running test name from pytest's environment variable."""
    current_test = os.environ.get("PYTEST_CURRENT_TEST", "")
    if current_test:
        # Format is e.g. "tests/test_x.py::test_func (call)"; keep the test function/node name.
        node_id = current_test.split(" ", maxsplit=1)[0]
        return node_id.split("::")[-1]
    return "unknown_test"


def sanitize_path_component(name: str) -> str:
    """Replace characters that are problematic in file/directory names with underscores."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def check_path_length(path: str) -> None:
    """Make sure that path name does not get too long for Windows."""

    character_limit_according_to_windows = 256
    # check if the system is windows
    is_windows = sys.platform.startswith("win")
    if is_windows and len(path) >= character_limit_according_to_windows:
        raise NameError(
            f"The path {path} exceeds the limit of 256 characters which is the limit for Windows. Please make your path shorter."
        )
