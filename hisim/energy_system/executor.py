"""Running an energy-system file: the lifecycle from a path on disk to a finished simulation.

This is the module that puts the whole package together. Reading a file, expanding its groups,
checking its structure, checking it against the classes it names, building and sizing every
configuration, constructing the components, connecting them and finally running the simulation
are eight separate stages with eight separate failure modes, each owned by its own module. What
lives here is the order they run in, the small number of decisions that only make sense once the
whole sequence is in view, and the two entry points a caller reaches for.

The order is not arbitrary. Groups are expanded before anything is validated, so that a
switched-off component's class never has to import and the off rule cannot leak into a later
stage. Structural validation runs before any class is imported, so that a file can be checked
for shape without HiSim's component tree. Sizing completes before the first component exists, so
that a contradiction is reported with nothing built. And the connections are checked only after
construction, because a component's ports come into being inside its constructor.

Two things belong to this module alone. The **simulation parameters** — the period, the
resolution, the post-processing to run — live in their own file, because the same energy system
is run over different periods and mixing the two would force a copy of the system per run; both
the JSON spelling the existing setups use and the YAML spelling new files prefer are accepted.
And a file carrying a ``metadata`` block is refused on a plain run: such a block is written by a
generator, so its presence means the caller handed in a generated record rather than an authored
file, which is a legitimate thing to do but has to be said out loud.
"""

# clean

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from hisim import log
from hisim import simulator as sim
from hisim.energy_system.audit import build_audit, write_audit
from hisim.energy_system.bindings import ClassBindings
from hisim.energy_system.classes import validate_classes
from hisim.energy_system.comments import AnnotatedEmitter, write_record
from hisim.energy_system.configure import ConfiguredSystem, configure_energy_system
from hisim.energy_system.errors import (
    EnergySystemErrorId,
    EnergySystemFormatError,
)
from hisim.energy_system.groups import ExpansionRecord, expand_groups
from hisim.energy_system.loader import parse_energy_system
from hisim.energy_system.model import EnergySystemFile
from hisim.energy_system.path_resolver import PathResolver
from hisim.energy_system.record import realize, verify_rerun
from hisim.energy_system.validation import validate_structure
from hisim.energy_system.wiring import WiredSystem, wire_energy_system
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters


class SimulationParametersReader:
    """Reads the run's period, resolution and post-processing from its own file.

    An energy-system file says what the household *is*; this file says what to *do* with it. The
    two are separate on purpose, so the same system can be run over a day and over a year without
    a second copy of it, and so a change of resolution never touches a line describing a boiler.

    Both spellings are read. New files are YAML like everything else in this format; the JSON
    files the Python setups already ship keep working unchanged, because there is no reason to
    make a caller convert one to run an energy system. The two carry exactly the same keys — YAML
    is a superset of JSON — so one parser handles both once the text is loaded.
    """

    #: Suffixes read as YAML. JSON is loaded through the same parser, which accepts it, but the
    #: distinction is kept so an unknown suffix can be named in the message.
    YAML_SUFFIXES: Tuple[str, ...] = (".yaml", ".yml")

    #: Suffix of the parameter files the Python setups already ship.
    JSON_SUFFIX: str = ".json"

    #: Keys holding a date, which arrive as ISO strings and are handed over as datetimes.
    DATE_KEYS: Tuple[str, ...] = ("start_date", "end_date")

    #: Key holding the post-processing options, which arrive as member names.
    OPTIONS_KEY: str = "post_processing_options"

    @classmethod
    def read(cls, path: Path) -> SimulationParameters:
        """Reads one simulation-parameters file into the object every component is handed.

        Args:
            path: Path of the ``*.simulation.yaml`` or ``*.simulation.json`` file.

        Returns:
            The parameters of the run.

        Raises:
            EnergySystemFormatError: ``EF-00`` for an unreadable suffix, ``EF-07`` if the file is
                not a mapping or a value cannot be interpreted.
        """
        suffix = path.suffix.lower()
        if suffix not in cls.YAML_SUFFIXES + (cls.JSON_SUFFIX,):
            raise EnergySystemFormatError(
                EnergySystemErrorId.UNSUPPORTED_FORMAT,
                str(path),
                f"'{suffix or path.name}' is not a simulation-parameters format.",
                alternatives=list(cls.YAML_SUFFIXES + (cls.JSON_SUFFIX,)),
                alternatives_label="suffixes",
            )
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, Mapping):
            raise EnergySystemFormatError(
                EnergySystemErrorId.MALFORMED_BLOCK,
                str(path),
                "a simulation-parameters file is a mapping of parameter names to values.",
            )
        return cls.build(dict(loaded), str(path))

    @classmethod
    def build(cls, values: Dict[str, Any], location: str) -> SimulationParameters:
        """Turns the raw parameter mapping into the parameters object.

        Two kinds of value need interpreting: the dates, which are written as ISO strings, and
        the post-processing options, which are written as member names so that a file says
        ``PLOT_LINE`` rather than an integer nobody can read back.

        Args:
            values: The mapping the file holds.
            location: Path of the file, used in the message.

        Returns:
            The parameters of the run.

        Raises:
            EnergySystemFormatError: ``EF-07`` if a date or an option cannot be interpreted, or
                if a key is not a parameter at all.
        """
        prepared = dict(values)
        for key in cls.DATE_KEYS:
            if isinstance(prepared.get(key), str):
                prepared[key] = datetime.datetime.fromisoformat(prepared[key])
        prepared[cls.OPTIONS_KEY] = cls._options(prepared.get(cls.OPTIONS_KEY) or [], location)
        try:
            return SimulationParameters(**prepared)
        except TypeError as error:
            raise EnergySystemFormatError(
                EnergySystemErrorId.MALFORMED_BLOCK,
                location,
                f"the simulation parameters could not be built: {error}",
            ) from error

    @classmethod
    def _options(cls, written: Any, location: str) -> List[PostProcessingOptions]:
        """Decodes the post-processing options from their member names.

        Args:
            written: The list of names the file holds.
            location: Path of the file, used in the message.

        Returns:
            The options in the written order.

        Raises:
            EnergySystemFormatError: ``EF-07`` if a name belongs to no option.
        """
        decoded: List[PostProcessingOptions] = []
        for name in written:
            option = getattr(PostProcessingOptions, str(name), None)
            if not isinstance(option, PostProcessingOptions):
                raise EnergySystemFormatError(
                    EnergySystemErrorId.MALFORMED_BLOCK,
                    location,
                    f"'{name}' is not a post-processing option.",
                    alternatives=[entry.name for entry in PostProcessingOptions],
                    alternatives_label="options",
                    offending_value=str(name),
                )
            decoded.append(option)
        return decoded


