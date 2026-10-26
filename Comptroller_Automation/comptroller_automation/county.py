"""County and map-position lookup. Websites almost never state their county,
so it comes from the US Census geocoder (free, no API key) for a given street
address. The same lookup gives the address's coordinates, used to find the
office nearest to the address on file."""

import functools
import math
import re

import requests

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
# The geocoder often finds nothing when a suite is included; a suite doesn't
# change where the building is, so it's left out of the lookup.
_SUITE = re.compile(r"[\s,]*(?:\b(?:suite|ste|unit|bldg|building|floor|fl|room|rm)\b\.?|#)\s*#?\s*[\w-]+.*$", re.I)


@functools.lru_cache(maxsize=4096)
def geocode(street, city, state, zip_code):
    """(latitude, longitude, county) for an address, or None if the Census
    geocoder can't match it. County is upper case without "County"."""
    try:
        resp = requests.get(CENSUS_URL, timeout=15, params={
            "address": f"{_SUITE.sub('', street or '')}, {city}, {state} {zip_code}",
            "benchmark": "Public_AR_Current", "vintage": "Current_Current", "format": "json",
        })
        resp.raise_for_status()
        matches = resp.json().get("result", {}).get("addressMatches", [])
        if not matches:
            return None
        coords = matches[0].get("coordinates", {})
        counties = matches[0].get("geographies", {}).get("Counties", [])
        county = (counties[0].get("BASENAME") or "").strip().upper() if counties else ""
        return coords.get("y"), coords.get("x"), county or None
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None


def county_for(street, city, state, zip_code):
    """Upper-case county name without the word "County" (the house style in
    REDCap, e.g. "HARRIS"), or None if the address can't be matched."""
    found = geocode(street, city, state, zip_code)
    return found[2] if found else None


def miles_between(a, b):
    """Straight-line distance in miles between two geocode() results."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))
