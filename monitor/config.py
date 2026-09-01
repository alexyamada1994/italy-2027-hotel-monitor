"""Trip definition, target zones, and quota policy.

Everything the operator is likely to tune lives here. The rest of the package
reads this module and never hardcodes trip facts.
"""

# --- Quota -------------------------------------------------------------------
# Free tier: 500 credits/month, 1 credit per request, reset on the 1st.
# Credits do not roll over and there is no card on file: when the quota runs
# out the API fails and the agent stops. That is intended.
MONTHLY_CREDITS = 500

# The spec reserved 450 listings / 50 details. Detail calls were measured to
# return exactly the same constraint-bearing fields as the listing (same
# `amenities`/`excluded_amenities`, no cancellation data, empty `prices`), so
# they buy nothing and the whole budget goes to listings.
LISTING_CREDITS = 500
DETAIL_CREDITS = 0

# 6 listings/cycle * ~73 cycles/month = ~438 credits, inside the allocation.
CYCLE_INTERVAL_HOURS = 10

# Abort the cycle rather than run it partially.
MIN_CREDITS_FOR_CYCLE = 6

# --- Source ------------------------------------------------------------------
SEARCH_URL = "https://scrappa.co/api/google-hotels/search"
OCCUPANCY = {"adults": 2, "rooms": 1}

# Minimum guest rating, applied server-side. Google Hotels takes a coded value,
# not a number: 7 = 3.5+, 8 = 4.0+, 9 = 4.5+ (a literal 4.5 is rejected with
# HTTP 422). Filtering at the source rather than locally is what makes this
# affordable: a page is 20 properties either way, so asking for 4.5+ returns 20
# qualifying hotels for one credit instead of ~8 survivors out of 20.
MIN_RATING_CODE = 9
RATING_CODE_TO_MIN = {7: 3.5, 8: 4.0, 9: 4.5}
MIN_RATING = RATING_CODE_TO_MIN[MIN_RATING_CODE]

# Properties not re-observed within this window drop off the dashboard. Anchors
# rotate, so a hotel can miss a few cycles legitimately -- but a price from
# weeks ago must not keep anchoring the headline trip total.
STALE_AFTER_DAYS = 14

FIXED_PARAMS = {"adults": 2, "currency": "EUR", "gl": "it", "hl": "en",
                "rating": MIN_RATING_CODE}

# A 5.0 from 3 reviews is not evidence. Properties below this review count keep
# their place in the results but are flagged `low_confidence` and demoted in the
# tiebreak, so a thinly-reviewed 4.8 never outranks a well-reviewed 4.6. Set to
# 0 to disable the flag entirely; nothing is ever dropped for it.
MIN_REVIEWS_FOR_CONFIDENCE = 30

# Hostels. Google Hotels labels them `type: "hotel"`, so the type field cannot
# separate them; the API's `property_types` is an include-list, which would mean
# enumerating every other code. Name matching is the reliable lever here.
# Word-boundary anchored so "Hostellerie" (a legitimate inn) is not caught.
EXCLUDE_NAME_PATTERNS = [
    r"\bhostels?\b",
    r"\bostell[oi]\b",
    r"\bbackpackers?\b",
    r"\bdormitor(?:y|io)\b",
    r"\byouth\s+hostel\b",
]

# --- City tax ----------------------------------------------------------------
# Google Hotels does not expose the Italian tassa di soggiorno: the gap between
# `lowest` and `before_taxes_fees` was measured at ~0.5% (EUR 102 vs 101.51),
# far below Rome's ~EUR 15/night for two adults. These are static per-person,
# per-night estimates, applied locally and reported in `city_tax_eur` so the
# figure stays separable from source data. Set to 0.0 to disable.
CITY_TAX_PER_PERSON_PER_NIGHT = {
    1: 7.50,   # Roma
    2: 6.00,   # Firenze
    3: 5.00,   # Venezia
    4: 3.00,   # Val Gardena
    5: 4.00,   # Cortina
    6: 5.00,   # Milano
}

# The source exposes a single nightly figure and no vendor rate names
# (`prices` came back empty on the detail endpoint), so `rate_name` records
# which figure was read rather than how a vendor names its rate. It is constant,
# which means `rate_changed` is always False -- the audit the spec wanted is not
# available from this source.
RATE_NAME = "google_hotels:rate_per_night.lowest(tax_incl)"

