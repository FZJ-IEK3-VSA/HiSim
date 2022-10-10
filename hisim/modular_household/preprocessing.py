import json
import hisim.log
import scipy.interpolate

def calculate_pv_investment_cost(economic_parameters, pv_included, pv_peak_power):
    """PV"""
    if economic_parameters["pv_bought"]:
        if not pv_included:
            hisim.log.information("Error: PV bought but not included")
        else:
            ccpv = json.load(open('..\hisim\modular_household\ComponentCostPV.json'))       
            pv_cost = scipy.interpolate.interp1d(ccpv["capacity_for_cost"], ccpv["cost_per_capacity"])
            pv_cost = pv_cost(pv_peak_power)
    else:
            pv_cost = 0
    return pv_cost


def calculate_smart_devices_investment_cost(economic_parameters, smart_devices_included):
    """SMART DEVICES"""
    if economic_parameters["smart_devices_bought"]:
        if not smart_devices_included:
            hisim.log.information("Error: Smart Devices bought but not included")
        else:
            ccsd = json.load(open('..\hisim\modular_household\ComponentCostSmartDevice.json'))
            smart_devices_cost = ccsd["smart_devices_cost"]
    else:
        smart_devices_cost = 0
    return smart_devices_cost


def calculate_surplus_controller_investment_cost(economic_parameters):
    """SURPLUS CONTROLLER"""
    if economic_parameters["surpluscontroller_bought"]:
            ccsc = json.load(open('..\hisim\modular_household\ComponentCostSurplusController.json'))
            surplus_controller_cost = ccsc["surplus_controller_cost"]
    else:
        surplus_controller_cost=0
    return surplus_controller_cost


def calculate_heating_investment_cost(economic_parameters, heatpump_included, heater_capacity):
    """HEATING"""
    if economic_parameters["heatpump_bought"]:
        if not heatpump_included:
            hisim.log.information("Error: heatpump bought but not included")
        else:
            cchp = json.load(open('..\hisim\modular_household\ComponentCostHeatPump.json'))
            heatpump_cost_interp = scipy.interpolate.interp1d(cchp["capacity_for_cost"], cchp["cost_per_capacity"])
            heatpump_cost = heatpump_cost_interp(heater_capacity)
    else:
            heatpump_cost = 0
    return heatpump_cost


def calculate_battery_investment_cost(economic_parameters, battery_included, battery_capacity):
    """BATTERY"""
    if economic_parameters["battery_bought"]:
        if not battery_included:
            hisim.log.information("Error: battery bought but not included")
        else:
            ccb = json.load(open('..\hisim\modular_household\ComponentCostBattery.json'))
            battery_cost_interp = scipy.interpolate.interp1d(ccb["capacity_cost"], ccb["cost"])
            battery_cost=battery_cost_interp(battery_capacity)
    else:
            battery_cost = 0
    return battery_cost


def calculate_chp_investment_cost(economic_parameters, chp_included, chp_power):
    """CHP + H2 STORAGE + ELECTROLYSIS"""
    if economic_parameters["chp_bought"]:
        if not chp_included:
            hisim.log.information("Error: chp bought but not included")
        else:
            ccchp = json.load(open('..\hisim\modular_household\ComponentCostCHP.json'))
            chp_cost_interp = scipy.interpolate.interp1d(ccchp["capacity_for_cost"], ccchp["cost_per_capacity"])
            chp_cost = chp_cost_interp(chp_power)
    else:
        chp_cost = 0      
    return chp_cost


def calculate_electrolyzer_investment_cost(economic_parameters, electrolyzer_included, electrolyzer_power):
    """Electrolyzer"""       
    if economic_parameters["electrolyzer_bought"]:
        if not electrolyzer_included:
            hisim.log.information("Error: electrolyzer bought but not included")
        else:
            ccel = json.load(open('..\hisim\modular_household\ComponentCostElectrolyzer.json'))
            electrolyzer_cost_interp = scipy.interpolate.interp1d(ccel["capacity_for_cost"], ccel["cost_per_capacity"])
            electrolyzer_cost = electrolyzer_cost_interp(electrolyzer_power)
    else:
        electrolyzer_cost=0
    return electrolyzer_cost


def calculate_h2storage_investment_cost(economic_parameters, h2system_included, h2_storage_size):
    """H2System"""       
    if economic_parameters["h2system_bought"]:
        if not h2system_included:
            hisim.log.information("Error: h2system bought but not included")
        else:
            cch2 = json.load(open('..\hisim\modular_household\ComponentCostH2Storage.json'))
            h2_storage_cost_interp = scipy.interpolate.interp1d(cch2["capacity_for_cost"], cch2["cost_per_capacity"])
            h2_storage_cost = h2_storage_cost_interp(h2_storage_size)
    else:
        h2_storage_cost = 0
    return h2_storage_cost


