""" Contains classes and methods for units. """
from __future__ import annotations

import dataclasses
from typing import Any, ClassVar, TypeVar, overload, Generic
from hisim.utils import InstanceCounter

T = TypeVar("T", bound=type)


class UnitType(type):
    """A metaclass that makes types."""

    _types_registered: ClassVar[dict[str, UnitType]] = {}

    @overload
    def __new__(cls, o: object, /) -> UnitType:
        pass

    @overload
    def __new__(
        cls: type[T],
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        /,
        **kwargs: Any,
    ) -> T:
        pass

    def __new__(
        cls,
        name: Any,
        bases: Any = None,
        namespace: Any = None,
        /,
        **kwargs: Any,
    ) -> type:
        """Initialize class."""
        if bases is None and namespace is None:
            return cls._types_registered[name]
        symbol = kwargs.pop("symbol", None)
        dim = super().__new__(cls, name, bases, namespace, **kwargs)
        if symbol is not None:
            cls._types_registered[symbol] = dim
        return dim


class AbstractUnit(metaclass=UnitType):  # abstract base (no symbol)
    """Abstract unit class."""

    pass


V = TypeVar("V", int, float)
U = TypeVar("U", bound=AbstractUnit)


@dataclasses.dataclass
class Quantity(InstanceCounter, Generic[V, U]):
    """Generic container for values and `UnitType` instances."""

    value: V
    unit: type[U]

    def _is_valid_operand(self, other):
        """Docstring missing."""
        return other.unit == self.unit

    def __add__(self, other: Quantity[V, U]) -> Quantity[V, U]:
        """Docstring missing."""
        if not self._is_valid_operand(other):
            return NotImplemented
        return Quantity(self.value + other.value, self.unit)

    def __radd__(self, other: Quantity[V, U]) -> Quantity[V, U]:
        """Docstring missing."""
        if not self._is_valid_operand(other):
            return NotImplemented
        return Quantity(self.value + other.value, self.unit)

    def __sub__(self, other: Quantity[V, U]) -> Quantity[V, U]:
        """Docstring missing."""
        if not self._is_valid_operand(other):
            return NotImplemented
        return Quantity(self.value - other.value, self.unit)

    def __rsub__(self, other: Quantity[V, U]) -> Quantity[V, U]:
        """Docstring missing."""
        if not self._is_valid_operand(other):
            return NotImplemented
        return Quantity(self.value - other.value, self.unit)

    def __eq__(self, other):
        """Docstring missing."""
        if not self._is_valid_operand(other):
            return NotImplemented
        return self.value == other.value

    def __lt__(self, other):
        """Docstring missing."""
        if not self._is_valid_operand(other):
            return NotImplemented
        return self.value < other.value

    def __le__(self, other):
        """Docstring missing."""
        if not self._is_valid_operand(other):
            return NotImplemented
        return self.value <= other.value

    def __gt__(self, other):
        """Docstring missing."""
        if not self._is_valid_operand(other):
            return NotImplemented
        return self.value > other.value

    def __ge__(self, other):
        """Docstring missing."""
        if not self._is_valid_operand(other):
            return NotImplemented
        return self.value >= other.value


class Unitless(AbstractUnit, symbol="-"):
    """Docstring missing."""

    pass


class Percent(AbstractUnit, symbol="%"):
    """Docstring missing."""

    pass


class Watt(AbstractUnit, symbol="W"):
    """Docstring missing."""

    pass


class Kilowatt(AbstractUnit, symbol="kW"):
    """Docstring missing."""

    pass


class KilowattHourPerTimestep(AbstractUnit, symbol="kWh per timestep"):
    """Docstring missing."""

    pass


class WattPerSquareMeter(AbstractUnit, symbol="W per square meter"):
    """Docstring missing."""

    pass


class WattHourPerSquareMeter(AbstractUnit, symbol="Wh per square meter"):
    """Docstring missing."""

    pass


class MeterPerSecond(AbstractUnit, symbol="m/s"):
    """Docstring missing."""

    pass


class WattHour(AbstractUnit, symbol="Wh"):
    """Docstring missing."""

    pass


class KilowattHour(AbstractUnit, symbol="kWh"):
    """Docstring missing."""

    pass


class Liter(AbstractUnit, symbol="L"):
    """Docstring missing."""

    pass


class CubicMeters(AbstractUnit, symbol="m^3"):
    """Docstring missing."""

    pass


class LiterPerTimestep(AbstractUnit, symbol="Liter per timestep"):
    """Docstring missing."""

    pass


class CubicMetersPerSecond(AbstractUnit, symbol="Cubic meters per second"):
    """Docstring missing."""

    pass


class Kilogram(AbstractUnit, symbol="kg"):
    """Docstring missing."""

    pass


class KilogramPerSecond(AbstractUnit, symbol="kg/s"):
    """Docstring missing."""

    pass


class Celsius(AbstractUnit, symbol="°C"):
    """Docstring missing."""

    pass


class Kelvin(AbstractUnit, symbol="K"):
    """Docstring missing."""

    pass


class Degrees(AbstractUnit, symbol="Degrees"):
    """Docstring missing."""

    pass


class Seconds(AbstractUnit, symbol="s"):
    """Docstring missing."""

    pass


class Hours(AbstractUnit, symbol="h"):
    """Docstring missing."""

    pass


class Timesteps(AbstractUnit, symbol="timesteps"):
    """Docstring missing."""

    pass


class Years(AbstractUnit, symbol="years"):
    """Docstring missing."""

    pass


class EurosPerKilowattHour(AbstractUnit, symbol="Euros per kWh"):
    """Docstring missing."""

    pass


class Euro(AbstractUnit, symbol="Euro"):
    """Docstring missing."""

    pass
