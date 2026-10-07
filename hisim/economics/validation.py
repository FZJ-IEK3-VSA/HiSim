"""Consistency checks for the shipped cost and subsidy data files (cost_spec.md §9.6).

Prices and schemes are data, so these checks catch what would be compile errors if the numbers were code: an unsourced
datapoint, an asset class with no price in a shipped country, a scheme conditioning on a question nobody asks, a tariff
whose id does not match its file name. An error means the data is inconsistent and fails CI; a warning (an old source,
an unreferenced entry) is advisory. Runs beside the engine, from the data-file tests and ``python -m hisim.economics
validate``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Set

from hisim.economics.database import CostDatabase, SourceRegistry
from hisim.economics.subsidies import (
    PerUnitBenefit,
    SubsidyCatalog,
    SubsidyContextFields,
    SubsidyScheme,
    TaxCreditBenefit,
    TieredPerUnitBenefit,
    question_targets,
    scheme_context_fields,
)
from hisim.economics.tariffs import TariffContract


class ValidationConstants:
    """Requirements the shipped data files are validated against.

    `REQUIRED_QUESTION_LANGUAGES` lists the languages every question catalog must cover, so the §5.7 questionnaire is
    answerable by the people a subsidy applies to; a new country with another official language extends it.
    """

    #: Languages the question catalogs must cover.
    REQUIRED_QUESTION_LANGUAGES = ("de", "en")


@dataclass
class ValidationReport:
    """Accumulated errors and warnings of a validation run.

    Errors fail CI; warnings are advisory. The checks collect rather than raise, so a reviewer sees every problem at
    once.
    """

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def merge(self, other: "ValidationReport") -> None:
        """Add another report's errors and warnings to this one, in place."""
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)

    @property
    def ok(self) -> bool:
        """True when there are no errors; warnings do not count. This is the CI gate and the CLI exit status."""
        return not self.errors


def validate_cost_database(
    base_path: Optional[str] = None,
    declared_asset_classes: Optional[Set] = None,
    used_carriers: Optional[Set] = None,
    reference_date: Optional[date] = None,
) -> ValidationReport:
    """Check the cost database: it loads, its sources and tariffs are valid, every needed price exists (§9.6).

    Schema checks happen by loading through `CostDatabase`; a load failure is one error and stops the run. Then come
    the tariff contracts (validated before the orphan check, so their sources count as referenced), orphaned and stale
    sources as warnings, and two coverage matrices: every declared asset class needs a device entry in every shipped
    country, and every used carrier needs a price entry. That way a new `ComponentType` without cost data fails CI
    rather than a user's run.

    Args:
        base_path: Cost database directory; the shipped one by default.
        declared_asset_classes: `ComponentType`s that must have device entries; omit to skip that check.
        used_carriers: `EnergyCarrier`s that must have price entries; omit to skip that check.
        reference_date: "Today" for the 12-month staleness check.

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

    # The tariff contracts ship inside the cost database. Validate them before the orphan check
    # so contract sources count as referenced.
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
    """Check that every shipped tariff contract parses, matches its file name and cites registry sources.

    Per file: the JSON parses through `TariffContract.from_json` (§8.2 schema, mandatory source ids), the contract id
    equals the file name (contracts are looked up by file name), a DYNAMIC contract's `spot_series` exists, and every
    source id resolves against the registry. ``inline:<citation>`` sources are an error in a file; they are allowed
    only for contracts built in memory (see `tariffs.py`).
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


