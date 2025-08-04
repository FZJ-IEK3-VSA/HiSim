"""Configuration module."""

# clean

from typing import Any, Optional
from dataclasses import dataclass, field
from dataclasses_json import dataclass_json
from hisim.loadtypes import LoadTypes, ComponentType, Units
from hisim.component import ConfigBase
from hisim import log

"""
Sources for opex techno-economic parameters:
Sources for opex techno-economic parameters:
        [1]: https://de.statista.com/statistik/daten/studie/914784/umfrage/entwicklung-der-strompreise-in-deutschland-verivox-verbraucherpreisindex/
        [2]: https://echtsolar.de/einspeiseverguetung/  (average of monthly injection revenue)
        [3]: https://de.statista.com/statistik/daten/studie/779/umfrage/durchschnittspreis-fuer-dieselkraftstoff-seit-dem-jahr-1950/
        [4]: https://de.statista.com/statistik/daten/studie/168286/umfrage/entwicklung-der-gaspreise-fuer-haushaltskunden-seit-2006/
        [5]: https://de.statista.com/statistik/daten/studie/38897/umfrage/co2-emissionsfaktor-fuer-den-strommix-in-deutschland-seit-1990/
        [6]: https://www.destatis.de/DE/Themen/Wirtschaft/Preise/Erdgas-Strom-DurchschnittsPreise/_inhalt.html#421258
        [7]: https://de.statista.com/statistik/daten/studie/250114/umfrage/preis-fuer-fernwaerme-nach-anschlusswert-in-deutschland/
        [8]: https://www.google.com/url?sa=t&source=web&rct=j&opi=89978449&url=https://www.bafa.de/SharedDocs/Downloads/DE/Energie/
             eew_infoblatt_co2_faktoren_2022.pdf%3F__blob%3DpublicationFile%26v%3D6&ved=2ahUKEwjai6GzsP2MAxW6RPEDHbV2G-cQFnoECFkQAQ&usg=AOvVaw1U3FERIjm5HLPDAuuO5ig0
        [9]: https://www.umweltbundesamt.de/themen/co2-emissionen-pro-kilowattstunde-strom-2024
        [10]: https://www-genesis.destatis.de/datenbank/online/statistic/61243/table/61243-0002
        [11]: https://www.bafa.de/SharedDocs/Downloads/DE/Energie/eew_infoblatt_co2_faktoren_2025.pdf?__blob=publicationFile&v=3
        [12]: https://de.statista.com/statistik/daten/studie/2633/umfrage/entwicklung-des-verbraucherpreises-fuer-leichtes-heizoel-seit-1960/
        [13]: https://de.statista.com/statistik/daten/studie/214738/umfrage/preisentwicklung-fuer-holzpellets-in-deutschland/
        [14]: https://www.umweltbundesamt.de/sites/default/files/medien/479/publikationen/
              factsheet_ansatz_zur_neubewertung_von_co2-emissionen_aus_der_holzverbrennung_0.pdf
        [15]: https://mediathek.fnr.de/energiepreisentwicklung.html
        [16]: https://de.statista.com/statistik/daten/studie/250114/umfrage/preis-fuer-fernwaerme-nach-anschlusswert-in-deutschland/
        [17]: https://doi.org/10.1016/j.enbuild.2022.112480 (Knosala et al. 2022)
"""
opex_techno_economic_parameters = {
    2018: {
        "electricity_costs_in_euro_per_kwh": 0.27825,  # EUR/kWh  # Source: [1]
        "electricity_footprint_in_kg_per_kwh": 0.473,  # kgCO2eq/kWh  # Source: [5]
        "electricity_to_grid_revenue_in_euro_per_kwh": 0.1205,  # EUR/kWh  # Source: [2]
        "contracting_heating_costs_hot_water_in_euro_per_kwh": 0.0033,  # EUR/kWh
        "contracting_heating_footprint_hot_water_in_kg_per_kwh": 0.02,  # kgCO2eq/kWh
        "contracting_heating_costs_cold_water_in_euro_per_kwh": 0,
        "contracting_heating_footprint_cold_water_in_kg_per_kwh": 0,
        "gas_costs_in_euro_per_kwh": 0.0664,  # EUR/kWh  # Source: [4]
        "gas_footprint_in_kg_per_kwh": 0.24,  # kgCO2eq/kWh
        "oil_costs_in_euro_per_l": 1.159835766,  # EUR/l
        "oil_footprint_in_kg_per_l": 3.2,  # kgCO2eq/l
        "diesel_costs_in_euro_per_l": 128.90,  # EUR/l  # Source: [3]
        "diesel_footprint_in_kg_per_l": 2.0,  # kgCO2eq/l
        "pellet_costs_in_euro_per_t": 247,  # EUR/t # Source: [13]
        "pellet_footprint_in_kg_per_kwh": 0.036,  # kgCo2eq/kWh # Source: [8]
        "wood_chip_costs_in_euro_per_t": 96,  # EUR/t Source: [15] using value for 2024 as almost constant over past years
        "wood_chip_footprint_in_kg_per_kwh": 0.0313,  # kgCo2eq/kWh # Source : [14]
        "district_heating_costs_in_euro_per_kwh": 0.07672,  # EUR/kWh Source : [16]
        "district_heating_footprint_in_kg_per_kwh": 0.280,  # kgCo2eq/kWh Source : [8]
        "green_hydrogen_gas_costs_in_euro_per_kwh": 0.25,  # EUR/kWh  # Source: [17] using value of 2020 and assuming it is almost constant for several years
        "green_hydrogen_gas_footprint_in_kg_per_kwh": 0,  # kgCO2eq/kWh
    },
    2019: {
        "electricity_costs_in_euro_per_kwh": 0.295,  # EUR/kWh  # Source: [1]
        "electricity_footprint_in_kg_per_kwh": 0.411,  # kgCO2eq/kWh  # Source: [5]
        "electricity_to_grid_revenue_in_euro_per_kwh": 0.1072,  # EUR/kWh  # Source: [2]
        "contracting_heating_costs_hot_water_in_euro_per_kwh": 0.0033,  # EUR/kWh
        "contracting_heating_footprint_hot_water_in_kg_per_kwh": 0.02,  # kgCO2eq/kWh
        "contracting_heating_costs_cold_water_in_euro_per_kwh": 0,
        "contracting_heating_footprint_cold_water_in_kg_per_kwh": 0,
        "gas_costs_in_euro_per_kwh": 0.0728,  # EUR/kWh  # Source: [4]
        "gas_footprint_in_kg_per_kwh": 0.24,  # kgCO2eq/kWh
        "oil_costs_in_euro_per_l": 1.159835766,  # EUR/l
        "oil_footprint_in_kg_per_l": 3.2,  # kgCO2eq/l
        "diesel_costs_in_euro_per_l": 1.2670,  # EUR/l  # Source: [3]
        "diesel_footprint_in_kg_per_l": 2.0,  # kgCO2eq/l
        "pellet_costs_in_euro_per_t": 251,  # EUR/t # Source: [13]
        "pellet_footprint_in_kg_per_kwh": 0.036,  # kgCo2eq/kWh # Source: [8]
        "wood_chip_costs_in_euro_per_t": 96,  # EUR/t Source: [15] using value for 2024 as almost constant over past years
        "wood_chip_footprint_in_kg_per_kwh": 0.0313,  # kgCo2eq/kWh # Source : [14]
        "district_heating_costs_in_euro_per_kwh": 0.07904,  # EUR/kWh Source : [16]
        "district_heating_footprint_in_kg_per_kwh": 0.280,  # kgCo2eq/kWh Source : [8]
        "green_hydrogen_gas_costs_in_euro_per_kwh": 0.25,  # EUR/kWh  # Source: [17] using value of 2020 and assuming it is almost constant for several years
        "green_hydrogen_gas_footprint_in_kg_per_kwh": 0,  # kgCO2eq/kWh
    },
    2020: {
        "electricity_costs_in_euro_per_kwh": 0.3005,  # EUR/kWh  # Source: [1]
        "electricity_footprint_in_kg_per_kwh": 0.369,  # kgCO2eq/kWh  # Source: [5]
        "electricity_to_grid_revenue_in_euro_per_kwh": 0.0838,  # EUR/kWh  # Source: [2]
        "contracting_heating_costs_hot_water_in_euro_per_kwh": 0.0033,  # EUR/kWh
        "contracting_heating_footprint_hot_water_in_kg_per_kwh": 0.02,  # kgCO2eq/kWh
        "contracting_heating_costs_cold_water_in_euro_per_kwh": 0,
        "contracting_heating_footprint_cold_water_in_kg_per_kwh": 0,
        "gas_costs_in_euro_per_kwh": 0.0699,  # EUR/kWh  # Source: [4]
        "gas_footprint_in_kg_per_kwh": 0.24,  # kgCO2eq/kWh
        "oil_costs_in_euro_per_l": 1.159835766,  # EUR/l
        "oil_footprint_in_kg_per_l": 3.2,  # kgCO2eq/l
        "diesel_costs_in_euro_per_l": 1.1240,  # EUR/l  # Source: [3]
        "diesel_footprint_in_kg_per_l": 2.0,  # kgCO2eq/l
        "pellet_costs_in_euro_per_t": 237,  # EUR/t # Source: [13]
        "pellet_footprint_in_kg_per_kwh": 0.036,  # kgCo2eq/kWh # Source: [8]
        "wood_chip_costs_in_euro_per_t": 96,  # EUR/t Source: [15] using value for 2024 as almost constant over past years
        "wood_chip_footprint_in_kg_per_kwh": 0.0313,  # kgCo2eq/kWh # Source : [14]
        "district_heating_costs_in_euro_per_kwh": 0.07656,  # EUR/kWh Source : [16]
        "district_heating_footprint_in_kg_per_kwh": 0.280,  # kgCo2eq/kWh Source : [8]
        "green_hydrogen_gas_costs_in_euro_per_kwh": 0.25,  # EUR/kWh  # Source: [17] using value of 2020 and assuming it is almost constant for several years
        "green_hydrogen_gas_footprint_in_kg_per_kwh": 0,  # kgCO2eq/kWh
    },
    2021: {
        "electricity_costs_in_euro_per_kwh": 0.3005,  # EUR/kWh  # Source: [1]
        "electricity_footprint_in_kg_per_kwh": 0.410,  # kgCO2eq/kWh  # Source: [5]
        "electricity_to_grid_revenue_in_euro_per_kwh": 0.0753,  # EUR/kWh  # Source: [2]
        "contracting_heating_costs_hot_water_in_euro_per_kwh": 0.0033,  # EUR/kWh
        "contracting_heating_footprint_hot_water_in_kg_per_kwh": 0.02,  # kgCO2eq/kWh
        "contracting_heating_costs_cold_water_in_euro_per_kwh": 0,
        "contracting_heating_footprint_cold_water_in_kg_per_kwh": 0,
        "gas_costs_in_euro_per_kwh": 0.0745,  # EUR/kWh  # Source: [4]
        "gas_footprint_in_kg_per_kwh": 0.24,  # kgCO2eq/kWh
        "oil_costs_in_euro_per_l": 1.159835766,  # EUR/l
        "oil_footprint_in_kg_per_l": 3.2,  # kgCO2eq/l
        "diesel_costs_in_euro_per_l": 1.399,  # EUR/l  # Source: [3]
        "diesel_footprint_in_kg_per_l": 2.0,  # kgCO2eq/l
        "pellet_costs_in_euro_per_t": 241,  # EUR/t # Source: [13]
        "pellet_footprint_in_kg_per_kwh": 0.036,  # kgCo2eq/kWh # Source: [8]
        "wood_chip_costs_in_euro_per_t": 96,  # EUR/t Source: [15] using value for 2024 as almost constant over past years
        "wood_chip_footprint_in_kg_per_kwh": 0.0313,  # kgCo2eq/kWh # Source : [14]
        "district_heating_costs_in_euro_per_kwh": 0.08277,  # EUR/kWh Source : [16]
        "district_heating_footprint_in_kg_per_kwh": 0.280,  # kgCo2eq/kWh Source : [8]
        "green_hydrogen_gas_costs_in_euro_per_kwh": 0.25,  # EUR/kWh  # Source: [17] using value of 2020 and assuming it is almost constant for several years
        "green_hydrogen_gas_footprint_in_kg_per_kwh": 0,  # kgCO2eq/kWh
    },
    2022: {
        "electricity_costs_in_euro_per_kwh": 0.43025,  # EUR/kWh  # Source: [1]
        "electricity_footprint_in_kg_per_kwh": 0.434,  # kgCO2eq/kWh  # Source: [5]
        "electricity_to_grid_revenue_in_euro_per_kwh": 0.0723,  # EUR/kWh  # Source: [2]
        "contracting_heating_costs_hot_water_in_euro_per_kwh": 0.0033,  # EUR/kWh
        "contracting_heating_footprint_hot_water_in_kg_per_kwh": 0.02,  # kgCO2eq/kWh
        "contracting_heating_costs_cold_water_in_euro_per_kwh": 0,
        "contracting_heating_footprint_cold_water_in_kg_per_kwh": 0,
        "gas_costs_in_euro_per_kwh": 0.0951,  # EUR/kWh  # Source: [4]
        "gas_footprint_in_kg_per_kwh": 0.24,  # kgCO2eq/kWh
        "oil_costs_in_euro_per_l": 1.159835766,  # EUR/l
        "oil_footprint_in_kg_per_l": 3.2,  # kgCO2eq/l
        "diesel_costs_in_euro_per_l": 1.96,  # EUR/l  # Source: [3]
        "diesel_footprint_in_kg_per_l": 2.0,  # kgCO2eq/l
        "pellet_costs_in_euro_per_t": 519,  # EUR/t # Source: [13]
        "pellet_footprint_in_kg_per_kwh": 0.036,  # kgCo2eq/kWh # Source: [8]
        "wood_chip_costs_in_euro_per_t": 96,  # EUR/t Source: [15] using value for 2024 as almost constant over past years
        "wood_chip_footprint_in_kg_per_kwh": 0.0313,  # kgCo2eq/kWh # Source : [14]
        "district_heating_costs_in_euro_per_kwh": 0.11945,  # EUR/kWh Source : [16]
        "district_heating_footprint_in_kg_per_kwh": 0.280,  # kgCo2eq/kWh Source : [8]
        "green_hydrogen_gas_costs_in_euro_per_kwh": 0.25,  # EUR/kWh  # Source: [17] using value of 2020 and assuming it is almost constant for several years
        "green_hydrogen_gas_footprint_in_kg_per_kwh": 0,  # kgCO2eq/kWh
    },
    2023: {
        "electricity_costs_in_euro_per_kwh": 0.4175,  # EUR/kWh  # Source: [6]
        "electricity_footprint_in_kg_per_kwh": 0.380,  # kgCO2eq/kWh  # Source: [5]
        "electricity_to_grid_revenue_in_euro_per_kwh": 0.0733,  # EUR/kWh  # Source: [2]
        "contracting_heating_costs_hot_water_in_euro_per_kwh": 0.147,  # EUR/kWh  # Source: [7]
        "contracting_heating_footprint_hot_water_in_kg_per_kwh": 0.1823,  # kgCO2eq/kWh
        "contracting_heating_costs_cold_water_in_euro_per_kwh": 0,
        "contracting_heating_footprint_cold_water_in_kg_per_kwh": 0,
        "gas_costs_in_euro_per_kwh": 0.1141,  # EUR/kWh  # Source: [6]
        "gas_footprint_in_kg_per_kwh": 0.247,  # kgCO2eq/kWh
        "oil_costs_in_euro_per_l": 1.159835766,  # EUR/l
        "oil_footprint_in_kg_per_l": 3.2,  # kgCO2eq/l
        "diesel_costs_in_euro_per_l": 1.73,  # EUR/l  # Source: [3]
        "diesel_footprint_in_kg_per_l": 2.0,  # kgCO2eq/l
        "pellet_costs_in_euro_per_t": 390,  # EUR/t # Source: [13]
        "pellet_footprint_in_kg_per_kwh": 0.036,  # kgCo2eq/kWh # Source: [8]
        "wood_chip_costs_in_euro_per_t": 96,  # EUR/t Source: [15] using value for 2024 as almost constant over past years
        "wood_chip_footprint_in_kg_per_kwh": 0.0313,  # kgCo2eq/kWh # Source : [14]
        "district_heating_costs_in_euro_per_kwh": 0.15034,  # EUR/kWh Source : [16]
        "district_heating_footprint_in_kg_per_kwh": 0.280,  # kgCo2eq/kWh Source : [8]
        "green_hydrogen_gas_costs_in_euro_per_kwh": 0.25,  # EUR/kWh  # Source: [17] using value of 2020 and assuming it is almost constant for several years
        "green_hydrogen_gas_footprint_in_kg_per_kwh": 0,  # kgCO2eq/kWh
    },
    2024: {
        "electricity_costs_in_euro_per_kwh": 0.4113,  # EUR/kWh  # Source: [10]
        "electricity_footprint_in_kg_per_kwh": 0.363,  # kgCO2eq/kWh  # Source: [9]
        "electricity_to_grid_revenue_in_euro_per_kwh": 0.0692,  # EUR/kWh  # Source: [2] average of 2024 values
        "contracting_heating_costs_hot_water_in_euro_per_kwh": 0.142,  # EUR/kWh  # Source: [7] 160 kW connection
        "contracting_heating_footprint_hot_water_in_kg_per_kwh": 0.28,  # kgCO2eq/kWh # Source: [11] assuming its for 2024
        "contracting_heating_costs_cold_water_in_euro_per_kwh": 0,
        "contracting_heating_footprint_cold_water_in_kg_per_kwh": 0,
        "gas_costs_in_euro_per_kwh": 0.10335,  # EUR/kWh  # Source: [6] average of both half years
        "gas_footprint_in_kg_per_kwh": 0.247,  # kgCO2eq/kWh
        "oil_costs_in_euro_per_l": 0.9941,  # EUR/l # Source: [12]
        "oil_footprint_in_kg_per_l": 3.2,  # kgCO2eq/l
        "diesel_costs_in_euro_per_l": 1.6649,  # EUR/l  # Source: [3]
        "diesel_footprint_in_kg_per_l": 2.0,  # kgCO2eq/l
        "pellet_costs_in_euro_per_t": 289,  # EUR/t # Source: [13]
        "pellet_footprint_in_kg_per_kwh": 0.036,  # kgCo2eq/kWh # Source: [8]
        "wood_chip_costs_in_euro_per_t": 96,  # EUR/t Source: [15]
        "wood_chip_footprint_in_kg_per_kwh": 0.0313,  # kgCo2eq/kWh # Source : [14]
        "district_heating_costs_in_euro_per_kwh": 0.14757,  # EUR/kWh Source : [16]
        "district_heating_footprint_in_kg_per_kwh": 0.280,  # kgCo2eq/kWh Source : [8]
        "green_hydrogen_gas_costs_in_euro_per_kwh": 0.25,  # EUR/kWh  # Source: [17] using value of 2020 and assuming it is almost constant for several years
        "green_hydrogen_gas_footprint_in_kg_per_kwh": 0,  # kgCO2eq/kWh
    },
}


