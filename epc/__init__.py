"""The EPC calculator: the certificate heating demand of the building a HiSim run computed with.

The calculator is three steps over two dictionaries, and its folders mirror them. Step 1
(:mod:`epc.inputs`) reads the ``building.expanded.yaml`` HiSim writes beside a run's realized record
into one country-neutral :class:`~epc.model.building.BuildingDescription`, computing nothing. Step 2
(:mod:`epc.engines`) hands that description to the engine of the run's country, which returns an
:class:`~epc.model.result.EpcResult`. Step 3 (:mod:`epc.reports`) writes the result as JSON, and a
batch adds one table and one summary over a results tree.

The package imports nothing from HiSim: the expanded record is its only input
(``roadmap/epc/input_contract.md``), and ``roadmap/epc/design.md`` is its specification.
"""

#: The calculator's version, stated in every result as ``run.calculator``.
__version__ = "0.1"
