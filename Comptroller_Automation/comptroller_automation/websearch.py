"""Suggest a website for a record that has none.

Searches the web for the facility's name, city and state, skips listing
sites (directories, social media, maps), and opens each remaining result.
A result is only suggested when that site shows the record's phone number
or street address, along with a matching name. The suggestion is never
written to REDCap and never used to change the record; a person confirms it.
"""

import base64
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from .extract import extract
from .locations import is_directory_domain
from .normalize import host, house_number, name_similarity, phone_digits, same_location, social_platform, zip5
from .scraper import HEADERS, FetchError, fetch_site

SECONDS_BETWEEN_SEARCHES = 4
MAX_CANDIDATES = 4

# Results that are listings about the facility, not its own website.
LISTING_SITES = {
    "yelp.com", "mapquest.com", "yellowpages.com", "bbb.org", "chamberofcommerce.com", "manta.com",
    "npiprofile.com", "npino.com", "npidb.org", "hipaaspace.com", "healthgrades.com", "vitals.com", "webmd.com",
    "zocdoc.com", "psychologytoday.com", "sharecare.com", "doximity.com", "linkedin.com", "indeed.com",
    "glassdoor.com", "wikipedia.org", "google.com", "bing.com", "apple.com", "opencorporates.com",
    "bizapedia.com", "dnb.com", "buzzfile.com", "medicare.gov", "cms.gov", "findtreatment.gov",
    "samhsa.gov", "usnews.com", "careclinic.io", "ratemds.com", "wellness.com", "doctor.com",
    "health.usnews.com", "nextdoor.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "allbiz.com", "cylex.us.com", "birdeye.com", "loc8nearme.com",
    "mapsus.net", "hotfrog.com", "superpages.com", "local.com", "citysearch.com", "addictioncenter.com",
    "rehab.com", "rehabs.com", "recovery.org", "treatment-centers.net", "goodtherapy.org", "therapyden.com",
}


@dataclass
class Suggestion:
    url: str
    evidence: str          # what on the site matched the record
    confidence: str        # high (phone and address) / medium (one of them, plus the name)


_search_lock = threading.Lock()
_last_search = [0.0]


def _polite_get(method, url, **kwargs):
    """One search at a time, a few seconds apart, so the engines don't block us."""
    with _search_lock:
        wait = SECONDS_BETWEEN_SEARCHES - (time.time() - _last_search[0])
        if wait > 0:
            time.sleep(wait)
        try:
            return requests.request(method, url, headers=HEADERS, timeout=30, **kwargs)
        finally:
            _last_search[0] = time.time()


def _bing_target(href):
    """Bing wraps results as bing.com/ck/a?...&u=a1<base64 of the real URL>."""
    if "bing.com/ck/" not in href:
        return href
    u = (parse_qs(urlparse(href).query).get("u") or [""])[0]
    if u.startswith("a1"):
        try:
            return base64.urlsafe_b64decode(u[2:] + "=" * (-len(u[2:]) % 4)).decode("utf-8", "replace")
        except ValueError:
            return ""
    return ""


def search(query):
    """Result URLs for a query: Bing first, DuckDuckGo if Bing gives nothing."""
    urls = []
    try:
        resp = _polite_get("GET", "https://www.bing.com/search", params={"q": query})
        soup = BeautifulSoup(resp.text, "html.parser")
        urls = [_bing_target(a.get("href", "")) for a in soup.select("li.b_algo h2 a")]
    except requests.RequestException:
        pass
    if not any(urls):
        try:
            resp = _polite_get("POST", "https://html.duckduckgo.com/html/", data={"q": query})
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.select("a.result__a"):
                href = a.get("href", "")
                uddg = (parse_qs(urlparse(href).query).get("uddg") or [href])[0]
                urls.append(uddg)
        except requests.RequestException:
            pass
    return [u for u in urls if u.startswith("http")]


def _is_listing(url):
    h = host(url)
    return (is_directory_domain(url) or social_platform(url) is not None
            or any(h == d or h.endswith("." + d) for d in LISTING_SITES))


def _candidates(record):
    """The facility's possible own websites, one per domain, best first.
    Each is the result page itself (a chain's page for this branch, say)."""
    queries = [f'"{record["name"]}" {record["city"]} {record["state"]}',
               f'{record["name"]} {record["city"]} {record["state"]}']
    phone = phone_digits(record["phone"])
    if phone:
        queries.append(f'"{phone[:3]}-{phone[3:6]}-{phone[6:]}" {record["name"]}')
    seen, out = set(), []
    for query in queries:
        for url in search(query):
            h = host(url)
            if not h or h in seen or _is_listing(url):
                continue
            seen.add(h)
            out.append(url)
        if len(out) >= MAX_CANDIDATES:
            break
    return out[:MAX_CANDIDATES]


def _evidence(record, pages):
    """What on the candidate site matches the record."""
    data = extract(pages)
    text = " ".join(" ".join(lines) for lines in data.text.values())
    on_phone = phone_digits(record["phone"])
    phone_hit = bool(on_phone) and (on_phone in {f.value for f in data.phones}
                                    or on_phone in re.sub(r"\D", "", text))
    on_addr = {"street": record["street"], "zip": zip5(record["zip"])}
    number = house_number(record["street"])
    street_hit = bool(record["street"]) and (
        any(same_location(on_addr, l.address()) for l in data.locations)
        or bool(number and zip5(record["zip"]) and re.search(rf"\b{number}\b", text) and zip5(record["zip"]) in text))
    names = [f.value for f in data.names] + [text]
    name_hit = any(name_similarity(record["name"], n) >= 0.8 for n in names[:-1]) or \
        record["name"].lower() in text.lower()
    return phone_hit, street_hit, name_hit


def suggest_website(record, browser=None):
    """A Suggestion, or None when no result could be confirmed."""
    for result in _candidates(record):
        # the site's home page (with its contact pages) first, then the result
        # page itself, which for a chain is often this branch's own page
        home = f"{urlparse(result).scheme}://{urlparse(result).netloc}/"
        for url in dict.fromkeys([home, result]):
            try:
                pages = fetch_site(url, max_pages=3, browser=browser)
            except FetchError:
                continue
            phone_hit, street_hit, name_hit = _evidence(record, pages)
            matched = [label for label, hit in (("phone", phone_hit), ("street address", street_hit),
                                                ("name", name_hit)) if hit]
            if phone_hit and street_hit:
                return Suggestion(pages[0].url, " and ".join(matched) + " match", "high")
            if (phone_hit or street_hit) and name_hit:
                return Suggestion(pages[0].url, " and ".join(matched) + " match", "medium")
    return None