@dataclass_json
@dataclass
class EmissionFactorsAndCostsForFuelsConfig:
    """Emission factors and costs for fuels config class."""

    electricity_costs_in_euro_per_kwh: float  # EUR/kWh
    electricity_footprint_in_kg_per_kwh: float  # kgCO2eq/kWh
    electricity_to_grid_revenue_in_euro_per_kwh: float  # EUR/kWh
    contracting_heating_costs_hot_water_in_euro_per_kwh: float  # EUR/kWh
    contracting_heating_footprint_hot_water_in_kg_per_kwh: float  # kgCO2eq/kWh
    contracting_heating_costs_cold_water_in_euro_per_kwh: float
    contracting_heating_footprint_cold_water_in_kg_per_kwh: float
    gas_costs_in_euro_per_kwh: float  # EUR/kWh
    gas_footprint_in_kg_per_kwh: float  # kgCO2eq/kWh
    oil_costs_in_euro_per_l: float  # EUR/l
    oil_footprint_in_kg_per_l: float  # kgCO2eq/l
    diesel_costs_in_euro_per_l: float  # EUR/l
    diesel_footprint_in_kg_per_l: float  # kgCO2eq/l
    pellet_costs_in_euro_per_t: float  # EUR/t
    pellet_footprint_in_kg_per_kwh: float  # kgCo2eq/kWh
    wood_chip_costs_in_euro_per_t: float  # EUR/t
    wood_chip_footprint_in_kg_per_kwh: float  # kgCo2eq/kWh
    district_heating_costs_in_euro_per_kwh: float  # EUR/kWh
    district_heating_footprint_in_kg_per_kwh: float  # kgCo2eq/kWh
    green_hydrogen_gas_costs_in_euro_per_kwh: float  # EUR/kWh
    green_hydrogen_gas_footprint_in_kg_per_kwh: float  # kgCo2eq/kWh

    @classmethod
    def get_values_for_year(
        cls, year: int
    ) -> "EmissionFactorsAndCostsForFuelsConfig":  # pylint: disable=too-many-return-statements
        """Get emission factors and fuel costs for certain year."""

        if year not in opex_techno_economic_parameters:
            raise KeyError(f"No Emission and cost factors implemented yet for the year {year}.")

        return EmissionFactorsAndCostsForFuelsConfig(**opex_techno_economic_parameters[year])


