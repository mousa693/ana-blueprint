"""ANA web viewer — a small FastAPI app + offline single-page UI for
inspecting CycleRecord journeys (from JSONL logs or live in-process runs)."""
from .server import create_app

__all__ = ["create_app"]
