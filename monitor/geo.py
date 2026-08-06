"""Local zone filtering.

The source does not filter by neighbourhood, so proximity is computed here from
the `gps_coordinates` it returns. Zones overlap by design -- in the historic
centres of Rome, Florence and Venice the centroids sit a few hundred metres
apart -- so they are never treated as mutually exclusive: a property is scored
against every centroid of its leg and keeps the nearest one.
"""

import math

EARTH_RADIUS_M = 6371000.0


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def locate(prop, leg):
    """Return (nearest_zone, distance_m, in_target_zone, zone_inferred).

    Legs without zones (4 and 5) are always in-zone: the whole locality counts.
    """
    if not leg["zones"]:
        return None, None, True, False

    gps = prop.get("gps_coordinates") or {}
    lat, lon = gps.get("latitude"), gps.get("longitude")
    if lat is None or lon is None:
        # No coordinates: the caller may fall back to the address, but we never
        # silently claim a zone we could not measure.
        return None, None, False, True

    distances = {
        name: haversine_m(lat, lon, clat, clon)
        for name, (clat, clon) in leg["zones"].items()
    }
    nearest = min(distances, key=distances.get)
    dist = distances[nearest]
    return nearest, round(dist, 1), dist <= leg["radius_m"], False
