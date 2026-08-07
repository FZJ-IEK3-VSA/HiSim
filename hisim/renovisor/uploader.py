"""REST submission of lifecycle events and result files (spec section 7).

All POSTs go to the single submission URL from the request envelope. The terminal submissions
(``post_failure`` and ``post_success``) and the result-file upload retry network errors and 5xx
responses with exponential backoff and fail immediately on 4xx contract errors. The
``post_started`` lifecycle event is non-fatal and exempt from this policy: it makes a single
immediate re-attempt with no backoff and returns ``False`` on any failure instead of raising.
``post_fn``/``sleep_fn`` are injectable for tests.
"""

import fnmatch
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

REQUEST_TIMEOUT_IN_SECONDS: float = 60.0
RETRY_DELAYS_IN_SECONDS: Tuple[float, ...] = (5.0, 25.0, 125.0)


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

    Args:
        result_directory: Root directory to search recursively.
        patterns: fnmatch patterns tested against both the relative POSIX path
            and the bare filename of each file.

    Returns:
        A sorted list of ``(relative POSIX path, absolute path)`` tuples for
        files matching at least one pattern.
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
    build_kwargs: Callable[[], Dict[str, Any]],
    post_fn: Callable[..., requests.Response] = requests.post,
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
    payload: Dict[str, Any],
    post_fn: Callable[..., requests.Response] = requests.post,
) -> bool:
    """POST the ``started`` lifecycle event; non-fatal, one immediate re-attempt (spec section 7).

    Args:
        url: Submission URL.
        auth_token: Optional bearer token sent in the ``Authorization`` header.
        payload: JSON body for the ``started`` event.
        post_fn: POST callable (injectable for tests; defaults to ``requests.post``).

    Returns:
        True if the server responded 2xx; False if both attempts failed with a
        network error or non-2xx status.
    """
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
    payload: Dict[str, Any],
    post_fn: Callable[..., requests.Response] = requests.post,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """POST the ``failed`` terminal event with the standard retry policy.

    Args:
        url: Submission URL.
        auth_token: Optional bearer token sent in the ``Authorization`` header.
        payload: JSON body for the ``failed`` event.
        post_fn: POST callable (injectable for tests; defaults to ``requests.post``).
        sleep_fn: Sleep callable for backoff between retries (injectable for tests).

    Raises:
        UploadError: If the server returns a 4xx response, or if all retry
            attempts are exhausted (network errors or 5xx responses).
    """
    _post_with_retries(url, auth_token, lambda: {"json": payload}, post_fn=post_fn, sleep_fn=sleep_fn)


def post_success(
    url: str,
    auth_token: Optional[str],
    form_fields: Dict[str, str],
    files: List[Tuple[str, Path]],
    post_fn: Callable[..., requests.Response] = requests.post,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """POST the result files as one multipart/form-data request with the retry policy.

    Each file becomes a ``files`` part whose filename is the path relative to the result
    directory; *form_fields* carry ``jobId``, ``variant``, ``status`` and ``translatorVersion``.

    Args:
        url: Submission URL.
        auth_token: Optional bearer token sent in the ``Authorization`` header.
        form_fields: Multipart form fields (e.g. ``jobId``, ``variant``, ``status``,
            ``translatorVersion``).
        files: List of ``(relative path, absolute path)`` tuples to upload as
            file parts.
        post_fn: POST callable (injectable for tests; defaults to ``requests.post``).
        sleep_fn: Sleep callable for backoff between retries (injectable for tests).

    Raises:
        UploadError: If the server returns a 4xx response, or if all retry
            attempts are exhausted (network errors or 5xx responses).
    """

    def build_kwargs() -> Dict[str, Any]:
        multipart = [
            ("files", (relative_path, file_path.read_bytes(), "application/octet-stream"))
            for relative_path, file_path in files
        ]
        return {"data": dict(form_fields), "files": multipart}

    _post_with_retries(url, auth_token, build_kwargs, post_fn=post_fn, sleep_fn=sleep_fn)
