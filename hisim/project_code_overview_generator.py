""" Makes an overview of all the components and collects important information for each module. """
from pathlib import Path as Pathlibpath
import importlib
from dataclasses import dataclass, field
import inspect
import os
import sys
from openpyxl import Workbook  # type: ignore
from typing import  List, Any
__authors__ = "Noah Pflugradt, Maximilian Hillen"
__copyright__ = "Copyright 2021-2022, FZJ-IEK-3"
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"

BuiltInAttributes = [
    "__builtins__",
"__cached__",
"__doc__",
"__file__",
"__name__",
"__package__",
"__path__",
]

@dataclass
class ClassInformation:
    Name: str = ""
    Lines_of_Code: int = 0

@dataclass
class StringInformation:
    Name: str = ""
    Value: str = ""

@dataclass
class MethodInformation:
    Name: str = ""

@dataclass
class ListInformation:
    Name: str = ""

@dataclass
class DictInformation:
    Name: str = ""

@dataclass
class OtherMembers:
    Name: str = ""
    VariableType: str = ""
@dataclass
class FileInformation:
    ModuleName: str = ""
    FileName: str = ""
    Length: str  = ""
    Authors: str = ""
    Copyright: str = ""
    Credits: str = ""
    License: str = ""
    Version: str = ""
    Maintainer: str = ""
    Email: str = ""
    Status: str = ""
    Lines: int = 0
    Python_Module_Loading_Possible: bool = False
    Classes: List[ClassInformation] = field(default_factory=list)
    Methods: List[MethodInformation] = field(default_factory=list)
    Strings: List[StringInformation] = field(default_factory=list)
    Lists: List[ListInformation] = field(default_factory=list)
    Dicts: List[DictInformation] = field(default_factory=list)
    Others: List[OtherMembers] = field(default_factory=list)
