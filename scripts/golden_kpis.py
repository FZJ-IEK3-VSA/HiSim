"""Pure KPI helpers for the golden-reference system.

HiSim writes ``all_kpis.json`` as a nested structure ``{building: {tag: {key: entry}}}``.
:func:`golden_leaves` turns that into the golden form: one **leaf** per KPI, keyed by its
dotted address (:attr:`~hisim.postprocessing.kpi_computation.kpi_address.KpiAddress.dotted`)
and carrying the address's fields beside the value and the unit::

    "BUI1.Building.Conditioned floor area (Building)": {
        "value": 121.2, "unit": "m2", "building": "BUI1", "tag": "Building",
        "name": "Conditioned floor area",
        "source": {"import": null, "instance": null, "member": "Building",
                   "assembly": null, "name": "Building"}
    }

``source`` holds the five identity fields of the entry's
:class:`~hisim.postprocessing.kpi_computation.kpi_structure.KpiSource` (its ``display_name``
and ``label`` are presentation and stay out), and is ``null`` for a derived KPI. The key is
built from the fields by :class:`KpiAddress` and checked against them on every read
(:func:`read_golden`), so nothing here ever splits a key.

:func:`compare` diffs a fresh leaf map against a stored one: the value with numeric tolerance,
the unit exactly, and the address fields exactly, each a :class:`Deviation` of its own kind.
Everything here is side-effect-free and unit-testable without running HiSim.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

# Every importer (the gate's scripts put the repo root on ``sys.path``; tests run from it) can
# import ``hisim``; this module adds nothing to the path itself, so importing it has no effect.
from hisim.postprocessing.kpi_computation.kpi_address import KpiAddress, KpiFinder
from hisim.postprocessing.kpi_computation.kpi_structure import KpiSource

# Tolerance policy (spec §7): same-machine output is byte-exact, so a very tight
# relative tolerance still passes while absorbing sub-ULP cross-platform drift.
REL_TOL = 1e-9
ABS_TOL = 0.0

#: The fields of a golden leaf, exactly these and no others.
LEAF_FIELDS = ("value", "unit", "building", "tag", "name", "source")
#: The identity fields of a leaf's ``source`` (``KpiSource`` without ``display_name`` and ``label``).
SOURCE_FIELDS = ("import", "instance", "member", "assembly", "name")

#: What to do about a golden in a form this tooling does not read.
REBLESS_HINT = (
    "re-bless it through the golden-update workflow with force_rewrite "
    "(scripts/golden_update.py --force-rewrite), which writes the fresh leaves without reading the old file"
)

#: The kinds of :class:`Deviation`, in the order :func:`compare` reports them per key.
MISSING = "missing"
VALUE = "value"
UNIT = "unit"
ADDRESS = "address"
NEW = "new"


class GoldenFormatError(ValueError):
    """A golden mapping that is not the leaf form :func:`golden_leaves` writes."""


@dataclass(frozen=True)
class Deviation:
    """One way a fresh KPI leaf map differs from a stored one.

    Attributes:
        kind: :data:`MISSING`, :data:`VALUE`, :data:`UNIT`, :data:`ADDRESS` or :data:`NEW`.
        key: The dotted address of the KPI.
        message: The human-readable line the gate and the bless print.
    """

    kind: str
    key: str
    message: str

    def __str__(self) -> str:
        """The message, so a deviation prints as the line it stands for."""
        return self.message


def _is_number(value: Any) -> bool:
    """True for real numbers (``bool`` excluded — it is compared exactly)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _coerce(value: Any) -> Any:
    """Coerce numeric values to ``float``; leave everything else untouched."""
    return float(value) if _is_number(value) else value


def source_fields(source: Optional[KpiSource]) -> Optional[dict[str, Optional[str]]]:
    """The identity fields of a source as a golden leaf stores them, or ``None`` for a derived KPI.

    Args:
        source: The entry's source.

    Returns:
        ``{"import", "instance", "member", "assembly", "name"}``, or ``None``.
    """
    if source is None:
        return None
    return {
        "import": source.import_key,
        "instance": source.instance,
        "member": source.member,
        "assembly": source.assembly,
        "name": source.name,
    }


def golden_leaf(address: KpiAddress, value: Any, unit: str) -> dict[str, Any]:
    """The golden leaf of one KPI: its value, its unit and the fields of its address.

    Args:
        address: The KPI's address; its :attr:`~KpiAddress.dotted` form is the leaf's key.
        value: The KPI value; a number is stored as a ``float``, anything else as it is.
        unit: The entry's unit, verbatim.

    Returns:
        The leaf, with exactly the fields of :data:`LEAF_FIELDS`.
    """
    return {
        "value": _coerce(value),
        "unit": unit,
        "building": address.building,
        "tag": address.tag,
        "name": address.name,
        "source": source_fields(address.source),
    }


