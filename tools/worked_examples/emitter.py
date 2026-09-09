"""§3.4: renders one parsed example as the deterministic YAML text that ships beside its workbook.

Every byte of a generated fixture is decided here, and that is why the small formatting helpers sit
in one module together with `emit_yaml` instead of next to the parser: CI regenerates all 25
fixtures and fails on a single byte of difference (§3.6), and the content fingerprint hashes the
same text (§3.8), so a change to how one number is rendered is a change to 25 committed files.
Keeping that decision in one file is what makes such a change reviewable.

Nothing in this module reads a workbook or writes a file — it turns an `Example` into a string.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Union

import yaml

from tools.worked_examples.attestation import FingerprintFormat, fingerprint_of
from tools.worked_examples.model import Example, ValidationError


class YamlText:
    """The fixed text and the rendering bounds of a generated fixture.

    Class-scoped because these are the emitter's format constants, not project-wide vocabulary:
    the header is the two comment lines every fixture opens with, and the three limits below are
    the boundaries at which "render this number as plain decimal" stops being possible.
    """

    GENERATED_HEADER = (
        "# GENERATED from {source} by tools/convert_worked_examples.py.\n"
        "# Edit the workbook and re-run the converter; never edit this file by hand (§3.6).\n"
    )
    #: Ceiling on a value's decimals: well past double precision for the magnitudes examples use.
    MAX_VALUE_DECIMALS = 12
    #: Above this magnitude an integral float is no longer exactly representable, so `int()` lies.
    INTEGRAL_RENDER_LIMIT = 1e15
    #: Decimals used to expand an input whose `repr` came out in exponent notation.
    INPUT_EXPANSION_DECIMALS = 15


def _require_usable_tolerance(tolerance: float) -> None:
    """Rejects a tolerance the decimal renderers cannot honestly represent.

    Both `math.ceil(-math.log10(t))` and the decimal expansion of `t` are meaningless for a
    non-positive, NaN or infinite tolerance, and a bare `tolerance <= 0` guard does not catch the
    last two: `nan <= 0` and `inf <= 0` are both False, so NaN reached `math.ceil` as a `ValueError`
    and infinity as an `OverflowError` — neither a `ValidationError`, so neither reported against
    the workbook that caused it.

    Raises:
        ValidationError: If the tolerance is not a finite positive number.
    """
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValidationError(f"abs_tol must be a finite positive number, got {tolerance!r}.")


def decimals_for_tolerance(tolerance: float) -> int:
    """Number of decimals a *value* is rounded to, derived from its declared tolerance (§3.3).

    An `abs_tol` of 0.01 means the example asserts cents, so printing more than two decimals in the
    YAML would suggest a precision the author never claimed — and would make the drift check
    sensitive to the last bits of a float. Capped at `YamlText.MAX_VALUE_DECIMALS`, which is well
    past double precision for the magnitudes worked examples use.

    This governs the value column only. The tolerance itself is rendered at full precision by
    `format_tolerance`, because rounding a tolerance changes what the example asserts.

    Raises:
        ValidationError: If the tolerance is not a finite positive number (guards a blank or
            zeroed column C).
    """
    _require_usable_tolerance(tolerance)
    return min(YamlText.MAX_VALUE_DECIMALS, max(0, int(math.ceil(-math.log10(tolerance)))))


def format_value(value: float, tolerance: float) -> str:
    """Formats an expected value with the decimals its tolerance implies.

    Together with the other `format_*` helpers this is what makes the generated YAML a *stable*
    text: the same workbook must always render byte-for-byte identically, otherwise the CI drift
    check (§3.6) and the content fingerprint (§3.8) would fire on rounding noise instead of on real
    edits. The negative-zero guard exists for the same reason — a value that rounds to zero must
    render the same regardless of which side it approached from.
    """
    decimals = decimals_for_tolerance(tolerance)
    text = f"{round(value, decimals):.{decimals}f}"
    if text.startswith("-") and float(text) == 0.0:
        text = text[1:]  # never emit "-0.00"
    return text


def format_tolerance(tolerance: float) -> str:
    """Formats a tolerance in plain decimal notation, at full precision (§3.4).

    Two properties have to hold at once, and neither is negotiable.

    *Plain decimal.* A tolerance written as `1e-06` is legal YAML 1.2 but is parsed as a **string**
    by YAML 1.1 loaders, which would silently turn the collector's numeric comparison into a type
    error. So no exponent ever reaches the file, however small the tolerance — and at least one
    decimal is emitted so the value also reads unambiguously as a float rather than as an integer.

    *Full precision.* Reading the emitted text back must yield exactly the number the author
    declared. Rendering with `decimals_for_tolerance` decimals did not: it stored a declared
    `0.025` as `0.03` (looser than declared), `0.15` as `0.1` (stricter), and anything below about
    5e-13 as `0.000000000000` — that is `0.0`, a tolerance that silently demands bit-exact
    equality. The decimal expansion of `repr(tolerance)` avoids all three: `repr` is the shortest
    text that round-trips through `float`, and expanding it through `Decimal` removes the exponent
    without changing the value, so `float(format_tolerance(t)) == t` for every finite positive `t`.

    Raises:
        ValidationError: If the tolerance is not a finite positive number.
    """
    _require_usable_tolerance(tolerance)
    text = format(Decimal(repr(float(tolerance))), "f")
    if "." not in text:
        text += ".0"
    return text


def format_number(value: Union[float, int]) -> str:
    """Formats an input number deterministically and without exponent notation.

    Inputs are transcribed rather than rounded — they are what the author typed and what the test
    feeds back into the engine — so this keeps full precision while removing the two ways Python
    can render the same value differently: exponent notation and a trailing `.0` on integral
    floats. Deterministic rendering is a requirement of the drift check, not a cosmetic choice.
    """
    if isinstance(value, int):
        return str(value)
    if float(value).is_integer() and abs(value) < YamlText.INTEGRAL_RENDER_LIMIT:
        return str(int(value))
    text = repr(float(value))
    if "e" in text or "E" in text:
        text = f"{value:.{YamlText.INPUT_EXPANSION_DECIMALS}f}".rstrip("0")
        if text.endswith("."):
            text += "0"
    return text


def quote(text: str) -> str:
    """Double-quoted YAML scalar; newlines from Excel cells collapse to spaces.

    Every string the workbook contributes (names, descriptions, derivations, notes) goes through
    here, so that a cell containing a colon, a leading `#` or an alt-enter line break cannot break
    the emitted document or turn into a multi-line block whose exact indentation would then be part
    of the fingerprint. Whitespace is collapsed for the same reason: it must not carry meaning.
    """
    collapsed = " ".join(str(text).split())
    escaped = collapsed.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def format_scalar(value: Union[float, int, str, bool]) -> str:
    """Formats an input value (number, string or boolean) as a YAML scalar.

    The single entry point for rendering the INPUTS table, so that the type openpyxl read out of
    the cell survives into the fixture: numbers stay numbers, strings are quoted, and booleans
    render as YAML booleans. The bool branch comes first because `bool` is a subclass of `int` in
    Python and would otherwise be printed as 0/1.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return format_number(value)
    return quote(str(value))


