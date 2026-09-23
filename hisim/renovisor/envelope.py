"""The insulation arithmetic: one element's U-value, and the sentence that explains it.

Series resistance and nothing else (§4.3 of the calculation-request specification)::

    R_added = thickness_in_mm / 1000 / material.thermal_conductivity_w_mk
    U_new   = 1 / (1 / U_existing + R_added)

``U_existing`` is always the request's own element U-value: under rule 5 the frontend derives it
from its country pack, so TABULA's U-value is never the base state and the archetype contributes
geometry, ventilation and thermal mass only. Layers stack in the order the measures added them,
so a facade that gets external insulation and then cavity fill is one wall with two layers rather
than two competing answers.

``placement`` does not enter the arithmetic in this release. It is provenance -- and the hook a
later placement-specific correction factor would attach to -- which is why it travels on the
layer and is named in the note.

The note is not decoration: it is the only place a reader can check the number. It carries the
starting U-value, every layer with its thickness and conductivity, and the result::

    1.1 W/(m2K) + external_insulation 120 mm of eps_rigid_board (lambda 0.0355 W/mK,
    +3.38 m2K/W): 1/(1/1.1 + 0.12/0.0355) = 0.229 W/(m2K)
"""

from typing import ClassVar, Iterable, Sequence, Tuple


class UValueComposer:
    """Composes one element's U-value from its existing value and every layer added to it.

    The class is stateless and its two methods are the whole physics of the envelope half of the
    translator. Keeping them in one place is what makes two layers on one wall add up instead of
    the second overwriting the first, and what lets a test check the arithmetic on known numbers
    without building a house.
    """

    #: Millimetres in a metre, for turning a layer thickness into a length.
    MM_PER_M: ClassVar[float] = 1000.0

    @classmethod
    def resistance(cls, thickness_in_mm: float, conductivity_in_watt_per_meter_per_kelvin: float) -> float:
        """Return the thermal resistance of one layer in m²·K/W.

        Args:
            thickness_in_mm: The layer's thickness.
            conductivity_in_watt_per_meter_per_kelvin: Lambda of its material.

        Returns:
            ``d / lambda`` with ``d`` converted from millimetres to metres.

        Raises:
            ValueError: When the conductivity is not positive or the thickness is negative.
                Neither can reach here from a validated request; the guard is against a table.
        """
        if conductivity_in_watt_per_meter_per_kelvin <= 0:
            raise ValueError("the thermal conductivity has to be positive")
        if thickness_in_mm < 0:
            raise ValueError("a layer thickness cannot be negative")
        return (thickness_in_mm / cls.MM_PER_M) / conductivity_in_watt_per_meter_per_kelvin

    @classmethod
    def compose(cls, existing_u_value_in_watt_per_m2_per_kelvin: float, resistances: Iterable[float]) -> float:
        """Return the U-value of an element after every added layer, in W/(m²·K).

        Args:
            existing_u_value_in_watt_per_m2_per_kelvin: The element's U-value before the layers.
            resistances: The added thermal resistances in m²·K/W, one per layer, in order.

        Returns:
            The composed U-value.

        Raises:
            ValueError: When the existing U-value is not positive or a resistance is negative.
        """
        if existing_u_value_in_watt_per_m2_per_kelvin <= 0:
            raise ValueError("the existing U-value has to be positive")
        total = 1.0 / existing_u_value_in_watt_per_m2_per_kelvin
        for resistance in resistances:
            if resistance < 0:
                raise ValueError("a thermal resistance cannot be negative")
            total += resistance
        return 1.0 / total


class LayerNote:
    """Builds the sentence that shows the U-value arithmetic with its numbers.

    One class rather than a format string in the caller, because the mapping report's whole
    claim to be checkable rests on this text: a reader has to be able to redo the division.
    """

    @classmethod
    def of(
        cls,
        existing_u_value_in_watt_per_m2_per_kelvin: float,
        layers: Sequence[Tuple[str, float, str, float]],
        composed_u_value_in_watt_per_m2_per_kelvin: float,
    ) -> str:
        """Return the note for one composed element.

        Args:
            existing_u_value_in_watt_per_m2_per_kelvin: Where the element started.
            layers: One ``(measure id, thickness in mm, material id, conductivity)`` per layer,
                in the order they were applied.
            composed_u_value_in_watt_per_m2_per_kelvin: Where it ended up.

        Returns:
            The sentence, with the whole division written out.
        """
        described = "; ".join(
            f"{measure_id} {thickness:g} mm of {material} (lambda {conductivity:g} W/mK)"
            for measure_id, thickness, material, conductivity in layers
        )
        arithmetic = " + ".join(
            f"{thickness / UValueComposer.MM_PER_M:g}/{conductivity:g}"
            for _, thickness, _, conductivity in layers
        )
        return (
            f"{existing_u_value_in_watt_per_m2_per_kelvin:g} W/(m2K) + {described}: "
            f"1/(1/{existing_u_value_in_watt_per_m2_per_kelvin:g} + {arithmetic}) = "
            f"{composed_u_value_in_watt_per_m2_per_kelvin:.4g} W/(m2K)"
        )
