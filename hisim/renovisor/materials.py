"""The runtime reader of the typed insulation-material table.

The translation layer needs one number per material to turn a thickness into a U-value — the
thermal conductivity λ in W/(m·K) — and four more to price and account for it later: the lifespan,
the investment cost per country, and the CO2 footprint and storage per m2. They live in
``data/insulation_materials.json``, generated from the contract's material dump by
:mod:`hisim.renovisor.materials_import` (decision Q5)::

    materials = InsulationMaterials.load()
    materials.by_asp_id("metac_glasswool").thermal_conductivity_in_watt_per_meter_per_kelvin
    # 0.0385

A material is addressed by its ``asp_id``, the database's own identifier, which decision C3 made
the contract's material vocabulary too — so the string in a request is the string in this table
and no alias layer sits between them.

The dump itself is never read at runtime. It is a spreadsheet export whose cells sometimes hold
prose, and reading it in a simulation would mean parsing prose in a simulation.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, Mapping, Optional, Tuple


@dataclass(frozen=True)
class ValueRange:
    """A numeric range from the material dump, with the original cell kept.

    Args:
        low: The lower bound, or ``None`` when the cell carried no number.
        high: The upper bound, or ``None`` for an open-ended or unparseable cell.
        raw: The dump's cell as text, for a reader who wants to see what was there.
    """

    low: Optional[float]
    high: Optional[float]
    raw: Optional[str]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ValueRange":
        """Build a range from one ``{"low": …, "high": …, "raw": …}`` object of the table."""
        return cls(low=data.get("low"), high=data.get("high"), raw=data.get("raw"))


@dataclass(frozen=True)
class CostRange:
    """The investment cost of one material in one country, in euro per cubic metre.

    Args:
        low: The lower bound, or ``None`` when the dump's cell was not a number (it is sometimes
            ``"not relevant"``, sometimes a per-square-metre price such as ``"41-80€/m2"``).
        high: The upper bound, on the same terms.
        raw_low: The dump's ``min`` cell as text, or ``None`` when it was absent.
        raw_high: The dump's ``max`` cell as text, or ``None`` when it was absent.
    """

    low: Optional[float]
    high: Optional[float]
    raw_low: Optional[str]
    raw_high: Optional[str]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CostRange":
        """Build a cost range from one country's object of the table."""
        return cls(
            low=data.get("low"),
            high=data.get("high"),
            raw_low=data.get("raw_low"),
            raw_high=data.get("raw_high"),
        )


@dataclass(frozen=True)
class Material:
    """One insulation material as the typed table carries it.

    Args:
        asp_id: The database identifier, e.g. ``"wood_fiber_rigid_board"``; also the value a
            request's ``material`` option carries.
        name: The dump's display name, e.g. ``"Wood fiber - Rigid board"``.
        thermal_conductivity_in_watt_per_meter_per_kelvin: λ, the only number the U-value
            derivation needs.
        lifespan_in_years: How long the material lasts, as a range.
        investment_cost_in_euro_per_m3: The cost range per country code (``IE``, ``ES``, ``NL``).
        co2_footprint_in_kg_per_m2: Cradle-to-gate plus end-of-life CO2 equivalent per square
            metre of the dump's reference build-up, or ``None`` when the dump has no figure.
        co2_storage_in_kg_per_m2: Biogenic carbon stored per square metre, negative for a material
            that stores more than it emits; ``None`` when the dump has no figure.
        end_of_life: The dump's best-case end-of-life options, e.g. ``["Re-Use", "Recycling"]``.
        comparison_baseline: Whether the dump marks this material as one of the conventional
            baselines the bio-based materials are compared against.
        sources: The dump's ``Sources`` text, verbatim.
        summary: The dump's homeowner-facing summary, verbatim; empty when it has none.
    """

    asp_id: str
    name: str
    thermal_conductivity_in_watt_per_meter_per_kelvin: float
    lifespan_in_years: ValueRange
    investment_cost_in_euro_per_m3: Mapping[str, CostRange]
    co2_footprint_in_kg_per_m2: Optional[float]
    co2_storage_in_kg_per_m2: Optional[float]
    end_of_life: Tuple[str, ...]
    comparison_baseline: bool
    sources: str
    summary: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Material":
        """Build a material from one object of the table's ``materials`` list."""
        costs = data.get("investment_cost_in_euro_per_m3") or {}
        return cls(
            asp_id=str(data["asp_id"]),
            name=str(data.get("name", "")),
            thermal_conductivity_in_watt_per_meter_per_kelvin=float(
                data["thermal_conductivity_in_watt_per_meter_per_kelvin"]
            ),
            lifespan_in_years=ValueRange.from_dict(data.get("lifespan_in_years") or {}),
            investment_cost_in_euro_per_m3={
                country: CostRange.from_dict(block) for country, block in costs.items()
            },
            co2_footprint_in_kg_per_m2=data.get("co2_footprint_in_kg_per_m2"),
            co2_storage_in_kg_per_m2=data.get("co2_storage_in_kg_per_m2"),
            end_of_life=tuple(str(item) for item in data.get("end_of_life") or ()),
            comparison_baseline=bool(data.get("comparison_baseline", False)),
            sources=str(data.get("sources", "")),
            summary=str(data.get("summary", "")),
        )


