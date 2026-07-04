"""Single writer thread for the core DB (spec §4.1).

``sqlite3`` is blocking and SQLite wants one writer, so every core-DB operation is
funnelled through one dedicated thread. Queued operations are executed in **grouped
batches** with a single commit, so N concurrent lease/report requests cost one fsync,
not N. Each operation runs inside a SAVEPOINT so one failing op rolls back alone
without poisoning the rest of its batch.
"""

import logging
import queue
import sqlite3
import threading
from concurrent.futures import Future
from typing import Any, Callable, Optional

LOGGER = logging.getLogger(__name__)

_MAX_BATCH = 128


class _Op:
    """One queued database operation."""

    __slots__ = ("fn", "future", "raw")

    def __init__(self, fn: Callable[[sqlite3.Connection], Any], raw: bool) -> None:
        self.fn = fn
        self.future: Future = Future()
        self.raw = raw


class DbWriter:
    """Serializes all mutations (and reads) of the core DB onto one thread."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Take ownership of ``conn``; only the writer thread touches it from now on."""
        self._conn = conn
        self._conn.isolation_level = None  # autocommit; we manage BEGIN/COMMIT explicitly
        self._queue: "queue.Queue[Optional[_Op]]" = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="db-writer", daemon=True)
        self._closed = False
        self._thread.start()

    def submit(self, fn: Callable[[sqlite3.Connection], Any], raw: bool = False) -> Future:
        """Queue ``fn(conn)`` for execution on the writer thread; returns a Future.

        ``raw=True`` runs the operation outside any transaction (needed for
        ``VACUUM INTO`` snapshots, which refuse to run inside one).
        """
        if self._closed:
            raise RuntimeError("DbWriter is closed")
        op = _Op(fn, raw)
        self._queue.put(op)
        return op.future

    def call(self, fn: Callable[[sqlite3.Connection], Any], timeout: float = 60.0, raw: bool = False) -> Any:
        """Run ``fn(conn)`` on the writer thread and return its result (blocking)."""
        return self.submit(fn, raw=raw).result(timeout=timeout)

    def _loop(self) -> None:
        while True:
            op = self._queue.get()
            if op is None:
                return
            batch = [op]
            while len(batch) < _MAX_BATCH:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is None:
                    self._run_batch(batch)
                    return
                batch.append(nxt)
            self._run_batch(batch)

    def _run_batch(self, batch: list) -> None:
        raw_ops = [op for op in batch if op.raw]
        txn_ops = [op for op in batch if not op.raw]
        if txn_ops:
            try:
                self._conn.execute("BEGIN")
            except sqlite3.Error:
                LOGGER.exception("BEGIN failed; running ops without explicit transaction")
            for i, op in enumerate(txn_ops):
                savepoint = f"op_{i}"
                try:
                    self._conn.execute(f"SAVEPOINT {savepoint}")
                    result = op.fn(self._conn)
                    self._conn.execute(f"RELEASE {savepoint}")
                    op.future.set_result(result)
                except Exception as exc:  # pylint: disable=broad-except
                    try:
                        self._conn.execute(f"ROLLBACK TO {savepoint}")
                        self._conn.execute(f"RELEASE {savepoint}")
                    except sqlite3.Error:
                        pass
                    op.future.set_exception(exc)
            try:
                self._conn.execute("COMMIT")
            except sqlite3.Error:
                LOGGER.exception("COMMIT failed; rolling back batch")
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
        for op in raw_ops:
            try:
                op.future.set_result(op.fn(self._conn))
            except Exception as exc:  # pylint: disable=broad-except
                op.future.set_exception(exc)

    def close(self) -> None:
        """Drain the queue and stop the writer thread."""
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._thread.join(timeout=30)
        try:
            self._conn.close()
        except sqlite3.Error:
            pass
