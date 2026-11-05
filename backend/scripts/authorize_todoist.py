#!/usr/bin/env python3
"""Explicit local Todoist setup; never prints authorization URLs or secrets."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from todoist_auth import CredentialStore, TodoistAuthError, authorize_local  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Personal Watch App Todoist authorization. Localhost callbacks are for local testing/one-time setup only. Prepare a private directory first; its credentials file is selected explicitly.")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("authorize", help="Open dedicated browser consent and save a private rotating token bundle")
    setup.add_argument("--credentials-file", type=Path, required=True)
    setup.add_argument("--port", type=int, default=0)
    setup.add_argument("--timeout", type=float, default=120)
    status = commands.add_parser("status", help="Read only local setup status; no token output or provider call")
    status.add_argument("--credentials-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "authorize":
            print("Opening Todoist consent for local setup. Authorization details stay in the browser.", flush=True)
            result = authorize_local(args.credentials_file, port=args.port, timeout=args.timeout)
        else:
            result = CredentialStore(args.credentials_file).status()
        print(json.dumps(result, sort_keys=True))
        return 0
    except TodoistAuthError as exc:
        # All module errors are fixed safe codes, never provider bodies/URLs.
        print("Todoist setup: " + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Todoist setup cancelled; existing credentials retained.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