@dataclass(frozen=True)
class BuiltEnergySystem:
    """Everything one run of an energy-system file produced short of the simulation itself.

    Held together rather than returned piecemeal because a run record is written from all of it
    at once: the file as it was after group expansion, the record of what the off rule removed,
    the resolved configurations with the sizing report that says where every number came from,
    the components and the connections that were made, and the warnings the run should print.

    The simulator is the object a caller runs; everything else is what a caller writes down.
    The three trailing fields exist only for that writing: the two file names a record's metadata
    states, the resolver that turns local paths back into portable references, and whether the
    caller declared this to be the re-execution of a record, which is what turns the reproduction
    guarantee from a claim into a check.
    """

    model: EnergySystemFile
    expansion: ExpansionRecord
    bindings: ClassBindings
    configured: ConfiguredSystem
    wired: WiredSystem
    simulator: sim.Simulator
    warnings: Tuple[str, ...]
    source_energy_system: str = ""
    source_simulation_parameters: str = ""
    path_resolver: Optional[PathResolver] = None
    rerun: bool = False


class EnergySystemExecutor:
    """Runs the stages of the lifecycle in order and hands back everything they produced.

    One executor serves one run of one file. The stages are separate methods so that a caller
    wanting less than a full run — a tool inspecting what a file would build, a test checking
    that configurations size correctly — can stop where it likes, and :meth:`build` is the whole
    sequence for the callers that want all of it.

    Nothing here decides anything a stage could decide for itself. The two judgements that
    genuinely belong to the sequence are the ``metadata`` gate, which needs to know whether the
    caller asked for a re-run, and the registration of the components with the simulator, which
    needs both the file's order and the simulator's own bookkeeping.
    """

    #: Whether the simulator should apply its own automatic default connections when a component
    #: is registered. It must not: in this format every connection is an item of the file, and
    #: letting the older mechanism run as well would add wires the file does not declare.
    CONNECT_AUTOMATICALLY: bool = False

    def __init__(
        self,
        model: EnergySystemFile,
        simulation_parameters: SimulationParameters,
        *,
        path_resolver: Optional[PathResolver] = None,
        source_directory: str = "",
        source_filename: str = "",
        source_energy_system: str = "",
        source_simulation_parameters: str = "",
        rerun: bool = False,
    ) -> None:
        """Prepares an executor for one loaded energy system.

        Args:
            model: The energy system as read from its file, before group expansion.
            simulation_parameters: Parameters of the run, handed to every component.
            path_resolver: The registry expanding ``${var}`` path references; the default
                registry for this machine when omitted.
            source_directory: Directory reported to the simulator as the run's location.
            source_filename: File name reported to the simulator as the run's name.
            source_energy_system: Path of the energy-system file, as a run record names it.
            source_simulation_parameters: Path of the parameters file, likewise.
            rerun: Whether the caller declared this to be the re-execution of a record, which
                is what makes the reproduction guarantee checkable.
        """
        self.model = model
        self.simulation_parameters = simulation_parameters
        self.path_resolver = path_resolver
        self.source_directory = source_directory
        self.source_filename = source_filename
        self.source_energy_system = source_energy_system
        self.source_simulation_parameters = source_simulation_parameters
        self.rerun = rerun

    def build(self) -> BuiltEnergySystem:
        """Runs every stage from the loaded file to a wired, registered simulator.

        Returns:
            The built system, its simulator ready for the run.

        Raises:
            EnergySystemError: For any condition of the error catalogue; the file is rejected at
                the earliest stage that can decide it, and nothing is constructed before the
                sizing of the whole system has succeeded.
        """
        expanded, expansion = expand_groups(self.model)
        validate_structure(expanded)
        bindings = validate_classes(expanded)
        configured = configure_energy_system(
            expanded, bindings=bindings, path_resolver=self.path_resolver
        )
        wired, wiring_warnings = wire_energy_system(
            expanded, configured, self.simulation_parameters
        )
        simulator = self.register(expanded, wired)
        warnings = configured.warnings + wiring_warnings
        for warning in warnings:
            log.warning(warning)
        return BuiltEnergySystem(
            model=expanded,
            expansion=expansion,
            bindings=bindings,
            configured=configured,
            wired=wired,
            simulator=simulator,
            warnings=warnings,
            source_energy_system=self.source_energy_system,
            source_simulation_parameters=self.source_simulation_parameters,
            path_resolver=self.path_resolver,
            rerun=self.rerun,
        )

    def register(self, model: EnergySystemFile, wired: WiredSystem) -> sim.Simulator:
        """Creates the simulator and registers every component in file order.

        File order decides the order of the global output list and therefore the column order of
        a result frame, so it is preserved exactly. The simulator's automatic default connection
        is switched off, because every connection this format makes is an item of the file and
        has already been made.

        Args:
            model: The expanded energy system, for its name.
            wired: The constructed and connected components.

        Returns:
            The simulator holding every component of the system.
        """
        simulator: sim.Simulator = sim.Simulator(
            module_directory=self.source_directory,
            module_filename=self.source_filename,
            setup_function=model.name,
            my_module_config=None,
            my_simulation_parameters=self.simulation_parameters,
        )
        for _name, component in wired.components:
            simulator.add_component(component, connect_automatically=self.CONNECT_AUTOMATICALLY)
        return simulator

    @classmethod
    def check_metadata(cls, model: EnergySystemFile, location: str, rerun: bool) -> None:
        """Refuses a file carrying a ``metadata`` block unless the caller asked for a re-run.

        A ``metadata`` block is written by a generator, never by hand, so a file that has one is
        a record of a previous run rather than an authored energy system. Re-running such a
        record is a legitimate and useful thing to do — it is how a run is reproduced exactly —
        but it is a different intent from running an authored file, and silently accepting both
        would make it impossible to tell which one happened.

        Args:
            model: The loaded energy system.
            location: Path of the file, used in the message.
            rerun: Whether the caller stated that it means to re-run a record.

        Raises:
            EnergySystemFormatError: ``EF-09`` if the file carries metadata and ``rerun`` is
                not set.
        """
        if model.metadata is None or rerun:
            return
        raise EnergySystemFormatError(
            EnergySystemErrorId.METADATA_ON_A_PLAIN_RUN,
            location,
            "this file carries a 'metadata' block, which only a generated run record has.",
            remedy=(
                "Re-run it explicitly if that is what you mean, or strip the metadata block to "
                "treat it as an authored energy system."
            ),
        )


