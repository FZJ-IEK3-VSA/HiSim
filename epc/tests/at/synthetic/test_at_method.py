"""T-5, T-7, T-9 for the Austrian engine: the whole computation on synthetic tables, its refusals, the command line.

DISCLAIMER - SYNTHETIC TEST DATA. Every table value in this module is invented to test the code,
and any building in it is a placeholder or a HiSim test record: no value comes from an OENORM
table, and no number computed here is the heating demand of any real building. Never use them for
a study or a real calculation. The standards themselves (OENORM B 8110-5, B 8110-6-1, B 8110-6-2,
H 5050-1, H 5056-1, edition 2024-03) can be purchased from Austrian Standards International:
https://www.austrian-standards.at

The tables come from :mod:`epc.tests.at.synthetic.at_tables` and the building is a fixture written by
HiSim with a site added in memory; no value of a licensed table appears here (R8.5).
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest

from epc.__main__ import main
from epc.engines import registry
from epc.engines.at import indicators
from epc.engines.at.method import AustrianEngine
from epc.model.building import BuildingDescription, Site
from epc.model.errors import EpcError
from epc.model.result import COMMON_CORE, SITE_CLIMATE_KEY, RunInfo
from epc.tests import support
from epc.tests.at.synthetic.at_tables import PROSE_CONSTANTS, synthetic_tables, write
from epc.tests.at.synthetic.test_at_terms import _building

pytestmark = pytest.mark.base


@pytest.fixture(name="engine")
def fixture_engine(tmp_path: Path) -> AustrianEngine:
    """The Austrian engine on the synthetic tables."""
    return AustrianEngine(data_folder=write(tmp_path, synthetic_tables()))


def _sited(**changes: object) -> BuildingDescription:
    """The configured fixture building at a synthetic site, changed by keyword."""
    return dataclasses.replace(_building(site=Site("W", 150.0)), **changes)  # type: ignore[arg-type]


def test_austria_is_registered() -> None:
    """The registry hands out the Austrian engine for AT (R3)."""
    assert isinstance(registry.for_country("AT"), AustrianEngine)


def test_the_engine_fills_the_core_the_site_key_and_its_assumptions(engine: AustrianEngine) -> None:
    """A full computation: the common core, HWB_SK, every default as an assumption, the table digests (I-7)."""
    result = engine.compute(_sited())
    result.run = RunInfo("record", "building", "AT", "at", engine.edition, engine.climate_basis, "epc", "now")
    written = result.to_dict()
    assert set(COMMON_CORE) | {SITE_CLIMATE_KEY} <= set(written["indicators"])
    bgf = 115.0 / PROSE_CONSTANTS["reference_area_share"]
    assert written["indicators"]["indicator_reference_area"]["value"] == bgf
    keys = [assumption["key"] for assumption in written["assumptions"]]
    for key in ("gross_floor_area", "gross_volume", "construction", "glass_share", "shading_factor",
                "thermal_bridges", "ventilation", "hot_water_system"):
        assert key in keys
    tables = engine._tables  # pylint: disable=protected-access
    assert tables is not None and set(written["provenance"]["tables"]) == set(tables.digests)
    assert "Q_TW,beh" in written["indicators"][SITE_CLIMATE_KEY]["note"]
    assert written["terms"]["annual"]["recoverable_hot_water_losses"]["value"] == sum(
        written["terms"]["monthly"]["recoverable_hot_water_losses"]["values"])
    for climate in ("reference_climate", "site_climate"):
        demand = written["terms"]["monthly"][f"heating_demand_{climate}"]["values"]
        assert written["terms"]["annual"][f"heating_demand_{climate}"]["value"] == sum(demand)
        assert written["indicators"][f"heating_demand_{climate}"]["value"] == sum(demand) / bgf


def test_the_tables_are_loaded_once(engine: AustrianEngine) -> None:
    """load_data is idempotent; compute loads them when nobody did."""
    engine.load_data()
    tables = engine._tables  # pylint: disable=protected-access
    engine.compute(_sited())
    assert engine._tables is tables  # pylint: disable=protected-access


@pytest.mark.parametrize(
    "site, error_id, field",
    [
        (Site(None, 150.0), "E-09", "climate_region"),
        (Site("W", None), "E-09", "elevation_in_m"),
        (Site("X", 150.0), "E-10", "climate_region"),
        (Site("W", -1.0), "E-10", "elevation_in_m"),
    ],
)
def test_the_site_fields_are_checked(engine: AustrianEngine, site: Site, error_id: str, field: str) -> None:
    """A missing site field refuses with E-09, an inadmissible one with E-10, each naming the field (R2.2)."""
    with pytest.raises(EpcError) as refusal:
        engine.compute(_sited(site=site))
    assert refusal.value.error_id == error_id
    assert f"'{field}'" in refusal.value.message


def test_a_building_without_envelope_area_is_refused(engine: AustrianEngine) -> None:
    """E-18 instead of a division by zero."""
    elements = tuple(dataclasses.replace(element, area_in_m2=0.0) for element in _sited().elements)
    with pytest.raises(EpcError) as refusal:
        engine.compute(_sited(elements=elements, windows=()))
    assert refusal.value.error_id == "E-18"


def test_the_heating_degree_days_follow_eq_4_with_the_days() -> None:
    """Eq. (4): (θ_i,h − θ_e,j) · d_j summed over the months at or below the limit of the tables (Q-D2)."""
    limit = PROSE_CONSTANTS["heating_degree_day_limit"]
    temperatures = (0.0, 2.0, 6.0, 10.0, limit, 16.0, 18.0, 18.0, limit + 0.5, 10.0, 5.0, 1.0)
    days = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    expected = sum((20.5 - t) * d for t, d in zip(temperatures, days) if t <= limit)
    assert indicators.heating_degree_days(PROSE_CONSTANTS, 20.5, temperatures) == expected


@pytest.mark.parametrize(
    "complete, status", [(True, "ready"), (False, "tables not usable (E-11): see epc/data/README.md")]
)
def test_countries_states_whether_the_austrian_tables_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], complete: bool, status: str
) -> None:
    """D42: ``ready`` on a complete table set, E-11 on an empty folder, whatever tables lie locally."""
    folder = write(tmp_path, synthetic_tables()) if complete else tmp_path

    class _Engine(AustrianEngine):
        """The Austrian engine reading this test's folder."""

        def __init__(self) -> None:
            super().__init__(data_folder=folder)

    monkeypatch.setitem(registry.ENGINES_BY_COUNTRY, "AT", _Engine)
    assert main(["countries"]) == 0
    assert capsys.readouterr().out == f"AT\t{AustrianEngine.edition}\t{AustrianEngine.climate_basis}\t{status}\n"


def test_the_command_line_lists_austria_and_refuses_a_record_without_site() -> None:
    """The command line names AT; a record without site refuses (E-09), without local tables E-11 (T-9)."""
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    listed = subprocess.run([sys.executable, "-m", "epc", "countries"], cwd=support.REPOSITORY_ROOT, env=environment,
                            capture_output=True, encoding="utf-8", check=False, timeout=120)
    assert listed.returncode == 0 and listed.stdout.startswith("AT\t")
    refused = subprocess.run(
        [sys.executable, "-m", "epc", "run", str(support.FIXTURES / support.CONFIGURED), "--country", "AT", "--out",
         str(support.REPOSITORY_ROOT / "epc" / "results" / "never-written")],
        cwd=support.REPOSITORY_ROOT, env=environment, capture_output=True, encoding="utf-8", check=False, timeout=120,
    )
    assert refused.returncode == 2
    assert "E-09" in refused.stderr or "E-11" in refused.stderr
    assert not (support.REPOSITORY_ROOT / "epc" / "results" / "never-written").exists()
