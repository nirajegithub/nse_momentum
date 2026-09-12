#!/usr/bin/env python3
"""
Multi-day ORB backtest driver.

Runs the corrected one-day comparison backtester for every weekday in the
requested date range, then combines its CSV outputs into one multi-day set.
It intentionally does not modify the underlying strategy engine.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--universe-json")
    parser.add_argument("--output-dir", default="backtest_orb_results")
    parser.add_argument(
        "--engine",
        default="scripts/historical_backtest_4_methods.py",
        help="Corrected one-day comparison backtester",
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    if end < start:
        parser.error("--end-date must be >= --start-date")

    dates = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            dates.append(cur.isoformat())
        cur += timedelta(days=1)

    root = Path.cwd()
    temp = root / args.output_dir / "_daily"
    temp.mkdir(parents=True, exist_ok=True)

    combined_signals = []
    combined_events = []
    daily_summary = []

    for day in dates:
        cmd = [sys.executable, args.engine, "--date", day,
               "--output-dir", str(temp)]
        if args.symbols:
            cmd += ["--symbols", *args.symbols]
        if args.limit is not None:
            cmd += ["--limit", str(args.limit)]
        if args.universe_json:
            cmd += ["--universe-json", args.universe_json]

        print("\n" + "=" * 72)
        print(f"RUNNING {day}")
        print("=" * 72)

        proc = subprocess.run(cmd, cwd=root)
        if proc.returncode != 0:
            daily_summary.append({
                "date": day,
                "status": "ERROR",
                "return_code": proc.returncode,
            })
            continue

        sig = temp / f"signals_compare_{day}.csv"
        evt = temp / f"events_compare_{day}.csv"

        day_signals = read_csv(sig)
        day_events = read_csv(evt)

        for row in day_signals:
            row["backtest_date"] = day
            combined_signals.append(row)
        for row in day_events:
            row["backtest_date"] = day
            combined_events.append(row)

        daily_summary.append({
            "date": day,
            "status": "OK",
            "signals": len(day_signals),
            "events": len(day_events),
        })

    out = root / args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    stem = f"{args.start_date}_{args.end_date}"
    write_csv(out / f"signals_orb_{stem}.csv", combined_signals)
    write_csv(out / f"events_orb_{stem}.csv", combined_events)
    write_csv(out / f"daily_orb_{stem}.csv", daily_summary)

    # Create a compact R summary for B/D from the combined signal file.
    by_method = {}
    for row in combined_signals:
        method = row.get("method", "")
        try:
            r = float(row.get("pnl_r", 0) or 0)
        except ValueError:
            r = 0.0
        item = by_method.setdefault(method, {"signals": 0, "total_r": 0.0})
        item["signals"] += 1
        item["total_r"] += r

    report = out / f"orb_multi_day_report_{stem}.md"
    lines = [
        f"# NSE Momentum ORB Multi-Day Backtest — {args.start_date} to {args.end_date}",
        "",
        "The underlying one-day engine is the corrected first-ORB state-machine version.",
        "",
        "## Rule",
        "",
        "- One first completed 15M close breaking the fixed 09:15 ORB per symbol/day.",
        "- Later continuation closes are ignored.",
        "- The first ORB opportunity remains consumed even if risk/T1 validation rejects it.",
        "- B1 uses the 09:15 close as stop.",
        "",
        "## Combined signals",
        "",
        "| Method | Signals | Total R | Avg R |",
        "|---|---:|---:|---:|",
    ]
    for method in ("B", "D", "A", "C"):
        item = by_method.get(method, {"signals": 0, "total_r": 0.0})
        avg = item["total_r"] / item["signals"] if item["signals"] else 0.0
        lines.append(
            f"| {method} | {item['signals']} | "
            f"{item['total_r']:.2f} | {avg:.2f} |"
        )

    lines += [
        "",
        "## Daily execution",
        "",
        "| Date | Status | Signals | Events |",
        "|---|---|---:|---:|",
    ]
    for row in daily_summary:
        lines.append(
            f"| {row['date']} | {row['status']} | "
            f"{row.get('signals', 0)} | {row.get('events', 0)} |"
        )

    report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n" + "=" * 72)
    print("ORB MULTI-DAY BACKTEST COMPLETE")
    print("=" * 72)
    for method in ("B", "D", "A", "C"):
        item = by_method.get(method, {"signals": 0, "total_r": 0.0})
        avg = item["total_r"] / item["signals"] if item["signals"] else 0.0
        print(
            f"Method {method}: Signals={item['signals']} | "
            f"Total R={item['total_r']:.2f} | Avg R={avg:.2f}"
        )
    print(f"\nSignals: {out / f'signals_orb_{stem}.csv'}")
    print(f"Events:  {out / f'events_orb_{stem}.csv'}")
    print(f"Daily:   {out / f'daily_orb_{stem}.csv'}")
    print(f"Report:  {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
