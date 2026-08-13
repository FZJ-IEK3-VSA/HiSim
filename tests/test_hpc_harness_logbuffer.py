"""Unit tests for the HPC-harness worker console ring and error reporter."""

import pytest

from hpc_harness.worker.logbuffer import ConsoleRing, ErrorReporter

pytestmark = pytest.mark.hpcharness


# --------------------------------------------------------------------- console ring


def test_error_reporter_captures_logged_exceptions_and_explicit_adds():
    """The reporter captures ERROR-level log exceptions and explicit adds, then drains once."""
    import logging

    reporter = ErrorReporter("worker")
    logger = logging.getLogger("test.errorreporter")
    logger.addHandler(reporter)
    logger.setLevel(logging.DEBUG)
    try:
        logger.warning("just a warning")  # below ERROR: ignored
        try:
            raise ValueError("kaboom")
        except ValueError:
            logger.exception("caught it")
    finally:
        logger.removeHandler(reporter)

    reporter.add(message="job 7 failed", error_type="JobFailure",
                 traceback_text="Traceback...\nBoom", job_id=7)

    records = reporter.drain()
    assert len(records) == 2  # the warning was not captured
    logged, explicit = records
    assert logged["error_type"] == "ValueError"
    assert "kaboom" in logged["traceback"] and logged["source"] == "worker"
    assert explicit["job_id"] == 7 and explicit["error_type"] == "JobFailure"
    assert not reporter.drain()  # drained


def test_console_ring_tail_and_incremental_offsets():
    """The console ring serves incremental slices by offset and a bounded tail on overflow."""
    ring = ConsoleRing(max_chars=20)
    ring.append("aaaaa")
    text, offset = ring.since(0)
    assert text == "aaaaa" and offset == 5
    ring.append("bbbbb")
    text, offset = ring.since(offset)
    assert text == "bbbbb" and offset == 10
    ring.append("c" * 30)  # overflows the ring
    assert ring.tail() == "c" * 20
    text, _ = ring.since(offset)
    assert set(text) == {"c"}
