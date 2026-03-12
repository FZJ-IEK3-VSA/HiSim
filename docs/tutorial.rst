.. _tutorial:
Executing a Building Simulation
-----------------------
In ``ETHOS.HiSim`` we are calling a specific building configuration a ``system setup``. A system setup encompasses the building itself, all the technical infrastructure and other parameters, such as geographic location, weather, residents, energy prices and many more. You can find the predefined system setups in the directory ``HiSim/system_setups``. 


Run Simple System Setups
-----------------------
We provide some simplified examples to show the general principles of the simulation. 
You can run the simple system setups in the directory ``HiSim/system_setups`` with the following command:

``python ../hisim/hisim_main.py simple_system_setup_one.py``

or


``python ../hisim/hisim_main.py simple_system_setup_two.py``


This command executes ``hisim_main.py`` on the setup function ``setup_function`` implemented in the files ``simple_system_setup_one.py``
and ``simple_system_setup_two.py`` that are stored in ``HiSim/system_setups``.
The results can be visualized under directory `results` created under the same directory where the script with the setup
function is located.


Run Basic Household System Setup
-----------------------
The directory ``HiSim/system_setups`` also contains a basic household configuration in the script ``basic_household.py``.
It can be executed with the following command:


``python ../hisim/hisim_main.py basic_household``


The system is set up with the following elements:

* Occupancy (Residents' Demands)
* Weather
* Photovoltaic System
* Building
* Heat Pump

Hence, photovoltaic modules and the heat pump are responsible to cover the electricity the thermal energy demands as best as possible. As the name of the setup function says, the components are explicitly connected to each other, binding inputs correspondingly to its output sequentially. This is difference then automatically connecting inputs and outputs based its similarity. For a better understanding of explicit connection, proceed to session ``IO Connecting Functions``.