"""
Sources for capex techno-economic parameters:
        [18]: http://dx.doi.org/10.1016/j.enbuild.2017.04.079
        [19]: /hisim/modular_household/emission_factors_and_costs_devices.csv
        (values are taken from WHY-project: https://cordis.europa.eu/project/id/891943/results but no concrete source was found)
        [20]: https://www.gebaeudeforum.de/service/downloads/ -> search for "Wärmeerzeugung im Bestand mit EE", January 2024
        [21]: https://  doi.org/10.3390/su13041938
        [22]: https://www.duh.de/fileadmin/user_upload/download/Projektinformation/Energieeffizienz/Wärmepumpen/300623_Waermepumpen_Faktenpapier_Neuauflage_Digital.pdf
        [23]: VDI2067-1
        [24]: https://doi.org/10.1016/j.renene.2023.01.117
        [25]: https://solarenergie.de/stromspeicher/preise
        [26]: https://www.ostrom.de/en/post/how-much-does-a-smart-meter-cost-in-2025
        [27]: https://mcsmeters.com/collections/gas-meters
        [28]: https://www.gasag.de/magazin/energiesparen/energiemanagementsystem-privathaushalt/
        [29]: https://www.adac.de/rund-ums-haus/energie/spartipps/energiemanagementsystem-zuhause/
        [30]: https://www.co2online.de/modernisieren-und-bauen/solarthermie/solarthermie-preise-kosten-amortisation/
        [31]: https://www.finanztip.de/photovoltaik/pv-anlage-finanzieren/
        [31]: https://www.kfw.de/inlandsfoerderung/Unternehmen/Energie-Umwelt/F%C3%B6rderprodukte/Erneuerbare-Energien-Standard-(270)/
        [32]: hisim/components/simple_water_storage.py -> function get_scaled_hot_water_storage
        [33]: https://renewa.de/sanierung/gewerke/heizung/fussbodenheizung/foerderung
"""
capex_techno_economic_parameters = {
    2024: {
        Units.KILOWATT: {
            ComponentType.HEAT_PUMP: {
                "investment_costs_in_euro_per_kw": 1600,  # Source: [22]
                "maintenance_costs_as_percentage_of_investment_per_year": 0.015,  # Source: [20]
                "technical_lifetime_in_years": 18,  # Source: [20]
                "co2_footprint_in_kg_per_kw": 165.84,  # Source: [19]
                "subsidy_as_percentage_of_investment_costs": 0.3,  # Source: [20]
            },
            ComponentType.GAS_HEATER: {
                "investment_costs_in_euro_per_kw": 0.36 * 1600,  # 36% of heat pump costs, Source: [20]
                "maintenance_costs_as_percentage_of_investment_per_year": 0.032,  # Source: [20]
                "technical_lifetime_in_years": 18,  # Source: [20]
                "co2_footprint_in_kg_per_kw": 49.47,  # Source: [19]
                "subsidy_as_percentage_of_investment_costs": 0,
            },
            ComponentType.OIL_HEATER: {
                "investment_costs_in_euro_per_kw": 0.75 * 0.36 * 1600,  # 75% of gas heater costs, Source: [19]
                "maintenance_costs_as_percentage_of_investment_per_year": 0.03,  # Source: [23]
                "technical_lifetime_in_years": 18,  # assume same as gas heater based on [19]
                "co2_footprint_in_kg_per_kw": 19.4,  # Source: [19]
                "subsidy_as_percentage_of_investment_costs": 0,
            },
            ComponentType.PELLET_HEATER: {
                "investment_costs_in_euro_per_kw": 0.96 * 1600,  # 96% of heat pump costs, Source: [20]
                "maintenance_costs_as_percentage_of_investment_per_year": 0.047,  # Source: [20]
                "technical_lifetime_in_years": 18,  # Source: [20]
                "co2_footprint_in_kg_per_kw": 49.47,  # assume similar to gas heater based on [19]
                "subsidy_as_percentage_of_investment_costs": 0.3,
            },
            ComponentType.WOOD_CHIP_HEATER: {
                "investment_costs_in_euro_per_kw": 0.96 * 1600,  # 96% of heat pump costs, Source: [20]
                "maintenance_costs_as_percentage_of_investment_per_year": 0.047,  # Source: [20]
                "technical_lifetime_in_years": 18,  # Source: [20]
                "co2_footprint_in_kg_per_kw": 49.47,  # assume similar to gas heater based on [19]
                "subsidy_as_percentage_of_investment_costs": 0.3,
            },
            ComponentType.DISTRICT_HEATING: {
                "investment_costs_in_euro_per_kw": 0.636 * 1600,  # 63.6% of heat pump costs, Source: [20]
                "maintenance_costs_as_percentage_of_investment_per_year": 0.026,  # Source: [20]
                "technical_lifetime_in_years": 20,  # Source: [20]
                "co2_footprint_in_kg_per_kw": 35.09,  # Source: [19], biomass district heating
                "subsidy_as_percentage_of_investment_costs": 0.3,
            },
            ComponentType.ELECTRIC_HEATER: {
                "investment_costs_in_euro_per_kw": 0.196 * 1600,  # 19.6% of heat pump costs, Source: [20]
                "maintenance_costs_as_percentage_of_investment_per_year": 0.01,  # Source: [20]
                "technical_lifetime_in_years": 22,  # Source: [20]
                "co2_footprint_in_kg_per_kw": 1.21,  # Source: [19]
                "subsidy_as_percentage_of_investment_costs": 0,  # Source: [20]
            },
            ComponentType.HYDROGEN_HEATER: {
                "investment_costs_in_euro_per_kw": 0.36
                * 1600,  # 36% of heat pump costs, Source: [20], same as gas heater
                "maintenance_costs_as_percentage_of_investment_per_year": 0.032,  # Source: [20]
                "technical_lifetime_in_years": 18,  # Source: [20]
                "co2_footprint_in_kg_per_kw": 49.47,  # Source: [19]
                "subsidy_as_percentage_of_investment_costs": 0.3,  # green hydrogen, source: [20]
            },
            ComponentType.PV: {
                "investment_costs_in_euro_per_kw": 794.41,  # Source: [19]
                "maintenance_costs_as_percentage_of_investment_per_year": 0.01,  # Source: [25]
                "technical_lifetime_in_years": 25,  # Source: [19]
                "co2_footprint_in_kg_per_kw": 330.51,  # Source: [19]
                "subsidy_as_percentage_of_investment_costs": 0,
                # there is a cheaper KfW loan for PV and batteries but it depends on several factors (bank, risk class etc.),
                # that's why we assume 0% subsidy here, source: [31,32]
            },
        },
        Units.KWH: {
            ComponentType.BATTERY: {
                "investment_costs_in_euro_per_kwh": 546,  # Source: [21]
                "maintenance_costs_as_percentage_of_investment_per_year": 0.032,  # Source: [20]
                "technical_lifetime_in_years": 10,  # Source: [19]
                "co2_footprint_in_kg_per_kwh": 130.7,  # Source: [19]
                "subsidy_as_percentage_of_investment_costs": 0,
                # there is a cheaper KfW loan for PV and batteries but it depends on several factors (bank, risk class etc.),
                # that's why we assume 0% subsidy here, source: [31,32]
            },
        },
        Units.LITER: {
            ComponentType.THERMAL_ENERGY_STORAGE: {
                "investment_costs_in_euro_per_liter": 14.51,  # EUR/liter, Source: [19]
                "maintenance_costs_as_percentage_of_investment_per_year": 0.01,  # Source: [20]
                "technical_lifetime_in_years": 20,  # Source: [20]
                "co2_footprint_in_kg_per_liter": 29.79
                / 50,  # Source: [19] ([19] is in kg/kW, and we assume 1kW approx. = 50l, based on [32])
                "subsidy_as_percentage_of_investment_costs": 0.15,
            },
        },
        Units.SQUARE_METER: {
            ComponentType.SOLAR_THERMAL_SYSTEM: {
                "investment_costs_in_euro_per_m2": 797,  # Source: [30]
                "maintenance_costs_as_percentage_of_investment_per_year": 0.01,  # Source: [20]
                "technical_lifetime_in_years": 20,
                "co2_footprint_in_kg_per_m2": 92.4,  # kgCO2eq/m2, source: [24]
                "subsidy_as_percentage_of_investment_costs": 0.3,  # Source: [30]
            },
        },
        Units.ANY: {
            ComponentType.ELECTRICITY_METER: {
                "investment_costs_in_euro": 100,  # EUR, Source: [26]
                "maintenance_costs_as_percentage_of_investment_per_year": 0.3,  # Source: [26]
                "technical_lifetime_in_years": 20,  # no idea, assumption
                "co2_footprint_in_kg": 0,  # no idea, assume 0
                "subsidy_as_percentage_of_investment_costs": 0,
            },
            ComponentType.GAS_METER: {
                "investment_costs_in_euro": 200,  # EUR, Source: [27]
                "maintenance_costs_as_percentage_of_investment_per_year": 0.15,  # assume around 30€ per year
                "technical_lifetime_in_years": 20,  # no idea, assumption
                "co2_footprint_in_kg": 0,  # no idea, assume 0
                "subsidy_as_percentage_of_investment_costs": 0,
            },
            ComponentType.ENERGY_MANAGEMENT_SYSTEM: {
                "investment_costs_in_euro": 3500,  # EUR/kW, Source: [28]
                "maintenance_costs_as_percentage_of_investment_per_year": 0.028,  # Source: [28]
                "technical_lifetime_in_years": 20,  # no idea, assumption
                "co2_footprint_in_kg": 0,  # no idea, assume 0
                "subsidy_as_percentage_of_investment_costs": 0.15,  # Source: [29]
            },
            ComponentType.HEAT_DISTRIBUTION_SYSTEM: {
                "investment_costs_in_euro": 6500,  # Source: [20] (Factsheet_65ProzentEE_01_Luft-Wasser-Waermepumpe.pdf)
                "maintenance_costs_as_percentage_of_investment_per_year": 0.01,  # Source: [23]
                "technical_lifetime_in_years": 50,  # Source: [23]
                "co2_footprint_in_kg": 0,  # no idea, assume 0
                "subsidy_as_percentage_of_investment_costs": 0.15,  # Source: [33]
            },
        },
    }
}


