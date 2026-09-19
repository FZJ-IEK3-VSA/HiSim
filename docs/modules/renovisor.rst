:orphan:

RenoVisor Translation Layer
===========================

.. automodule:: hisim.renovisor
   :members:
   :undoc-members:
   :show-inheritance:

Working Principle
-----------------

The :mod:`hisim.renovisor` package turns one **calculation request** -- a house
inventory plus a list of catalogue measures -- into one finished HiSim
simulation, in five steps that each own a module. **Validate**
(:mod:`~hisim.renovisor.request`) checks the request against the vendored JSON
Schema and against the frozen catalogue table, and reports every fault at once
rather than the first. **Apply** (:mod:`~hisim.renovisor.apply`, with
:mod:`~hisim.renovisor.envelope` for the U-value arithmetic) runs one function
per catalogue measure over a deep copy of the house and never names a HiSim
component, so a HiSim rename changes one binding rather than every measure.
**Translate** (:mod:`~hisim.renovisor.translate`, with
:mod:`~hisim.renovisor.tabula` for the archetype the dwelling is simulated as)
writes the renovated house into one recorded ``*.energy_system.yaml`` twin, and
:mod:`~hisim.renovisor.report` accounts for every leaf of the request in
``mapping_report.json``. **Run** (:mod:`~hisim.renovisor.run`, with
:mod:`~hisim.renovisor.simulation` for the release's own simulation parameters)
builds that file, simulates it and collects what HiSim wrote. **Results**
(:mod:`~hisim.renovisor.result`, fed by :mod:`~hisim.renovisor.kpis`,
:mod:`~hisim.renovisor.costs`, :mod:`~hisim.renovisor.layers` and
:mod:`~hisim.renovisor.provenance`) assemble ``result.json``, every value
carrying where it came from.

The **money** is not in ``result.json``. A renovation is a plan over several
years, and one run of one state cannot price it, so
:mod:`~hisim.renovisor.economics` builds the economic context the lifecycle cost
engine needs -- the existing-asset register, the envelope cost subjects and the
applicant -- and the whole of the money is produced afterwards by
:mod:`hisim.economics.staged` over the finished jobs of a plan::

    python -m hisim.economics staged \
        --stage jobs/baseline:0:baseline --stage jobs/package:0:"stage 1" \
        --out economics_result.json

That writes ``economics_result.json`` (:mod:`hisim.economics.staged_document`,
validated against ``hisim/economics/economics_result.schema.json``), and
``result.json`` lists every cost field of the contract under ``missing`` with
the key of that document which answers it. Beside the pipeline stands the **capability
document**: :mod:`~hisim.renovisor.capabilities` runs the whole probe set
through the pure layers and aggregates what this image can and cannot do, and
:mod:`~hisim.renovisor.map` renders the same probe run as the committed
``roadmap/renovisor/translation_map.html`` page.

API Reference
-------------

hisim.renovisor.request module
------------------------------

.. automodule:: hisim.renovisor.request
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.apply module
----------------------------

.. automodule:: hisim.renovisor.apply
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.envelope module
-------------------------------

.. automodule:: hisim.renovisor.envelope
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.constants module
--------------------------------

.. automodule:: hisim.renovisor.constants
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.tabula module
-----------------------------

.. automodule:: hisim.renovisor.tabula
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.translate module
--------------------------------

.. automodule:: hisim.renovisor.translate
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.whitelist module
--------------------------------

.. automodule:: hisim.renovisor.whitelist
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.report module
-----------------------------

.. automodule:: hisim.renovisor.report
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.simulation module
---------------------------------

.. automodule:: hisim.renovisor.simulation
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.run module
--------------------------

.. automodule:: hisim.renovisor.run
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.kpis module
---------------------------

.. automodule:: hisim.renovisor.kpis
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.costs module
----------------------------

.. automodule:: hisim.renovisor.costs
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.economics module
--------------------------------

.. automodule:: hisim.renovisor.economics
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.provenance module
---------------------------------

.. automodule:: hisim.renovisor.provenance
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.result module
-----------------------------

.. automodule:: hisim.renovisor.result
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.layers module
-----------------------------

.. automodule:: hisim.renovisor.layers
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.capabilities module
-----------------------------------

.. automodule:: hisim.renovisor.capabilities
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.map module
--------------------------

.. automodule:: hisim.renovisor.map
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.occupancy module
--------------------------------

.. automodule:: hisim.renovisor.occupancy
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.vocabulary module
---------------------------------

.. automodule:: hisim.renovisor.vocabulary
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.contract package
--------------------------------

.. automodule:: hisim.renovisor.contract
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.contract.refresh module
---------------------------------------

.. automodule:: hisim.renovisor.contract.refresh
   :members:
   :undoc-members:
   :show-inheritance:
