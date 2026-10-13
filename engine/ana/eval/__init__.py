"""ANA evaluation harness — the objective yardstick for tuning engine dynamics.

    from ana.eval import compute_metrics, run_journey, evaluate, compare

`python -m ana.eval` runs every built-in profile over several seeds and writes a
markdown + JSON report with a PASS/FAIL rubric.
"""
from .metrics import compute_metrics  # noqa: F401
from .harness import PROFILES, compare, evaluate, run_journey, seed_identity  # noqa: F401