class OverviewGenerator:
    def add_to_cell(self, column: int, row: int, value: Any, worksheet):
        """ Write data to the Excel sheet. """
        worksheet.cell(column=column, row=row, value=value)
        column = column + 1
        return column

    def run(self):
        """ Execute the components finder. """
        dest_filename = 'components_information.xlsx'

        # collect file names
        python_files = self.collect_files()

        # read all the information
        fis: List[FileInformation] = []
        for filename in python_files:
            myfi = self.process_one_file(filename)
            fis.append(myfi)

        # delete old excel file
        if os.path.exists(dest_filename):
            os.remove(dest_filename)

        # Setting up new xlsx file
        workbook = Workbook()
        worksheet1 = workbook.active
        worksheet1.title = "HiSim Files"
        row: int = 1
        for myfi in fis:
            row = self.write_one_file_block(myfi, row, worksheet1)
            row = row + 1
        # import the module and iterate through its attributes
        workbook.save(dest_filename)

    def write_one_file_block(self, myfi, row, worksheet1):
        """ writes the block for a single file to excel"""
        column: int = 1
        column = self.add_to_cell(column=column, row=row, value=myfi.ModuleName, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.FileName, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.Lines, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.Python_Module_Loading_Possible,
                                  worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.Authors, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.Copyright, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.Email, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.License, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.Maintainer, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.Maintainer, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.Status, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.Version, worksheet=worksheet1)

        if (len(myfi.Classes)) > 0:
            self.add_to_cell(column=column, row=row, value="Classes", worksheet=worksheet1)
            for myclass in myfi.Classes:
                row = row + 1
                subcol = column + 1
                subcol = self.add_to_cell(column=subcol, row=row, value=myclass.Name, worksheet=worksheet1)
                subcol = self.add_to_cell(column=subcol, row=row, value=myclass.Lines_of_Code, worksheet=worksheet1)
            row = row + 1
        if (len(myfi.Methods)) > 0:
            self.add_to_cell(column=column, row=row, value="Methods", worksheet=worksheet1)
            for mymethods in myfi.Methods:
                row = row + 1
                subcol = column + 1
                subcol = self.add_to_cell(column=subcol, row=row, value=mymethods.Name, worksheet=worksheet1)
            row = row + 1
        if (len(myfi.Strings)) > 0:
            self.add_to_cell(column=column, row=row, value="Strings", worksheet=worksheet1)
            for mystr in myfi.Strings:
                row = row + 1
                subcol = column + 1
                subcol = self.add_to_cell(column=subcol, row=row, value=mystr.Name, worksheet=worksheet1)
                subcol = self.add_to_cell(column=subcol, row=row, value=mystr.Value, worksheet=worksheet1)
            row = row + 1
        if (len(myfi.Others)) > 0:
            self.add_to_cell(column=column, row=row, value="Others", worksheet=worksheet1)
            for otherstuff in myfi.Others:
                row = row + 1
                subcol = column + 1
                subcol = self.add_to_cell(column=subcol, row=row, value=otherstuff.Name, worksheet=worksheet1)
                subcol = self.add_to_cell(column=subcol, row=row, value=otherstuff.VariableType, worksheet=worksheet1)
            row = row + 1
        return row

    def process_one_file(self, filename):
        # import the module and iterate through its attributes
        myfi: FileInformation = FileInformation()

        myfi.FileName = filename
        myfi.ModuleName = os.path.basename(filename)
        self.analyze_file_directly(filename, myfi)
        try:
            spec = importlib.util.spec_from_file_location(myfi.ModuleName, myfi.FileName)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules[myfi.ModuleName] = module
        except:  # noqa
            module = None
        if module is None:
            myfi.Python_Module_Loading_Possible = False
            return myfi

        python_module_name = module.__name__
        for module_member in inspect.getmembers(module):
            if hasattr(module_member[1], "__module__"):
                # this is an import from another module, therefore skip
                if str(python_module_name) != str(module_member[1].__module__):
                    continue
            if str(module_member[0]) in BuiltInAttributes:
                continue
            mytype = type(module_member[1])
            strname = str(module_member[0])
            strval = str(module_member[1])
            if str(mytype) == "<class 'type'>":
                ci = ClassInformation()
                ci.Name = strname
                ci.Lines_of_Code = len(inspect.getsourcelines(module_member[1]))
                myfi.Classes.append(ci)
                continue
            if str(mytype) == "<class 'module'>":
                continue
            if str(mytype) == "<class 'function'>":
                mi = MethodInformation(module_member[0])
                myfi.Methods.append(mi)
                continue
            if str(mytype) == "<class 'str'>":
                self.process_string_attribute(myfi, strname, strval)
                continue
            if str(mytype) == "<class 'list'>":
                li = ListInformation(str(module_member[0]))
                myfi.Lists.append(li)
                continue
            if str(mytype) == "<class 'dict'>":
                dii = DictInformation(str(module_member[0]))
                myfi.Dicts.append(dii)
                continue
            oi = OtherMembers(str(module_member[0]), str(mytype))
            myfi.Others.append(oi)
        return myfi

    def process_string_attribute(self, myfi, strname, strval):
        if strname == "__authors__":
            myfi.Authors = strval
        elif strname == "__copyright__":
            myfi.Copyright = strval
        elif strname == "__email__":
            myfi.Email = strval
        elif strname == "__license__":
            myfi.License = strval
        elif strname == "__maintainer__":
            myfi.Maintainer = strval
        elif strname == "__status__":
            myfi.Status = strval
        elif strname == "__version__":
            myfi.Version = strval
        else:
            sti = StringInformation(strname, strval)
            myfi.Strings.append(sti)

    def analyze_file_directly(self, filename, myfi):
        count = 0
        with open(filename, "r", encoding="utf8") as sourcefile:
            for count, _line in enumerate(sourcefile):
                pass
        myfi.Lines = count

    def collect_files(self):
        # Iterate through the modules in the current package
        hisim_dir = Pathlibpath(__file__).resolve().parent.parent
        files = []
        for dirpath, _, filenames in os.walk(hisim_dir):
            for filename in [f for f in filenames if f.endswith(".py")]:
                pypath = os.path.join(dirpath, filename)
                if pypath.__contains__(".eggs"):
                    continue
                files.append(pypath)
        return files


if __name__ == "__main__":
    cf = OverviewGenerator()
    cf.run()
