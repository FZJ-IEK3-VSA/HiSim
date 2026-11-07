"""The registry of the setup rules: it collects every rule of the package, and runs the rules of a stage.

``SetupRuleRegistry.collect()`` finds every rule class in the modules of ``hisim/setup_rules/`` and checks the
rules against each other: no two share a code or a name, and every required code is a rule of the same or an
earlier stage, with no cycle. ``run(stage, subject, kind)`` runs every rule of the stage that reads the subject and
that the kind does not waive, and raises one ``SetupRuleViolation`` with every fault of the enforced rules; the
faults of the other rules are logged. The registry is built on demand and kept by its caller, never stored in a
module.
"""

from __future__ import annotations

import heapq
import importlib
import pkgutil
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple, Type

import hisim.setup_rules
from hisim import log
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.rule import (
    Fault,
    RuleStage,
    RuleSubject,
    SetupRule,
    SetupRuleViolation,
    Skip,
    UndeclaredSetupKindError,
)

#: A rule class of any subject; the registry holds the rules of every subject together.
RuleClass = Type[SetupRule]


@dataclass(frozen=True)
class StageFindings:
    """What the rules of one stage found in one subject: the faults that refuse, the faults that are only logged, and the skips.

    ``subject`` is the class name of the subject the rules read, which tells the two subjects of the ``BUILD``
    stage apart. ``faults`` are the faults of the enforced rules and ``logged`` those of the others, each ordered by code, then
    by component and port; ``skipped`` are the rules that were not run because a rule they require found faults or
    was itself skipped, in code order.
    """

    stage: RuleStage
    subject: str
    kind: SetupKind
    faults: Tuple[Fault, ...]
    logged: Tuple[Fault, ...]
    skipped: Tuple[Skip, ...]

    def violation(self) -> SetupRuleViolation:
        """Return the violation that lists the refusing faults and the skips of the stage."""
        return SetupRuleViolation(self.stage, self.kind, self.faults, self.skipped)

    def to_dict(self) -> Dict[str, object]:
        """Return the findings as plain data: the violation's fields, the subject, and the faults that are only logged under ``logged``."""
        return {
            "stage": self.stage.value,
            "subject": self.subject,
            "kind": self.kind.value,
            "faults": [fault.to_dict() for fault in self.faults],
            "logged": [fault.to_dict() for fault in self.logged],
            "skipped": [skip.to_dict() for skip in self.skipped],
        }


