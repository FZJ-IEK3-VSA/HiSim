"""Shared machinery for the committed building golden snapshots (cleanup phase 1 harness).

The building cleanup (see ``system_docs/building_cleanup_spec.md``) is defined to be
behavior-identical, and its referee is a pair of committed golden files: one
characterization snapshot of :py:class:`hisim.components.building.BuildingInformation`
over the whole TABULA catalogue, and one snapshot of a synthetic one-day ``Building``
simulation. Both layers need exactly the same three services -- an exact-float JSON
encoding, a deterministic text layout for the golden file, and a single documented way
to opt into rewriting it -- so those services live here instead of being duplicated in
two test modules.

Two properties of the encoding matter for the harness to be a usable referee. Floats are
written through ``json.dumps``, i.e. through ``repr``, which round-trips a binary64 value
exactly; comparison is therefore exact equality and not an approximate match, because a
cleanup that shifts the last mantissa bit has changed the physics and must be reviewed.
Non-finite floats are replaced by string sentinels, since ``float('nan') != float('nan')``
would make a NaN-carrying snapshot permanently red and silently unverifiable.

The goldens are rewritten only when the environment variable
``HISIM_REGENERATE_BUILDING_GOLDENS`` is set to a truthy value, which the phase spec
requires to be a deliberate act accompanied by a justification in the commit message.
Without it a missing golden raises :py:class:`MissingGoldenFileError` instead of being
created on the fly, so an absent referee can never be mistaken for a passing test.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union


class MissingGoldenFileError(FileNotFoundError):
    """Raised when a golden file the harness compares against does not exist.

    Creating the file implicitly would turn "there is no reference" into a green test,
    which is precisely the failure mode the golden harness exists to prevent. The error
    message therefore names the missing path and the environment variable that rewrites
    it, so the developer regenerates deliberately rather than by accident.
    """


class GoldenPolicy:
    """Locations, naming and the regeneration switch for the building golden files.

    Everything the harness needs to know about *where* goldens live and *when* they may
    be rewritten is collected in this one class, so neither test module has to hardcode a
    path or re-invent the environment-variable spelling. The values are class attributes
    rather than module-level constants, following the repository convention against
    module-level state.
    """

    #: Environment variable that opts into rewriting the goldens.
    REGENERATION_ENVIRONMENT_VARIABLE: str = "HISIM_REGENERATE_BUILDING_GOLDENS"
    #: Directory (relative to this file) holding the committed goldens.
    GOLDEN_DIRECTORY_NAME: str = "goldens"
    #: Values of the regeneration variable that count as "yes".
    TRUTHY_VALUES: Tuple[str, ...] = ("1", "true", "yes", "on")
    #: Prefix marking a snapshot entry that records an exception instead of values.
    RAISES_PREFIX: str = "raises: "
    #: How many characters of an exception message are pinned in a snapshot.
    ERROR_MESSAGE_CHARACTER_LIMIT: int = 80
    #: Sentinels used for the three non-finite float values.
    NOT_A_NUMBER_SENTINEL: str = "nan"
    POSITIVE_INFINITY_SENTINEL: str = "inf"
    NEGATIVE_INFINITY_SENTINEL: str = "-inf"

    @classmethod
    def golden_directory(cls) -> Path:
        """Return the absolute path of the directory holding the committed goldens.

        The directory is resolved relative to this support module rather than to the
        current working directory, because the suite is run both from the repository root
        and from ``tests/`` (the CI workflow does the latter).
        """
        return Path(__file__).resolve().parent / cls.GOLDEN_DIRECTORY_NAME

    @classmethod
    def golden_path(cls, file_name: str) -> Path:
        """Return the absolute path of one golden file inside the goldens directory.

        Takes the bare file name (e.g. ``building_information.json``) so that callers
        never spell out a directory, keeping every golden in one committed place.
        """
        return cls.golden_directory() / file_name

    @classmethod
    def regeneration_requested(cls) -> bool:
        """Whether the environment asks for the goldens to be rewritten.

        Reads :py:attr:`REGENERATION_ENVIRONMENT_VARIABLE` and accepts the usual truthy
        spellings case-insensitively. Any other value -- including the variable being
        unset or empty -- means "compare only", which is the state every CI run and every
        ordinary local run must be in.
        """
        raw_value = os.environ.get(cls.REGENERATION_ENVIRONMENT_VARIABLE, "")
        return raw_value.strip().lower() in cls.TRUTHY_VALUES

    @classmethod
    def describe_exception(cls, error: BaseException) -> str:
        """Render an exception as the pinned snapshot string for a crashing input.

        A TABULA building code that currently makes the production code raise is recorded
        behavior, not a test failure: it is pinned as ``"raises: <type>: <message>"`` with
        the message truncated to :py:attr:`ERROR_MESSAGE_CHARACTER_LIMIT` characters so
        that incidental formatting differences (paths, repr of values) do not make the
        golden brittle. If a later cleanup phase accidentally *fixes* such a crash, the
        golden goes red and the fix is moved into its own reviewed commit.
        """
        message = str(error)[: cls.ERROR_MESSAGE_CHARACTER_LIMIT]
        return f"{cls.RAISES_PREFIX}{type(error).__name__}: {message}"


def encode_float(value: float) -> Union[float, str]:
    """Encode one float for a golden file, keeping full binary64 fidelity.

    Finite values are passed through unchanged (``json.dumps`` writes them via ``repr``,
    which round-trips exactly, so no rounding is introduced anywhere in the harness).
    The three non-finite values are replaced by the string sentinels from
    :py:class:`GoldenPolicy`, because NaN never compares equal to itself and would make
    any snapshot containing it impossible to verify.
    """
    plain_value = float(value)
    if math.isnan(plain_value):
        return GoldenPolicy.NOT_A_NUMBER_SENTINEL
    if math.isinf(plain_value):
        return (
            GoldenPolicy.POSITIVE_INFINITY_SENTINEL
            if plain_value > 0.0
            else GoldenPolicy.NEGATIVE_INFINITY_SENTINEL
        )
    return plain_value


def encode_value(value: Any) -> Any:
    """Encode one snapshot value (scalar, string, sequence or mapping) for a golden file.

    Handles the value kinds the building classes actually expose: bools, ints, floats,
    strings, lists of those, dictionaries keyed by strings, and numpy scalars (which the
    pandas-backed TABULA lookups produce and which are unwrapped via ``item()``).
    Anything else raises :py:class:`TypeError` on purpose: a new attribute of an
    unforeseen type must be a deliberate decision by whoever adds it, not something the
    harness silently stringifies and stops guarding.
    """
    if isinstance(value, float):
        return encode_float(value)
    if isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [encode_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): encode_value(item) for key, item in sorted(value.items())}
    if hasattr(value, "item"):  # numpy scalar types (np.int64, np.bool_, ...)
        return encode_value(value.item())
    raise TypeError(f"The golden harness has no encoding for a value of type '{type(value).__name__}'.")


def format_golden_text(payload: Mapping[str, Any]) -> str:
    """Render a golden payload as deterministic JSON text with one entry per line.

    The layout is chosen for reviewability of the committed file: top-level sections and
    their entries each get their own line, while the entry *values* stay compact on that
    line. A changed building code or output vector therefore shows up as a single changed
    line in ``git diff`` instead of as a reflowed block, and the file stays a few
    megabytes rather than tens of megabytes.

    Args:
        payload: mapping of section name to either a plain JSON-encodable value or a
            mapping of entry key to JSON-encodable value.

    Returns:
        The complete file text, ending in a newline, parseable by ``json.loads``.
    """
    lines: List[str] = ["{"]
    section_names = sorted(payload)
    for section_position, section_name in enumerate(section_names):
        section_separator = "," if section_position < len(section_names) - 1 else ""
        section_value = payload[section_name]
        if not isinstance(section_value, dict):
            lines.append(f" {json.dumps(section_name)}: {_compact_json(section_value)}{section_separator}")
            continue
        lines.append(f" {json.dumps(section_name)}: {{")
        entry_keys = sorted(section_value)
        for entry_position, entry_key in enumerate(entry_keys):
            entry_separator = "," if entry_position < len(entry_keys) - 1 else ""
            lines.append(f"  {json.dumps(entry_key)}: {_compact_json(section_value[entry_key])}{entry_separator}")
        lines.append(f" }}{section_separator}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _compact_json(value: Any) -> str:
    """Dump one value as compact single-line JSON, refusing non-finite floats.

    ``allow_nan=False`` is a deliberate backstop: every non-finite float must already have
    been turned into a string sentinel by :py:func:`encode_float`, so a raw NaN reaching
    the writer is a bug in the snapshot code and should fail loudly rather than produce a
    file that is not valid JSON.
    """
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def write_golden(file_name: str, payload: Mapping[str, Any]) -> Path:
    """Write one golden file, creating the goldens directory if necessary.

    Called only from the regeneration path guarded by
    :py:meth:`GoldenPolicy.regeneration_requested`, so an ordinary test run never touches
    the working tree. The written text is fully deterministic (sorted sections and
    entries), which is what makes the file's diff a reviewable part of a merge request.
    """
    golden_path = GoldenPolicy.golden_path(file_name)
    golden_path.parent.mkdir(parents=True, exist_ok=True)
    golden_path.write_text(format_golden_text(payload), encoding="utf-8")
    return golden_path


def load_golden(file_name: str) -> Dict[str, Any]:
    """Load one committed golden file, or explain how to create it.

    Args:
        file_name: bare name of the golden inside the goldens directory.

    Returns:
        The parsed payload.

    Raises:
        MissingGoldenFileError: if the file does not exist. Regenerating is deliberate by
            design, so the harness refuses to silently create its own reference.
    """
    golden_path = GoldenPolicy.golden_path(file_name)
    if not golden_path.is_file():
        raise MissingGoldenFileError(
            f"The golden file '{golden_path}' does not exist. It is a committed reference and is "
            f"never created implicitly. To create it deliberately, re-run this test module with "
            f"{GoldenPolicy.REGENERATION_ENVIRONMENT_VARIABLE}=1 and commit the resulting file "
            f"together with a justification for the change."
        )
    with golden_path.open(encoding="utf-8") as golden_file:
        loaded_payload: Dict[str, Any] = json.load(golden_file)
    return loaded_payload


def describe_entry_differences(
    label: str,
    expected_entry: Union[str, Mapping[str, Any]],
    actual_entry: Union[str, Mapping[str, Any]],
) -> Optional[str]:
    """Compare one snapshot entry against its golden and describe any difference.

    An entry is either a mapping of attribute name to encoded value or a pinned
    ``"raises: ..."`` string, and the two sides may disagree about which of the two they
    are -- that is exactly what happens when a cleanup accidentally fixes or introduces a
    crash, so it gets its own message. For mapping entries every attribute is compared
    with exact equality and *all* mismatching attributes are reported, because a physics
    change usually moves a whole group of derived values and seeing the group is what
    identifies the cause.

    Args:
        label: human-readable identity of the entry (e.g. the TABULA building code).
        expected_entry: the entry as stored in the golden.
        actual_entry: the entry produced by the current code.

    Returns:
        ``None`` when the entry matches the golden exactly, otherwise a multi-line
        description naming the entry and every differing attribute.
    """
    if isinstance(expected_entry, str) or isinstance(actual_entry, str):
        if expected_entry == actual_entry:
            return None
        return (
            f"{label}: snapshot kind or pinned exception changed.\n"
            f"  golden:  {expected_entry!r}\n"
            f"  current: {actual_entry!r}"
        )

    difference_lines: List[str] = []
    for attribute_name in sorted(set(expected_entry) | set(actual_entry)):
        if attribute_name not in expected_entry:
            difference_lines.append(f"  {attribute_name}: absent in golden, current = {actual_entry[attribute_name]!r}")
        elif attribute_name not in actual_entry:
            difference_lines.append(
                f"  {attribute_name}: golden = {expected_entry[attribute_name]!r}, absent in current"
            )
        elif expected_entry[attribute_name] != actual_entry[attribute_name]:
            difference_lines.append(
                f"  {attribute_name}: golden = {expected_entry[attribute_name]!r}, "
                f"current = {actual_entry[attribute_name]!r}"
            )
    if not difference_lines:
        return None
    return f"{label}: {len(difference_lines)} attribute(s) differ from the golden.\n" + "\n".join(difference_lines)
