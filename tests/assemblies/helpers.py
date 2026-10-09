"""Shared helpers of the assemblies tests: both libraries, the real site entries, inline files and expansion."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import ClassVar, Optional, Tuple

from hisim.energy_system.assemblies.expansion import expand_imports
from hisim.energy_system.assemblies.record import ImportRecord
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.document import RawDocument
from hisim.energy_system.executor import BuiltEnergySystem, EnergySystemExecutor
from hisim.energy_system.loader import EnergySystemReader
from hisim.energy_system.model import EnergySystemFile
from hisim.energy_system.source_lines import LineIndex
from hisim.simulationparameters import SimulationParameters


class Mocks:
    """The mock library, its house, and the site entries of mock classes its assemblies bind to.

    Example: ``site(Mocks.WEATHER) + imports("pv: {assembly: mock/labelled_array}")`` is a site with
    one mock PV array. The mock files hold only the shapes the real library cannot show
    (``assemblies_spec.md`` §9.3); :class:`Real` holds the real counterparts.
    """

    ROOT: ClassVar[Path] = Path(__file__).resolve().parent / "mock_assemblies"
    LIBRARY: ClassVar[Path] = ROOT / "library"
    HOUSE: ClassVar[Path] = ROOT / "systems" / "house.energy_system.yaml"
    PARAMETERS: ClassVar[Path] = ROOT / "one_day_kpis.simulation.yaml"

    #: Dotted prefix of the mock component classes.
    CLASSES: ClassVar[str] = "tests.assemblies.mock_components"

    #: Site entries of the mock weather, the mock residents and the mock energy manager.
    WEATHER: ClassVar[str] = f"Weather: {{class: {CLASSES}.MockWeather, preset: standard}}"
    OCCUPANCY: ClassVar[str] = f"Occupancy: {{class: {CLASSES}.MockOccupancy, preset: standard}}"
    EMS: ClassVar[str] = f"Ems: {{class: {CLASSES}.MockEms, preset: standard}}"


class Real:
    """The real library, the composed files beside it, and the site entries its assemblies bind to.

    Example: ``site(Real.WEATHER, Real.OCCUPANCY) + imports(Real.PV)`` is a house with one real PV
    array; :func:`expand_text` and :func:`build_text` resolve it against both libraries.

    ``WEATHER``, ``OCCUPANCY`` and ``BUILDING`` are written as the composed files and
    ``energy_systems/assemblies/test_partners.yaml`` write them, so a build constructs what a composed
    file constructs; a test holds them equal to the partners. ``HEAT_DISTRIBUTION_CONTROLLER`` and
    ``HEAT_DISTRIBUTION`` are reduced for expansion only: they read no other site entry,
    so :meth:`heating_site` needs no building, and a site of them expands but does not build. A build of
    a PV array states its power: at its default the array is sized from the building's roof, which needs
    a ``Building``.
    """

    REPOSITORY: ClassVar[Path] = Path(__file__).resolve().parents[2]
    LIBRARY: ClassVar[Path] = REPOSITORY / "energy_systems" / "assemblies"
    ENERGY_SYSTEMS: ClassVar[Path] = REPOSITORY / "energy_systems"
    #: One day at 15 min with the KPIs as JSON and the energy-balance report: what the end-to-end runs use.
    PARAMETERS: ClassVar[Path] = Path(__file__).resolve().parent / "one_day_balance.simulation.yaml"

    #: The composed file of the heat-pump twin, and the one of the condensing gas boiler's twin.
    HEATPUMP_HOUSE: ClassVar[Path] = ENERGY_SYSTEMS / "household_heatpump_building_sizer.composed.energy_system.yaml"
    GAS_HOUSE: ClassVar[Path] = ENERGY_SYSTEMS / "household_gas_building_sizer.composed.energy_system.yaml"

    WEATHER: ClassVar[str] = (
        "Weather: {class: hisim.components.weather.Weather, config: {location: Aachen, source_path: "
        "'${inputs}/weather/test-reference-years_1995-2012_1-location/data_processed/aachen_center', "
        "data_source: DWD_TRY, heating_reference_temperature_in_celsius: -7.0, predictive_control: false}}"
    )
    OCCUPANCY: ClassVar[str] = (
        "UTSPConnector: {class: hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector, "
        "preset: couple_both_at_work, config: {data_acquisition_mode: USE_LOCAL_LPG}}"
    )
    BUILDING: ClassVar[str] = (
        "Building: {class: hisim.components.building.building.Building, preset: german_single_family_home, "
        "config: {number_of_apartments: 1.0, enable_opening_windows: true, weather_identity: "
        "Aachen/DWD_TRY/weather/test-reference-years_1995-2012_1-location/data_processed/aachen_center}, "
        "inputs: [Weather, UTSPConnector]}"
    )
    HEAT_DISTRIBUTION_CONTROLLER: ClassVar[str] = (
        "HeatDistributionController: "
        "{class: hisim.components.heat_distribution_system.HeatDistributionController, preset: building_derived}"
    )
    HEAT_DISTRIBUTION: ClassVar[str] = (
        "HeatDistributionSystem: {class: hisim.components.heat_distribution_system.HeatDistribution, "
        "preset: building_derived, inputs: [{$port: space_heating}], ports: {space_heating: {circuit: space_heating}}}"
    )
    #: The energy manager's and the electricity meter's classes, for site entries.
    ENERGY_MANAGER_CLASS: ClassVar[str] = (
        "hisim.components.controller_l2_energy_management_system.L2GenericEnergyManagementSystem"
    )
    METER_CLASS: ClassVar[str] = "hisim.components.electricity_meter.ElectricityMeter"

    #: A site PV array of the real class at a stated power: a provider of the arrays' peak power by its class.
    ROOF: ClassVar[str] = (
        "Roof: {class: hisim.components.generic_pv_system.PVSystem, preset: rooftop, "
        "config: {power_in_watt: 3000}, inputs: [Weather]}"
    )
    #: A PV array at a stated power, which a build sizes without a building.
    PV: ClassVar[str] = "pv: {assembly: pv/array, parameters: {power_in_watt: 5000}}"
    #: The energy manager, and the grid whose meter observes the manager's balance alone (ems_with_battery).
    CONTROL: ClassVar[str] = "control: {assembly: control/ems_self_consumption}"
    GRID_ON_BALANCE: ClassVar[str] = (
        "grid: {assembly: supply/electricity_grid, observes: [{output: TotalElectricityToOrFromGrid}]}"
    )

    @classmethod
    def heating_site(cls, *extra: str) -> str:
        """The site a heating assembly binds to in an expansion: weather, residents, heat distribution, its controller.

        Example: ``Real.heating_site() + imports("gas: {assembly: supply/gas_connection}", ...)``. The
        distribution is the other end of every heating assembly's ``space_heating`` circuit. The site has
        no building, so it expands but does not build; a build of a heating assembly reads a composed file.

        Args:
            extra: Further site entries, each one indented YAML block.

        Returns:
            The text of a version-4 file without imports.
        """
        return site(cls.WEATHER, cls.OCCUPANCY, cls.HEAT_DISTRIBUTION_CONTROLLER, cls.HEAT_DISTRIBUTION, *extra)


#: The test contract of an inline assembly without a numeric parameter.
EMPTY_CONTRACT = "tests: {bounds: [], monotone: []}\n"


def both_libraries() -> AssemblyResolver:
    """A resolver over the mock library, then the real one; their families never collide."""
    return AssemblyResolver([Mocks.LIBRARY, Real.LIBRARY])


class Library:
    """A temporary assembly library for the files one test needs, searched after the mock and the real library."""

    def __init__(self, directory: Path) -> None:
        """Prepares an empty library in a test's temporary directory."""
        self.directory = directory / "library"
        self.directory.mkdir(parents=True, exist_ok=True)

    def add(self, library_path: str, text: str) -> Path:
        """Writes one assembly file, dedenting the text."""
        target = self.directory / f"{library_path}.assembly.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
        return target

    def resolver(self) -> AssemblyResolver:
        """A resolver over the mock library, the real one, then this one."""
        return AssemblyResolver([Mocks.LIBRARY, Real.LIBRARY, self.directory])


