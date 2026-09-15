"""Envelope physics: where a U-value comes from, how layers compose, and which measures fit.

Four things the envelope measures need and that nothing else in HiSim owns:

*the element's current U-value* — the number an added insulation layer improves on. Decision Q10
settled the precedence: the inventory wins per element when it carries a value, the TABULA
archetype row supplies the rest, and the report says which (:class:`InventoryThenTabula`).

*a thickness when the request does not state one* — decision Q11 made it target-driven: the
thinnest 10 mm step that reaches the Irish regulatory target for that element, with no
thermal-bridge surcharge (:class:`ThicknessDefault`, :class:`RegulatoryTargets`).

*the composition rule* — several measures on one element add their resistances and the result is
written once (:class:`UValueComposer`, requirement M2).

*whether the measures make sense on this building* — two measures describing different
constructions of one element cannot both be applied (:class:`ExclusivityTable`), and a measure for
a construction the building does not have is refused (:class:`FitToBuilding`, decision Q3).

Example::

    current = InventoryThenTabula(inventory)
    u_old = current.u_value(ThermalElement.FACADE)               # 2.4 from the TABULA row
    thickness = ThicknessDefault.for_target(u_old, 0.0355, 0.35) # 80 mm
    UValueComposer.compose(u_old, [0.080 / 0.0355])              # 0.34 W/m2K
"""

import csv
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Dict, FrozenSet, Iterable, Optional, Protocol, Sequence, Tuple
import json

from hisim import utils
from hisim.renovisor.inventory import Inventory
from hisim.renovisor.reasons import ReasonCode, RefusalDetail
from hisim.renovisor.tabula_ie import TabulaLookupError, select_building_code
from hisim.renovisor.vocabulary import (
    FloorConstruction,
    RetrofitStatus,
    ThermalElement,
    VocabularyLookup,
    WallConstruction,
)


class EnvelopePaths:
    """The inventory paths of the envelope facts the measures read and write.

    One place for them so that the resolver, the U-value source and the fit check all address the
    same field. The contract's ``envelope_details`` block names each element's U-value after the
    element itself, which is what :meth:`u_value_path` builds.
    """

    #: The block holding one U-value and one area per thermal element.
    ENVELOPE_DETAILS: ClassVar[str] = "building_config.envelope_details"

    #: The block holding the building's general facts, including the two construction types.
    GENERAL: ClassVar[str] = "building_config.general"

    #: How the building was built, per decision Q3; not in the vendored contract yet.
    FLOOR_CONSTRUCTION: ClassVar[str] = "building_config.general.floor_construction"
    WALL_CONSTRUCTION: ClassVar[str] = "building_config.general.wall_construction"

    @classmethod
    def u_value_path(cls, element: ThermalElement) -> str:
        """Return the inventory path of one element's U-value.

        Args:
            element: One of the five thermal elements.

        Returns:
            E.g. ``building_config.envelope_details.roof_u_value_in_watt_per_m2_per_kelvin``.
        """
        return f"{cls.ENVELOPE_DETAILS}.{element.value.lower()}_u_value_in_watt_per_m2_per_kelvin"


class CurrentUValues(Protocol):
    """The U-value an element has before any measure is applied.

    A protocol rather than a class so that a test can hand the resolver a fixed set of numbers
    without a TABULA lookup, and so that a later source (a measured value, a BER assessment) can
    be added without touching the resolver.
    """

    def u_value(self, element: ThermalElement) -> float:
        """Return the element's current U-value in W/(m2·K)."""
        ...

    def source_of(self, element: ThermalElement) -> str:
        """Return a short phrase naming where that U-value came from, for the report."""
        ...