@dataclass_json
@dataclass
class EmissionFactorsAndCostsForDevicesConfig:
    """Emission factors and costs for devices config class."""

    technical_lifetime_in_years: float
    maintenance_costs_as_percentage_of_investment_per_year: float
    subsidy_as_percentage_of_investment_costs: float
    investment_costs_in_euro_per_kw: Optional[float] = None
    investment_costs_in_euro_per_kwh: Optional[float] = None
    investment_costs_in_euro_per_liter: Optional[float] = None
    investment_costs_in_euro_per_m2: Optional[float] = None
    investment_costs_in_euro: Optional[float] = None
    co2_footprint_in_kg_per_kw: Optional[float] = None
    co2_footprint_in_kg_per_kwh: Optional[float] = None
    co2_footprint_in_kg_per_liter: Optional[float] = None
    co2_footprint_in_kg_per_m2: Optional[float] = None
    co2_footprint_in_kg: Optional[float] = None

    @classmethod
    def get_values_for_year(
        cls, year: int, unit: Units, device: ComponentType
    ) -> "EmissionFactorsAndCostsForDevicesConfig":
        """Get emission factors and costs for a given year and device."""

        if year not in capex_techno_economic_parameters:
            log.debug(
                f"No specfic capex and emission data available for year {year}. "
                f"Use data from year {next(iter(capex_techno_economic_parameters))}"
            )
            year = next(iter(capex_techno_economic_parameters))

        if unit not in capex_techno_economic_parameters[year]:
            raise KeyError(f"No data available for unit '{unit}' in year {year}.")

        if device not in capex_techno_economic_parameters[year][unit]:
            raise KeyError(f"No data available for device '{device}' in unit '{unit}' for year {year}.")

        capex_techno_economic_values = cls(**capex_techno_economic_parameters[year][unit][device])

        return capex_techno_economic_values