def read_system(text: str, origin: str = "inline.energy_system.yaml") -> Tuple[EnergySystemFile, LineIndex]:
    """Reads an inline energy-system file, dedenting it, with its line index."""
    dedented = textwrap.dedent(text).lstrip()
    return EnergySystemReader.build(RawDocument.parse_text(dedented, origin), origin), LineIndex.from_text(
        dedented, origin
    )


def expand_text(text: str, resolver: Optional[AssemblyResolver] = None) -> Tuple[EnergySystemFile, ImportRecord]:
    """Reads and expands an inline energy-system file against both libraries (or a given resolver)."""
    model, lines = read_system(text)
    return expand_imports(model, resolver or both_libraries(), lines=lines)


def site(*entries: str, imports: str = "") -> str:  # pylint: disable=redefined-outer-name  # the file's key
    """A version-4 file with the given site entries (each an indented YAML block) and imports block."""
    body = "\n".join(textwrap.indent(textwrap.dedent(entry).strip(), "  ") for entry in entries)
    text = f"schema_version: 4\nname: inline\ncomponents:\n{body}\n"
    if imports:
        text += "imports:\n" + textwrap.indent(textwrap.dedent(imports).strip(), "  ") + "\n"
    return text


def imports(*lines: str) -> str:
    """An ``imports:`` block of one-line imports, to append to the text of :func:`site`.

    Example: ``site(Real.WEATHER) + imports("pv: {assembly: pv/array}")``.
    """
    return "imports:\n" + "".join(f"  {line}\n" for line in lines)


def build_text(text: str, result_directory: Path, resolver: Optional[AssemblyResolver] = None) -> BuiltEnergySystem:
    """Reads an inline file and builds it — expansion, sizing, construction and wiring — without running it."""
    model, lines = read_system(text)
    parameters = SimulationParameters.one_day_only(2021, 900)
    parameters.result_directory = str(result_directory)
    return EnergySystemExecutor(
        model, parameters, assembly_resolver=resolver or both_libraries(), source_lines=lines
    ).build()
