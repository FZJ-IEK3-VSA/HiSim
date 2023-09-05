from builda_client.client import BuildaClient
import pandas as pd

client: BuildaClient = BuildaClient()

#footprint_stats = client.get_footprint_area_statistics(nuts_level = 4)
buildings: list[Building] = client.get_buildings()

#data_df = pd.DataFrame(footprint_stats)
print(buildings)
#print(len(data_df))