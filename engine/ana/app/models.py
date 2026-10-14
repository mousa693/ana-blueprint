"""SQLAlchemy 2.x models (docs/PRODUCT_SPEC.md §3 "Data").

Additions beyond the spec's column list (all additive, noted in the report):

* ``identities.state_json`` / ``insights_json`` — the evolving self returned by
  ``JourneyRuntime.identity_state()`` (saved when a journey closes or ends and
  passed to the next ``start``) and the journal insights captured with it.
* ``journeys.chapter`` — the current chapter label, so the journey list needs
  no runtime.
* ``sessions`` — server-side session ids (sha256 of the cookie token) so
  logout / account deletion revoke them.
* ``global_usage`` — the global per-day model-turn counter (kept separate from
  per-user ``usage`` so deleting an account does not refund the global budget).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (DateTime, ForeignKey, Integer, LargeBinary, String, Text,
                        UniqueConstraint, Boolean)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)   # sha256(token)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"),
                                         index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Identity(Base):
    __tablename__ = "identities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"),
                                         unique=True)
    identity_id: Mapped[str] = mapped_column(String(64), unique=True)   # shard scope
    name: Mapped[str] = mapped_column(String(80))
    seed_json: Mapped[str] = mapped_column(Text)
    state_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    insights_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Journey(Base):
    __tablename__ = "journeys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"),
                                         index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    use_llm: Mapped[bool] = mapped_column(Boolean, default=False)
    seed_value: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    cycles: Mapped[int] = mapped_column(Integer, default=0)
    snapshot: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    chapter: Mapped[str] = mapped_column(String(120), default="")


class Cycle(Base):
    __tablename__ = "cycles"
    __table_args__ = (UniqueConstraint("journey_id", "tick"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    journey_id: Mapped[str] = mapped_column(String(32), ForeignKey("journeys.id", ondelete="CASCADE"),
                                            index=True)
    tick: Mapped[int] = mapped_column(Integer)
    record_json: Mapped[str] = mapped_column(Text)        # CycleRecord-lite
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DebriefRow(Base):
    __tablename__ = "debriefs"
    journey_id: Mapped[str] = mapped_column(String(32), ForeignKey("journeys.id", ondelete="CASCADE"),
                                            primary_key=True)
    json: Mapped[str] = mapped_column(Text)
    markdown: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Usage(Base):
    __tablename__ = "usage"
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"),
                                         primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)     # YYYY-MM-DD (UTC)
    cycles: Mapped[int] = mapped_column(Integer, default=0)
    llm_cycles: Mapped[int] = mapped_column(Integer, default=0)


class GlobalUsage(Base):
    __tablename__ = "global_usage"
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    llm_cycles: Mapped[int] = mapped_column(Integer, default=0)


class SafetyEvent(Base):
    __tablename__ = "safety_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"),
                                         index=True)
    journey_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    level: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