class TabulaUValues:
    """The U-values of one TABULA archetype row, read from the processed typology table.

    TABULA is the European building typology whose Irish rows HiSim already ships for the
    ``Building`` component. Its ``U_Actual_<Element>_1`` columns are the U-values of the archetype
    as built, which is the best available estimate for a dwelling whose survey did not measure
    them.

    Example::

        TabulaUValues.for_code("IE.N.SFH.04.Gen.ReEx.001.001")[ThermalElement.FACADE]   # 2.4
    """

    #: The TABULA column holding each element's actual U-value. ``Wall`` is the facade and
    #: ``Floor`` the lowest storey; the ``_1`` suffix is TABULA's first (and, for the Irish generic
    #: examples, only) instance of each element.
    COLUMN_BY_ELEMENT: ClassVar[Dict[ThermalElement, str]] = {
        ThermalElement.ROOF: "U_Actual_Roof_1",
        ThermalElement.FACADE: "U_Actual_Wall_1",
        ThermalElement.FLOOR: "U_Actual_Floor_1",
        ThermalElement.WINDOW: "U_Actual_Window_1",
        ThermalElement.DOOR: "U_Actual_Door_1",
    }

    #: The column carrying the typology code each row belongs to.
    CODE_COLUMN: ClassVar[str] = "Code_BuildingVariant"

    #: The processed TABULA table is Latin-1 and semicolon-delimited, with a decimal comma.
    ENCODING: ClassVar[str] = "latin-1"
    DELIMITER: ClassVar[str] = ";"

    @classmethod
    def for_code(cls, building_code: str) -> Dict[ThermalElement, float]:
        """Return the five U-values of one TABULA building code.

        Args:
            building_code: A code as :func:`hisim.renovisor.tabula_ie.select_building_code`
                returns it, e.g. ``"IE.N.SFH.04.Gen.ReEx.001.001"``.

        Returns:
            One U-value per thermal element, in W/(m2·K).

        Raises:
            KeyError: When the table has no row with that code.
        """
        return dict(cls._load_table()[building_code])

    @classmethod
    @lru_cache(maxsize=1)
    def _load_table(cls) -> Dict[str, Dict[ThermalElement, float]]:
        """Parse the processed TABULA CSV into ``code -> element -> U-value`` (cached per process)."""
        table: Dict[str, Dict[ThermalElement, float]] = {}
        with open(utils.HISIMPATH["housing"], encoding=cls.ENCODING) as csv_file:
            for row in csv.DictReader(csv_file, delimiter=cls.DELIMITER):
                code = (row.get(cls.CODE_COLUMN) or "").strip()
                if not code:
                    continue
                table[code] = {
                    element: cls._parse_decimal(row.get(column))
                    for element, column in cls.COLUMN_BY_ELEMENT.items()
                }
        return table

    @classmethod
    def _parse_decimal(cls, raw: object) -> float:
        """Parse a TABULA numeric cell, accepting the German decimal comma; blanks become 0."""
        try:
            return float(str(raw).replace(",", "."))
        except (TypeError, ValueError):
            return 0.0


class InventoryThenTabula:
    """The current U-value per element: the inventory's value when it has one, else TABULA's.

    Decision Q10: a survey that measured an element beats an archetype estimate of it, per element
    and not per building, so a dwelling whose windows were surveyed and whose walls were not gets
    the measured window and the archetype wall. The same rule is used for the base simulation, so
    a base and a variant never disagree about what the building started as.

    Args:
        inventory: The pre-measure inventory. Its location, TABULA type, construction year and
            retrofit status select the archetype row; its ``envelope_details`` supply the
            overrides.

    Raises:
        TabulaLookupError: When no TABULA archetype exists for the country and building type; the
            application turns that into ``Refusal(NO_TABULA_ARCHETYPE)``.
    """

    #: The TABULA refurbishment variant each retrofit status selects.
    VARIANT_BY_RETROFIT_STATUS: ClassVar[Dict[RetrofitStatus, int]] = {
        RetrofitStatus.UNRENOVATED: 1,
        RetrofitStatus.USUAL_REFURB: 2,
        RetrofitStatus.ADVANCED_REFURB: 3,
    }

    #: Used when the inventory states no country; Ireland is the only country the layer serves.
    DEFAULT_COUNTRY_CODE: ClassVar[str] = "IE"

    def __init__(self, inventory: Inventory) -> None:
        """Select the TABULA archetype for this inventory and remember its envelope overrides."""
        self._inventory = inventory
        country = str(inventory.get("location.country_code", self.DEFAULT_COUNTRY_CODE))
        building_type = str(inventory.get(f"{EnvelopePaths.GENERAL}.tabula_building_type", "SFH"))
        construction_year = int(inventory.get(f"{EnvelopePaths.GENERAL}.construction_year", 1900))
        status = VocabularyLookup.member(
            RetrofitStatus, inventory.get(f"{EnvelopePaths.GENERAL}.retrofit_status", "UNRENOVATED")
        )
        self._selection = select_building_code(
            country=country.upper(),
            building_type=building_type.upper(),
            construction_year=construction_year,
            refurbishment_variant=self.VARIANT_BY_RETROFIT_STATUS[status],
        )
        self._tabula_u_values = TabulaUValues.for_code(self._selection.building_code)

    @property
    def building_code(self) -> str:
        """Return the TABULA code the archetype U-values came from."""
        return self._selection.building_code

    @property
    def selection_notes(self) -> Tuple[str, ...]:
        """Return the fallbacks the archetype lookup applied, for the translation report."""
        return tuple(self._selection.notes)

    def u_value(self, element: ThermalElement) -> float:
        """Return the element's current U-value in W/(m2·K).

        Args:
            element: One of the five thermal elements.

        Returns:
            The inventory's ``envelope_details`` value when it carries a non-null one, else the
            TABULA archetype's.
        """
        stated = self._inventory.get(EnvelopePaths.u_value_path(element))
        if isinstance(stated, (int, float)) and not isinstance(stated, bool):
            return float(stated)
        return self._tabula_u_values[element]

    def source_of(self, element: ThermalElement) -> str:
        """Return a short phrase naming where the element's U-value came from."""
        stated = self._inventory.get(EnvelopePaths.u_value_path(element))
        if isinstance(stated, (int, float)) and not isinstance(stated, bool):
            return "the inventory's envelope_details"
        return f"TABULA row {self._selection.building_code}"


