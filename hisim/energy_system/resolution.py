"""Where the record of one resolved aggregator feed lives, and why it is not here.

A resolved feed — the participant, the port measured, the channel its tags selected and the
optional back-channel — is created by this package and consumed by a component: an aggregator is
handed one and grows the ports it names. That makes it a type both layers share, so it lives in
:mod:`hisim.config.channels`, below the components, together with the channel declarations it
refers to. Were it to live here, every component import would execute this package and load the
file format's reader with it.

This module re-exports the three records so that the stages that produce them — feed resolution,
the aggregator-port checks and the wiring — keep reading them from the package they belong to.
The machinery that produces them is :mod:`hisim.energy_system.feed_resolution`.
"""

# clean

from hisim.config.channels import (
    ResolvedDispatch,
    ResolvedDynamicConnection,
    ResolvedDynamicWire,
)

__all__ = ["ResolvedDispatch", "ResolvedDynamicConnection", "ResolvedDynamicWire"]
