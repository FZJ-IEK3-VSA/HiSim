import pandas as pd
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import seaborn as sns
import math
import numpy as np


# Lese die Excel-Datei und das Tabellenblatt "Bezug" ein
df = pd.read_excel('C://Users//Standard//Desktop//hisim//results//2a20240624//Gesamtergebnis.xlsx', sheet_name='Netzbezug')
print(df.head())
# Extrahiere die relevanten Spalten
brennstoffzellenleistung = df['FuelCellPowerkWh']  # Spalte B
batteriekapazität = df['BatteryCapacitykWh']         # Spalte C
inverterleistung = df['InverterPowerkW']          # Spalte D
netzbezugsmenge = df['ElectricityfromGridMWh']           # Spalte E
kapitalwert = df['Kapitalwert']           # Spalte F
kapitalwert:float = kapitalwert/1000000

############################################################################# 1
# #### Netzbezug Scatter Plot,


# Erstelle eine 3D-Scatter-Plot-Figur Netzbezug 
fig = plt.figure(figsize=(10, 8))
ax = fig.add_subplot(111, projection='3d')
fig.patch.set_facecolor('white')
ax.set_facecolor('white')
ax.view_init(25, 235)

punktgroesse = min(netzbezugsmenge)/(netzbezugsmenge)*100
# Erzeuge den Scatter-Plot mit Farbe basierend auf der Netzbezugsmenge
sc = ax.scatter(brennstoffzellenleistung, batteriekapazität, inverterleistung,
                c= netzbezugsmenge, cmap='RdYlGn_r',s=punktgroesse, edgecolor='black')

# Füge Farbbalken hinzu, um die Skalierung zu zeigen
cbar = plt.colorbar(sc)
cbar.set_label('Netzbezugsmenge Strom in MWh/a')

# Beschrifte die Achsen
ax.set_xlabel('rSOC-Leistung in kW')
ax.set_ylabel('Batteriekapazität in kWh')
ax.set_zlabel('Inverterleistung in kW')

## **Setze die Grenzen für die Achsen**
brennstoffzellenleistung_max:float =  brennstoffzellenleistung.max()
brennstoffzellenleistung_max_ = math.ceil(brennstoffzellenleistung_max/500)*500
batteriekapazität_max = batteriekapazität.max()
batteriekapazität_max_ = math.ceil(batteriekapazität_max/1000)*1000
inverterleistung_max = inverterleistung.max()
inverterleistung_max_ = math.ceil(inverterleistung_max/500)*500
ax.set_xlim(0, brennstoffzellenleistung_max_)
ax.set_ylim(0, batteriekapazität_max_)
ax.set_zlim(0, inverterleistung_max_)


# Zeige den Plot an
#plt.show()
plt.savefig('C://Users//Standard//Desktop//hisim//results//2a20240624//Netzbezug_scatter.png', dpi=300)  # Speichert als PNG mit 300 DPI Auflösung


############################################################################# 2
### Kapitalwert Scatter Plot

fig = plt.figure(figsize=(10, 8))
ax = fig.add_subplot(111, projection='3d')
fig.patch.set_facecolor('white')
ax.set_facecolor('white')
ax.view_init(25, 235)

# Erzeuge den Scatter-Plot mit Farbe basierend auf der Netzbezugsmenge

kapitalwertmin_ = kapitalwert.min()

kapitalwertmax_ = kapitalwert.max()
punktgroesse = kapitalwertmax_/kapitalwert*100
sc = ax.scatter(brennstoffzellenleistung, batteriekapazität, inverterleistung,
                c= kapitalwert, cmap='RdYlGn', vmin=kapitalwertmin_, vmax=kapitalwertmax_ ,s=punktgroesse, edgecolor='black')

# Füge Farbbalken hinzu, um die Skalierung zu zeigen
cbar = plt.colorbar(sc, ticks=np.linspace(kapitalwertmin_, kapitalwertmax_,4))
cbar.ax.set_yticklabels(['niedrig', '', '', 'hoch'])
cbar.set_label('Kapitalwert nach 20 Jahren (negativ)')

# Beschrifte die Achsen
ax.set_xlabel('rSOC-Leistung in kW')
ax.set_ylabel('Batteriekapazität in kWh')
ax.set_zlabel('Inverterleistung in kW')

# **Setze die Grenzen für die Achsen**
ax.set_xlim(0, brennstoffzellenleistung_max_)
ax.set_ylim(0, batteriekapazität_max_)
ax.set_zlim(0, inverterleistung_max_)


# Zeige den Plot an
#plt.show()
plt.savefig('C://Users//Standard//Desktop//hisim//results//2a20240624//Kapitalwert_scatter.png', dpi=300)  # Speichert als PNG mit 300 DPI Auflösung


############################################################################# 3
##### Filterung 10 kleinsten Netzbezugswerte --> dazugehörige Kapitalwerte!

