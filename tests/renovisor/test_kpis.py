"""The KPI block: which HiSim figures it reads, how it scales them, and what it mocks.

The test that matters most here is the drift check. The three names :class:`KpiSources` reads are
HiSim internals, not contract fields, and a rename in ``kpi_preparation.py`` would otherwise turn
into a result payload that quietly lost its energy demand. So the names are looked for in the
source file itself, exactly as the bindings table's resolution check does for component fields
(challenge C22).

The rest is the behaviour a reader of ``result.json`` depends on: an annual figure from a
part-year run is scaled and labelled ``PARTIAL``, a rate is not scaled at all, the mocked
constants are the contract's own examples rather than numbers typed into HiSim, and a field with
no source is absent with its reason rather than present with a zero.
"""

import datetime
import json
import math
from pathlib import Path
from typing import Any, Dict

import pytest

from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.apply import AddedLayer
from hisim.components.building.building import Building
from hisim.renovisor.capabilities import ProbeSet
from hisim.renovisor.constants import ComfortGrades, GradeScale
from hisim.renovisor.kpis import (
    LifecycleCo2,
    ComfortSources,
    ContractExamples,
    KpiBuilder,
    KpiDocument,
    KpiField,
    KpiSchema,
    KpiSources,
)
from hisim.renovisor.layers import ElementAreas, EnvelopeLayers
from hisim.renovisor.request import Material
from hisim.renovisor.provenance import Period
from hisim.renovisor.vocabulary import Provenance, ThermalElement

pytestmark = pytest.mark.base

KPI_PREPARATION = (
    Path(__file__).resolve().parent.parent.parent
    / "hisim"
    / "postprocessing"
    / "kpi_computation"
    / "kpi_preparation.py"
)


def document_of(values: Dict[str, float]) -> KpiDocument:
    """Return a KPI document carrying *values*, in the three-level shape HiSim writes.

    Args:
        values: KPI name -> value.

    Returns:
        The document, with every entry under one building object and one group.
    """
    return KpiDocument(
        {
            "BUI1": {
                "General": {
                    name: {"name": name, "unit": "kWh", "value": value}
                    for name, value in values.items()
                }
            }
        }
    )


def one_day() -> Period:
    """Return the period of the committed one-day example."""
    return Period.from_dates(datetime.datetime(2021, 1, 1), datetime.datetime(2021, 1, 2))


def a_full_year() -> Period:
    """Return a full calendar year."""
    return Period.from_dates(datetime.datetime(2021, 1, 1), datetime.datetime(2022, 1, 1))


def layers_of(*additions: AddedLayer) -> EnvelopeLayers:
    """Return the layers of a package, with one fixed area per element.

    Args:
        *additions: The insulation effects.

    Returns:
        The resolved layers; every element is 100 m2, which keeps the arithmetic checkable by eye.
    """
    areas = ElementAreas(
        areas={element: 100.0 for element in ThermalElement},
        sources={element: "a test" for element in ThermalElement},
    )
    return EnvelopeLayers.of(additions, areas)


def a_material() -> Material:
    """Return the mockup's own material, which is what a request carries under rule 5."""
    return Material.from_dict(ProbeSet.material())


def build(document: Any, period: Period, layers: EnvelopeLayers, co2: Any = None) -> Any:
    """Return the KPI block for one set of inputs."""
    return KpiBuilder(
        document=document,
        period=period,
        layers=layers,
        lifecycle_co2=co2,
        country="IE",
    ).build()


def test_every_kpi_name_the_table_reads_exists_in_kpi_preparation() -> None:
    """Challenge C22: a renamed HiSim KPI is a failing build, not a silently absent field."""
    source = KPI_PREPARATION.read_text(encoding="utf-8")
    for name in KpiSources.all_names():
        assert f'"{name}"' in source, f"{name} is no longer a KPI name in kpi_preparation.py"


def test_an_annual_figure_from_a_short_run_is_scaled_and_partial() -> None:
    """A day's purchased energy times 365-ish is an extrapolation and says so (requirement A14)."""
    document = document_of({KpiSources.ENERGY_DEMAND_NAME: 10.0})

    block = build(document, one_day(), layers_of())

    value = block.values[KpiField.ENERGY_DEMAND.value]
    assert value["provenance"] == Provenance.PARTIAL.value
    assert value["value"] == pytest.approx(10.0 * 365.25)
    assert value["period"]["fraction_of_year"] == pytest.approx(1 / 365, rel=1e-2)