class SetupRuleRegistry:
    """Every setup rule of one calculation, in code order, and the one place that runs them.

    A hook builds the registry with :meth:`collect` and calls :meth:`run` once for its stage. A test or a tool may
    build one from chosen rule classes instead; the same checks apply.
    """

    def __init__(self, rules: Sequence[RuleClass]) -> None:
        """Hold the rules in code order, after checking them against each other.

        Args:
            rules: The rule classes.

        Raises:
            TypeError: If two rules share a code or a name, or a requirement names no rule, a rule of a later stage,
                or closes a cycle.
        """
        self.rules: Tuple[RuleClass, ...] = tuple(sorted(rules, key=lambda rule: rule.CODE))
        problems = self.registry_problems(self.rules)
        if problems:
            raise TypeError("the setup rules do not fit together: " + "; ".join(problems))

    @classmethod
    def collect(cls) -> SetupRuleRegistry:
        """Return the registry of every rule the modules of ``hisim/setup_rules/`` define."""
        return cls(cls.discover())

    @staticmethod
    def discover() -> Tuple[RuleClass, ...]:
        """Return every rule class defined in a module of the rules package, in code order.

        Every module of the package is imported, which is what makes a new rule module take part without a list to
        extend. A class counts when it is a ``SetupRule`` subclass defined in that module and declares its own
        ``CODE``; a shared base of a family's rules declares none.
        """
        found: List[RuleClass] = []
        for module_info in pkgutil.iter_modules(hisim.setup_rules.__path__):
            # Discovery by module name is the registry's purpose: a rule module joins by existing.
            module = importlib.import_module(f"{hisim.setup_rules.__name__}.{module_info.name}")
            found.extend(
                value
                for value in vars(module).values()
                if isinstance(value, type)
                and issubclass(value, SetupRule)
                and value.__module__ == module.__name__
                and "CODE" in value.__dict__
            )
        return tuple(sorted(found, key=lambda rule: rule.CODE))

    @staticmethod
    def registry_problems(rules: Sequence[RuleClass]) -> List[str]:
        """Return every problem of a set of rules taken together, one sentence each.

        A code or a name used twice, a required code that is no rule of the set, a required rule of a later stage,
        and a cycle of requirements are problems. For example, a ``BUILD`` rule ``EP-06`` that requires an
        ``AFTER_RUN`` rule could never see that rule's result.

        Args:
            rules: The rule classes.

        Returns:
            The problems; empty for rules that fit together.
        """
        problems: List[str] = []
        codes: Dict[str, str] = {}
        names: Dict[str, str] = {}
        for rule in rules:
            if rule.CODE in codes:
                problems.append(f"{codes[rule.CODE]} and {rule.__qualname__} share the CODE {rule.CODE}")
            if rule.NAME in names:
                problems.append(f"{names[rule.NAME]} and {rule.__qualname__} share the NAME {rule.NAME}")
            codes[rule.CODE] = names[rule.NAME] = rule.__qualname__
        stage_by_code = {rule.CODE: rule.STAGE for rule in rules}
        for rule in rules:
            for required in sorted(rule.REQUIRES):
                if required not in stage_by_code:
                    problems.append(f"{rule.CODE} requires {required}, which is no rule")
                elif stage_by_code[required].position > rule.STAGE.position:
                    problems.append(
                        f"{rule.CODE} ({rule.STAGE.value}) requires {required} of the later stage {stage_by_code[required].value}"
                    )
        cycle = SetupRuleRegistry.requirement_cycle({rule.CODE: rule.REQUIRES for rule in rules})
        if cycle:
            problems.append(f"the requirements form a cycle: {' -> '.join(cycle)}")
        return problems

    @staticmethod
    def requirement_cycle(requires_by_code: Mapping[str, FrozenSet[str]]) -> Tuple[str, ...]:
        """Return one cycle of requirements as the codes along it, the first repeated at the end, or ``()`` without one.

        Example: ``{"EP-01": {"EP-02"}, "EP-02": {"EP-01"}}`` gives ``("EP-01", "EP-02", "EP-01")``. A required
        code that is not a key is a leaf. The codes are visited in sorted order, so the cycle reported is stable.

        Args:
            requires_by_code: The codes each rule requires, by the rule's code.

        Returns:
            The codes of one cycle, or an empty tuple.
        """
        finished: Set[str] = set()

        def visit(code: str, path: Tuple[str, ...]) -> Tuple[str, ...]:
            """Walk the requirements from one code; return the first cycle found on the path."""
            if code in path:
                return path[path.index(code):] + (code,)
            if code in finished or code not in requires_by_code:
                return ()
            for required in sorted(requires_by_code[code]):
                cycle = visit(required, path + (code,))
                if cycle:
                    return cycle
            finished.add(code)
            return ()

        for code in sorted(requires_by_code):
            cycle = visit(code, ())
            if cycle:
                return cycle
        return ()

    @staticmethod
    def execution_order(rules: Sequence[RuleClass]) -> Tuple[RuleClass, ...]:
        """Return the rules in the order to run them: code order, except that a rule runs after the rules it requires.

        Example: ``EF-31`` requiring ``EP-05`` runs after it, although its code sorts first; ``EP-01`` and ``EP-02``
        with no requirement between them run in code order. A required code that is not among the rules (another
        stage's rule, or one the kind waives) does not hold a rule back.

        Args:
            rules: The rules of one stage, free of cycles.

        Returns:
            The same rules, in the order to run them.
        """
        by_code = {rule.CODE: rule for rule in rules}
        waiting = {rule.CODE: {code for code in rule.REQUIRES if code in by_code} for rule in rules}
        ready = [code for code, required in waiting.items() if not required]
        heapq.heapify(ready)
        ordered: List[RuleClass] = []
        while ready:
            code = heapq.heappop(ready)
            ordered.append(by_code[code])
            for other, required in waiting.items():
                if code in required:
                    required.discard(code)
                    if not required:
                        heapq.heappush(ready, other)
        return tuple(ordered)

    def rule(self, code_or_name: str) -> Optional[RuleClass]:
        """Return the rule with this code (``EP-06``) or this name (``room-heat-needs-receiver``), or ``None``."""
        return next((rule for rule in self.rules if code_or_name in (rule.CODE, rule.NAME)), None)

    def rules_for(self, stage: RuleStage, kind: SetupKind) -> Tuple[RuleClass, ...]:
        """Return the rules of a stage that the kind does not waive, in code order."""
        return tuple(rule for rule in self.rules if rule.STAGE == stage and kind not in rule.WAIVED_FOR)

    def findings(self, stage: RuleStage, subject: RuleSubject, kind: Optional[SetupKind]) -> StageFindings:
        """Run every rule of the stage that reads this subject and that the kind does not waive, and return what they found.

        The rules run in :meth:`execution_order`. A rule whose ``REQUIRES`` names a rule that found faults, or that
        was itself skipped, in this call is not run and is listed as skipped. A required rule that does not run in
        this call, of another stage or another subject or waived for the kind, counts as passed: had it found a
        fault that refuses, an earlier hook would have stopped the calculation.

        Args:
            stage: The stage to run.
            subject: The subject of that stage, built by the hook.
            kind: The kind the setup declared; ``None`` when it declared none.

        Returns:
            The faults, the logged faults and the skips.

        Raises:
            UndeclaredSetupKindError: If the setup declares no kind.
            TypeError: If the subject belongs to another stage.
        """
        if kind is None:
            raise UndeclaredSetupKindError()
        if subject.STAGE != stage:
            raise TypeError(f"a {type(subject).__name__} is the subject of the stage {subject.STAGE.value}, not {stage.value}")
        applicable = [rule for rule in self.rules_for(stage, kind) if rule.subject_type() is type(subject)]
        failed: Set[str] = set()
        faults: List[Fault] = []
        logged: List[Fault] = []
        skipped: List[Skip] = []
        for rule in self.execution_order(applicable):
            blocking = tuple(sorted(code for code in rule.REQUIRES if code in failed))
            if blocking:
                skipped.append(Skip(rule.CODE, blocking))
                failed.add(rule.CODE)
                continue
            found = tuple(rule().check(subject))
            if found:
                failed.add(rule.CODE)
                (faults if rule.ENFORCED else logged).extend(found)

        def order(fault: Fault) -> Tuple[str, str, str]:
            """Sort a fault by its code, then its component and port."""
            return (fault.code, fault.component or "", fault.port or "")

        return StageFindings(
            stage=stage,
            subject=type(subject).__name__,
            kind=kind,
            faults=tuple(sorted(faults, key=order)),
            logged=tuple(sorted(logged, key=order)),
            skipped=tuple(sorted(skipped, key=lambda skip: skip.code)),
        )

    def run(self, stage: RuleStage, subject: RuleSubject, kind: Optional[SetupKind]) -> StageFindings:
        """Run the rules of a stage on a subject: log the faults of the rules that only log, and refuse the enforced ones.

        This is the one call each hook makes: the loader with the loaded document, the declarative build with the
        built document, the simulator before the first step with the built system, and the post-processing first
        thing with the run's results.

        Args:
            stage: The stage to run.
            subject: The subject of that stage.
            kind: The kind the setup declared; ``None`` when it declared none.

        Returns:
            The findings, when no enforced rule found a fault.

        Raises:
            SetupRuleViolation: Listing every fault of the enforced rules and every skip, when there is a fault.
            UndeclaredSetupKindError: If the setup declares no kind.
        """
        findings = self.findings(stage, subject, kind)
        for fault in findings.logged:
            log.warning(fault.log_line())
        if findings.faults:
            raise findings.violation()
        for skip in findings.skipped:
            log.warning(skip.line())
        return findings
