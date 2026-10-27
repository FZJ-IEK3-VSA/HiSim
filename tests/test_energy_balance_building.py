"""The building declares its energy ports and closes in every step of the households' winter and summer weeks.

Bead hisim-energy-sankeys-9uoo.6.

Complete Sankey spec, §5.4 (``roadmap/complete_energy_sankeys_spec.md``), with decisions D9 and D20 of
``roadmap/fluid_loops_spec.md``. The building takes the space heating its distribution system delivers
(``SPACE_HEATING_HEAT``, on its input ``HeatReceivedInWatt``, which reads the distribution system's
``HeatDeliveredInWatt``), the sun through its windows (``SOLAR``, from the sun) and the internal gains of the
occupancy (``ROOM_HEAT``, on its inputs ``HeatingByResidents`` and ``HeatingByDevices``). A cooling device takes heat
out of it (``COOLING``, on its input ``HeatRemovedByCoolingInWatt``, which reads the distribution system's
``HeatDrawnForCoolingInWatt``) and a CHP would heat it (its input ``ThermalPowerCHP``). These households have no CHP,
so that input is not connected and books nothing, and their distribution system may cool only where its controller's
configuration enables it, which no preset and no twin does, so their cooling moves nothing. It loses heat to the
outside by ventilation, through windows and doors, through the
opaque elements and through the share of the gains on the window surfaces, and its thermal mass stores the rest (all
``ROOM_HEAT``). The 5R1C model closes over these ports to float precision; airing, which only overwrites the
published air temperature, moves no heat and is drawn nowhere (owner decision Q-E).

The second January week (heating, little sun) and the last week of July (sun, airing, a building that asks for
cooling) at 900 s of the heat-pump and the gas household, each run once per module with every output written to
CSV: the run finishing proves every declared balance closes (the check fails the run otherwise); the tests read the
report, the Sankey graph and the building's columns. One more run switches the distribution system's cooling on in
the gas household for the summer days it cools on, to prove that the building's cooling pairs with it.
"""

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, Set, Tuple

import numpy as np
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
        "july": ("2021-07-27", "2021-08-03"),
    }

    #: The building's ports as ``channel -> (role, carrier, peer kind, peer)``; the first four are inputs.
    PORTS: ClassVar[Dict[str, Tuple[str, str, str, str]]] = {
        "HeatReceivedInWatt": ("consumption", "space_heating_heat", "component", "HeatDistributionSystem"),
        "HeatRemovedByCoolingInWatt": ("production", "cooling", "component", "HeatDistributionSystem"),
        "HeatingByResidents": ("consumption", "room_heat", "component", "UTSPConnector"),
        "HeatingByDevices": ("consumption", "room_heat", "component", "UTSPConnector"),
        "SolarGainThroughWindows": ("consumption", "solar", "boundary", "sun"),
        "VentilationLossInWatt": ("loss", "room_heat", "boundary", "outdoors"),
        "TransmissionLossWindowsAndDoorsInWatt": ("loss", "room_heat", "boundary", "outdoors"),
        "TransmissionLossOpaqueInWatt": ("loss", "room_heat", "boundary", "outdoors"),
        "GainsLostThroughWindowsInWatt": ("loss", "room_heat", "boundary", "outdoors"),
        "ThermalMassStoredEnergyIncreaseInWattHour": ("stored_change", "room_heat", "store", "Building · stored"),
    }

    #: The result columns, as (component, output), whose energy enters the building, leaves it or is lost, for the
    #: per-step residual: an input port's column is the output it reads.
    INPUTS: ClassVar[Tuple[Tuple[str, str], ...]] = (
        ("HeatDistributionSystem", "HeatDeliveredInWatt"),
        ("Building", "SolarGainThroughWindows"),
        ("UTSPConnector", "HeatingByResidents"),
        ("UTSPConnector", "HeatingByDevices"),
    )
    OUTPUTS: ClassVar[Tuple[Tuple[str, str], ...]] = (("HeatDistributionSystem", "HeatDrawnForCoolingInWatt"),)
    LOSSES: ClassVar[Tuple[str, ...]] = (
        "VentilationLossInWatt",
        "TransmissionLossWindowsAndDoorsInWatt",
        "TransmissionLossOpaqueInWatt",
        "GainsLostThroughWindowsInWatt",
    )

    #: The run with the distribution system's cooling switched on: the household and its days (first day, day after
    #: the last), summer days on which its cooling mode takes room heat into the buffer.
    COOLING_TWIN: ClassVar[str] = "household_gas_building_sizer"
    COOLING_DAYS: ClassVar[Tuple[str, str]] = ("2021-07-27", "2021-07-29")

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


