"""Runtime configuration for an ANA simulation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ANAConfig:
    # Model gateway
    model: str = "claude-opus-5-5"
    use_llm: bool = True            # if False, everything runs deterministically
    max_tokens: int = 2000
    temperature_hint: str = "adaptive"  # thinking mode hint

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
    shard_db: str = "./ana_run/shards.db"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