def golden_leaves(all_kpis: Any) -> dict[str, dict[str, Any]]:
    """Turn a loaded ``all_kpis.json`` into the golden leaf map, keyed by dotted address.

    Every entry is resolved by :class:`KpiFinder`, which refuses a key that disagrees with its
    entry. A golden needs the structured source, so an entry written before the ``source``
    field existed is refused rather than read with a guessed address.

    Args:
        all_kpis: The parsed ``all_kpis.json`` (``building -> tag -> key -> entry``).

    Returns:
        ``dotted address -> leaf`` (:func:`golden_leaf`), in collection order.

    Raises:
        GoldenFormatError: If the document is not a KPI collection, an entry lacks ``source``,
            ``unit`` or ``value``, holds a non-scalar value, or two entries share one address.
        ValueError: From :class:`KpiFinder`, if a key disagrees with its entry.
    """
    if not isinstance(all_kpis, Mapping):
        raise GoldenFormatError(f"all_kpis.json holds a JSON {type(all_kpis).__name__}, not a KPI collection.")
    leaves: dict[str, dict[str, Any]] = {}
    for address, entry in KpiFinder(all_kpis).entries():
        where = f"all_kpis.json entry '{address.dotted}'"
        if "source" not in entry:
            raise GoldenFormatError(
                f"{where} carries no 'source': it was written before the structured source existed, "
                "and a golden reference stores the source's fields."
            )
        unit = entry.get("unit")
        if not isinstance(unit, str):
            raise GoldenFormatError(f"{where} carries no unit string (unit={unit!r}).")
        if "value" not in entry:
            raise GoldenFormatError(f"{where} carries no value.")
        value = entry["value"]
        if not (value is None or isinstance(value, (str, int, float))):
            raise GoldenFormatError(f"{where} holds a {type(value).__name__} value, not a scalar.")
        if address.dotted in leaves:
            raise GoldenFormatError(f"{where}: two entries share this address.")
        leaves[address.dotted] = golden_leaf(address, value, unit)
    return leaves