def build_energy_system(
    energy_system_path: Any,
    simulation_parameters: SimulationParameters,
    *,
    path_resolver: Optional[PathResolver] = None,
    rerun: bool = False,
    simulation_parameters_path: Any = "",
) -> BuiltEnergySystem:
    """Loads an energy-system file and builds everything it describes, without running it.

    The half of the lifecycle a tool wants: a caller inspecting what a file would build, a test
    checking that a system wires, or a run that is about to write its record before starting.
    Simulation parameters are handed in rather than read, so that the same system can be built
    against parameters a caller constructed itself.

    Args:
        energy_system_path: Path of the ``*.energy_system.yaml`` file.
        simulation_parameters: Parameters of the run.
        path_resolver: The registry expanding ``${var}`` path references; the default registry
            for this machine when omitted.
        rerun: Whether a generated run record is being re-run, which is what makes a
            ``metadata`` block acceptable.
        simulation_parameters_path: Path of the parameters file, when the caller read them from
            one; a run record names it so that the pair which reproduces the run is written
            down, and nothing else uses it.

    Returns:
        The built system, its simulator registered and wired.

    Raises:
        EnergySystemError: For any condition of the error catalogue.
    """
    path = Path(energy_system_path)
    model = parse_energy_system(path)
    EnergySystemExecutor.check_metadata(model, str(path), rerun)
    executor = EnergySystemExecutor(
        model=model,
        simulation_parameters=simulation_parameters,
        path_resolver=path_resolver,
        source_directory=str(path.parent.resolve()),
        source_filename=path.stem,
        source_energy_system=str(path),
        source_simulation_parameters=str(simulation_parameters_path or ""),
        rerun=rerun,
    )
    return executor.build()