@dataclass(frozen=True)
class RegulatoryTarget:
    """One row of the Irish target table: an element's target U-value and where it comes from.

    Args:
        target_id: The row's id, e.g. ``"pitched_roof_insulated_at_ceiling"``.
        u_value_in_watt_per_m2_per_kelvin: The target U-value.
        description: Which construction the row applies to, in one sentence.
        source: The document the number is taken from.
        status: Whether the row has been verified; every row is ``PROVISIONAL`` today.
    """

    target_id: str
    u_value_in_watt_per_m2_per_kelvin: float
    description: str
    source: str
    status: str


class RegulatoryTargets:
    """The target U-values per element for Ireland, from ``data/regulatory_u_values_IE.json``.

    They are what decision Q11's thickness rule aims at: an insulation layer whose thickness the
    request does not state is made just thick enough to reach the target for that element. The
    table is hand-written and every row carries its source and a status, because a regulatory
    number that drifts silently is a wrong result that looks right.

    Example::

        RegulatoryTargets.load().u_value("wall")   # 0.35
    """

    #: The hand-written table, beside this module.
    TABLE_PATH: ClassVar[Path] = Path(__file__).resolve().parent / "data" / "regulatory_u_values_IE.json"

    def __init__(self, targets: Tuple[RegulatoryTarget, ...]) -> None:
        """Store the rows and index them by id."""
        self._targets = targets
        self._by_id: Dict[str, RegulatoryTarget] = {target.target_id: target for target in targets}

    @classmethod
    def load(cls, table_path: Optional[Path] = None) -> "RegulatoryTargets":
        """Read the table and return it.

        Args:
            table_path: Where to read from; defaults to :attr:`TABLE_PATH`.

        Returns:
            The loaded table.

        Raises:
            ValueError: When the file has no ``targets`` list.
        """
        path = cls.TABLE_PATH if table_path is None else table_path
        document = json.loads(path.read_text(encoding="utf-8"))
        rows = document.get("targets")
        if not isinstance(rows, list):
            raise ValueError(f"{path} has no 'targets' list")
        return cls(
            tuple(
                RegulatoryTarget(
                    target_id=str(row["id"]),
                    u_value_in_watt_per_m2_per_kelvin=float(row["u_value_in_watt_per_m2_per_kelvin"]),
                    description=str(row.get("description", "")),
                    source=str(row.get("source", "")),
                    status=str(row.get("status", "")),
                )
                for row in rows
            )
        )

    def target(self, target_id: str) -> RegulatoryTarget:
        """Return one row by its id.

        Args:
            target_id: The row id, e.g. ``"ground_floor"``.

        Returns:
            The :class:`RegulatoryTarget`.

        Raises:
            KeyError: When the table has no such row. A measure that needs a target the table
                lacks refuses with ``UNDEFINED_MEASURE_BUILDUP`` rather than inventing one.
        """
        return self._by_id[target_id]

    def u_value(self, target_id: str) -> float:
        """Return one row's target U-value in W/(m2·K).

        Raises:
            KeyError: When the table has no such row.
        """
        return self.target(target_id).u_value_in_watt_per_m2_per_kelvin

    def target_ids(self) -> Tuple[str, ...]:
        """Return every row id, in file order."""
        return tuple(target.target_id for target in self._targets)

    def describe(self, target_id: str) -> str:
        """Return one line stating a row's value, its source and its status.

        Used on the translation report's ``DEFAULTED`` line for a thickness, so the reader sees
        the number the layer was sized to reach and where it comes from.

        Args:
            target_id: The row id.

        Returns:
            E.g. ``"0.35 W/m2K (Building Regulations TGD L Dwellings 2011, Table 5, material
            alterations) [PROVISIONAL - verify against the current TGD L revision]"``.

        Raises:
            KeyError: When the table has no such row.
        """
        row = self.target(target_id)
        return (
            f"{row.u_value_in_watt_per_m2_per_kelvin} W/m2K ({row.source}) [{row.status}]"
        )


