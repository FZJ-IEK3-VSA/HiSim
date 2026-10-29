"""What the shell requires of an engine, and nothing more.

An engine is one country's method: its norm tables and norm defaults as data, its computation, its
result keys. The shell requires of it only what :class:`EpcEngine` states (design §5.4, R10): its
country, its pinned edition, whether its method has a site climate, a way to load its data once,
and ``compute(building) -> EpcResult``. How an engine is laid out inside is its own matter; the
Austrian stage files are the pattern later engines follow where it fits their norm (design §3.4).

An engine knows no file of HiSim and no report format. It reads the site fields its method needs
from ``building.site`` and refuses a missing or inadmissible one itself (``E-09``, ``E-10``); every
norm default it applies it records with :meth:`~epc.model.result.EpcResult.assume`.
"""

from __future__ import annotations

from typing import ClassVar, Protocol

from epc.model.building import BuildingDescription
from epc.model.result import ClimateBasis, EpcResult


class EpcEngine(Protocol):
    """The protocol every engine satisfies; engines need not inherit from it.

    Attributes:
        country: ISO 3166-1 alpha-2 code the engine is registered under.
        edition: The norm edition it implements, stated in every result (R11).
        climate_basis: ``reference+site`` when the method has a site climate besides the reference
            climate, ``national`` for one national climate.
    """

    country: ClassVar[str]
    edition: ClassVar[str]
    climate_basis: ClassVar[ClimateBasis]

    def load_data(self) -> None:
        """Loads and checks the engine's data tables; idempotent, called once per process.

        Raises:
            EpcError: ``E-11`` for a missing table file, ``E-12`` for an incomplete table.
        """

    def compute(self, building: BuildingDescription) -> EpcResult:
        """Computes the result for one building; pure.

        Args:
            building: The building as HiSim computed with it.

        Returns:
            The result with the common core, the engine's own keys and its assumptions.

        Raises:
            EpcError: ``E-09``, ``E-10`` for the site fields the method reads, or an engine's own
                refusal of design §8.
        """
