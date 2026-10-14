"""Counting every model call against the global daily budget.

``create_app`` wraps the shared gateway in :class:`MeteredGateway`. Every
public call (``structured`` / ``complete`` / ``complete_json``) — whoever
makes it: the story engine, NPC layer, onboarding, the safety screen, the
debrief — is counted on the :class:`Meter` of the request it runs in (a
``ContextVar``; the engine calls the gateway synchronously in the request's
thread). At the end of the request the API settles the global counter with
``calls - reserved`` (see ``api.metered``), so the counter equals the number
of model calls actually made, and an unused reservation is refunded.

The proxy subclasses ``ModelGateway`` only so journey snapshots keep treating
it as the live gateway handle (pickled by reference, never by value); every
attribute is delegated to the wrapped gateway.
"""
from __future__ import annotations

import threading
from contextvars import ContextVar
from typing import Any, Optional

from ana.llm.gateway import ModelGateway


class Meter:
    """Model calls made, and global budget units reserved, by one request."""

    __slots__ = ("calls", "reserved", "_lock")

    def __init__(self) -> None:
        self.calls = 0
        self.reserved = 0
        self._lock = threading.Lock()

    def count(self) -> None:
        with self._lock:
            self.calls += 1


_current: ContextVar[Optional[Meter]] = ContextVar("ana_model_meter", default=None)


def current() -> Optional[Meter]:
    return _current.get()


def activate(meter: Meter):
    return _current.set(meter)


def deactivate(token) -> None:
    _current.reset(token)


class MeteredGateway(ModelGateway):
    def __init__(self, inner: Any):            # noqa: D401 - no super().__init__
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_stray", 0)
        object.__setattr__(self, "_stray_lock", threading.Lock())

    # -- delegation --------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        if name in ("_inner", "_stray", "_stray_lock"):
            raise AttributeError(name)
        return getattr(object.__getattribute__(self, "_inner"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._inner, name, value)

    @property
    def inner(self) -> Any:
        return self._inner

    @property
    def available(self) -> bool:            # type: ignore[override]
        return bool(getattr(self._inner, "available", False))

    # -- counting ----------------------------------------------------------
    def _count(self) -> None:
        m = _current.get()
        if m is not None:
            m.count()
        else:                                # outside any request: settle later
            with self._stray_lock:
                object.__setattr__(self, "_stray", self._stray + 1)

    def take_stray(self) -> int:
        with self._stray_lock:
            n = self._stray
            object.__setattr__(self, "_stray", 0)
            return n

    def structured(self, *a: Any, **k: Any):     # type: ignore[override]
        self._count()
        return self._inner.structured(*a, **k)

    def complete(self, *a: Any, **k: Any):       # type: ignore[override]
        self._count()
        return self._inner.complete(*a, **k)

    def complete_json(self, *a: Any, **k: Any):  # type: ignore[override]
        self._count()
        return self._inner.complete_json(*a, **k)


class SafetyGate:
    """The gateway handed to the safety screen for one person's text.

    Each model second opinion takes a token from the person's own safety
    bucket and a unit of the global budget; when either is spent it answers
    ``None`` and ``screen_text_llm`` returns the deterministic floor — the
    screen always runs, only the model opinion is rationed."""

    def __init__(self, gateway: Any, *, allow, reserve):
        self._gateway = gateway
        self._allow = allow          # () -> bool  (per-user bucket)
        self._reserve = reserve      # () -> bool  (global budget)
        self.model_calls = 0
        self.refused = 0

    @property
    def available(self) -> bool:
        return True

    def structured(self, *a: Any, **k: Any):
        if not self._allow() or not self._reserve():
            self.refused += 1
            return None
        self.model_calls += 1
        return self._gateway.structured(*a, **k)

    def complete(self, *a: Any, **k: Any):      # not used by the screen
        return None