def calculate_electric_vehicle_investment_cost(economic_parameters, ev_included, ev_capacity):
    """ELECTRIC VEHICLE"""
    if economic_parameters["ev_bought"]:
        if not ev_included:
            hisim.log.information("Error: EV bought but not included")
        else:
            ccev = json.load(open('..\hisim\modular_household\ComponentCostElectricVehicle.json'))
            ev_cost_interp = scipy.interpolate.interp1d(ccev["capacity_for_cost"], ccev["cost_per_capacity"])
            ev_cost=ev_cost_interp(ev_capacity)
    else:
        ev_cost = 0
    return ev_cost


def calculate_buffer_investment_cost(economic_parameters, buffer_included, buffer_volume):  
    """BUFFER""" 
    if economic_parameters["buffer_bought"]:
        if not buffer_included:
            hisim.log.information("Error: Buffer bought but not included")
        else:
            ccbu = json.load(open('..\hisim\modular_household\ComponentCostBuffer.json'))
            buffer_cost_interp = scipy.interpolate.interp1d(ccbu["capacity_for_cost"], ccbu["cost_per_capacity"])
            buffer_cost=buffer_cost_interp(buffer_volume)
    else:
        buffer_capacity = 0
    return buffer_cost

def total_investment_cost_threshold_exceedance_check(economic_parameters, pv_cost, smart_devices_cost, battery_cost, surplus_controller_cost, heatpump_cost, buffer_cost, chp_cost, h2_storage_cost, electrolyzer_cost, ev_cost):
    total_threshold = economic_parameters["pv_threshold"] + economic_parameters["smart_devices_threshold"] + economic_parameters["heatpump_threshold"] +\
        economic_parameters["battery_threshold"] + economic_parameters["buffer_threshold"] + economic_parameters["h2system_threshold"] + economic_parameters["chp_threshold"] +\
        economic_parameters["electrolyzer_threshold"] + economic_parameters["surpluscontroller_threshold"] + economic_parameters["ev_threshold"]
    print("total threshold:",total_threshold)

    investment_cost = pv_cost + smart_devices_cost + heatpump_cost + battery_cost + buffer_cost\
        + chp_cost + h2_storage_cost + electrolyzer_cost + ev_cost + surplus_controller_cost
        
    if investment_cost > total_threshold:
        hisim.log.information("Error: Total investment cost exceeded the total threshold")
    else:
        hisim.log.information("Everything alright: Total investment cost is below total threshold")
    return investment_cost

def investment_cost_per_component_exceedance_check(economic_parameters, pv_cost, smart_devices_cost, battery_cost, surplus_controller_cost, heatpump_cost, buffer_cost, chp_cost, h2_storage_cost, electrolyzer_cost, ev_cost):
    if pv_cost > economic_parameters["pv_threshold"]:
        hisim.log.information("Problem: PV investment cost threshold exceeded.")
    elif smart_devices_cost > economic_parameters["smart_devices_threshold"]:
        hisim.log.information("Problem: Smart devices investment cost threshold exceeded.")
    elif heatpump_cost > economic_parameters["heatpump_threshold"]:
        hisim.log.information("Problem: Heatpump investment cost threshold exceeded.")
    elif battery_cost > economic_parameters["battery_threshold"]:
        hisim.log.information("Problem: Battery investment cost threshold exceeded.")
    elif buffer_cost > economic_parameters["buffer_threshold"]:
        hisim.log.information("Problem: Buffer investment cost threshold exceeded.")
    elif chp_cost > economic_parameters["chp_threshold"]:
        hisim.log.information("Problem: CHP investment cost threshold exceeded.")                                        
    elif h2_storage_cost > economic_parameters["h2storage_threshold"]:
        hisim.log.information("Problem: H2Storage investment cost threshold exceeded.")
    elif electrolyzer_cost > economic_parameters["electrolyzer_threshold"]:
        hisim.log.information("Problem: Electrolyzer investment cost threshold exceeded.")
    elif ev_cost > economic_parameters["ev_threshold"]:
        hisim.log.information("Problem: EV investment cost threshold exceeded.")
    elif surplus_controller_cost > economic_parameters["surplus_controller_threshold"]:
        hisim.log.information("Problem: Surplus controller investment cost threshold exceeded.")
    else: 
        hisim.log.information("All components do not exceed their treshold.")