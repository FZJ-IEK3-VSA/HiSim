"""The one part of a real component's surface the economics extraction asks every test double for.

``bridge.build_evaluation_inputs`` records each simulated component's KPI source
(``EvaluationInputs.component_sources``, ``roadmap/kpi_address_spec.md``) so the staged document's
cost rows can say which subject is a HiSim component. The economics tests drive the bridge with
duck-typed stand-ins that carry a ``component_name`` and little else; deriving from this class
gives them the source a real component of that name records, built the same way
(:meth:`hisim.component.Component.kpi_source`).
"""

from hisim.config import ComponentID, DisplayConfig
from hisim.postprocessing.kpi_computation.kpi_structure import KpiSource


def plain_component_source(component_name: str) -> KpiSource:
    """The KPI source a plain component of that name, with no building, unit or display name, records.

    The one place the tests build it: the bridge's test doubles (:class:`NamedComponentSource`) and
    the synthetic stages (``tests.economics.synthetic_stages.SyntheticPlan.component_source``)
    both record this source, so a cost row and its component's KPIs join on the same fields.
    """
    return KpiSource.for_component(ComponentID(component_name), DisplayConfig())


class NamedComponentSource:
    """Gives a test double of a component the KPI source of a plain component of its name."""

    component_name: str

    def kpi_source(self) -> KpiSource:
        """The KPI source a plain component of this name, with no display name, records."""
        return plain_component_source(self.component_name)
