"""Generate the four scenarios of the car double-counting study.

All four are derived from ``system_setups/household_heatpump_car_building_sizer.scenario.json``
so that everything except the car modelling is held fixed. The study is a 2x2:

                                | LPG charging in the household profile | not in it
    --------------------------- + ------------------------------------- + ---------
    HiSim Car/CarBattery/L1     |  S4 lpg_car_and_car_element (shipped)  | S3 car_element_only
    no HiSim car components     |  S2 lpg_car_only                       | S1 no_car

The two axes are set independently:

* **LPG side** -- ``charging_station_set`` decides which LPG load type the refuelling is
  booked to (``tblChargingStationSetEntries.GridChargingLoadtypeID``).  ``Charging At Home
  with 11 kW`` books it to ``Electricity``, which is exactly the profile HiSim reads as
  ``ElectricalPowerConsumption``.  ``Charging At Home with 03.7 kW, output results to Car
  Electricity`` books it to the separate ``Electricity for Car Charging`` load type, which
  HiSim never reads -- so the driving distances and car locations survive but the charging
  energy leaves the household profile.  S1 switches transportation off entirely.
* **HiSim side** -- the ``Car`` / ``CarBattery`` / ``L1Controller`` components plus their EMS
  wiring are present or absent.

Removing the HiSim car components means re-labelling the EMS's dynamic ports, because the
labels carry a running index:

* inputs  -- ``Input_{src_object}_{src_output}_{len(self.inputs)}``   (dynamic_component.py:164)
* outputs -- ``Output{len(self.outputs) + 1}``                        (dynamic_component.py:123)

The shipped file starts its dynamic inputs at ``_2`` and its dynamic outputs at ``Output16``,
i.e. the EMS has 2 static inputs and 15 static outputs. Dropping one dynamic port therefore
shifts every later label down by one, and the ``connections`` entries have to follow.

Run from this directory::

    python make_scenarios.py
"""
# clean
import copy
import json
import os

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                    "household_heatpump_car_building_sizer.scenario.json")
HERE = os.path.dirname(os.path.abspath(__file__))

#: Component classes that make up the HiSim-side car chain.
CAR_CHAIN_CLASSES = {
    "hisim.components.generic_car.Car",
    "hisim.components.advanced_ev_battery_bslib.CarBattery",
    "hisim.components.controller_l1_generic_ev_charge.L1Controller",
}
#: Their instance names in the shipped scenario.
CAR_CHAIN_NAMES = {
    "Car_2_22kW_Charging_Power_avg_Speed_30_kmh_0",
    "CarBattery_1",
    "L1EVChargeControl_1",
}

#: Number of non-dynamic EMS ports, deduced from the labels in the shipped scenario.
EMS_STATIC_INPUTS = 2
EMS_STATIC_OUTPUTS = 15

#: Routes the LPG's home charging into the separate "Electricity for Car Charging" load type.
CHARGING_TO_CAR_ELECTRICITY = {
    "name": "Charging At Home with 03.7 kW, output results to Car Electricity",
    "guid": {"str_val": "223f0577-9249-4293-a849-ea12e2033377"},
}


def _ems(scenario):
    """Return the EMS component definition of *scenario*."""
    return next(c for c in scenario["components"]
                if c["configuration"]["name"] == "L2EMSElectricityController")


