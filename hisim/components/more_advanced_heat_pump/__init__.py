"""The hplib heat pump: one machine for space heating and domestic hot water, its two L1 controllers and its model parts.

The heat pump's thermal and electrical power come from hplib (https://github.com/FZJ-IEK3-VSA/HPLib), evaluated at the
step's source and outlet temperatures. What lives where:

- ``component.py`` -- the heat pump itself: :class:`~.component.MoreAdvancedHeatPumpHPLib`, its configuration
  :class:`~.component.MoreAdvancedHeatPumpHPLibConfig`, the state it keeps between steps and the position of the
  hot-water storage in the system (:class:`~.component.PositionHotWaterStorageInSystemSetup`).
- ``controller_space_heating.py`` -- the space-heating L1 controller, its configuration, its modes and its state.
- ``controller_dhw.py`` -- the domestic-hot-water L1 controller, its configuration, its states and its state tuple.
- ``seasonal_cop.py`` -- the EN 14825 seasonal COP (:class:`~.seasonal_cop.StandardizedSeasonalCop`) and the
  calibration of the COP to a datasheet SCOP (:class:`~.seasonal_cop.ScopCalibration`).
- ``hplib_operation.py`` -- hplib's result as a typed value (:class:`~.hplib_operation.HplibResult`), its cache key and
  the frozen values one step passes between the component's functions (:class:`~.hplib_operation.HeatPumpOperation`
  and its companions).

The modules import each other in one direction only: ``hplib_operation`` <- ``seasonal_cop`` <- ``component``, and
``controller_space_heating`` <- ``controller_dhw`` <- ``component``; the one way back, the space-heating controller's
default connection to the heat pump, imports ``component`` inside the method that needs it.

This ``__init__`` re-exports nothing: every name is imported from the module that defines it, and an energy-system file
names a class by that module, for example ``hisim.components.more_advanced_heat_pump.component.MoreAdvancedHeatPumpHPLib``.
"""
