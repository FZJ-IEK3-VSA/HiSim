"""ETHOS.HiSim MPI HPC harness.

A resilient master/worker work-distribution framework for running large batches
(10,000+) of HiSim simulations across many compute nodes.

Topology: one MPI rank per node. Rank 0 owns a SQLite task database and dispatches
work; every rank (including rank 0, optionally) runs a local pool of HiSim
simulations as isolated subprocesses, gated by available memory. See ``__main__``
for the command-line interface and the package README for the full design.
"""
