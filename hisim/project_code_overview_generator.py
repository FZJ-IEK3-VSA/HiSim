""" Makes an overview of all the components and collects important information for each module. """
# clean
from types import ModuleType
from typing import List, Optional, Set, TypedDict, Union
from pathlib import Path as Pathlibpath
import importlib
import importlib.machinery
import importlib.util
from dataclasses import dataclass, field
import inspect
import logging
import os
import sys
from openpyxl import Workbook  # type: ignore
from openpyxl.worksheet.worksheet import Worksheet  # type: ignore

# todo: check for print commands in all files and fail
# todo: check for duplicate class names and fail if different components have the same class name

__authors__ = "Noah Pflugradt, Maximilian Hillen"
__copyright__ = "Copyright 2021-2022, FZJ-IEK-3"
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"

BUILT_IN_ATTRIBUTES: List[str] = [
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

    """Stores information about classes."""

    class_name: str = ""
    lines_of_code: int = 0


@dataclass
class StringInformation:

    """Stores information for strings."""

    string_name: str = ""
    string_value: str = ""


@dataclass
class MethodInformation:

    """Stores information about methods."""

    method_name: str = ""


@dataclass
class ListInformation:

    """Stores information for all lists."""

    list_name: str = ""


@dataclass
class DictInformation:

    """Stores information for all dictionaries."""

    dict_name: str = ""


@dataclass
class OtherMembers:

    """Stores information for all other members of the files."""

    member_name: str = ""
    variable_type: str = ""


@dataclass
class FileInformation:

    """Stores the information about a single file."""

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


class ToolScriptConfig(TypedDict, total=False):
    """Keyword arguments for :meth:`OverviewGenerator._write_tool_script`.

    Typing the per-tool configuration dicts emitted by
    :meth:`OverviewGenerator.write_clean_files` so that ``**config``
    unpacking is statically checked instead of falling back to ``Any``.
    """

    output_path: str
    command_template: str
    use_forward_slashes: bool
    extra_lines: Optional[List[str]]


class OverviewGenerator:

    """Generates an overview of all modules."""

    def __init__(self) -> None:
        """Initialize the OverviewGenerator.

        Sets up an empty set to track class names encountered during
        file processing, used to detect and raise an error for duplicate
        class definitions across modules.
        """
        self.existing_classes: Set[str] = set()

    def add_to_cell(self, column: int, row: int, value: Union[str, int, bool], worksheet: Worksheet) -> int:
        """Write a single value into the Excel worksheet.

        Writes ``value`` into the cell at ``(column, row)`` of ``worksheet`` and
        returns the next column index (``column + 1``) so that consecutive
        calls can be chained to fill a row left to right.

        Args:
            column (int): The 1-based column index of the target cell.
            row (int): The 1-based row index of the target cell.
            value (Union[str, int, bool]): The value to write into the cell
                (a module/file name, line count, boolean flag, or metadata
                string).
            worksheet (Worksheet): The openpyxl worksheet to write into.

        Returns:
            int: The next free column index (``column + 1``).
        """
        worksheet.cell(column=column, row=row, value=value)
        column = column + 1
        return column

    def run(self) -> None:
        """Generate the full module overview and linting tool scripts.

        Orchestrates the overview generation by collecting every ``.py`` file
        in the ``hisim`` package, processing each one into a
        :class:`FileInformation`, removing any pre-existing
        ``components_information.xlsx`` output file, writing all collected
        metadata into a fresh workbook saved as
        ``components_information.xlsx``, and finally emitting the linting tool
        scripts via :meth:`write_clean_files`.

        Returns:
            None
        """
        dest_filename = "components_information.xlsx"

        # collect file names
        python_files = self.collect_files()

        # read all the information
        fis = [self.process_one_file(filename) for filename in python_files]

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

    @staticmethod
    def _write_tool_script(
        fis: List[FileInformation],
        output_path: str,
        command_template: str,
        use_forward_slashes: bool = False,
        extra_lines: Optional[List[str]] = None,
    ) -> None:
        """Write a tool-specific script file for cleaned files."""
        with open(output_path, "w", encoding="utf8") as fh:
            for myfi in fis:
                if not myfi.cleaned:
                    continue
                relative_name = myfi.file_name.replace("C:\\work\\hisim_github\\HiSim\\", "")
                path = relative_name.replace("\\", "/") if use_forward_slashes else relative_name
                fh.write(command_template.format(path=path) + "\n")
                if extra_lines:
                    for line in extra_lines:
                        fh.write(line + "\n")

    def write_clean_files(self, fis: List[FileInformation]) -> None:
        """Emit linting tool scripts listing only the cleaned files.

        Writes five script files (``flake8_calls.txt``,
        ``prospector_calls.txt``, ``prospector_mass_call.cmd``,
        ``flake8_mass_call.cmd`` and ``pylint_mass_call.cmd``), each containing
        one command per file whose :attr:`FileInformation.cleaned` flag is
        ``True``.

        Args:
            fis (List[FileInformation]): The file information records produced
                by :meth:`process_one_file`. Only records with ``cleaned`` set
                to ``True`` are written to the scripts.

        Returns:
            None
        """
        _cmd_exit = "if %errorlevel% neq 0 exit /b"
        configs: List[ToolScriptConfig] = [
            {
                "output_path": "../flake8_calls.txt",
                "command_template": "        flake8 {path} --count --select=E9,F63,F7,F82,E800 --show-source --statistics",
                "use_forward_slashes": True,
            },
            {
                "output_path": "../prospector_calls.txt",
                "command_template": "        prospector {path}",
                "use_forward_slashes": True,
            },
            {
                "output_path": "../prospector_mass_call.cmd",
                "command_template": "prospector {path}",
                "extra_lines": [_cmd_exit],
            },
            {
                "output_path": "../flake8_mass_call.cmd",
                "command_template": "flake8 {path} --ignore=E501 --show-source ",
                "extra_lines": [_cmd_exit],
            },
            {
                "output_path": "../pylint_mass_call.cmd",
                "command_template": "pylint {path}",
                "extra_lines": [_cmd_exit],
            },
        ]
        for config in configs:
            OverviewGenerator._write_tool_script(fis, **config)

    def write_one_file_block(self, myfi: FileInformation, row: int, worksheet1: Worksheet) -> int:
        """Write a single file's metadata block into the worksheet.

        Starting at ``row``, writes the file's metadata followed by nested
        sections for its classes, methods, strings, and other members (each
        section header on ``row`` followed by one sub-row per member).

        Args:
            myfi (FileInformation): The file whose metadata and members are
                written.
            row (int): The worksheet row at which to start the block.
            worksheet1 (Worksheet): The worksheet to write into.

        Returns:
            int: The next free row after the block (the row immediately
            following the last written row), ready for the next block.
        """
        column: int = 1
        column = self.add_to_cell(column=column, row=row, value=myfi.module_name, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.file_name, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.lines, worksheet=worksheet1)
        column = self.add_to_cell(
            column=column, row=row, value=myfi.python_module_loading_possible, worksheet=worksheet1
        )
        column = self.add_to_cell(column=column, row=row, value=myfi.cleaned, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.authors, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.copyright, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.email, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.license, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.maintainer, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.maintainer, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.status, worksheet=worksheet1)
        column = self.add_to_cell(column=column, row=row, value=myfi.version, worksheet=worksheet1)

        if myfi.classes:
            self.add_to_cell(column=column, row=row, value="Classes", worksheet=worksheet1)
            myclass: ClassInformation
            for myclass in myfi.classes:
                row = row + 1
                subcol = column + 1
                subcol = self.add_to_cell(column=subcol, row=row, value=myclass.class_name, worksheet=worksheet1)
                subcol = self.add_to_cell(column=subcol, row=row, value=myclass.lines_of_code, worksheet=worksheet1)
            row = row + 1
        if myfi.methods:
            self.add_to_cell(column=column, row=row, value="Methods", worksheet=worksheet1)
            mymethods: MethodInformation
            for mymethods in myfi.methods:
                row = row + 1
                subcol = column + 1
                subcol = self.add_to_cell(column=subcol, row=row, value=mymethods.method_name, worksheet=worksheet1)
            row = row + 1
        if myfi.strings:
            self.add_to_cell(column=column, row=row, value="Strings", worksheet=worksheet1)
            mystr: StringInformation
            for mystr in myfi.strings:
                row = row + 1
                subcol = column + 1
                subcol = self.add_to_cell(column=subcol, row=row, value=mystr.string_name, worksheet=worksheet1)
                subcol = self.add_to_cell(column=subcol, row=row, value=mystr.string_value, worksheet=worksheet1)
            row = row + 1
        if myfi.others:
            self.add_to_cell(column=column, row=row, value="Others", worksheet=worksheet1)
            otherstuff: OtherMembers
            for otherstuff in myfi.others:
                row = row + 1
                subcol = column + 1
                subcol = self.add_to_cell(column=subcol, row=row, value=otherstuff.member_name, worksheet=worksheet1)
                subcol = self.add_to_cell(column=subcol, row=row, value=otherstuff.variable_type, worksheet=worksheet1)
            row = row + 1
        return row

    def process_one_file(self, filename: str) -> FileInformation:  # noqa
        """Build the :class:`FileInformation` for a single source file.

        Creates a :class:`FileInformation` keyed by ``filename``, counts its
        lines and detects the ``# clean`` tag via
        :meth:`analyze_file_directly`, loads it as a module via
        :meth:`try_to_load_module`, and when loading succeeds inspects the
        module's own members with :func:`inspect.getmembers` to populate the
        file's classes, methods, strings, lists, dicts and other members.

        Args:
            filename (str): Path to the ``.py`` file to process.

        Returns:
            FileInformation: The populated file information record. If the
            module could not be loaded, ``python_module_loading_possible`` is
            set to ``False`` and only the directly-analyzed data is present.
        """
        myfi: FileInformation = FileInformation()

        myfi.file_name = filename
        myfi.module_name = os.path.basename(filename)
        self.analyze_file_directly(filename, myfi)
        module = self.try_to_load_module(myfi)
        if module is None:
            myfi.python_module_loading_possible = False
            return myfi

        python_module_name = module.__name__
        for name, member in inspect.getmembers(module):
            if hasattr(member, "__module__"):
                # this is an import from another module, therefore skip
                if str(python_module_name) != str(member.__module__):
                    continue
            if str(name) in BUILT_IN_ATTRIBUTES:
                continue
            strname = str(name)
            strval = str(member)
            if inspect.isclass(member):
                class_info = ClassInformation()
                class_info.class_name = strname
                if strname in self.existing_classes:
                    logging.getLogger(__name__).warning(
                        "The class %s exists multiple times.", strname
                    )
                else:
                    self.existing_classes.add(strname)
                try:
                    class_info.lines_of_code = len(inspect.getsourcelines(member))
                except (OSError, TypeError):
                    # inspect.getsourcelines raises OSError("could not find
                    # class definition") for classes created without a literal
                    # ``class`` statement (e.g. collections.namedtuple or the
                    # functional Enum API) and TypeError when the source file
                    # cannot be located at all. Report the problem visibly and
                    # record the class with a zero line count instead of
                    # aborting the entire overview generation for a single
                    # unintrospectable class.
                    logging.getLogger(__name__).warning(
                        "Could not determine source lines for class %s in %s.",
                        strname,
                        myfi.file_name,
                    )
                    class_info.lines_of_code = 0
                myfi.classes.append(class_info)
                continue
            if inspect.ismodule(member):
                continue
            if inspect.isfunction(member):
                method_information = MethodInformation(name)
                myfi.methods.append(method_information)
                continue
            if isinstance(member, str):
                self.process_string_attribute(myfi, strname, strval)
                continue
            if isinstance(member, list):
                list_information = ListInformation(strname)
                myfi.lists.append(list_information)
                continue
            if isinstance(member, dict):
                dii = DictInformation(strname)
                myfi.dicts.append(dii)
                continue
            other_information = OtherMembers(strname, str(type(member)))
            myfi.others.append(other_information)
        return myfi

    def try_to_load_module(self, myfi: FileInformation) -> Optional[ModuleType]:
        """Load a file as a Python module.

        Builds an importlib spec from ``myfi.file_name`` under the key
        ``myfi.module_name``, executes the module, and registers it in
        :data:`sys.modules` (mutating it in place) so subsequent imports can
        reuse it.

        Args:
            myfi (FileInformation): Carries ``file_name``/``module_name``
                used to locate and key the module.

        Returns:
            Optional[ModuleType]: The loaded module, or ``None`` if import or
            execution failed (a warning is logged).
        """
        try:
            spec: Optional[importlib.machinery.ModuleSpec] = importlib.util.spec_from_file_location(
                myfi.module_name, myfi.file_name
            )
            if spec is None or spec.loader is None:
                # spec_from_file_location returns None for paths it cannot
                # resolve to a loader. Fail loudly with a clear message
                # instead of letting module_from_spec raise a confusing
                # AttributeError that the broad except below would mask.
                logging.getLogger(__name__).warning(
                    "Could not create import spec for %s: spec or loader is None.",
                    myfi.file_name,
                )
                return None
            module: ModuleType = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules[myfi.module_name] = module
            return module
        except BaseException as e:  # noqa: BLE001 # pylint: disable=broad-except
            # ``BaseException`` (not just ``Exception``) is intentional: a module
            # may raise non-Exception outcomes while it executes. ``SystemExit``
            # comes from files like setup.py that call setup() at import time;
            # pytest raises ``Failed``/``Skipped``/``Exit`` (subclasses of
            # ``BaseException`` via ``OutcomeException``) -- e.g. a test module
            # that accesses an unregistered ``pytest.mark`` under
            # ``--strict-markers``. Such a file is simply not importable in this
            # context, so report it visibly and skip it instead of letting the
            # outcome abort the entire overview generation. ``KeyboardInterrupt``
            # is re-raised so a user Ctrl-C is never swallowed.
            if isinstance(e, KeyboardInterrupt):
                raise
            logging.getLogger(__name__).warning(
                "Could not load %s as a module: %s: %s",
                myfi.file_name,
                type(e).__name__,
                e,
            )
            return None

    def process_string_attribute(self, myfi: FileInformation, strname: str, strval: str) -> None:
        """Route a module-level string attribute onto ``myfi``.

        Recognized dunder metadata attributes (``__authors__``,
        ``__copyright__``, ``__email__``, ``__license__``, ``__maintainer__``,
        ``__status__``, ``__version__``) are mapped onto the corresponding
        :class:`FileInformation` fields. Every other string attribute is
        wrapped in a :class:`StringInformation` and appended to
        ``myfi.strings``.

        Args:
            myfi (FileInformation): The file information to update in place.
            strname (str): The attribute name (e.g. ``__authors__``).
            strval (str): The attribute's string value.

        Returns:
            None
        """
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

    def analyze_file_directly(self, filename: str, myfi: FileInformation) -> None:
        """Analyze a source file without importing it.

        Reads ``filename`` line by line, counts the number of lines, and sets
        ``myfi.cleaned`` to ``True`` if a line starting with ``# clean`` is
        found. The line count and cleaned flag are written back onto ``myfi``
        in place.

        Args:
            filename (str): Path to the ``.py`` file to analyze.
            myfi (FileInformation): The file information to mutate in place.

        Returns:
            None
        """
        count = 0
        with open(filename, "r", encoding="utf8") as sourcefile:
            for count, line in enumerate(sourcefile):
                if line.startswith("# clean"):
                    print(f"found clean tag {myfi.file_name}")
                    myfi.cleaned = True
        if not myfi.cleaned:
            print(f"no clean tag {myfi.file_name}")
        myfi.lines = count

    def collect_files(self) -> List[str]:
        """Collect every ``.py`` file in the ``hisim`` package.

        Walks the ``hisim`` package directory (the parent directory of this
        file) and returns the path of every ``.py`` file found,
        skipping any path containing ``.eggs`` or ``.venv``.

        ``.venv`` and ``.eggs`` directories are pruned from ``os.walk`` in
        place so the walk never descends into them (a virtualenv can hold
        thousands of ``.py`` files); the surviving candidates are still
        checked against the full joined path so that a file name itself
        containing ``.eggs``/``.venv`` is rejected as before.

        Returns:
            List[str]: The collected ``.py`` file paths.
        """
        hisim_dir = Pathlibpath(__file__).resolve().parent
        result: List[str] = []
        for dirpath, dirnames, filenames in os.walk(hisim_dir):
            # Prune dirnames in place so os.walk skips these subtrees entirely
            # instead of enumerating (and then discarding) every file within.
            dirnames[:] = [d for d in dirnames if ".eggs" not in d and ".venv" not in d]
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                full = os.path.join(dirpath, filename)
                if ".eggs" not in full and ".venv" not in full:
                    result.append(full)
        return result


if __name__ == "__main__":
    cf = OverviewGenerator()
    cf.run()
