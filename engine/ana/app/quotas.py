"""Per-user daily turn cap and the global daily model budget (UTC days).

Every limit is enforced with an atomic conditional UPDATE
(``SET n = n + 1 WHERE n < limit``) whose rowcount says whether the unit was
granted, so concurrent requests can never overshoot it — on SQLite (the write
is serialised by the database lock) and on Postgres (the row lock makes the
second UPDATE re-evaluate its WHERE against the committed value) alike.

These helpers COMMIT the session they are given: call them only when that
session holds no unrelated pending writes (the API calls them before it
writes anything else, or right after a commit).

The global counter counts *model calls* (every request the shared gateway
sends — scenes, interpretation, NPC lines, onboarding, safety screens,
debriefs), not turns. See :mod:`ana.app.metering`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import GlobalUsage, Usage


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# -- reads ---------------------------------------------------------------

def cycles_today(db: Session, user_id: str) -> int:
    v = db.execute(select(Usage.cycles).where(Usage.user_id == user_id,
                                              Usage.day == today())).scalar()
    return int(v or 0)


def global_llm_today(db: Session) -> int:
    v = db.execute(select(GlobalUsage.llm_cycles).where(GlobalUsage.day == today())).scalar()
    return int(v or 0)


def llm_budget_left(db: Session, limit: int) -> bool:
    return global_llm_today(db) < limit


def usage_rows(db: Session, user_id: str) -> list[Usage]:
    return list(db.execute(select(Usage).where(Usage.user_id == user_id)
                           .order_by(Usage.day)).scalars())


# -- row creation (race-safe) --------------------------------------------

def _ensure(db: Session, model, key: dict, **defaults) -> None:
    cond = [getattr(model, k) == v for k, v in key.items()]
    if db.execute(select(*[getattr(model, k) for k in key]).where(*cond)).first() is not None:
        return
    try:
        db.add(model(**key, **defaults))
        db.commit()
    except IntegrityError:          # created concurrently (or the user is gone)
        db.rollback()


# -- per-user turns --------------------------------------------------------

def reserve_turn(db: Session, user_id: str, cap: int) -> bool:
    """Take one of today's ``cap`` turns for ``user_id``; False when spent."""
    day = today()
    _ensure(db, Usage, {"user_id": user_id, "day": day}, cycles=0, llm_cycles=0)
    res = db.execute(update(Usage)
                     .where(Usage.user_id == user_id, Usage.day == day, Usage.cycles < cap)
                     .values(cycles=Usage.cycles + 1)
                     .execution_options(synchronize_session=False))
    db.commit()
    return res.rowcount == 1


def release_turn(db: Session, user_id: str) -> None:
    """Give back a turn taken by :func:`reserve_turn` (the turn did not happen)."""
    db.execute(update(Usage)
               .where(Usage.user_id == user_id, Usage.day == today(), Usage.cycles > 0)
               .values(cycles=Usage.cycles - 1)
               .execution_options(synchronize_session=False))
    db.commit()


def mark_llm_turn(db: Session, user_id: str) -> None:
    """Note that one of the user's turns was dreamed by the model (statistic;
    no commit — it rides along with the turn's own writes)."""
    db.execute(update(Usage)
               .where(Usage.user_id == user_id, Usage.day == today())
               .values(llm_cycles=Usage.llm_cycles + 1)
               .execution_options(synchronize_session=False))


# -- global model budget ---------------------------------------------------

def reserve_llm(db: Session, limit: int) -> bool:
    """Atomically take one unit of today's global model budget."""
    day = today()
    _ensure(db, GlobalUsage, {"day": day}, llm_cycles=0)
    res = db.execute(update(GlobalUsage)
                     .where(GlobalUsage.day == day, GlobalUsage.llm_cycles < limit)
                     .values(llm_cycles=GlobalUsage.llm_cycles + 1)
                     .execution_options(synchronize_session=False))
    db.commit()
    return res.rowcount == 1


def adjust_llm(db: Session, delta: int) -> None:
    """Add ``delta`` (may be negative: an unused reservation) to today's
    global counter, never below zero."""
    if not delta:
        return
    day = today()
    _ensure(db, GlobalUsage, {"day": day}, llm_cycles=0)
    if delta > 0:
        stmt = update(GlobalUsage).where(GlobalUsage.day == day) \
            .values(llm_cycles=GlobalUsage.llm_cycles + delta)
    else:
        stmt = update(GlobalUsage).where(GlobalUsage.day == day,
                                         GlobalUsage.llm_cycles >= -delta) \
            .values(llm_cycles=GlobalUsage.llm_cycles + delta)
    db.execute(stmt.execution_options(synchronize_session=False))
    db.commit()
