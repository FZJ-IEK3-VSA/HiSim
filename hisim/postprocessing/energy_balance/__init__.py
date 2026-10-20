"""Energy balances per component and one connected Sankey graph of the house, from the declared energy ports.

Epic hisim-9uoo; the graph model is ``roadmap/complete_energy_sankeys_spec.md`` §4. ``ports`` reads the declared
ports of a run and finds each port's peer, skipping the exempt components (controllers and meters move no energy)
and falling back to the port's carrier default; ``check`` closes each component's balance and each link between
declared components (both sides must book it); ``bus`` forms the house electricity bus, derives the grid import and
export per step and cross-checks them with the meter; ``sankey`` draws the yearly flows as one connected graph with
one filter per carrier; ``export`` fails the run when they do not hold and writes the report and the Sankeys into the
result directory. The check runs in every simulation; :class:`~hisim.postprocessingoptions.PostProcessingOptions`
``EXPORT_ENERGY_BALANCE`` only writes the files.

Coverage: the water storages and the heat distribution system declare their ports with the hydronic coupling
(hisim-fxix.6), as ``HydronicPort`` on (mass flow, temperature); the electricity devices, the building and the rest
follow in the PRs of the spec's §9. Until then they are drawn as undeclared nodes.
"""

from hisim.postprocessing.energy_balance.bus import BusCheck, ElectricityBus, MeterCrossCheck
from hisim.postprocessing.energy_balance.check import (
    BalanceCheck,
    ComponentBalance,
    EnergyBalanceError,
    LinkBalance,
    Tolerance,
)
from hisim.postprocessing.energy_balance.export import EnergyBalanceReport
from hisim.postprocessing.energy_balance.ports import DeclaredPorts, Exemptions, Peer, PeerKind, PortSeries

__all__ = [
    "BalanceCheck",
    "BusCheck",
    "ComponentBalance",
    "DeclaredPorts",
    "ElectricityBus",
    "EnergyBalanceError",
    "EnergyBalanceReport",
    "Exemptions",
    "LinkBalance",
    "MeterCrossCheck",
    "Peer",
    "PeerKind",
    "PortSeries",
    "Tolerance",
]
