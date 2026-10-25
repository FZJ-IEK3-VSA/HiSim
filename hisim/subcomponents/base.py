"""The marker base class every subcomponent inherits."""


class Subcomponent:
    """Marker for a subcomponent: a plain class that models part of a device's behaviour inside a component.

    A component builds its subcomponents once from its own configuration and calls them in its ``i_simulate``. A
    subcomponent has no ports and no lifecycle, and the simulator never sees it, so it costs no convergence pass. It
    is tested alone. A class may be used by several components, but each component builds its own instances; an
    instance is never shared between two components.

    The class carries no behaviour and no state. It only names the pattern, so a reader, a review and the tests in
    ``tests/subcomponents/`` can find every subcomponent. For example::

        @dataclass(frozen=True)
        class BoilerPump(Subcomponent):
            maximal_mass_flow_in_kg_per_second: float

    Inheriting it changes nothing about a dataclass: the fields, the equality, the hash and the frozenness stay those
    of the subclass. The empty ``__slots__`` keeps that true for a dataclass with ``slots=True`` as well.
    """

    __slots__ = ()
