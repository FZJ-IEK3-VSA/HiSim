#!/usr/bin/env python3
"""The horizon vocabulary of the golden gates: one home for the words and the one rule.

Two independent programs have to agree on which (setup, parameter set) pairs the golden gate
covers: the stdlib-only matrix emitter that fans CI out (``golden_matrix.py``, running in the
discover job before dependencies are installed) and the runner that actually executes pairs
(``runner.py``, importing all of HiSim). The horizon names, the factory mapping and the
participation rule therefore live here, in a module both can import, instead of as two copies
that drift the day one of them is edited alone.

Deliberately standard-library only, like the matrix emitter: this module is below both consumers,
so it must import neither ``hisim`` nor either of them.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional


class HorizonVocabulary:
    """The three horizons, their ``SimulationParameters`` factories, and the participation rule.

    Everything here is validation and vocabulary: the class holds no state and is never
    instantiated. The two ``check_*`` methods are the fail-loud half — a configuration mistake
    must be a named refusal at load time, because the silent alternative is a setup vanishing
    from every gate and every matrix with nothing red anywhere.
    """

    #: Map a human-facing horizon name to the SimulationParameters factory that produces it.
    #: Selecting by factory keeps this robust to parameter-set id naming.
    HORIZON_FACTORIES: ClassVar[Dict[str, str]] = {
        "day": "one_day_only",
        "week": "one_week_only",
        "year": "full_year",
    }

    #: The reverse view: which horizon a parameter set's factory belongs to. Used to honour a
    #: setup's own ``horizons`` restriction (a setup listed only for the week gate must not
    #: enter the full-year matrix, whose cost is what the restriction exists to spare).
    FACTORY_HORIZONS: ClassVar[Dict[str, str]] = {
        factory: horizon for horizon, factory in HORIZON_FACTORIES.items()
    }

    @classmethod
    def runs_factory(cls, horizons: Optional[list], factory: str) -> bool:
        """Whether a setup with these horizons participates in parameter sets built by ``factory``.

        This is the one participation rule the emitter and the runner share. It assumes its
        inputs were validated by :meth:`check_horizons` and :meth:`check_factory`; malformed
        input belongs there, where it can be refused with a location, not here.

        Args:
            horizons: the setup's restriction, or ``None`` for no restriction.
            factory: the ``SimulationParameters`` factory name of a parameter set.

        Returns:
            bool: True when the setup carries no restriction, or the factory's horizon is
            among the ones it names.
        """
        return horizons is None or cls.FACTORY_HORIZONS.get(factory) in horizons

    @classmethod
    def check_horizons(cls, horizons: Any, where: str) -> None:
        """Refuses every malformed ``horizons`` value with a message naming the entry.

        Three mistakes would otherwise pass silently or fail nonsensically: an empty list makes
        a setup participate in nothing (it vanishes from every gate with no error anywhere), a
        bare string iterates per character (reporting unknown horizons ``['w', 'e', 'e', 'k']``),
        and a number raises a bare ``TypeError`` far from the config.

        Args:
            horizons: the raw config value; ``None`` (no restriction) is valid.
            where: how the message names the offending entry.

        Raises:
            ValueError: for a non-list value, an empty list, or an unknown horizon name.
        """
        if horizons is None:
            return
        if not isinstance(horizons, list):
            raise ValueError(
                f"{where}: 'horizons' must be a list of horizon names, not "
                f"{type(horizons).__name__} ({horizons!r})."
            )
        if not horizons:
            raise ValueError(
                f"{where}: 'horizons' is an empty list, so the setup would participate in "
                "nothing at all; drop the key to run every horizon, or name the ones it runs."
            )
        unknown = [h for h in horizons if h not in cls.HORIZON_FACTORIES]
        if unknown:
            raise ValueError(
                f"{where} names unknown horizons {unknown}; "
                f"valid horizons: {sorted(cls.HORIZON_FACTORIES)}."
            )

    @classmethod
    def check_factory(cls, factory: Any, where: str) -> None:
        """Refuses a parameter-set factory the horizon vocabulary does not know.

        An unmapped factory would resolve to no horizon, and the participation rule would then
        silently exclude every restricted setup from that parameter set while the unrestricted
        ones run it — an asymmetry nobody asked for. A new factory therefore has to enter
        :attr:`HORIZON_FACTORIES` consciously before a parameter set may use it.

        Args:
            factory: the raw factory name from the config.
            where: how the message names the offending entry.

        Raises:
            ValueError: when the factory is not a key of :attr:`FACTORY_HORIZONS`.
        """
        if factory not in cls.FACTORY_HORIZONS:
            raise ValueError(
                f"{where} uses factory {factory!r}, which maps to no horizon; "
                f"known factories: {sorted(cls.FACTORY_HORIZONS)}. Add the factory to "
                "HORIZON_FACTORIES before a parameter set may use it."
            )
