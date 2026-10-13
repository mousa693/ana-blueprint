"""Memory Shard System — a synchronous facade over the author's MCMG graph.

The blueprint's Memory Shard System stores episodic memories *and* "memories of
the future" (goals/visions treated as if already remembered). MCMG already
implements exactly this: shards with origin tiers, tension-vs-conflict
detection, entropy-aware decay, and semantic recall. This wrapper:

  * exposes a small synchronous API the rest of ANA uses (the loop is sync),
  * maps ANA concepts onto MCMG's `CoreOrigin` tiers:
        IDENTITY  -> core self (nearly immortal)
        PRIME     -> integrated lessons / syntheses (slow decay)
        ALPHA     -> compressions / stabilised experience (medium)
        OMEGA     -> fresh expansions / raw scene events (fast decay)
        EXTERNAL  -> seeded profile facts
  * records future-memory shards with a `horizon:future` tag so the generative
    engine can foreshadow them (backcasting).

MCMG is async; we drive it on a private event loop so callers stay synchronous.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from ..cores.mcmg import MCMG, MCMGConfig, CoreOrigin, RecallStrategy


class ShardMemory:
    def __init__(self, db_path: Optional[str] = None):
        self._loop = asyncio.new_event_loop()
        cfg = MCMGConfig(shard_dir="./ana_run/shards")
        self._g = MCMG(config=cfg, db_path=db_path)

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    # -- writing ---------------------------------------------------------
    def record_event(self, summary: str, *, themes: list[str] | None = None,
                     symbols: list[str] | None = None, emotion: str | None = None,
                     origin: CoreOrigin = CoreOrigin.OMEGA,
                     is_shard: bool = False) -> str:
        """Record an experience as a memory shard."""
        tags = set()
        for t in (themes or []):
            tags.add(f"theme:{t}")
        for s in (symbols or []):
            tags.add(f"symbol:{s}")
        if emotion:
            tags.add(f"emotion:{emotion}")
        return self._run(self._g.add(summary, tags=tags, origin=origin, is_shard=is_shard))

    def record_lesson(self, lesson: str, *, themes: list[str] | None = None) -> str:
        """A synthesised lesson is a Prime shard — it matters and persists."""
        tags = {f"theme:{t}" for t in (themes or [])}
        tags.add("kind:lesson")
        return self._run(self._g.add(lesson, tags=tags, origin=CoreOrigin.PRIME,
                                     is_shard=True, confidence=0.7))

    def record_identity(self, fact: str, *, tag: str | None = None) -> str:
        """A core identity fact — nearly immortal, always persisted."""
        tags = {"kind:identity"}
        if tag:
            tags.add(tag)
        return self._run(self._g.add(fact, tags=tags, origin=CoreOrigin.IDENTITY,
                                     is_shard=True, confidence=0.8))

    def record_future_memory(self, vision: str, *, theme: str | None = None) -> str:
        """A 'memory of the future' — a goal/vision stored as a guiding shard."""
        tags = {"horizon:future", "kind:vision"}
        if theme:
            tags.add(f"theme:{theme}")
        return self._run(self._g.add(vision, tags=tags, origin=CoreOrigin.PRIME,
                                     is_shard=True, confidence=0.6))

    # -- reading ---------------------------------------------------------
    def recall(self, query: str, *, top_k: int = 5, chain_depth: int = 1) -> list[str]:
        """Semantic+associative recall; returns shard contents as strings."""
        results = self._run(self._g.recall(query, top_k=top_k, chain_depth=chain_depth,
                                           strategy=RecallStrategy.HYBRID))
        return [str(r.node.content) for r in results]

    def recall_future(self, query: str | None = None, top_k: int = 3) -> list[str]:
        """Recall future-memory shards to foreshadow them (backcasting).

        Future memories are retrieved by their `horizon:future` tag rather than by
        query match, so they surface as guiding attractors even when the current
        scene's words don't resemble the vision text.
        """
        nodes = self._run(self._g.recall_by_tag("horizon:future"))
        contents = [str(n.content) for n in nodes[:top_k]]
        return contents

    def recall_lessons(self, query: str, *, top_k: int = 2) -> list[str]:
        """The most relevant integrated lessons (Prime `kind:lesson` shards).

        Ranked by hybrid match against `query`; if nothing matches, falls back to
        the strongest lessons overall so the self always remembers what it has
        learned.
        """
        if top_k <= 0:
            return []
        results = self._run(self._g.recall(query, top_k=top_k, chain_depth=0,
                                           context_tags={"kind:lesson"},
                                           origin_filter=CoreOrigin.PRIME,
                                           strategy=RecallStrategy.HYBRID))
        out = [str(r.node.content) for r in results]
        if len(out) < top_k:
            for n in self._run(self._g.recall_by_tag("kind:lesson")):
                c = str(n.content)
                if c not in out:
                    out.append(c)
                if len(out) >= top_k:
                    break
        return out[:top_k]

    def find_future_memory(self, vision: str) -> Optional[str]:
        """Locate the future-memory shard for `vision` (by `horizon:future` tag)."""
        want = vision.strip().lower()
        nodes = self._run(self._g.recall_by_tag("horizon:future"))
        for n in nodes:
            if str(n.content).strip().lower() == want:
                return n.id
        for n in nodes:
            c = str(n.content).strip().lower()
            if want in c or c in want:
                return n.id
        return None

    def reinforce_future(self, vision: str) -> Optional[str]:
        """Strengthen a future-memory shard (the vision is being lived toward).

        Uses MCMG's duplicate-reinforcement path: re-adding identical content
        raises its confidence and lowers its entropy, so it surfaces earlier in
        `recall_future`. Returns the shard id, or None if no such vision exists.
        """
        nid = self.find_future_memory(vision)
        if nid is None:
            return None
        node = self._g.get(nid)
        if node is None:
            return None
        return self._run(self._g.add(node.content, tags=set(node.tags),
                                     origin=node.origin, is_shard=True))

    def record_fulfilment(self, vision: str, statement: str, *,
                          themes: list[str] | None = None) -> str:
        """Mark a memory of the future as realised.

        Reinforces the original future shard (if present), writes a Prime lesson
        shard in the first person ("I became ..."), and links the two so recall
        of either chains to the other. Returns the new lesson shard id.
        """
        vid = self.reinforce_future(vision)
        tags = {f"theme:{t}" for t in (themes or [])}
        tags |= {"kind:lesson", "kind:fulfilled", "horizon:realised"}
        lid = self._run(self._g.add(statement, tags=tags, origin=CoreOrigin.PRIME,
                                    is_shard=True, confidence=0.8))
        if vid is not None:
            self._run(self._g.associate_bidirectional(vid, lid, weight=0.9))
        return lid

    def fulfilled(self) -> list[str]:
        """All fulfilment shards recorded so far."""
        return [str(n.content) for n in self._run(self._g.recall_by_tag("kind:fulfilled"))]

    def decay(self) -> dict:
        return self._run(self._g.decay())

    def stats(self) -> dict:
        return self._g.stats()

    def close(self) -> None:
        self._run(self._g.close())
        self._loop.close()
