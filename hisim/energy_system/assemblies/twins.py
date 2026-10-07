"""The composed twins: which composed file reproduces which recorded twin, under which rename (§13 steps 4 and 5).

A *composed twin* is a committed ``energy_systems/<stem>.composed.energy_system.yaml`` — the site of a Python setup's
recorded twin plus imports of ``energy_systems/assemblies/`` — that builds the same system as the twin
``energy_systems/<stem>.energy_system.yaml``. Its members carry structured addresses (``heating-HeatPump``) where the
twin writes plain names (``MoreAdvancedHeatPumpHPLib``); :attr:`ComposedTwin.rename` maps every expanded member address
to the twin's name, and the site entries keep the twin's names. :data:`COMPOSED_TWINS` is the one table of them, read
by the twin gates (``tests/assemblies/``) and by the golden gate's ``composed`` mode (``scripts/golden_check.py``),
which runs a composed file for the golden horizons and compares its KPIs, renamed here, with the Python setup's
blessed golden.

The functions apply a rename map to the three things a run names after a member: a component reference (``Name`` or
``Name.Output``), a derived port, column or KPI name, which carries the member's port-name part
(:meth:`~hisim.config.names.NameSyntax.port_name_part`, ``heating_HeatPump``), and a KPI address. A KPI address is
renamed field by field (:func:`rename_address`), never by splitting its key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from hisim.config.names import NameSyntax
from hisim.postprocessing.kpi_computation.kpi_address import KpiAddress
from hisim.postprocessing.kpi_computation.kpi_structure import KpiSource


class UnmappedMemberError(ValueError):
    """A KPI reported for an assembly member the rename map gives no twin name."""


@dataclass(frozen=True)
class ComposedTwin:
    """One recorded twin and the composed file that reproduces it.

    Attributes:
        stem: The Python setup's stem (``system_setups/<stem>.py``); the twin is ``<stem>.energy_system.yaml``
            and the composed file ``<stem>.composed.energy_system.yaml``, both in ``energy_systems/``.
        rename: Every expanded member address of the composed file to the twin's name.
    """

    stem: str
    rename: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Freeze the rename map, so the table cannot be changed through an entry."""
        object.__setattr__(self, "rename", MappingProxyType(dict(self.rename)))

    @property
    def twin(self) -> str:
        """The recorded twin's file name in ``energy_systems/``."""
        return f"{self.stem}.energy_system.yaml"

    @property
    def composed(self) -> str:
        """The composed file's name in ``energy_systems/``."""
        return f"{self.stem}.composed.energy_system.yaml"


#: Every composed twin by its setup's stem.
COMPOSED_TWINS: Mapping[str, ComposedTwin] = MappingProxyType(
    {
        twin.stem: twin
        for twin in (
            ComposedTwin(
                "household_heatpump_building_sizer",
                {
                    "heating-ControllerDHW": "HeatPumpControllerDHW",
                    "heating-ControllerSH": "MoreAdvancedHeatPumpHPLibControllerSH",
                    "heating-HeatPump": "MoreAdvancedHeatPumpHPLib",
                    "heating-Buffer": "SimpleHotWaterStorage",
                    "dhw-DHWStorage": "DHWStorage",
                    "pv-pv_system-PVSystem": "PVSystem",
                    "battery-battery-Battery": "Battery",
                    "control-EMS": "L2EMSElectricityController",
                    "grid-ElectricityMeter": "ElectricityMeter",
                },
            ),
            ComposedTwin(
                "household_gas_building_sizer",
                {
                    "heating-Boiler": "CondensingGasBoiler",
                    "heating-Controller": "ModulatingBoilerController",
                    "heating-Buffer": "SimpleHotWaterStorage",
                    "dhw-DHWStorage": "DHWStorage",
                    "gas-GasMeter": "GasMeter",
                    "pv-pv_system-PVSystem": "PVSystem",
                    "battery-battery-Battery": "Battery",
                    "control-EMS": "L2EMSElectricityController",
                    "grid-ElectricityMeter": "ElectricityMeter",
                },
            ),
        )
    }
)


def rename_reference(text: str, mapping: Mapping[str, str]) -> str:
    """A component reference (``Name`` or ``Name.Output``) with its component renamed to the twin's name."""
    component, dot, rest = text.partition(".")
    return mapping.get(component, component) + dot + rest


def rename_port_parts(text: str, mapping: Mapping[str, str]) -> str:
    """A derived port, column or KPI name with every member's port-name part renamed (``heating_HeatPump``).

    The longest part goes first, so a member whose port-name part begins another's never renames inside it.
    """
    for address in sorted(mapping, key=lambda name: (-len(name), name)):
        text = text.replace(NameSyntax.port_name_part(address), mapping[address])
    return text


def rename_column(column: str, mapping: Mapping[str, str]) -> str:
    """A result column, ``<component> - <output> [<unit>]``, with the component and its port-name parts renamed."""
    component, _, output = column.partition(" - ")
    return f"{rename_reference(component, mapping)} - {rename_port_parts(output, mapping)}"


def rename_address(address: KpiAddress, mapping: Mapping[str, str]) -> KpiAddress:
    """A composed run's KPI address as the twin's run addresses the same KPI.

    The name has its port-name parts renamed (``Priority for ElectricalInputPowerSHFromheating_HeatPump``). A source
    the map names becomes the twin's site component of that name: no import, no path, no assembly, its member and
    runtime name the twin's name. A site source is kept as it is.

    Raises:
        UnmappedMemberError: If the source is an assembly member (it has an address path) the map does not name; its
            KPI would otherwise be compared under the member's address and only show up as missing and new.
    """
    source = address.source
    if source is not None and source.name in mapping:
        source = KpiSource(name=mapping[source.name], member=mapping[source.name])
    elif source is not None and source.path:
        raise UnmappedMemberError(
            f"The KPI '{address.dotted}' is reported for the assembly member '{source.name}', which the rename map "
            f"gives no twin name; the map names {sorted(mapping)}."
        )
    return KpiAddress(
        building=address.building, tag=address.tag, name=rename_port_parts(address.name, mapping), source=source
    )
