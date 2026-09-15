"""One-off import of the insulation-material dump into a small, typed, checked-in table.

``materials.yaml`` in the vendored contract is a spreadsheet export, not a database: 26 materials
with fourteen application areas, product listings per country, footnote-bearing prose, and a
handful of cells that carry the wrong quantity or a string where a number belongs. HiSim needs seven
things out of it — the thermal conductivity, the lifespan, the investment cost per country, the
CO2 footprint per m2 and per m3, the CO2 storage per m2, and the end-of-life options — and needs
them to be numbers.

Decision Q5 settled how: this script reads the dump once and writes
``hisim/renovisor/data/insulation_materials.json``, which is what the runtime reads. Run it after
every contract refresh::

    python -m hisim.renovisor.materials_import

It prints one line per material and exits non-zero when any row fails, because a silently skipped
or silently defaulted material is a physics error that would surface as a plausible-looking
U-value. The only hard failure is a non-numeric thermal conductivity: everything else has a
defined "no value here" representation that keeps the raw string for a human to look at.

What is deliberately not imported: heat capacity and density (the XPS heat capacity in the dump is
a conductivity and ``eps_beads_cavity``'s is the string ``120-1401-0``; neither quantity is needed
for a U-value), and the fourteen component-application areas, which are the catalogue's business
and not HiSim's (challenge C11).
"""

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

from hisim.renovisor.contract import ContractFiles


@dataclass(frozen=True)
class ParsedRange:
    """A numeric range parsed out of one spreadsheet cell, with the cell kept verbatim.

    The dump writes ranges as prose: ``"110 - 270"``, ``"28-45"``, ``"≥ 50"``, ``"50"``. Parsing
    them into ``(low, high)`` makes them usable; keeping :attr:`raw` makes them checkable, and is
    the only thing left when a cell does not parse at all.

    Args:
        low: The lower bound, or ``None`` when the cell carries no number.
        high: The upper bound; ``None`` for an open-ended cell such as ``"≥ 50"`` and for a cell
            that did not parse.
        raw: The cell exactly as the dump has it, as a string; ``None`` when the cell was absent.
    """

    low: Optional[float]
    high: Optional[float]
    raw: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        """Return the range as the JSON object the table carries."""
        return {"low": self.low, "high": self.high, "raw": self.raw}


class RangeParser:
    """Parses the dump's numeric cells, whether they hold a number, a range or prose.

    Example::

        RangeParser.parse("110 - 270")   # ParsedRange(low=110.0, high=270.0, raw='110 - 270')
        RangeParser.parse("≥ 50")        # ParsedRange(low=50.0, high=None, raw='≥ 50')
        RangeParser.parse("n")           # ParsedRange(low=None, high=None, raw='n')

    A single number is read as a degenerate range with equal bounds; an "at least" cell as a lower
    bound with no upper one. Anything the two patterns do not match keeps only its raw text, which
    is what makes a missing number visible in the committed table instead of invented.
    """

    #: ``28-45``, ``110 - 270``, ``40-75`` — two numbers with a hyphen between them.
    RANGE_PATTERN: ClassVar["re.Pattern[str]"] = re.compile(
        r"^\s*(?P<low>-?\d+(?:[.,]\d+)?)\s*[-–—]\s*(?P<high>-?\d+(?:[.,]\d+)?)\s*$"
    )

    #: ``50``, ``≥ 50``, ``>= 50``, ``> 50`` — one number, optionally with a lower-bound sign.
    SINGLE_PATTERN: ClassVar["re.Pattern[str]"] = re.compile(
        r"^\s*(?P<sign>[≥≤><]=?)?\s*(?P<value>-?\d+(?:[.,]\d+)?)\s*$"
    )

    #: The signs that make a single number an open-ended lower bound rather than an exact value.
    LOWER_BOUND_SIGNS: ClassVar[Tuple[str, ...]] = ("≥", ">=", ">")

    @classmethod
    def parse(cls, raw: Any) -> ParsedRange:
        """Parse one cell into a :class:`ParsedRange`.

        Args:
            raw: The cell value as the YAML parser produced it: a number, a string, or ``None``.

        Returns:
            The parsed range. A cell that does not parse has both bounds ``None`` and keeps its
            text in :attr:`ParsedRange.raw`.
        """
        if raw is None:
            return ParsedRange(low=None, high=None, raw=None)
        if isinstance(raw, bool):
            return ParsedRange(low=None, high=None, raw=str(raw))
        if isinstance(raw, (int, float)):
            return ParsedRange(low=float(raw), high=float(raw), raw=str(raw))
        text = str(raw)
        range_match = cls.RANGE_PATTERN.match(text)
        if range_match is not None:
            return ParsedRange(
                low=cls._number(range_match.group("low")),
                high=cls._number(range_match.group("high")),
                raw=text,
            )
        single_match = cls.SINGLE_PATTERN.match(text)
        if single_match is not None:
            value = cls._number(single_match.group("value"))
            sign = single_match.group("sign")
            if sign in cls.LOWER_BOUND_SIGNS:
                return ParsedRange(low=value, high=None, raw=text)
            if sign is not None:
                return ParsedRange(low=None, high=value, raw=text)
            return ParsedRange(low=value, high=value, raw=text)
        return ParsedRange(low=None, high=None, raw=text)

    @classmethod
    def number_or_none(cls, raw: Any) -> Optional[float]:
        """Return *raw* as a float when it already is a number, else ``None``.

        Used for the cells that must be plain numbers — a cost bound, a CO2 figure — where a range
        or a note means "no usable value" rather than something to interpret.
        """
        if isinstance(raw, bool) or raw is None:
            return None
        if isinstance(raw, (int, float)):
            return float(raw)
        return None

    @classmethod
    def _number(cls, text: str) -> float:
        """Parse one number, accepting a decimal comma as the dump's German rows use it."""
        return float(text.replace(",", "."))


