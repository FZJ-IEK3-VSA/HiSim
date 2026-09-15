:orphan:

RenoVisor Translation Layer
===========================

.. automodule:: hisim.renovisor
   :members:
   :undoc-members:
   :show-inheritance:

The :mod:`hisim.renovisor` package turns a RenoVisor home inventory and a
renovation package into a HiSim simulation. It is built in layers, and the
layering is the point: the measure layer writes the *inventory* and never names
a HiSim component or configuration field, so a HiSim rename changes one binding
rather than every measure function.

Working Principle
-----------------

1. **Read the contract** (:mod:`hisim.renovisor.contract`) — the vendored copies
   of ``openapi.yaml`` (the inventory schema), ``measures.yaml`` (the measure
   catalogue) and ``materials.yaml`` (the insulation-material dump), pinned to a
   contract commit by ``PINNED.yaml``. Everything reads them through
   :class:`~hisim.renovisor.contract.ContractFiles`.

2. **Normalise the vocabularies** (:mod:`hisim.renovisor.vocabulary`,
   :mod:`hisim.renovisor.catalogue`) — the closed enum sets in HiSim spelling,
   and the only reader of the catalogue, which derives the measure, option and
   value ids a request uses.

3. **Apply the measures** (:mod:`hisim.renovisor.options`,
   :mod:`hisim.renovisor.registry`, :mod:`hisim.renovisor.effects`) — one
   function per catalogue measure produces effects from validated option values;
   the accumulator composes them once, so several insulation layers on one
   element add up instead of overwriting each other.

4. **Supply the physics** (:mod:`hisim.renovisor.envelope`,
   :mod:`hisim.renovisor.materials`) — where an element's current U-value comes
   from, how a missing thickness is derived from a regulatory target, and the
   material conductivities, each with its source.

5. **Write the result** (:mod:`hisim.renovisor.inventory`,
   :mod:`hisim.renovisor.application`, :mod:`hisim.renovisor.report`,
   :mod:`hisim.renovisor.base_files`) — the post-measure inventory, validated
   against the contract, plus which recorded energy-system file to run, which
   variants and groups to switch, and a report line for every field and every
   measure.

The bindings, the parametriser and the ``calculate`` command are a later step;
:mod:`hisim.renovisor.__main__` carries the intended command-line interface and
exits with a message rather than pretending to work.

API Reference
-------------

hisim.renovisor.contract module
-------------------------------

.. automodule:: hisim.renovisor.contract
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.vocabulary module
---------------------------------

.. automodule:: hisim.renovisor.vocabulary
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.catalogue module
--------------------------------

.. automodule:: hisim.renovisor.catalogue
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.options module
------------------------------

.. automodule:: hisim.renovisor.options
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.registry module
-------------------------------

.. automodule:: hisim.renovisor.registry
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.effects module
------------------------------

.. automodule:: hisim.renovisor.effects
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.envelope module
-------------------------------

.. automodule:: hisim.renovisor.envelope
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.materials module
--------------------------------

.. automodule:: hisim.renovisor.materials
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.materials\_import module
----------------------------------------

.. automodule:: hisim.renovisor.materials_import
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.inventory module
--------------------------------

.. automodule:: hisim.renovisor.inventory
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.base\_files module
----------------------------------

.. automodule:: hisim.renovisor.base_files
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.application module
----------------------------------

.. automodule:: hisim.renovisor.application
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.reasons module
------------------------------

.. automodule:: hisim.renovisor.reasons
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.report module
-----------------------------

.. automodule:: hisim.renovisor.report
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.tabula\_ie module
---------------------------------

.. automodule:: hisim.renovisor.tabula_ie
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.\_\_main\_\_ module
-----------------------------------

.. automodule:: hisim.renovisor.__main__
   :members:
   :undoc-members:
   :show-inheritance:
