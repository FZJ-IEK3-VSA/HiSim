"""Parsing and validation of the translator input (wrapper envelope + RenoVisor request).

The RenoVisor payload uses camelCase keys and demands that unknown keys be ignored, so the
request part is kept as a plain dict; only the fields the translator relies on are validated
here. Validation failures raise :class:`RequestValidationError`, which the CLI maps to exit
code 2 (spec section 2).
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional


class RequestValidationError(Exception):
    """Raised when the translator input fails validation (CLI exit code 2)."""


KNOWN_HEATING_PRIMARIES = {
    "gas",
    "oil",
    "solid_fuel",
    "heat_pump",
    "wood",
    "direct_electric",
    "district",
    "irish_cooking_range",
}

KNOWN_DWELLING_TYPES = {
    "detached_sfh",
    "semi_detached_sfh",
    "terraced_sfh",
    "bungalow",
    "apartment",
    "other",
}


@dataclass
class SubmissionConfig:
    """Where and what to upload (spec section 2, ``job.submission``)."""

    url: str
    files: List[str]
    auth_token: Optional[str] = None


@dataclass
class SimulationOverrides:
    """Optional overrides of the simulation-parameter defaults (spec section 5)."""

    year: Optional[int] = None
    seconds_per_timestep: Optional[int] = None
    post_processing_options: List[str] = field(default_factory=list)


@dataclass
class TranslatorInput:
    """Validated translator input: job envelope plus the raw RenoVisor request dict."""

    job_id: str
    submission: SubmissionConfig
    simulation_overrides: SimulationOverrides
    request: dict


def _require(mapping: dict, key: str, path: str) -> Any:
    """Return ``mapping[key]`` or raise a :class:`RequestValidationError` naming the JSON path."""
    if not isinstance(mapping, dict) or key not in mapping:
        raise RequestValidationError(f"Missing required field '{path}'.")
    return mapping[key]


def _require_type(value: Any, expected: type, path: str) -> Any:
    """Check that *value* has the *expected* type, raising with the JSON *path* otherwise."""
    if not isinstance(value, expected):
        raise RequestValidationError(f"Field '{path}' must be of type {expected.__name__}, got {type(value).__name__}.")
    return value


def parse_translator_input(data: dict) -> TranslatorInput:
    """Validate the wrapper envelope and the RenoVisor request fields the translator relies on.

    Unknown keys anywhere in the input are ignored (forward compatibility). Only structural
    problems that make the request impossible to translate raise
    :class:`RequestValidationError`.
    """
    _require_type(data, dict, "<root>")
    job = _require_type(_require(data, "job", "job"), dict, "job")
    job_id = _require_type(_require(job, "jobId", "job.jobId"), str, "job.jobId")
    if not job_id.strip():
        raise RequestValidationError("Field 'job.jobId' must not be empty.")

    submission_dict = _require_type(_require(job, "submission", "job.submission"), dict, "job.submission")
    url = _require_type(_require(submission_dict, "url", "job.submission.url"), str, "job.submission.url")
    files = _require_type(_require(submission_dict, "files", "job.submission.files"), list, "job.submission.files")
    for index, pattern in enumerate(files):
        _require_type(pattern, str, f"job.submission.files[{index}]")
    auth_token = submission_dict.get("authToken")
    if auth_token is not None:
        _require_type(auth_token, str, "job.submission.authToken")
    submission = SubmissionConfig(url=url, files=list(files), auth_token=auth_token)

    overrides = _parse_simulation_overrides(job.get("simulationOverrides"))

    request = _require_type(_require(data, "request", "request"), dict, "request")
    _validate_request(request)

    return TranslatorInput(job_id=job_id, submission=submission, simulation_overrides=overrides, request=request)


def _parse_simulation_overrides(raw: Any) -> SimulationOverrides:
    """Parse the optional ``job.simulationOverrides`` block."""
    if raw is None:
        return SimulationOverrides()
    _require_type(raw, dict, "job.simulationOverrides")
    year = raw.get("year")
    if year is not None:
        _require_type(year, int, "job.simulationOverrides.year")
    seconds = raw.get("secondsPerTimestep")
    if seconds is not None:
        _require_type(seconds, int, "job.simulationOverrides.secondsPerTimestep")
        if seconds <= 0:
            raise RequestValidationError("Field 'job.simulationOverrides.secondsPerTimestep' must be positive.")
    options = raw.get("postProcessingOptions", [])
    _require_type(options, list, "job.simulationOverrides.postProcessingOptions")
    option_names: List[str] = []
    for index, name in enumerate(options):
        _require_type(name, str, f"job.simulationOverrides.postProcessingOptions[{index}]")
        option_names.append(name)
    _validate_post_processing_option_names(option_names)
    return SimulationOverrides(year=year, seconds_per_timestep=seconds, post_processing_options=option_names)


def _validate_post_processing_option_names(names: List[str]) -> None:
    """Check that every override name is a valid :class:`PostProcessingOptions` member."""
    # local import to keep parsing lightweight
    from hisim.postprocessingoptions import PostProcessingOptions  # pylint: disable=import-outside-toplevel

    for name in names:
        if not hasattr(PostProcessingOptions, name):
            raise RequestValidationError(f"Unknown post-processing option '{name}' in job.simulationOverrides.")


def _validate_request(request: dict) -> None:
    """Validate the RenoVisor request fields the mapping depends on (spec section 2)."""
    contract_version = _require_type(
        _require(request, "contractVersion", "request.contractVersion"), str, "request.contractVersion"
    )
    if not contract_version.startswith("1."):
        raise RequestValidationError(
            f"Unsupported contractVersion '{contract_version}': this translator supports contract 1.x."
        )

    location = _require_type(_require(request, "location", "request.location"), dict, "request.location")
    _require_type(_require(location, "countryCode", "request.location.countryCode"), str, "request.location.countryCode")

    home = _require_type(_require(request, "homeInputs", "request.homeInputs"), dict, "request.homeInputs")
    dwelling_type = _require_type(
        _require(home, "dwellingType", "request.homeInputs.dwellingType"), str, "request.homeInputs.dwellingType"
    )
    if dwelling_type not in KNOWN_DWELLING_TYPES:
        raise RequestValidationError(
            f"Unknown dwellingType '{dwelling_type}'. Allowed: {sorted(KNOWN_DWELLING_TYPES)}."
        )
    construction_year = _require(home, "constructionYear", "request.homeInputs.constructionYear")
    _require_type(construction_year, int, "request.homeInputs.constructionYear")
    if not 1700 <= construction_year <= 2100:
        raise RequestValidationError("Field 'request.homeInputs.constructionYear' must be between 1700 and 2100.")
    floor_area = _require(home, "floorAreaM2", "request.homeInputs.floorAreaM2")
    if not isinstance(floor_area, (int, float)) or floor_area <= 0:
        raise RequestValidationError("Field 'request.homeInputs.floorAreaM2' must be a positive number.")
    occupants = _require(home, "occupants", "request.homeInputs.occupants")
    if not isinstance(occupants, int) or occupants <= 0:
        raise RequestValidationError("Field 'request.homeInputs.occupants' must be a positive integer.")

    heating = _require_type(_require(home, "heating", "request.homeInputs.heating"), dict, "request.homeInputs.heating")
    primary = _require_type(
        _require(heating, "primary", "request.homeInputs.heating.primary"), str, "request.homeInputs.heating.primary"
    )
    if primary not in KNOWN_HEATING_PRIMARIES:
        raise RequestValidationError(
            f"Unknown heating.primary '{primary}'. Allowed: {sorted(KNOWN_HEATING_PRIMARIES)}."
        )

    measures = _require_type(_require(request, "measures", "request.measures"), list, "request.measures")
    for index, measure in enumerate(measures):
        _require_type(measure, dict, f"request.measures[{index}]")
