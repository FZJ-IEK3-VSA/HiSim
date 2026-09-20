"""The RenoVisor translation layer: one calculation request into one HiSim simulation.

RenoVisor is a renovation-advice product whose backend asks HiSim what a given renovation of a
given dwelling would do to its energy use, its emissions and its costs. This package is the
layer that turns its vocabulary into HiSim's. One request is one house inventory plus one list
of catalogue measures, and the baseline is the same request with an empty list.

The pipeline, and the module that owns each step::

    request ──validate──▶ Request ──apply──▶ renovated house ──translate──▶ *.energy_system.yaml
                                                     │                              │
                                                     └──────▶ mapping_report.json   └──▶ run ──▶ result.json

*the vocabulary* -- :mod:`~hisim.renovisor.vocabulary` holds every closed string set a request
may carry, spelled as the measure catalogue spells it and named as HiSim would name it.

*the contract* (:mod:`hisim.renovisor.contract`) -- vendored copies of the measure catalogue,
the material database, the request schema, the worked mockup and the capability document's own
shape, each pinned to the revision it was taken from.

*the pure layers* -- :mod:`~hisim.renovisor.request` (the schema, the frozen catalogue table and
every semantic check), :mod:`~hisim.renovisor.apply` (one function per measure, over a deep copy
of the house), :mod:`~hisim.renovisor.envelope` (the insulation arithmetic),
:mod:`~hisim.renovisor.tabula` (which archetype a dwelling is simulated as),
:mod:`~hisim.renovisor.translate` (the house into one recorded twin),
:mod:`~hisim.renovisor.report` (the account of every leaf) and
:mod:`~hisim.renovisor.whitelist` (the one list of what is accepted and not acted on). Nothing
here runs a simulation.

*the run* -- :mod:`~hisim.renovisor.simulation` (the release's own parameters) and
:mod:`~hisim.renovisor.run` (validate, translate, simulate, assemble), which
:mod:`hisim.renovisor.__main__` exposes as five commands.

*the result* -- :mod:`~hisim.renovisor.result`, :mod:`~hisim.renovisor.kpis`,
:mod:`~hisim.renovisor.costs`, :mod:`~hisim.renovisor.layers` and
:mod:`~hisim.renovisor.provenance`: ``result.json``, every value carrying where it came from.

*the announcement* -- :mod:`~hisim.renovisor.capabilities` runs the whole probe set through the
pure layers and aggregates it into the document the backend serves per image;
:mod:`~hisim.renovisor.map` renders the same probe run as one committed HTML page.

The rule the whole package rests on: **fail loudly, except for what is written down.** An
invalid request is refused by name with every problem at once; a feature the translator has not
implemented is a note and the calculation runs, but only if
``hisim/renovisor/not_implemented_yet.yaml`` says so; anything else that cannot be mapped fails
the translator's own build rather than a user's request.
"""

#: The version of the translation layer itself, distinct from the contract revision
#: ``contract/PINNED.yaml`` records and from the HiSim version. It is echoed in the mapping
#: report and in the capability document so a result can be traced back to the rules that
#: produced it, and the backend gates artifact requirements on it. Meaning of the steps:
#: ``2.0.0`` the v2 request and outputs of the frontend spec; ``2.1.0`` every run writes the three
#: economics artifacts (``economic_inputs.json``, ``lifecycle_costs.json``,
#: ``cost_provenance.json``) also inside a container, because post-processing no longer strips
#: options there (shared todo H2). The backend reads no version and requires no artifact (owner
#: decision of 2026-09-20); the step is a changelog marker, nothing keys off it.
TRANSLATOR_VERSION: str = "2.1.0"
