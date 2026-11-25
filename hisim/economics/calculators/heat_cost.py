"""Which cash flows are heat costs: the numerator of the levelized cost of heat (LCOH).

The LCOH is the net present value of an evaluation's heat costs, annualized and divided by its annual useful heat
(rooms plus hot water). It counts heat costs only, by owner decision of 2026-11-13: the heat generators', storages' and
emitters' own cash flows, the fuels, and the heating devices' share of the electricity bill. Appliance electricity, a
photovoltaic array, a battery and the building envelope are not heat costs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, FrozenSet, Iterable, Mapping, Optional, Sequence, Tuple

from hisim.economics.carriers import EnergyCarrier, EnergyFlowRole
from hisim.economics.timeline import CashFlowTimeline, discount_factor
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType


@dataclass(frozen=True)
class HeatCostBasis:
    """What the levelized cost of heat of one evaluation needs besides its timeline: the heat and the electricity split.

    Args:
        annual_heat_demand_in_kwh: The annual useful heat the figure divides by, in kWh/a; for a staged plan the
            equivalent annual heat of its horizon. None or zero suppresses the figure.
        electricity_heat_share_by_year: For each year 0..T, the share of the electricity bill that is heat cost
            (:meth:`HeatCostAttribution.electricity_heat_share`), dimensionless in [0, 1]. None when the share of some
            year is unknown, which suppresses the figure.
    """

    annual_heat_demand_in_kwh: Optional[float]
    electricity_heat_share_by_year: Optional[Tuple[float, ...]]


class HeatCostAttribution:
    """The rule that decides which cash flows of a timeline are heat costs.

    A cash flow is a heat cost when its subject is:

    - a heat subject: a component whose asset class is a heat generator, a heat storage or an emitter
      (`HEAT_ASSET_CLASSES`); every category of its cash flows counts (investment, planning, removal, replacements,
      maintenance, residual value, subsidies, the anyway credit);
    - a heat carrier: a fuel bought only for heat (`HEAT_CARRIERS`); every charge counts, the CO2 price included;
    - the electricity carrier: each year's charges, CO2 price included, times that year's electricity heat share.

    Everything else is not a heat cost: appliances, a photovoltaic array and a battery (their savings reach heat only
    through the smaller electricity bill the share applies to), the feed-in revenue, the envelope, the meters, loan
    flows and the replacement reserve.

    Example: a heat pump that uses 3,000 kWh/a beside 2,000 kWh/a of appliance electricity bears 60 % of the
    electricity bill: `electricity_heat_share({"HeatPump": {"HEAT_PUMP_ELECTRICITY": 3000.0}, "Occupancy":
    {"HOUSEHOLD_ELECTRICITY": 2000.0}}, [ComponentType.HEAT_PUMP])` is 0.6.
    """

    #: The asset classes whose cash flows are heat costs: heat generators, heat storages and emitters.
    HEAT_ASSET_CLASSES: ClassVar[FrozenSet[ComponentType]] = frozenset(
        {
            ComponentType.HEAT_PUMP,
            ComponentType.HEAT_PUMP_BUILDING,
            ComponentType.HEAT_PUMP_DHW,
            ComponentType.GAS_HEATER,
            ComponentType.OIL_HEATER,
            ComponentType.PELLET_HEATER,
            ComponentType.WOOD_CHIP_HEATER,
            ComponentType.HYDROGEN_HEATER,
            ComponentType.DISTRICT_HEATING,
            ComponentType.ELECTRIC_HEATER,
            ComponentType.ELECTRIC_HEATING_SH,
            ComponentType.ELECTRIC_HEATING_DHW,
            ComponentType.SOLAR_THERMAL_SYSTEM,
            ComponentType.BOILER,
            ComponentType.BUFFER,
            ComponentType.THERMAL_ENERGY_STORAGE,
            ComponentType.SPACE_HEATING_STORAGE,
            ComponentType.DOMESTIC_HOT_WATER_STORAGE,
            ComponentType.HEATERS,
            ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING,
            ComponentType.HEAT_DISTRIBUTION_SYSTEM_RADIATOR,
            ComponentType.HEAT_DISTRIBUTION_SYSTEM_LOW_TEMPERATURE_RADIATOR,
        }
    )

    #: The energy-balance roles of the heating devices' electricity: a heat pump's (its compressor, pumps and backup
    #: heating rod) and a resistive heater's. Their sum is the numerator of the electricity heat share.
    HEATING_ELECTRICITY_ROLES: ClassVar[FrozenSet[EnergyFlowRole]] = frozenset(
        {EnergyFlowRole.HEAT_PUMP_ELECTRICITY, EnergyFlowRole.ELECTRIC_HEATER_ELECTRICITY}
    )

    #: The heat generators that run on electricity: their electricity must be metered for the share to be known.
    ELECTRIC_HEAT_ASSET_CLASSES: ClassVar[FrozenSet[ComponentType]] = frozenset(
        {
            ComponentType.HEAT_PUMP,
            ComponentType.HEAT_PUMP_BUILDING,
            ComponentType.HEAT_PUMP_DHW,
            ComponentType.ELECTRIC_HEATER,
            ComponentType.ELECTRIC_HEATING_SH,
            ComponentType.ELECTRIC_HEATING_DHW,
        }
    )

    #: The carriers a household buys only for heat, by timeline subject: every charge of theirs is a heat cost.
    HEAT_CARRIERS: ClassVar[FrozenSet[str]] = frozenset(
        {
            EnergyCarrier.NATURAL_GAS.value,
            EnergyCarrier.HEATING_OIL.value,
            EnergyCarrier.PELLETS.value,
            EnergyCarrier.WOOD_CHIPS.value,
            EnergyCarrier.DISTRICT_HEATING.value,
            EnergyCarrier.HYDROGEN.value,
        }
    )

    #: The carrier heat shares with the appliances, by timeline subject: its charges count by the electricity share.
    SHARED_CARRIER: ClassVar[str] = EnergyCarrier.ELECTRICITY.value

    @classmethod
    def heat_subjects(cls, asset_class_by_subject: Mapping[str, Optional[ComponentType]]) -> FrozenSet[str]:
        """Return the subjects whose asset class is a heat generator, storage or emitter.

        Args:
            asset_class_by_subject: Subject -> its asset class, None for a subject without one (a carrier, a synthetic
                subject, a measure HiSim holds no class for).

        Returns:
            The heat subjects.
        """
        return frozenset(
            subject for subject, asset_class in asset_class_by_subject.items() if asset_class in cls.HEAT_ASSET_CLASSES
        )

    @classmethod
    def electricity_heat_share(
        cls, attribution_by_subject_in_kwh: Mapping[str, Mapping[str, float]], asset_classes: Iterable[ComponentType]
    ) -> Optional[float]:
        """Return the share of the household's electricity bill that is heat cost, or None when it is unknown.

        The share is the heating devices' metered electricity over the metered consumption of heating devices and
        appliances together: `heating / (heating + household)`. The heating electricity is the sum of the roles in
        `HEATING_ELECTRICITY_ROLES` (`HEAT_PUMP_ELECTRICITY` and `ELECTRIC_HEATER_ELECTRICITY`), the household's the
        role `HOUSEHOLD_ELECTRICITY`. Both are annual or both are over the simulated period; the ratio is the same.
        For example 3,000 kWh of heat pump and 2,000 kWh of appliances give 0.6, and so do 3,000 kWh of a resistive
        heater beside the same appliances.

        Args:
            attribution_by_subject_in_kwh: Subject -> energy-balance role -> kWh, as one run records it.
            asset_classes: The asset classes of the run's cost subjects.

        Returns:
            The share in [0, 1]; 0.0 when no heating device runs on electricity; None when one does but no heating
                electricity is metered, so the heat cost is unknown.
        """
        heat_in_kwh = sum(
            by_role.get(role.value, 0.0)
            for by_role in attribution_by_subject_in_kwh.values()
            for role in cls.HEATING_ELECTRICITY_ROLES
        )
        if heat_in_kwh > 0.0:
            household_in_kwh = sum(
                by_role.get(EnergyFlowRole.HOUSEHOLD_ELECTRICITY.value, 0.0)
                for by_role in attribution_by_subject_in_kwh.values()
            )
            return heat_in_kwh / (heat_in_kwh + household_in_kwh)
        if any(asset_class in cls.ELECTRIC_HEAT_ASSET_CLASSES for asset_class in asset_classes):
            return None
        return 0.0

    @classmethod
    def heat_cost_npv_in_euro(
        cls,
        timeline: CashFlowTimeline,
        heat_subjects: FrozenSet[str],
        electricity_heat_share_by_year: Sequence[float],
        interest_rate: float,
    ) -> UncertainValue:
        """Return the net present value of the heat costs on a timeline, in euro.

        Each entry is weighted (1 for a heat subject or a heat carrier, the year's share for the electricity carrier, 0
        otherwise) and discounted from its year to year 0, slot by slot, as `CashFlowTimeline.npv` discounts.

        Args:
            timeline: The perspective's scoped timeline.
            heat_subjects: The subjects :meth:`heat_subjects` names.
            electricity_heat_share_by_year: The electricity heat share of each year 0..T.
            interest_rate: Nominal discount rate as a fraction.

        Returns:
            The heat costs' NPV as a band; exact zero when nothing on the timeline is a heat cost.

        Raises:
            IndexError: If an electricity entry falls in a year the shares do not cover.
        """
        total = UncertainValue.exact(0.0)
        for entry in timeline.entries:
            if entry.subject in heat_subjects or entry.subject in cls.HEAT_CARRIERS:
                weight = 1.0
            elif entry.subject == cls.SHARED_CARRIER:
                weight = electricity_heat_share_by_year[entry.year]
            else:
                continue
            total = total + entry.amount_in_euro.scale(weight * discount_factor(interest_rate, entry.year))
        return total
