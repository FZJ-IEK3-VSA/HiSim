"""The composed-twin table (``hisim/energy_system/assemblies/twins.py``) and the renaming of a KPI address."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hisim.energy_system.assemblies.twins import COMPOSED_TWINS, UnmappedMemberError, rename_address
from hisim.postprocessing.kpi_computation.kpi_address import KpiAddress
from hisim.postprocessing.kpi_computation.kpi_structure import KpiAddressStep, KpiSource

ENERGY_SYSTEMS = Path(__file__).resolve().parents[2] / "energy_systems"
HEATPUMP = COMPOSED_TWINS["household_heatpump_building_sizer"]


@pytest.mark.assemblies
def test_the_table_lists_exactly_the_committed_composed_files() -> None:
    """Catches a composed file the golden gate never runs, or an entry whose files are missing.

    ``golden_matrix.py --with-composed`` counts a composed run wherever the file exists, and ``golden_check.py``
    runs one wherever the table has an entry: the two agree only while the table lists exactly the files.
    """
    committed = {path.name for path in ENERGY_SYSTEMS.glob("*.composed.energy_system.yaml")}
    assert {twin.composed for twin in COMPOSED_TWINS.values()} == committed
    for twin in COMPOSED_TWINS.values():
        assert (ENERGY_SYSTEMS / twin.twin).exists(), f"{twin.stem}: no twin {twin.twin}"


@pytest.mark.assemblies
def test_the_rename_map_names_every_import_of_the_composed_file() -> None:
    """Catches an import whose members the map forgets: their KPIs would fail the golden gate by name only."""
    for twin in COMPOSED_TWINS.values():
        imports = set(yaml.safe_load((ENERGY_SYSTEMS / twin.composed).read_text(encoding="utf-8"))["imports"])
        assert {address.split("-")[0] for address in twin.rename} == imports, twin.stem


@pytest.mark.assemblies
def test_the_table_cannot_be_changed_through_an_entry() -> None:
    """Catches a caller editing a rename map in place, which would change every later reader's comparison."""
    with pytest.raises(TypeError):
        HEATPUMP.rename["heating-HeatPump"] = "Other"  # type: ignore[index]


def member_source(name: str, member: str, import_key: str) -> KpiSource:
    """The source of an assembly member, as a composed run reports it."""
    return KpiSource(
        import_key=import_key,
        path=(KpiAddressStep(import_key=import_key, instance=None),),
        member=member,
        assembly="heating/air_source_heat_pump",
        name=name,
    )


@pytest.mark.assemblies
def test_a_member_kpi_is_addressed_as_the_twins_site_component() -> None:
    """Catches a renamed KPI keeping the member's import, path or assembly, which the golden's leaf does not carry."""
    address = KpiAddress("BUI1", "Heat Pump For Space Heating", "Heating hours of SH heat pump",
                         member_source("heating-HeatPump", "HeatPump", "heating"))
    renamed = rename_address(address, HEATPUMP.rename)
    assert renamed == KpiAddress(
        "BUI1",
        "Heat Pump For Space Heating",
        "Heating hours of SH heat pump",
        KpiSource(name="MoreAdvancedHeatPumpHPLib", member="MoreAdvancedHeatPumpHPLib"),
    )
    assert renamed.key == "Heating hours of SH heat pump (MoreAdvancedHeatPumpHPLib)"


@pytest.mark.assemblies
def test_a_port_name_part_in_a_kpi_name_is_renamed_and_a_site_source_kept() -> None:
    """The energy manager's priority quotes the member's port-name part; a site component keeps its source."""
    site = KpiSource(name="UTSPConnector", member="UTSPConnector")
    name = "Priority for ElectricalInputPowerSHFromheating_HeatPump"
    address = KpiAddress("BUI1", "Energy Management System", name, site)
    assert rename_address(address, HEATPUMP.rename) == KpiAddress(
        "BUI1", "Energy Management System", "Priority for ElectricalInputPowerSHFromMoreAdvancedHeatPumpHPLib", site
    )


@pytest.mark.assemblies
def test_a_member_the_map_does_not_name_is_refused() -> None:
    """Catches an incomplete rename map passing as a KPI that is merely missing and new."""
    address = KpiAddress("BUI1", "General", "x", member_source("heating-Pump", "Pump", "heating"))
    with pytest.raises(UnmappedMemberError, match="heating-Pump"):
        rename_address(address, HEATPUMP.rename)
