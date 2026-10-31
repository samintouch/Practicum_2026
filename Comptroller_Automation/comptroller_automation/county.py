"""County and map-position lookup. Websites almost never state their county,
so it comes from the US Census geocoder (free, no API key) for a given street
address, or from OpenStreetMap's Nominatim when the Census service can't
answer (it has outages). The same lookup gives the address's coordinates,
used to find the office nearest to the address on file."""

import functools
import math
import re
import threading
import time

import requests

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy: identify the application, at most one request a second.
NOMINATIM_HEADERS = {"User-Agent": "Comptroller_Automation/1.0 (REDCap facility-record verification)"}
# The geocoder often finds nothing when a suite is included; a suite doesn't
# change where the building is, so it's left out of the lookup.
_SUITE = re.compile(r"[\s,]*(?:\b(?:suite|ste|unit|bldg|building|floor|fl|room|rm)\b\.?|#)\s*#?\s*[\w-]+.*$", re.I)

_nominatim_lock = threading.Lock()
_nominatim_last = [0.0]


# When the Census service is down each call takes ~10 s to fail, which added
# minutes to a run. After two failures in a row it's skipped for a while and
# Nominatim answers straight away; then it's tried again in case it's back.
CENSUS_PAUSE_SECONDS = 600
_census_state = {"failures": 0, "paused_until": 0.0}
_census_lock = threading.Lock()


def _census_available():
    with _census_lock:
        return time.time() >= _census_state["paused_until"]


def _census_result(ok):
    with _census_lock:
        if ok:
            _census_state["failures"] = 0
            return
        _census_state["failures"] += 1
        if _census_state["failures"] >= 2:
            _census_state["paused_until"] = time.time() + CENSUS_PAUSE_SECONDS
            _census_state["failures"] = 0


def _census(street, city, state, zip_code):
    if not _census_available():
        return None
    try:
        resp = requests.get(CENSUS_URL, timeout=15, params={
            "address": f"{street}, {city}, {state} {zip_code}",
            "benchmark": "Public_AR_Current", "vintage": "Current_Current", "format": "json",
        })
        if resp.status_code >= 500:
            _census_result(False)
            return None
        resp.raise_for_status()
        _census_result(True)
        matches = resp.json().get("result", {}).get("addressMatches", [])
        if not matches:
            return None
        coords = matches[0].get("coordinates", {})
        counties = matches[0].get("geographies", {}).get("Counties", [])
        county = (counties[0].get("BASENAME") or "").strip().upper() if counties else ""
        return coords.get("y"), coords.get("x"), county or None
    except (requests.ConnectionError, requests.Timeout):
        _census_result(False)           # the service itself is unreachable
        return None
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None


def _nominatim(street, city, state, zip_code):
    with _nominatim_lock:
        wait = 1.1 - (time.time() - _nominatim_last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            resp = requests.get(NOMINATIM_URL, headers=NOMINATIM_HEADERS, timeout=15, params={
                "street": street, "city": city, "state": state, "postalcode": zip_code,
                "country": "us", "format": "jsonv2", "addressdetails": 1, "limit": 1,
            })
            resp.raise_for_status()
            results = resp.json()
        except (requests.RequestException, ValueError):
            return None
        finally:
            _nominatim_last[0] = time.time()
    if not results:
        return None
    county = (results[0].get("address", {}).get("county") or "").upper().removesuffix(" COUNTY").strip()
    return float(results[0]["lat"]), float(results[0]["lon"]), county or None


@functools.lru_cache(maxsize=4096)
def geocode(street, city, state, zip_code):
    """(latitude, longitude, county) for an address, or None if neither the
    Census geocoder nor Nominatim can match it. County is upper case without
    "County"."""
    street = _SUITE.sub("", street or "")
    return _census(street, city, state, zip_code) or _nominatim(street, city, state, zip_code)


@functools.lru_cache(maxsize=4096)
def locate(street, city, state, zip_code):
    """Approximate (latitude, longitude) for distance comparisons: the exact
    address when it can be found, otherwise the middle of its ZIP code. Not
    used for the county, which needs the exact address."""
    found = geocode(street, city, state, zip_code)
    if found:
        return found[:2]
    centre = _nominatim("", city, state, zip_code)
    return centre[:2] if centre else None


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
