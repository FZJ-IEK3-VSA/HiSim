"""The building declares its energy ports and closes in every step of the households' winter and summer weeks.

Bead hisim-energy-sankeys-9uoo.6.

Complete Sankey spec, §5.4 (``roadmap/complete_energy_sankeys_spec.md``). The building takes the space heating its
distribution system delivers (``SPACE_HEATING_HEAT``), the sun through its windows (``SOLAR``, from the sun) and the
internal gains of the occupancy (``ROOM_HEAT``, paired with the occupancy's device heat and body heat); a cooling
device would take heat out of it (``COOLING``) and a CHP would heat it, neither of which these households have. It
loses heat to the outside by ventilation, through windows and doors, through the opaque elements and through the
share of the gains on the window surfaces, and its thermal mass stores the rest (all ``ROOM_HEAT``). The 5R1C model
closes over these ports to float precision; airing, which only overwrites the published air temperature, moves no
heat and is drawn nowhere (owner decision Q-E).

The second January week (heating, little sun) and the first full week of July (sun, airing) at 900 s of the
heat-pump and the gas household, each run once per module with every output written to CSV: the run finishing proves
every declared balance closes (the check fails the run otherwise); the tests read the report, the Sankey graph and
the building's columns.
"""

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, Set, Tuple

import pandas as pd
import pytest

from tests.test_energy_balance_electricity import overall_links
from tests.test_energy_balance_generators import balance_entry, port_roles, run_household


class Expected:
    """What the households must show: the weeks, the building's ports with their peers and its links."""

    #: The two households of the spec's §9, row PR 4.
    TWINS: ClassVar[Tuple[str, ...]] = ("household_gas_building_sizer", "household_heatpump_building_sizer")

    #: The weeks run, as (name, first day, day after the last).
    WEEKS: ClassVar[Dict[str, Tuple[str, str]]] = {
        "january": ("2021-01-10", "2021-01-17"),
        "july": ("2021-07-05", "2021-07-12"),
    }

    #: The building's ports as ``output -> (role, carrier, peer kind, peer)``.
    PORTS: ClassVar[Dict[str, Tuple[str, str, str, str]]] = {
        "HeatReceivedInWatt": ("in", "space_heating_heat", "component", "HeatDistributionSystem"),
        "HeatReceivedFromChpInWatt": ("in", "space_heating_heat", "unlinked", "unlinked"),
        "HeatRemovedByCoolingInWatt": ("out", "cooling", "component", "HeatDistributionSystem"),
        "SolarGainThroughWindows": ("in", "solar", "boundary", "sun"),
        "InternalHeatGainsFromOccupancy": ("in", "room_heat", "component", "UTSPConnector"),
        "VentilationLossInWatt": ("loss", "room_heat", "boundary", "outdoors"),
        "TransmissionLossWindowsAndDoorsInWatt": ("loss", "room_heat", "boundary", "outdoors"),
        "TransmissionLossOpaqueInWatt": ("loss", "room_heat", "boundary", "outdoors"),
        "GainsLostThroughWindowsInWatt": ("loss", "room_heat", "boundary", "outdoors"),
        "ThermalMassStoredEnergyIncreaseInWattHour": ("stored_change", "room_heat", "store", "Building · stored"),
    }

    #: The ports that bring energy into the building, leave it, are lost or stored, for the per-step residual.
    INPUTS: ClassVar[Tuple[str, ...]] = (
        "HeatReceivedInWatt",
        "HeatReceivedFromChpInWatt",
        "SolarGainThroughWindows",
        "InternalHeatGainsFromOccupancy",
    )
    OUTPUTS: ClassVar[Tuple[str, ...]] = ("HeatRemovedByCoolingInWatt",)
    LOSSES: ClassVar[Tuple[str, ...]] = (
        "VentilationLossInWatt",
        "TransmissionLossWindowsAndDoorsInWatt",
        "TransmissionLossOpaqueInWatt",
        "GainsLostThroughWindowsInWatt",
    )

    #: Links of the connected diagram that end or start at the building, as (carrier, source, target).
    LINKS: ClassVar[Set[Tuple[str, str, str]]] = {
        ("solar", "sun", "Building"),
        ("room_heat", "UTSPConnector", "Building"),
        ("room_heat", "Building", "Building · VentilationLossInWatt"),
        ("room_heat", "Building · VentilationLossInWatt", "outdoors"),
        ("room_heat", "Building", "Building · TransmissionLossWindowsAndDoorsInWatt"),
        ("room_heat", "Building", "Building · TransmissionLossOpaqueInWatt"),
        ("room_heat", "Building", "Building · GainsLostThroughWindowsInWatt"),
    }


