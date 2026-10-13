"""`python -m ana.web [--host 127.0.0.1] [--port 8765] [--log run.jsonl ...]`"""
from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m ana.web",
                                description="ANA journey viewer (web UI)")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind address (default 127.0.0.1; only change if you mean it)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--log", action="append", default=[],
                   help="JSONL run log to preload (repeatable)")
    args = p.parse_args(argv)

    import uvicorn
    from .server import create_app

    app = create_app(logs=args.log)
    print(f"ANA viewer on http://{args.host}:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