#### Scatter Plot der 20 kleinsten Netzbezugsmenge-Werte
# Sortiere den DataFrame nach der Netzbezugsmenge und wähle die 20 kleinsten Werte aus
df_smallest_netbezug = df.nsmallest(10, 'ElectricityfromGridMWh')

# Extrahiere die relevanten Spalten
brennstoffzellenleistung_small = df_smallest_netbezug['FuelCellPowerkWh']
batteriekapazität_small = df_smallest_netbezug['BatteryCapacitykWh']
inverterleistung_small = df_smallest_netbezug['InverterPowerkW']
netzbezugsmenge_small = df_smallest_netbezug['ElectricityfromGridMWh']
kapitalwert_small = df_smallest_netbezug['Kapitalwert'] / 1000000  # Umrechnung in Millionen Euro

# Erstelle einen 3D Scatter-Plot für die 10 kleinsten Netzbezugsmenge-Werte
fig = plt.figure(figsize=(10, 8))
ax = fig.add_subplot(111, projection='3d')
fig.patch.set_facecolor('white')
ax.set_facecolor('white')
ax.view_init(25, 235)


punktgroesse = min(netzbezugsmenge_small)/(netzbezugsmenge_small)*100
# Erzeuge den Scatter-Plot mit Farbe basierend auf der Netzbezugsmenge
sc = ax.scatter(brennstoffzellenleistung_small, batteriekapazität_small, inverterleistung_small,
                c= netzbezugsmenge_small, cmap='RdYlGn_r',s=punktgroesse, edgecolor='black')

# Füge Farbbalken hinzu, um die Skalierung zu zeigen
cbar = plt.colorbar(sc)
cbar.set_label('Netzbezugsmenge Strom in MWh/a')

# Beschrifte die Achsen
ax.set_xlabel('rSOC-Leistung in kW')
ax.set_ylabel('Batteriekapazität in kWh')
ax.set_zlabel('Inverterleistung in kW')

# **Setze die Grenzen für die Achsen**
brennstoffzellenleistung_max_small:float =  brennstoffzellenleistung_small.max()
brennstoffzellenleistung_max__small = math.ceil(brennstoffzellenleistung_max_small/50)*50
batteriekapazität_max_small = batteriekapazität_small.max()
batteriekapazität_max__small = math.ceil(batteriekapazität_max_small/500)*500
inverterleistung_max_small = inverterleistung_small.max()
inverterleistung_max__small = math.ceil(inverterleistung_max_small/20)*20
ax.set_xlim(20, brennstoffzellenleistung_max__small)
ax.set_ylim(1500, batteriekapazität_max__small)
ax.set_zlim(50, inverterleistung_max__small)
#plt.show()
plt.savefig('C://Users//Standard//Desktop//hisim//results//2a20240624//Netzbezug_20kleisnten_scatter.png', dpi=300)  # Speichert als PNG mit 300 DPI Auflösung




############################################################################# 4
# Erstelle einen Scatter-Plot für die 20 kleinsten Netzbezugsmenge-Werte mit den passenden Kapitalwerten

kapitalwertmin__small = kapitalwert_small.min()
kapitalwertmax__small = kapitalwert_small.max()

punktgroesse = kapitalwertmax__small/kapitalwert_small*100

sc = ax.scatter(brennstoffzellenleistung_small, batteriekapazität_small, inverterleistung_small,
                c= kapitalwert_small, cmap='RdYlGn',s=punktgroesse, edgecolor='black')


# Füge Farbbalken hinzu, um die Skalierung zu zeigen
cbar.remove()
cbar = plt.colorbar(sc, ticks=np.linspace(kapitalwertmin__small, kapitalwertmax__small,4))
cbar.ax.set_yticklabels(['niedrig', '', '', 'hoch'])
cbar.set_label('Kapitalwert nach 20 Jahren (negativ)')

# Beschrifte die Achsen
ax.set_xlabel('rSOC-Leistung in kW')
ax.set_ylabel('Batteriekapazität in kWh')
ax.set_zlabel('Inverterleistung in kW')

# **Setze die Grenzen für die Achsen**
brennstoffzellenleistung_max_small:float =  brennstoffzellenleistung_small.max()
brennstoffzellenleistung_max__small = math.ceil(brennstoffzellenleistung_max_small/50)*50
batteriekapazität_max_small = batteriekapazität_small.max()
batteriekapazität_max__small= math.ceil(batteriekapazität_max_small/500)*500
inverterleistung_max_small = inverterleistung_small.max()
inverterleistung_max__small = math.ceil(inverterleistung_max_small/20)*20
ax.set_xlim(20, brennstoffzellenleistung_max__small)
ax.set_ylim(1500, batteriekapazität_max__small)
ax.set_zlim(50, inverterleistung_max__small)


# Zeige den Plot an
#plt.show()
plt.savefig('C://Users//Standard//Desktop//hisim//results//2a20240624//Kapitalwerte_zu_Netzbezug_20kleisnten_scatter.png', dpi=300)  # Speichert als PNG mit 300 DPI Auflösung
