"""Runtime configuration for an ANA simulation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ANAConfig:
    # Model gateway
    model: str = "claude-opus-5-5"
    use_llm: bool = True            # if False, everything runs deterministically
    max_tokens: int = 16000          # non-streaming ceiling; scenes use far less
    effort: str = "medium"           # Opus 5.5 effort: low|medium|high|xhigh|max
    use_fallbacks: bool = True       # server-side refusal fallbacks

    # Loop
    max_cycles: int = 12
    seed: int = 42

    # Synchronicity
    synchronicity_base_prob: float = 0.35
    synchronicity_cooldown: int = 2

    # Entropy
    entropy_prob: float = 0.3

    # Self-alignment safety band
    stress_ceiling: float = 0.85
    stress_floor_for_boredom: float = 0.1
    # Themes the user has asked never to be confronted with (trauma guards).
    forbidden_themes: list[str] = field(default_factory=list)
    always_hope_after_dark: bool = True

    # Persistence
    data_dir: Path = Path("./ana_run")
    # SQLite shard store used when build_session(persist_shards=True). Defaults
    # to <data_dir>/shards.db. One DB may hold many identities: shards are
    # scoped per IdentityCore.identity_id.
    shard_db: Optional[str] = None
    # Identity id for the default identity build_session creates when none is
    # supplied (None -> derived from its name, "the-protagonist").
    identity_id: Optional[str] = None

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if self.shard_db is None:
            self.shard_db = str(self.data_dir / "shards.db")

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.shard_db and self.shard_db != ":memory:":
            Path(self.shard_db).parent.mkdir(parents=True, exist_ok=True)
