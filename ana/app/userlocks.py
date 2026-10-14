"""Per-user reader/writer locks (in-process; v1 runs one worker).

* shared — anything that drives one of the person's journeys (act, pending,
  resume, end, debrief, view) or reads their shards (export);
* exclusive — what changes the set of journeys / the identity or removes
  the account: create, onboarding, identity edit, account deletion.

So a deletion waits for every in-flight request of that person and no new
one starts until it has committed; a creation's active-journey check and
insert cannot interleave with another's. Writers are preferred (a waiting
deletion blocks new readers). Lock order everywhere: user lock, then journey
locks. Never take a user lock twice in one request.
"""
from __future__ import annotations

import threading
import time
import weakref
from contextlib import contextmanager
from typing import Iterator


class UserBusy(Exception):
    """The user's lock could not be taken in time."""


class RWLock:
    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0

    def _wait(self, pred, deadline: float) -> bool:
        while not pred():
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            self._cond.wait(left)
        return True

    def acquire_read(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._cond:
            if not self._wait(lambda: not self._writer and not self._writers_waiting, deadline):
                return False
            self._readers += 1
            return True

    def release_read(self) -> None:
        with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._cond:
            self._writers_waiting += 1
            try:
                if not self._wait(lambda: not self._writer and self._readers == 0, deadline):
                    return False
                self._writer = True
                return True
            finally:
                self._writers_waiting -= 1
                self._cond.notify_all()

    def release_write(self) -> None:
        with self._cond:
            self._writer = False
            self._cond.notify_all()


class UserLocks:
    def __init__(self) -> None:
        self._locks: "weakref.WeakValueDictionary[str, RWLock]" = weakref.WeakValueDictionary()
        self._mu = threading.Lock()

    def _get(self, uid: str) -> RWLock:
        with self._mu:
            lk = self._locks.get(uid)
            if lk is None:
                lk = RWLock()
                self._locks[uid] = lk
            return lk

    @contextmanager
    def shared(self, uid: str, timeout: float = 30.0) -> Iterator[None]:
        lk = self._get(uid)
        if not lk.acquire_read(timeout):
            raise UserBusy(uid)
        try:
            yield
        finally:
            lk.release_read()

    @contextmanager
    def exclusive(self, uid: str, timeout: float = 30.0) -> Iterator[None]:
        lk = self._get(uid)
        if not lk.acquire_write(timeout):
            raise UserBusy(uid)
        try:
            yield
        finally:
            lk.release_write()
