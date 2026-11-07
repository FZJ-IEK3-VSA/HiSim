"""The setup rules: every check of a simulated system, one class per rule, run by one registry at four hooks.

A setup rule (``SetupRule``, ``rule.py``) is one named check on one subject at one stage, with a stable code
(``EP-06``), a kebab-case name (``room-heat-needs-receiver``), a docstring in the template "What it checks / Why /
Example of a fault / How to fix", and a pure ``check`` method that returns every fault it finds. The stages
(``RuleStage``) are ``LOAD`` (the energy-system document, before any component class is imported), ``BUILD`` (the
built, wired system, before the first step) and ``AFTER_RUN`` (the result table, in the post-processing). Each stage
has its subjects (``subjects.py``).

Every setup declares its kind (``SetupKind``, ``kinds.py``): ``demo``, ``residential``, ``commercial`` or
``plant``. A rule applies to every kind unless its own ``WAIVED_FOR`` names the kind with a reason; there is no
central table of waivers. ``hisim setup-rules waivers`` prints them all, read from the rules.

The registry (``SetupRuleRegistry``, ``registry.py``) collects every rule of the package's modules, one module per
rule family. Four hooks call it, once each: the loader on the loaded document, the declarative build on the built
document, the simulator before the first step on the built system, and the post-processing on the results. The
registry refuses a setup that declares no kind, runs every applicable rule of the stage, logs the faults of the
rules that only log, and raises one ``SetupRuleViolation`` with every fault of the enforced rules, in code order,
and every rule it skipped. ``inventory.py`` lists every check HiSim applies, those that live here and those that
stay elsewhere. The package imports no component and writes no file.
"""
