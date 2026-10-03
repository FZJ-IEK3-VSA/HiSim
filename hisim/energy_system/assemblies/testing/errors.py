"""The named errors of the assembly test harness (``assemblies_spec.md`` §9.4).

The harness distinguishes what is wrong with an assembly from what is wrong with the harness or
its inputs. A check an assembly fails is not raised where it is found: it is recorded, every other
sample still runs, and :class:`~.report.AssemblyTestFailure` lists all of them at the end. The
errors here are raised at once, because nothing sensible can follow them: a test-partner registry
that does not read, a port no registered partner serves, a sample the harness built against the
assembly's own constraints, a shard that holds nothing.
"""

from __future__ import annotations


class AssemblyHarnessError(Exception):
    """Base of every error the harness raises itself."""


class TestPartnerRegistryError(AssemblyHarnessError):
    """A ``test_partners.yaml`` that does not read: a malformed entry, a duplicate, an unknown reference."""

    # The class name starts with "Test", which pytest would collect if a test module imported it.
    __test__ = False


class TestPartnerMissingError(AssemblyHarnessError):
    """A port of an assembly under test that no registered test partner serves."""

    __test__ = False


class SampleConstructionError(AssemblyHarnessError):
    """A deterministic sample the assembly's declarations do not admit (a contract problem, named)."""


class SamplerError(AssemblyHarnessError):
    """A sample the harness built violates a constraint of the assembly: a bug of the harness."""


class HarnessUsageError(AssemblyHarnessError):
    """The harness was asked for something it cannot do: an empty library, an empty shard, a bad tier."""
