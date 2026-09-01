#!/usr/bin/env python
"""Generate docs/data.json for the dashboard from the append-only run log.

Reads state/runs.ndjson (every cycle, in order) and produces:
  - the latest known row per (leg, hotel), since anchors rotate and no single
    cycle sees every hotel;
  - a price series per hotel for the history chart;
  - per-leg roll-ups and a trip total built from the cheapest in-band option.
"""

import json
import os
from datetime import datetime, timezone

from monitor import config, core, prefs, state

DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
OUT_PATH = os.path.join(DOCS_DIR, "data.json")

BUDGET_FLOOR_EUR = 3170
BUDGET_CEILING_EUR = 4280


def _load_runs():
    runs = []
    if not os.path.exists(state.RUNS_PATH):
        return runs
    with open(state.RUNS_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                runs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    runs.sort(key=lambda r: r.get("run_ts") or "")
    return runs


def _dedupe_alerts(alerts):
    """One entry per (hotel, alert type), most recent kept.

    The raw feed repeats the same property every time an anchor resurfaces it,
    which is what made the listing look like it was echoing names.
    """
    seen = {}
    for a in alerts:
        if prefs.verdict(a.get("leg_id"), a.get("hotel_name")) == "disliked":
            continue
        seen[(a.get("type"), core.identity(a.get("leg_id"), a.get("hotel_name") or ""))] = a
    return sorted(seen.values(), key=lambda a: a.get("run_ts") or "", reverse=True)[:40]


def _current(row, now=None):
    """A row still worth showing: meets today's rating floor and was seen
    recently. History keeps everything; the dashboard shows what is current."""
    if (row.get("rating") or 0) < config.MIN_RATING:
        return False
    if core.is_excluded_property(row.get("hotel_name")):
        return False
    if row.get("preference") == "disliked":
        return False
    seen = row.get("last_seen")
    if not seen:
        return False
    try:
        age = (now or datetime.now(timezone.utc)) - datetime.fromisoformat(seen)
    except ValueError:
        return True
    return age.days <= config.STALE_AFTER_DAYS


def build():
    runs = _load_runs()

    latest = {}          # identity -> most recent observation of a property
    series = {}          # leg_id -> [[run_ts, cheapest in-band price], ...]
    alerts = []

    for run in runs:
        ts = run.get("run_ts")
        for leg in run.get("legs", []):
            lid = leg["leg_id"]
            for row in leg.get("results", []):
                # Keyed on stable identity, not property_token: the same hotel
                # arrives under different tokens from different anchors, which
                # is what put duplicate rows in the listing.
                key = core.identity(lid, row["hotel_name"])
                # Derive low_confidence rather than trusting the stored value:
                # rows recorded before the field existed would otherwise read
                # as well-evidenced and outrank properly flagged ones.
                latest[key] = dict(
                    row, leg_id=lid, last_seen=ts, query=leg.get("query"),
                    low_confidence=(row.get("review_count") or 0)
                    < config.MIN_REVIEWS_FOR_CONFIDENCE,
                    # Derived, not trusted: rows predate the field, and a
                    # judgement added today must apply to old observations.
                    preference=prefs.verdict(lid, row.get("hotel_name")))
            # An above-band observation is still the newest truth about the
            # price. Without this, a hotel that rose above the ceiling kept
            # displaying its last in-band price forever -- Ca 'dei Dogi showed
            # EUR 202 while actually selling at EUR 217. Carry the enriched
            # fields forward and update price and band.
            for e in leg.get("excluded", []):
                if e.get("band_status") != "above_band":
                    continue
                nm = e.get("hotel_name") or ""
                key = core.identity(lid, nm)
                prev = latest.get(key)
                if not prev or (prev.get("last_seen") or "") > (ts or ""):
                    continue
                latest[key] = dict(prev,
                                   price_per_night_eur=e.get("price_per_night_eur") or 0.0,
                                   total_stay_eur=round((e.get("price_per_night_eur") or 0.0)
                                                        * config.LEGS_BY_ID[lid]["nights"], 2),
                                   band_status="above_band", last_seen=ts,
                                   delta_vs_last_run_pct=None)
            # One point per leg per cycle: the best in-band price on offer.
            band = [r["price_per_night_eur"] for r in leg.get("results", [])
                    if r["band_status"] == "in_band"]
            if band:
                series.setdefault(str(lid), []).append([ts, min(band)])
        for a in run.get("alerts", []):
            alerts.append(dict(a, run_ts=ts))

    legs_out = []
    trip_low = 0
    trip_complete = True

    for leg in config.LEGS:
        lid = leg["leg_id"]
        pool = [r for k, r in latest.items()
                if r["leg_id"] == lid and _current(r)]
        # In-band first, then below-band: below-band is kept for human review
        # but must not bury the options that actually meet the brief. Inside
        # each group use the monitor's own ranking, so the breakfast / AC /
        # review-confidence tiebreak survives into the dashboard.
        rows = (core.rank([r for r in pool if r["band_status"] == "in_band"]) +
                core.rank([r for r in pool if r["band_status"] != "in_band"]))
        in_band = [r for r in rows if r["band_status"] == "in_band"]

        if in_band:
            # Explicitly the cheapest, not rows[0]: the list is rank-ordered,
            # where a pricier option can lead on breakfast or review evidence.
            trip_low += min(r["price_per_night_eur"] for r in in_band) * leg["nights"]
        else:
            trip_complete = False

        legs_out.append({
            "leg_id": lid,
            "city": leg["city"],
            "check_in": leg["check_in"],
            "check_out": leg["check_out"],
            "nights": leg["nights"],
            "min_per_night": leg["min_per_night"],
            "max_per_night": leg["max_per_night"],
            "radius_m": leg["radius_m"],
            "zones": [{"name": n, "lat": la, "lon": lo}
                      for n, (la, lo) in leg["zones"].items()],
            "requires": ("parking" if leg["require_parking"] else
                         "air conditioning" if leg["require_ac"] else None),
            "candidates": rows,
            "in_band_count": len(in_band),
            "below_band_count": sum(1 for r in rows if r["band_status"] == "below_band"),
        })

    last_run = runs[-1] if runs else {}
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "last_run_ts": last_run.get("run_ts"),
        "cycles": len(runs),
        "credits_used_month": config.MONTHLY_CREDITS - last_run.get(
            "credits_remaining_month", config.MONTHLY_CREDITS),
        "credits_remaining_month": last_run.get("credits_remaining_month"),
        "monthly_credits": config.MONTHLY_CREDITS,
        "budget_floor_eur": BUDGET_FLOOR_EUR,
        "budget_ceiling_eur": BUDGET_CEILING_EUR,
        "trip_total_low_eur": round(trip_low, 2) if trip_complete else None,
        "trip_total_partial": not trip_complete,
        "legs": legs_out,
        "series": series,
        "alerts": _dedupe_alerts(alerts),
        "errors": last_run.get("errors", []),
    }

    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    total = sum(len(l["candidates"]) for l in legs_out)
    print(f"docs/data.json: {len(runs)} cycles, {total} tracked properties, "
          f"{len(payload['series'])} price series, {len(alerts)} alerts")


if __name__ == "__main__":
    build()
