"""Per-user daily turn cap and the global daily model-turn budget (UTC days)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import GlobalUsage, Usage


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def user_usage(db: Session, user_id: str, day: str | None = None) -> Usage:
    day = day or today()
    row = db.get(Usage, (user_id, day))
    if row is None:
        row = Usage(user_id=user_id, day=day, cycles=0, llm_cycles=0)
        db.add(row)
        db.flush()
    return row


def cycles_today(db: Session, user_id: str) -> int:
    row = db.get(Usage, (user_id, today()))
    return row.cycles if row else 0


def global_llm_today(db: Session) -> int:
    row = db.get(GlobalUsage, today())
    return row.llm_cycles if row else 0


def llm_budget_left(db: Session, limit: int) -> bool:
    return global_llm_today(db) < limit


def record_turn(db: Session, user_id: str, *, llm: bool) -> None:
    row = user_usage(db, user_id)
    row.cycles += 1
    if llm:
        row.llm_cycles += 1
        record_llm(db)


def record_llm(db: Session) -> None:
    day = today()
    g = db.get(GlobalUsage, day)
    if g is None:
        g = GlobalUsage(day=day, llm_cycles=0)
        db.add(g)
        db.flush()
    g.llm_cycles += 1


def usage_rows(db: Session, user_id: str) -> list[Usage]:
    return list(db.execute(select(Usage).where(Usage.user_id == user_id)
                           .order_by(Usage.day)).scalars())
