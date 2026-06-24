"""Contains classes and methods for units."""

from __future__ import annotations

from abc import ABCMeta
import dataclasses
from typing import Any, ClassVar, TypeVar, overload, Generic

from dataclass_wizard import JSONWizard
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


class MetaMeta(UnitType, ABCMeta):
    """Combination of metaclasses to prevent conflicts."""

    pass


class AbstractUnit(UnitType, metaclass=MetaMeta):  # abstract base (no symbol)
    """Abstract unit class."""

    pass


V = TypeVar("V", int, float)
U = TypeVar("U", bound=AbstractUnit)


@dataclasses.dataclass
class Quantity(InstanceCounter, Generic[V, U]):
    """Generic container for values and `UnitType` instances."""

    value: V
    unit: type[U]

    def _is_valid_operand(self, other) -> bool:
        """Check if the other Quantity has the same unit.

        Args:
            other: The other Quantity to compare.

        Returns:
            True if units match, False otherwise.
        """
        if not isinstance(other, Quantity):
            return False
        return other.unit == self.unit

    def __add__(self, other: Quantity[V, U]) -> Quantity[V, U]:
        """Add two Quantities with the same unit.

        Args:
            other: The Quantity to add.

        Returns:
            A new Quantity with the summed value and same unit.

        Raises:
            TypeError: If ``other`` is not a Quantity with the same unit. This
                method returns ``NotImplemented`` for incompatible operands and
                Python's binary-operator machinery raises ``TypeError`` when no
                fallback succeeds.
        """
        if not self._is_valid_operand(other):
            return NotImplemented
        return Quantity(self.value + other.value, self.unit)

    def __radd__(self, other: Quantity[V, U]) -> Quantity[V, U]:
        """Reverse addition — supports `other + quantity`.

        Args:
            other: The Quantity to add.

        Returns:
            A new Quantity with the summed value and same unit.

        Raises:
            TypeError: If ``other`` is not a Quantity with the same unit. This
                method returns ``NotImplemented`` for incompatible operands and
                Python's binary-operator machinery raises ``TypeError`` when no
                fallback succeeds.
        """
        if not self._is_valid_operand(other):
            return NotImplemented
        return Quantity(self.value + other.value, self.unit)

    def __sub__(self, other: Quantity[V, U]) -> Quantity[V, U]:
        """Subtract two Quantities with the same unit.

        Args:
            other: The Quantity to subtract.

        Returns:
            A new Quantity with the difference and same unit.

        Raises:
            TypeError: If ``other`` is not a Quantity with the same unit. This
                method returns ``NotImplemented`` for incompatible operands and
                Python's binary-operator machinery raises ``TypeError`` when no
                fallback succeeds.
        """
        if not self._is_valid_operand(other):
            return NotImplemented
        return Quantity(self.value - other.value, self.unit)

    def __rsub__(self, other: Quantity[V, U]) -> Quantity[V, U]:
        """Reverse subtraction — supports `other - quantity`.

        Args:
            other: The Quantity to subtract.

        Returns:
            A new Quantity with the difference and same unit.

        Raises:
            TypeError: If ``other`` is not a Quantity with the same unit. This
                method returns ``NotImplemented`` for incompatible operands and
                Python's binary-operator machinery raises ``TypeError`` when no
                fallback succeeds.
        """
        if not self._is_valid_operand(other):
            return NotImplemented
        return Quantity(self.value - other.value, self.unit)

    def __eq__(self, other: object) -> bool:
        """Check equality with another Quantity.

        Args:
            other: The object to compare.

        Returns:
            True if both Quantities have the same unit and value.
        """
        if not isinstance(other, Quantity) or other.unit != self.unit:
            return False
        return bool(self.value == other.value)

    def __lt__(self, other: Quantity[V, U]) -> bool:
        """Less-than comparison for Quantities with the same unit."""
        if not self._is_valid_operand(other):
            return NotImplemented
        return self.value < other.value

    def __le__(self, other: Quantity[V, U]) -> bool:
        """Less-than-or-equal comparison for Quantities with the same unit."""
        if not self._is_valid_operand(other):
            return NotImplemented
        return self.value <= other.value

    def __gt__(self, other: Quantity[V, U]) -> bool:
        """Greater-than comparison for Quantities with the same unit."""
        if not self._is_valid_operand(other):
            return NotImplemented
        return self.value > other.value

    def __ge__(self, other: Quantity[V, U]) -> bool:
        """Greater-than-or-equal comparison for Quantities with the same unit."""
        if not self._is_valid_operand(other):
            return NotImplemented
        return self.value >= other.value


