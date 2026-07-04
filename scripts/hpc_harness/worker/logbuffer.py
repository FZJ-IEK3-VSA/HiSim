"""Worker-side logging plumbing (spec §4.7).

``ConsoleRing`` — rolling buffer of the worker's own console for on-demand snapshots.
``ShipBuffer`` — collects structured records at ``log_ship_level``+ (and every job
failure regardless of level) for batched shipping to the server.
"""

import logging
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple


class ConsoleRing:
    """Bounded ring of console text with a monotonically growing offset."""

    def __init__(self, max_chars: int = 200_000) -> None:
        """Keep at most ``max_chars`` characters of recent output."""
        self.max_chars = max_chars
        self._chunks: Deque[str] = deque()
        self._size = 0
        self.offset = 0  # total characters ever appended

    def append(self, text: str) -> None:
        """Add console output to the ring."""
        if not text:
            return
        self._chunks.append(text)
        self._size += len(text)
        self.offset += len(text)
        while self._size > self.max_chars and len(self._chunks) > 1:
            dropped = self._chunks.popleft()
            self._size -= len(dropped)
        if self._size > self.max_chars:  # one oversized chunk: keep its tail
            tail = "".join(self._chunks)[-self.max_chars:]
            self._chunks.clear()
            self._chunks.append(tail)
            self._size = len(tail)

    def tail(self, max_chars: int = 20_000) -> str:
        """Most recent output (one-shot console snapshot)."""
        return "".join(self._chunks)[-max_chars:]

    def since(self, offset: int) -> Tuple[str, int]:
        """Incremental output after ``offset`` (follow mode); returns (text, new_offset)."""
        text = "".join(self._chunks)
        start = self.offset - len(text)
        if offset <= start:
            return text, self.offset
        return text[offset - start:], self.offset


class RingHandler(logging.Handler):
    """Feeds the worker's own log stream into the console ring."""

    def __init__(self, ring: ConsoleRing) -> None:
        """Attach to ``ring``."""
        super().__init__()
        self.ring = ring
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        """Append the formatted record to the ring."""
        try:
            self.ring.append(self.format(record) + "\n")
        except Exception:  # pylint: disable=broad-except
            pass


class ShipBuffer(logging.Handler):
    """Buffers WARNING+ records (configurable) for batched shipping to the server."""

    def __init__(self, level_name: str = "WARNING", max_records: int = 500) -> None:
        """Buffer records at or above ``level_name``."""
        super().__init__(level=getattr(logging, level_name.upper(), logging.WARNING))
        self._records: Deque[Dict[str, Any]] = deque(maxlen=max_records)

    def emit(self, record: logging.LogRecord) -> None:
        """Queue one structured record."""
        try:
            entry: Dict[str, Any] = {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage()[:2000],
            }
            if record.exc_text:
                entry["traceback"] = record.exc_text[:8000]
            job_id = getattr(record, "job_id", None)
            if job_id is not None:
                entry["job_id"] = job_id
            self._records.append(entry)
        except Exception:  # pylint: disable=broad-except
            pass

    def add_failure(self, job_id: int, message: str, traceback_text: Optional[str], console_tail: str) -> None:
        """Always ship a job failure with its traceback + console tail (spec §4.7)."""
        self._records.append(
            {
                "ts": time.time(),
                "level": "ERROR",
                "logger": "hpc_harness.job",
                "job_id": job_id,
                "message": message[:2000],
                "traceback": ((traceback_text or "") + "\n--- console tail ---\n" + console_tail)[:12000],
            }
        )

    def drain(self) -> List[Dict[str, Any]]:
        """Take all buffered records for shipping."""
        records = list(self._records)
        self._records.clear()
        return records
