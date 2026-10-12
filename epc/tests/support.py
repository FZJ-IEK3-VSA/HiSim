"""What the tests share: the fixture records written by HiSim, and dummy engines for the shell.

The fixtures under ``fixtures/`` are files HiSim's building record writer wrote, unedited (AC10).
Every refusal case is built from one of them in memory and written to a temporary directory, so a
fixture never changes. The dummy engines exist only here: they compute placeholders, no norm, and
are registered by the tests that need them (T-7, T-9).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, ClassVar, Dict

import yaml

from epc.inputs.record import RECORD_FILENAME
from epc.model.building import BuildingDescription
from epc.model.errors import EpcError, EpcErrorKind
from epc.model.result import ClimateBasis, EpcResult

#: The folder of the fixture records.
FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: A detached house whose floor HiSim computes with factor 0.5.
FLOOR_FACTOR_HALF = "at_sfh02_floor_factor_half"

#: A detached house with a configured floor area and a configured wall U-value.
CONFIGURED = "at_sfh04_configured"

#: Every fixture record.
FIXTURE_NAMES = (FLOOR_FACTOR_HALF, CONFIGURED)

#: The repository root, from which ``python -m epc`` runs.
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def fixture_document(name: str) -> Dict[str, Any]:
    """A fresh copy of one fixture record as YAML loads it, for changing in memory."""
    loaded: Dict[str, Any] = yaml.safe_load((FIXTURES / name / RECORD_FILENAME).read_text(encoding="utf-8"))
    return copy.deepcopy(loaded)


def write_record(results_dir: Path, document: Dict[str, Any]) -> Path:
    """Writes a record into a results directory, creating it; returns the directory."""
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / RECORD_FILENAME).write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return results_dir


def first_building(document: Dict[str, Any]) -> Dict[str, Any]:
    """The first building entry of a record document."""
    building: Dict[str, Any] = document["buildings"][0]
    return building


class DummyEngine:
    """An engine of the user-assigned country code ZZ with one national climate; placeholders only."""

    country: ClassVar[str] = "ZZ"
    edition: ClassVar[str] = "dummy edition"
    climate_basis: ClassVar[ClimateBasis] = "national"

    def load_data(self) -> None:
        """Has no data."""

    def compute(self, building: BuildingDescription) -> EpcResult:
        """Fills the common core with placeholders and one term of each kind."""
        result = EpcResult(building)
        result.add_indicator("heating_demand_reference_climate", 1.0, "kWh/(m2 a)", cites="dummy", label="HD")
        result.add_indicator("indicator_reference_area", building.conditioned_floor_area_in_m2, "m2")
        result.add_term("dummy_annual_term", 2.0, "kWh/a", cites="dummy")
        result.add_monthly("dummy_monthly_term", [0.0] * 12, "kWh")
        result.assume("dummy_default", "a placeholder", "no norm")
        return result


class CorelessEngine(DummyEngine):
    """The dummy engine returning no common core, which the result refuses on writing (I-7)."""

    def compute(self, building: BuildingDescription) -> EpcResult:
        """Fills nothing."""
        return EpcResult(building)


class SiteEngine(DummyEngine):
    """An engine of code ZY with a site climate, which reads the site's climate region."""

    country: ClassVar[str] = "ZY"
    climate_basis: ClassVar[ClimateBasis] = "reference+site"

    def compute(self, building: BuildingDescription) -> EpcResult:
        """Refuses a building without a climate region (E-09), else fills the core and the site key."""
        if building.site.climate_region is None:
            raise EpcError(
                EpcErrorKind.SITE_FIELD_MISSING,
                name=building.name,
                field="climate_region",
                cc=self.country,
                fields="climate_region",
            )
        result = super().compute(building)
        result.add_indicator("heating_demand_site_climate", 1.0, "kWh/(m2 a)")
        return result