class ThicknessDefault:
    """The thickness an insulation layer gets when the request does not state one (decision Q11).

    The rule: the thinnest layer that brings the element from its current U-value to the
    regulatory target, rounded up to the next 10 mm. From ``U_new = 1/(1/U_old + d/λ)`` and
    ``U_new = U_target`` follows ``d = λ · (1/U_target − 1/U_old)``.

    Example: a solid wall at 1.5 W/(m2·K) insulated with EPS (λ = 0.0355 W/(m·K)) to the 0.35
    target needs ``0.0355 · (1/0.35 − 1/1.5) = 0.0778 m``, so 80 mm.

    No thermal-bridge surcharge is applied, and the translation report says so on the line: the
    layer is sized for the plain one-dimensional element, which slightly overstates what the
    finished wall achieves.
    """

    #: Thicknesses are rounded up to this step, the increment insulation is sold in.
    STEP_IN_MM: ClassVar[int] = 10

    #: Millimetres per metre: λ is stated per metre while thicknesses are stated in millimetres.
    MM_PER_M: ClassVar[float] = 1000.0

    @classmethod
    def for_target(
        cls,
        current_u_value_in_watt_per_m2_per_kelvin: float,
        conductivity_in_watt_per_meter_per_kelvin: float,
        target_u_value_in_watt_per_m2_per_kelvin: float,
    ) -> int:
        """Return the default thickness in millimetres.

        Args:
            current_u_value_in_watt_per_m2_per_kelvin: The element's U-value before the measure.
            conductivity_in_watt_per_meter_per_kelvin: λ of the chosen insulation material.
            target_u_value_in_watt_per_m2_per_kelvin: The regulatory target for the element.

        Returns:
            The thickness, rounded up to the next 10 mm. ``0`` when the element already meets the
            target, which is honest rather than useful: the request should then state a thickness.

        Raises:
            ValueError: When the conductivity or either U-value is not positive, which would make
                the formula meaningless.
        """
        if conductivity_in_watt_per_meter_per_kelvin <= 0:
            raise ValueError("the thermal conductivity has to be positive")
        if current_u_value_in_watt_per_m2_per_kelvin <= 0 or target_u_value_in_watt_per_m2_per_kelvin <= 0:
            raise ValueError("both U-values have to be positive")
        required_resistance = (
            1.0 / target_u_value_in_watt_per_m2_per_kelvin - 1.0 / current_u_value_in_watt_per_m2_per_kelvin
        )
        if required_resistance <= 0:
            return 0
        thickness_in_mm = required_resistance * conductivity_in_watt_per_meter_per_kelvin * cls.MM_PER_M
        return int(math.ceil(thickness_in_mm / cls.STEP_IN_MM) * cls.STEP_IN_MM)


