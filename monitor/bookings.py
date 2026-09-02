"""Confirmed bookings — the baseline each leg is measured against.

A booking does two things:

1. It becomes the leg's **effective ceiling**. Both current bookings sit just
   above their stated band max (Roma EUR 262.58 vs EUR 250, Veneza EUR 215.49
   vs EUR 215), so without this an option priced between the band max and the
   booking would be filtered out as `above_band` -- even though it is cheaper
   than what is already booked, which makes it exactly the alternative worth
   seeing. The band floor is untouched.

2. It gives every candidate a saving figure to be judged by, rather than an
   abstract band.

A booking is reference data supplied by the traveller, not something the source
has to surface. iH Hotels Venezia Salute Palace has never appeared in any cycle
(it is outside the top results for the Dorsoduro anchor, with or without the
rating filter), so it carries optional coordinates purely so it can be pinned.
"""

import json
import os

from . import prefs

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "bookings.json")


def _load():
    try:
        with open(_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    out = {}
    for key, entry in raw.items():
        if key.startswith("_") or not isinstance(entry, dict):
            continue
        try:
            leg_id = int(key)
        except ValueError:
            continue
        if not entry.get("hotel_name") or entry.get("price_per_night_eur") is None:
            continue
        out[leg_id] = dict(entry, leg_id=leg_id,
                           _norm=prefs.normalise(entry["hotel_name"]))
    return out


BOOKINGS = _load()


def for_leg(leg_id):
    return BOOKINGS.get(leg_id)


def reference_price(leg_id):
    b = BOOKINGS.get(leg_id)
    return b["price_per_night_eur"] if b else None


def effective_max(leg):
    """The leg's ceiling, raised to the booking price where one exists."""
    ref = reference_price(leg["leg_id"])
    return max(leg["max_per_night"], ref) if ref else leg["max_per_night"]


def is_booking(leg_id, hotel_name):
    """Does this property name refer to the booked hotel?

    Deliberately asymmetric. The booking name must appear in the property name,
    or the property name must be a *substantial* prefix-like fragment of it.
    A plain two-way substring test matched any property whose name happened to
    be a fragment of the booking's: "Salute" alone matched
    "iH Hotels Venezia Salute Palace", so an unrelated EUR 165 listing was
    labelled as the booking.
    """
    b = BOOKINGS.get(leg_id)
    if not b:
        return False
    n = prefs.normalise(hotel_name)
    if not n:
        return False
    ref = b["_norm"]
    if ref in n:
        return True
    # Reverse direction only for names close in length to the booking's, so a
    # single shared word cannot claim the match.
    return n in ref and len(n) >= 0.7 * len(ref)


def compare(leg_id, price_per_night, nights):
    """(pct difference vs the booking, total saving over the stay).

    Negative pct means cheaper than what is booked. Returns (None, None) for
    legs with no booking.
    """
    ref = reference_price(leg_id)
    if ref in (None, 0) or price_per_night is None:
        return None, None
    pct = round((price_per_night - ref) / ref * 100.0, 2)
    saving = round((ref - price_per_night) * nights, 2)
    return pct, saving
