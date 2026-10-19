"""A run whose window starts after 1 January warns about the components in it that still read January.

The weather, the PV system and the building's solar gains read the run's window since hisim-9g2.4; the
classes ``hisim.january_readers.JanuaryReaders`` lists do not yet, so a July run with one of them mixes
two seasons while it finishes and looks normal. ``Simulator.run_all_timesteps`` logs one warning at its
start naming the ones the run contains. These tests run one hourly day through the real simulator with a
CSV loader, which reads its profile from row 0 whatever the start, or with the example component, which
reads no profile, and read the warning from what the logger prints; the pure parts are tested directly.
"""

import datetime
import importlib
from pathlib import Path
from typing import List

import pandas as pd
import pytest

from hisim import loadtypes as lt
from hisim.components import csvloader, example_component, solar_thermal_system
from hisim.january_readers import JanuaryReader, JanuaryReaders
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator
from tests import functions_for_testing as fft

pytestmark = pytest.mark.base


class Runs:
    """Builds and runs one hourly day through the simulator, and finds the warning in what it printed."""

    #: The first day of the July runs.
    JULY_FIRST: datetime.datetime = datetime.datetime(2021, 7, 1)
    #: The first day of the January runs.
    NEW_YEAR: datetime.datetime = datetime.datetime(2021, 1, 1)
    #: The timestep of every run, in seconds.
    SECONDS_PER_TIMESTEP: int = 3600
    #: The words every warning of this kind starts its explanation with, after the date.
    WARNING_MARKER: str = "after 1 January, but some of its components do not follow the run's window yet"

    @classmethod
    def parameters(cls, start_date: datetime.datetime, tmp_path: Path) -> SimulationParameters:
        """Return the parameters of one hourly day from ``start_date``, writing into ``tmp_path``."""
        return SimulationParameters(
            start_date=start_date,
            end_date=start_date + datetime.timedelta(days=1),
            seconds_per_timestep=cls.SECONDS_PER_TIMESTEP,
            result_directory=str(tmp_path / "results"),
        )

    @staticmethod
    def csv_loader(parameters: SimulationParameters) -> csvloader.CSVLoader:
        """Return a CSV loader over a 24-row electricity profile handed in as a data frame, without a file."""
        config = csvloader.CSVLoaderConfig.for_csv_file(
            "ProfileReader",
            csv_filename="in_memory.csv",
            column=1,
            loadtype=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            column_name="power_in_watt",
        )
        frame = pd.DataFrame({"hour": range(24), "power_in_watt": [100.0] * 24})
        return csvloader.CSVLoader(config=config, my_simulation_parameters=parameters, dataframe=frame)

    @staticmethod
    def example(parameters: SimulationParameters) -> example_component.ExampleComponent:
        """Return the example component, which reads no profile."""
        return example_component.ExampleComponent(
            config=fft.sized_example_component_config(), my_simulation_parameters=parameters
        )

    @classmethod
    def run(cls, start_date: datetime.datetime, with_csv_loader: bool, tmp_path: Path) -> None:
        """Run one hourly day from ``start_date`` with the example component, and a CSV loader if asked."""
        parameters = cls.parameters(start_date, tmp_path)
        simulator = Simulator(
            module_directory=str(tmp_path), module_filename="january_readers", my_simulation_parameters=parameters
        )
        simulator.set_simulation_parameters(parameters)
        simulator.add_component(cls.example(parameters))
        if with_csv_loader:
            simulator.add_component(cls.csv_loader(parameters))
        simulator.run_all_timesteps()

    @classmethod
    def warnings_in(cls, printed: str) -> List[str]:
        """Return the printed warning lines about components that still read January."""
        return [line for line in printed.splitlines() if line.startswith("WRN:") and cls.WARNING_MARKER in line]


class TestTheRunWarns:
    """The warning appears exactly when the window starts after 1 January and a January reader is present."""

    def test_a_july_run_with_a_csv_loader_names_it_once(self, tmp_path: Path, capsys) -> None:
        """A July day with a CSV loader warns once, naming the loader and the bead that fixes it."""
        Runs.run(Runs.JULY_FIRST, with_csv_loader=True, tmp_path=tmp_path)

        warnings = Runs.warnings_in(capsys.readouterr().out)

        assert len(warnings) == 1
        assert "2021-07-01" in warnings[0]
        assert "CSVLoader reads" in warnings[0]
        assert "hisim-9g2.14" in warnings[0]
        assert "UtspLpgConnector" not in warnings[0]

    def test_a_january_run_with_a_csv_loader_logs_nothing(self, tmp_path: Path, capsys) -> None:
        """A day from 1 January reads the right rows, so the same system logs no warning."""
        Runs.run(Runs.NEW_YEAR, with_csv_loader=True, tmp_path=tmp_path)

        assert not Runs.warnings_in(capsys.readouterr().out)

    def test_a_july_run_without_a_january_reader_logs_nothing(self, tmp_path: Path, capsys) -> None:
        """A July day of the example component alone has nothing that reads January, so nothing is logged."""
        Runs.run(Runs.JULY_FIRST, with_csv_loader=False, tmp_path=tmp_path)

        assert not Runs.warnings_in(capsys.readouterr().out)


class TestTheList:
    """The list names real classes, matches subclasses, and keeps its order."""

    @pytest.mark.parametrize("reader", JanuaryReaders.CLASSES, ids=lambda reader: reader.class_path)
    def test_every_entry_names_an_existing_class(self, reader: JanuaryReader) -> None:
        """Each dotted path imports to a class of that name, so a renamed component cannot silently drop out."""
        module_name, class_name = reader.class_path.rsplit(".", 1)
        component_class = getattr(importlib.import_module(module_name), class_name)
        assert f"{component_class.__module__}.{component_class.__qualname__}" == reader.class_path

    def test_a_subclass_is_named_by_its_base_class(self) -> None:
        """A subclass of a January reader inherits the reading, so the base class's entry matches it."""

        class OwnLoader(csvloader.CSVLoader):  # pylint: disable=too-few-public-methods  # a stand-in subclass only
            """A CSV loader subclass that changes nothing."""

        assert [reader.class_path for reader in JanuaryReaders.present([OwnLoader, OwnLoader])] == [
            "hisim.components.csvloader.CSVLoader"
        ]

    def test_classes_come_back_in_the_order_of_the_list(self) -> None:
        """Several readers are reported in the list's order, whatever order the run holds them in."""
        classes = [csvloader.CSVLoader, solar_thermal_system.SolarThermalSystem]

        names = [reader.class_path.rsplit(".", 1)[-1] for reader in JanuaryReaders.present(classes)]

        assert names == ["SolarThermalSystem", "CSVLoader"]

    @pytest.mark.parametrize(
        ("start_date", "expected"),
        [
            (datetime.datetime(2021, 1, 1), False),
            (datetime.datetime(2021, 1, 2), True),
            (datetime.datetime(2021, 7, 1), True),
            (datetime.datetime(2020, 12, 31), True),
        ],
    )
    def test_only_a_start_after_new_year_counts(self, start_date: datetime.datetime, expected: bool) -> None:
        """1 January 00:00 is no mid-year start; any later midnight of the year is."""
        assert JanuaryReaders.starts_after_new_year(start_date) is expected

    def test_no_warning_text_for_new_year(self) -> None:
        """The text is None for a start on 1 January even with a January reader present."""
        assert JanuaryReaders.warning_text(datetime.datetime(2021, 1, 1), [csvloader.CSVLoader]) is None
