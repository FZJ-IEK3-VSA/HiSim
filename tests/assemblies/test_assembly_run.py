"""A mock house run end to end through ``hisim energy-system run``, its record re-run, and a wiring refusal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from hisim.cli import main
from hisim.cli_exit import ExitCodes
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.errors import EnergySystemWiringError
from hisim.energy_system.executor import EnergySystemExecutor
from hisim.postprocessing.kpi_computation.kpi_address import KpiFinder
from hisim.simulationparameters import SimulationParameters
from tests.assemblies.helpers import EMPTY_CONTRACT, WEATHER, Library, Mocks, read_system, site


@pytest.mark.base
def test_a_mock_house_runs_records_its_imports_and_its_record_reruns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Catches a run with imports that cannot be told from a flat one, or whose record does not reproduce it."""
    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, str(Mocks.LIBRARY))
    first = tmp_path / "first"
    assert (
        main(["energy-system", "run", str(Mocks.HOUSE), str(Mocks.PARAMETERS), "--result-dir", str(first)])
        == ExitCodes.OK
    )

    record = yaml.safe_load((first / "realized.energy_system.yaml").read_text(encoding="utf-8"))
    assert record["schema_version"] == 3 and "imports" not in record
    imports = record["metadata"]["imports"]
    assert [(item["import"], item["instance"]) for item in imports["instances"]] == [
        ("pv", "east"),
        ("pv", "west"),
        ("tank", None),
        ("heater", None),
    ]
    assert imports["addresses"]["heater-Controller"]["path"] == [{"import": "heater"}]
    assert imports["instances"][3]["quote"]["source"] == "mock"
    source_map = record["metadata"]["source_map"]
    assert source_map["tank-Tank"]["inputs[1]"]["chain"] == [
        "house.energy_system.yaml:33",
        "mock/hot_water_tank.assembly.yaml:20",
    ]

    kpis = KpiFinder(json.loads((first / "all_kpis.json").read_text(encoding="utf-8")))
    arrays = kpis.addresses(import_key="pv")
    assert sorted(address.source.name for address in arrays if address.source is not None) == [
        "pv-east-PVSystem",
        "pv-west-PVSystem",
    ]
    assert all(address.source is not None and len(address.source.path) == 1 for address in arrays)

    monkeypatch.delenv(AssemblyResolver.ENVIRONMENT_VARIABLE)
    second = tmp_path / "second"
    code = main(
        [
            "energy-system",
            "run",
            str(first / "realized.energy_system.yaml"),
            str(first / "realized.simulation.yaml"),
            "--result-dir",
            str(second),
            "--rerun",
        ]
    )
    capsys.readouterr()
    assert code == ExitCodes.OK
    again = yaml.safe_load((second / "realized.energy_system.yaml").read_text(encoding="utf-8"))
    assert again["metadata"]["imports"] == imports and again["metadata"]["source_map"] == source_map
    assert again["components"] == record["components"]


@pytest.mark.base
def test_a_wiring_refusal_of_a_lowered_item_names_where_it_came_from(tmp_path: Path) -> None:
    """Catches a refusal of an item an import produced that names only the expanded component."""
    library = Library(tmp_path)
    library.add(
        "test/bare",
        f"""\
        schema_version: 4
        kind: assembly
        name: test/bare
        components:
          Device:
            class: {Mocks.CLASSES}.MockBareDevice
            preset: standard
            inputs: [{{$port: weather}}]
        interface:
          needs:
            weather: {{into: [Device], partner: MockWeather}}
        {EMPTY_CONTRACT}""",
    )
    model, lines = read_system(site(WEATHER, imports="bare: {assembly: test/bare}"))
    parameters = SimulationParameters.one_day_only(2021, 900)
    parameters.result_directory = str(tmp_path / "results")
    with pytest.raises(EnergySystemWiringError, match="EF-23") as refusal:
        EnergySystemExecutor(model, parameters, assembly_resolver=library.resolver(), source_lines=lines).build()
    message = str(refusal.value)
    assert message.endswith(
        "[source: bare-Device (import bare, inline.energy_system.yaml:6 → test/bare.assembly.yaml:5)]"
    )
