"""In-process LRU cache of ``JourneyRuntime`` keyed by journey id.

* One lock per journey: every use of a journey's runtime (and the DB writes
  that go with it) happens while holding it — "one active request per journey".
* On a miss the runtime is restored from the snapshot this server stored in
  the ``journeys`` row (never from anything a client sent).
* The caller snapshots to the DB after every turn (:func:`snapshot_into`).
* LRU eviction closes the runtime (shards flushed); account deletion evicts
  with ``close(flush=False)`` before the shards are deleted.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from ana.runtime import JourneyRuntime

logger = logging.getLogger("ana.app.runtimes")


class JourneyBusy(Exception):
    """Another request holds this journey's lock."""


class RuntimeCache:
    def __init__(self, *, max_size: int, shard_db: Optional[str], gateway_for: Callable[[], object]):
        self.max_size = max_size
        self.shard_db = shard_db
        self._gateway_for = gateway_for
        self._runtimes: "OrderedDict[str, JourneyRuntime]" = OrderedDict()
        self._locks: dict[str, threading.Lock] = {}
        self._mu = threading.Lock()

    # -- locks -----------------------------------------------------------
    def lock_for(self, jid: str) -> threading.Lock:
        with self._mu:
            lk = self._locks.get(jid)
            if lk is None:
                lk = self._locks[jid] = threading.Lock()
            return lk

    @contextmanager
    def locked(self, jid: str, *, timeout: float = 0.0) -> Iterator[None]:
        lk = self.lock_for(jid)
        ok = lk.acquire(timeout=timeout) if timeout > 0 else lk.acquire(blocking=False)
        if not ok:
            raise JourneyBusy(jid)
        try:
            yield
        finally:
            lk.release()

    # -- runtimes (call with the journey's lock held) ---------------------
    def get(self, jid: str, snapshot: Optional[bytes]) -> JourneyRuntime:
        with self._mu:
            rt = self._runtimes.get(jid)
            if rt is not None:
                self._runtimes.move_to_end(jid)
                return rt
        if not snapshot:
            raise LookupError(f"journey {jid} has no snapshot")
        rt = JourneyRuntime.restore(bytes(snapshot), shard_db=self.shard_db,
                                    gateway=self._gateway_for())
        self.put(jid, rt)
        return rt

    def cached(self, jid: str) -> bool:
        with self._mu:
            return jid in self._runtimes

    def put(self, jid: str, rt: JourneyRuntime) -> None:
        victims: list[tuple[str, JourneyRuntime]] = []
        with self._mu:
            old = self._runtimes.pop(jid, None)
            self._runtimes[jid] = rt
            if old is not None and old is not rt:
                victims.append((jid, old))
            for vid in list(self._runtimes):
                if len(self._runtimes) <= self.max_size:
                    break
                if vid == jid:
                    continue
                lk = self._locks.get(vid)
                if lk is not None and lk.locked():
                    continue          # in use; try the next-oldest
                victims.append((vid, self._runtimes.pop(vid)))
        for vid, v in victims:
            self._close(vid, v, flush=True)

    def drop(self, jid: str, *, flush: bool = True) -> None:
        """Forget ``jid``'s runtime (closing it). Call with its lock held, or
        when nobody can be using it."""
        with self._mu:
            rt = self._runtimes.pop(jid, None)
        if rt is not None:
            self._close(jid, rt, flush=flush)

    def forget_lock(self, jid: str) -> None:
        with self._mu:
            self._locks.pop(jid, None)

    @staticmethod
    def _close(jid: str, rt: JourneyRuntime, *, flush: bool) -> None:
        try:
            rt.close(flush=flush)
        except Exception:   # pragma: no cover - best effort
            logger.exception("closing runtime for journey %s failed", jid)

    def clear(self, *, flush: bool = True) -> None:
        with self._mu:
            items = list(self._runtimes.items())
            self._runtimes.clear()
        for jid, rt in items:
            self._close(jid, rt, flush=flush)

    def __len__(self) -> int:
        with self._mu:
            return len(self._runtimes)
