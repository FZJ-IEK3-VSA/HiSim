"""The zero-call-site CHP configuration that read an Excel sheet at construction (D-16).

Moved out of `hisim/components/advanced_fuel_cell.py` by component sweep decision D-16
(2026-09-15), the way the same decision moved `SimpleHotWaterStorageController` out of
`simple_water_storage.py`. `CHPConfigAdvanced` had no call site of any kind -- no system setup,
no test, no other component, no scenario JSON and no energy-system file ever named it -- and it
is not a configuration in the sense the rest of the repository uses the word: it is a plain
class, not a `ConfigBase` dataclass, so it can neither be serialized into an energy-system file
nor carry a preset. Its sibling `CHPConfig` in the same module is what `CHP` is actually built
from, and that one converted to a `hydrogen` preset in the same change.

What it did: read one row of `hisim/inputs/chp_system/mock_up_efficiencies.xlsx` -- the row of
"BlueGen BG15", with five other machine names left in the file as commented-out alternatives --
and hold that row's power band, efficiencies, maximum mass flow and maximum temperature as
attributes. The whole class body sat inside `__init__` so that the file was only opened when
someone constructed it. The spreadsheet stays where it is: nothing else reads it, but it is the
only record of those six machines.

`advanced_fuel_cell.py` keeps everything else and, with this class gone, no longer imports `os`
or `hisim.utils`, whose sole readers the Excel lookup below was.

The class below is the file's own text, unchanged. The imports it needs are recorded as a
comment rather than as live imports: nothing here is maintained, and nothing imports from
`obsolete/`.

    import os

    import pandas as pd

    from hisim import log
    from hisim import utils
"""


class CHPConfigAdvanced:
    """CHP config advanced class."""

    def __init__(self) -> None:
        """Initialize the class."""
        # Remark: moved the whole class body into the __init__ function to avoid errors if the file read below
        # does not exist.

        # system_name = "BlueGEN15"
        # system_name = "Dachs 0.8"
        # system_name = "Test_KWK"
        # system_name = "Dachs G2.9"
        # system_name = "HOMER"
        system_name = "BlueGen BG15"

        dataframe = pd.read_excel(
            os.path.join(utils.HISIMPATH["chp_system"], "mock_up_efficiencies.xlsx"),
            index_col=0,
        )

        df_specific = dataframe.loc[str(system_name)]

        if str(df_specific["is_modulating"]) == "Yes":
            self.is_modulating: bool = True
            self.p_el_min: float = df_specific["P_el_min"]
            self.p_th_min: float = df_specific["P_th_min"]
            self.p_total_min: float = df_specific["P_total_min"]
            self.eff_el_min: float = df_specific["eff_el_min"]
            self.eff_th_min: float = df_specific["eff_th_min"]

        elif str(df_specific["is_modulating"]) == "No":
            self.is_modulating = False
        else:
            log.error("Modulation is not defined. Modulation must be 'Yes' or 'No'")
            raise ValueError

        self.p_el_max: float = df_specific["P_el_max"]
        self.p_th_max: float = df_specific["P_th_max"]
        self.p_total_max: float = df_specific["P_total_max"]  # maximum fuel consumption
        self.eff_el_max: float = df_specific["eff_el_max"]
        self.eff_th_max: float = df_specific["eff_th_max"]
        self.mass_flow_max: float = df_specific["mass_flow (dT=20°C)"]
        self.temperature_max: float = df_specific["temperature_max"]
        self.delta_temperature: float = 10
