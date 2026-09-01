"""Explicit human judgements on individual properties.

Matched on a normalised name rather than property_token: the same property can
carry a different token depending on which query surfaced it, and several
judged properties have not been observed yet at all.

Disliked properties are dropped from results (recorded in `excluded` so the
decision stays auditable). Liked ones are pinned to the top of their leg.
"""

import json
import os
import re
import unicodedata

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "preferences.json")


def normalise(name):
    """Fold accents, drop punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _load():
    try:
        with open(_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, {}
    def index(section):
        out = {}
        for leg_id, names in (raw.get(section) or {}).items():
            out[int(leg_id)] = [normalise(n) for n in names if n]
        return out
    return index("liked"), index("disliked")


LIKED, DISLIKED = _load()


def verdict(leg_id, hotel_name):
    """Return 'liked', 'disliked', or None.

    Substring match in both directions: the source's name is often longer than
    what a human writes down ("Midway" vs "Hotel Midway", "INNSiDE by Melia
    Milano Torre GalFa" vs the accented original).
    """
    n = normalise(hotel_name)
    if not n:
        return None
    for label, table in (("disliked", DISLIKED), ("liked", LIKED)):
        for entry in table.get(leg_id, []):
            if entry and (entry in n or n in entry):
                return label
    return None