@pytest.fixture(name="week", scope="module", params=[(twin, week) for twin in Expected.TWINS for week in Expected.WEEKS])
def fixture_week(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Tuple[str, str, Path]:
    """One household's week at 900 s with every output written to CSV, run once for every test of this module.

    Returns:
        The setup's name, the week's name and the result directory.
    """
    setup, week = request.param
    start, end = Expected.WEEKS[week]
    results = run_household(
        tmp_path_factory.mktemp(f"{setup}_{week}"),
        setup,
        start=start,
        end=end,
        post_processing_options=("EXPORT_ENERGY_BALANCE", "EXPORT_TO_CSV"),
    )
    return setup, week, results


def building_column(results: Path, output: str) -> pd.Series:
    """One output of the building per step, read from the CSV the run wrote."""
    return pd.read_csv(results / f"Building_{output}.csv", index_col=0).iloc[:, 0]


def residual_in_kilowatt_hours_per_step(results: Path) -> pd.Series:
    """The building's balance per step from its own columns, in kWh: in minus out, losses and the stored change.

    A 900 s step turns a power in W into kWh by 0.25 / 1000, the stored change is published in Wh.
    """
    hours_per_step = 0.25

    def total(outputs: Tuple[str, ...]) -> pd.Series:
        return sum((building_column(results, output) for output in outputs), pd.Series(0.0)) * hours_per_step / 1000.0

    stored = building_column(results, "ThermalMassStoredEnergyIncreaseInWattHour") / 1000.0
    return total(Expected.INPUTS) - total(Expected.OUTPUTS) - total(Expected.LOSSES) - stored


@pytest.mark.extendedbase2
def test_the_building_declares_its_ports_and_closes_in_every_step(week: Tuple[str, str, Path]) -> None:
    """The building declares the ports of §5.4 with their peers and closes to 1e-9 kWh in every step.

    Catches: a loss against the aired instead of the solved air temperature, a stored change that is not the
    persistent one (bug B1 of the spec), and gains on the window surfaces that reach no port (B2).
    """
    _, _, results = week
    entry = balance_entry(results, "Building")
    assert entry["verdict"] == "closes" and entry["worst_step"] is None and entry["failing_steps"] == 0
    assert abs(entry["residual_kwh"]) <= 1e-9
    assert port_roles(entry) == {output: (role, carrier) for output, (role, carrier, _, _) in Expected.PORTS.items()}
    peers = {port["output"]: (port["peer_kind"], port["peer"]) for port in entry["ports"]}
    assert peers == {output: (kind, peer) for output, (_, _, kind, peer) in Expected.PORTS.items()}
    assert residual_in_kilowatt_hours_per_step(results).abs().max() <= 1e-9


@pytest.mark.extendedbase2
def test_the_building_s_flows_are_physical(week: Tuple[str, str, Path]) -> None:
    """The house is heated in January and aired in July; nothing cools it and no CHP heats it.

    Catches: a heating port that books nothing in winter, losses with their sign turned, and gains on the window
    surfaces that are not the model's share of about 0.8 % of the gains they come from.
    """
    _, week_name, results = week
    totals = {port["output"]: port["total_kwh"] for port in balance_entry(results, "Building")["ports"]}
    assert totals["HeatRemovedByCoolingInWatt"] == 0.0 and totals["HeatReceivedFromChpInWatt"] == 0.0
    assert totals["SolarGainThroughWindows"] > 0.0 and totals["InternalHeatGainsFromOccupancy"] > 0.0
    assert all(totals[loss] > 0.0 for loss in Expected.LOSSES)
    lost_share = totals["GainsLostThroughWindowsInWatt"] / (
        0.5 * totals["InternalHeatGainsFromOccupancy"] + totals["SolarGainThroughWindows"]
    )
    assert 0.005 < lost_share < 0.01
    if week_name == "january":
        assert totals["HeatReceivedInWatt"] > totals["SolarGainThroughWindows"]
    else:
        assert building_column(results, "OpenWindow").sum() > 0, "the occupants air the house in July"


@pytest.mark.extendedbase2
def test_the_building_is_linked_to_the_occupancy_the_distribution_system_and_outdoors(week: Tuple[str, str, Path]) -> None:
    """The building's internal gains, its space heating and its losses are linked to their peers.

    The occupancy's two room-heat outputs pair with the building's internal gains, the distribution system's heat with
    its space heating; the cooling port, which moves nothing, makes no link; the losses go outdoors.

    Catches: internal gains paired with one of the occupancy's outputs only, and an idle cooling port that fails the
    run as a one-sided booking with a distribution system that declares no cooling.
    """
    _, week_name, results = week
    report: Dict[str, Any] = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
    links = {(link["sender"], link["receiver"], link["carrier"]): link for link in report["links"]}
    assert links[("UTSPConnector", "Building", "room_heat")]["verdict"] == "closes"
    if week_name == "january":
        assert links[("HeatDistributionSystem", "Building", "space_heating_heat")]["verdict"] == "closes"
    assert not [key for key in links if "cooling" in key]
    diagram_links = overall_links(results)
    assert Expected.LINKS <= diagram_links, sorted(Expected.LINKS - diagram_links)
    nodes = {node for _, source, target in diagram_links for node in (source, target)}
    assert "undeclared: Building" not in nodes and "unaccounted" not in nodes
    assert not report["unlinked"], "the idle CHP port is no unlinked port"


@pytest.mark.extendedbase2
def test_only_the_weather_is_undeclared_so_the_meter_check_is_reported(week: Tuple[str, str, Path]) -> None:
    """With the building declared, the weather is the households' last undeclared component (its exemption is PR 5d).

    The electricity meter's check therefore stays reported and not enforced (owner decision Q-H); it agrees in every
    step all the same.
    """
    _, _, results = week
    report: Dict[str, Any] = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
    assert [entry["component"] for entry in report["undeclared"]] == ["Weather"]
    meter = {meter["meter"]: meter for meter in report["meters"]}["ElectricityMeter"]
    assert meter["verdict"] == "agrees" and meter["enforced"] is False
