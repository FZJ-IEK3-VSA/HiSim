"""The matplotlib Sankey figure of a run's ``overall`` energy view (the matplotlib Sankey spec).

This package reads a run's inputs (``inputs``: the overall view, the balance facts, the KPIs and the realized
configuration), folds the view into the drawn graph of one trunk per component, meter or undeclared node and decides
every node's role, the main chain and the trees (``graph``), computes the values written on the figure (``metrics``)
and holds the presentation table (``style``). The renderer and the automatic layout build on it.
"""

from hisim.postprocessing.energy_balance.figure.graph import DrawnGraph, GraphBuilder, Role
from hisim.postprocessing.energy_balance.figure.inputs import SankeyInputError, SankeyInputs

__all__ = ["DrawnGraph", "GraphBuilder", "Role", "SankeyInputError", "SankeyInputs"]