@dataclass_json
@dataclass
class WarmWaterStorageConfig(ConfigBase):
    """Warm water storage config class."""

    building_name: str
    name: str
    tank_diameter: float  # [m]
    tank_height: float  # [m]
    tank_start_temperature: float  # [°C]
    temperature_difference: float  # [°C]
    tank_u_value: float  # [W/m^2*K]
    slice_height_minimum: float  # [m]

    @classmethod
    def get_default_config(
        cls,
        building_name: str = "BUI1",
    ) -> Any:
        """Gets a default config."""
        return WarmWaterStorageConfig(
            building_name=building_name,
            name="WarmWaterStorage",
            tank_diameter=1,  # 0.9534        # [m]
            tank_height=2,  # 3.15              # [m]
            tank_start_temperature=65,  # [°C]
            temperature_difference=0.3,  # [°C]
            tank_u_value=0,  # 0.35                 # [W/m^2*K]
            slice_height_minimum=0.05,  # [m]
        )


class CHPControllerConfig:
    """Chp controller config.

    The CHP controller is used to implement an on and off hysteresis
    Decide if its heat- or electricity-led

    Two temperature sensors in the tank are giving the needed information.
    They can be set at a height percentage in the tank. =% is the top, 100 % s the bottom of the tank.
    If the T at the upper sensor is below temperature_switch_on the chp will run until the lower sensor is above temperature_switch_off.
    A minimum runtime in minutes can be defined for the chp.

    If the chp is electric-led, ths is not needed and the electricity demand is provided directly to the chp
    """

    method_of_operation = "heat"
    temperature_switch_on = 60  # [°C]
    temperature_switch_off = 65  # [°C]

    # in steps of 20 % [0, 20, 40 ,60, 80, 100]
    heights_in_tank = [0, 20, 40, 60, 80, 100]
    height_upper_sensor = 20  # [%]
    height_lower_sensor = 60  # [%]

    minimum_runtime_minutes = 4000  # [min]


