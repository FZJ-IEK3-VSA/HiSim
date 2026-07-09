"""REST submission of lifecycle events and result files (spec section 7).

All POSTs go to the single submission URL from the request envelope. Network errors and 5xx
responses are retried with exponential backoff; 4xx responses are contract errors and fail
immediately. ``post_fn``/``sleep_fn`` are injectable for tests.
"""

import fnmatch
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

REQUEST_TIMEOUT_IN_SECONDS = 60.0
RETRY_DELAYS_IN_SECONDS = (5.0, 25.0, 125.0)


class UploadError(Exception):
    """Raised when a POST to the submission URL ultimately fails (CLI exit code 4)."""


def _headers(auth_token: Optional[str]) -> Dict[str, str]:
    """Build the request headers, including bearer auth when a token is configured."""
    if auth_token:
        return {"Authorization": f"Bearer {auth_token}"}
    return {}


def match_result_files(result_directory: Path, patterns: List[str]) -> List[Tuple[str, Path]]:
    """Return ``(relative posix path, absolute path)`` of result files matching any pattern.

    Patterns are matched recursively against both the path relative to the result directory
    and the bare filename (spec section 2, ``job.submission.files``).
    """
    matches: List[Tuple[str, Path]] = []
    for file_path in result_directory.rglob("*"):
        if not file_path.is_file():
            continue
        relative_path = file_path.relative_to(result_directory).as_posix()
        if any(fnmatch.fnmatch(relative_path, pattern) or fnmatch.fnmatch(file_path.name, pattern) for pattern in patterns):
            matches.append((relative_path, file_path))
    return sorted(matches)


def _post_with_retries(
    url: str,
    auth_token: Optional[str],
    build_kwargs: Callable[[], dict],
    post_fn: Callable = requests.post,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """POST with the spec's retry policy; *build_kwargs* is re-evaluated per attempt.

    Rebuilding the keyword arguments each attempt matters for multipart uploads, whose file
    payloads cannot be re-sent once consumed.
    """
    last_error: Optional[str] = None
    for attempt_index in range(len(RETRY_DELAYS_IN_SECONDS) + 1):
        if attempt_index > 0:
            sleep_fn(RETRY_DELAYS_IN_SECONDS[attempt_index - 1])
        try:
            response = post_fn(url, headers=_headers(auth_token), timeout=REQUEST_TIMEOUT_IN_SECONDS, **build_kwargs())
        except requests.RequestException as error:
            last_error = f"network error: {error}"
            continue
        if 200 <= response.status_code < 300:
            return
        if response.status_code >= 500:
            last_error = f"server error: HTTP {response.status_code}"
            continue
        raise UploadError(f"Submission to {url} rejected with HTTP {response.status_code} (not retried).")
    raise UploadError(f"Submission to {url} failed after {len(RETRY_DELAYS_IN_SECONDS) + 1} attempts; last: {last_error}.")


def post_started(
    url: str,
    auth_token: Optional[str],
    payload: dict,
    post_fn: Callable = requests.post,
) -> bool:
    """POST the ``started`` lifecycle event; non-fatal, one immediate re-attempt (spec section 7)."""
    for _ in range(2):
        try:
            response = post_fn(url, headers=_headers(auth_token), json=payload, timeout=REQUEST_TIMEOUT_IN_SECONDS)
            if 200 <= response.status_code < 300:
                return True
        except requests.RequestException:
            pass
    return False


def post_failure(
    url: str,
    auth_token: Optional[str],
    payload: dict,
    post_fn: Callable = requests.post,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """POST the ``failed`` terminal event with the standard retry policy."""
    _post_with_retries(url, auth_token, lambda: {"json": payload}, post_fn=post_fn, sleep_fn=sleep_fn)


def post_success(
    url: str,
    auth_token: Optional[str],
    form_fields: Dict[str, str],
    files: List[Tuple[str, Path]],
    post_fn: Callable = requests.post,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """POST the result files as one multipart/form-data request with the retry policy.

    Each file becomes a ``files`` part whose filename is the path relative to the result
    directory; *form_fields* carry ``jobId``, ``variant``, ``status`` and ``translatorVersion``.
    """

    def build_kwargs() -> dict:
        multipart = [
            ("files", (relative_path, file_path.read_bytes(), "application/octet-stream"))
            for relative_path, file_path in files
        ]
        return {"data": dict(form_fields), "files": multipart}

    _post_with_retries(url, auth_token, build_kwargs, post_fn=post_fn, sleep_fn=sleep_fn)
