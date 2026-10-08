"""Cost perspectives: named combinations of five independent dimensions (cost_spec.md §4).

A perspective names one question ("what does the tenant pay, with subsidies?") by fixing the installation context, the
actor scope, the subsidy mode, the financing and the accounting. The engine evaluates a bundle of them against the same
simulation and timeline, so the figures in one report are consistent. Price and rate assumptions are not a dimension;
they are `EconomicParameters` (§4.6). Acting on the dimensions is the evaluator's job; this module only describes
perspectives and loads the shipped bundle.
"""

from __future__ import annotations

import enum
import json
import os
from dataclasses import dataclass, field
from typing import ClassVar, List, Optional, Tuple

from hisim.economics.financing import FinancingPlan
from hisim.economics.timeline import Actor


class InstallationContext(str, enum.Enum):
    """Which investments the perspective charges (§4.1, §4.2).

    GREENFIELD buys everything new at year 0. BROWNFIELD charges only the measures against a register of installed
    assets: kept assets cost nothing today and are replaced at `service_life - age`; replaced ones add the old device's
    removal cost and may earn the anyway credit (the avoided cost of a replacement that was due anyway). STATUS_QUO is
    the do-nothing reference, the existing system kept and replaced like-for-like. OPERATING_ONLY drops every
    investment category and charges a replacement reserve instead (Instandhaltungsrücklage, §4.2).
    """

    GREENFIELD = "GREENFIELD"
    BROWNFIELD = "BROWNFIELD"
    STATUS_QUO = "STATUS_QUO"
    OPERATING_ONLY = "OPERATING_ONLY"


class SubsidyModeKind(str, enum.Enum):
    """Kinds of subsidy filtering (§5.5).

    NONE and FULL give the gross and net views of one evaluation. ONLY and EXCLUDE carry a scheme-id list, for asking
    what one programme is worth.
    """

    NONE = "NONE"
    FULL = "FULL"
    ONLY = "ONLY"
    EXCLUDE = "EXCLUDE"


@dataclass(frozen=True)
class SubsidyMode:
    """Which subsidy schemes a perspective admits: NONE, FULL, ONLY(scheme_ids) or EXCLUDE(scheme_ids).

    A filter on admissibility only: whether a scheme legally applies is the catalog's condition tree (§5.4), and how
    schemes combine is the cumulation solver's (§5.5). Build one with the named constructors; `admits` is the predicate
    the subsidy engine consults.
    """

    kind: SubsidyModeKind = SubsidyModeKind.FULL
    scheme_ids: Tuple[str, ...] = ()

    @classmethod
    def none(cls) -> "SubsidyMode":
        """No subsidies: the gross view, where nothing is admitted regardless of eligibility."""
        return cls(SubsidyModeKind.NONE)

    @classmethod
    def full(cls) -> "SubsidyMode":
        """All eligible subsidies — the default, and the net view of the shipped bundle."""
        return cls(SubsidyModeKind.FULL)

    @classmethod
    def only(cls, scheme_ids: Tuple[str, ...]) -> "SubsidyMode":
        """Only the named schemes; everything else is suppressed even if it would qualify."""
        return cls(SubsidyModeKind.ONLY, scheme_ids)

    @classmethod
    def exclude(cls, scheme_ids: Tuple[str, ...]) -> "SubsidyMode":
        """All eligible schemes except the named ones — "what if this programme disappeared"."""
        return cls(SubsidyModeKind.EXCLUDE, scheme_ids)

    def admits(self, scheme_id: str) -> bool:
        """Return whether a scheme may contribute under this mode.

        An admitted scheme still has to meet its eligibility conditions and survive the cumulation solver.
        """
        if self.kind == SubsidyModeKind.NONE:
            return False
        if self.kind == SubsidyModeKind.ONLY:
            return scheme_id in self.scheme_ids
        if self.kind == SubsidyModeKind.EXCLUDE:
            return scheme_id not in self.scheme_ids
        return True


class Accounting(str, enum.Enum):
    """Financial or macroeconomic accounting (EU 244/2012, §4.5).

    FINANCIAL is the household's bill: prices as paid, VAT and energy taxes included, subsidies per the subsidy mode;
    the default. MACROECONOMIC is the EPBD cost-optimal societal view: transfers removed (no subsidies, prices net of
    taxes and levies) and a CO2 damage cost added. A macroeconomic result is a research figure, not a bill anyone pays.
    """

    FINANCIAL = "FINANCIAL"
    MACROECONOMIC = "MACROECONOMIC"