class UValueComposer:
    """Composes one element's U-value from its baseline and every resistance added to it.

    ``U_new = 1 / (1/U_baseline + Σ d/λ)``, in one place, so that two insulation layers on one wall
    add up instead of the second overwriting the first (requirement M2). Replacement measures set
    the baseline instead of adding to it, which is why the baseline is a parameter here rather
    than always the building's original value.

    Example::

        UValueComposer.compose(2.4, [0.08 / 0.0355, 0.06 / 0.04])   # two layers on one wall
    """

    @classmethod
    def compose(cls, baseline_u_value_in_watt_per_m2_per_kelvin: float, resistances: Iterable[float]) -> float:
        """Return the composed U-value in W/(m2·K).

        Args:
            baseline_u_value_in_watt_per_m2_per_kelvin: The element's U-value before the layers,
                which is the replacement value when a measure replaced the element.
            resistances: The added thermal resistances in m2·K/W, one per insulation layer.

        Returns:
            The composed U-value.

        Raises:
            ValueError: When the baseline U-value is not positive or a resistance is negative.
        """
        if baseline_u_value_in_watt_per_m2_per_kelvin <= 0:
            raise ValueError("the baseline U-value has to be positive")
        total_resistance = 1.0 / baseline_u_value_in_watt_per_m2_per_kelvin
        for resistance in resistances:
            if resistance < 0:
                raise ValueError("a thermal resistance cannot be negative")
            total_resistance += resistance
        return 1.0 / total_resistance

    @classmethod
    def resistance(cls, thickness_in_mm: int, conductivity_in_watt_per_meter_per_kelvin: float) -> float:
        """Return the thermal resistance of one layer in m2·K/W.

        Args:
            thickness_in_mm: The layer's thickness.
            conductivity_in_watt_per_meter_per_kelvin: λ of its material.

        Returns:
            ``d / λ`` with ``d`` converted from millimetres to metres.

        Raises:
            ValueError: When the conductivity is not positive or the thickness is negative.
        """
        if conductivity_in_watt_per_meter_per_kelvin <= 0:
            raise ValueError("the thermal conductivity has to be positive")
        if thickness_in_mm < 0:
            raise ValueError("a layer thickness cannot be negative")
        return (thickness_in_mm / 1000.0) / conductivity_in_watt_per_meter_per_kelvin


class ExclusivityTable:
    """Which measures on one element describe constructions that cannot both be true.

    A floor is either a slab on ground, a suspended timber floor, or a floor over a basement; a
    roof is either insulated as a warm deck or at its rafters and ceiling. A package naming
    measures from two of those groups describes two different buildings, which decision Q3 says to
    refuse rather than compose. Measures within one group do compose: rafter insulation and
    rolled-out attic insulation are two layers of one loft.

    The facade has no groups: external and internal insulation of one wall are two real layers and
    add up.
    """

    BY_ELEMENT: ClassVar[Dict[ThermalElement, Tuple[FrozenSet[str], ...]]] = {
        ThermalElement.FLOOR: (
            frozenset(
                {
                    "BASEMENT_CEILING_INSULATION",
                    "BASEMENT_INTERNAL_INSULATION",
                    "BASEMENT_EXTERNAL_INSULATION",
                }
            ),
            frozenset({"SOLID_GROUND_FLOOR_INSULATION"}),
            frozenset({"SUSPENDED_GROUND_FLOOR_INSULATION"}),
        ),
        ThermalElement.ROOF: (
            frozenset({"WARM_ROOF_INSULATION"}),
            frozenset(
                {
                    "RAFTER_INSULATION",
                    "ROLLED_OUT_ATTIC_INSULATION",
                    "TOP_FLOOR_CEILING_INSULATION",
                }
            ),
        ),
    }

    @classmethod
    def check(cls, measure_ids: Sequence[str]) -> Tuple[RefusalDetail, ...]:
        """Return a refusal for every element whose measures span two construction groups.

        Args:
            measure_ids: The measure ids the package names, in package order.

        Returns:
            One :class:`~hisim.renovisor.reasons.RefusalDetail` with
            ``CONTRADICTORY_MEASURES`` per offending element; empty when the package is coherent.
        """
        refusals = []
        for element, groups in cls.BY_ELEMENT.items():
            hit = [
                sorted(group & set(measure_ids))
                for group in groups
                if group & set(measure_ids)
            ]
            if len(hit) > 1:
                described = " and ".join(", ".join(names) for names in hit)
                refusals.append(
                    RefusalDetail(
                        reason=ReasonCode.CONTRADICTORY_MEASURES,
                        path="package.measures",
                        detail=(
                            f"{described} describe different constructions of the {element.value.lower()}; "
                            "a building has one of them"
                        ),
                    )
                )
        return tuple(refusals)


