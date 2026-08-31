"""Recording a Python setup as an energy-system file: the direction the executor does not run.

Everything in :mod:`hisim.energy_system` reads a file and builds a system from it. This subpackage
runs the other way: it takes a system a Python ``setup_function`` has already built and writes the
file that describes it. That is what turns twenty-odd Python setups into declarative twins without
anybody retyping them, and it is why the recorder observes a run rather than parsing source — the
setups compute, and the only thing that reliably knows what they computed is the objects they left
behind.

Three stages, in this order, and separate on purpose:

    - :mod:`hisim.energy_system.recording.observe` — a live, wired simulator into plain data. The
      only stage that touches HiSim's runtime, and it touches it read-only.
    - :mod:`hisim.energy_system.recording.builder` — that data into an ``EnergySystemFile``. Pure,
      which is where every test of the recorder's judgement lives, helped by
      :mod:`~hisim.energy_system.recording.names` (can this component be addressed at all),
      :mod:`~hisim.energy_system.recording.configs` (preset plus deviation, or a full block) and
      :mod:`~hisim.energy_system.recording.inputs` (bare item, aggregator feed or explicit wire).
    - :mod:`hisim.energy_system.recording.session` — the order they run in, the comment header a
      recorded file carries, and the check that the file really builds again, helped by
      :mod:`~hisim.energy_system.recording.parameters`, which decides whether the run's parameters
      are already described by a file beside it or need one of their own.

What a recording never contains is as much of the definition as what it does. No ``AUTO``, because
a recording states what ran; no ``sizing_sources``, because where a number came from is not
observable; no ``groups`` and no ``variants``, because which parts of a household belong together
is a judgement a person makes and not something one run can be asked about.

That judgement has a second pass of its own, and it is built from the same three stages run several
times over. :mod:`~hisim.energy_system.recording.probes` reads the authored list of module
configurations a setup is recorded under; :mod:`~hisim.energy_system.recording.probe_session` records
each of them in its own process; :mod:`~hisim.energy_system.recording.matrix` reduces the recordings
to the three-state table a person is asked about;
:mod:`~hisim.energy_system.recording.workbook` writes that table out as a spreadsheet and
:mod:`~hisim.energy_system.recording.workbook_import` reads the answer back;
:mod:`~hisim.energy_system.recording.grouping` is the answer as a value, with its committed form in
``grouping_io`` and its consistency rules in ``grouping_checks``; and
:mod:`~hisim.energy_system.recording.regrouping` applies it, producing the grouped file and proving
it against every recording the probes produced.

This subpackage is deliberately not re-exported by :mod:`hisim.energy_system`. Reading a file must
stay possible without importing HiSim's component tree, and a recorder necessarily imports it.
"""

# clean

from hisim.energy_system.recording.builder import EnergySystemBuilder, PortablePathGuard, build
from hisim.energy_system.recording.configs import EntryConfigWriter
from hisim.energy_system.recording.inputs import InputItemWriter
from hisim.energy_system.recording.names import RecordedNames
from hisim.energy_system.recording.parameters import (
    ParameterFileLibrary,
    ParameterFileName,
    ParameterFileWriter,
    ParameterNormalisation,
    ParameterReference,
    normalise_parameters,
)
from hisim.energy_system.recording.observe import (
    ObservedComponent,
    ObservedDispatch,
    ObservedFeed,
    RecordedSystem,
    SystemObserver,
    observe,
)
from hisim.energy_system.recording.session import (
    RecordedFileWriter,
    RecordingResult,
    RecordingSession,
    record_setup,
)
from hisim.energy_system.recording.grouping import (
    Assignment,
    AssignmentKind,
    ConfigurationSelection,
    Grouping,
)
from hisim.energy_system.recording.grouping_checks import check_grouping
from hisim.energy_system.recording.grouping_io import dump_grouping, read_grouping
from hisim.energy_system.recording.grouping_report import ColumnVerdict, CombinationSpace, GroupingReport
from hisim.energy_system.recording.matrix import CellState, ComponentRow, ProbeMatrix, ProbeRecording
from hisim.energy_system.recording.probe_session import GroupingPass, ProbeRunner
from hisim.energy_system.recording.probes import ModuleConfigMaterialiser, ProbeConfiguration, ProbeList
from hisim.energy_system.recording.regrouping import ColumnRealizer, GroupedSystemBuilder, Knob, apply_grouping
from hisim.energy_system.recording.workbook import WorkbookLayout, WorkbookWriter, write_workbook
from hisim.energy_system.recording.workbook_import import WorkbookReader, read_workbook

__all__ = [
    "Assignment",
    "AssignmentKind",
    "CellState",
    "ColumnRealizer",
    "ColumnVerdict",
    "CombinationSpace",
    "ComponentRow",
    "ConfigurationSelection",
    "EnergySystemBuilder",
    "GroupedSystemBuilder",
    "Grouping",
    "GroupingPass",
    "GroupingReport",
    "Knob",
    "ModuleConfigMaterialiser",
    "ProbeConfiguration",
    "ProbeList",
    "ProbeMatrix",
    "ProbeRecording",
    "ProbeRunner",
    "WorkbookLayout",
    "WorkbookReader",
    "WorkbookWriter",
    "apply_grouping",
    "check_grouping",
    "dump_grouping",
    "read_grouping",
    "read_workbook",
    "write_workbook",
    "EntryConfigWriter",
    "InputItemWriter",
    "ObservedComponent",
    "ObservedDispatch",
    "ObservedFeed",
    "ParameterFileLibrary",
    "ParameterFileName",
    "ParameterFileWriter",
    "ParameterNormalisation",
    "ParameterReference",
    "PortablePathGuard",
    "RecordedFileWriter",
    "RecordedNames",
    "RecordedSystem",
    "RecordingResult",
    "RecordingSession",
    "SystemObserver",
    "build",
    "normalise_parameters",
    "observe",
    "record_setup",
]
