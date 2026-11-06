"""The connected energy Sankey of the heat-pump and the gas household draws the whole house: the acceptance of the complete Sankeys.

Beads hisim-mzft (the acceptance) and hisim-energy-sankeys-9uoo.8 (the system balance per carrier).

Complete Sankey spec, §9 row PR 6 (``roadmap/complete_energy_sankeys_spec.md``). In the connected diagram every
component of the run is a node that declares its ports, a meter, or exempt with a reason; no node is drawn grey as
``undeclared`` and no port ends at ``unlinked``. Every component's and every meter's residual is within the balance's
tolerance (decision 6 of the epic: 0.1 % of the throughput over the run, and per step the larger of 1e-6 kWh and
0.1 %), so the red ``unaccounted`` node is not drawn. Every meter publishes, in every step, what the ports linked to it
take minus what they give (§4.4, owner decision Q-J), and every metered port passes a meter. Because no component is
undeclared, both meter checks are enforced (owner decision Q-H) and the run itself would have failed on a gap.

The second January week at 900 s of the two households of the spec's §7, each run once per module with a predefined
occupancy profile.
"""

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Set, Tuple

import pytest

from tests.test_energy_balance_generators import run_household


class Acceptance:
    """What the acceptance runs and what it reads: the two households, the meters each must have and the report."""

    #: The heat-pump and the boiler household of the spec's §7, each a twin with a buffer and a hot-water tank.
    TWINS: ClassVar[Tuple[str, ...]] = ("household_gas_building_sizer", "household_heatpump_building_sizer")

    #: The meters each household must draw as nodes: every household meters its electricity, the boiler house its gas.
    METERS: ClassVar[Dict[str, Set[str]]] = {
        "household_gas_building_sizer": {"ElectricityMeter", "GasMeter"},
        "household_heatpump_building_sizer": {"ElectricityMeter"},
    }

    #: The node kinds of the Sankey graph that mean a flow the balance cannot place (``NodeKind`` in
    #: ``hisim/postprocessing/energy_balance/sankey.py``).
    UNPLACED_NODE_KINDS: ClassVar[Set[str]] = {"undeclared", "unlinked", "unaccounted"}

    @staticmethod
    def report(results: Path) -> Dict[str, Any]:
        """Return the run's ``balance_report.json`` as a dictionary."""
        report: Dict[str, Any] = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
        return report

    @staticmethod
    def overall_nodes(results: Path) -> List[Dict[str, str]]:
        """Return the nodes of the connected diagram (``overall`` of ``energy_sankey.json``), each with its name and kind."""
        diagrams: Dict[str, Any] = json.loads((results / "energy_sankeys" / "energy_sankey.json").read_text("utf-8"))
        nodes: List[Dict[str, str]] = diagrams["overall"]["nodes"]
        return nodes


@pytest.fixture(name="twin", scope="module", params=Acceptance.TWINS)
def fixture_twin(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Tuple[str, Path]:
    """One household's second January week at 900 s, run once for every test of this module.

    Returns:
        The setup's name and its result directory.
    """
    setup: str = request.param
    return setup, run_household(tmp_path_factory.mktemp(setup), setup)


@pytest.mark.extendedbase2
def test_no_node_of_the_connected_diagram_is_undeclared_unlinked_or_unaccounted(twin: Tuple[str, Path]) -> None:
    """Every node of the connected diagram is a component, a meter, a boundary, a loss or a storage.

    Catches a component of the house that declares no port and is not exempt (drawn grey as ``undeclared``), a port
    whose energy goes nowhere (``unlinked``), and a residual beyond the tolerance (the red ``unaccounted`` node).
    """
    _, results = twin
    nodes = Acceptance.overall_nodes(results)
    unplaced = [node for node in nodes if node["kind"] in Acceptance.UNPLACED_NODE_KINDS]
    assert not unplaced
    report = Acceptance.report(results)
    assert not report["undeclared"] and not report["unlinked"]
    drawn_components = {node["name"] for node in nodes if node["kind"] == "component"}
    assert drawn_components == {entry["component"] for entry in report["components"]}


@pytest.mark.extendedbase2
def test_every_component_and_every_link_closes_within_the_tolerance(twin: Tuple[str, Path]) -> None:
    """Every component's residual is within 0.1 % of its throughput and no step is beyond the per-step tolerance.

    Catches a component that creates or loses energy, and a transfer that its sender and its receiver book
    differently.
    """
    _, results = twin
    report = Acceptance.report(results)
    assert report["verdict"] == "closes"
    assert report["tolerance"] == {"annual_relative": 0.001, "step_relative": 0.001, "step_absolute_kwh": 1e-06}
    for entry in report["components"]:
        assert entry["verdict"] == "closes" and entry["failing_steps"] == 0, entry["component"]
        assert abs(entry["residual_kwh"]) <= 0.001 * entry["throughput_kwh"] + 1e-6, entry["component"]
    assert report["links"] and all(link["verdict"] == "closes" for link in report["links"])
    assert not report["circuit_faults"]


@pytest.mark.extendedbase2
def test_every_meter_publishes_what_it_reads_in_every_step(twin: Tuple[str, Path]) -> None:
    """Each meter's import minus export equals what its linked ports take minus what they give, in every step.

    The check is enforced, since no component is undeclared (owner decision Q-H). Catches a meter that reads a flow
    no port declares, and a port linked to a meter that the meter does not count.
    """
    setup, results = twin
    meters = {meter["meter"]: meter for meter in Acceptance.report(results)["meters"]}
    assert set(meters) == Acceptance.METERS[setup]
    for name, meter in meters.items():
        assert meter["verdict"] == "agrees" and meter["failing_steps"] == 0 and meter["worst_step"] is None, name
        assert meter["enforced"] is True and meter["not_enforced_because"] is None, name
        assert meter["reads"], name
        assert meter["published_import_kwh"] - meter["published_export_kwh"] == pytest.approx(
            meter["consumed_kwh"] - meter["produced_kwh"], rel=1e-6, abs=1e-6
        ), name


@pytest.mark.extendedbase2
def test_no_port_is_unmetered(twin: Tuple[str, Path]) -> None:
    """Every electricity and bought-carrier port passes a meter, and the check is enforced.

    Catches a device whose electricity or fuel the household's meters miss: missing from the bill, the KPIs and the
    grid's balance.
    """
    _, results = twin
    unmetered = Acceptance.report(results)["unmetered"]
    assert unmetered == {"enforced": True, "not_enforced_because": None, "ports": []}
