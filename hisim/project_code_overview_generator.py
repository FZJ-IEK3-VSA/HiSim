""" Makes an overview of all the components and collects important information for each module. """
# clean
from types import ModuleType
from typing import List, Any, Optional
from pathlib import Path as Pathlibpath
import importlib
import importlib.util
from dataclasses import dataclass, field
import inspect
import os
import sys
from openpyxl import Workbook  # type: ignore

# todo: check for print commands in all files and fail
# todo: check for duplicate class names and fail if different components have the same class name

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

    """ Stores information about classes. """

    class_name: str = ""
    lines_of_code: int = 0


@dataclass
class StringInformation:

    """ Stores information for strings. """

    string_name: str = ""
    string_value: str = ""


@dataclass
class MethodInformation:

    """ Stores information about methods. """

    method_name: str = ""


@dataclass
class ListInformation:

    """ Stores information for all lists. """

    list_name: str = ""


@dataclass
class DictInformation:

    """ Stores information for all dictionaries. """

    dict_name: str = ""


@dataclass
class OtherMembers:

    """ Stores information for all other members of the files. """

    member_name: str = ""
    variable_type: str = ""


@dataclass
class FileInformation:

    """ Stores the information about a single file. """

    module_name: str = ""
    file_name: str = ""
    length: str = ""
    authors: str = ""
    cleaned: bool = False
    copyright: str = ""
    credits: str = ""
    license: str = ""
    version: str = ""
    maintainer: str = ""
    email: str = ""
    status: str = ""
    lines: int = 0
    python_module_loading_possible: bool = False
    classes: List[ClassInformation] = field(default_factory=list)
    methods: List[MethodInformation] = field(default_factory=list)
    strings: List[StringInformation] = field(default_factory=list)
    lists: List[ListInformation] = field(default_factory=list)
    dicts: List[DictInformation] = field(default_factory=list)
    others: List[OtherMembers] = field(default_factory=list)


