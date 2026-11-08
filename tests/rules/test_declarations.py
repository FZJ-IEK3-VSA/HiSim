"""Tests of the declaration of a setup's kind: every setup declares one, and nothing runs, records or reads without it.

A Python setup declares its kind in the first line of its setup function, an energy-system file in its top-level
key ``setup_kind``. The reader refuses a file without it, the simulator a run, the recorder a setup and the
post-processing a result, each before anything else happens. There is no default kind.
"""

import ast
from pathlib import Path
from typing import ClassVar, List

import pandas as pd
import pytest
import yaml

from hisim.components.random_numbers import RandomNumbers, RandomNumbersConfig
from hisim.config import ComponentID
from hisim.energy_system.emitter import EnergySystemEmitter
from hisim.energy_system.errors import EnergySystemFormatError
from hisim.energy_system.loader import load_energy_system
from hisim.energy_system.recording.observe import SystemObserver
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.postprocessing.postprocessing_main import PostProcessor
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.rule import UndeclaredSetupKindError
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator

pytestmark = pytest.mark.base


class Repository:
    """Where the setups and the energy-system files of the repository are."""

    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[2]

    @classmethod
    def setups(cls) -> List[Path]:
        """Return every Python setup of ``system_setups/``."""
        return sorted(path for path in (cls.ROOT / "system_setups").glob("*.py") if path.name != "__init__.py")

    @classmethod
    def energy_system_files(cls) -> List[Path]:
        """Return every energy-system file: the twins, grouped and composed files, and the fixtures."""
        return sorted((cls.ROOT / "energy_systems").glob("*.energy_system.yaml")) + sorted(
            (cls.ROOT / "tests" / "energy_systems").glob("*.energy_system.yaml")
        )


@pytest.mark.parametrize("setup", Repository.setups(), ids=lambda path: path.stem)
def test_every_python_setup_declares_its_kind_in_the_first_line_of_its_setup_function(setup: Path) -> None:
    """Catches a setup that would be refused before its first step, or one that declares its kind after building."""
    function = next(
        node for node in ast.parse(setup.read_text(encoding="utf-8")).body if isinstance(node, ast.FunctionDef) and node.name == "setup_function"
    )
    statements = function.body[1:] if isinstance(function.body[0], ast.Expr) and isinstance(function.body[0].value, ast.Constant) else function.body
    first = statements[0]

    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call), setup.name
    call = first.value
    assert isinstance(call.func, ast.Attribute) and call.func.attr == "declare_setup_kind", setup.name
    assert ast.unparse(call.func.value) == function.args.args[0].arg, setup.name
    assert ast.unparse(call.args[0]).removeprefix("SetupKind.") in SetupKind.__members__, setup.name


@pytest.mark.parametrize("path", Repository.energy_system_files(), ids=lambda path: path.name)
def test_every_energy_system_file_states_its_kind(path: Path) -> None:
    """Catches a twin, a composed file or a fixture that the reader would refuse."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert document.get("setup_kind") in [kind.value for kind in SetupKind], path.name


class TestTheReader:
    """The top-level key ``setup_kind``: mandatory, one of four words, written after the name."""

    DOCUMENT: ClassVar[str] = (
        "schema_version: 3\nname: kinds\n{kind}components:\n"
        "  meter:\n    class: hisim.components.electricity_meter.ElectricityMeter\n    preset: default\n"
    )

    def test_a_file_without_a_kind_is_refused_naming_the_four_kinds(self) -> None:
        """Catches a default kind slipping in for a file that states none."""
        with pytest.raises(EnergySystemFormatError, match=r"EF-03 at <text>\.setup_kind: the document declares no setup_kind.*commercial, demo, plant, residential"):
            load_energy_system(self.DOCUMENT.format(kind=""))

    def test_an_unknown_kind_is_refused(self) -> None:
        """Catches a misspelt kind read as no kind or as a default."""
        with pytest.raises(EnergySystemFormatError, match="declares setup_kind 'house'"):
            load_energy_system(self.DOCUMENT.format(kind="setup_kind: house\n"))

    def test_the_kind_is_read_and_written_after_the_name(self) -> None:
        """Catches the emitter dropping the key, which would make every recorded file unreadable."""
        model = load_energy_system(self.DOCUMENT.format(kind="setup_kind: plant\n"))

        assert model.setup_kind == SetupKind.PLANT
        assert list(EnergySystemEmitter.to_document(model))[:3] == ["schema_version", "name", "setup_kind"]
        assert "\nsetup_kind: plant\n" in EnergySystemEmitter.dump(model)


class Rig:
    """A one-component simulator of one hour-step day that writes below a test's directory."""

    @staticmethod
    def simulator(tmp_path: Path) -> Simulator:
        """Return a simulator with one random-number source and no kind declared."""
        parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
        parameters.result_directory = str(tmp_path / "results")
        simulator: Simulator = Simulator(module_directory=str(tmp_path), module_filename="undeclared", my_simulation_parameters=parameters)
        simulator.add_component(
            RandomNumbers(config=RandomNumbersConfig(component_id=ComponentID(name="Random"), minimum=0, maximum=1, seed=1), my_simulation_parameters=parameters)
        )
        return simulator


class TestTheSimulator:
    """``Simulator.declare_setup_kind`` and the refusal of an undeclared run before its first step."""

    def test_a_run_that_declares_no_kind_is_refused_before_its_first_step(self, tmp_path: Path) -> None:
        """Catches an undeclared Python setup that runs as if it had a default kind."""
        simulator = Rig.simulator(tmp_path)

        with pytest.raises(UndeclaredSetupKindError, match="this setup declares no kind"):
            simulator.run_all_timesteps()

    def test_a_setup_declares_one_kind(self, tmp_path: Path) -> None:
        """Catches a second, different declaration silently replacing the first, or a string taken for a kind."""
        simulator = Rig.simulator(tmp_path)
        simulator.declare_setup_kind(SetupKind.DEMO)
        simulator.declare_setup_kind(SetupKind.DEMO)

        assert simulator.setup_kind == SetupKind.DEMO
        with pytest.raises(ValueError, match="declared the kind demo already and now declares plant"):
            simulator.declare_setup_kind(SetupKind.PLANT)
        with pytest.raises(TypeError, match="takes a SetupKind, not 'demo'"):
            simulator.declare_setup_kind("demo")  # type: ignore[arg-type]

    def test_the_recorder_refuses_a_setup_that_declares_no_kind(self, tmp_path: Path) -> None:
        """Catches a twin written without the key its reader requires."""
        simulator = Rig.simulator(tmp_path)

        with pytest.raises(UndeclaredSetupKindError):
            SystemObserver.observe(simulator, "undeclared.py")


def test_the_post_processing_refuses_results_without_a_kind(tmp_path: Path) -> None:
    """Catches a result table judged by no rule because its run carried no kind."""
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
    parameters.result_directory = str(tmp_path)
    ppdt = PostProcessingDataTransfer(
        results=pd.DataFrame(), all_outputs=[], simulation_parameters=parameters, wrapped_components=[], mode=1, setup_function="setup_function",
        module_filename="undeclared", module_config=None, execution_time_in_s=0.0, results_monthly=None, results_hourly=None,
        results_cumulative=None, results_daily=None,
    )

    with pytest.raises(UndeclaredSetupKindError):
        PostProcessor().run(ppdt)