def test_an_annual_figure_from_a_full_year_is_simulated_and_unscaled() -> None:
    """Nothing is extrapolated when a whole year was simulated, so nothing is labelled PARTIAL."""
    document = document_of({KpiSources.ENERGY_DEMAND_NAME: 3000.0})

    block = build(document, a_full_year(), layers_of())

    value = block.values[KpiField.ENERGY_DEMAND.value]
    assert value["provenance"] == Provenance.SIMULATED.value
    assert value["value"] == pytest.approx(3000.0, rel=1e-2)


def test_a_rate_is_never_scaled_to_a_year() -> None:
    """Forty per cent over a day is forty per cent; dividing it would produce a nonsense."""
    document = document_of({KpiSources.SELF_SUFFICIENCY_NAME: 40.0})

    block = build(document, one_day(), layers_of())

    value = block.values[KpiField.SELF_SUFFICIENCY.value]
    assert value["value"] == 40.0
    assert value["provenance"] == Provenance.SIMULATED.value


def test_an_absent_single_source_kpi_makes_the_field_missing() -> None:
    """Decision R8: no source, no field -- and a reason naming the KPI that was not there."""
    block = build(document_of({}), one_day(), layers_of())

    assert KpiField.ENERGY_DEMAND.value not in block.values
    reasons = {entry.field: entry.reason for entry in block.missing}
    assert KpiSources.ENERGY_DEMAND_NAME in reasons[f"kpis.{KpiField.ENERGY_DEMAND.value}"]


def test_a_run_without_a_kpi_file_leaves_every_computed_field_missing() -> None:
    """A run whose options produced no ``all_kpis.json`` publishes no KPI, not a set of zeros."""
    block = build(None, one_day(), layers_of())

    missing = {entry.field for entry in block.missing}
    assert f"kpis.{KpiField.ENERGY_DEMAND.value}" in missing
    assert f"kpis.{KpiField.SELF_SUFFICIENCY.value}" in missing


def test_the_mocked_values_are_the_contracts_own_examples() -> None:
    """Decision A12 allows a mocked value; it does not allow one HiSim made up."""
    schema = ContractFiles.openapi()["components"]["schemas"]["Kpis"]["properties"]
    block = build(document_of({}), one_day(), layers_of())

    assert (
        block.values[KpiField.DISRUPTION_DAYS.value]["value"]
        == schema["disruption_days_by_level"]["examples"][0]
    )
    assert (
        block.values[KpiField.INDOOR_AIR_QUALITY.value]["value"]
        == schema["indoor_air_quality"]["examples"][0]
    )
    for field in (
        KpiField.DISRUPTION_DAYS,
        KpiField.INDOOR_AIR_QUALITY,
        KpiField.THERMAL_INSULATION_EFFECT,
    ):
        assert block.values[field.value]["provenance"] == Provenance.MOCKED.value


def test_the_mocked_source_names_the_schema_path_it_was_read_from() -> None:
    """A reader has to be able to find the constant in the contract without asking anybody."""
    _, source = ContractExamples.of(KpiField.SUMMER_HEAT_PROTECTION.value)
    assert ContractFiles.OPENAPI_FILENAME in source
    assert "examples[0]" in source


def test_the_energy_label_is_a_labelled_null() -> None:
    """No BER or DEAP procedure exists in HiSim, so no letter is invented (A12)."""
    block = build(document_of({}), one_day(), layers_of())

    value = block.values[KpiField.ENERGY_LABEL.value]
    assert value["value"] is None
    assert value["provenance"] == Provenance.MOCKED.value


def test_embodied_carbon_is_the_request_materials_footprint_times_the_element_area() -> None:
    """Decision Q22 under rule 5: the request's own kg CO2 per m2 times the element's area."""
    material = a_material()
    layers = layers_of(
        AddedLayer(
            element=ThermalElement.FACADE,
            placement="external_wall_external",
            thickness_in_mm=100,
            material=material,
            measure_id="external_insulation",
        )
    )

    block = build(document_of({}), one_day(), layers)

    value = block.values[KpiField.EMBODIED_CO2.value]
    assert material.co2_footprint_a1_a3_c3_c4_kg_m2 is not None
    assert value["value"] == pytest.approx(material.co2_footprint_a1_a3_c3_c4_kg_m2 * 100.0)
    assert value["provenance"] == Provenance.SIMULATED.value


