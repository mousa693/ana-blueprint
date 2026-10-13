"""`python -m ana.web [--host 127.0.0.1] [--port 8765] [--log run.jsonl ...]
[--allow-host NAME ...] [--allow-llm]`"""
from __future__ import annotations

import argparse

_WILDCARD = {"0.0.0.0", "::", "[::]", ""}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m ana.web",
                                description="ANA journey viewer (web UI)")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind address (default 127.0.0.1; only change if you mean it)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--log", action="append", default=[],
                   help="JSONL run log to preload (repeatable)")
    p.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                   help="extra Host-header name to accept besides localhost, 127.0.0.1 "
                        "and [::1] (repeatable; needed when binding to another interface)")
    p.add_argument("--allow-llm", action="store_true",
                   help="let POST /api/live request use_llm=true (off by default: "
                        "the endpoint is unauthenticated and LLM calls cost money)")
    args = p.parse_args(argv)

    import uvicorn
    from .server import create_app

    hosts = list(args.allow_host)
    if args.host not in _WILDCARD:
        hosts.append(args.host)
    elif not args.allow_host:
        print("warning: binding to a wildcard address; only localhost Host headers are "
              "accepted unless you pass --allow-host <name-or-ip>")
    app = create_app(logs=args.log, allowed_hosts=hosts, allow_llm=args.allow_llm)
    shown = f"[{args.host}]" if ":" in args.host else args.host
    print(f"ANA viewer on http://{shown}:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
