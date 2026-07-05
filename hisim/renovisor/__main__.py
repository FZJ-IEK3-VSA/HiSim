"""Command-line interface of the RenoVisor translator (spec section 1).

Usage::

    python -m hisim.renovisor run <request.json> --variant {base|measures} [options]

Exit codes: 0 success, 2 request validation failed, 3 simulation failed, 4 upload failed.
"""

import argparse
import datetime
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import List, Optional, Tuple

from hisim import log
from hisim.renovisor import TRANSLATOR_VERSION, mapping, runner, uploader
from hisim.renovisor.schema import RequestValidationError, TranslatorInput, parse_translator_input

MAPPING_REPORT_FILENAME = "renovisor_mapping_report.json"

EXIT_SUCCESS = 0
EXIT_VALIDATION_FAILED = 2
EXIT_SIMULATION_FAILED = 3
EXIT_UPLOAD_FAILED = 4


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m hisim.renovisor",
        description="RenoVisor -> HiSim translator: map a home-inventory request onto a building-sizer "
        "system setup, run the simulation and submit result files via REST.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Translate, simulate and upload one request.")
    run_parser.add_argument("request_file", help="Path to the translator request JSON (wrapper envelope).")
    run_parser.add_argument(
        "--variant",
        required=True,
        choices=("base", "measures"),
        help="'base' simulates the inventory as-is; 'measures' applies all measures first.",
    )
    run_parser.add_argument(
        "--result-dir",
        default=None,
        help="Directory for HiSim results (never auto-deleted). Default: a temporary directory.",
    )
    run_parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Run everything but skip all REST submissions (local testing); implies keeping the files.",
    )
    run_parser.add_argument(
        "--keep-files",
        action="store_true",
        help="Do not delete the temporary result directory after a successful upload.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point; returns the process exit code."""
    arguments = build_argument_parser().parse_args(argv)
    return run_command(arguments)


def run_command(arguments: argparse.Namespace) -> int:
    """Execute the full pipeline for one request (spec overview diagram)."""
    try:
        translator_input = _load_and_validate(arguments.request_file)
        translation = mapping.translate(translator_input.request, arguments.variant, translator_input.job_id)
        result_directory, directory_is_temporary = _prepare_result_directory(arguments, translator_input.job_id)
        simulation_parameters = runner.build_simulation_parameters(
            translator_input.simulation_overrides, result_directory
        )
    except RequestValidationError as error:
        log.error(f"Request validation failed: {error}")
        return EXIT_VALIDATION_FAILED

    submission = translator_input.submission
    if not arguments.no_upload:
        _post_started_event(translator_input, translation, arguments.variant)

    try:
        actual_result_directory = runner.run_simulation(
            translation.setup_filename, translation.modular_household_config, simulation_parameters, result_directory
        )
        report_path = _write_mapping_report(translation, translator_input, arguments.variant, actual_result_directory)
        upload_files = _collect_upload_files(actual_result_directory, submission.files, report_path)
    except Exception as error:  # noqa: broad-except  # any simulation failure must become a failure report
        _handle_pipeline_failure(translator_input, arguments, error)
        return EXIT_SIMULATION_FAILED

    if arguments.no_upload:
        log.information(
            f"Upload skipped (--no-upload). {len(upload_files)} file(s) ready in {actual_result_directory}."
        )
        return EXIT_SUCCESS

    try:
        uploader.post_success(
            submission.url,
            submission.auth_token,
            form_fields={
                "jobId": translator_input.job_id,
                "variant": arguments.variant,
                "status": "succeeded",
                "translatorVersion": TRANSLATOR_VERSION,
            },
            files=upload_files,
        )
    except uploader.UploadError as error:
        log.error(f"Result upload failed: {error}. Result files kept in {actual_result_directory}.")
        return EXIT_UPLOAD_FAILED

    log.information(f"Uploaded {len(upload_files)} file(s) for job '{translator_input.job_id}'.")
    if directory_is_temporary and not arguments.keep_files:
        shutil.rmtree(result_directory, ignore_errors=True)
    return EXIT_SUCCESS


def _load_and_validate(request_file: str) -> TranslatorInput:
    """Load the request JSON and validate the envelope."""
    request_path = Path(request_file)
    if not request_path.is_file():
        raise RequestValidationError(f"Request file not found: {request_path}")
    try:
        data = json.loads(request_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise RequestValidationError(f"Request file is not valid JSON: {error}") from error
    return parse_translator_input(data)


def _prepare_result_directory(arguments: argparse.Namespace, job_id: str) -> Tuple[Path, bool]:
    """Return the result directory and whether the translator owns (may delete) it."""
    if arguments.result_dir is not None:
        directory = Path(arguments.result_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return directory, False
    return Path(tempfile.mkdtemp(prefix=f"renovisor_{job_id}_")), True


def _post_started_event(
    translator_input: TranslatorInput, translation: mapping.TranslationResult, variant: str
) -> None:
    """Send the non-fatal ``started`` lifecycle event (spec section 7)."""
    payload = {
        "jobId": translator_input.job_id,
        "variant": variant,
        "status": "started",
        "translatorVersion": TRANSLATOR_VERSION,
        "selectedSetup": translation.setup_filename,
        "startedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    delivered = uploader.post_started(translator_input.submission.url, translator_input.submission.auth_token, payload)
    if not delivered:
        log.warning("Could not deliver the 'started' event; continuing with the simulation.")


def _write_mapping_report(
    translation: mapping.TranslationResult,
    translator_input: TranslatorInput,
    variant: str,
    result_directory: Path,
) -> Path:
    """Write ``renovisor_mapping_report.json`` into the result directory (spec section 6)."""
    report_dict = mapping.build_mapping_report_dict(translation, translator_input.job_id, variant)
    report_path = result_directory / MAPPING_REPORT_FILENAME
    report_path.write_text(json.dumps(report_dict, indent=2), encoding="utf-8")
    return report_path


def _collect_upload_files(
    result_directory: Path, patterns: List[str], report_path: Path
) -> List[Tuple[str, Path]]:
    """Match the requested files and make sure the mapping report is always included."""
    upload_files = uploader.match_result_files(result_directory, patterns)
    report_relative_path = report_path.relative_to(result_directory).as_posix()
    if all(relative_path != report_relative_path for relative_path, _ in upload_files):
        upload_files.append((report_relative_path, report_path))
    return upload_files


def _handle_pipeline_failure(
    translator_input: TranslatorInput, arguments: argparse.Namespace, error: Exception
) -> None:
    """Log a simulation/collection failure and POST the failure report (spec section 7)."""
    log_tail = "\n".join(traceback.format_exc().splitlines()[-100:])
    log.error(f"Simulation failed for job '{translator_input.job_id}': {error}")
    if arguments.no_upload:
        return
    payload = {
        "jobId": translator_input.job_id,
        "variant": arguments.variant,
        "status": "failed",
        "stage": "simulation",
        "errorMessage": str(error),
        "logTail": log_tail,
    }
    try:
        uploader.post_failure(translator_input.submission.url, translator_input.submission.auth_token, payload)
    except uploader.UploadError as upload_error:
        log.error(f"Could not deliver the failure report: {upload_error}")


if __name__ == "__main__":
    sys.exit(main())