def _source_of_leaf(raw: Any, where: str) -> Optional[KpiSource]:
    """Read a leaf's ``source`` back into a :class:`KpiSource`, refusing anything but the five fields."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != set(SOURCE_FIELDS):
        raise GoldenFormatError(
            f"{where}: 'source' must be null or an object with exactly the fields {', '.join(SOURCE_FIELDS)}, "
            f"got {raw!r}; {REBLESS_HINT}."
        )
    for field_name in SOURCE_FIELDS:
        if raw[field_name] is not None and not isinstance(raw[field_name], str):
            raise GoldenFormatError(f"{where}: source.{field_name} is not a string or null: {raw[field_name]!r}.")
    if not raw["name"]:
        raise GoldenFormatError(f"{where}: source.name is empty; {REBLESS_HINT}.")
    # The five identity fields are checked above; the strict decoder reads them, the two
    # presentation fields (display_name, label) explicitly absent from a golden leaf.
    try:
        return KpiSource.from_json_object(raw, where)
    except ValueError as error:
        raise GoldenFormatError(f"{error}; {REBLESS_HINT}.") from error


def leaf_address(key: str, leaf: Any, origin: str) -> KpiAddress:
    """Read the address of one stored leaf, refusing a leaf that is not the golden form.

    Args:
        key: The leaf's key in the golden mapping.
        leaf: The stored leaf.
        origin: Where the mapping came from (a path, a commit), for the error message.

    Returns:
        The address the leaf's fields describe; its dotted form equals ``key``.

    Raises:
        GoldenFormatError: If the leaf is a bare value (the old flat form), lacks or adds a
            field, holds a field of the wrong type, or its key is not its address's dotted form.
    """
    where = f"{origin}: KPI '{key}'"
    if not isinstance(leaf, dict):
        raise GoldenFormatError(
            f"{where} holds a bare {type(leaf).__name__}: this golden is in the old flat form "
            f"(dotted key -> value, no unit, no source); {REBLESS_HINT}."
        )
    if set(leaf) != set(LEAF_FIELDS):
        missing = [name for name in LEAF_FIELDS if name not in leaf]
        extra = sorted(set(leaf) - set(LEAF_FIELDS))
        raise GoldenFormatError(
            f"{where} is not a golden leaf (missing fields: {missing or 'none'}, unknown fields: "
            f"{extra or 'none'}); {REBLESS_HINT}."
        )
    for field_name in ("unit", "building", "tag", "name"):
        if not isinstance(leaf[field_name], str):
            raise GoldenFormatError(f"{where}: '{field_name}' is not a string: {leaf[field_name]!r}.")
    if not (leaf["value"] is None or isinstance(leaf["value"], (str, int, float))):
        raise GoldenFormatError(f"{where}: 'value' is not a scalar: {leaf['value']!r}.")
    address = KpiAddress(
        building=leaf["building"], tag=leaf["tag"], name=leaf["name"], source=_source_of_leaf(leaf["source"], where)
    )
    if address.dotted != key:
        raise GoldenFormatError(
            f"{where} disagrees with its own fields, which address it as '{address.dotted}'; "
            f"the key of a leaf is the dotted address of its fields; {REBLESS_HINT}."
        )
    return address


def read_golden(stored: Any, origin: str) -> dict[str, dict[str, Any]]:
    """Check a loaded golden mapping and return it, refusing anything but the leaf form.

    Args:
        stored: The parsed golden file.
        origin: Where it came from, for the error message.

    Returns:
        The same mapping, every leaf checked by :func:`leaf_address`.

    Raises:
        GoldenFormatError: If it is not a JSON object of golden leaves.
    """
    if not isinstance(stored, dict):
        raise GoldenFormatError(
            f"{origin}: holds a JSON {type(stored).__name__}, not an object of golden leaves; {REBLESS_HINT}."
        )
    for key, leaf in stored.items():
        leaf_address(key, leaf, origin)
    return stored


def load_golden(path: Path) -> dict[str, dict[str, Any]]:
    """Read and check one golden file (:func:`read_golden`).

    Raises:
        GoldenFormatError: If the file cannot be read as JSON or is not the leaf form.
    """
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldenFormatError(f"{path}: cannot be read as JSON ({exc}); {REBLESS_HINT}.") from exc
    return read_golden(stored, str(path))


def _format_numeric_change(name: str, key: str, ref_v: float, got_v: float, rel_tol: float, abs_tol: float) -> str:
    """Explain a numeric KPI divergence, including the magnitude and the tolerance exceeded."""
    abs_diff = abs(got_v - ref_v)
    rel_diff = abs_diff / abs(ref_v) if ref_v != 0 else math.inf
    rel_pct = "inf%" if math.isinf(rel_diff) else f"{rel_diff:.3%}"
    return (
        f"{name}: KPI '{key}' changed: ref={ref_v!r} got={got_v!r} "
        f"(abs diff={abs_diff:.6g}, rel diff={rel_pct}; "
        f"tolerance rel={rel_tol:g} abs={abs_tol:g})"
    )


def _address_fields(leaf: Mapping[str, Any]) -> dict[str, Any]:
    """The address part of a leaf: everything but value and unit."""
    return {field_name: leaf[field_name] for field_name in ("building", "tag", "name", "source")}


def _compare_leaf(name: str, key: str, got: Mapping[str, Any], ref: Mapping[str, Any], rel_tol: float,
                  abs_tol: float) -> list[Deviation]:
    """The deviations of one KPI present on both sides: value, then unit, then address fields."""
    found: list[Deviation] = []
    ref_v, got_v = ref["value"], got["value"]
    if _is_number(ref_v) and _is_number(got_v):
        if not math.isclose(got_v, ref_v, rel_tol=rel_tol, abs_tol=abs_tol):
            found.append(Deviation(VALUE, key, _format_numeric_change(name, key, ref_v, got_v, rel_tol, abs_tol)))
    elif got_v != ref_v:
        found.append(Deviation(VALUE, key, f"{name}: KPI '{key}' changed: ref={ref_v!r} got={got_v!r}"))
    if got["unit"] != ref["unit"]:
        found.append(
            Deviation(UNIT, key, f"{name}: KPI '{key}' changed its unit: ref={ref['unit']!r} got={got['unit']!r}")
        )
    if _address_fields(got) != _address_fields(ref):
        found.append(
            Deviation(
                ADDRESS,
                key,
                f"{name}: KPI '{key}' changed its address fields: ref={_address_fields(ref)!r} "
                f"got={_address_fields(got)!r}",
            )
        )
    return found


def compare(
    name: str,
    got: Mapping[str, Mapping[str, Any]],
    ref: Mapping[str, Mapping[str, Any]],
    rel_tol: float = REL_TOL,
    abs_tol: float = ABS_TOL,
) -> list[Deviation]:
    """Return every deviation of the leaf map ``got`` from the stored leaf map ``ref``.

    Numeric values are compared with :func:`math.isclose`, non-numeric values by exact
    equality; a numeric divergence reports the absolute and relative delta plus the tolerance
    it exceeded. The unit is compared exactly and a changed unit is a deviation of its own kind
    (:data:`UNIT`), whatever the value did. The address fields under one key are compared
    exactly too (:data:`ADDRESS`: a source whose member or import changed while its runtime
    name stayed). Reports, in this order: per ``ref`` key, missing from ``got`` or its value,
    unit and address deviations; then the keys present only in ``got``, sorted.
    """
    errs: list[Deviation] = []
    for key, ref_leaf in ref.items():
        if key not in got:
            errs.append(Deviation(MISSING, key, f"{name}: missing KPI '{key}' in current run"))
            continue
        errs.extend(_compare_leaf(name, key, got[key], ref_leaf, rel_tol, abs_tol))
    for key in sorted(got.keys() - ref.keys()):
        errs.append(Deviation(NEW, key, f"{name}: new KPI '{key}' not in reference (regenerate if intended)"))
    return errs
