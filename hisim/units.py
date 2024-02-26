from __future__ import annotations

import dataclasses
from typing import Any, ClassVar, TypeVar, overload, Generic
from hisim.utils import InstanceCounter

T = TypeVar("T", bound=type)


class UnitType(type):
    """A metaclass that makes types."""

    _types_registered: ClassVar[dict[str, UnitType]] = {}

    @overload
    def __new__(mcs, o: object, /) -> UnitType: ...

    @overload
    def __new__(
        mcs: type[T],
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        /,
        **kwargs: Any,
    ) -> T: ...

    def __new__(
        mcs,
        name: Any,
        bases: Any = None,
        namespace: Any = None,
        /,
        **kwargs: Any,
    ) -> type:
        if bases is None and namespace is None:
            return mcs._types_registered[name]
        symbol = kwargs.pop("symbol", None)
        dim = super().__new__(mcs, name, bases, namespace, **kwargs)
        if symbol is not None:
            mcs._types_registered[symbol] = dim
        return dim


class AbstractUnit(metaclass=UnitType):  # abstract base (no symbol)
    pass


V = TypeVar("V", int, float)
U = TypeVar("U", bound=AbstractUnit)


@dataclasses.dataclass
class Quantity(InstanceCounter, Generic[V, U]):
    """generic container for values and `UnitType` instances"""

    value: V
    unit: type[U]
    # count: InstanceCounter = InstanceCounter()

    def _is_valid_operand(self, other):
        return other.unit == self.unit

    def __add__(self, other: Quantity[V, U]) -> Quantity[V, U]:
        if not self._is_valid_operand(other):
            return NotImplemented
        return Quantity(self.value + other.value, self.unit)

    def __radd__(self, other: Quantity[V, U]) -> Quantity[V, U]:
        if not self._is_valid_operand(other):
            return NotImplemented
        return Quantity(self.value + other.value, self.unit)

    def __sub__(self, other: Quantity[V, U]) -> Quantity[V, U]:
        if not self._is_valid_operand(other):
            return NotImplemented
        return Quantity(self.value - other.value, self.unit)

    def __rsub__(self, other: Quantity[V, U]) -> Quantity[V, U]:
        if not self._is_valid_operand(other):
            return NotImplemented
        return Quantity(self.value - other.value, self.unit)

    def __eq__(self, other):
        if not self._is_valid_operand(other):
            return NotImplemented
        return self.value == other.value

    def __lt__(self, other):
        if not self._is_valid_operand(other):
            return NotImplemented
        return self.value < other.value

    def __le__(self, other):
        if not self._is_valid_operand(other):
            return NotImplemented
        return self.value <= other.value

    def __gt__(self, other):
        if not self._is_valid_operand(other):
            return NotImplemented
        return self.value > other.value

    def __ge__(self, other):
        if not self._is_valid_operand(other):
            return NotImplemented
        return self.value >= other.value


class Unitless(AbstractUnit, symbol="-"):
    pass


class Percent(AbstractUnit, symbol="%"):
    pass


class Watt(AbstractUnit, symbol="W"):
    pass


class Kilowatt(AbstractUnit, symbol="kW"):
    pass


class KilowattHourPerTimestep(AbstractUnit, symbol="kWh per timestep"):
    pass


class WattPerSquareMeter(AbstractUnit, symbol="W per square meter"):
    pass


class WattHourPerSquareMeter(AbstractUnit, symbol="Wh per square meter"):
    pass


class MeterPerSecond(AbstractUnit, symbol="m/s"):
    pass


class WattHour(AbstractUnit, symbol="Wh"):
    pass


class KilowattHour(AbstractUnit, symbol="kWh"):
    pass


class Liter(AbstractUnit, symbol="L"):
    pass


class CubicMeters(AbstractUnit, symbol="m^3"):
    pass


class LiterPerTimestep(AbstractUnit, symbol="Liter per timestep"):
    pass


class CubicMetersPerSecond(AbstractUnit, symbol="Cubic meters per second"):
    pass


class Kilogram(AbstractUnit, symbol="kg"):
    pass


class KilogramPerSecond(AbstractUnit, symbol="kg/s"):
    pass


class Celsius(AbstractUnit, symbol="°C"):
    pass


class Kelvin(AbstractUnit, symbol="K"):
    pass


class Degrees(AbstractUnit, symbol="Degrees"):
    pass


class Seconds(AbstractUnit, symbol="s"):
    pass


class Hours(AbstractUnit, symbol="h"):
    pass


class Timesteps(AbstractUnit, symbol="timesteps"):
    pass


class Years(AbstractUnit, symbol="years"):
    pass


class EurosPerKilowattHour(AbstractUnit, symbol="Euros per kWh"):
    pass


class Euro(AbstractUnit, symbol="Euro"):
    pass
