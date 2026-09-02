# obsolete/

Code that HiSim no longer uses, kept rather than deleted.

A module lands here when nothing in `system_setups/`, `tests/` or `hisim/` builds it any more and the
component sweep (`roadmap/declarative_energy_systems/p4_component_sweep_requirements.md`, decisions
D-1 … D-32) has decided not to convert it. Moving beats deleting: the physics and the parameter values took
work to get right, and a future reader looking for "did HiSim ever model this?" should find an answer rather
than a gap in the history.

**Nothing here is maintained.** These files are excluded from the quality gates, are not imported by
anything, and are not expected to run against the current interfaces. Treat them as a reference, not as a
library — if one of them becomes useful again, it comes back through `hisim/` with tests, not by being
imported from here.

This directory is a staging area. Its contents move on to the separate obsolete repository, which is why a
file may sit here for a while before disappearing from this one.

## What is here, and why

| Moved | Decision | Reason |
|---|---|---|
| `components/advanced_heat_pump_hplib.py` | D-1 | Zero setup instantiations. Its sibling `MoreAdvancedHeatPumpHPLibConfig` does the same job in plain floats and is what the setups use, while this one is the only class whose sizable fields are `Quantity`-typed — which the sizing kernel cannot express. Converting it would have meant either lowering its types or teaching the kernel `Quantity`, for a class with no consumer. |

`tests/` holds the tests that came with them, so their history stays next to the code they tested.
