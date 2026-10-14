"""Direct access to one identity's rows in the MCMG shard store.

MCMG persists nodes in a single ``nodes(id, data JSON, is_shard, origin,
updated_at)`` table. A scoped ``ShardMemory`` tags every node with
``cluster_id = "identity:<scope_id(identity_id)>"`` (see
``ana/identity/memory.py``), which is what both export and deletion filter on:
a cheap ``LIKE`` prefilter, then an exact check of the parsed ``cluster_id``.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from ana.identity.memory import scope_id


def cluster_for(identity_id: str) -> str:
    return f"identity:{scope_id(identity_id)}"


def _connect(shard_db: Optional[str]) -> Optional[sqlite3.Connection]:
    if not shard_db or (shard_db != ":memory:" and not Path(shard_db).exists()):
        return None
    conn = sqlite3.connect(shard_db, timeout=30)
    conn.row_factory = sqlite3.Row
    has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes'").fetchone()
    if not has:
        conn.close()
        return None
    return conn


def _rows(conn: sqlite3.Connection, identity_id: str) -> list[tuple[str, dict]]:
    cluster = cluster_for(identity_id)
    like = "%" + json.dumps({"cluster_id": cluster})[1:-1].replace("\\", "\\\\") \
        .replace("%", "\\%").replace("_", "\\_") + "%"
    out = []
    for row in conn.execute("SELECT id, data FROM nodes WHERE data LIKE ? ESCAPE '\\'", (like,)):
        try:
            data = json.loads(row["data"])
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("cluster_id") == cluster:
            out.append((row["id"], data))
    return out


def export_shards(shard_db: Optional[str], identity_id: str) -> list[dict]:
    conn = _connect(shard_db)
    if conn is None:
        return []
    try:
        rows = _rows(conn, identity_id)
    finally:
        conn.close()
    out = []
    for _id, d in rows:
        tags = sorted(t for t in d.get("tags", []) if not str(t).startswith("cluster:"))
        out.append({"content": d.get("content"), "tags": tags})
    return out


def delete_shards(shard_db: Optional[str], identity_id: str) -> int:
    conn = _connect(shard_db)
    if conn is None:
        return 0
    try:
        ids = [i for i, _ in _rows(conn, identity_id)]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            conn.execute(f"DELETE FROM nodes WHERE id IN ({','.join('?' * len(chunk))})", chunk)
        conn.commit()
        return len(ids)
    finally:
        conn.close()


def count_shards(shard_db: Optional[str], identity_id: str) -> int:
    conn = _connect(shard_db)
    if conn is None:
        return 0
    try:
        return len(_rows(conn, identity_id))
    finally:
        conn.close()
