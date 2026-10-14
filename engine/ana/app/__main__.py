"""``python -m ana.app [--host 127.0.0.1] [--port 8000]``"""
from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ana.app", description="Run the ANA web product.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--log-level", default="info")
    a = ap.parse_args(argv)
    logging.basicConfig(level=getattr(logging, a.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .config import ConfigError
    from .main import create_app

    try:
        app = create_app()
    except ConfigError as e:
        print(f"ana: configuration error: {e}", file=sys.stderr)
        return 2
    import uvicorn

    uvicorn.run(app, host=a.host, port=a.port, log_level=a.log_level, workers=1,
                proxy_headers=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
