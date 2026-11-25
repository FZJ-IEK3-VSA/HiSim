"""The types that cross the seams between HiSim's module groups, as plain data.

A contract is a frozen record that one group fills and another reads, so that neither imports the other: for
example, post-processing's energy balance fills :class:`~hisim.contracts.port_energies.PortEnergies` and the cost
engine reads it. A contract imports only the leaves (``hisim.loadtypes`` and the like), the physical constants, the
configuration layer and the simulation parameters; the allowed imports are listed in ``system_docs/module_graph.yaml``.
"""
