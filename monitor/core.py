"""Normalisation, constraint filtering, deltas and alerts."""

from datetime import datetime, timedelta, timezone

from . import config, geo

# Amenity strings as the source spells them (observed vocabulary).
_AC_TOKENS = ("air conditioning",)
_PARKING_TOKENS = ("free parking", "parking")
_FREE_BREAKFAST = ("free breakfast",)
_PAID_BREAKFAST = ("breakfast ($)",)


def _has(tokens, amenities):
    return any(any(t == a.lower() for t in tokens) for a in amenities)


def read_amenities(prop):
    """Resolve the three amenity constraints from the listing.

    Returns (has_ac, has_parking, breakfast_included, unverified) where each
    flag is True / False / None. None means the source said nothing -- absence
    of information is not absence of the attribute, so it is never read as a
    negative and never causes a drop.
    """
    have = [a.lower() for a in (prop.get("amenities") or [])]
    lack = [a.lower() for a in (prop.get("excluded_amenities") or [])]
    unverified = []

    def resolve(tokens, label):
        if _has(tokens, have):
            return True
        if _has(tokens, lack):
            return False
        unverified.append(label)
        return None

    has_ac = resolve(_AC_TOKENS, "has_ac")
    has_parking = resolve(_PARKING_TOKENS, "has_parking")

    if _has(_FREE_BREAKFAST, have):
        breakfast = True
    elif _has(_PAID_BREAKFAST, have) or _has(_FREE_BREAKFAST, lack):
        breakfast = False          # offered but chargeable, or explicitly absent
    else:
        breakfast = None
        unverified.append("breakfast_included")

    return has_ac, has_parking, breakfast, unverified


def normalise_price(prop, leg):
    """Per-night price with taxes and mandatory fees, plus municipal city tax.

    `rate_per_night.extracted_lowest` is the tax-inclusive figure the source
    reports (`before_taxes_fees` is the lower, pre-tax one). The Italian
    tassa di soggiorno is not in the feed at all, so it is added from the
    static table in config and reported separately in `city_tax_eur`.
    """
    rpn = prop.get("rate_per_night") or {}
    base = rpn.get("extracted_lowest")
    if base is None:
        return None, None, None

    per_person = config.CITY_TAX_PER_PERSON_PER_NIGHT.get(leg["leg_id"], 0.0)
    city_tax = round(per_person * config.OCCUPANCY["adults"], 2)

    per_night = round(float(base) + city_tax, 2)
    total_stay = round(per_night * leg["nights"], 2)
    return per_night, total_stay, city_tax


def classify(per_night, leg):
    if per_night < leg["min_per_night"]:
        return "below_band"
    if per_night > leg["max_per_night"]:
        return "above_band"
    return "in_band"


def seven_day_average(history, leg_id, hotel_id, now):
    cutoff = now - timedelta(days=7)
    vals = [
        r["price_per_night_eur"] for r in history
        if r.get("leg_id") == leg_id and r.get("hotel_id") == hotel_id
        and r.get("price_per_night_eur") is not None
        and _parse_ts(r.get("run_ts")) is not None
        and _parse_ts(r["run_ts"]) >= cutoff
    ]
    return sum(vals) / len(vals) if vals else None


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _pct_delta(current, reference):
    if reference in (None, 0):
        return None
    return round((current - reference) / reference * 100.0, 2)


def build_result(prop, leg, snapshot, history, now):
    """Turn one raw property into an output row, or into an exclusion."""
    hotel_id = prop.get("property_token")
    name = prop.get("name") or ""
    if not hotel_id:
        return None, {"hotel_id": None, "hotel_name": name, "price_per_night_eur": 0.0,
                      "band_status": None, "reason": "missing property_token"}

    per_night, total_stay, city_tax = normalise_price(prop, leg)
    if per_night is None:
        return None, {"hotel_id": hotel_id, "hotel_name": name, "price_per_night_eur": 0.0,
                      "band_status": None, "reason": "no price returned by source"}

    band = classify(per_night, leg)
    zone, dist, in_zone, inferred = geo.locate(prop, leg)

    # Above band is recorded by name, price and reason only -- but it keeps its
    # id so a tracked hotel leaving the band can still be detected.
    if band == "above_band":
        return None, {"hotel_id": hotel_id, "hotel_name": name,
                      "price_per_night_eur": per_night, "band_status": "above_band",
                      "reason": f"above_band (max EUR {leg['max_per_night']}/night)"}

    if not in_zone:
        return None, {"hotel_id": hotel_id, "hotel_name": name,
                      "price_per_night_eur": per_night, "band_status": band,
                      "reason": f"outside target zone ({zone} {dist}m > {leg['radius_m']}m)"}

    has_ac, has_parking, breakfast, unverified = read_amenities(prop)

    # Hard constraints. A definite False drops the property; None keeps it.
    if leg["require_ac"] and has_ac is False:
        return None, {"hotel_id": hotel_id, "hotel_name": name,
                      "price_per_night_eur": per_night, "band_status": band,
                      "reason": "no air conditioning (hard constraint)"}
    if leg["require_parking"] and has_parking is False:
        return None, {"hotel_id": hotel_id, "hotel_name": name,
                      "price_per_night_eur": per_night, "band_status": band,
                      "reason": "no parking (hard constraint)"}

    # Only the constraints this leg actually enforces count as unverified.
    relevant = {"has_ac"} if leg["require_ac"] else set()
    if leg["require_parking"]:
        relevant.add("has_parking")
    unverified = [u for u in unverified if u in relevant]

    # Free cancellation is absent from both the listing and the detail
    # endpoint, so it can never be verified from this source.
    unverified.append("free_cancellation")

    key = f"{leg['leg_id']}:{hotel_id}"
    prev = snapshot.get(key) or {}
    prev_price = prev.get("price_per_night_eur")
    avg7 = seven_day_average(history, leg["leg_id"], hotel_id, now)

    row = {
        "hotel_id": hotel_id,
        "hotel_name": name,
        "property_type": prop.get("type"),
        "nearest_zone": zone,
        "distance_to_zone_m": dist,
        "in_target_zone": in_zone,
        "zone_inferred": inferred,
        "price_per_night_eur": per_night,
        "total_stay_eur": total_stay,
        "rate_name": config.RATE_NAME,
        "rate_changed": bool(prev.get("rate_name") and prev["rate_name"] != config.RATE_NAME),
        "taxes_included": True,
        "city_tax_eur": city_tax,
        "fx_converted": False,
        "free_cancellation_until": None,
        "has_ac": has_ac,
        "has_parking": has_parking,
        "breakfast_included": breakfast,
        "rating": prop.get("overall_rating") or 0.0,
        "review_count": prop.get("reviews") or 0,
        "url": prop.get("link") or "",
        "band_status": band,
        "detail_checked_at": None,
        "unverified_constraints": unverified,
        "delta_vs_last_run_pct": _pct_delta(per_night, prev_price),
        "delta_vs_7d_avg_pct": _pct_delta(per_night, avg7),
    }
    return row, None