class OverviewGenerator:

    """ Generates an overview of all modules. """

    def __init__(self):
        """ Initializes the class. """
        self.existing_classes: List[str] = []

    def add_to_cell(self, column: int, row: int, value: Any, worksheet: Workbook) -> int:
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
        self.write_clean_files(fis)

    def write_clean_files(self, fis: List[FileInformation]) -> None:
        """ Writes files for calling flak8e and prospector. """
        with open("../flake8_calls.txt", "w", encoding="utf8") as flake8:
            for myfi in fis:
                if not myfi.cleaned:
                    continue
                relative_name = myfi.file_name.replace("C:\\work\\hisim_github\\HiSim\\", "")
                relative_name_slash = relative_name.replace("\\", "/")
                flake8.write("        flake8 " + relative_name_slash + " --count --select=E9,F63,F7,F82,E800 --show-source --statistics\n")
        with open("../prospector_calls.txt", "w", encoding="utf8") as prospector:
            for myfi in fis:
                if not myfi.cleaned:
                    continue
                relative_name = myfi.file_name.replace("C:\\work\\hisim_github\\HiSim\\", "")
                relative_name_slash = relative_name.replace("\\", "/")
                prospector.write("        prospector " + relative_name_slash + "\n")

        with open("../prospector_mass_call.cmd", "w", encoding="utf8") as prospector_cmd:
            for myfi in fis:
                if not myfi.cleaned:
                    continue
                relative_name = myfi.file_name.replace("C:\\work\\hisim_github\\HiSim\\", "")
                relative_name_slash = relative_name.replace("\\", "/")
                prospector_cmd.write("prospector " + relative_name + "\n")
                prospector_cmd.write("if %errorlevel% neq 0 exit /b\n")
        with open("../flake8_mass_call.cmd", "w", encoding="utf8") as flake8_cmd:
            for myfi in fis:
                if not myfi.cleaned:
                    continue
                relative_name = myfi.file_name.replace("C:\\work\\hisim_github\\HiSim\\", "")
                relative_name_slash = relative_name.replace("\\", "/")
                flake8_cmd.write("flake8 " + relative_name + " --ignore=E501 --show-source \n")
                flake8_cmd.write("if %errorlevel% neq 0 exit /b\n")
        with open("../pylint_mass_call.cmd", "w", encoding="utf8") as flake8_cmd:
            for myfi in fis:
                if not myfi.cleaned:
                    continue
                relative_name = myfi.file_name.replace("C:\\work\\hisim_github\\HiSim\\", "")
                relative_name_slash = relative_name.replace("\\", "/")
                flake8_cmd.write("pylint " + relative_name + "\n")
                flake8_cmd.write("if %errorlevel% neq 0 exit /b\n")

    def write_one_file_block(self, myfi, row, worksheet1):
        """ Writes the block for a single file to excel. """
        column: int = 1
        column = self.add_to_cell(column=column, row=row, value=myfi.module_name, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.file_name, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.lines, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.python_module_loading_possible,
                                  worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.cleaned, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.authors, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.copyright, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.email, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.license, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.maintainer, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.maintainer, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.status, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.version, worksheet=worksheet1)

        if (len(myfi.classes)) > 0:
            self.add_to_cell(column=column, row=row, value="Classes", worksheet=worksheet1)
            myclass: ClassInformation
            for myclass in myfi.classes:
                row = row + 1
                subcol = column + 1
                subcol = self.add_to_cell(column=subcol, row=row, value=myclass.class_name, worksheet=worksheet1)
                subcol = self.add_to_cell(column=subcol, row=row, value=myclass.lines_of_code, worksheet=worksheet1)
            row = row + 1
        if (len(myfi.methods)) > 0:
            self.add_to_cell(column=column, row=row, value="Methods", worksheet=worksheet1)
            mymethods: MethodInformation
            for mymethods in myfi.methods:
                row = row + 1
                subcol = column + 1
                subcol = self.add_to_cell(column=subcol, row=row, value=mymethods.method_name, worksheet=worksheet1)
            row = row + 1
        if (len(myfi.strings)) > 0:
            self.add_to_cell(column=column, row=row, value="Strings", worksheet=worksheet1)
            mystr: StringInformation
            for mystr in myfi.strings:
                row = row + 1
                subcol = column + 1
                subcol = self.add_to_cell(column=subcol, row=row, value=mystr.string_name, worksheet=worksheet1)
                subcol = self.add_to_cell(column=subcol, row=row, value=mystr.string_value, worksheet=worksheet1)
            row = row + 1
        if (len(myfi.others)) > 0:
            self.add_to_cell(column=column, row=row, value="Others", worksheet=worksheet1)
            otherstuff: OtherMembers
            for otherstuff in myfi.others:
                row = row + 1
                subcol = column + 1
                subcol = self.add_to_cell(column=subcol, row=row, value=otherstuff.member_name, worksheet=worksheet1)
                subcol = self.add_to_cell(column=subcol, row=row, value=otherstuff.variable_type, worksheet=worksheet1)
            row = row + 1
        return row

    def process_one_file(self, filename):  # noqa
        """ Import the module and iterate through its attributes. """
        myfi: FileInformation = FileInformation()

        myfi.file_name = filename
        myfi.module_name = os.path.basename(filename)
        self.analyze_file_directly(filename, myfi)
        module = self.try_to_load_module(myfi)
        if module is None:
            myfi.python_module_loading_possible = False
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
                class_info = ClassInformation()
                class_info.class_name = strname
                if strname in self.existing_classes:
                    raise ValueError("The class " + strname + " exists multiple times.")
                self.existing_classes.append(strname)
                class_info.lines_of_code = len(inspect.getsourcelines(module_member[1]))
                myfi.classes.append(class_info)
                continue
            if str(mytype) == "<class 'module'>":
                continue
            if str(mytype) == "<class 'function'>":
                method_information = MethodInformation(module_member[0])
                myfi.methods.append(method_information)
                continue
            if str(mytype) == "<class 'str'>":
                self.process_string_attribute(myfi, strname, strval)
                continue
            if str(mytype) == "<class 'list'>":
                list_information = ListInformation(strname)
                myfi.lists.append(list_information)
                continue
            if str(mytype) == "<class 'dict'>":
                dii = DictInformation(strname)
                myfi.dicts.append(dii)
                continue
            other_information = OtherMembers(strname, str(mytype))
            myfi.others.append(other_information)
        return myfi

    def try_to_load_module(self, myfi):
        """ Tries to load a file as python module. Returns None if it couldn't be loaded. """
        try:
            spec = importlib.util.spec_from_file_location(myfi.module_name, myfi.file_name)
            module: Optional[ModuleType] = importlib.util.module_from_spec(spec)  # type: ignore
            spec.loader.exec_module(module)  # type: ignore
            sys.modules[myfi.module_name] = module  # type: ignore
        except Exception:  # noqa: broad-except # pylint: disable=broad-except
            module = None
        return module

    def process_string_attribute(self, myfi, strname, strval):
        """ Processes all attributes that are of of the type string. """
        if strname == "__authors__":
            myfi.authors = strval
        elif strname == "__copyright__":
            myfi.copyright = strval
        elif strname == "__email__":
            myfi.email = strval
        elif strname == "__license__":
            myfi.license = strval
        elif strname == "__maintainer__":
            myfi.maintainer = strval
        elif strname == "__status__":
            myfi.status = strval
        elif strname == "__version__":
            myfi.version = strval
        else:
            sti = StringInformation(strname, strval)
            myfi.strings.append(sti)

    def analyze_file_directly(self, filename, myfi):
        """ Analyze all the files and count the lines in each file. Could be expanded with more checks. """
        count = 0
        with open(filename, "r", encoding="utf8") as sourcefile:
            for count, line in enumerate(sourcefile):
                if line.startswith("# clean"):
                    print("found clean tag " + myfi.file_name)
                    myfi.cleaned = True
                pass
        if not myfi.cleaned:
            print("no clean tag " + myfi.file_name)
        myfi.lines = count

    def collect_files(self):
        """ Iterate through the modules in the current package. """
        hisim_dir = Pathlibpath(__file__).resolve().parent.parent
        files = []
        for dirpath, _, filenames in os.walk(hisim_dir):
            for filename in [f for f in filenames if f.endswith(".py")]:
                pypath = os.path.join(dirpath, filename)
                if ".eggs" in pypath:
                    continue
                if ".venv" in pypath:
                    continue
                files.append(pypath)
        return files


if __name__ == "__main__":
    cf = OverviewGenerator()
    cf.run()
