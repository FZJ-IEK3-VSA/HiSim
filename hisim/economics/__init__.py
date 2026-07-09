"""Lifecycle cost engine (cost_spec.md): parallel successor of the capex/opex calculation.

Public API surface. The engine is strictly parallel to the legacy cost path until the
Phase-7 cutover: it never calls ``get_cost_capex``/``get_cost_opex`` and only writes new
files. See ``cost_spec.md`` and ``cost_module_issues.md`` at the repo root.
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
