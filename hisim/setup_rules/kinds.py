"""The kind of a setup: the one word a setup declares about itself, from which the setup rules decide what runs."""

from __future__ import annotations

from enum import Enum


class SetupKind(str, Enum):
    """What one simulated system is, as its author declares it.

    A setup declares exactly one kind: an energy-system file as its top-level key ``setup_kind``, a Python setup
    with ``my_sim.declare_setup_kind(SetupKind.RESIDENTIAL)``. Every setup rule applies to every kind unless the rule
    names the kind in its ``WAIVED_FOR`` with a reason. Nothing checks that a kind fits its setup: the kind is a
    switch that selects waivers, and what it says is the author's responsibility. The values are the lowercase words
    a file spells, for example ``setup_kind: demo``.
    """

    #: A demonstration or a test rig: it shows a mechanism, and its numbers are no statement about a house or a plant.
    DEMO = "demo"
    #: A building people live in: a household occupancy, apartments, domestic hot water per person.
    RESIDENTIAL = "residential"
    #: A building people work in: usage profiles by zone type, no apartments.
    COMMERCIAL = "commercial"
    #: A system without a building: a generator, a storage, a converter and the meters around them.
    PLANT = "plant"
