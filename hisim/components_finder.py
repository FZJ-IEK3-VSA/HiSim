from pathlib import Path as gg
from pkgutil import iter_modules
from importlib import import_module
import csv
import inspect
import os

__authors__ = "Maximilian Hillen,"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt, Vitor Hugo Bellotto Zago"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = "development"

"""
    This Script produces an excel sheet which includes various information of all components in the folder
    components. The information are:
    -   Name of Component
    -   Coding-Stamp (authors, copyright, ...)
    -   All clases which are included in the component
    The clearest layout of the excel sheet can be read in excel-reader
"""

#delte old file
if os.path.exists('components_information.csv'):
    os.remove('components_information.csv')
#open csv file in write mode and add header
with open('components_information.csv', 'a', encoding='UTF8') as f:
    #todo: automize how many number of classes for header, for this first check component with most classes
    header = ['ComponentName','Stamp']
    for i in range(1,16):
        header.append(str(i)+'.Class')
    writer = csv.writer(f)
    writer.writerow(header)

    # iterate through the modules in the current package
    package_dir = os.path.join(gg(__file__).resolve().parent, "components")

    for (_, module_name, _) in iter_modules([package_dir]):
        added_row=[]
        # import the module and iterate through its attributes
        module = import_module(f"components.{module_name}")
        added_row.append(module_name)

        # define informations of stamp
        stamp=["__authors__",
               "__copyright__",
               "__credits__",
               "__license__",
               "__version__",
               "__maintainer__",
               "__email__",
               "__status__"]

        # search if stamp in component exist and add if it exist
        with open(package_dir+'\\'+module_name+'.py') as temp_f:
            datafile = temp_f.readlines()
            added_string=""
            for stamp in stamp:
                str_match = [s for s in datafile if stamp in s]
                if str_match:
                    str_match=str_match[0].replace('\n','')
                    #added_string=added_string+"\n"+str_match
                    added_string = added_string + str_match
                else:
                    added_string="no stamp exists"
                    break
            added_row.append(added_string)
            temp_f.close()

        # add class names of components
        for m in inspect.getmembers(module, inspect.isclass):
            if module_name in m[1].__module__:

                added_row.append(m[0])
        writer.writerow(added_row)