@pytest.fixture(name="cooling_run", scope="module")
def fixture_cooling_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The gas household on two summer days at 900 s with its distribution system's cooling switched on.

    Cooling is off in every preset and twin, so this run sets ``cooling_enabled`` in the heat distribution
    controller's configuration; the distribution system copies it as a sizing fact.

    Returns:
        The result directory.
    """
    start, end = Expected.COOLING_DAYS
    return run_household(
        tmp_path_factory.mktemp("cooling_on"),
        Expected.COOLING_TWIN,
        start=start,
        end=end,
        post_processing_options=("EXPORT_ENERGY_BALANCE", "EXPORT_TO_CSV"),
        config_overrides={"HeatDistributionController": {"cooling_enabled": True}},
    )


def result_column(results: Path, component: str, output: str) -> np.ndarray:
    """One output of a component per step, read from the CSV the run wrote."""
    column: np.ndarray = pd.read_csv(results / f"{component}_{output}.csv", index_col=0).iloc[:, 0].to_numpy(dtype=float)
    return column


def building_column(results: Path, output: str) -> np.ndarray:
    """One output of the building per step, read from the CSV the run wrote."""
    return result_column(results, "Building", output)


def largest_residual_in_kilowatt_hours_per_step(results: Path) -> float:
    """The building's largest balance per step from the result columns, in kWh: in minus out, losses and stored change.

    What enters and what a cooling device removes are the columns the building's input ports read. A 900 s step
    turns a power in W into kWh by 0.25 h / 1000, and the stored change is published in Wh.
    """
    kilowatt_hours_per_watt = 0.25 / 1000.0
    residual = building_column(results, "ThermalMassStoredEnergyIncreaseInWattHour") / -1000.0
    for columns, sign in ((Expected.INPUTS, 1.0), (Expected.OUTPUTS, -1.0)):
        for component, output in columns:
            residual += sign * kilowatt_hours_per_watt * result_column(results, component, output)
    for output in Expected.LOSSES:
        residual -= kilowatt_hours_per_watt * building_column(results, output)
    return float(np.abs(residual).max())


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
    assert largest_residual_in_kilowatt_hours_per_step(results) <= 1e-9


@pytest.mark.extendedbase2
def test_the_building_s_flows_are_physical(week: Tuple[str, str, Path]) -> None:
    """The house is heated in January and aired in July; nothing cools it and no CHP heats it.

    In July the building asks for cooling in some steps, but the distribution system's cooling is off, as in every
    preset, so the cooling port moves nothing.

    Catches: a heating port that books nothing in winter, losses with their sign turned, gains on the window surfaces
    that are not the model's share of about 0.8 % of the gains they come from, and a distribution system that cools
    although its configuration does not enable it.
    """
    _, week_name, results = week
    totals = {port["output"]: port["total_kwh"] for port in balance_entry(results, "Building")["ports"]}
    assert "ThermalPowerCHP" not in totals, "an input port that is not connected books nothing"
    internal_gains_in_kilowatt_hour = totals["HeatingByResidents"] + totals["HeatingByDevices"]
    assert totals["SolarGainThroughWindows"] > 0.0 and internal_gains_in_kilowatt_hour > 0.0
    assert all(totals[loss] > 0.0 for loss in Expected.LOSSES)
    lost_share = totals["GainsLostThroughWindowsInWatt"] / (
        0.5 * internal_gains_in_kilowatt_hour + totals["SolarGainThroughWindows"]
    )
    assert 0.005 < lost_share < 0.01
    assert totals["HeatRemovedByCoolingInWatt"] == 0.0
    if week_name == "january":
        assert totals["HeatReceivedInWatt"] > totals["SolarGainThroughWindows"]
    else:
        assert building_column(results, "OpenWindow").sum() > 0, "the occupants air the house in July"
        assert (building_column(results, "TheoreticalThermalBuildingDemand") < 0.0).any(), "the house asks for cooling"


@pytest.mark.extendedbase2
def test_the_building_is_linked_to_the_occupancy_the_distribution_system_and_outdoors(week: Tuple[str, str, Path]) -> None:
    """The building's internal gains, its space heating and its losses are linked to their peers.

    The occupancy's two room-heat outputs pair with the building's two gain inputs; in January the distribution
    system's heating pairs with the building's space heating. The cooling ports of the building and the distribution
    system, which move nothing, make no link in either week. The losses go outdoors.

    Catches: internal gains paired with one of the occupancy's outputs only, and an idle cooling port that fails the
    run as a one-sided booking or is reported unlinked.
    """
    _, week_name, results = week
    report: Dict[str, Any] = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
    links = {(link["sender"], link["receiver"], link["carrier"]): link for link in report["links"]}
    assert links[("UTSPConnector", "Building", "room_heat")]["verdict"] == "closes"
    if week_name == "january":
        assert links[("HeatDistributionSystem", "Building", "space_heating_heat")]["verdict"] == "closes"
    # the balance books a hot-water loop whose supplier takes heat back in a step as cooling (§3.4 of the fluid loops
    # spec); only the building's cooling must make no link
    assert not [key for key in links if "cooling" in key and "Building" in key]
    diagram_links = overall_links(results)
    assert Expected.LINKS <= diagram_links, sorted(Expected.LINKS - diagram_links)
    nodes = {node for _, source, target in diagram_links for node in (source, target)}
    assert "undeclared: Building" not in nodes and "unaccounted" not in nodes
    assert not report["unlinked"], "the building's and the distribution system's ports all have a peer"


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


@pytest.mark.extendedbase2
def test_with_cooling_switched_on_the_building_s_cooling_pairs_with_the_distribution_system(cooling_run: Path) -> None:
    """Where the configuration enables cooling, the heat the distribution system takes from the building pairs.

    The building's input ``HeatRemovedByCoolingInWatt`` (``COOLING`` ``PRODUCTION``) reads the distribution system's
    output ``HeatDrawnForCoolingInWatt`` (``COOLING`` ``CONSUMPTION``): both book the one column, and the building still
    closes in every step.

    Catches: a cooling port on either side with the wrong role, sign or peer, which fails the link or the run, and a
    building that does not close while it is cooled.
    """
    report: Dict[str, Any] = json.loads((cooling_run / "balance_report.json").read_text(encoding="utf-8"))
    links = {(link["sender"], link["receiver"], link["carrier"]): link for link in report["links"]}
    cooling = links[("Building", "HeatDistributionSystem", "cooling")]
    assert cooling["verdict"] == "closes" and cooling["received_kwh"] > 0.0
    building = {port["output"]: port["total_kwh"] for port in balance_entry(cooling_run, "Building")["ports"]}
    distribution = {
        port["output"]: port["total_kwh"] for port in balance_entry(cooling_run, "HeatDistributionSystem")["ports"]
    }
    assert building["HeatRemovedByCoolingInWatt"] == pytest.approx(distribution["HeatDrawnForCoolingInWatt"], rel=1e-12)
    assert building["HeatRemovedByCoolingInWatt"] == pytest.approx(cooling["received_kwh"], rel=1e-12)
    assert largest_residual_in_kilowatt_hours_per_step(cooling_run) <= 1e-9