class MaterialRowError(Exception):
    """Raised when one material row cannot be imported and must not be silently dropped.

    Args:
        asp_id: The material's database id, or the row's index when it has no id.
        detail: What is wrong, naming the offending value.
    """

    def __init__(self, asp_id: str, detail: str) -> None:
        """Store the id and the detail and build the message the script prints."""
        super().__init__(f"{asp_id}: {detail}")
        self.asp_id = asp_id
        self.detail = detail


class MaterialsImport:
    """Turns the vendored ``materials.yaml`` into the typed table HiSim reads at runtime.

    The class is the whole import: :meth:`build` produces the JSON payload, :meth:`write` puts it
    where the reader looks, and :meth:`main` is what ``python -m`` runs. Splitting them lets a
    test regenerate the table into a temporary directory and compare it against the committed
    file, which is what turns "somebody refreshed the contract and forgot to re-import" into a
    failing build.
    """

    #: Where the generated table lives, beside the module that reads it.
    OUTPUT_PATH: ClassVar[Path] = Path(__file__).resolve().parent / "data" / "insulation_materials.json"

    #: The countries whose investment costs the dump carries, in the order the table writes them.
    COUNTRIES: ClassVar[Tuple[str, ...]] = ("IE", "ES", "NL")

    #: Keys of the dump, spelled out so a spreadsheet rename shows up as a failing import rather
    #: than as a column that quietly became ``None``.
    PROPERTIES_KEY: ClassVar[str] = "Material properties and characteristics"
    ENVIRONMENT_KEY: ClassVar[str] = "Environmental / Health factor"
    ECONOMIC_PROPERTIES_KEY: ClassVar[str] = "Economic properties"
    CONDUCTIVITY_KEY: ClassVar[str] = "Thermal conductivity (AVERAGE)"
    LIFESPAN_KEY: ClassVar[str] = "Lifespan"
    LIFESPAN_YEARS_KEY: ClassVar[str] = "(Years)"
    COST_KEY_TEMPLATE: ClassVar[str] = "material's investment costs {country} (€/m³)"
    CO2_FOOTPRINT_KEY: ClassVar[str] = "CO₂ footprint (A1-A3, C3-C4 EN 15804 +A2) kg CO₂-eq./m2"
    CO2_STORAGE_KEY: ClassVar[str] = "CO₂ storage (GWP-biogenic A1-A3) kg CO₂-eq./m2"

    #: The dump's *per-unit* footprint block, the only cell carrying a footprint per cubic metre.
    #: It is a two-cell block: ``column X`` holds the number and ``column Y`` names the unit that
    #: number is in, so the unit has to be read before the number may be believed.
    CO2_PER_UNIT_BLOCK_KEY: ClassVar[str] = "CO₂ footprint (A1-A3, EN 15804 +A2)"
    CO2_PER_UNIT_KEY: ClassVar[str] = "(kg CO₂-eq./unit)"
    CO2_PER_UNIT_VALUE_COLUMN: ClassVar[str] = "column X"
    CO2_PER_UNIT_UNIT_COLUMN: ClassVar[str] = "column Y"

    #: The only unit whose number is imported as ``co2_footprint_in_kg_per_m3``. A row whose
    #: ``column Y`` says anything else states the footprint of a different quantity, and importing
    #: it as a volumetric one would later be multiplied by a thickness and an area, which is how a
    #: per-square-metre figure becomes a wrong embodied-carbon total.
    CO2_PER_CUBIC_METRE_UNIT: ClassVar[str] = "kg CO₂-eq./m³"

    END_OF_LIFE_KEY: ClassVar[str] = "End of Life (best scenario)"
    SOURCES_KEY: ClassVar[str] = "Sources"
    SUMMARY_KEY: ClassVar[str] = "summary"

    #: Indentation of the written JSON; fixed so a re-import is byte-identical.
    JSON_INDENT: ClassVar[int] = 2

    @classmethod
    def build(cls) -> Dict[str, Any]:
        """Read the vendored dump and return the table as a JSON-ready dictionary.

        Returns:
            ``{"source": {...}, "materials": [...]}`` with the contract commit and hash of the
            dump in ``source`` and one object per material in ``materials``, in dump order.

        Raises:
            MaterialRowError: When a row has no ``asp_id`` or a non-numeric thermal conductivity.
                The first failing row stops the build; :meth:`main` reports every failing row
                instead.
        """
        rows, failures = cls._read_rows()
        if failures:
            raise failures[0]
        return {"source": cls._source(), "materials": rows}

    @classmethod
    def write(cls, output_path: Optional[Path] = None) -> Path:
        """Write the table and return the path it was written to.

        Args:
            output_path: Where to write; defaults to :attr:`OUTPUT_PATH`. A test passes a
                temporary path to regenerate the table without touching the committed one.

        Returns:
            The path written.

        Raises:
            MaterialRowError: As :meth:`build` does.
        """
        destination = cls.OUTPUT_PATH if output_path is None else output_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(cls.render(cls.build()), encoding="utf-8")
        return destination

    @classmethod
    def render(cls, payload: Mapping[str, Any]) -> str:
        """Return the table's exact file text for *payload*, so writes are reproducible."""
        return json.dumps(payload, indent=cls.JSON_INDENT, ensure_ascii=False, sort_keys=False) + "\n"

    @classmethod
    def main(cls, argv: Optional[List[str]] = None) -> int:
        """Run the import as a command, printing a row-by-row summary.

        Args:
            argv: Command-line arguments; only used to keep the signature testable. No options
                are defined, because the import has nothing to choose.

        Returns:
            ``0`` when every row imported, ``1`` when any row failed. Nothing is written when a
            row failed, so a half-imported table never reaches the repository.
        """
        del argv
        rows, failures = cls._read_rows()
        for row in rows:
            conductivity = row["thermal_conductivity_in_watt_per_meter_per_kelvin"]
            print(f"  ok      {row['asp_id']:<46} lambda={conductivity}")
        for failure in failures:
            print(f"  FAILED  {failure.asp_id:<46} {failure.detail}")
        if failures:
            print(f"{len(failures)} of {len(rows) + len(failures)} rows failed; nothing written.")
            return 1
        payload = {"source": cls._source(), "materials": rows}
        cls.OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        cls.OUTPUT_PATH.write_text(cls.render(payload), encoding="utf-8")
        print(f"{len(rows)} materials written to {cls.OUTPUT_PATH}")
        return 0

    @classmethod
    def _read_rows(cls) -> Tuple[List[Dict[str, Any]], List[MaterialRowError]]:
        """Return the imported rows and the failures, so the command can report all of both."""
        document = ContractFiles.materials()
        entries = document.get("materials") if isinstance(document, dict) else None
        if not isinstance(entries, list):
            raise ValueError("materials.yaml has no 'materials' list")
        rows: List[Dict[str, Any]] = []
        failures: List[MaterialRowError] = []
        for index, entry in enumerate(entries):
            try:
                rows.append(cls._read_row(index, entry))
            except MaterialRowError as error:
                failures.append(error)
        return rows, failures

    @classmethod
    def _read_row(cls, index: int, entry: Mapping[str, Any]) -> Dict[str, Any]:
        """Turn one dump row into the table's material object."""
        asp_id = str(entry.get("asp_id") or "").strip()
        if not asp_id:
            raise MaterialRowError(f"row[{index}]", "the row carries no asp_id")
        properties = entry.get(cls.PROPERTIES_KEY) or {}
        conductivity = RangeParser.number_or_none(properties.get(cls.CONDUCTIVITY_KEY))
        if conductivity is None:
            raise MaterialRowError(
                asp_id,
                f"thermal conductivity is {properties.get(cls.CONDUCTIVITY_KEY)!r}, which is not a number",
            )
        environment = entry.get(cls.ENVIRONMENT_KEY) or {}
        economics = entry.get(cls.ECONOMIC_PROPERTIES_KEY) or {}
        lifespan_block = economics.get(cls.LIFESPAN_KEY)
        lifespan_raw = lifespan_block.get(cls.LIFESPAN_YEARS_KEY) if isinstance(lifespan_block, dict) else None
        end_of_life = environment.get(cls.END_OF_LIFE_KEY) or []
        return {
            "asp_id": asp_id,
            "name": str(entry.get("name", "")),
            "thermal_conductivity_in_watt_per_meter_per_kelvin": conductivity,
            "lifespan_in_years": RangeParser.parse(lifespan_raw).to_dict(),
            "investment_cost_in_euro_per_m3": cls._costs(economics),
            "co2_footprint_in_kg_per_m2": RangeParser.number_or_none(environment.get(cls.CO2_FOOTPRINT_KEY)),
            "co2_footprint_in_kg_per_m3": cls._co2_per_cubic_metre(environment),
            "co2_storage_in_kg_per_m2": RangeParser.number_or_none(environment.get(cls.CO2_STORAGE_KEY)),
            "end_of_life": [str(item) for item in end_of_life],
            "comparison_baseline": bool(entry.get("comparison_baseline", False)),
            "sources": cls._text(environment.get(cls.SOURCES_KEY)),
            "summary": cls._text(environment.get(cls.SUMMARY_KEY)),
        }

    @classmethod
    def _co2_per_cubic_metre(cls, environment: Mapping[str, Any]) -> Optional[float]:
        """Return the row's cradle-to-gate footprint per cubic metre, or ``None``.

        The dump states this figure in a block of two cells rather than in a column whose header
        names the unit: ``column X`` is the number and ``column Y`` is the unit it is in. Every
        row of the current dump that has the block at all says ``kg CO₂-eq./m³``, but reading the
        unit is the point -- three rows carry no block, and a row whose unit ever changes must
        drop out of the table rather than be multiplied by a volume (step 6 §3, decision Q22).

        Args:
            environment: The row's ``Environmental / Health factor`` block.

        Returns:
            The footprint in kg CO2-eq. per cubic metre, or ``None`` when the row has no such
            block, no number in it, or a unit other than :attr:`CO2_PER_CUBIC_METRE_UNIT`.
        """
        block = environment.get(cls.CO2_PER_UNIT_BLOCK_KEY)
        per_unit = block.get(cls.CO2_PER_UNIT_KEY) if isinstance(block, dict) else None
        if not isinstance(per_unit, dict):
            return None
        unit = per_unit.get(cls.CO2_PER_UNIT_UNIT_COLUMN)
        if str(unit).strip() != cls.CO2_PER_CUBIC_METRE_UNIT:
            return None
        return RangeParser.number_or_none(per_unit.get(cls.CO2_PER_UNIT_VALUE_COLUMN))

    @classmethod
    def _costs(cls, economics: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Return the per-country investment cost bounds, keeping unparseable cells as raw text."""
        costs: Dict[str, Dict[str, Any]] = {}
        for country in cls.COUNTRIES:
            block = economics.get(cls.COST_KEY_TEMPLATE.format(country=country))
            raw_min = block.get("min") if isinstance(block, dict) else None
            raw_max = block.get("max") if isinstance(block, dict) else None
            costs[country] = {
                "low": RangeParser.number_or_none(raw_min),
                "high": RangeParser.number_or_none(raw_max),
                "raw_low": None if raw_min is None else str(raw_min),
                "raw_high": None if raw_max is None else str(raw_max),
            }
        return costs

    @classmethod
    def _text(cls, raw: Any) -> str:
        """Return a prose cell as a string, with an absent cell becoming the empty string."""
        return "" if raw is None else str(raw)

    @classmethod
    def _source(cls) -> Dict[str, Any]:
        """Return the provenance header: which contract revision the dump was taken from."""
        pinned = ContractFiles.pinned()
        record = (pinned.get("files") or {}).get(ContractFiles.MATERIALS_FILENAME) or {}
        return {
            "contract_commit": record.get("commit"),
            "file": ContractFiles.MATERIALS_FILENAME,
            "sha256": record.get("sha256"),
        }


if __name__ == "__main__":
    sys.exit(MaterialsImport.main(sys.argv[1:]))
