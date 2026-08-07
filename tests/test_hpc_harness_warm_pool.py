"""Unit tests for the HPC-harness worker warm-pool slot sizing."""

import pytest

from hpc_harness.worker.warm_pool import compute_max_slots

pytestmark = pytest.mark.hpcharness


# ---------------------------------------------------------------------- slot sizing


def test_compute_max_slots_is_min_of_memory_and_cores():
    """Slot count is the min of memory- and core-derived limits, capped and floored at 1."""
    # 256 GB node, 10 GB/job, 12 GB headroom -> 24 memory slots; 128 cores -> min = 24.
    assert compute_max_slots(10.0, 12.0, cores=128, cores_per_job=1, reserved_cores=0,
                             total_mem_gb=256.0) == 24
    # 4-core node with plenty of memory: cores bind.
    assert compute_max_slots(1.0, 1.0, cores=4, cores_per_job=1, reserved_cores=1,
                             total_mem_gb=256.0) == 3
    # Configured cap wins when smaller; floor of 1 always holds.
    assert compute_max_slots(10.0, 12.0, cores=128, cores_per_job=1, reserved_cores=0,
                             configured=8, total_mem_gb=256.0) == 8
    assert compute_max_slots(500.0, 12.0, cores=1, cores_per_job=1, reserved_cores=0,
                             total_mem_gb=16.0) == 1