class ActorScope(str, enum.Enum):
    """Whose cash flows the perspective reports (§6).

    SYSTEM is the total before allocation; the others are the self-using owner and the two sides of a tenancy.
    Allocation only re-tags and splits entries, so the scopes sum back to SYSTEM in every band slot (§6.5).
    `timeline.Actor` is the payer tag on an entry; `to_actor` maps between the two.
    """

    SYSTEM = "SYSTEM"
    OWNER_OCCUPIER = "OWNER_OCCUPIER"
    LANDLORD = "LANDLORD"
    TENANT = "TENANT"

    def to_actor(self) -> Actor:
        """Return the matching timeline payer `Actor`.

        `CashFlowTimeline.scoped_to` treats SYSTEM as "every entry", not "entries tagged SYSTEM".
        """
        return {
            ActorScope.SYSTEM: Actor.SYSTEM,
            ActorScope.OWNER_OCCUPIER: Actor.OWNER_OCCUPIER,
            ActorScope.LANDLORD: Actor.LANDLORD,
            ActorScope.TENANT: Actor.TENANT,
        }[self]


@dataclass
class Perspective:
    """One perspective: an id plus the five dimensions (§4).

    The `id` is the name results are published under (`LifecycleCostResult.perspective_id`, KPI namespaces, `explain`
    paths such as `brownfield_net/equivalent_annual_cost_in_euro`), so it is part of the output contract. The shipped
    bundle is `perspectives_default.json`; a RenoVisor request may define more.
    """

    #: Default location of the shipped default perspective bundle (§7.1).
    DEFAULT_BUNDLE_PATH: ClassVar[str] = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cost_database", "perspectives_default.json"
    )

    id: str
    installation_context: InstallationContext
    actor_scope: ActorScope = ActorScope.SYSTEM
    subsidy_mode: SubsidyMode = field(default_factory=SubsidyMode.full)
    financing: Optional[FinancingPlan] = None  # None = cash purchase
    accounting: Accounting = Accounting.FINANCIAL

    @classmethod
    def from_json(cls, raw: dict) -> "Perspective":
        """Parse one entry of `perspectives_default.json` or a request block.

        `subsidies` may be a kind string (`"FULL"`) or an object with `kind` and `scheme_ids`. `financing` may be null,
        `"cash"` or `"-"` for a cash purchase, `{}` for the default loan, or an object of `FinancingPlan` fields.
        `actor` and `accounting` default to SYSTEM and FINANCIAL.

        Args:
            raw: One perspective object; `id` and `context` are mandatory.

        Returns:
            The parsed perspective.

        Raises:
            KeyError: If `id` or `context` is missing.
            ValueError: If a dimension value is not a member of its enum.
        """
        subsidy_raw = raw.get("subsidies", "FULL")
        if isinstance(subsidy_raw, dict):
            subsidy_mode = SubsidyMode(
                SubsidyModeKind(subsidy_raw["kind"]), tuple(subsidy_raw.get("scheme_ids", []))
            )
        else:
            subsidy_mode = SubsidyMode(SubsidyModeKind(subsidy_raw))
        financing = None
        if raw.get("financing") not in (None, "cash", "-"):
            financing_raw = raw["financing"]
            financing = FinancingPlan(**financing_raw) if isinstance(financing_raw, dict) else FinancingPlan()
        return cls(
            id=raw["id"],
            installation_context=InstallationContext(raw["context"]),
            actor_scope=ActorScope(raw.get("actor", "SYSTEM")),
            subsidy_mode=subsidy_mode,
            financing=financing,
            accounting=Accounting(raw.get("accounting", "FINANCIAL")),
        )


def load_default_bundle(path: Optional[str] = None) -> List[Perspective]:
    """Load the shipped perspective bundle (§7.1).

    The nine shipped perspectives are greenfield gross and net, brownfield gross and net, operating, owner_monthly,
    landlord, tenant and macroeconomic. Callers pair it with `select_applicable`.

    Args:
        path: Alternative bundle file; defaults to `Perspective.DEFAULT_BUNDLE_PATH`.

    Returns:
        The perspectives in file order, which is the order results and report sections appear in.
    """
    with open(path or Perspective.DEFAULT_BUNDLE_PATH, encoding="utf-8") as file:
        raw = json.load(file)
    return [Perspective.from_json(item) for item in raw["perspectives"]]


def select_applicable(perspectives: List[Perspective], has_register: bool) -> List[Perspective]:
    """Return the perspectives that fit whether an existing-asset register exists (§7.1).

    Without a register, BROWNFIELD and STATUS_QUO rows are dropped (nothing to compare against); with one, GREENFIELD
    rows are dropped (they would charge an existing system again). Other rows pass unchanged.

    Args:
        perspectives: Candidate perspectives, normally the default bundle.
        has_register: Whether an existing-asset register was supplied.

    Returns:
        The applicable subset, in input order.
    """
    selected = []
    for perspective in perspectives:
        needs_register = perspective.installation_context in (
            InstallationContext.BROWNFIELD,
            InstallationContext.STATUS_QUO,
        )
        if needs_register and not has_register:
            continue
        if perspective.installation_context == InstallationContext.GREENFIELD and has_register:
            continue
        selected.append(perspective)
    return selected
