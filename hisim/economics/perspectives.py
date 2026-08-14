"""Cost perspectives: named configurations of five orthogonal dimensions (cost_spec.md §4).

"What does this system cost?" has no single answer, and pretending otherwise is how cost studies end
up incomparable. A gross figure and a subsidized one, the system total and the tenant's share, the
research view and the household bill are all legitimate — they answer different questions. A
`Perspective` names one such question by fixing five independent dimensions, and the engine
evaluates a whole bundle of them against the *same* simulation and the same canonical timeline
(§3.6), so the numbers in a report are guaranteed to be mutually consistent rather than assembled
from separate runs.

The five dimensions are:

1. **installation context** (`InstallationContext`) — which investments are charged at all:
   everything new (GREENFIELD), only the measures on top of an existing system (BROWNFIELD), the
   do-nothing reference (STATUS_QUO), or running costs only (OPERATING_ONLY);
2. **actor scope** (`ActorScope`) — whose cash flows are reported, after the allocation ruleset of
   §6 has tagged every entry with a payer;
3. **subsidy mode** (`SubsidyMode`) — none, all eligible schemes, or an explicit include/exclude
   list, which is what makes a gross/net pair of one evaluation;
4. **financing** (`FinancingPlan`, `None` = cash) — a loan changes the liquidity profile
   dramatically and the NPV only via the loan-rate/discount-rate spread (§4.3, §4.4);
5. **accounting** (`Accounting`) — the household bill (FINANCIAL) or the socio-economic view
   (MACROECONOMIC: transfers removed, taxes and levies stripped, CO2 damage cost added, §4.5).

Note what is deliberately *not* a dimension: price and rate assumptions. Interest rates, escalation
paths and CO2 trajectories are `EconomicParameters` and belong to the scenario layer (§4.6);
uncertainty bands are a third, orthogonal mechanism again (§3.9). Perspectives are about the frame
of the question, not about the numbers going in.

This module owns only the description of a perspective and the loading/selection of the shipped
bundle. Acting on the dimensions — filtering categories, allocating payers, deciding which subsidy
awards apply — is the evaluator's and the calculators' job.
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

    The most consequential of the five dimensions, because it decides what the comparison is even
    about. GREENFIELD buys everything new at year 0 — the right frame for new construction and for
    "what does this system cost in absolute terms". BROWNFIELD charges only the *measures* against a
    register of what is already installed: kept assets cost nothing but get replaced at
    `service_life − age`, replaced ones add the old device's removal cost and may earn the
    anyway-cost credit. STATUS_QUO is the do-nothing reference — the existing system kept and
    replaced like-for-like — which matters because doing nothing is not free and pretending it is
    flatters inaction.

    OPERATING_ONLY is the odd one out: it drops every investment-related category (see
    `CategoryRules.INVESTMENT_CATEGORIES`) and adds a replacement reserve instead, answering "what
    does running this cost per year, honestly including wear" — the German
    Instandhaltungsrücklage logic (§4.2).
    """

    GREENFIELD = "GREENFIELD"
    BROWNFIELD = "BROWNFIELD"
    STATUS_QUO = "STATUS_QUO"
    OPERATING_ONLY = "OPERATING_ONLY"


class SubsidyModeKind(str, enum.Enum):
    """Kinds of subsidy filtering (§5.5).

    The discriminator of `SubsidyMode`. NONE and FULL are the two ends that produce the shipped
    gross/net perspective pair from one evaluation; ONLY and EXCLUDE carry an explicit scheme-id
    list and exist for the policy question "what is this particular programme worth", which is
    answered by re-evaluating with that scheme admitted or suppressed and differencing the results.
    """

    NONE = "NONE"
    FULL = "FULL"
    ONLY = "ONLY"
    EXCLUDE = "EXCLUDE"


