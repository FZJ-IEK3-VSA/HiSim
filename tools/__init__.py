"""Standalone developer tools of the HiSim repository.

A package marker and nothing else. The tools below are written to be *run* as scripts, but the
test suite also imports them by their dotted path (`tools.worked_examples.…`) so that a tool and
the tests that consume its output can share one implementation of a protocol instead of
re-deriving it; that dotted path needs `tools` to be a package.

Nothing here is shipped with HiSim: `setup.py` packages `hisim` only, so a tool may depend on
whatever a developer's checkout has without widening the installed dependency set.
"""
