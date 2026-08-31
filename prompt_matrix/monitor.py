"""CLI usage summary from local history.sqlite."""

from __future__ import annotations

import argparse
import json
import sys

from rich.console import Console
from rich.table import Table

try:
    from .history import usage_summary
except ImportError:
    from history import usage_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="assure monitor", description="Show local Send usage.")
    parser.add_argument("--show-cost", action="store_true")
    parser.add_argument("--show-latency", action="store_true", help="Not stored. Prints n/a.")
    parser.add_argument("--show-success-rate", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)
    data = usage_summary(days=args.days)
    if args.json:
        sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        return 0
    console = Console()
    table = Table(title=f"Usage ({data['days']} days, this machine)")
    table.add_column("Model")
    table.add_column("Runs", justify="right")
    table.add_column("Sends with reply", justify="right")
    if args.show_cost:
        table.add_column("Est. USD")
    table.add_column("Tokens", justify="right")
    if args.show_success_rate:
        table.add_column("Reply rate")
    for row in data["models"]:
        cells = [row["model"], str(row["runs"]), str(row["with_reply"])]
        if args.show_cost:
            cost = row.get("estimated_cost")
            cells.append("n/a" if cost is None else f"{cost:.4f}")
        cells.append(str(row.get("tokens") or 0))
        if args.show_success_rate:
            cells.append(f"{row['reply_rate']:.0%}" if row.get("reply_rate") is not None else "n/a")
        table.add_row(*cells)
    console.print(table)
    if args.show_latency:
        console.print("[dim]Latency is not stored in history.sqlite.[/dim]")
    console.print(f"Total runs {data['runs']}. Sends with a reply {data['with_reply']}.")
    return 0