@dataclass(frozen=True)
class SubsidyMode:
    """NONE | FULL | ONLY(scheme_ids) | EXCLUDE(scheme_ids).

    A tagged union expressing which subsidy schemes a perspective admits — a kind plus, for the two
    list-carrying kinds, the scheme ids. It is a *filter on admissibility only*: it never decides
    whether a scheme is legally applicable (that is the catalog's condition tree, §5.4) nor how
    several schemes cumulate (that is the cumulation solver, §5.5). Frozen and tuple-based so it is
    hashable and safely shared between perspectives.

    The named constructors below are the intended way to build one; `admits` is the single
    predicate the subsidy engine consults.
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
        """Whether a scheme may contribute under this mode.

        The one place the four kinds are interpreted, so no caller re-implements the filter. It
        answers admissibility only — a scheme that passes here still has to satisfy its eligibility
        conditions and survive the cumulation solver before any money is booked.
        """
        if self.kind == SubsidyModeKind.NONE:
            return False
        if self.kind == SubsidyModeKind.ONLY:
            return scheme_id in self.scheme_ids
        if self.kind == SubsidyModeKind.EXCLUDE:
            return scheme_id not in self.scheme_ids
        return True


class Accounting(str, enum.Enum):
    """Financial vs macroeconomic accounting (EU 244/2012, §4.5).

    FINANCIAL is the household's own bill: prices as paid, gross of VAT and energy taxes, subsidies
    included per the subsidy mode. It is the default and the only sensible basis for owner, landlord
    and tenant perspectives. MACROECONOMIC is the socio-economic view the EPBD cost-optimal
    methodology requires: pure transfers are removed (no subsidies, prices net of taxes and levies)
    and a CO2 damage cost is added instead, because from society's point of view emissions cost
    something whether or not anyone is billed for them.

    The distinction matters for interpretation as much as for arithmetic — a macroeconomic result is
    a research figure, not a number any household will ever pay.
    """

    FINANCIAL = "FINANCIAL"
    MACROECONOMIC = "MACROECONOMIC"


class ActorScope(str, enum.Enum):
    """Whose cash flows the perspective reports (§6).

    Selects one payer's slice of the allocated timeline: the total before allocation (SYSTEM), the
    self-using owner, or the two sides of a tenancy. The split is what makes the landlord/tenant
    dilemma visible — who pays for the retrofit and who benefits from the lower bill — and because
    allocation only re-tags and splits existing entries, the scopes sum back to SYSTEM exactly, per
    uncertainty slot (§6.5).

    It duplicates `timeline.Actor` on purpose: `Actor` is a property of an entry inside the kernel,
    `ActorScope` a dimension of a perspective in the configuration layer, and `to_actor` is the one
    bridge between them.
    """

    SYSTEM = "SYSTEM"
    OWNER_OCCUPIER = "OWNER_OCCUPIER"
    LANDLORD = "LANDLORD"
    TENANT = "TENANT"

    def to_actor(self) -> Actor:
        """Maps to the timeline payer enum.

        The single translation point between the perspective vocabulary and the timeline's payer
        tags; the resulting `Actor` is handed to `CashFlowTimeline.scoped_to`, where SYSTEM means
        "everything" rather than "entries literally tagged SYSTEM".
        """
        return {
            ActorScope.SYSTEM: Actor.SYSTEM,
            ActorScope.OWNER_OCCUPIER: Actor.OWNER_OCCUPIER,
            ActorScope.LANDLORD: Actor.LANDLORD,
            ActorScope.TENANT: Actor.TENANT,
        }[self]


@dataclass
class Perspective:
    """A named configuration of five orthogonal dimensions (§4).

    One instance is one answerable question, and its `id` is the name that question's results are
    published under: `LifecycleCostResult.perspective_id`, the keys of the `EvaluationMatrix`, the
    namespaced KPIs, and the left-hand side of an `explain` value path such as
    `brownfield_net/equivalent_annual_cost_in_euro`. Ids are therefore part of the output contract
    and should not be renamed casually.

    Perspectives are data, not code: the shipped bundle lives in `perspectives_default.json`, a
    RenoVisor request may define additional ones, and `from_json` is the only parser. Since the
    dimensions are orthogonal, the combinations the bundle does not ship are still expressible —
    a macroeconomic operating-only tenant view is a legal object, just not one anybody asked for.
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
        """Parses one entry of perspectives_default.json (or a request block).

        The only reader of the perspective schema, which is why adding a bundle row or letting a
        RenoVisor request define its own perspective needs no code change. It is deliberately
        lenient about the two dimensions with several natural spellings: `subsidies` may be a bare
        kind string (`"FULL"`) or an object with `kind` and `scheme_ids`, and `financing` may be
        `null`, `"cash"` or `"-"` for a cash purchase, `{}` for the default loan, or an object of
        `FinancingPlan` fields. `actor` and `accounting` default to SYSTEM/FINANCIAL when absent.

        Args:
            raw: One perspective object; `id` and `context` are mandatory, the rest optional.

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
    """Loads the standard perspective bundle (§7.1).

    Reads the nine shipped perspectives — greenfield gross/net, brownfield gross/net, operating,
    owner_monthly, landlord, tenant and macroeconomic — that every `COMPUTE_LIFECYCLE_COSTS` run
    evaluates. It is called by `bridge.py` at the end of a simulation and by the `evaluate`,
    `explain` and `report` CLI commands, always paired with `select_applicable`, which prunes the
    rows that do not fit the situation.

    Args:
        path: Alternative bundle file; defaults to `Perspective.DEFAULT_BUNDLE_PATH`.

    Returns:
        The perspectives in file order, which is the order results and report sections appear in.
    """
    with open(path or Perspective.DEFAULT_BUNDLE_PATH, encoding="utf-8") as file:
        raw = json.load(file)
    return [Perspective.from_json(item) for item in raw["perspectives"]]


def select_applicable(perspectives: List[Perspective], has_register: bool) -> List[Perspective]:
    """Greenfield rows are skipped when a register exists and vice versa (§7.1).

    Turns the presence of an `ExistingAssetRegister` into the perspective selection, so a caller
    declares the *situation* — is there an existing system or not — instead of hand-picking which
    perspectives make sense. Brownfield and status-quo views need a register to have anything to
    compare against; a greenfield view would double-charge a building whose system already exists.
    Every other row (operating, and the actor and accounting variants, which are brownfield-based
    in the shipped bundle) passes through unchanged.

    Both CLI entry points and `bridge.py` call it immediately after `load_default_bundle`, which is
    why an economic run with no `EconomicContext` silently produces the greenfield pair only.

    Args:
        perspectives: Candidate perspectives, normally the default bundle.
        has_register: Whether an existing-asset register was supplied for this variant.

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
