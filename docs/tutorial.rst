.. _tutorial:

Run Examples
================================================

Run Simple Examples
-----------------------
Run the python interpreter in the `hisim/examples` directory with the following command:

``python
python ../hisim/hisim.py examples first_example
``

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

Run Modular Household Example
-----------------------
The directory `hisim\examples` also contains a modular example in the script `modular_example.py`. The first setup function (`modular_household_explicit`) can be executed with the following command:

```python
python ../hisim/hisim.py modular_example modular_household_explicit
```

The example automatically builds and connects all components, which are desired. The components are set up according the system configuration, which is built upon the specifications provided by a json file 'modular_example_config.json' located in the `examples` directory. Please, check the :ref:`modularexampleinterfaces` explanation to completely understand the json interface.

```json
{"system_config_": {"pv_included": true, "pv_peak_power": 10000.0, "smart_devices_included": false, "buffer_included": true, "buffer_volume": 2.6, "battery_included": false, "battery_capacity": 20.0, "heatpump_included": true, "heatpump_power": 1.0, "chp_included": false, "chp_power": 12, "h2_storage_included": true, "h2_storage_size": 100, "electrolyzer_included": true, "electrolyzer_power": 5000.0, "ev_included": false, "charging_station": {"Name": "Charging At Home with 03.7 kW", "Guid": {"StrVal": "38e3a15d-d6f5-4f51-a16a-da287d14608f"}}, "utsp_connect": true, "url": "http://134.94.131.167:443/api/v1/profilerequest", "api_key": "limited_OXT60O84N9ITLO1CM9CJ1V393QFKOKCN"}, "archetype_config_": {"location": "Aachen", "occupancy_profile": {"Name": "CHR01 Couple both at Work", "Guid": {"StrVal": "516a33ab-79e1-4221-853b-967fc11cc85a"}}, "building_code": "DE.N.SFH.05.Gen.ReEx.001.002", "absolute_conditioned_floor_area": 121.2, "water_heating_system_installed": "DistrictHeating", "heating_system_installed": "DistrictHeating", "mobility_set": null, "mobility_distance": null}}
```