"""
MIT License

Copyright (c) 2026 Mousa (@m_mousa)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

MCMG‑ANA v3 — Modular Cyclic Memory Graph, ANA‑Native Edition
=============================================================

This module implements a cyclic memory graph for ANA agents.  It exposes
coroutine‑friendly APIs, synchronous SQLite persistence for shards/identity
nodes, TF‑IDF based semantic recall, tiered decay and shard promotion.  The
implementation is suitable for local experimentation and early integration but
requires careful handling of persistence and concurrency for production
deployments.  In particular, only shard and identity memories are durably
written to SQLite – the remainder of the graph is held in memory.  Full
snapshots can be taken via ``save()``/``load()`` if persistent recovery of
all state is desired.  Callers should schedule ``decay()`` periodically and
avoid blocking the event loop by offloading heavy work onto threads.

The design preserves several invariants:

* Immutable node identifiers (UUID4).
* Entropy as a first‑class signal in scoring and decay.
* Identity core memories never decay below a floor.
* Shard memories survive instance resets via SQLite.
* All public APIs are coroutine safe via per‑operation locks.

Replacing TF‑IDF embeddings with transformer‑based models is supported by
supplying a custom embedder implementation conforming to the same interface.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import time
import uuid
from collections import defaultdict, deque, Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────


class MemoryStatus(str, Enum):
    """Lifecycle state of a memory node."""

    ACTIVE = "active"
    TENSION = "tension"  # dialectical — productive, preserved (was CONFLICT in v2)
    CONFLICT = "conflict"  # identity conflict — genuinely bad, penalised
    ARCHIVED = "archived"
    SHARD = "shard"  # Prime‑witnessed, persists across resets


class CoreOrigin(str, Enum):
    """Source of a memory node.  Determines decay tier and confidence floor."""

    OMEGA = "omega"  # expansion — decays fast
    ALPHA = "alpha"  # compression — decays medium
    PRIME = "prime"  # synthesis — decays slow
    IDENTITY = "identity"  # attractor — nearly immortal
    EXTERNAL = "external"  # injected from outside


class RecallStrategy(str, Enum):
    """Recall scoring strategies."""

    EXACT = "exact"
    TAG = "tag"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"  # exact + tag + semantic, weighted


# ─────────────────────────────────────────────────────────────
# Decay tiers — ANA‑aware
# ─────────────────────────────────────────────────────────────


DECAY_RATES: Dict[CoreOrigin, float] = {
    CoreOrigin.OMEGA: 0.012,  # fast — expansions are ephemeral
    CoreOrigin.ALPHA: 0.005,  # medium — compressions are more stable
    CoreOrigin.PRIME: 0.001,  # slow — syntheses matter
    CoreOrigin.IDENTITY: 0.0001,  # nearly immortal — identity core
    CoreOrigin.EXTERNAL: 0.008,
}

ENTROPY_RATES: Dict[CoreOrigin, float] = {
    CoreOrigin.OMEGA: 0.008,
    CoreOrigin.ALPHA: 0.003,
    CoreOrigin.PRIME: 0.0005,
    CoreOrigin.IDENTITY: 0.00005,
    CoreOrigin.EXTERNAL: 0.005,
}

CONFIDENCE_FLOORS: Dict[CoreOrigin, float] = {
    CoreOrigin.OMEGA: 0.0,
    CoreOrigin.ALPHA: 0.05,
    CoreOrigin.PRIME: 0.2,
    CoreOrigin.IDENTITY: 0.6,  # identity nodes never fall below this
    CoreOrigin.EXTERNAL: 0.0,
}


# ─────────────────────────────────────────────────────────────
# Semantic embedding — TF‑IDF with numpy
# Swappable: replace embed() with any model returning np.ndarray
# ─────────────────────────────────────────────────────────────


class TFIDFEmbedder:
    """
    Lightweight TF‑IDF embedder using a fixed vocabulary.  Produces
    L2‑normalised float32 vectors for cosine similarity.  To replace with a
    transformer model, implement ``embed()`` and optionally ``fit()``.
    """

    DIM = 512  # vocabulary size / vector dimension

    def __init__(self) -> None:
        self._idf: np.ndarray = np.ones(self.DIM, dtype=np.float32)
        self._doc_count: int = 0

    def _tokenise(self, text: str) -> List[str]:
        return re.findall(r"[a-z]{2,}", text.lower())

    def _term_index(self, term: str) -> int:
        """Deterministic index via hash — no dynamic vocab needed."""
        return int(hashlib.md5(term.encode()).hexdigest(), 16) % self.DIM

    def fit(self, texts: List[str]) -> None:
        """Update IDF weights from a corpus.  Call after bulk loads."""
        self._doc_count = len(texts)
        freq = np.zeros(self.DIM, dtype=np.float32)
        for text in texts:
            seen: Set[int] = set()
            for tok in self._tokenise(text):
                idx = self._term_index(tok)
                if idx not in seen:
                    freq[idx] += 1
                    seen.add(idx)
        # Laplace smoothing
        self._idf = np.log((self._doc_count + 1) / (freq + 1)) + 1.0

    def embed(self, text: str) -> np.ndarray:
        """Embed text → L2‑normalised TF‑IDF vector."""
        tokens = self._tokenise(text)
        if not tokens:
            return np.zeros(self.DIM, dtype=np.float32)
        freq = Counter(tokens)
        total = len(tokens)
        vec = np.zeros(self.DIM, dtype=np.float32)
        for tok, count in freq.items():
            idx = self._term_index(tok)
            tf = count / total
            vec[idx] += tf * self._idf[idx]
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec.astype(np.float32)

    def similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity — vectors assumed L2‑normalised."""
        return float(np.dot(a, b))


