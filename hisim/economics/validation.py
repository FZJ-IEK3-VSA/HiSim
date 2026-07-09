"""Data-file CI checks (cost_spec.md §9.6).

Since prices and schemes are data, the data gets the CI treatment code used to get
implicitly. These functions are called from tests (`tests/test_economics_data_files.py`) and
can be run standalone via ``python -m hisim.economics validate``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Set

from hisim.economics.database import DEFAULT_COST_DATABASE_PATH, CostDatabase
from hisim.economics.subsidies import DEFAULT_SUBSIDY_CATALOG_PATH, SubsidyCatalog

#: Languages the question catalogs must cover (spec Q31: de + en in v1).
REQUIRED_QUESTION_LANGUAGES = ("de", "en")


@dataclass
class ValidationReport:
    """Errors fail CI; warnings are advisory (staleness)."""

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def merge(self, other: "ValidationReport") -> None:
        """In-place union."""
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)

    @property
    def ok(self) -> bool:
        """No errors."""
        return not self.errors


def validate_cost_database(
    base_path: Optional[str] = None,
    declared_asset_classes: Optional[Set] = None,
    used_carriers: Optional[Set] = None,
    reference_date: Optional[date] = None,
) -> ValidationReport:
    """Schema + source completeness + coverage matrix + staleness for the cost database."""
    report = ValidationReport()
    path = base_path or DEFAULT_COST_DATABASE_PATH
    try:
        database = CostDatabase(path)
    except Exception as err:  # pylint: disable=broad-except — every load error is a CI error
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


def validate_subsidy_catalog(country: str, base_path: Optional[str] = None) -> ValidationReport:
    """Schema, condition grammar, question coverage and staleness for one country catalog."""
    report = ValidationReport()
    base = base_path or DEFAULT_SUBSIDY_CATALOG_PATH
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

    # Question coverage (§5.7, §9.6): every referenced user-answerable field has a question
    # in every required language; orphaned questions are flagged.
    referenced: Set[str] = set()
    for scheme in catalog.schemes:
        for fieldname in scheme.eligibility.referenced_fields():
            if fieldname and not fieldname.startswith("measure."):
                referenced.add(fieldname)
        if scheme.eligible_cost.proration == "RESIDENTIAL_SHARE":
            referenced.add("building.residential_floor_area_in_m2")
            referenced.add("building.commercial_floor_area_in_m2")
        if scheme.eligible_cost.cap_per_dwelling_unit_in_euro:
            referenced.add("building.dwelling_units")
    derived = {"building.residential_share"}  # derived, asked via the area questions
    for fieldname in sorted(referenced - derived):
        entry = catalog.questions.get(fieldname)
        if entry is None:
            report.errors.append(
                f"Subsidy catalog {country}: field {fieldname!r} referenced by scheme conditions has "
                "no question catalog entry (§5.7)."
            )
            continue
        for language in REQUIRED_QUESTION_LANGUAGES:
            if language not in entry.question:
                report.errors.append(
                    f"Subsidy catalog {country}: question for {fieldname!r} misses language {language!r}."
                )
    for fieldname in catalog.questions:
        if fieldname not in referenced and fieldname not in derived:
            report.warnings.append(
                f"Subsidy catalog {country}: orphaned question entry {fieldname!r} (referenced by no scheme)."
            )
    return report


def validate_all(cost_database_path: Optional[str] = None, subsidy_base_path: Optional[str] = None) -> ValidationReport:
    """Everything: cost database plus all shipped subsidy catalogs."""
    report = validate_cost_database(cost_database_path)
    base = subsidy_base_path or DEFAULT_SUBSIDY_CATALOG_PATH
    if os.path.isdir(base):
        for file_name in sorted(os.listdir(base)):
            if (
                file_name.endswith(".json")
                and not file_name.startswith("questions_")
                and file_name != "sources.json"
            ):
                report.merge(validate_subsidy_catalog(file_name[:-len(".json")], base))
    return report
