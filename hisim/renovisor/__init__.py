"""The RenoVisor translation layer: a home inventory plus a renovation package into a simulation.

RenoVisor is a renovation-advice product whose backend asks HiSim what a given renovation of a
given dwelling would do to its energy use, its emissions and its costs. This package is the layer
that turns its vocabulary into HiSim's, in three parts that the contract, not HiSim, fixes the
shape of:

*the contract* (:mod:`hisim.renovisor.contract`) — a vendored copy of the shared API contract:
the inventory schema an incoming request is validated against, the catalogue of renovation
measures, and the insulation-material database.

*the pure translation layer* — :mod:`~hisim.renovisor.vocabulary` (the closed vocabularies),
:mod:`~hisim.renovisor.catalogue` (the only reader of the catalogue),
:mod:`~hisim.renovisor.options` (reading one measure's option values),
:mod:`~hisim.renovisor.registry` (one function per measure),
:mod:`~hisim.renovisor.effects` (the closed effect set and its resolver),
:mod:`~hisim.renovisor.envelope` and :mod:`~hisim.renovisor.materials` (the physics and its data),
:mod:`~hisim.renovisor.inventory` (the path-addressed document) and
:mod:`~hisim.renovisor.application` (applying one package to one inventory). Nothing here runs a
simulation or opens an energy-system file.

*the run* — :mod:`~hisim.renovisor.bindings` (which component of a recorded energy-system file
owns which inventory leaf), :mod:`~hisim.renovisor.occupancy` (the nearest household of the
LoadProfileGenerator catalogue), :mod:`~hisim.renovisor.laws` (the sizing laws the measure layer
leaves pending, and the demand estimates they read), :mod:`~hisim.renovisor.parametriser` (writing
all of it into one recorded base file, within what requirement R4 permits) and
:mod:`~hisim.renovisor.calculate` (one input directory in, one output directory out), which
:mod:`hisim.renovisor.__main__` exposes as a single ``calculate`` command.

*the map* — :mod:`~hisim.renovisor.map` renders the whole translation as one committed HTML page,
including a worked example traced from the inventory through to the parametrised file.

The layering rule the whole package rests on: the measure layer writes the *inventory* and never
names a HiSim component or config field, so a HiSim rename changes one binding rather than 33
measure functions (requirement M6 of ``roadmap/renovisor/measures_v2_requirements.md``).
"""

#: The version of the translation layer itself, distinct from the contract revision
#: ``contract/PINNED.yaml`` records and from the HiSim version. It is echoed in the translation
#: report so a result can be traced back to the rules that produced it.
TRANSLATOR_VERSION: str = "2.0.0-dev"
