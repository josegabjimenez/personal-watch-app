#!/usr/bin/env python3
"""Inspect or explicitly initialize the separate reminder admission budget.

No provider calls. Initialization is an operator action after approval; neither
this command nor a nonzero number grants permission to activate a write domain.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reminder_ai_budget import ReminderBudget, BudgetBlocked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "initialize"))
    parser.add_argument("--budget", required=True, type=Path)
    parser.add_argument("--monthly-limit-usd", default="0")
    parser.add_argument("--confirm-initialize", action="store_true")
    args = parser.parse_args()
    try:
        budget = ReminderBudget(args.budget, monthly_limit_usd=args.monthly_limit_usd)
        if args.command == "initialize":
            if not args.confirm_initialize:
                parser.error("initialization requires an explicit approved operator action")
            budget.initialize()
        result = budget.snapshot()
        print(json.dumps(asdict(result), ensure_ascii=False, default=str, sort_keys=True))
        return 0
    except BudgetBlocked as error:
        print(json.dumps({"status": "blocked", "code": error.code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