class GasHeaterConfig:
    """Gas heater config class."""

    is_modulating = True
    P_th_min = 1_000  # [W]
    P_th_max = 12_000  # [W]
    eff_th_min = 0.60  # [-]
    eff_th_max = 0.90  # [-]
    delta_temperature = 25
    mass_flow_max = P_th_max / (4180 * delta_temperature)  # kg/s ## -> ~0.07
    temperature_max = 80  # [°C]


class GasControllerConfig:
    """Gas controller config class.

    This controller works like the CHP controller, but switches on later so the CHP is used more often.
    Gas heater is used as a backup if the CHP power is not high enough.
    If the minimum_runtime is smaller than the timestep, the minimum_runtime is 1 timestep --> generic_gas_heater.py
    """

    temperature_switch_on = 55  # [°C]
    temperature_switch_off = 70  # [°C]

    # in steps of 20 % [0, 20, 40 ,60, 80, 100]
    height_upper_sensor = 20  # [%]
    height_lower_sensor = 80  # [%]

    # minimal timestep is minute
    minimum_runtime_minutes = 7000  # [min]


class LoadConfig:
    """Load config."""

    # massflow_load_minute = 2.5          # [kg/min]
    # massflow_load = massflow_load_minute / 60   # [kg/s]

    possible_massflows_load = [0.1, 0.2, 0.3, 0.4]  # [kg/s]
    delta_temperature = 20

    # the returnflow shows if there was enough energy in the water
    # -> use in load! Not in storage, there the water from WW is included
    temperature_returnflow_minimum = 30  # [°C]

    kwh_per_year = 20_201
    demand_factor = kwh_per_year / 1000


