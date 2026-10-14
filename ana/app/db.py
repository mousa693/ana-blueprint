"""Engine / session factory. SQLite (default) and Postgres (psycopg 3)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from .models import SCHEMA_VERSION, Base, SchemaVersion


def make_engine(url: str) -> Engine:
    u = make_url(url)
    if u.get_backend_name() == "sqlite":
        db = u.database
        if db and db != ":memory:":
            Path(db).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30},
                               future=True)

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _rec):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True, future=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        row = s.execute(select(SchemaVersion)).scalars().first()
        if row is None:
            s.add(SchemaVersion(version=SCHEMA_VERSION))
            s.commit()
        elif row.version > SCHEMA_VERSION:
            raise RuntimeError(f"database schema version {row.version} is newer than this "
                               f"server ({SCHEMA_VERSION})")


def make_sessionmaker(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False, future=True)
