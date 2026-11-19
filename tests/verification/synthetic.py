"""A small made-up suite and its made-up ranges, shared by the comparison and the report tests.

The bounds are in hundreds of MWh so that none can be mistaken for a range of the standard.
"""

from typing import Any, Dict, List, Optional

from hisim.verification import compare


def suite_mapping(register: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """A small suite: two base ranges, two difference ranges, one reversed, one not modelled."""
    return {
        "suite": "synthetic",
        "reference_file": "ranges.json",
        "cases": ["1", "2", "3"],
        "groups": {
            "light": {
                "ranges_total": 5,
                "minimum_required": 3,
                "range_cases": [
                    {"case": "1", "metric": "heating"},
                    {"case": "1", "metric": "cooling"},
                    {"case": "2", "parent": "1", "metric": "heating"},
                    {"case": "3", "parent": "1", "metric": "heating", "reverse": True},
                    {"case": "4", "parent": "1", "metric": "cooling", "status": "not_modelled"},
                ],
            }
        },
        "outputs": {},
        "deviation_register": register or [],
    }


def ranges(**overrides: Any) -> compare.ReferenceRanges:
    """Made-up bounds for the small suite, in hundreds of MWh so that none can be mistaken for a real one."""
    bounds: Dict[str, Any] = {
        "1": {"heating": [400.0, 600.0], "cooling": [200.0, 300.0]},
        "2-1": {"heating": [-100.0, 100.0], "cooling": None},
        "1-3": {"heating": [50.0, 150.0], "cooling": None},
        "4-1": {"heating": None, "cooling": [10.0, 110.0]},
    }
    bounds.update(overrides)
    return compare.ReferenceRanges.from_mapping({"units": "MWh/year", "ranges": bounds})
