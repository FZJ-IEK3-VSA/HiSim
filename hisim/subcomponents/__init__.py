"""Subcomponents: plain classes that model part of a device's behaviour, shared by two or more components.

A subcomponent is a plain class a component builds from its configuration and calls in its own ``i_simulate``,
for example a burner's modulation band or a circulation pump's largest flow. It has no ports and no lifecycle, and
the simulator never sees it. Every subcomponent class inherits the marker :class:`hisim.subcomponents.base.Subcomponent`.
The hierarchy is assembly -> component -> subcomponent.

This package holds the subcomponents that two or more components use. A subcomponent with a single user stays in
its component's module and moves here when a second user appears. The definition and its rules are in
``system_docs/component_design_principles.md``, section C.

A module in this package imports only the standard library, numpy, ``hisim.config``, ``hisim.hydronics``,
``hisim.loadtypes``, ``hisim.units`` and other modules of this package; ``tests/test_subcomponent_import_lint.py``
checks it. So a subcomponent never depends on the component runtime, the simulator or another component.
"""
