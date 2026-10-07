"""Lifecycle cost engine (cost_spec.md): the successor of the per-component capex/opex calculation.

It computes lifecycle costs over a configurable horizon (default 20 years, annuity method per VDI 2067-1) from facts
declared by components, energy flows measured by the meters and versioned data files (prices, lifetimes, subsidy
schemes). This module re-exports only what a caller outside the package needs: the declaration types, the register of
existing assets, `EconomicParameters` and `UncertainValue`. The engine and reporting layers are imported by module
path, so `hisim/component.py` can import `economics.facts` without loading the engine. ``hisim/economics/README.md``
has the package tour.
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