def rank(results):
    """Cheapest first; inside a +/- EUR 10 window prefer breakfast, then AC,
    then rating."""
    def key(r):
        bucket = round(r["price_per_night_eur"] / config.TIEBREAK_WINDOW_EUR)
        return (bucket,
                0 if r["breakfast_included"] else 1,
                0 if r["has_ac"] else 1,
                -(r["rating"] or 0.0),
                r["price_per_night_eur"])
    return sorted(results, key=key)


def _shoulder_suppressed(leg_id, now):
    return (leg_id in config.SHOULDER_SEASON_LEGS
            and now < datetime.fromisoformat(config.SHOULDER_SEASON_UNTIL + "T00:00:00+00:00"))


def detect_alerts(leg, results, excluded, snapshot, now):
    """Only the four conditions that count as signal. Dynamic-pricing noise
    below 8% is not one of them."""
    alerts = []
    in_band = [r for r in results if r["band_status"] == "in_band"]

    for r in in_band:
        key = f"{leg['leg_id']}:{r['hotel_id']}"
        was_tracked = key in snapshot

        d7 = r["delta_vs_7d_avg_pct"]
        if (was_tracked and not r["rate_changed"]
                and d7 is not None and d7 <= -config.ALERT_DROP_PCT):
            alerts.append({
                "type": "price_drop", "leg_id": leg["leg_id"],
                "hotel_id": r["hotel_id"], "hotel_name": r["hotel_name"],
                "detail": f"{d7}% vs 7d average, now EUR {r['price_per_night_eur']}/night",
            })

        # New in-band hotel in a target zone. Free cancellation is excluded
        # from this gate because the source cannot verify it -- keeping it
        # would mean this alert could never fire at all.
        if not was_tracked and r["in_target_zone"]:
            hard_ok = (not leg["require_ac"] or r["has_ac"] is not False) and \
                      (not leg["require_parking"] or r["has_parking"] is not False)
            if hard_ok:
                alerts.append({
                    "type": "new_in_band", "leg_id": leg["leg_id"],
                    "hotel_id": r["hotel_id"], "hotel_name": r["hotel_name"],
                    "detail": f"EUR {r['price_per_night_eur']}/night, "
                              f"{r['nearest_zone']} {r['distance_to_zone_m']}m",
                })

    # Tracked hotel that left the band upwards. Above-band properties are not
    # in `results` by design, so this reads the exclusion list.
    gone_above = {e["hotel_id"]: e for e in excluded
                  if e.get("band_status") == "above_band" and e.get("hotel_id")}
    for key, prev in snapshot.items():
        pleg, _, phid = key.partition(":")
        if int(pleg) != leg["leg_id"] or prev.get("band_status") != "in_band":
            continue
        now_row = gone_above.get(phid)
        if now_row is not None:
            alerts.append({
                "type": "left_band", "leg_id": leg["leg_id"],
                "hotel_id": phid, "hotel_name": prev.get("hotel_name", ""),
                "detail": f"in_band -> above_band at EUR {now_row['price_per_night_eur']}/night",
            })

    if len(in_band) < config.MIN_IN_BAND_OPTIONS and not _shoulder_suppressed(leg["leg_id"], now):
        alerts.append({
            "type": "thin_inventory", "leg_id": leg["leg_id"],
            "hotel_id": None, "hotel_name": None,
            "detail": f"only {len(in_band)} in_band options (threshold {config.MIN_IN_BAND_OPTIONS})",
        })

    return alerts
