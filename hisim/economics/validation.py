"""Data-file CI checks (cost_spec.md §9.6).

Since prices and schemes are data, the data gets the CI treatment code used to get
implicitly. These functions are called from tests (`tests/test_economics_data_and_integration.py`)
and can be run standalone via ``python -m hisim.economics validate``.

**The design question it answers.** Moving every number out of Python into `hisim/cost_database/`
and `hisim/subsidy_catalog/` bought versioning, sourcing and country-specificity — but it also
moved a large part of the engine's correctness out of the reach of the type checker, the linter and
code review. This module is the compensation: the checks that would have been compile errors if the
numbers were still code. An unsourced datapoint, an asset class with no price in one shipped
country, a subsidy scheme conditioning on a question nobody asks, a tariff contract whose id does
not match its file name — each of these produces a perfectly loadable data file and a wrong or
crashing run, and each is caught here instead.

**Errors versus warnings** is the module's central distinction and is deliberate. An *error* means
the shipped data is internally inconsistent: it fails CI and must be fixed before merge. A
*warning* means the data may be out of date or untidy — a source retrieved more than twelve months
ago, a catalog snapshot older than a year, a registry entry or question referenced by nothing.
Those are judgement calls for a data reviewer, not gates, because time passing is not a defect.

Its place in the pipeline: this runs *beside* the engine, never inside it. It is invoked from
the data-file test module in CI (today `tests/test_economics_data_and_integration.py`) and by the
`validate` CLI subcommand after a data edit; no simulation, no evaluation and no result depends on
it. The checks it performs are
those of §9.6: schema validation (by loading through the real loaders), source completeness,
coverage matrices, tariff-contract integrity, question coverage and staleness.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Set

from hisim.economics.database import CostDatabase, SourceRegistry
from hisim.economics.subsidies import (
    SubsidyCatalog,
    SubsidyScheme,
    TaxCreditBenefit,
    question_targets,
    scheme_context_fields,
)
from hisim.economics.tariffs import TariffContract


class ValidationConstants:
    """Requirements the shipped data files are validated against.

    Only the language requirement so far. It lives here rather than inline because it is a policy
    decision (spec Q31: German and English in v1) that a future country addition will revisit — the
    §5.7 questionnaire has to be answerable by the people the subsidy applies to, so adding a
    country with another official language means extending this tuple and the question catalogs
    together.
    """

    #: Languages the question catalogs must cover (spec Q31: de + en in v1).
    REQUIRED_QUESTION_LANGUAGES = ("de", "en")


@dataclass
class ValidationReport:
    """Errors fail CI; warnings are advisory (staleness).

    The accumulating result of a validation run, and the reason the checks collect rather than
    raise: a data reviewer wants *every* problem in the file they just edited, not the first one.
    Messages are plain strings because their only consumers are a test assertion and the CLI's
    stdout — nothing branches on their content.
    """

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def merge(self, other: "ValidationReport") -> None:
        """In-place union.

        How the composite checks assemble their sub-reports (`validate_all` over the catalogs,
        `validate_cost_database` over the tariff contracts) while keeping the error/warning
        distinction of each.
        """
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)

    @property
    def ok(self) -> bool:
        """No errors. Warnings do not affect it — this is exactly the CI gate and the CLI exit."""
        return not self.errors


def validate_cost_database(
    base_path: Optional[str] = None,
    declared_asset_classes: Optional[Set] = None,
    used_carriers: Optional[Set] = None,
    reference_date: Optional[date] = None,
) -> ValidationReport:
    """Schema + source completeness + coverage matrix + staleness for the cost database.

    Runs the §9.6 checks over `hisim/cost_database/`. Schema validation is not re-implemented here:
    the database is simply *loaded* through `CostDatabase`, whose loaders already reject an
    unsourced datapoint, an inverted uncertainty band or an unknown enum — so a load failure is
    reported as a single CI error and the run stops there. On top of that come the checks that need
    a whole-corpus view: the tariff contracts (which used never to be walked at all, W2.5/B9),
    orphaned and stale source registry entries as warnings, and the two coverage matrices.

    **The coverage matrix is the counterpart of the legacy "new component must implement
    get_cost_capex" rule** (§9.6): every asset class any component declares must have a device entry
    in *every* shipped country, and every carrier any meter bills must have a price entry — so
    adding a `ComponentType` without cost data fails CI instead of failing a user's run in a country
    nobody tested. The checks are skipped when the caller passes no declared classes/carriers, which
    is what lets the CLI run without importing the component zoo.

    Ordering note: the tariff contracts are validated *before* the orphan check, so contract sources
    count as referenced and do not get reported as orphans.

    Args:
        base_path: Cost database directory; the shipped one by default.
        declared_asset_classes: `ComponentType`s to demand device entries for. Omit to skip.
        used_carriers: `EnergyCarrier`s to demand price entries for. Omit to skip.
        reference_date: "Today" for the 12-month staleness comparison; makes the check testable.

    Returns:
        The report; `ok` is False if anything structural is wrong.
    """
    report = ValidationReport()
    path = base_path or CostDatabase.DEFAULT_PATH
    try:
        database = CostDatabase(path)
    # Catching broadly is the point: every load error is a CI error.
    except Exception as err:  # pylint: disable=broad-except
        report.errors.append(f"Cost database failed to load: {err}")
        return report

    # Resolve auxiliary files' sources so their registry entries don't count as orphans.
    allocation_path = os.path.join(path, "allocation_DE_2024.json")
    if os.path.isfile(allocation_path):
        with open(allocation_path, encoding="utf-8") as file:
            allocation = json.load(file)
        try:
            database.sources.resolve(tuple(allocation.get("source_ids", ())), "allocation_DE_2024.json")
        except Exception as err:  # pylint: disable=broad-except
            report.errors.append(str(err))

    # W2.5 / §7 B9: the tariff contracts ship inside the cost database but were never validated.
    # Run before the orphan check so contract sources count as referenced.
    report.merge(validate_tariff_contracts(os.path.join(path, "tariffs"), database.sources))

    orphans = database.sources.orphaned_ids()
    if orphans:
        report.warnings.append(f"Orphaned source registry entries (referenced by no data entry): {orphans}")
    stale = database.sources.stale_ids(reference_date=reference_date)
    if stale:
        report.warnings.append(f"Sources with retrieved date older than 12 months: {stale}")

    # Coverage matrix (§9.6): every declared asset class x supported country has an entry.
    if declared_asset_classes:
        for country in database.devices:
            for asset_class in sorted(declared_asset_classes, key=lambda item: item.value):
                if not database.has_device_entry(asset_class, country):
                    report.errors.append(
                        f"Coverage matrix: no device entry for {asset_class.value!r} in {country}."
                    )
    if used_carriers:
        for country in database.energy_prices:
            for carrier in sorted(used_carriers, key=lambda item: item.value):
                if not database.has_energy_price(carrier, country):
                    report.errors.append(f"Coverage matrix: no energy price for {carrier.value!r} in {country}.")
    return report


def validate_tariff_contracts(
    base_path: Optional[str] = None, registry: Optional[SourceRegistry] = None
) -> ValidationReport:
    """Every shipped tariff contract parses, is addressable by its file name and cites sources.

    W2.5 / §7 B9: `validate_all` never walked ``cost_database/tariffs/*.json``, so a malformed
    contract shipped silently and only failed when a simulation happened to reference it.

    Checks per file: the JSON parses through the §8.2 schema (`TariffContract.from_json`, which
    also enforces mandatory source ids), the contract id matches the file name (contracts are
    looked up by file name in `load_tariff_contract`), a DYNAMIC contract's `spot_series` exists,
    and every source id resolves against the cost database's registry.

    **W2.4b: inline sources are an error in catalog files.** ``inline:<citation>`` used to be
    accepted here with a warning; a shipped data file that cites its source in prose cannot be
    reviewed, deduplicated or checked for staleness, so a tariff *file* must name registry
    entries. The convention survives only for contracts built in memory (worked examples, test
    fixtures, the provider's SYNTHETIC_TEST contract), which no registry can back — see the
    `tariffs.py` module docstring.
    """
    report = ValidationReport()
    path = base_path or TariffContract.DEFAULT_PATH
    if not os.path.isdir(path):
        report.errors.append(f"Tariff directory {path} does not exist.")
        return report
    series_path = os.path.join(os.path.dirname(path), "spot_series")
    for file_name in sorted(name for name in os.listdir(path) if name.endswith(".json")):
        contract_id = file_name[: -len(".json")]
        full_path = os.path.join(path, file_name)
        try:
            with open(full_path, encoding="utf-8") as file:
                raw = json.load(file)
            contract = TariffContract.from_json(raw)
        # Catching broadly is the point: every load error is a CI error.
        except Exception as err:  # pylint: disable=broad-except
            report.errors.append(f"Tariff contract {file_name}: failed to parse: {err}")
            continue
        if contract.id != contract_id:
            report.errors.append(
                f"Tariff contract {file_name}: declares id {contract.id!r} but is loaded by file "
                f"name, so it can never be resolved by its own id."
            )
        if contract.supply.spot_series and not os.path.isfile(
            os.path.join(series_path, f"{contract.supply.spot_series}.csv")
        ):
            report.errors.append(
                f"Tariff contract {contract.id}: references spot series "
                f"{contract.supply.spot_series!r}, which does not exist in {series_path}."
            )
        inline = [source_id for source_id in contract.source_ids if source_id.startswith("inline:")]
        registry_ids = tuple(
            source_id for source_id in contract.source_ids if not source_id.startswith("inline:")
        )
        if registry is not None and registry_ids:
            try:
                registry.resolve(registry_ids, f"tariff contract {contract.id}")
            except Exception as err:  # pylint: disable=broad-except
                report.errors.append(str(err))
        if inline:
            report.errors.append(
                f"Tariff contract {contract.id}: cites inline source(s) {inline} instead of "
                f"registry entries in {file_name} — a shipped catalog file must reference "
                "sources.json entries (§3.10, W2.4). Inline sources are only admissible for "
                "contracts built in memory."
            )
    return report


def validate_subsidy_catalog(country: str, base_path: Optional[str] = None) -> ValidationReport:
    """Schema, condition grammar, question coverage and staleness for one country catalog.

    Validates one `subsidy_catalog/<COUNTRY>.json` and its question file. As with the cost database,
    schema and benefit typing are enforced by *loading* the catalog (W2.2), so what remains here are
    the checks that need the catalog as a whole: snapshot-date staleness, scheme-id uniqueness,
    `excludes` pointing at schemes that actually exist (an exclusion naming a typo can never fire,
    which silently over-grants), tax-credit instalment shares summing to 1, and cumulation groups
    whose members agree on their combined rate cap — they must, because the solver applies the
    minimum cap declared in a group to every member.

    **Question coverage (§5.7) is the check that makes the questionnaire complete by construction.**
    Every context field a shipped scheme's conditions reference must have a question-catalog entry
    in every required language, or eligibility could depend on something the user was never asked;
    the reverse direction, a question no scheme reads, is only a warning. Which fields a scheme
    depends on comes from the single field-vocabulary registry in `subsidies.py` (W2.3), so this
    module cannot drift from the condition language it validates.

    Args:
        country: Catalog country code, i.e. the file stem (`DE`, `AT`).
        base_path: Catalog directory; the shipped one by default.

    Returns:
        The report. A catalog that fails to load yields exactly one error and no further checks.
    """
    report = ValidationReport()
    base = base_path or SubsidyCatalog.DEFAULT_PATH
    try:
        catalog = SubsidyCatalog.load(country, base)
    except Exception as err:  # pylint: disable=broad-except
        report.errors.append(f"Subsidy catalog {country} failed to load: {err}")
        return report

    # Staleness (§9.6): catalog_snapshot_date older than 12 months.
    if catalog.snapshot_date:
        try:
            snapshot = datetime.strptime(catalog.snapshot_date, "%Y-%m-%d").date()
            if (date.today() - snapshot).days > 365:
                report.warnings.append(
                    f"Subsidy catalog {country}: snapshot date {catalog.snapshot_date} is older than 12 months."
                )
        except ValueError:
            report.errors.append(f"Subsidy catalog {country}: invalid catalog_snapshot_date.")
    else:
        report.errors.append(f"Subsidy catalog {country}: catalog_snapshot_date missing.")

    # Cross-scheme coherence (W2.5). Benefit *typing* is enforced at load (W2.2) — a malformed
    # benefit makes the load above fail — so what is left here are the checks that need the whole
    # catalog: id uniqueness, `excludes` pointing at real schemes, and cumulation groups whose
    # members agree on their combined rate cap. (`cumulation_group` is a group *label*, not a
    # scheme id, so there is nothing to resolve it against; its catalog-level invariant is the
    # cap coherence below, because the solver applies the minimum cap declared in the group to
    # all of its members.)
    seen: Set[str] = set()
    for scheme in catalog.schemes:
        if scheme.id in seen:
            report.errors.append(f"Subsidy catalog {country}: duplicate scheme id {scheme.id!r}.")
        seen.add(scheme.id)
    for scheme in catalog.schemes:
        for excluded in scheme.excludes:
            if excluded not in seen:
                report.errors.append(
                    f"Subsidy catalog {country}: scheme {scheme.id!r} excludes unknown scheme id "
                    f"{excluded!r} — the exclusion can never fire."
                )
        benefit = scheme.benefit
        if isinstance(benefit, TaxCreditBenefit) and benefit.annual_shares:
            total_share = sum(benefit.annual_shares)
            if abs(total_share - 1.0) > 1e-9:
                report.errors.append(
                    f"Subsidy catalog {country}: scheme {scheme.id!r} has annual_shares summing "
                    f"to {total_share} instead of 1."
                )
    groups: Dict[str, List[SubsidyScheme]] = {}
    for scheme in catalog.schemes:
        if scheme.cumulation_group:
            groups.setdefault(scheme.cumulation_group, []).append(scheme)
    for group, members in sorted(groups.items()):
        caps = {scheme.combined_rate_cap for scheme in members}
        if len(caps) > 1:
            report.errors.append(
                f"Subsidy catalog {country}: cumulation group {group!r} declares inconsistent "
                f"combined_rate_cap values {sorted(cap for cap in caps if cap is not None)}"
                f"{' plus null' if None in caps else ''} across "
                f"{sorted(scheme.id for scheme in members)} — the solver applies the minimum cap "
                "of the group to every member, so the members must agree."
            )

    # Question coverage (§5.7, §9.6): every referenced user-answerable field has a question
    # in every required language; orphaned questions are flagged. Which fields a scheme depends
    # on, and which question a derived field is asked through, come from the one field-vocabulary
    # registry in `subsidies.py` (W2.3) — this module no longer keeps its own copy.
    asked: Set[str] = set()
    for scheme in catalog.schemes:
        for fieldname in scheme_context_fields(scheme):
            if fieldname and not fieldname.startswith("measure."):
                asked.update(question_targets(fieldname))
    for fieldname in sorted(asked):
        entry = catalog.questions.get(fieldname)
        if entry is None:
            report.errors.append(
                f"Subsidy catalog {country}: field {fieldname!r} referenced by scheme conditions has "
                "no question catalog entry (§5.7)."
            )
            continue
        for language in ValidationConstants.REQUIRED_QUESTION_LANGUAGES:
            if language not in entry.question:
                report.errors.append(
                    f"Subsidy catalog {country}: question for {fieldname!r} misses language {language!r}."
                )
    for fieldname in catalog.questions:
        if fieldname not in asked:
            report.warnings.append(
                f"Subsidy catalog {country}: orphaned question entry {fieldname!r} (referenced by no scheme)."
            )
    return report


def validate_all(cost_database_path: Optional[str] = None, subsidy_base_path: Optional[str] = None) -> ValidationReport:
    """Everything: cost database plus all shipped subsidy catalogs.

    The single entry point behind ``python -m hisim.economics validate`` and behind the data-file CI
    test: it validates the cost database and then discovers the shipped country catalogs from the
    directory listing — every `*.json` that is not a `questions_*` file and not `sources.json` — so
    adding a country adds its checks automatically, with no code change (§10.1 Phase 4).

    Note the deliberate gap: no `declared_asset_classes` or `used_carriers` are passed on, so the
    coverage matrices are *not* checked here. Those need the set of classes and carriers components
    actually declare, which the CI test supplies explicitly; a green `validate` run therefore means
    "the shipped data is internally consistent", not "every component can be priced".

    Args:
        cost_database_path: Cost database directory; the shipped one by default.
        subsidy_base_path: Subsidy catalog directory; the shipped one by default.

    Returns:
        The merged report over everything checked.
    """
    report = validate_cost_database(cost_database_path)
    base = subsidy_base_path or SubsidyCatalog.DEFAULT_PATH
    if os.path.isdir(base):
        for file_name in sorted(os.listdir(base)):
            if (
                file_name.endswith(".json")
                and not file_name.startswith("questions_")
                and file_name != "sources.json"
            ):
                report.merge(validate_subsidy_catalog(file_name[:-len(".json")], base))
    return report
