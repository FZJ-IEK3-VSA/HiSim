.. _installation:

Installation
=====================================================================
# HiSim - House Infrastructure Simulator
HiSim is a Python package for simulation and analysis of household scenarios using modern components as alternative to fossil fuel based ones. This package integrates load profiles generation of electricity consumption, heating demand, electricity generation, and strategies of smart strategies of modern components, such as heat pump, battery, electric vehicle or thermal energy storage. HiSim is a package under development by Forschungszentrum Jülich und Hochschule Emden/Leer. For detailed documentation, please access [ReadTheDocs](https://household-infrastructure-simulator.readthedocs.io/en/latest/) of this repository.

Clone repository
-----------------------
To clone this repository, enter the following command to your terminal:

``python
git clone https://github.com/FZJ-IEK3-VSA/HiSim.git
``

Set Virtual Environment
-----------------------
Before installing `hisim`, it is recommended to set up a python virtual environment. Let `hisimvenv` be the name of virtual environment to be created. For Windows users, setting the virtual environment in the path `\hisim` is done with the command line:

``python
python -m venv hisimvenv
``

After its creation, the virtual environment can be activated in the same directory:
``python
hisimvenv\Scripts\activate
``
For Linux/Mac users, the virtual environment is set up and activated as follows:

``python
virtual hisimvenv
source hisimvenv/bin/activate
``
Alternatively, Anaconda can be used to set up and activate the virtual environment:
``python
conda create -n hisimvenv python=3.8
conda activate hisimvenv
``
With the successful activation, `hisim` is ready to be locally installed.

Install package
------------------------
After setting up the virtual environment, install the package to your local libraries:

``python
python setup.py install
``