class ElectricityDemandConfig:
    """Electricity demand config class."""

    kwh_per_year = 6000
    demand_factor = kwh_per_year / 1000


class HouseholdWarmWaterDemandConfig:
    """Household warm water demand config."""

    freshwater_temperature = 10  # [°C]
    ww_temperature_demand = 45  # [°C]
    # german --> Grädigkeit
    # difference between T1_in and T2_out
    temperature_difference_hot = 5  # [°C]
    temperature_difference_cold = 6  # [°C]

    heat_exchanger_losses = 0

    kwh_per_year = 2000
    demand_factor = kwh_per_year / 1000


class HydrogenStorageConfig:
    """Hydrogen storage config class."""

    # combination of
    min_capacity = 0  # [kg_H2]
    max_capacity = 500  # [kg_H2]

    starting_fill = 400  # [kg_H2]

    max_charging_rate_hour = 2  # [kg/h]
    max_discharging_rate_hour = 2  # [kg/h]
    max_charging_rate = max_charging_rate_hour / 3600
    max_discharging_rate = max_discharging_rate_hour / 3600

    # ToDo: How does the necessary Heat/Energy come to the Storage?
    energy_for_charge = 0  # [kWh/kg]
    energy_for_discharge = 0  # [kWh/kg]

    loss_factor_per_day = 0  # [lost_%/day]


class AdvElectrolyzerConfig:
    """Adv electrolyzer config class."""

    waste_energy = 400  # [W]   # 400
    min_power = 1_400  # [W]   # 1400
    max_power = 2_4000  # [W]   # 2400
    min_power_percent = 60  # [%]
    max_power_percent = 100  # [%]
    min_hydrogen_production_rate_hour = 300  # [Nl/h]
    max_hydrogen_production_rate_hour = 5000  # [Nl/h]   #500
    min_hydrogen_production_rate = min_hydrogen_production_rate_hour / 3600  # [Nl/s]
    max_hydrogen_production_rate = max_hydrogen_production_rate_hour / 3600  # [Nl/s]
    pressure_hydrogen_output = 30  # [bar]     --> max pressure mode at 35 bar

    """
    The production rate can be converted to an efficiency.
    eff_electrolyzer = (production_rate_hour * hydrogen_specific_heat_capacity_per_kg[kWh/kg]) / (Power_this_timestep[kWh] * hydrogen_specific_volume [m³kg])

    in the component electrolyzer:
    hydrogen_output = Power_this_timestep[kWh] * eff_electrolyzer / hydrogen_specific_heat_capacity_per_kg[kWh/kg]

    I think its overengineering because the providers give the needed information and we try to calculate it back and forth

    --> Solution: efficiency of the electrolyzer is calculated and is an Output
    """


class PVConfig:
    """PV config class."""

    peak_power = 20_000  # [W]


@dataclass_json
@dataclass
class ExtendedControllerConfig(ConfigBase):
    """Extended controller config class."""

    building_name: str
    name: str
    # Active Components
    chp: bool
    gas_heater: bool
    electrolyzer: bool
    # electrolyzer: bool

    # power mode chp
    # chp_mode: str
    chp_mode: str
    chp_power_states_possible: int
    maximum_autarky: bool

    @classmethod
    def get_default_config(
        cls,
        building_name: str = "BUI1",
    ) -> Any:
        """Gets a default ExtendedControllerConfig."""
        return ExtendedControllerConfig(
            building_name=building_name,
            name="Example Component",
            chp=True,
            gas_heater=True,
            electrolyzer=True,
            # electrolyzer = False,
            # power mode chp,
            # chp_mode = "heat",
            chp_mode="power",
            chp_power_states_possible=10,
            maximum_autarky=False,
        )


