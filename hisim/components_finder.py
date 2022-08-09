from pathlib import Path as gg
from pkgutil import iter_modules
from importlib import import_module
import inspect
import os
from openpyxl import Workbook  # type: ignore

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


# Set value to True when it should be shown in excel-sheet
# Can be added later, so that just wanted informations are in excel-sheet
class Information:
    information = {'component_name': True,
                   'stamp': True,
                   'class_name': True,
                   'class_columns': True,
                   'smart_controller_able': True,
                   'default_connections_setted': True,
                   'validated_date': False,
                   'unit_tested': False}


# Define informations of coding-stamp which should be added to excel-sheet
class Stamp:
    stamp = ["__authors__",
             "__copyright__",
             "__credits__",
             "__license__",
             "__version__",
             "__maintainer__",
             "__email__",
             "__status__"]

class Components_Finder:

    def calculate_max_classes_of_component(self, package_dir):
        classes_counter = 0
        for (_, module_name, _) in iter_modules([package_dir]):
            module = import_module(f"components.{module_name}")
            classes_counter_check = 0
            for m in inspect.getmembers(module, inspect.isclass):
                if module_name in m[1].__module__:
                    classes_counter_check = classes_counter_check + 1
            if classes_counter_check > classes_counter:
                classes_counter = classes_counter_check
        return classes_counter


    def add_to_cell(self, column: int, row: int, value: str, worksheet):
        _ = worksheet.cell(column=column, row=row, value=value)
        column = column + 1
        return _, column


    def run(self):
        dest_filename = 'components_information.xlsx'
        # delete old file
        if os.path.exists(dest_filename):
            os.remove(dest_filename)
        # Setting up xlsx file with name
        wb = Workbook()

        ws1 = wb.active
        ws1.title = "components_information"

        # open csv file in write mode and add header
        with open(dest_filename, 'a', encoding='UTF8') as file_stream:
            # iterate through the modules in the current package
            package_dir = os.path.join(gg(__file__).resolve().parent, "components")

            # Find greatest value of classes of components
            classes_max = self.calculate_max_classes_of_component(package_dir)
            row = 1
            column = 1
            # Write Header of excl-sheet
            _, column = self.add_to_cell(column=column, row=row, value="ComponentName", worksheet=ws1)
            for stamp in Stamp.stamp:
                _, column = self.add_to_cell(column=column, row=row, value=stamp, worksheet=ws1)

            for i in range(1, classes_max + 1):
                _, column = self.add_to_cell(column=column, row=row, value=str(i) + ".Class", worksheet=ws1)
                _, column = self.add_to_cell(column=column, row=row, value=str(i) + ".ClassLength", worksheet=ws1)
            _, column = self.add_to_cell(column=column, row=row, value="Unit-Test-Status", worksheet=ws1)

            # Start to iteratre to get Informations to fill in excel-sheet
            for (_, module_name, _) in iter_modules([package_dir]):
                column = 1
                row = row + 1

                # import the module and iterate through its attributes
                module = import_module(f"components.{module_name}")
                module_dict = module.__dict__

                # Writing Component Name and Coding-Stamp in to excel-sheet
                _, column =  self.add_to_cell(column=column, row=row, value=module_name, worksheet=ws1)

                # check if coding-stamp exist and write down
                stamp_to_add = ""
                for stamp in Stamp.stamp:
                    if stamp in module_dict:
                        if type(module_dict[stamp]) == list:
                            for x in module_dict[stamp]:
                                _, column = self.add_to_cell(column=column, row=row, value=str(x), worksheet=ws1)
                        else:
                            _, column =  self.add_to_cell(column=column, row=row, value=module_dict[stamp], worksheet=ws1)
                    else:
                        _, column =  self.add_to_cell(column=column, row=row, value="miss", worksheet=ws1)

                # Writing Class Names and Class Length in to excel Sheet
                added_classes_total = 0
                for module_member in inspect.getmembers(module, inspect.isclass):
                    if module_name in module_member[1].__module__:
                        _, column =  self.add_to_cell(column=column, row=row, value=module_member[0], worksheet=ws1)
                        _, column =  self.add_to_cell(column=column, row=row,
                                                value=str(len(inspect.getsourcelines(module_member[1])[0])), worksheet=ws1)
                        added_classes_total = added_classes_total + 1
                if added_classes_total < classes_max:
                    for i in range(0, classes_max - added_classes_total):
                        _, column =  self.add_to_cell(column=column, row=row, value="", worksheet=ws1)
                        _, column =  self.add_to_cell(column=column, row=row, value="", worksheet=ws1)

                # Checks if unit-test is written for the component in tests-folder and add if not
                package_dir_tests = os.path.join(gg(__file__).resolve().parent.parent, "tests")
                check_var = False
                for (_, module_name_test, _) in iter_modules([package_dir_tests]):
                    if module_name in module_name_test:
                        check_var = True
                if check_var:
                    _, column =  self.add_to_cell(column=column, row=row, value='available', worksheet=ws1)
                else:
                    _, column =  self.add_to_cell(column=column, row=row, value='missing', worksheet=ws1)

            wb.save(dest_filename)

if __name__ == "__main__":
    cf = Components_Finder()
    cf.run()