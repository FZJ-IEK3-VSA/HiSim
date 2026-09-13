"""Tests for :class:`hisim.result_path_provider.SingletonMeta`, the thread-safe singleton metaclass.

The metaclass has exactly one production user, ``ResultPathProviderSingleton``, and lives in that
module. What is tested here is the metaclass contract itself -- one instance per class, and one
instance even when the very first accesses race -- so the subject is a throwaway class defined
inside each test. Defining it locally also keeps the tests out of the shared ``_instances`` cache:
a class that has never been instantiated has no entry there, so the creation path can be stressed
without evicting the result path provider another test may be relying on.
"""

# clean

import threading
from typing import Any, List

import pytest
from pytest import MarkDecorator

from hisim.result_path_provider import ResultPathProviderSingleton, SingletonMeta

pytestmark: MarkDecorator = pytest.mark.base


def test_singleton_returns_same_instance() -> None:
    """Two constructions of a ``SingletonMeta`` class return the very same instance.

    A plain class is constructed alongside as the control: without the metaclass, two
    constructions must yield two distinct objects.
    """

    # https://medium.com/analytics-vidhya/how-to-create-a-thread-safe-singleton-class-in-python-822e1170a7f6
    class Singleton(metaclass=SingletonMeta):

        """A class that exists only to be constructed twice."""

    assert Singleton() is Singleton()

    # Sanity check - a non-singleton class should create two separate instances

    class NonSingleton:

        """Just a class to show the difference between a singleton and a non-singleton."""

    assert NonSingleton() is not NonSingleton()

    # The one production user has the property too.
    assert ResultPathProviderSingleton() is ResultPathProviderSingleton()


def test_singleton_concurrent_first_access_is_thread_safe() -> None:
    """Verify double-checked locking: concurrent first accesses yield one instance.

    ``SingletonMeta.__call__`` uses double-checked locking so that the lock is only acquired on
    the creation path. This test stresses that path: the class is fresh, so every one of the
    threads races through the outer (lock-free) check, and the result must still be a single
    shared instance (no duplicate construction).
    """

    class Singleton(metaclass=SingletonMeta):

        """A never-yet-instantiated class, so all threads start on the creation path."""

    results: List[Any] = []
    num_threads = 32

    def worker() -> None:
        results.append(Singleton())

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == num_threads
    first = results[0]
    assert all(r is first for r in results), "concurrent first access created distinct instances"

    # A subsequent call must still return the same instance (fast path).
    assert Singleton() is first