@dataclass_json
@dataclass
class PhysicsConfig:
    """Physics config class.

    Returns physical and chemical properties of different energy carries.

    Sources:
    Schmidt 2020: Wasserstofftechnik  S.170ff
    https://gammel.de/de/lexikon/heizwert---brennwert/4838.
    Values are taken at standard conditions (25°C)
    https://energyfaculty.com/physical-and-thermal-properties/

    Brennwert: Higher heating value gross caloric value, Heizwert: Lower heating value or net caloric value.
    """

    # Init
    density_in_kg_per_m3: float
    lower_heating_value_in_joule_per_m3: float
    higher_heating_value_in_joule_per_m3: float
    specific_heat_capacity_in_joule_per_kg_per_kelvin: float

    # Post-Init
    specific_volume_in_m3_per_kg: float = field(init=False)
    lower_heating_value_in_joule_per_kg: float = field(init=False)
    higher_heating_value_in_joule_per_kg: float = field(init=False)
    specific_heat_capacity_in_watthour_per_kg_per_kelvin: float = field(init=False)

    def __post_init__(self):
        """Post init function.

        These variables are calculated automatically based on init values.
        """

        self.specific_volume_in_m3_per_kg = 1 / self.density_in_kg_per_m3
        self.lower_heating_value_in_joule_per_kg = self.lower_heating_value_in_joule_per_m3 / self.density_in_kg_per_m3
        self.higher_heating_value_in_joule_per_kg = (
            self.higher_heating_value_in_joule_per_m3 / self.density_in_kg_per_m3
        )
        self.specific_heat_capacity_in_watthour_per_kg_per_kelvin = (
            self.specific_heat_capacity_in_joule_per_kg_per_kelvin / 3600
        )

    @classmethod
    def get_properties_for_energy_carrier(cls, energy_carrier: LoadTypes) -> "PhysicsConfig":
        """Get physical and chemical properties from specific energy carrier."""
        if energy_carrier == LoadTypes.GAS:
            # natural gas (here we use the values of methane because this is what natural gas for residential heating mostly consists of)
            return PhysicsConfig(
                density_in_kg_per_m3=0.71750,
                lower_heating_value_in_joule_per_m3=35.895 * 1e6,
                higher_heating_value_in_joule_per_m3=39.819 * 1e6,
                specific_heat_capacity_in_joule_per_kg_per_kelvin=2190,
            )
        if energy_carrier == LoadTypes.GREEN_HYDROGEN:
            return PhysicsConfig(
                density_in_kg_per_m3=0.08989,
                lower_heating_value_in_joule_per_m3=10.783 * 1e6,
                higher_heating_value_in_joule_per_m3=12.745 * 1e6,
                specific_heat_capacity_in_joule_per_kg_per_kelvin=14200,
            )
        if energy_carrier == LoadTypes.OIL:
            return PhysicsConfig(
                density_in_kg_per_m3=0.83 * 1e3,
                lower_heating_value_in_joule_per_m3=35.358 * 1e9,
                higher_heating_value_in_joule_per_m3=37.682 * 1e9,
                specific_heat_capacity_in_joule_per_kg_per_kelvin=1970,
            )
        if energy_carrier == LoadTypes.PELLETS:
            # density here = bulk density (Schüttdichte)
            # source: https://www.chemie.de/lexikon/Holzpellet.html
            # higher heating value of pellets unknown -> set to lower heating value
            return PhysicsConfig(
                density_in_kg_per_m3=650,
                lower_heating_value_in_joule_per_m3=11.7 * 1e9,
                higher_heating_value_in_joule_per_m3=11.7 * 1e9,
                specific_heat_capacity_in_joule_per_kg_per_kelvin=2500,
            )
        if energy_carrier == LoadTypes.WOOD_CHIPS:
            # density here = bulk density (Schüttdichte)
            # source density and heating value: https://www.umweltbundesamt.de/sites/default/files/medien/479/publikationen/
            # factsheet_ansatz_zur_neubewertung_von_co2-emissionen_aus_der_holzverbrennung_0.pdf
            # source heat capacity: https://www.schweizer-fn.de/stoff/wkapazitaet/wkapazitaet_baustoff_erde.php
            # higher heating value of wood chips unknown -> set to lower heating value
            return PhysicsConfig(
                density_in_kg_per_m3=250,  # approximate value based on different wood types
                lower_heating_value_in_joule_per_m3=15.6 * 1e9,
                higher_heating_value_in_joule_per_m3=15.6 * 1e9,
                specific_heat_capacity_in_joule_per_kg_per_kelvin=2000,  # estimated based on values for different woods
            )
        if energy_carrier == LoadTypes.WATER:
            return PhysicsConfig(
                density_in_kg_per_m3=1000,
                lower_heating_value_in_joule_per_m3=0,
                higher_heating_value_in_joule_per_m3=0,
                specific_heat_capacity_in_joule_per_kg_per_kelvin=4180,
            )

        raise ValueError(f"Energy carrier {energy_carrier} not implemented in PhysicsConfig yet.")

    # Schmidt 2020: Wasserstofftechnik  S.170ff
    # fuel value H2:    10.782 MJ/m³    (S.172)
    # density H2:       0.08989 kg/m³   (S. 23) -> standard conditions
    # hydrogen_density_in_kg_per_m3 = 0.08989  # [kg/m³]
    # hydrogen_specific_volume_in_m3_per_kg = 1 / hydrogen_density_in_kg_per_m3  # [m^3/kg]
    # hydrogen_specific_fuel_value_in_joule_per_m3 = 10.782 * 10**6  # [J/m³]
    # hydrogen_specific_fuel_value_in_joule_per_kg = hydrogen_specific_fuel_value_in_joule_per_m3 / hydrogen_density_in_kg_per_m3  # [J/kg]

    # Schmidt 2020: Wasserstofftechnik  S.170ff
    # fuel value Methan:    35.894 MJ/m³    (S.172)
    # density Methan:       0.71750 kg/m³   (S. 23) -> standard conditions
    # natural_gas_density = 0.71750  # [kg/m³]
    # natural_gas_specific_volume = 1 / hydrogen_density_in_kg_per_m3  # [m^3/kg]
    # natural_gas_specific_fuel_value_per_m_3 = 35.894 * 10**6  # [J/m³]
    # natural_gas_specific_fuel_value_per_kg = natural_gas_specific_fuel_value_per_m_3 / natural_gas_density  # [J/kg]