class FitToBuilding:
    """Which construction a measure needs the building to have.

    Cavity wall insulation needs a cavity; a solid-floor measure needs a solid floor. The
    inventory facts that say so are the two decision Q3 adds, and the vendored contract does not
    carry them yet — so the check runs only when the inventory states the fact, and a request that
    does not state it is composed on trust (the working assumption of challenge C8).
    """

    #: Measure id -> (inventory path of the fact, the constructions the measure fits).
    BY_MEASURE: ClassVar[Dict[str, Tuple[str, Tuple[str, ...]]]] = {
        "CAVITY_WALL_INSULATION": (
            EnvelopePaths.WALL_CONSTRUCTION,
            (WallConstruction.CAVITY.value,),
        ),
        "SOLID_GROUND_FLOOR_INSULATION": (
            EnvelopePaths.FLOOR_CONSTRUCTION,
            (FloorConstruction.SLAB_ON_GROUND.value,),
        ),
        "SUSPENDED_GROUND_FLOOR_INSULATION": (
            EnvelopePaths.FLOOR_CONSTRUCTION,
            (FloorConstruction.SUSPENDED_TIMBER.value,),
        ),
        "BASEMENT_CEILING_INSULATION": (
            EnvelopePaths.FLOOR_CONSTRUCTION,
            (FloorConstruction.OVER_UNHEATED_BASEMENT.value, FloorConstruction.OVER_HEATED_BASEMENT.value),
        ),
        "BASEMENT_INTERNAL_INSULATION": (
            EnvelopePaths.FLOOR_CONSTRUCTION,
            (FloorConstruction.OVER_UNHEATED_BASEMENT.value, FloorConstruction.OVER_HEATED_BASEMENT.value),
        ),
        "BASEMENT_EXTERNAL_INSULATION": (
            EnvelopePaths.FLOOR_CONSTRUCTION,
            (FloorConstruction.OVER_UNHEATED_BASEMENT.value, FloorConstruction.OVER_HEATED_BASEMENT.value),
        ),
    }

    @classmethod
    def check(cls, measure_ids: Sequence[str], inventory: Inventory) -> Tuple[RefusalDetail, ...]:
        """Return a refusal for every measure the building's recorded construction rules out.

        Args:
            measure_ids: The measure ids the package names.
            inventory: The pre-measure inventory.

        Returns:
            One :class:`~hisim.renovisor.reasons.RefusalDetail` with
            ``MEASURE_DOES_NOT_FIT_BUILDING`` per offending measure; empty when every measure fits
            or the inventory does not state the fact.
        """
        refusals = []
        for measure_id in measure_ids:
            requirement = cls.BY_MEASURE.get(measure_id)
            if requirement is None:
                continue
            path, allowed = requirement
            stated = inventory.get(path)
            if stated is None:
                continue
            if str(stated).strip().upper() not in allowed:
                refusals.append(
                    RefusalDetail(
                        reason=ReasonCode.MEASURE_DOES_NOT_FIT_BUILDING,
                        path=path,
                        detail=(
                            f"{measure_id} needs {' or '.join(allowed)}; the inventory states "
                            f"'{stated}'"
                        ),
                        measure_id=measure_id,
                    )
                )
        return tuple(refusals)


class TabulaArchetype:
    """Turns a TABULA lookup failure into the refusal the caller sees.

    Kept beside the U-value source because it is the same lookup: the archetype that supplies the
    envelope is also the archetype the base simulation runs on, so a country or building type
    without one is a refusal, not a fallback (challenge C20).
    """

    @classmethod
    def refusal_for(cls, error: TabulaLookupError) -> RefusalDetail:
        """Return the ``NO_TABULA_ARCHETYPE`` refusal for a failed archetype lookup."""
        return RefusalDetail(
            reason=ReasonCode.NO_TABULA_ARCHETYPE,
            path=f"{EnvelopePaths.GENERAL}.tabula_building_type",
            detail=str(error),
        )