class InsulationMaterials:
    """The insulation-material table, addressed by ``asp_id``.

    Example::

        materials = InsulationMaterials.load()
        materials.asp_ids()[:2]                  # ('wood_fiber_rigid_board', 'wood_wool_rigid_board')
        materials.contains("polystyrene_eps_rigid_board")   # True

    :meth:`load` re-reads the file each time, as the contract accessors do: the file is 26 rows,
    and a cache would hide a re-import that happened during a long test session.
    """

    #: The generated table this class reads, beside this module.
    TABLE_PATH: ClassVar[Path] = Path(__file__).resolve().parent / "data" / "insulation_materials.json"

    def __init__(self, materials: Tuple[Material, ...], source: Mapping[str, Any]) -> None:
        """Store the materials and index them by ``asp_id``.

        Args:
            materials: The rows, in table order.
            source: The table's provenance header: the contract commit and hash the dump came
                from.

        Raises:
            ValueError: When two rows share an ``asp_id``.
        """
        self._materials = materials
        self._source = dict(source)
        self._by_asp_id: Dict[str, Material] = {}
        for material in materials:
            if material.asp_id in self._by_asp_id:
                raise ValueError(f"duplicate asp_id '{material.asp_id}' in the material table")
            self._by_asp_id[material.asp_id] = material

    @classmethod
    def load(cls, table_path: Optional[Path] = None) -> "InsulationMaterials":
        """Read the generated table and return it.

        Args:
            table_path: Where to read from; defaults to :attr:`TABLE_PATH`.

        Returns:
            The loaded table.

        Raises:
            FileNotFoundError: When the table has not been generated yet; run
                ``python -m hisim.renovisor.materials_import``.
            ValueError: When the file has no ``materials`` list.
        """
        path = cls.TABLE_PATH if table_path is None else table_path
        document = json.loads(path.read_text(encoding="utf-8"))
        entries = document.get("materials")
        if not isinstance(entries, list):
            raise ValueError(f"{path} has no 'materials' list")
        return cls(
            materials=tuple(Material.from_dict(entry) for entry in entries),
            source=document.get("source") or {},
        )

    def by_asp_id(self, asp_id: str) -> Material:
        """Return one material by its database id.

        Args:
            asp_id: The material id, e.g. ``"open_cell_spray_foam"``.

        Returns:
            The :class:`Material`.

        Raises:
            KeyError: When the table has no such material. Callers turn that into a
                ``Refusal(MATERIAL_NOT_IN_DATABASE)``; they never substitute another material.
        """
        return self._by_asp_id[asp_id]

    def contains(self, asp_id: str) -> bool:
        """Return whether the table has a row for *asp_id*."""
        return asp_id in self._by_asp_id

    def asp_ids(self) -> Tuple[str, ...]:
        """Return every material id, in table order."""
        return tuple(material.asp_id for material in self._materials)

    def materials(self) -> Tuple[Material, ...]:
        """Return every material, in table order."""
        return self._materials

    def source(self) -> Dict[str, Any]:
        """Return the provenance header: which contract revision the table was generated from."""
        return dict(self._source)
