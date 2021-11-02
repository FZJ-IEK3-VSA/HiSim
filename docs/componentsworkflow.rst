.. _componentsworkflow:

Components Workflow
======================================

Understanding components is an essential part of `BESIM`. For the basic simulations, the requirements to handle components are twofold: knowing how to create an object to implement within the `setup function` and how to connect the objects among themselves.

The examples implemented in *BESIM* are used here as

Create a Component object
--------------------------------------


.. code-block:: python

    my_photovoltaic_system = pvs.PVSystem(location=location,
                                          power=power,
                                          load_module_data=load_module_data,
                                          module_name=module_name,
                                          integrateInverter=integrateInverter,
                                          inverter_name=inverter_name,
                                          sim_params=my_sim_params)


Connect Component Objects
--------------------------------------

.. code-block:: python

    # Create sum builder object
      my_sum = SumBuilderForTwoInputs(name="Sum",
                                      loadtype=loadtypes.LoadTypes.Any,
                                      unit=loadtypes.Units.Any)
      # Connect inputs from sum object to both previous outputs
      my_sum.connect_input(input_fieldname=my_sum.SumInput1,
                           src_object_name=my_rn1.ComponentName,
                           src_field_name=my_rn1.RandomOutput)
      my_sum.connect_input(input_fieldname=my_sum.SumInput2,
                           src_object_name=my_rn2.ComponentName,
                           src_field_name=my_rn2.RandomOutput)
      my_sim.add_component(my_sum)

