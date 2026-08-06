#!/usr/bin/env python
"""One monitoring cycle.

Prints a single JSON object to stdout and nothing else, so the downstream
pipeline can consume it directly. Diagnostics go to stderr.

    python run_cycle.py            # run a cycle
    python run_cycle.py --dry-run  # plan only, spends no credits
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from monitor import config, core, state
from monitor.source import QuotaExhausted, ScrappaClient, SourceError


def _anchor_for(leg, cycle_index):
    """Rotate the zone anchor across cycles so every centroid is sampled
    over time while each leg still costs exactly one listing per run."""
    anchors = leg["anchors"]
    return anchors[cycle_index % len(anchors)]


def run_cycle(dry_run=False):
    now = datetime.now(timezone.utc)
    run_ts = now.isoformat()

    ledger = state.CreditLedger(config.MONTHLY_CREDITS)
    snapshot = state.load_snapshot()
    history = state.load_history()
    cycle_index = (sum(1 for _ in open(state.RUNS_PATH, encoding="utf-8"))
                   if os.path.exists(state.RUNS_PATH) else 0)

    run = {
        "run_ts": run_ts,
        "occupancy": config.OCCUPANCY,
        "credits_used_this_run": 0,
        "credits_remaining_month": ledger.remaining,
        "legs": [],
        "alerts": [],
        "pending_validation": [],
        "errors": [],
    }

    if ledger.state_lost:
        run["errors"].append({
            "leg_id": 0,
            "message": "quota_exhausted: credit counter lost with history present; "
                       "assuming worst case until the 1st-of-month reset",
        })
        return run

    if ledger.remaining < config.MIN_CREDITS_FOR_CYCLE:
        run["errors"].append({
            "leg_id": 0,
            "message": f"quota_exhausted: {ledger.remaining} credits remaining, "
                       f"{config.MIN_CREDITS_FOR_CYCLE} needed for a full cycle; cycle skipped",
        })
        return run

    client = None if dry_run else ScrappaClient(ledger)
    # The snapshot accumulates rather than being rebuilt: anchors rotate between
    # cycles, so a hotel absent from this cycle's sample has not disappeared and
    # must not read as new when its anchor comes round again.
    new_snapshot = dict(snapshot)

    for leg in config.LEGS:
        anchor = _anchor_for(leg, cycle_index)
        leg_out = {
            "leg_id": leg["leg_id"],
            "city": leg["city"],
            "check_in": leg["check_in"],
            "check_out": leg["check_out"],
            "query": anchor,
            "results": [],
            "excluded": [],
        }

        if dry_run:
            run["legs"].append(leg_out)
            continue

        try:
            props = client.search(anchor, leg["check_in"], leg["check_out"])
        except QuotaExhausted as exc:
            # Nothing left to spend: stop issuing calls but keep what we have.
            run["errors"].append({"leg_id": leg["leg_id"],
                                  "message": f"quota_exhausted: {exc}"})
            run["legs"].append(leg_out)
            break
        except SourceError as exc:
            # A single bad leg never aborts the cycle.
            run["errors"].append({"leg_id": leg["leg_id"], "message": str(exc)})
            run["legs"].append(leg_out)
            continue

        results = []
        for prop in props:
            row, excluded = core.build_result(prop, leg, snapshot, history, now)
            if row is not None:
                results.append(row)
            elif excluded is not None:
                leg_out["excluded"].append(excluded)

        results = core.rank(results)
        leg_out["results"] = results
        run["alerts"].extend(
            core.detect_alerts(leg, results, leg_out["excluded"], snapshot, now))

        for r in results:
            new_snapshot[f"{leg['leg_id']}:{r['hotel_id']}"] = {
                "hotel_name": r["hotel_name"],
                "price_per_night_eur": r["price_per_night_eur"],
                "rate_name": r["rate_name"],
                "band_status": r["band_status"],
                "run_ts": run_ts,
            }

        state.append_history([
            {"run_ts": run_ts, "leg_id": leg["leg_id"], "hotel_id": r["hotel_id"],
             "hotel_name": r["hotel_name"], "price_per_night_eur": r["price_per_night_eur"],
             "band_status": r["band_status"], "rate_name": r["rate_name"]}
            for r in results
        ])

        run["legs"].append(leg_out)

    if not dry_run:
        state.save_snapshot(new_snapshot)

    run["credits_used_this_run"] = ledger.used_this_run
    run["credits_remaining_month"] = ledger.remaining
    if not dry_run:
        state.append_run(run)
    return run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="plan the cycle without spending credits")
    args = ap.parse_args()

    try:
        run = run_cycle(dry_run=args.dry_run)
    except SourceError as exc:
        print(json.dumps({"errors": [{"leg_id": 0, "message": str(exc)}]}), flush=True)
        return 1

    print(json.dumps(run, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