def test_embodied_carbon_is_absent_when_the_request_states_no_footprint() -> None:
    """A material the frontend sent without a CO2 column makes the figure absent, not zero."""
    layers = layers_of(
        AddedLayer(
            element=ThermalElement.FACADE,
            placement="external_wall_external",
            thickness_in_mm=100,
            material=Material(asp_id="pir", thermal_conductivity_w_mk=0.022),
            measure_id="external_insulation",
        )
    )

    block = build(document_of({}), one_day(), layers)

    assert KpiField.EMBODIED_CO2.value not in block.values
    reasons = {entry.field: entry.reason for entry in block.missing}
    assert "co2_footprint_a1_a3_c3_c4_kg_m2" in reasons[f"kpis.{KpiField.EMBODIED_CO2.value}"]


def test_a_package_without_an_envelope_measure_has_no_embodied_carbon_field() -> None:
    """Decision R8 again: a heating-only package has no insulation, not zero insulation."""
    block = build(document_of({}), one_day(), layers_of())

    assert KpiField.EMBODIED_CO2.value not in block.values
    assert f"kpis.{KpiField.EMBODIED_CO2.value}" in {entry.field for entry in block.missing}


def test_a_layer_with_no_element_area_makes_the_whole_figure_missing() -> None:
    """A partial sum over some of a building's insulation would read as the whole building's."""
    areas = ElementAreas(areas={}, sources={})
    layers = EnvelopeLayers.of(
        (
            AddedLayer(
                element=ThermalElement.ROOF,
                placement="top_floor_ceiling",
                thickness_in_mm=160,
                material=a_material(),
                measure_id="rolled_out_attic_insulation",
            ),
        ),
        areas,
    )

    block = build(document_of({}), one_day(), layers)

    assert KpiField.EMBODIED_CO2.value not in block.values
    reasons = {entry.field: entry.reason for entry in block.missing}
    assert "no element area" in reasons[f"kpis.{KpiField.EMBODIED_CO2.value}"]


def test_the_kpi_document_finds_an_entry_by_its_plain_name() -> None:
    """The innermost key is qualified when two components collide; the entry's ``name`` is not."""
    document = KpiDocument(
        {
            "BUI1": {
                "Electricity Meter": {
                    "ElectricityMeter: Total energy from grid": {
                        "name": "Total energy from grid",
                        "unit": "kWh",
                        "value": 18.0,
                    }
                }
            }
        }
    )

    assert document.number("Total energy from grid") == 18.0
    assert document.number("something else") is None


def test_the_kpi_document_reads_a_file_written_by_the_run(tmp_path: Path) -> None:
    """The loader takes a result directory, and an absent file is an absent document."""
    assert KpiDocument.load(tmp_path) is None
    (tmp_path / KpiDocument.FILE_NAME).write_text(
        json.dumps({"BUI1": {"General": {"x": {"name": "x", "unit": "-", "value": 1.0}}}}),
        encoding="utf-8",
    )

    document = KpiDocument.load(tmp_path)

    assert document is not None
    assert document.number("x") == 1.0


def test_the_map_pane_describes_every_kpi_field() -> None:
    """A field added to the payload without a row on the translation map is a failing build."""
    described = {row.field for row in KpiSchema.rows()}
    assert described == {field.value for field in KpiField}


def test_emissions_come_from_the_cost_engine_with_the_dwellings_own_factors() -> None:
    """The legacy CO2 KPI is not read: its factor table has rows for DE and AT only.

    The cost engine prices and weighs the run against ``energy_prices_<country>.json`` for the
    country the economic parameters name, so an Irish dwelling's operational carbon is Ireland's
    grid. Over a whole year the figure is a measurement of the run and therefore ``SIMULATED``.
    """
    co2 = LifecycleCo2(
        {
            "operational_co2_by_year_in_kg": [0.0, 2500.0, 2500.0],
            "operational_co2_by_carrier_in_kg": {"ELECTRICITY": 50000.0},
            "emission_factor_by_carrier_in_kg_per_kwh": {"ELECTRICITY": 0.28},
        }
    )

    block = build(document_of({}), a_full_year(), layers_of(), co2)

    value = block.values[KpiField.EMISSIONS.value]
    assert value["value"] == pytest.approx(2500.0)
    assert value["provenance"] == Provenance.SIMULATED.value
    assert "IE" in value["source"]
    assert "ELECTRICITY at 0.28 kg/kWh" in value["source"]