# ─────────────────────────────────────────────────────────────
# Core data structures
# ─────────────────────────────────────────────────────────────


@dataclass
class Association:
    """Weighted directed edge between two memory nodes."""

    target_id: str
    weight: float = 1.0
    created: float = field(default_factory=time.time)

    def reinforce(self, delta: float = 0.1) -> None:
        self.weight = min(1.0, self.weight + delta)

    def decay(self, rate: float) -> None:
        self.weight = max(0.0, self.weight - rate)

    def to_dict(self) -> Dict[str, Any]:
        return {"target_id": self.target_id, "weight": self.weight, "created": self.created}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Association:
        return cls(d["target_id"], d.get("weight", 1.0), d.get("created", time.time()))


@dataclass
class MemoryNode:
    """
    Atomic memory unit.

    Fields:
        id            – unique UUID4 identifier.
        content       – stored payload (any JSON‑serialisable object).  Non‑serialisable
                        objects will be stringified.
        tags          – set of string tags (e.g. origin, cluster).
        cluster_id    – optional cluster identifier for grouping nodes.
        confidence    – [0,1] accuracy probability.
        entropy       – [0,1] disorder/instability.
        origin        – which ANA core wrote this.
        is_restricted – whether recall of this node is disallowed unless explicitly enabled.
        is_shard      – whether this node is persisted across resets.
        status        – lifecycle state (active/tension/conflict/archived/shard).
        parent_id     – optional parent node id for versioning.
        child_ids     – list of versioned descendants.
        associations  – adjacency list of directed edges with weights.
        embedding     – semantic vector (optional; not persisted to disk).
        created       – timestamp of creation.
        last_accessed – timestamp of last recall.
        access_count  – number of recall touches.
    """

    id: str
    content: Any
    tags: Set[str]
    cluster_id: Optional[str]
    confidence: float
    entropy: float
    origin: CoreOrigin
    is_restricted: bool
    is_shard: bool
    status: MemoryStatus
    parent_id: Optional[str]
    child_ids: List[str]
    associations: Dict[str, Association]
    embedding: Optional[np.ndarray]
    created: float
    last_accessed: float
    access_count: int = 0

    @classmethod
    def create(
        cls,
        content: Any,
        *,
        tags: Optional[Set[str]] = None,
        cluster_id: Optional[str] = None,
        confidence: float = 0.55,
        entropy: float = 0.4,
        origin: CoreOrigin = CoreOrigin.EXTERNAL,
        is_restricted: bool = False,
        is_shard: bool = False,
        parent_id: Optional[str] = None,
        embedding: Optional[np.ndarray] = None,
    ) -> MemoryNode:
        now = time.time()
        tag_set = set(tags) if tags else set()
        tag_set.add(f"origin:{origin.value}")
        if cluster_id:
            tag_set.add(f"cluster:{cluster_id}")
        if is_shard:
            tag_set.add("shard")
        return cls(
            id=str(uuid.uuid4()),
            content=content,
            tags=tag_set,
            cluster_id=cluster_id,
            confidence=confidence,
            entropy=entropy,
            origin=origin,
            is_restricted=is_restricted,
            is_shard=is_shard,
            status=MemoryStatus.SHARD if is_shard else MemoryStatus.ACTIVE,
            parent_id=parent_id,
            child_ids=[],
            associations={},
            embedding=embedding,
            created=now,
            last_accessed=now,
            access_count=0,
        )

    def touch(self) -> None:
        self.last_accessed = time.time()
        self.access_count += 1

    def reinforce(self, confidence_delta: float = 0.1, entropy_delta: float = -0.05) -> None:
        floor = CONFIDENCE_FLOORS[self.origin]
        self.confidence = min(1.0, max(floor, self.confidence + confidence_delta))
        self.entropy = max(0.0, self.entropy + entropy_delta)

    def weaken(self, confidence_delta: float = 0.1, entropy_delta: float = 0.05) -> None:
        floor = CONFIDENCE_FLOORS[self.origin]
        self.confidence = max(floor, self.confidence - confidence_delta)
        self.entropy = min(1.0, self.entropy + entropy_delta)

    def associate(self, target_id: str, weight: float = 1.0) -> None:
        if target_id == self.id:
            return
        if target_id in self.associations:
            self.associations[target_id].reinforce(weight * 0.1)
        else:
            # clamp weight to [0,1]
            self.associations[target_id] = Association(target_id, min(1.0, max(0.0, weight)))

    def effective_score(self) -> float:
        """Confidence penalised by entropy — the core recall signal."""
        return self.confidence * (1.0 - self.entropy * 0.5)

    def decay_step(self, elapsed: float) -> None:
        """Apply ANA‑aware decay based on origin tier."""
        cr = DECAY_RATES[self.origin]
        er = ENTROPY_RATES[self.origin]
        floor = CONFIDENCE_FLOORS[self.origin]
        self.confidence = max(floor, self.confidence - cr * elapsed)
        self.entropy = min(1.0, self.entropy + er * elapsed)

    def to_dict(self) -> Dict[str, Any]:
        # Only persist JSON‑serialisable content; non‑serialisable types are stringified.
        content_safe = self.content
        try:
            json.dumps(content_safe)
        except TypeError:
            logger.warning(
                f"Non‑JSON‑serialisable content in node {self.id}; stringifying for persistence"
            )
            content_safe = str(self.content)
        return {
            "id": self.id,
            "content": content_safe,
            "tags": sorted(self.tags),
            "cluster_id": self.cluster_id,
            "confidence": self.confidence,
            "entropy": self.entropy,
            "origin": self.origin.value,
            "is_restricted": self.is_restricted,
            "is_shard": self.is_shard,
            "status": self.status.value,
            "parent_id": self.parent_id,
            "child_ids": self.child_ids,
            "associations": {k: v.to_dict() for k, v in self.associations.items()},
            # embedding is a derived attribute and is intentionally omitted
            "embedding": None,
            "created": self.created,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> MemoryNode:
        assocs = {k: Association.from_dict(v) for k, v in d.get("associations", {}).items()}
        return cls(
            id=d["id"],
            content=d["content"],
            tags=set(d.get("tags", [])),
            cluster_id=d.get("cluster_id"),
            confidence=d.get("confidence", 0.55),
            entropy=d.get("entropy", 0.4),
            origin=CoreOrigin(d.get("origin", "external")),
            is_restricted=d.get("is_restricted", False),
            is_shard=d.get("is_shard", False),
            status=MemoryStatus(d.get("status", "active")),
            parent_id=d.get("parent_id"),
            child_ids=d.get("child_ids", []),
            associations=assocs,
            embedding=None,  # embeddings are recomputed on load
            created=d.get("created", time.time()),
            last_accessed=d.get("last_accessed", time.time()),
            access_count=d.get("access_count", 0),
        )


@dataclass
class MemoryCluster:
    """Hierarchical grouping of memory nodes."""

    id: str
    parent_id: Optional[str] = None
    child_ids: List[str] = field(default_factory=list)
    member_ids: List[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "child_ids": self.child_ids,
            "member_ids": self.member_ids,
            "created": self.created,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> MemoryCluster:
        return cls(
            id=d["id"],
            parent_id=d.get("parent_id"),
            child_ids=d.get("child_ids", []),
            member_ids=d.get("member_ids", []),
            created=d.get("created", time.time()),
        )


@dataclass
class RecallResult:
    node_id: str
    score: float
    depth: int
    node: MemoryNode
    match_type: str = "exact"  # exact | tag | semantic | chain

    def __lt__(self, other: RecallResult) -> bool:
        # Sort descending by score
        return self.score > other.score


# ─────────────────────────────────────────────────────────────
# Index
# ─────────────────────────────────────────────────────────────


class MCMGIndex:
    """Simple in‑memory indices for tags, clusters and origins."""

    def __init__(self) -> None:
        self._tag_index: Dict[str, Set[str]] = defaultdict(set)
        self._cluster_index: Dict[str, Set[str]] = defaultdict(set)
        self._origin_index: Dict[str, Set[str]] = defaultdict(set)

    def add(self, node: MemoryNode) -> None:
        for tag in node.tags:
            self._tag_index[tag].add(node.id)
        if node.cluster_id:
            self._cluster_index[node.cluster_id].add(node.id)
        self._origin_index[node.origin.value].add(node.id)

    def remove(self, node: MemoryNode) -> None:
        for tag in node.tags:
            self._tag_index[tag].discard(node.id)
        if node.cluster_id:
            self._cluster_index[node.cluster_id].discard(node.id)
        self._origin_index[node.origin.value].discard(node.id)

    def by_tag(self, tag: str) -> FrozenSet[str]:
        return frozenset(self._tag_index.get(tag, set()))

    def by_tags(self, tags: Set[str]) -> FrozenSet[str]:
        if not tags:
            return frozenset()
        sets = [self._tag_index.get(t, set()) for t in tags]
        return frozenset(sets[0].intersection(*sets[1:]))

    def by_cluster(self, cluster_id: str) -> FrozenSet[str]:
        return frozenset(self._cluster_index.get(cluster_id, set()))

    def by_origin(self, origin: CoreOrigin) -> FrozenSet[str]:
        return frozenset(self._origin_index.get(origin.value, set()))


# ─────────────────────────────────────────────────────────────
# Conflict detector — ANA‑aware
# ─────────────────────────────────────────────────────────────


class ConflictDetector:
    """
    ANA‑aware conflict classification.

    TENSION   — Omega vs Alpha disagreement on same entity. Productive. Preserve both.
    CONFLICT  — two memories contradict on entity identity. Penalise.
    NONE      — no conflict.
    """

    ENTITY_PREFIXES: FrozenSet[str] = frozenset({"person", "entity", "object", "concept", "id"})
    SKIP_PREFIXES: FrozenSet[str] = frozenset({"cluster", "source", "ts", "origin"})

    @classmethod
    def classify(
        cls,
        tags_a: Set[str],
        origin_a: CoreOrigin,
        tags_b: Set[str],
        origin_b: CoreOrigin,
    ) -> Optional[MemoryStatus]:
        """Return TENSION, CONFLICT or None based on tag disagreement."""
        props_a = cls._property_map(tags_a)
        props_b = cls._property_map(tags_b)
        shared_entities = {
            k
            for k in props_a.keys() & props_b.keys()
            if k in cls.ENTITY_PREFIXES and props_a[k] == props_b[k]
        }
        if not shared_entities:
            return None
        non_entity_keys = (props_a.keys() & props_b.keys()) - cls.ENTITY_PREFIXES
        differs = any(props_a[k] != props_b[k] for k in non_entity_keys)
        if not differs:
            return None
        opposites = {CoreOrigin.OMEGA, CoreOrigin.ALPHA}
        if {origin_a, origin_b} == opposites:
            return MemoryStatus.TENSION
        return MemoryStatus.CONFLICT

    @classmethod
    def _property_map(cls, tags: Set[str]) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for tag in tags:
            if ":" not in tag:
                continue
            prefix, _, value = tag.partition(":")
            if prefix in cls.SKIP_PREFIXES:
                continue
            result[prefix] = value
        return result


# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────


@dataclass
class MCMGConfig:
    """Configuration parameters for the memory engine."""

    association_decay_rate: float = 0.0005
    prune_confidence_threshold: float = 0.08
    max_nodes: Optional[int] = None
    initial_confidence: float = 0.55
    initial_entropy: float = 0.4
    conflict_confidence_penalty: float = 0.25
    conflict_entropy_penalty: float = 0.15
    tension_confidence_penalty: float = 0.05  # minimal — tension is productive
    tension_entropy_penalty: float = 0.02
    reinforcement_confidence: float = 0.12
    reinforcement_entropy: float = 0.06
    semantic_weight: float = 0.4  # weight of semantic score in hybrid recall
    semantic_threshold: float = 0.15  # minimum cosine sim to include in results
    shard_dir: str = "./ana_shards"
    schema_version: int = 3
    refit_interval: int = 10  # Refit embeddings every N decay cycles

    strict_serialization: bool = False  # if True, error on non‑JSON‑serialisable content

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> MCMGConfig:
        cfg = cls()
        for k, v in d.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


# ─────────────────────────────────────────────────────────────
# MCMG‑ANA Engine
# ─────────────────────────────────────────────────────────────


class MCMG:
    """
    MCMG‑ANA v3 — async‑style, ANA‑embedded memory engine.

    This class provides coroutine‑safe methods to add, recall, associate, decay and
    manage memory nodes.  Only nodes marked as shards or identity are durably
    persisted to the backing SQLite store.  All other nodes are transient
    in‑memory objects.  Snapshots can be saved and loaded via JSON files.
    """

    SCHEMA_VERSION = 3

    def __init__(
        self,
        config: Optional[MCMGConfig] = None,
        db_path: Optional[str] = None,
        embedder: Optional[TFIDFEmbedder] = None,
    ) -> None:
        self.config = config or MCMGConfig()
        self._embedder = embedder or TFIDFEmbedder()
        self._nodes: Dict[str, MemoryNode] = {}
        self._clusters: Dict[str, MemoryCluster] = {}
        self._index = MCMGIndex()
        self._lock = asyncio.Lock()
        self._last_decay = time.time()
        self._decay_count = 0
        self._db: Optional[sqlite3.Connection] = None
        if db_path:
            try:
                Path(self.config.shard_dir).mkdir(exist_ok=True)
                # allow cross‑thread use; SQLite remains synchronous
                self._db = sqlite3.connect(db_path, check_same_thread=False)
                self._db.row_factory = sqlite3.Row
                self._db.execute("PRAGMA journal_mode=WAL")
                self._init_db()
                self._load_shards()
            except Exception as e:
                logger.error(f"Failed to initialise DB at {db_path}: {e}")
                self._db = None  # fallback to in‑memory only

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate_inputs(
        self, confidence: Optional[float], entropy: Optional[float], weight: Optional[float] = None
    ) -> None:
        """Validate numeric inputs for confidence, entropy and weight."""
        if confidence is not None and not (0.0 <= confidence <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        if entropy is not None and not (0.0 <= entropy <= 1.0):
            raise ValueError("entropy must be between 0.0 and 1.0")
        if weight is not None and weight < 0.0:
            raise ValueError("weight must be >= 0.0")

    def _init_db(self) -> None:
        if not self._db:
            return
        try:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    is_shard INTEGER NOT NULL,
                    origin TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shard ON nodes (is_shard)
                """
            )
            self._db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_origin ON nodes (origin)
                """
            )
            self._db.commit()
        except Exception as e:
            logger.error(f"Failed to init DB schema: {e}")

    def _load_shards(self) -> None:
        """Load persisted shard and identity nodes from the DB into memory."""
        if not self._db:
            return
        try:
            cursor = self._db.execute(
                "SELECT data FROM nodes WHERE is_shard = 1 OR origin = ?",
                (CoreOrigin.IDENTITY.value,),
            )
            for row in cursor:
                try:
                    node_dict = json.loads(row["data"])
                    node = MemoryNode.from_dict(node_dict)
                    self._nodes[node.id] = node
                    self._index.add(node)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to load shard: {e}")
            logger.info("Loaded %d shards/identity nodes from DB", len(self._nodes))
        except Exception as e:
            logger.error(f"Failed to load shards: {e}")

    async def _persist_node(self, node: MemoryNode) -> None:
        """Write a single node to the DB asynchronously.  Ignores non‑persistable nodes."""
        if not self._db:
            return
        try:
            await asyncio.to_thread(
                self._db.execute,
                """
                INSERT OR REPLACE INTO nodes (id, data, is_shard, origin, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    node.id,
                    json.dumps(node.to_dict()),
                    int(node.is_shard),
                    node.origin.value,
                    time.time(),
                ),
            )
            await asyncio.to_thread(self._db.commit)
        except Exception as e:
            logger.error("Failed to persist node %s: %s", node.id[:8], e)

    def _collect_persistable_nodes_unsafe(self) -> List[MemoryNode]:
        """Return a list of nodes that need durable persistence (shards and identity)."""
        return [n for n in self._nodes.values() if n.is_shard or n.origin == CoreOrigin.IDENTITY]

    def _eligible(
        self,
        node: MemoryNode,
        allow_restricted: bool,
        candidate_ids: Optional[FrozenSet[str]],
    ) -> bool:
        if not allow_restricted and node.is_restricted:
            return False
        if node.status == MemoryStatus.ARCHIVED:
            return False
        if candidate_ids is not None and node.id not in candidate_ids:
            return False
        return True

    def _find_duplicate_unsafe(self, content: Any, cluster_id: Optional[str]) -> Optional[MemoryNode]:
        for node in self._nodes.values():
            if node.content == content and node.cluster_id == cluster_id:
                return node
        return None

    def _classify_conflicts_unsafe(self, node: MemoryNode) -> Optional[Tuple[MemoryStatus, List[str]]]:
        results: Dict[MemoryStatus, List[str]] = {MemoryStatus.TENSION: [], MemoryStatus.CONFLICT: []}
        for nid, other in self._nodes.items():
            if other.status == MemoryStatus.ARCHIVED:
                continue
            status = ConflictDetector.classify(node.tags, node.origin, other.tags, other.origin)
            if status:
                results[status].append(nid)
        if results[MemoryStatus.CONFLICT]:
            return MemoryStatus.CONFLICT, results[MemoryStatus.CONFLICT]
        if results[MemoryStatus.TENSION]:
            return MemoryStatus.TENSION, results[MemoryStatus.TENSION]
        return None

    def _score_node(
        self,
        node: MemoryNode,
        query: str,
        query_emb: np.ndarray,
        strategy: RecallStrategy,
    ) -> Tuple[float, str]:
        score = 0.0
        match_type = "none"
        text = str(node.content).lower()
        if strategy in (RecallStrategy.EXACT, RecallStrategy.HYBRID):
            if query in text:
                exact_score = 0.6 + 0.2 * (1.0 - min(1.0, len(text) / 500))
                score += exact_score
                match_type = "exact"
        if strategy in (RecallStrategy.TAG, RecallStrategy.HYBRID):
            for tag in node.tags:
                if query in tag.lower():
                    score += 0.3
                    if match_type == "none":
                        match_type = "tag"
                    break
        if strategy in (RecallStrategy.SEMANTIC, RecallStrategy.HYBRID):
            if node.embedding is not None:
                sim = self._embedder.similarity(query_emb, node.embedding)
                if sim >= self.config.semantic_threshold:
                    semantic_contribution = sim * self.config.semantic_weight
                    score += semantic_contribution
                    if match_type == "none":
                        match_type = "semantic"
        if score == 0.0:
            return 0.0, "none"
        return min(1.0, score * node.effective_score()), match_type

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add(
        self,
        content: Any,
        *,
        tags: Optional[Set[str]] = None,
        cluster: Optional[str] = None,
        confidence: Optional[float] = None,
        entropy: Optional[float] = None,
        origin: CoreOrigin = CoreOrigin.EXTERNAL,
        is_restricted: bool = False,
        is_shard: bool = False,
        parent_id: Optional[str] = None,
    ) -> str:
        """
        Add a new memory node.  Returns the node id.  If a duplicate
        (same content and cluster) exists it is reinforced instead.  Only
        shard/identity nodes are persisted.
        """
        self._validate_inputs(confidence, entropy)
        nodes_to_persist: List[MemoryNode] = []
        async with self._lock:
            dup = self._find_duplicate_unsafe(content, cluster)
            if dup:
                dup.reinforce(self.config.reinforcement_confidence, -self.config.reinforcement_entropy)
                dup.touch()
                if dup.is_shard or dup.origin == CoreOrigin.IDENTITY:
                    nodes_to_persist.append(dup)
                return_id = dup.id
            else:
                node = MemoryNode.create(
                    content,
                    tags=tags,
                    cluster_id=cluster,
                    confidence=self.config.initial_confidence if confidence is None else confidence,
                    entropy=self.config.initial_entropy if entropy is None else entropy,
                    origin=origin,
                    is_restricted=is_restricted,
                    is_shard=is_shard,
                    parent_id=parent_id,
                )
                # compute embedding outside of DB writes
                node.embedding = self._embedder.embed(str(content))
                # conflict detection
                conflict = self._classify_conflicts_unsafe(node)
                if conflict:
                    status, penalised_ids = conflict
                    node.status = status
                    if status == MemoryStatus.CONFLICT:
                        penalty_c = self.config.conflict_confidence_penalty
                        penalty_e = self.config.conflict_entropy_penalty
                    else:
                        penalty_c = self.config.tension_confidence_penalty
                        penalty_e = self.config.tension_entropy_penalty
                    for pid in penalised_ids:
                        other = self._nodes[pid]
                        other.weaken(penalty_c, penalty_e)
                        other.status = status
                        if other.is_shard or other.origin == CoreOrigin.IDENTITY:
                            nodes_to_persist.append(other)
                    node.weaken(penalty_c, penalty_e)
                    logger.warning(f"{status.value.capitalize()} detected: {node.id[:8]} ↔ {penalised_ids}")
                self._nodes[node.id] = node
                self._index.add(node)
                # cluster membership
                if cluster:
                    if cluster not in self._clusters:
                        self._clusters[cluster] = MemoryCluster(cluster)
                    self._clusters[cluster].member_ids.append(node.id)
                # parent linkage
                if parent_id and parent_id in self._nodes:
                    self._nodes[parent_id].child_ids.append(node.id)
                # persist new shard or identity nodes
                if node.is_shard or node.origin == CoreOrigin.IDENTITY:
                    nodes_to_persist.append(node)
                return_id = node.id
        # outside lock: persist durable nodes
        for n in nodes_to_persist:
            await self._persist_node(n)
        return return_id

    async def recall(
        self,
        query: str,
        *,
        top_k: int = 5,
        context_tags: Optional[Set[str]] = None,
        chain_depth: int = 0,
        strategy: RecallStrategy = RecallStrategy.HYBRID,
        allow_restricted: bool = False,
        origin_filter: Optional[CoreOrigin] = None,
    ) -> List[RecallResult]:
        """
        Recall matching nodes.  Hybrid scoring uses exact, tag and semantic
        similarity.  Returns a list of ``RecallResult`` sorted by score.
        """
        query_emb = self._embedder.embed(query)
        persist_nodes: List[MemoryNode] = []
        async with self._lock:
            candidates: Dict[str, RecallResult] = {}
            candidate_ids: Optional[FrozenSet[str]] = None
            if context_tags:
                candidate_ids = self._index.by_tags(context_tags)
            if origin_filter:
                origin_ids = self._index.by_origin(origin_filter)
                candidate_ids = origin_ids if candidate_ids is None else candidate_ids & origin_ids
            q = query.lower()
            for node in self._nodes.values():
                if not self._eligible(node, allow_restricted, candidate_ids):
                    continue
                score, match_type = self._score_node(node, q, query_emb, strategy)
                if score > 0:
                    candidates[node.id] = RecallResult(node.id, score, 0, node, match_type)
            # BFS associative chaining
            if chain_depth > 0 and candidates:
                frontier = deque((r.node_id, r.score) for r in candidates.values())
                visited = set(candidates.keys())
                for depth in range(1, chain_depth + 1):
                    next_frontier: deque = deque()
                    while frontier:
                        nid, base_score = frontier.popleft()
                        node = self._nodes.get(nid)
                        if not node:
                            continue
                        for assoc in node.associations.values():
                            if assoc.target_id in visited:
                                continue
                            target = self._nodes.get(assoc.target_id)
                            if not target or not self._eligible(target, allow_restricted, None):
                                continue
                            score = base_score * assoc.weight * target.effective_score()
                            candidates[assoc.target_id] = RecallResult(
                                assoc.target_id, score, depth, target, "chain"
                            )
                            visited.add(assoc.target_id)
                            next_frontier.append((assoc.target_id, score))
                    frontier = next_frontier
            results = sorted(candidates.values())[:top_k] if top_k else sorted(candidates.values())
            # touch and mark persistable nodes
            for r in results:
                r.node.touch()
                if r.node.is_shard or r.node.origin == CoreOrigin.IDENTITY:
                    persist_nodes.append(r.node)
        # outside lock: persist touched durable nodes
        for n in persist_nodes:
            await self._persist_node(n)
        return results

    async def recall_by_tag(self, tag: str, allow_restricted: bool = False) -> List[MemoryNode]:
        persist_nodes: List[MemoryNode] = []
        async with self._lock:
            ids = self._index.by_tag(tag)
            nodes = [self._nodes[i] for i in ids if i in self._nodes]
            if not allow_restricted:
                nodes = [n for n in nodes if not n.is_restricted]
            nodes.sort(key=lambda n: n.effective_score(), reverse=True)
            for n in nodes:
                if n.is_shard or n.origin == CoreOrigin.IDENTITY:
                    persist_nodes.append(n)
                n.touch()
        for n in persist_nodes:
            await self._persist_node(n)
        return nodes

    async def recall_cluster(self, cluster_id: str, allow_restricted: bool = False) -> List[MemoryNode]:
        persist_nodes: List[MemoryNode] = []
        async with self._lock:
            ids = self._index.by_cluster(cluster_id)
            nodes = [self._nodes[i] for i in ids if i in self._nodes]
            if not allow_restricted:
                nodes = [n for n in nodes if not n.is_restricted]
            nodes.sort(key=lambda n: n.effective_score(), reverse=True)
            for n in nodes:
                if n.is_shard or n.origin == CoreOrigin.IDENTITY:
                    persist_nodes.append(n)
                n.touch()
        for n in persist_nodes:
            await self._persist_node(n)
        return nodes

    async def recall_shards(self) -> List[MemoryNode]:
        """Return all shard and identity nodes."""
        persist_nodes: List[MemoryNode] = []
        async with self._lock:
            nodes = [n for n in self._nodes.values() if n.is_shard or n.status == MemoryStatus.SHARD]
            for n in nodes:
                if n.is_shard or n.origin == CoreOrigin.IDENTITY:
                    persist_nodes.append(n)
                n.touch()
        for n in persist_nodes:
            await self._persist_node(n)
        return nodes

    async def associate(self, source_id: str, target_id: str, weight: float = 1.0) -> bool:
        """Create or reinforce a directed association between two nodes."""
        self._validate_inputs(None, None, weight)
        persist_nodes: List[MemoryNode] = []
        async with self._lock:
            src = self._nodes.get(source_id)
            tgt = self._nodes.get(target_id)
            if not src or not tgt:
                return False
            src.associate(target_id, weight)
            if src.is_shard or src.origin == CoreOrigin.IDENTITY:
                persist_nodes.append(src)
        for n in persist_nodes:
            await self._persist_node(n)
        return True

    async def associate_bidirectional(self, id_a: str, id_b: str, weight: float = 1.0) -> bool:
        """Create symmetric associations between two nodes."""
        self._validate_inputs(None, None, weight)
        persist_nodes: List[MemoryNode] = []
        async with self._lock:
            src = self._nodes.get(id_a)
            tgt = self._nodes.get(id_b)
            if not src or not tgt:
                return False
            src.associate(id_b, weight)
            tgt.associate(id_a, weight)
            if src.is_shard or src.origin == CoreOrigin.IDENTITY:
                persist_nodes.append(src)
            if tgt.is_shard or tgt.origin == CoreOrigin.IDENTITY:
                persist_nodes.append(tgt)
        for n in persist_nodes:
            await self._persist_node(n)
        return True

    async def version(
        self,
        mem_id: str,
        new_content: Any,
        *,
        tags: Optional[Set[str]] = None,
    ) -> Optional[str]:
        """Create a new version of an existing node and archive the parent."""
        nodes_to_persist: List[MemoryNode] = []
        async with self._lock:
            parent = self._nodes.get(mem_id)
            if not parent:
                return None
            parent.status = MemoryStatus.ARCHIVED
            if parent.is_shard or parent.origin == CoreOrigin.IDENTITY:
                nodes_to_persist.append(parent)
        # persist parent outside lock
        for n in nodes_to_persist:
            await self._persist_node(n)
        # create child outside of original lock (add() acquires its own lock)
        child_id = await self.add(
            new_content,
            tags=tags if tags is not None else parent.tags.copy(),
            cluster=parent.cluster_id,
            origin=parent.origin,
            is_restricted=parent.is_restricted,
            is_shard=parent.is_shard,
            parent_id=mem_id,
            confidence=parent.confidence,
            entropy=parent.entropy,
        )
        # update child status after creation
        persist_nodes_child: List[MemoryNode] = []
        async with self._lock:
            child = self._nodes.get(child_id)
            if child:
                child.status = MemoryStatus.SHARD if parent.is_shard else MemoryStatus.ACTIVE
                if child.is_shard or child.origin == CoreOrigin.IDENTITY:
                    persist_nodes_child.append(child)
        for n in persist_nodes_child:
            await self._persist_node(n)
        return child_id

    async def promote_shard(self, mem_id: str) -> bool:
        """Promote a node to shard status permanently."""
        persist_nodes: List[MemoryNode] = []
        async with self._lock:
            node = self._nodes.get(mem_id)
            if not node:
                return False
            node.is_shard = True
            node.status = MemoryStatus.SHARD
            node.tags.add("shard")
            persist_nodes.append(node)
        for n in persist_nodes:
            await self._persist_node(n)
        return True

    async def remove(self, mem_id: str) -> bool:
        """Remove a node entirely from the graph."""
        persist_nodes: List[MemoryNode] = []
        to_remove_db: bool = False
        async with self._lock:
            node = self._nodes.pop(mem_id, None)
            if not node:
                return False
            self._index.remove(node)
            # cluster membership
            if node.cluster_id and node.cluster_id in self._clusters:
                cluster = self._clusters[node.cluster_id]
                if mem_id in cluster.member_ids:
                    cluster.member_ids.remove(mem_id)
                if not cluster.member_ids:
                    del self._clusters[node.cluster_id]
            # parent linkage
            if node.parent_id and node.parent_id in self._nodes:
                parent = self._nodes[node.parent_id]
                if mem_id in parent.child_ids:
                    parent.child_ids.remove(mem_id)
                if parent.is_shard or parent.origin == CoreOrigin.IDENTITY:
                    persist_nodes.append(parent)
            # remove inbound associations
            for n in self._nodes.values():
                if mem_id in n.associations:
                    del n.associations[mem_id]
                    if n.is_shard or n.origin == CoreOrigin.IDENTITY:
                        persist_nodes.append(n)
            if self._db:
                # mark deletion needed outside lock
                to_remove_db = True
        # persist parent/associates modifications
        for n in persist_nodes:
            await self._persist_node(n)
        # delete row from DB outside lock if needed
        if to_remove_db and self._db:
            try:
                await asyncio.to_thread(self._db.execute, "DELETE FROM nodes WHERE id=?", (mem_id,))
                await asyncio.to_thread(self._db.commit)
            except Exception as e:
                logger.error(f"Failed to delete node {mem_id} from DB: {e}")
        return True

    async def flag(self, mem_id: str, restricted: bool = True) -> bool:
        """Flag or unflag a node as restricted."""
        persist_nodes: List[MemoryNode] = []
        async with self._lock:
            node = self._nodes.get(mem_id)
            if not node:
                return False
            node.is_restricted = restricted
            if node.is_shard or node.origin == CoreOrigin.IDENTITY:
                persist_nodes.append(node)
        for n in persist_nodes:
            await self._persist_node(n)
        return True

    async def decay(self, elapsed: Optional[float] = None) -> Dict[str, Any]:
        """
        Apply ANA‑aware tiered decay to all active nodes.  Omega decays fast,
        Alpha medium, Prime slow, Identity almost never.  Also decays
        association weights and prunes low‑confidence memories.  Periodically
        refits the embedder based on the current corpus.  Returns a summary.

        ``elapsed`` is the time since the last decay in whatever unit the
        caller's clock runs on (a simulated world passes world hours).  When
        omitted, wall‑clock seconds since the previous call are used.
        """
        persist_nodes: List[MemoryNode] = []
        now = time.time()
        async with self._lock:
            if elapsed is None:
                elapsed = now - self._last_decay
            self._last_decay = now
            for node in list(self._nodes.values()):
                if node.status == MemoryStatus.ARCHIVED:
                    continue
                node.decay_step(elapsed)
                # decay associations and drop dead ones
                for assoc_id in list(node.associations):
                    assoc = node.associations[assoc_id]
                    assoc.decay(self.config.association_decay_rate * elapsed)
                    if assoc.weight <= 0.0:
                        del node.associations[assoc_id]
                if node.is_shard or node.origin == CoreOrigin.IDENTITY:
                    persist_nodes.append(node)
            # periodic IDF refit
            self._decay_count += 1
            if self._decay_count % self.config.refit_interval == 0:
                texts = [str(n.content) for n in self._nodes.values()]
                if texts:
                    self._embedder.fit(texts)
            # prune low‑confidence nodes
            pruned = self._prune_unsafe()
        # persist mutated durable nodes outside lock
        for n in persist_nodes:
            await self._persist_node(n)
        logger.info("Decay cycle: elapsed=%.1fs pruned=%d total=%d", elapsed, pruned, len(self._nodes))
        return {"elapsed_seconds": elapsed, "pruned": pruned, "total": len(self._nodes)}

    async def refit_embeddings(self) -> None:
        """Refit IDF weights and recompute all embeddings for all nodes."""
        async with self._lock:
            texts = [str(n.content) for n in self._nodes.values()]
            self._embedder.fit(texts)
            for node in self._nodes.values():
                node.embedding = self._embedder.embed(str(node.content))
        logger.info("Refit embeddings on %d nodes", len(self._nodes))

    def stats(self) -> Dict[str, Any]:
        """Return snapshot statistics of the memory graph."""
        nodes = list(self._nodes.values())
        if not nodes:
            return {"total_nodes": 0, "total_clusters": len(self._clusters)}
        total = len(nodes)
        by_origin = {o.value: 0 for o in CoreOrigin}
        for n in nodes:
            by_origin[n.origin.value] += 1
        return {
            "total_nodes": total,
            "active_nodes": sum(1 for n in nodes if n.status == MemoryStatus.ACTIVE),
            "shard_nodes": sum(1 for n in nodes if n.is_shard),
            "tension_nodes": sum(1 for n in nodes if n.status == MemoryStatus.TENSION),
            "conflict_nodes": sum(1 for n in nodes if n.status == MemoryStatus.CONFLICT),
            "archived_nodes": sum(1 for n in nodes if n.status == MemoryStatus.ARCHIVED),
            "restricted_nodes": sum(1 for n in nodes if n.is_restricted),
            "total_clusters": len(self._clusters),
            "nodes_by_origin": by_origin,
            "avg_confidence": sum(n.confidence for n in nodes) / total,
            "avg_entropy": sum(n.entropy for n in nodes) / total,
            "avg_effective_score": sum(n.effective_score() for n in nodes) / total,
            "total_associations": sum(len(n.associations) for n in nodes),
            "nodes_with_embeddings": sum(1 for n in nodes if n.embedding is not None),
        }

    def _prune_unsafe(self) -> int:
        """Remove low‑confidence, non‑shard, non‑archived nodes.  Returns count removed."""
        threshold = self.config.prune_confidence_threshold
        # select candidates for removal
        to_remove: List[str] = [
            nid
            for nid, n in self._nodes.items()
            if n.confidence <= threshold
            and n.status not in (MemoryStatus.ARCHIVED, MemoryStatus.SHARD)
            and not n.is_shard
        ]
        # enforce maximum node count
        if self.config.max_nodes is not None:
            active = [nid for nid in self._nodes if nid not in to_remove]
            if len(active) > self.config.max_nodes:
                # sort by effective score then last accessed
                active.sort(
                    key=lambda nid: (
                        self._nodes[nid].effective_score(),
                        self._nodes[nid].last_accessed,
                    )
                )
                excess = len(active) - self.config.max_nodes
                # pruneable excludes shards and identity nodes
                pruneable = [
                    nid
                    for nid in active
                    if not self._nodes[nid].is_shard and self._nodes[nid].origin != CoreOrigin.IDENTITY
                ]
                to_remove.extend(pruneable[:excess])
        removed = 0
        for nid in set(to_remove):
            node = self._nodes.pop(nid, None)
            if node:
                self._index.remove(node)
                removed += 1
        return removed

    async def save(self, filepath: str) -> None:
        """Write a full JSON snapshot of the graph to the specified file."""
        async with self._lock:
            payload = {
                "schema_version": self.SCHEMA_VERSION,
                "saved_at": time.time(),
                "config": self.config.to_dict(),
                "clusters": [c.to_dict() for c in self._clusters.values()],
                "nodes": [n.to_dict() for n in self._nodes.values()],
            }
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            logger.info("Snapshot saved: %d nodes → %s", len(self._nodes), filepath)
        except Exception as e:
            logger.error(f"Failed to save snapshot to {filepath}: {e}")

    async def load(self, filepath: str) -> None:
        """Restore the graph from a JSON snapshot."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load snapshot from {filepath}: {e}")
            return
        schema = data.get("schema_version", 1)
        if schema < self.SCHEMA_VERSION:
            logger.warning(
                "Loading schema v%d into v%d engine; manual migration may be needed", schema, self.SCHEMA_VERSION
            )
        async with self._lock:
            self._nodes.clear()
            self._clusters.clear()
            self._index = MCMGIndex()
            self.config = MCMGConfig.from_dict(data.get("config", {}))
            for cd in data.get("clusters", []):
                self._clusters[cd["id"]] = MemoryCluster.from_dict(cd)
            for nd in data.get("nodes", []):
                node = MemoryNode.from_dict(nd)
                self._nodes[node.id] = node
                self._index.add(node)
        # recompute embeddings after load
        await self.refit_embeddings()
        logger.info("Loaded %d nodes from %s", len(self._nodes), filepath)

    async def close(self) -> None:
        """Flush durable nodes and close the DB connection."""
        # persist shards/identity nodes before closing
        persist_nodes = self._collect_persistable_nodes_unsafe()
        for n in persist_nodes:
            await self._persist_node(n)
        if self._db:
            try:
                self._db.close()
            except Exception as e:
                logger.error(f"Failed to close DB: {e}")
            self._db = None

    def get(self, mem_id: str) -> Optional[MemoryNode]:
        """Return a node by its identifier, or None if not present."""
        return self._nodes.get(mem_id)

    def __len__(self) -> int:
        """Return the number of nodes currently in memory."""
        return len(self._nodes)

    def __iter__(self) -> Iterator[MemoryNode]:
        """Iterate over all memory nodes."""
        return iter(self._nodes.values())
