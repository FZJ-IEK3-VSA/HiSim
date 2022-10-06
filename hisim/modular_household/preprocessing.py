import json
import hisim.log
import scipy.interpolate

def calculate_pv_investment_cost(economic_parameters, pv_peak_power):
    """PV"""
    if economic_parameters["pv_bought"]:
            ccpv = json.load(open('..\hisim\modular_household\ComponentCostPV.json'))       
            pv_cost = scipy.interpolate.interp1d(ccpv["capacity_for_cost"], ccpv["cost_per_capacity"])
            pv_cost = pv_cost(pv_peak_power)
    else:
            pv_cost = 0
    return pv_cost

def calculate_smart_devices_investment_cost(economic_parameters):
    """SMART DEVICES"""
    if economic_parameters["smart_devices_bought"]:
        ccsd = json.load(open('..\hisim\modular_household\ComponentCostSmartDevice.json'))
        smart_devices_cost = ccsd["smart_devices_cost"]
    else:
        smart_devices_cost = 0
    return smart_devices_cost

def calculate_surplus_controller_investment_cost(chp_included, battery_included, smart_devices_included, ev_included, heatpump_included):
    """SURPLUS CONTROLLER"""
    if chp_included or battery_included or smart_devices_included or ev_included or heatpump_included:
        ccsc = json.load(open('..\hisim\modular_household\ComponentCostSurplusController.json'))
        surplus_controller_cost = ccsc["surplus_controller_cost"]
    else:
        surplus_controller_cost=0    
    return surplus_controller_cost

"""WATERHEATING"""
def calculate_heating_investment_cost(economic_parameters, heater_capacity):
    """HEATING"""
    cchp = json.load(open('..\hisim\modular_household\ComponentCostHeatPump.json'))

    if economic_parameters["heatpump_bought"]:
            heatpump_cost_interp = scipy.interpolate.interp1d(cchp["capacity_cost"], cchp["cost"])
            heatpump_cost = heatpump_cost_interp(heater_capacity)
    else:
            heatpump_cost = 0
    return heatpump_cost

def calculate_battery_investment_cost(economic_parameters, battery_capacity):
    """BATTERY"""
    #EconomicParameters.battery_bought abfragen, ob Batterie bereits vorhanden ist
    ccb = json.load(open('..\hisim\modular_household\ComponentCostBattery.json'))
    if economic_parameters["battery_bought"]:
            battery_cost_interp = scipy.interpolate.interp1d(ccb["capacity_cost"], ccb["cost"])
            battery_cost=battery_cost_interp(battery_capacity)
    else:
            battery_cost = 0
    return battery_cost

"""CHP + H2 STORAGE + ELECTROLYSIS"""
def calculate_chp_investment_cost(economic_parameters, chp_included, chp_power, h2_storage_size, electrolyzer_power):
    if economic_parameters["h2system_bought"]:
        if not chp_included:
            print("Error: h2system bought but chp not included")
        ccchp = json.load(open('..\hisim\modular_household\ComponentCostCHP.json'))
        chp_cost_interp = scipy.interpolate.interp1d(ccchp["capacity_for_cost"], ccchp["cost_per_capacity"])
        chp_cost = chp_cost_interp(chp_power)
    
        cch2 = json.load(open('..\hisim\modular_household\ComponentCostH2Storage.json'))
        h2_storage_cost_interp = scipy.interpolate.interp1d(cch2["capacity_for_cost"], cch2["cost_per_capacity"])
        h2_storage_cost = h2_storage_cost_interp(h2_storage_size)
    
        ccel = json.load(open('..\hisim\modular_household\ComponentCostElectrolyzer.json'))
        electrolyzer_cost_interp = scipy.interpolate.interp1d(ccel["capacity_for_cost"], ccel["cost_per_capacity"])
        electrolyzer_cost = electrolyzer_cost_interp(electrolyzer_power)
    else:
        chp_cost = 0
        h2_storage_cost=0
        electrolyzer_cost=0
    return chp_cost, h2_storage_cost, electrolyzer_cost
    
    """ELECTRIC VEHICLE"""
def calculate_electric_vehicle_investment_cost(economic_parameters, ev_capacity):        
    if economic_parameters["ev_bought"]:
        ccev = json.load(open('..\hisim\modular_household\ComponentCostElectricVehicle.json'))
        ev_cost_interp = scipy.interpolate.interp1d(ccev["capacity_for_cost"], ccev["cost_per_capacity"])
        ev_cost=ev_cost_interp(ev_capacity)
    else:
        ev_cost = 0
    return ev_cost

    """BUFFER""" 
def calculate_buffer_investment_cost(economic_parameters, buffer_volume):       
    if economic_parameters["buffer_bought"]:
        ccbu = json.load(open('..\hisim\modular_household\ComponentCostBuffer.json'))
        buffer_cost_interp = scipy.interpolate.interp1d(ccbu["capacity_for_cost"], ccbu["cost_per_capacity"])
        buffer_cost=buffer_cost_interp(buffer_volume)
    else:
        buffer_cost = 0
    return buffer_cost

def total_investment_cost_treshold_exceedance_check(pv_cost, smart_devices_cost, battery_cost, surplus_controller_cost, heatpump_cost, buffer_cost, chp_cost, h2_storage_cost, electrolyzer_cost, ev_cost):
    investment_cost = pv_cost + smart_devices_cost + heatpump_cost + battery_cost + buffer_cost + chp_cost
    + h2_storage_cost + electrolyzer_cost + ev_cost + surplus_controller_cost

def investment_cost_per_component_exceedance_check(economic_parameters, pv_cost, smart_devices_cost, battery_cost, surplus_controller_cost, heatpump_cost, buffer_cost, chp_cost, h2_storage_cost, electrolyzer_cost, ev_cost):
    if pv_cost > economic_parameters["pv_treshold"]:
        hisim.log.information("PV investment cost treshold exceeded.")
    elif smart_devices_cost > economic_parameters["smart_devices_treshold"]:
        hisim.log.information("Smart devices investment cost treshold exceeded.")
    elif heatpump_cost > economic_parameters["heatpump_treshold"]:
        hisim.log.information("Heatpump investment cost treshold exceeded.")
    elif battery_cost > economic_parameters["battery_treshold"]:
        hisim.log.information("Battery investment cost treshold exceeded.")
    elif buffer_cost > economic_parameters["buffer_treshold"]:
        hisim.log.information("Buffer investment cost treshold exceeded.")
    elif chp_cost > economic_parameters["chp_treshold"]:
        hisim.log.information("CHP investment cost treshold exceeded.")                                        
    elif h2_storage_cost > economic_parameters["h2storage_treshold"]:
        hisim.log.information("H2Storage investment cost treshold exceeded.")
    elif electrolyzer_cost > economic_parameters["electrolyzer_treshold"]:
        hisim.log.information("Electrolyzer investment cost treshold exceeded.")
    elif ev_cost > economic_parameters["ev_treshold"]:
        hisim.log.information("EV investment cost treshold exceeded.")
    elif surplus_controller_cost > economic_parameters["surplus_controller_treshold"]:
        hisim.log.information("Surplus controller investment cost treshold exceeded.")
    