def test_emissions_are_partial_over_a_part_year_and_absent_without_the_engine() -> None:
    """A part-year figure is the engine's extrapolation; no engine export means no figure."""
    co2 = LifecycleCo2({"operational_co2_by_year_in_kg": [0.0, 2500.0]})

    partial = build(document_of({}), one_day(), layers_of(), co2)
    assert partial.values[KpiField.EMISSIONS.value]["provenance"] == Provenance.PARTIAL.value

    absent = build(document_of({}), a_full_year(), layers_of(), None)
    assert KpiField.EMISSIONS.value not in absent.values
    reasons = {entry.field: entry.reason for entry in absent.missing}
    assert "lifecycle_costs.json" in reasons[f"kpis.{KpiField.EMISSIONS.value}"]


def comfort_document(below: float, above: float, unit: str = Building.DEGREE_HOURS_UNIT) -> KpiDocument:
    """Return a KPI document with the two degree-hour sums the comfort grades read.

    Args:
        below: The heating degree-hours of the simulated period, which the builder does not scale.
        above: The summer degree-hours of the simulated period, likewise.
        unit: The unit the entries carry; empty for an entry that names none.

    Returns:
        The document, in the three-level shape HiSim writes.
    """
    values = {ComfortSources.UNDERHEATING_NAME: below, ComfortSources.OVERHEATING_NAME: above}
    return KpiDocument(
        {
            "BUI1": {
                "Building": {
                    name: {"name": name, "unit": unit, "value": value}
                    for name, value in values.items()
                }
            }
        }
    )


def nearly_a_year() -> Period:
    """Return 363 days, which :meth:`Period.is_full_year` admits as a year (0.6 % short)."""
    return Period.from_dates(datetime.datetime(2021, 1, 1), datetime.datetime(2021, 12, 30))


