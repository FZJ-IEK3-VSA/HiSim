"""Guards against booking the LPG's car charging into the household electricity profile.

``UtspLpgConnector`` publishes the LPG's ``Electricity`` sum profile as
``ElectricalPowerConsumption``. Four of the LPG's charging station sets -- the plain
``Charging At Home with ... kW`` ones -- book the car's charging into exactly that load type. Using
one of them has two failure modes, both silent:

* the setup also builds a :class:`~hisim.components.generic_car.Car`, and the same kilometres are
  charged twice (once inside the residents' profile, once through the ``CarBattery`` /
  ``L1Controller`` chain into the EMS);
* the setup builds no car at all, and the household still carries one nobody declared.

``Charging At Home with 03.7 kW, output results to Car Electricity`` books the same charging to the
separate ``Electricity for Car Charging`` load type, which HiSim never reads
(``define_required_result_files`` requests only ``Electricity``, ``Warm_Water``,
``Inner_Device_Heat_Gains``, the activity files, flexibility and the car JSONs). Driving distances
and car locations are unaffected.

These tests need neither the LPG binary nor a UTSP connection.

Scope: the ``charging_station_set`` of the **connector**. ``L1Controller`` carries a field of the
same name, but it only describes HiSim's own wallbox (power and location) and never reaches the
LPG, so it is deliberately not restricted here. ``obsolete/`` is excluded -- those setups reproduce
published results and are not run.
"""
# clean
import ast
import glob
import os
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pytest
from utspclient.helpers.lpgdata import ChargingStationSets