def validate_subsidy_catalog(
    country: str, base_path: Optional[str] = None, cost_database: Optional[CostDatabase] = None
) -> ValidationReport:
    """Check one country's subsidy catalog: it loads, it is coherent, its questions are covered, and it is current.

    The schema is checked by loading. Then: snapshot-date staleness, unique scheme ids, `excludes` that name existing
    schemes, tax-credit instalment shares summing to 1, cumulation groups agreeing on their rate cap, and (given the
    cost database) per-unit schemes using the unit the country's device entries price their asset class in. Question
    coverage (§5.7): every context field a scheme's conditions read needs a question in every required language, or
    eligibility could depend on something the user was never asked; a question no scheme reads is a warning.

    Args:
        country: Catalog country code, i.e. the file stem (`DE`, `AT`).
        base_path: Catalog directory; the shipped one by default.
        cost_database: The database the per-unit size units are checked against; None skips that check.

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

    # Cross-scheme coherence. Benefit typing is enforced by the load above; what is left needs the
    # whole catalog: id uniqueness, `excludes` pointing at real schemes, and cumulation groups
    # whose members agree on their combined rate cap (the solver applies the group's minimum cap
    # to every member). `cumulation_group` is a label, not a scheme id, so it is not resolved.
    seen: Set[str] = set()
    for scheme in catalog.schemes:
        if scheme.id in seen:
            report.errors.append(f"Subsidy catalog {country}: duplicate scheme id {scheme.id!r}.")
        seen.add(scheme.id)
    # Display names. The field is optional (the report falls back to the id), so a missing one is
    # a warning; a shipped scheme without one shows `DE_BEG_EM_HP_SPEED_2024` instead of "speed
    # bonus", and two schemes sharing a name would make the report ambiguous.
    display_names: Dict[str, str] = {}
    for scheme in catalog.schemes:
        if scheme.display_name is None:
            report.warnings.append(
                f"Subsidy catalog {country}: scheme {scheme.id!r} has no display_name; the report "
                "will show its raw id (Q20)."
            )
            continue
        if not scheme.display_name.strip():
            report.errors.append(
                f"Subsidy catalog {country}: scheme {scheme.id!r} declares a blank display_name — "
                "omit the field to fall back to the id instead."
            )
        elif scheme.display_name in display_names:
            report.errors.append(
                f"Subsidy catalog {country}: schemes {display_names[scheme.display_name]!r} and "
                f"{scheme.id!r} share the display_name {scheme.display_name!r}; a reader could not "
                "tell the two apart."
            )
        else:
            display_names[scheme.display_name] = scheme.id
    for scheme in catalog.schemes:
        for excluded in scheme.excludes:
            if excluded not in seen:
                report.errors.append(
                    f"Subsidy catalog {country}: scheme {scheme.id!r} excludes unknown scheme id "
                    f"{excluded!r} — the exclusion can never fire."
                )
        benefit = scheme.benefit
        if cost_database is not None and isinstance(benefit, (PerUnitBenefit, TieredPerUnitBenefit)):
            for asset_class in scheme.asset_classes:
                priced_in = sorted(
                    {
                        entry.size_unit.value
                        for entry in cost_database.devices.get(country, [])
                        if entry.component_type == asset_class
                    }
                    - {benefit.size_unit.value}
                )
                if priced_in:
                    report.errors.append(
                        f"Subsidy catalog {country}: scheme {scheme.id!r} pays per "
                        f"{benefit.size_unit.value!r}, but {asset_class.value} is priced and sized in "
                        f"{priced_in} — the solver would refuse to price the measure."
                    )
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
    # on, and which question a derived field is asked through, come from the field-vocabulary
    # registry of the subsidy package.
    asked: Set[str] = set()
    for scheme in catalog.schemes:
        for fieldname in scheme_context_fields(scheme):
            if fieldname and not SubsidyContextFields.is_computed(fieldname):
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
    """Check the cost database and every shipped subsidy catalog.

    The entry point of ``python -m hisim.economics validate`` and the data-file CI test. Country catalogs are found by
    listing the directory (every `*.json` except `questions_*` and `sources.json`), so a new country is checked
    automatically. The coverage matrices are not checked here, because they need the classes and carriers components
    declare; a green run means the data is internally consistent, not that every component can be priced.

    Args:
        cost_database_path: Cost database directory; the shipped one by default.
        subsidy_base_path: Subsidy catalog directory; the shipped one by default.

    Returns:
        The merged report.
    """
    report = validate_cost_database(cost_database_path)
    try:
        cost_database: Optional[CostDatabase] = CostDatabase(cost_database_path)
    except Exception:  # pylint: disable=broad-except
        cost_database = None  # already reported by validate_cost_database; skip the unit cross-check
    base = subsidy_base_path or SubsidyCatalog.DEFAULT_PATH
    if os.path.isdir(base):
        for file_name in sorted(os.listdir(base)):
            if (
                file_name.endswith(".json")
                and not file_name.startswith("questions_")
                and file_name != "sources.json"
            ):
                report.merge(validate_subsidy_catalog(file_name[:-len(".json")], base, cost_database))
    return report
