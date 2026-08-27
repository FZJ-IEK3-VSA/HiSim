"""Sizing-fact contributions: what a config class declares it computes for its siblings.

This module holds the *declaration* side of cross-component sizing: a config class that
is a source of sizing facts (the building contributing the heating load, a boiler
contributing its power band for its controller) declares that as a tuple of
:class:`FactContribution` objects on the class attribute named by
:attr:`FactContribution.CLASS_ATTRIBUTE`. The *resolution* side — reading these
declarations, validating the resulting dependency graph and computing the values at the
right moment — lives in :mod:`hisim.config.engine`.

Keeping declaration and resolution separate means a component author touching a config
class only ever reads this small module, while the engine's fixed-point machinery stays
out of sight. Like every module of the ``hisim.config`` package it imports nothing from
the rest of HiSim.
"""

# clean

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Callable, ClassVar, Mapping, Tuple

from hisim.config.context import SizingContext
from hisim.config.laws import SizingError


@dataclass(frozen=True)
class FactContribution:
    """One declared output of a config class: which facts it computes, from what.

    The ``facts`` names are **static per class**: the set never depends on
    resolution, only the values do — ``compute`` may return ``None`` for a fact whose
    feature is off, and a consumer reading such a null fact fails hard with a
    "provided as null by X" attribution. ``compute`` receives the (by then fully
    resolved) config and the context view and must return exactly the declared keys.

    Which consumer reads which provider is deliberately *not* declared here: every
    declaring instance is addressable as ``"<instance name>.<fact>"``, a bare fact binds
    when exactly one instance in the resolved set declares it, and every other case is
    decided by the consumer's explicit sources mapping.
    """

    #: Name of the class attribute under which a config class declares its
    #: contributions, e.g. ``BuildingConfig.SIZING_CONTRIBUTIONS = (FactContribution(...),)``.
    #: The engine reads the declarations through this name.
    CLASS_ATTRIBUTE: ClassVar[str] = "SIZING_CONTRIBUTIONS"

    facts: Tuple[str, ...]
    compute: Callable[[Any, SizingContext], Mapping[str, Any]]
    reads: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validates the declared fact names against the SizingContext registry.

        Every engine fact must be a ``SizingContext`` field, so laws can read it and the
        single vocabulary of facts (one Size term per context field) extends to contributions.
        """
        known = {field.name for field in dataclasses.fields(SizingContext)}
        unknown = [name for name in self.facts if name not in known]
        if unknown:
            raise SizingError(
                f"FactContribution declares unknown fact(s) {unknown}; every fact must be "
                "a SizingContext field (add the field and its Size term first)."
            )
