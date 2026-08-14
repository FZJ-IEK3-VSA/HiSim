"""Display grouping and the categorical palette, shared by every report output (W4.7).

The 16+ cost categories of `timeline.CostCategory` fold onto 8 display groups so a fixed
categorical palette covers them and a group keeps its hue across the HTML report, the SVG
charts and the matplotlib PNGs. That mapping is the *one* piece of the seam-4 contract the spec
leaves on the presentation side (§2.4, stated exception): the grouping is a display concept —
the group **sums** are not, and come from `views.fold_categories` / `views.fold_category_matrix`,
into which presentation passes `CATEGORY_TO_GROUP`.

This module exists so `report_plots.py` no longer imports `reporting.py` (and through it the
engine) just to learn what colour "Energy" is. It imports nothing but `timeline.CostCategory`,
which makes it importable from either side of the seam without dragging anything along; the
import-lint in `tests/test_economics_import_lint.py` pins that.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from hisim.economics.timeline import CostCategory


def _build_category_to_group(
    display_groups: List[Tuple[str, Tuple[CostCategory, ...]]]
) -> Dict[CostCategory, int]:
    """Total category -> group-index mapping; categories no group declares land in group 0.

    Inverts the group definitions into a lookup and, crucially, makes it *total*: it iterates over
    the whole `CostCategory` enum rather than only over the declared members, so a category added
    later still has an entry. That is what lets the result be handed to `views.fold_categories`,
    which rejects gaps on purpose, without a future enum member breaking every report.
    """
    declared = {
        category: index for index, (_name, categories) in enumerate(display_groups) for category in categories
    }
    return {category: declared.get(category, 0) for category in CostCategory}


class PresentationStyle:
    """The display grouping and its categorical palette (W4.7).

    Holds the three constants that make every chart in every report output look like one system: the
    ordered display groups, the light and dark colour ramps, and the total category-to-group map.
    Grouping is needed because there are more cost categories than a categorical palette can
    distinguish, and *fixed* ordering is needed because a group that changes hue or stack position
    between the HTML report, its inline SVGs and the matplotlib PNGs makes the three impossible to
    read side by side.

    A reviewer should note the boundary this class sits on: it decides how sums are *labelled and
    coloured*, never what the sums are. `views.fold_categories` computes the folded values and takes
    `CATEGORY_TO_GROUP` as an argument, so a grouping change can never alter a number.
    """

    #: (group label, member categories) in the fixed order every chart stacks and legends them.
    DISPLAY_GROUPS: List[Tuple[str, Tuple[CostCategory, ...]]] = [
        ("Investment & financing", (CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL,
                                    CostCategory.LOAN_INTEREST, CostCategory.LOAN_PRINCIPAL,
                                    CostCategory.LOAN_DISBURSEMENT)),
        ("Feed-in revenue", (CostCategory.FEED_IN_REVENUE,)),
        ("Residual value & anyway credit", (CostCategory.RESIDUAL_VALUE, CostCategory.ANYWAY_COST_CREDIT)),
        ("Subsidies", (CostCategory.SUBSIDY,)),
        ("Replacements", (CostCategory.REPLACEMENT, CostCategory.REPLACEMENT_RESERVE)),
        ("Energy", (CostCategory.ENERGY_WORKING, CostCategory.ENERGY_STANDING,
                    CostCategory.ENERGY_CAPACITY_CHARGE)),
        ("CO2", (CostCategory.ENERGY_CO2_PRICE, CostCategory.CO2_DAMAGE)),
        ("Maintenance & operation", (CostCategory.MAINTENANCE, CostCategory.FIXED_OPERATION,
                                     CostCategory.MODERNIZATION_LEVY)),
    ]

    #: Light-mode categorical slots 1..8 of the dataviz reference palette, in fixed order (never
    #: cycled). The dark-mode set is the same hues at the contrast the dark surface needs.
    GROUP_COLORS_LIGHT = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
    GROUP_COLORS_DARK = ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9", "#e66767", "#d55181", "#d95926"]

    #: **Total** category -> group-index mapping — every `CostCategory` member has an entry, so it
    #: can be handed to `views.fold_categories` (which rejects gaps on purpose) without a category
    #: added later blowing up a report. A category no group declares lands in group 0, the same
    #: fallback `group_of` has always had.
    CATEGORY_TO_GROUP: Dict[CostCategory, int] = _build_category_to_group(DISPLAY_GROUPS)


def group_of(category: CostCategory) -> int:
    """Display-group index of a cost category.

    The accessor the HTML report and the matplotlib companions use to pick a stack segment and its
    colour for a category. It never raises for a valid `CostCategory`, because the underlying map is
    total; the index doubles as the index into `GROUP_COLORS_LIGHT`/`GROUP_COLORS_DARK` and into
    `DISPLAY_GROUPS`, which is what keeps colour, label and stacking order in lockstep.
    """
    return PresentationStyle.CATEGORY_TO_GROUP[category]


def group_name(index: int) -> str:
    """Label of a display group.

    The inverse-direction accessor, used for legends, axis labels and table headers so a group's
    human-readable name is written in exactly one place. Takes the index `group_of` returns; an
    index outside the eight defined groups is a programming error and raises.
    """
    return PresentationStyle.DISPLAY_GROUPS[index][0]
