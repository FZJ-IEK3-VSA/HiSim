"""Lifecycle cost engine (cost_spec.md): parallel successor of the capex/opex calculation.

Public API surface. The engine is strictly parallel to the legacy cost path until the
Phase-7 cutover: it never calls ``get_cost_capex``/``get_cost_opex`` and only writes new
files. See ``cost_spec.md`` and ``cost_module_issues.md`` at the repo root.

The package computes lifecycle costs over a configurable horizon (default 20 years, annuity method
per VDI 2067-1 / DIN EN 15459-1) from three kinds of input: *facts* declared by components (what am
I, how big), *energy flows* measured by the meters (what crossed the system boundary), and
*versioned data files* (prices, lifetimes, subsidy schemes). Everything else — discounting,
replacements, subsidies, actor splits, uncertainty bands — happens centrally in
`evaluator.EconomicEvaluator`, which is a pure function of its inputs.

Re-exported here is only what a *caller from outside the package* needs: the declaration types a
component or meter fills in (`ComponentCostFacts`, `EnergyFlowFacts`, `BillingDeterminants`,
`CostRelevance`), the brownfield register (`ExistingAsset`, `ExistingAssetRegister`), the assumption
record (`EconomicParameters`), and the value type every monetary figure uses (`UncertainValue`,
`Slot`). The engine, timeline, database, subsidy and reporting layers are deliberately *not*
re-exported — they are imported by module path, which keeps this file's import cost low enough that
`hisim/component.py` can depend on `economics.facts` without pulling the engine into every
simulation.

For the package tour — pipeline diagram, perspective model, per-module table and the data-file
layout — see ``hisim/economics/README.md``.
"""

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.facts import (
    BillingDeterminants,
    ComponentCostFacts,
    CostRelevance,
    EnergyFlowFacts,
    ExistingAsset,
    ExistingAssetRegister,
)
from hisim.economics.parameters import EconomicParameters
from hisim.economics.uncertainty import Slot, UncertainValue

__all__ = [
    "BillingDeterminants",
    "ComponentCostFacts",
    "CostRelevance",
    "EconomicParameters",
    "EnergyCarrier",
    "EnergyFlowFacts",
    "ExistingAsset",
    "ExistingAssetRegister",
    "Slot",
    "UncertainValue",
]