@dataclasses.dataclass
class Unitless(AbstractUnit, JSONWizard):
    """Unitless unit."""

    symbol: str = "-"


@dataclasses.dataclass
class Percent(AbstractUnit, JSONWizard):
    """Percent unit."""

    symbol: str = "%"


@dataclasses.dataclass
class Watt(AbstractUnit, JSONWizard):
    """Watt unit."""

    symbol: str = "W"


@dataclasses.dataclass
class Kilowatt(AbstractUnit, JSONWizard):
    """Kilowatt unit."""

    symbol: str = "kW"


@dataclasses.dataclass
class KilowattHourPerTimestep(AbstractUnit, JSONWizard):
    """Kilowatt Hour Per Timestep unit."""

    symbol: str = "kWh per timestep"


@dataclasses.dataclass
class WattPerSquareMeter(AbstractUnit, JSONWizard):
    """Watt Per Square Meter unit."""

    symbol: str = "W per square meter"


@dataclasses.dataclass
class WattPerMeter(AbstractUnit, JSONWizard):
    """Watt Per Meter unit."""

    symbol: str = "W per meter"


@dataclasses.dataclass
class WattHourPerSquareMeter(AbstractUnit, JSONWizard):
    """Watt Hour Per Square Meter unit."""

    symbol: str = "Wh per square meter"


@dataclasses.dataclass
class MeterPerSecond(AbstractUnit, JSONWizard):
    """Meter Per Second unit."""

    symbol: str = "m/s"


@dataclasses.dataclass
class WattHour(AbstractUnit, JSONWizard):
    """Watt Hour unit."""

    symbol: str = "Wh"


@dataclasses.dataclass
class KilowattHour(AbstractUnit, JSONWizard):
    """Kilowatt Hour unit."""

    symbol: str = "kWh"


@dataclasses.dataclass
class Liter(AbstractUnit, JSONWizard):
    """Liter unit."""

    symbol: str = "L"


@dataclasses.dataclass
class CubicMeters(AbstractUnit, JSONWizard):
    """Cubic Meters unit."""

    symbol: str = "m^3"


@dataclasses.dataclass
class LiterPerTimestep(AbstractUnit, JSONWizard):
    """Liter Per Timestep unit."""

    symbol: str = "Liter per timestep"


@dataclasses.dataclass
class CubicMetersPerSecond(AbstractUnit, JSONWizard):
    """Cubic Meters Per Second unit."""

    symbol: str = "Cubic meters per second"


@dataclasses.dataclass
class Kilogram(AbstractUnit, JSONWizard):
    """Kilogram unit."""

    symbol: str = "kg"


@dataclasses.dataclass
class KilogramPerSecond(AbstractUnit, JSONWizard):
    """Kilogram Per Second unit."""

    symbol: str = "kg/s"


@dataclasses.dataclass
class Celsius(AbstractUnit, JSONWizard):
    """Celsius unit."""

    symbol: str = "°C"


@dataclasses.dataclass
class Kelvin(AbstractUnit, JSONWizard):
    """Kelvin unit."""

    symbol: str = "K"


@dataclasses.dataclass
class Degrees(AbstractUnit, JSONWizard):
    """Degrees unit."""

    symbol: str = "Degrees"


@dataclasses.dataclass
class Seconds(AbstractUnit, JSONWizard):
    """Seconds unit."""

    symbol: str = "s"


@dataclasses.dataclass
class Hours(AbstractUnit, JSONWizard):
    """Hours unit."""

    symbol: str = "h"


@dataclasses.dataclass
class Timesteps(AbstractUnit, JSONWizard):
    """Timesteps unit."""

    symbol: str = "timesteps"


@dataclasses.dataclass
class Years(AbstractUnit, JSONWizard):
    """Years unit."""

    symbol: str = "years"


@dataclasses.dataclass
class EurosPerKilowattHour(AbstractUnit, JSONWizard):
    """Euros Per Kilowatt Hour unit."""

    symbol: str = "Euros per kWh"


@dataclasses.dataclass
class Euro(AbstractUnit, JSONWizard):
    """Euro unit."""

    symbol: str = "Euro"