class TestComfortGrades:
    """hisim-sska (renovisorissues #29): the grades are computed from the simulated year.

    Scope: every test feeds a hand-made ``all_kpis.json``. The chain from a real full-year run to
    the graded ``result.json`` is left to a slow job (bead hisim-evw9).
    """

    @pytest.mark.parametrize(
        "below, heating", [(0.0, "high"), (100.0, "high"), (100.1, "medium"), (500.0, "medium"), (501.0, "low")]
    )
    def test_the_heating_grade_cuts_at_100_and_500(self, below: float, heating: str) -> None:
        """Degree-hours below the house's own setpoint; a value on a limit takes the better grade."""
        block = build(comfort_document(below, 0.0), a_full_year(), layers_of())

        leaf = block.values[KpiField.COMFORT.value]["heating"]
        assert leaf["value"] == heating
        assert leaf["provenance"] == Provenance.SIMULATED.value
        assert ComfortSources.UNDERHEATING_NAME in leaf["source"]

    @pytest.mark.parametrize(
        "above, cooling, protection",
        [(0.0, "high", 5), (250.0, "high", 5), (400.0, "high", 4), (700.0, "medium", 3),
         (1200.0, "medium", 2), (1500.0, "low", 1)],
    )
    def test_the_summer_grades_share_one_axis(self, above: float, cooling: str, protection: int) -> None:
        """comfort.cooling and summer_heat_protection grade the same degree-hours above 26 °C."""
        block = build(comfort_document(0.0, above), a_full_year(), layers_of())

        assert block.values[KpiField.COMFORT.value]["cooling"]["value"] == cooling
        summer = block.values[KpiField.SUMMER_HEAT_PROTECTION.value]
        assert summer["value"] == protection
        assert summer["provenance"] == Provenance.SIMULATED.value

    @pytest.mark.parametrize("below, shown, heating", [(100.04, "100.0", "high"), (100.06, "100.1", "medium")])
    def test_the_grade_is_taken_from_the_value_the_source_shows(self, below: float, shown: str, heating: str) -> None:
        """Just above a limit: rounded to one decimal, shown with one and graded as shown."""
        block = build(comfort_document(below, 0.0), a_full_year(), layers_of())

        leaf = block.values[KpiField.COMFORT.value]["heating"]
        assert leaf["value"] == heating
        assert f" = {shown} {Building.DEGREE_HOURS_UNIT} over the simulated year;" in leaf["source"]

    @pytest.mark.parametrize("period", [nearly_a_year(), a_full_year()], ids=["363 days", "365 days"])
    def test_a_full_year_period_is_graded_unscaled(self, period: Period) -> None:
        """A period that counts as a year is that year: 100.0 on the limit stays high, 99.5 is 99.5."""
        assert period.is_full_year()
        for below, shown in ((99.5, "99.5"), (100.0, "100.0")):
            leaf = build(comfort_document(below, 0.0), period, layers_of()).values[KpiField.COMFORT.value]["heating"]
            assert leaf["value"] == "high"
            assert f" = {shown} {Building.DEGREE_HOURS_UNIT} over the simulated year;" in leaf["source"]

    @pytest.mark.parametrize("unit", [Building.DEGREE_HOURS_UNIT, ""])
    def test_the_source_quotes_the_building_s_unit(self, unit: str) -> None:
        """The unit the KPI was published in, or the Building's own when the entry names none."""
        block = build(comfort_document(10.0, 0.0, unit=unit), a_full_year(), layers_of())

        source = block.values[KpiField.COMFORT.value]["heating"]["source"]
        assert f"{Building.DEGREE_HOURS_UNIT} over the simulated year" in source
        assert "K*h" not in source

    @pytest.mark.parametrize("value", [float("nan"), float("inf")])
    def test_a_non_finite_sum_is_absent_with_the_value(self, value: float) -> None:
        """A NaN temperature propagates to the sum; it is named, not graded as the worst or best."""
        document = KpiDocument(
            {"BUI1": {"Building": {"x": {"name": ComfortSources.OVERHEATING_NAME, "unit": "°C*h", "value": value}}}}
        )
        block = build(document, a_full_year(), layers_of())

        assert KpiField.SUMMER_HEAT_PROTECTION.value not in block.values
        reasons = {entry.field: entry.reason for entry in block.missing}
        for path in ("comfort.cooling", KpiField.SUMMER_HEAT_PROTECTION.value):
            assert ComfortSources.OVERHEATING_NAME in reasons[f"kpis.{path}"]
            assert repr(value) in reasons[f"kpis.{path}"]

    def test_the_two_summer_scales_fail_together(self) -> None:
        """'low' and 1 start above one shared limit, DIN 4108-2's requirement for homes."""
        summer, protection = ComfortGrades.SUMMER, ComfortGrades.SUMMER_HEAT_PROTECTION
        limit = summer.limits[-1]

        assert protection.limits[-1] == limit
        assert summer.grade(limit) != summer.worst and protection.grade(limit) != protection.worst
        assert summer.grade(limit + 0.1) == summer.worst and protection.grade(limit + 0.1) == protection.worst

    def test_a_short_run_publishes_no_grade(self) -> None:
        """A winter day has no summer: the three grades are missing with the reason."""
        block = build(comfort_document(10.0, 0.0), one_day(), layers_of())

        assert KpiField.COMFORT.value not in block.values
        assert KpiField.SUMMER_HEAT_PROTECTION.value not in block.values
        missing = {entry.field: entry.reason for entry in block.missing}
        for path in ("comfort.heating", "comfort.cooling", KpiField.SUMMER_HEAT_PROTECTION.value):
            assert missing[f"kpis.{path}"] == ComfortSources.SHORT_RUN_REASON

    def test_a_year_without_the_kpis_names_what_is_missing(self) -> None:
        """No invented grade when the Building did not report its degree-hours."""
        block = build(document_of({}), a_full_year(), layers_of())

        assert KpiField.COMFORT.value not in block.values
        reasons = [entry.reason for entry in block.missing if entry.field == "kpis.comfort.heating"]
        assert reasons and ComfortSources.UNDERHEATING_NAME in reasons[0]


class TestGradeScale:
    """The scale refuses what no sum of clipped temperature differences produces."""

    def test_a_negative_value_is_refused_with_the_value(self) -> None:
        """A negative sum is a bug upstream, not the best grade."""
        with pytest.raises(ValueError, match="-0.5"):
            ComfortGrades.HEATING.grade(-0.5)

    def test_a_non_finite_value_is_refused(self) -> None:
        """A value that compares false with every limit, like NaN, would otherwise take the worst grade."""
        with pytest.raises(ValueError, match="nan"):
            ComfortGrades.HEATING.grade(math.nan)

    @pytest.mark.parametrize(
        "limits, grades",
        [((500.0, 100.0), ("high", "medium")), ((100.0, 100.0), ("high", "medium")),
         ((-1.0, 100.0), ("high", "medium")), ((100.0,), ("high", "medium")), ((), ())],
    )
    def test_a_malformed_scale_is_refused_on_construction(self, limits: Any, grades: Any) -> None:
        """Descending, repeated or negative limits, a grade without a limit, or no limit at all."""
        with pytest.raises(ValueError):
            GradeScale(limits=limits, grades=grades, worst="low")
