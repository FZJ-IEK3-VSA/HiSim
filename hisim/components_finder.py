from pathlib import Path as gg
from pkgutil import iter_modules
from importlib import import_module
import csv
import inspect
import os
from openpyxl import Workbook

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

#Set value to True when it should be shown in excel-sheet
#Can be added later, so that just wanted informations are in excel-sheet
class Information:
    information = {'component_name':True,
                    'stamp':True,
                    'class_name':True,
                    'class_columns':True,
                    'smart_controller_able':True,
                    'default_connections_setted':True,
                    'validated_date':False,
                    'unit_tested':False}
#Define informations of coding-stamp which should be added to excel-sheet
class Stamp:
    stamp = ["__authors__",
             "__copyright__",
             "__credits__",
             "__license__",
             "__version__",
             "__maintainer__",
             "__email__",
             "__status__"]

def calculate_max_classes_of_component(package_dir):
    classes_counter =0
    for (_, module_name, _) in iter_modules([package_dir]):
        module = import_module(f"components.{module_name}")
        classes_counter_check = 0
        for m in inspect.getmembers(module, inspect.isclass):
            if module_name in m[1].__module__:
                classes_counter_check=classes_counter_check+1
        if classes_counter_check>classes_counter:
            classes_counter=classes_counter_check
    return classes_counter

#delte old file
if os.path.exists('components_information.xlsx'):
    os.remove('components_information.xlsx')
#Setting up xlsx file with name
wb = Workbook()
dest_filename = 'components_information.xlsx'
ws1 = wb.active
ws1.title = "components_information"

#open csv file in write mode and add header
with open('components_information.xlxs', 'a', encoding='UTF8') as f:

    # iterate through the modules in the current package
    package_dir = os.path.join(gg(__file__).resolve().parent, "components")

    #Find greatest value of classes of components
    classes_max=calculate_max_classes_of_component(package_dir)
    counter_of_components=1

    #Write Header of excl-sheet
    ws1['A' + str(counter_of_components)] = "ComponentName"
    ws1['B' + str(counter_of_components)] = "Coding-Stamp"
    row_to_add_class = 3
    for i in range(1,classes_max+1):
        _ = ws1.cell(column=row_to_add_class, row=counter_of_components, value=str(i)+".Class")
        row_to_add_class = row_to_add_class + 1
        _ = ws1.cell(column=row_to_add_class, row=counter_of_components, value=str(i)+".ClassLength")
        row_to_add_class =row_to_add_class+1
    _ = ws1.cell(column=row_to_add_class, row=counter_of_components, value="Unit-Test-Status")

    #Start to iteratre to get Informations to fill in excel-sheet
    for (_, module_name, _) in iter_modules([package_dir]):
        counter_of_components = counter_of_components+1
        added_row_classes=[]
        added_row_classes_columns=[]

        # import the module and iterate through its attributes
        module = import_module(f"components.{module_name}")
        module_dict=module.__dict__

        #check if coding-stamp exist and write down
        stamp_to_add=""
        for stamp in Stamp.stamp:
            if stamp in module_dict:
                if type(module_dict[stamp])==list:
                    for x in module_dict[stamp]:
                        stamp_to_add = stamp_to_add + '/' + stamp + ": " + str(x)
                else:
                    stamp_to_add=stamp_to_add+'/'+stamp+": "+module_dict[stamp]
            else:
                stamp_to_add=stamp_to_add+'/'+stamp+": miss"

        '''
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
        '''

        #Writing Component Name and Coding-Stamp in to excel-sheet
        ws1['A'+str(counter_of_components)]=module_name
        ws1['B' + str(counter_of_components)] = stamp_to_add
        row_to_add_class=3

        #Writing Class Names and Class Length in to excel Sheet
        for m in inspect.getmembers(module, inspect.isclass):
            if module_name in m[1].__module__:
                _ = ws1.cell(column=row_to_add_class, row=counter_of_components, value=m[0])
                row_to_add_class=row_to_add_class+1
                _ = ws1.cell(column=row_to_add_class, row=counter_of_components, value=len(inspect.getsourcelines(m[1])[0]))
                row_to_add_class=row_to_add_class+1

        #Checks if unit-test is written for the component in tests-folder

        row_to_add_class=3+classes_max*2
        package_dir_tests = os.path.join(gg(__file__).resolve().parent.parent, "tests")
        for (_, module_name_test, _) in iter_modules([package_dir_tests]):
            if module_name in module_name_test:
                check_var=True
                break
            else:
                check_var=False
        if check_var:
            ws1.cell(column=row_to_add_class, row=counter_of_components, value='avaible')
            row_to_add_class = row_to_add_class + 1
        else:
            ws1.cell(column=row_to_add_class, row=counter_of_components, value='miss')
            row_to_add_class = row_to_add_class + 1

    wb.save("components_information.xlsx")