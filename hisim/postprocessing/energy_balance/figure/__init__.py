"""The matplotlib Sankey figure of a run's ``overall`` energy view (the matplotlib Sankey spec).

The graph half reads a run's inputs (``inputs``), folds the view into the drawn graph with roles, chain and trees
(``graph``), gives every flow its side (``orientation``), computes the values on the figure (``metrics``) and holds
the presentation table (``style``). The layout builds the library's parameters (``parameters``) and repairs them
round by round (``search``); the renderer draws them with ``matplotlib.sankey`` (``drawing``), joins the
cross-links with connectors (``connectors``), places the labels and checks for overlaps (``labels``, ``texts``) and
writes the figure and the layout file (``render``); ``pins`` reads a complete pin file drawn instead.
``draw_overall`` is the entry the post-processing and the command call.
"""

from pathlib import Path
from typing import List, Optional, Tuple

from hisim.postprocessing.energy_balance.figure.graph import DrawnGraph, GraphBuilder, Role
from hisim.postprocessing.energy_balance.figure.inputs import SankeyInputError, SankeyInputs
from hisim.postprocessing.energy_balance.figure.parameters import LayoutConstants
from hisim.postprocessing.energy_balance.figure.pins import PinFile
from hisim.postprocessing.energy_balance.figure.render import SankeyFigure
from hisim.postprocessing.energy_balance.figure.search import Fault, Layout, PlacementSearch


class FigureFiles:
    """Draws a run's figure and writes its files."""

    #: The sub-directory of a result directory the figure goes to (beside the plotly Sankeys).
    SUBDIRECTORY: str = "energy_sankeys"

    @staticmethod
    def draw_overall(inputs: SankeyInputs, directory: Path, pins: Optional[Path] = None, constants: Optional[LayoutConstants] = None) -> Tuple[Layout, List[Path]]:
        """Lay out a run's overall view (or draw it from a pin file) and write the figure and the layout file.

        Example: ``FigureFiles.draw_overall(SankeyInputs.from_result_directory(result), result / "energy_sankeys", pins)``.

        Args:
            inputs: The run's inputs.
            directory: Where the files go; created if missing.
            pins: A complete pin file (a hand layout) drawn instead of the computed layout, or None.
            constants: The layout constants (the defaults when omitted).

        Returns:
            The layout (its ``faults`` list what failed) and the paths written; nothing is written for a run whose
            view has nothing to draw.
        """
        constants = constants or LayoutConstants()
        layout = PinFile.draw(inputs, pins, constants) if pins is not None else PlacementSearch.run(inputs, constants)
        if layout.nothing_to_draw:
            return layout, []
        return layout, SankeyFigure.write(layout, directory, constants)

    @staticmethod
    def draw_run(result_directory: Path) -> List[str]:
        """Draw a finished run's figure into its ``energy_sankeys`` directory; return the faults, never raise (D15).

        The post-processing calls this for ``PLOT_SANKEY`` after the balance files are written. Every failure -- a
        layout fault, a missing input, an error inside the renderer -- is a sentence in the returned list and in
        ``energy_sankeys/overall.layout_error.json``, which the golden check reads; the run itself never fails on
        its picture.

        Example: ``FigureFiles.draw_run(Path(simulation_parameters.result_directory))`` returns ``[]`` for a clean
        figure.

        Args:
            result_directory: The run's result directory, holding the balance report and the views.

        Returns:
            The faults as sentences; empty when the figure was drawn without one or the view has nothing to draw.
        """
        directory = result_directory / FigureFiles.SUBDIRECTORY
        try:
            layout, _ = FigureFiles.draw_overall(SankeyInputs.from_result_directory(result_directory), directory)
        except Exception as error:  # pylint: disable=broad-except
            faults = [f"error: the figure could not be drawn: {type(error).__name__}: {error}"]
            SankeyFigure.write_faults(directory, faults, [])
            return faults
        return SankeyFigure.fault_sentences(layout)


draw_overall = FigureFiles.draw_overall

__all__ = ["DrawnGraph", "Fault", "FigureFiles", "GraphBuilder", "Layout", "LayoutConstants", "Role", "SankeyInputError", "SankeyInputs", "draw_overall"]
