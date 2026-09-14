#!/usr/bin/env python
"""Compatibility wrapper for ``deeptutor start``."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deeptutor.runtime.launcher import start  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start DeepTutor Web.")
    parser.add_argument(
        "--home",
        type=Path,
        default=None,
        help="Runtime workspace root. Defaults to the current directory.",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Use the Next.js development server for frontend work.",
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help=(
            "Disable authentication for a loopback single-user session. Only "
            "safe because uvicorn binds 127.0.0.1 by default; never use this "
            "when the backend is reachable from the network (S-AUTH-01)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.no_auth:
        os.environ["AUTH_ENABLED"] = "false"
        os.environ["NEXT_PUBLIC_AUTH_ENABLED"] = "false"
    start(home=args.home, dev=args.dev)


if __name__ == "__main__":
    main()