def emit_yaml(example: Example) -> str:
    """Renders the deterministic YAML of one example (§3.4), review block last (§3.8).

    Rendering is done by hand rather than through `yaml.dump` so that key order, float formatting
    and quoting are fully under this module's control: the CI drift check compares the regenerated
    text byte for byte, so a PyYAML version bump changing its emitter style would otherwise look
    like every example drifted at once. The result is parsed back with `yaml.safe_load` as a final
    sanity check before it is handed to the caller.

    The review block is appended only when the fingerprint of the just-rendered content matches the
    one attested in the workbook — that is the entire auto-reset mechanism of §3.8. Because it is
    written last, `content_for_fingerprint` can strip it again by a simple prefix cut.

    Returns:
        The complete YAML text, ending in a newline.

    Raises:
        ValidationError: If the rendered text does not parse back into a mapping with an `expected`
            key (a guard against a malformed cell breaking the document structure).
    """
    metadata = example.metadata
    lines = [YamlText.GENERATED_HEADER.format(source=example.source_name).rstrip("\n")]
    lines.append(f"name: {quote(metadata['name'])}")
    lines.append(f"group: {quote(example.group)}")
    lines.append(f"spec_section: {quote(metadata['spec_section'])}")
    lines.append(f"computed_by: {quote(metadata['computed_by'])}")
    lines.append(f"description: {quote(metadata['description'])}")
    lines.append("inputs:")
    for label, value in example.inputs:
        lines.append(f"  {label}: {format_scalar(value)}")
    lines.append("expected:")
    for row in example.expected:
        lines.append(f"  {row.label}:")
        lines.append(f"    value: {format_value(row.value, row.abs_tol)}")
        lines.append(f"    abs_tol: {format_tolerance(row.abs_tol)}")
        if row.derivation is not None:
            lines.append(f"    derivation: {quote(row.derivation)}")
        if row.note is not None:
            lines.append(f"    note: {quote(row.note)}")
    text = "\n".join(lines) + "\n"

    fingerprint = fingerprint_of(text)
    if metadata["reviewed_fingerprint"] == fingerprint:
        text += FingerprintFormat.BLOCK_KEY
        text += f"  reviewed_by: {quote(metadata['reviewed_by'])}\n"
        text += f"  review_date: {quote(metadata['review_date'])}\n"
        text += f"  fingerprint: {quote(fingerprint)}\n"
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, dict) or "expected" not in parsed:
        raise ValidationError(f"{example.source_name}: generated YAML is malformed.")
    return text
