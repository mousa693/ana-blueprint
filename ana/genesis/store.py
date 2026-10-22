"""
On-disk home for everything Genesis collects.

One directory (``genesis_data/`` by default, git-ignored) holding plain JSON,
written atomically after every answer so a session can stop at any moment and
resume later.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path("genesis_data")

BATTERY_ANSWERS = "battery_answers.json"
INTERVIEW = "interview.json"
SEED_SELF = "seed_self.json"
FIDELITY = "fidelity_report.json"


class Store:
    def __init__(self, root: Path | str = DEFAULT_DIR) -> None:
        self.root = Path(root)

    def path(self, name: str) -> Path:
        return self.root / name

    def exists(self, name: str) -> bool:
        return self.path(name).exists()

    def read(self, name: str, default: Any = None) -> Any:
        p = self.path(name)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))

    def write(self, name: str, data: Any) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        target = self.path(name)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=f".{name}.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, target)
        return target
