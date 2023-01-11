
 <a href="https://www.fz-juelich.de/en/iek/iek-3"><img src="https://www.fz-juelich.de/static/media/Logo.2ceb35fc.svg" alt="Forschungszentrum Juelich Logo" width="230px"></a> 

# HiSim - Household Infrastructure and Building Simulator
HiSim is a Python package for simulation and analysis of household scenarios and building systems using modern components as alternative to fossil fuel based ones. This package integrates load profiles generation of electricity consumption, heating demand, electricity generation, and strategies of smart strategies of modern components, such as heat pump, battery, electric vehicle or thermal energy storage. 

HiSim is a package under development by Forschungszentrum Jülich. For detailed documentation, please access [ReadTheDocs](https://household-infrastructure-simulator.readthedocs.io/en/latest/) or the [latest version](https://github.com/FZJ-IEK3-VSA/HiSim) of this this repository.

## General information
This very early version of HiSim was used in the project [PIEG-Strom](https://zdin.de/digitales-niedersachsen/projektubersicht/pieg-strom) to simulate results for the [VDI 4657-3](https://www.vdi.de/richtlinien/details/vdi-4657-blatt-3-planung-und-integration-von-energiespeichern-in-gebaeudeenergiesystemen-elektrische-stromspeicher-ess) guideline. The authors want to thank .....

## Usage
### Clone repository
To clone this repository, enter the following command to your terminal:

```python
git clone https://github.com/FZJ-IEK3-VSA/HiSim.git
```

### Virtual Environment
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
conda create -n hisimvenv python=3.9
conda activate hisimvenv
```
With the successful activation, `hisim` is ready to be locally installed.

### Install package
After setting up the virtual environment, install the package to your local libraries:

```python
pip install -e .
```
## Run simulations files from PIEG-Strom project
Run the python interpreter in the `hisim/examples` directory with the following command:

```python
python vdi4657_chapter_9-2-3-1.py
```

The results are stored under directory `hisim/examples/`.

## License
MIT License

Copyright (C) 2020-2021 Noah Pflugradt, Vitor Zago, Frank Burkard, Tjarko Tjaden, Leander Kotzur, Detlef Stolten

You should have received a copy of the MIT License along with this program.
If not, see https://opensource.org/licenses/MIT

## About Us
We are the [Institute of Energy and Climate Research - Techno-economic Systems Analysis (IEK-3)](https://www.fz-juelich.de/iek/iek-3/DE/Home/home_node.html) belonging to the [Forschungszentrum Jülich](www.fz-juelich.de/). Our interdisciplinary institute's research is focusing on energy-related process and systems analyses. Data searches and system simulations are used to determine energy and mass balances, as well as to evaluate performance, emissions and costs of energy systems. The results are used for performing comparative assessment studies between the various systems. Our current priorities include the development of energy strategies, in accordance with the German Federal Government’s greenhouse gas reduction targets, by designing new infrastructures for sustainable and secure energy supply chains and by conducting cost analysis studies for integrating new technologies into future energy market frameworks.

## Acknowledgement
This work was supported by the Helmholtz Association under the Joint Initiative ["Energy System 2050 - A Contribution of the Research Field Energy"](https://www.helmholtz.de/en/research/energy/energy_system_2050/).

<a href="https://www.helmholtz.de/en/"><img src="https://www.helmholtz.de/fileadmin/user_upload/05_aktuelles/Marke_Design/logos/HG_LOGO_S_ENG_RGB.jpg" alt="Helmholtz Logo" width="200px"></a>

The project PIEG-Strom project is supported by "WIPANO - knowledge and technology transfer through patents and standards" with funding from the Federal Ministry for Economic Affairs and Energy (BMWi) (FKZ: 03TN0004). The authors would like to thank Projektträger Jülich (PTJ) and BMWi for their support.

<a href="https://www.bmwk.de/Navigation/DE/Home/home.html"><img src="https://upload.wikimedia.org/wikipedia/commons/4/44/Gefoerdert_LOGO_BMWI.jpg" alt="BMWi Logo" width="200px"></a>

For this work weather data is based on data from ["German Weather Service (Deutscher Wetterdienst-DWD)"](https://www.dwd.de/DE/Home/home_node.html/)

<a href="https://www.dwd.de/"><img src="https://www.dwd.de/SharedDocs/bilder/DE/logos/dwd/dwd_logo_258x69.png?__blob=normal&v=1" alt="DWD Logo" width="200px"></a>