# --- Legs --------------------------------------------------------------------
# `anchors` are the query strings rotated across cycles. The mandated strings
# ("hotels Rome city center" etc.) were verified to return zero properties;
# zone-anchored queries return results that are ~100% inside the target radius.
LEGS = [
    {
        "leg_id": 1, "city": "Roma",
        "check_in": "2027-05-13", "check_out": "2027-05-17", "nights": 4,
        "min_per_night": 180, "max_per_night": 250,
        "radius_m": 700,
        "zones": {
            "Piazza di Spagna": (41.9058, 12.4823),
            "Fontana di Trevi": (41.9009, 12.4833),
            "Pantheon": (41.8986, 12.4769),
            "Piazza Barberini": (41.9036, 12.4886),
        },
        "anchors": [
            "Piazza di Spagna, Rome", "Fontana di Trevi, Rome",
            "Pantheon, Rome", "Piazza Barberini, Rome",
        ],
        "require_ac": True, "require_parking": False,
    },
    {
        "leg_id": 2, "city": "Florenca",
        "check_in": "2027-05-17", "check_out": "2027-05-19", "nights": 2,
        "min_per_night": 200, "max_per_night": 250,
        "radius_m": 500,
        "zones": {
            "Santa Maria del Fiore": (43.7731, 11.2560),
            "Ponte Vecchio": (43.7680, 11.2531),
            "San Giovanni": (43.7733, 11.2547),
        },
        "anchors": ["Duomo, Florence", "Ponte Vecchio, Florence"],
        "require_ac": True, "require_parking": False,
    },
    {
        "leg_id": 3, "city": "Veneza",
        "check_in": "2027-05-19", "check_out": "2027-05-21", "nights": 2,
        "min_per_night": 180, "max_per_night": 215,
        "radius_m": 600,
        "zones": {
            "San Marco": (45.4341, 12.3388),
            "Dorsoduro": (45.4310, 12.3270),
        },
        "anchors": ["San Marco, Venice", "Dorsoduro, Venice"],
        "require_ac": True, "require_parking": False,
    },
    {
        # A valley, not a city: "Val Gardena" returns zero properties, so the
        # three localities are rotated instead. No zone filter applies.
        "leg_id": 4, "city": "Val Gardena",
        "check_in": "2027-05-21", "check_out": "2027-05-25", "nights": 4,
        "min_per_night": 220, "max_per_night": 300,
        "radius_m": None, "zones": {},
        "anchors": ["Ortisei", "Selva di Val Gardena", "Santa Cristina Valgardena"],
        "require_ac": False, "require_parking": True,
    },
    {
        "leg_id": 5, "city": "Cortina d'Ampezzo",
        "check_in": "2027-05-25", "check_out": "2027-05-28", "nights": 3,
        "min_per_night": 220, "max_per_night": 300,
        "radius_m": None, "zones": {},
        "anchors": ["Cortina d'Ampezzo"],
        "require_ac": False, "require_parking": True,
    },
    {
        "leg_id": 6, "city": "Milao",
        "check_in": "2027-05-28", "check_out": "2027-05-29", "nights": 1,
        "min_per_night": 150, "max_per_night": 250,
        "radius_m": 800,
        "zones": {
            "Duomo": (45.4642, 9.1900),
            "Milano Centrale": (45.4862, 9.2045),
            "Brera": (45.4720, 9.1880),
        },
        "anchors": ["Duomo, Milan", "Milano Centrale", "Brera, Milan"],
        "require_ac": True, "require_parking": False,
    },
]

LEGS_BY_ID = {leg["leg_id"]: leg for leg in LEGS}

# Legs 4 and 5 are shoulder season in the Dolomites: the ski season has ended
# and trekking has not started, so thin inventory is expected rather than a
# tightening market. Suppress the "<3 in_band options" alert there until then.
SHOULDER_SEASON_LEGS = {4, 5}
SHOULDER_SEASON_UNTIL = "2027-02-01"

MIN_IN_BAND_OPTIONS = 3
ALERT_DROP_PCT = 8.0          # vs 7-day moving average
DETAIL_TRIGGER_DELTA_PCT = 10.0
TIEBREAK_WINDOW_EUR = 10.0    # options within +/- this are ranked by amenities

# Pages fetched per leg per cycle. A page is 20 properties and one credit,
# and page N is only reachable via page N-1's token, so this multiplies cost
# directly: 2 pages x 6 legs = 12 credits/cycle. Override per run with --pages.
PAGES_PER_LEG = 2

# Query every zone anchor each cycle instead of rotating one per cycle.
# Rotation left most properties invisible for several cycles running; a sweep
# reaches anything near any centroid in a single run. Cost is
# (anchors x pages) per leg = 30 credits/cycle at 2 pages.
SWEEP_ALL_ANCHORS = True
