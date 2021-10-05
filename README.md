
<a href="https://www.fz-juelich.de/iek/iek-3/EN/Home/home_node.html"><img src="https://www.fz-juelich.de/SharedDocs/Bilder/INM/INM-1/EN/FZj_Logo.jpg?__blob=normal" alt="Forschungszentrum Juelich Logo" width="230px"></a>
<a href="https://www.hs-emden-leer.de/">
<img src="https://www.hs-emden-leer.de/typo3conf/ext/fr_sitepackage_hs_emden_leer/Resources/Public/Images/logo-header-normal.svg" alt="Hochschule Emden/Leer" width="230px"></a>



# HiSim - House Infrastructure Simulator
HiSim is a Python package for simulation and analysis of household scenarios using modern components as alternative to fossil fuel based ones. This package integrates load profiles generation of electricity consumption, heating demand, electricity generation, and strategies of smart strategies of modern components, such as heat pump, battery, electric vehicle or thermal energy storage. HiSim is a package under development by Forschungszentrum Jülich und Hochschule Emden/Leer.




Clone repository
-----------------------
To clone this repository, enter the following command to your terminal:

```python
git clone GITHUB_LINK_TO_REPOSITORY
```

Virtual Environment
-----------------------
Before installing `hisim`, it is recommended to set up a python virtual environment. Let `hisimvenv` be the name of virtual environment to be created. For Windows users, setting the virtual environment in the path `\hisim` is done with the command line:

```python
python -m venv hisimvenv
```

After its creation, the virtual environment can be activated in the same directory:
```python
hisimvenv\Scripts\activate
```
For Linux/Mac users, the virtual environment is set up and activated as follows:

```python
virtual hisimvenv
source hisimvenv/bin/activate
```
Alternatively, Anaconda can be used to set up and activate the virtual environment:
```python
conda create -n hisimvenv python=x.x anaconda
source activate hisimvenv
```
With the successful activation, `hisim` is ready to be locally installed.

Install package
------------------------
After setting up the virtual environment, install the package to your local libraries:

```python
python setup.py install
```
Run Simple Examples
-----------------------
Run the python interpreter in the `hisim/examples` directory with the following command:

```python
python ../hisim/hisim.py examples first_example
```

This command executes `hisim.py` on the setup function `first_example` implemented in the file `examples.py` that is stored in `hisim/examples`. The same file contains another setup function that can be used: `second_example`. The results can be visualized under directory `results` created under the same directory where the script with the setup function is located.

Run Basic Household Example
-----------------------
The directory `hisim\examples` also contains a basic household configuration in the script `basic_household.py`. The first setup function (`basic_household_explicit`) can be executed with the following command:

```python
python ../hisim/hisim.py basic_household basic_household_explicit
```

The system is set up with the following elements:

* Occupancy (Residents' Demands)
* Weather
* Photovoltaic System
* Building
* Heat Pump

Hence, photovoltaic modules and the heat pump are responsible to cover the electricity the thermal energy demands as best as possible. As the name of the setup function says, the components are explicitly connected to each other, binding inputs correspondingly to its output sequentially. This is difference then automatically connecting inputs and outputs based its similarity. For a better understanding of explicit connection, proceed to session `IO Connecting Functions`.

Generic Setup Function Walkthrough
---------------------
The basic structure of a setup function follows:
1. Set the simulation parameters (See `SimulationParameters` class in `hisim/hisim/component.py`)
1. Create a `Component` object and add it to `Simulator` object
    1. Create a `Component` object from one of the child classes implemented in `hisim/hisim/components`
        1. Check if `Component` class has been correctly imported
    1. If necessary, connect your object's inputs with previous created `Component` objects' outputs.
    1. Finally, add your `Component` object to `Simulator` object
1. Repeat step 2 while all the necessary components have been created, connected and added to the `Simulator` object.

Once you are done, you can run the setup function according to the description in the simple example run.

Package Structure
-----------
The main program is executed from `hisim/hisim/hisim.py`. The `Simulator`(`simulator.py`) object groups `Component`s declared and added from the setups functions. The `ComponentWrapper`(`simulator.py`) gathers together the `Component`s inside an `Simulator` Object. The `Simulator` object performs the entire simulation under the function `run_all_timesteps` and stores the results in a Python pickle `data.pkl` in a subdirectory of `hisim/hisim/results` named after the executed setup function. Plots and the report are automatically generated from the pickle by the class `PostProcessor` (`hisim/hisim/postprocessing/postprocessing.py`).

Component Class
-----------
A child class inherits from the `Component` class in `hisim/hisim/component.py` and has to have the following methods implemented:

* i_save_state: updates previous state variable with the current state variable
* i_restore_state: updates current state variable with the previous state variable
* i_simulate: performs a timestep iteration for the `Component`
* i_doublecheck: checks if the values are expected throughout the iteration

These methods are used by `Simulator` to execute the simulation and generate the results.

List of `Component` children
-----------
Theses classes inherent from `Component` (`component.py`) class and can be used in your setup function to customize different configurations. All `Component` class children are stored in `hisim/hisim/components` directory. Some of these classes are:
- `RandomNumbers` (`random_numbers.py`)
- `SimpleController` (`simple_controller.py`)
- `SimpleSotrage` (`simple_storage.py`)
- `Transformer` (`transformer.py`)
- `PVSystem` (`pvs.py`)
- `CHPSystem` (`chp_system.py`)
- `Csvload` (`csvload.py`)
- `SumBuilderForTwoInputs` (`sumbuilder.py`)
- `SumBuilderForThreeInputs` (`sumbuilder.py`)
- ToDo: more components to be added

Connecting Input/Outputs
-----------
Let `my_home_electricity_grid` and `my_appliance` be Component objects used in the setup function. The object `my_apppliance` has an output `ElectricityOutput` that has to be connected to an object `ElectricityGrid`. The object `my_home_electricity_grid` has an input `ElectricityInput`, where this connection takes place. In the setup function, the connection is performed with the method `connect_input` from the `Simulator` class:

```python
my_home_electricity_grid.connect_input(input_fieldname=my_home_electricity_grid.ElectricityInput,
                                       src_object_name=my_appliance.ComponentName,
                                       src_field_name=my_appliance.ElectricityOutput)
```

Configuration Automator
-----------
A configuration automator is under development and has the goal to reduce connections calls among similar components.

Post Processing
-----------
After the simulator runs all time steps, the post processing (`postprocessing.py`) reads the persistent saved results, plots the data and
generates a report.