def write_records(built: BuiltEnergySystem, result_directory: str) -> Tuple[str, str, str]:
    """Writes the three artifacts that describe one built energy system.

    The realized record states what was built, annotated with where every number came from; the
    audit companion states the same provenance as data nothing has to parse out of a comment; and
    the wire log lists every connection that was made. All three are written before the first
    timestep runs, so a run that crashes halfway still leaves behind a complete description of
    the system it was running — which is when such a description is worth the most.

    Nothing has to have been simulated for this to work. A caller that only built a system, to
    see what a file would produce, gets the same three files.

    Args:
        built: The finished build.
        result_directory: Where the artifacts go; created when it does not exist.

    Returns:
        The paths of the record, the audit and the wire log.

    Raises:
        EnergySystemRecordError: ``EF-60`` when the record would not be fully concrete, and
            ``EF-61`` when this was a re-run that failed to reproduce the record it was given.
    """
    record = realize(built)
    if built.rerun:
        verify_rerun(built, record)
    os.makedirs(result_directory, exist_ok=True)
    record_path = write_record(
        record, build_audit(built), os.path.join(result_directory, AnnotatedEmitter.RECORD_FILENAME)
    )
    audit_path, wire_path = write_audit(built, result_directory)
    log.information(
        f"Wrote the realized record of '{built.model.name}' to '{record_path}', its audit to "
        f"'{audit_path}' and its wire log to '{wire_path}'."
    )
    return record_path, audit_path, wire_path


def run_energy_system(
    energy_system_path: Any,
    simulation_parameters_path: Any,
    result_directory: Optional[str] = None,
    rerun: bool = False,
) -> BuiltEnergySystem:
    """Runs one energy-system file over one simulation period and writes its results.

    The whole lifecycle in one call, and the entry point every caller that just wants a run
    reaches for: the two files in, a finished simulation and its result directory out. What the
    run computes and how much it plots afterwards is governed entirely by the post-processing
    options of the parameters file, exactly as it is for a Python setup.

    Args:
        energy_system_path: Path of the ``*.energy_system.yaml`` file.
        simulation_parameters_path: Path of the ``*.simulation.yaml`` or ``*.simulation.json``
            file holding the period, the resolution and the post-processing options.
        result_directory: Where to write the results; the parameters file's own choice, or the
            directory the simulator derives from the system's name, when omitted.
        rerun: Whether a generated run record is being re-run, which is what makes a
            ``metadata`` block acceptable.

    Returns:
        The built system, after the simulation has finished; its simulator's parameters carry
        the result directory the run actually wrote to.

    Raises:
        EnergySystemError: For any condition of the error catalogue, raised before the first
            component is constructed wherever the condition allows it.
    """
    parameters = SimulationParametersReader.read(Path(simulation_parameters_path))
    if result_directory is not None:
        parameters.result_directory = result_directory
        os.makedirs(result_directory, exist_ok=True)
    built = build_energy_system(
        energy_system_path,
        parameters,
        rerun=rerun,
        simulation_parameters_path=simulation_parameters_path,
    )
    write_records(built, built.simulator.get_simulation_parameters().result_directory)
    log.information(f"Starting the simulation of '{built.model.name}'.")
    built.simulator.run_all_timesteps()
    log.information(
        f"Finished the simulation of '{built.model.name}'; results are in "
        f"{built.simulator.get_simulation_parameters().result_directory}."
    )
    return built
