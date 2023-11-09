.. _componentsworkflow:

Components Workflow
======================================

Understanding components is an essential part of `HiSim`. For the basic simulations, the requirements to handle components are twofold:

* create an Component object to be implemented within the `setup function`
* connect Component objects among themselves.

The snippets showed here are found in the system_setup file `basic_household.py`.

Create a Component object
--------------------------------------

All the implemented *Component* classes are implemented in directory *hisim/components*. The Python modules are named after the corresponded class, but it might have supplementary classes implemented, as such, the module *battery.py* has the *Battery* class implemented in it along with *BatteryController*.

In the following example, the object component *PVSystem* (for Photovoltaic System) from setup function *setup_function* is explored. The class *PVSystem* is found under the directory *hisim/components* in module *pvs.py*. To create an object, the module *pvs.py* has to be imported, and the required parameters from the class constructor have to be passed in the instantiation, as shown in the following snippet.

.. code-block:: python

    my_photovoltaic_system = pvs.PVSystem(location=location,
                                          power=power,
                                          load_module_data=load_module_data,
                                          module_name=module_name,
                                          integrateInverter=integrateInverter,
                                          inverter_name=inverter_name,
                                          sim_params=my_sim_params)


Connect Component objects
--------------------------------------

To pass and receive information, fluxes and energy among the components during the simulation, it is necessary to connect them. The class *Component* as well as all its children have the method *connect_input* to connect to previously instantiated *Components* objects. The inputs to be connected can be found in the implementation as *ComponentInput* attributes. For example, in the Component class *PVSystem*, the following *ComponentInput* is passed as an attribute of the class:

.. code-block:: python

        self.DNIC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                    self.DirectNormalIrradiance,
                                                    lt.LoadTypes.Irradiance,
                                                    lt.Units.Wm2,
                                                    True)

This means, it is necessary to find a Component class that has an *ComponentOutput* defined as *DirectNormalIrradiance*. This is the case for the class *Weather* in module *weather.py*.

The method *connect_input* requires three parameters:

* input_fieldname: *ComponentInput* from your current *Component* object.
* src_object_name: name of *Component* previously instantiated to be connected to.
* src_field_name: *ComponentOutput* from *Component* previously instantiated to be connected to.

Take the Component *PVSystem* object used as an example in the previous section. To connect the *DirectNormalIrradiance* from the object Component *PVSystem* to object component *Weather*, the parameters have to be initialized as:

* input_fieldname: The *ComponentInput* name is *DirectNormalIrradiance*, hence *my_photovoltaic_system.DirectNormalIrradiance*
* src_object_name: name of *Component* owner of the *ComponentOutput* to be connected to the object *PVSystem* is the object class *Weather*, hence *my_weather.ComponentName*
* src_field_name: *ComponentOutput* from the object class *Weather* is *DirectNormalIrradiance*, therefore *my_weather.DirectNormalIrrandiance*.

.. code-block:: python

    my_photovoltaic_system.connect_input(my_photovoltaic_system.DirectNormalIrradiance,
                                         my_weather.ComponentName,
                                         my_weather.DirectNormalIrradiance)


The names of *ComponentInput* and *ComponentOutput* do not have to be necessarily the same.

