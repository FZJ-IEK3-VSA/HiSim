"""Energy balances per component and one connected Sankey graph of the house, from the declared energy ports.

Epic hisim-energy-sankeys-9uoo; the graph model is ``roadmap/complete_energy_sankeys_spec.md`` §4. ``ports`` reads
the declared ports and the meters of a run and finds each port's peer through the wiring: a meter that reads it,
also through the exempt controllers, or a component that moves energy, else the port's carrier default; ``check``
closes each component's balance and each link between declared components (both sides must book it); ``meters``
checks per step that each meter publishes what it reads from the declared ports linked to it, and that every
metered port passes a meter; ``sankey`` draws the yearly flows as one connected graph with one filter per carrier;
``export`` fails the run when they do not hold and writes the report and the Sankeys into the result directory. The
check runs in every simulation; :class:`~hisim.postprocessingoptions.PostProcessingOptions` ``EXPORT_ENERGY_BALANCE``
only writes the files.

Coverage: the heat generators, the electricity devices and the occupancy declare their ports, the meters their
published exchanges; the water storages and the heat distribution system behind a buffer declare their water circuits
as ``HydronicPort`` ends at both ends of each circuit (PR 3 of the spec's §9, hydronic stage E, hisim-hydronics-fxix.6).
The building and the rest follow in the other PRs of §9, the distribution system without a buffer with hisim-pnw7;
until then they are drawn as undeclared nodes. :meth:`ports.Wiring.refuse_a_broken_wiring` refuses a wiring that breaks
a rule of the ports before the run simulates.
"""

from hisim.postprocessing.energy_balance.check import (
    BalanceCheck,
    ComponentBalance,
    EnergyBalanceError,
    LinkBalance,
    Tolerance,
)
from hisim.postprocessing.energy_balance.export import EnergyBalanceReport
from hisim.postprocessing.energy_balance.meters import MeterBalance, MeterCheck
from hisim.postprocessing.energy_balance.ports import (
    CircuitColumns,
    DeclaredPorts,
    EnergyPortWiringError,
    Exemptions,
    Peer,
    PeerKind,
    PortSeries,
    Wiring,
)

__all__ = [
    "BalanceCheck",
    "CircuitColumns",
    "ComponentBalance",
    "DeclaredPorts",
    "EnergyBalanceError",
    "EnergyPortWiringError",
    "EnergyBalanceReport",
    "Exemptions",
    "LinkBalance",
    "MeterBalance",
    "MeterCheck",
    "Peer",
    "PeerKind",
    "PortSeries",
    "Tolerance",
    "Wiring",
]