from hisim.components.loadprofilegenerator_utsp_connector import (
    CHARGING_STATION_SETS_BOOKED_TO_HOUSEHOLD_ELECTRICITY,
    UtspLpgConnectorConfig,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Directories whose configurations must not book car charging into the household electricity.
ENFORCED_ROOTS = ("system_setups", "hisim", "tests", "scripts")

#: Names of the connector config field and of the factory that produces a default connector config.
CONFIG_CLASS_NAME = "UtspLpgConnectorConfig"
CONFIG_FACTORY_NAME = "get_default_utsp_connector_config"
FIELD_NAME = "charging_station_set"


def _lpg_name(attribute_name: str) -> Optional[str]:
    """Translate a ``ChargingStationSets`` attribute name into the LPG's display name."""
    reference = getattr(ChargingStationSets, attribute_name, None)
    return None if reference is None else reference.Name


def _python_files() -> List[Path]:
    """Every Python file under the enforced roots."""
    files: List[Path] = []
    for root in ENFORCED_ROOTS:
        files.extend(Path(p) for p in glob.glob(str(REPO_ROOT / root / "**" / "*.py"), recursive=True))
    return sorted(files)


def _scenario_files() -> List[Path]:
    """Every scenario JSON under the enforced roots."""
    files: List[Path] = []
    for root in ENFORCED_ROOTS:
        files.extend(
            Path(p) for p in glob.glob(str(REPO_ROOT / root / "**" / "*.scenario.json"), recursive=True)
        )
    return sorted(files)


def _called_name(node: ast.Call) -> str:
    """Return the bare name of a call target (``a.b.C(...)`` -> ``C``)."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _charging_set_attribute(node: ast.AST) -> Optional[str]:
    """Return the attribute name of a ``ChargingStationSets.X`` expression, else ``None``."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id == "ChargingStationSets":
            return node.attr
    return None


def _collect_offences(source: str) -> List[Tuple[int, str]]:
    """Return ``(line, lpg_name)`` for every connector config that books to household electricity.

    Two shapes are recognised, which together cover how the repository builds connector configs::

        UtspLpgConnectorConfig(..., charging_station_set=<expr>, ...)
        config = UtspLpgConnectorConfig(...) / ...get_default_utsp_connector_config()
        config.charging_station_set = <expr>

    ``<expr>`` is resolved when it is a literal ``ChargingStationSets.X`` or a variable assigned one
    somewhere in the same module. Anything else is left alone: this check exists to catch the known
    shapes, not to be a type checker.
    """
    tree = ast.parse(source)

    # variable -> ChargingStationSets attribute name
    charging_set_variables: Dict[str, str] = {}
    # variables that hold a connector config
    connector_config_variables: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        attribute = _charging_set_attribute(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                if attribute is not None:
                    charging_set_variables[target.id] = attribute
                elif isinstance(node.value, ast.Call) and _called_name(node.value) in (
                    CONFIG_CLASS_NAME,
                    CONFIG_FACTORY_NAME,
                ):
                    connector_config_variables.add(target.id)

    def resolve(node: ast.AST) -> Optional[str]:
        attribute = _charging_set_attribute(node)
        if attribute is None and isinstance(node, ast.Name):
            attribute = charging_set_variables.get(node.id)
        return None if attribute is None else _lpg_name(attribute)

    offences: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        resolved: Optional[str] = None
        lineno = 0
        if isinstance(node, ast.Call) and _called_name(node) == CONFIG_CLASS_NAME:
            lineno = node.lineno
            for keyword in node.keywords:
                if keyword.arg == FIELD_NAME:
                    resolved = resolve(keyword.value)
        elif isinstance(node, ast.Assign):
            lineno = node.lineno
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == FIELD_NAME
                    and isinstance(target.value, ast.Name)
                    and target.value.id in connector_config_variables
                ):
                    resolved = resolve(node.value)
        if resolved in CHARGING_STATION_SETS_BOOKED_TO_HOUSEHOLD_ELECTRICITY:
            offences.append((lineno, str(resolved)))
    return offences


@pytest.mark.base
def test_shipped_default_does_not_book_to_household_electricity() -> None:
    """The default config is what every setup inherits, so it decides the whole repository."""
    default = UtspLpgConnectorConfig.get_default_utsp_connector_config()
    assert default.charging_station_set is not None
    assert default.charging_station_set.Name not in CHARGING_STATION_SETS_BOOKED_TO_HOUSEHOLD_ELECTRICITY, (
        f"the default connector config books car charging into the household electricity profile "
        f"via '{default.charging_station_set.Name}'"
    )


@pytest.mark.base
def test_no_scenario_json_books_car_charging_to_household_electricity() -> None:
    """No shipped scenario may hand the connector a set that books to household electricity."""
    import json

    offences: List[str] = []
    for path in _scenario_files():
        scenario = json.loads(path.read_text(encoding="utf-8"))
        for component in scenario.get("components", []):
            if not component.get("component_full_classname", "").endswith("UtspLpgConnector"):
                continue
            charging_station_set = component.get("configuration", {}).get(FIELD_NAME)
            if not charging_station_set:
                continue
            name = charging_station_set.get("name")
            if name in CHARGING_STATION_SETS_BOOKED_TO_HOUSEHOLD_ELECTRICITY:
                offences.append(f"{path.relative_to(REPO_ROOT)}: '{name}'")
    assert not offences, (
        "these scenarios book the LPG's car charging into the residents' electricity profile:\n  "
        + "\n  ".join(offences)
        + "\nUse 'Charging At Home with 03.7 kW, output results to Car Electricity' instead."
    )


@pytest.mark.base
def test_no_python_configuration_books_car_charging_to_household_electricity() -> None:
    """Same rule for connector configs built in Python."""
    offences: List[str] = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8", errors="replace")
        if CONFIG_CLASS_NAME not in source and CONFIG_FACTORY_NAME not in source:
            continue
        for line, name in _collect_offences(source):
            offences.append(f"{path.relative_to(REPO_ROOT)}:{line}: '{name}'")
    assert not offences, (
        "these Python configurations book the LPG's car charging into the residents' electricity "
        "profile:\n  " + "\n  ".join(offences)
        + "\nUse ChargingStationSets.Charging_At_Home_with_03_7_kW_output_results_to_Car_Electricity."
    )


@pytest.mark.base
def test_offence_detector_recognises_the_shapes_it_claims_to() -> None:
    """The scanner is only worth having if it actually fires -- pin its behaviour."""
    forbidden = "Charging_At_Home_with_11_kW"
    allowed = "Charging_At_Home_with_03_7_kW_output_results_to_Car_Electricity"

    keyword_argument = f"config = UtspLpgConnectorConfig(charging_station_set=ChargingStationSets.{forbidden})"
    via_variable = (
        f"chosen = ChargingStationSets.{forbidden}\n"
        "config = UtspLpgConnectorConfig(charging_station_set=chosen)"
    )
    attribute_assignment = (
        "config = UtspLpgConnectorConfig.get_default_utsp_connector_config()\n"
        f"config.charging_station_set = ChargingStationSets.{forbidden}"
    )
    # The L1Controller field of the same name is out of scope and must not be flagged.
    controller_config = (
        "config = ChargingStationConfig.get_default_config("
        f"charging_station_set=ChargingStationSets.{forbidden})"
    )
    good = f"config = UtspLpgConnectorConfig(charging_station_set=ChargingStationSets.{allowed})"

    assert _collect_offences(keyword_argument), "keyword argument not detected"
    assert _collect_offences(via_variable), "assignment through a local variable not detected"
    assert _collect_offences(attribute_assignment), "attribute assignment on a config not detected"
    assert not _collect_offences(controller_config), "L1Controller config must not be flagged"
    assert not _collect_offences(good), "the recommended set must not be flagged"


@pytest.mark.base
def test_forbidden_list_matches_the_lpg_database() -> None:
    """Keep the hard-coded list in sync with the LPG database that defines the routing.

    ``tblChargingStationSetEntries.GridChargingLoadtypeID`` decides which load type a set's
    charging is booked to. Skipped when the database is not on disk (it ships inside ``pylpg``,
    whose platform folder differs per OS).
    """
    import sqlite3

    import pylpg

    candidates = glob.glob(
        os.path.join(os.path.dirname(pylpg.__file__), "**", "profilegenerator.db3"), recursive=True
    )
    if not candidates:
        pytest.skip("the LPG database is not available in this environment")

    connection = sqlite3.connect(candidates[0])
    try:
        cursor = connection.cursor()
        electricity_id = cursor.execute("select ID from tblLoadTypes where Name = 'Electricity'").fetchone()[0]
        from_database = {
            row[0]
            for row in cursor.execute(
                "select distinct sets.Name from tblChargingStationSets sets "
                "join tblChargingStationSetEntries entries on entries.ChargingStationSetID = sets.ID "
                "where entries.GridChargingLoadtypeID = ?",
                (electricity_id,),
            )
        }
    finally:
        connection.close()

    assert from_database == set(CHARGING_STATION_SETS_BOOKED_TO_HOUSEHOLD_ELECTRICITY), (
        "CHARGING_STATION_SETS_BOOKED_TO_HOUSEHOLD_ELECTRICITY no longer matches the LPG database; "
        f"database says {sorted(from_database)}"
    )
