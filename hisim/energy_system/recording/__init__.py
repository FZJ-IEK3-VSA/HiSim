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
      recorded file carries, and the check that the file really builds again.

What a recording never contains is as much of the definition as what it does. No ``AUTO``, because
a recording states what ran; no ``sizing_sources``, because where a number came from is not
observable; no ``groups`` and no ``variants``, because which parts of a household belong together
is a judgement a person makes and not something one run can be asked about.

This subpackage is deliberately not re-exported by :mod:`hisim.energy_system`. Reading a file must
stay possible without importing HiSim's component tree, and a recorder necessarily imports it.
"""

# clean

from hisim.energy_system.recording.builder import EnergySystemBuilder, PortablePathGuard, build
from hisim.energy_system.recording.configs import EntryConfigWriter
from hisim.energy_system.recording.inputs import InputItemWriter
from hisim.energy_system.recording.names import RecordedNames
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

__all__ = [
    "EnergySystemBuilder",
    "EntryConfigWriter",
    "InputItemWriter",
    "ObservedComponent",
    "ObservedDispatch",
    "ObservedFeed",
    "PortablePathGuard",
    "RecordedFileWriter",
    "RecordedNames",
    "RecordedSystem",
    "RecordingResult",
    "RecordingSession",
    "SystemObserver",
    "build",
    "observe",
    "record_setup",
]
