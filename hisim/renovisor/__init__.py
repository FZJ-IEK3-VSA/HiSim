"""RenoVisor -> HiSim translator.

Takes a JSON request with a home inventory (RenoVisor contract, see ``scripts/hisim_spec.md``),
selects a ``*_building_sizer`` system setup, generates a ``ModularHouseholdConfig`` parameter
file, runs the simulation and submits selected result files to a server via REST.

See ``spec.md`` in this package for the full specification.
"""

TRANSLATOR_VERSION = "1.0.0"