def strip_car_chain(scenario):
    """Remove the HiSim car components and re-label the EMS ports they used."""
    scenario["components"] = [c for c in scenario["components"]
                              if c["component_full_classname"] not in CAR_CHAIN_CLASSES]
    ems = _ems(scenario)

    # Inputs: drop the car-battery input, then renumber the survivors in order.
    kept_inputs = [i for i in ems["inputs"] if i["source_object_name"] not in CAR_CHAIN_NAMES]
    input_rename = {}
    for new_index, inp in enumerate(kept_inputs, start=EMS_STATIC_INPUTS):
        stem = f"Input_{inp['source_object_name']}_{inp['source_component_output']}"
        input_rename[stem] = f"{stem}_{new_index}"
    ems["inputs"] = kept_inputs

    # Outputs: drop the L1Controller target, then renumber the survivors in order.
    kept_outputs = [o for o in ems["outputs"] if o.get("source_component_class") != "L1Controller"]
    output_rename = {}
    for new_index, out in enumerate(kept_outputs, start=EMS_STATIC_OUTPUTS + 1):
        output_rename[out["source_output_name"]] = f"{out['source_output_name']}Output{new_index}"
    ems["outputs"] = kept_outputs

    connections = []
    for conn in scenario["connections"]:
        if (conn["source"]["component_name"] in CAR_CHAIN_NAMES
                or conn["target"]["component_name"] in CAR_CHAIN_NAMES):
            continue
        target, source = conn["target"], conn["source"]
        if target["component_name"] == "L2EMSElectricityController" and target["field_name"].startswith("Input_"):
            stem = target["field_name"].rsplit("_", 1)[0]
            target["field_name"] = input_rename[stem]
        if source["component_name"] == "L2EMSElectricityController" and "Output" in source["field_name"]:
            stem = source["field_name"].split("Output")[0]
            if stem in output_rename:
                source["field_name"] = output_rename[stem]
        connections.append(conn)
    scenario["connections"] = connections
    return scenario


def lpg_config(scenario):
    """Return the UtspLpgConnector configuration of *scenario*."""
    return next(c["configuration"] for c in scenario["components"]
                if c["component_full_classname"].endswith("UtspLpgConnector"))


def build(base, name, description, *, transportation, charging_to_car_electricity, car_chain):
    """Assemble one study scenario from *base*."""
    scenario = copy.deepcopy(base)
    scenario["name"] = name
    scenario["description"] = description

    cfg = lpg_config(scenario)
    if not transportation:
        # All three mobility fields must be None for the connector to disable transportation
        # (loadprofilegenerator_utsp_connector.py:1084-1096).
        cfg["travel_route_set"] = None
        cfg["transportation_device_set"] = None
        cfg["charging_station_set"] = None
    elif charging_to_car_electricity:
        cfg["charging_station_set"] = copy.deepcopy(CHARGING_TO_CAR_ELECTRICITY)

    if not car_chain:
        cfg["cars"] = None
        strip_car_chain(scenario)
    return scenario


def main():
    """Write the four study scenarios next to this script."""
    with open(BASE, encoding="utf-8") as handle:
        base = json.load(handle)

    variants = [
        ("s1_no_car",
         "S1 no car",
         "No car at all: LPG transportation disabled, no HiSim car components.",
         dict(transportation=False, charging_to_car_electricity=False, car_chain=False)),
        ("s2_lpg_car_only",
         "S2 LPG car only",
         "The LPG drives and charges an electric car into the household electricity profile; "
         "HiSim has no car components.",
         dict(transportation=True, charging_to_car_electricity=False, car_chain=False)),
        ("s3_car_element_only",
         "S3 car element only",
         "The LPG supplies driving distances and car locations but books the charging to the "
         "separate 'Electricity for Car Charging' load type; HiSim models the car itself.",
         dict(transportation=True, charging_to_car_electricity=True, car_chain=True)),
        ("s4_lpg_car_and_car_element",
         "S4 LPG car and car element",
         "The shipped configuration: the LPG charges the car into the household profile AND "
         "HiSim models the same car again.",
         dict(transportation=True, charging_to_car_electricity=False, car_chain=True)),
    ]

    for stem, name, description, kwargs in variants:
        scenario = build(base, name, description, **kwargs)
        path = os.path.join(HERE, f"{stem}.scenario.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(scenario, handle, indent=4)
            handle.write("\n")
        print(f"wrote {os.path.basename(path)}: "
              f"{len(scenario['components'])} components, {len(scenario['connections'])} connections")


if __name__ == "__main__":
    main()
