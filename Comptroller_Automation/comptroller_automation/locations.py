"""Other locations of the same organization, and which become new records.

A location found on the site becomes a new REDCap record only if:
  - the site is the organization's own (not a doctor/hospital directory),
  - its name belongs to the same organization,
  - it has a name, street number, city, state and ZIP and isn't marked closed,
  - it isn't already in REDCap (same street number and ZIP).
Everything else goes to the "Other Locations" sheet with the reason.
"""

import html
import re
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from .extract import Location, extract_page, page_heading
from .normalize import house_number, host, name_similarity, same_location, street_key, zip5
from .scraper import FetchError, fetch_page

MAX_LOCATION_PAGES = 60

# Third-party listing sites: their "locations" are other providers.
DIRECTORY_DOMAINS = {
    "zocdoc.com", "healthgrades.com", "psychologytoday.com", "vitals.com", "webmd.com", "wellmedhealthcare.com",
    "npino.com", "npiprofile.com", "npidb.org", "yelp.com", "sharecare.com", "doximity.com", "findatopdoc.com",
    "ratemds.com", "caredash.com", "md.com", "usnews.com", "castleconnolly.com", "mapquest.com",
    "yellowpages.com", "google.com", "facebook.com", "findtreatment.gov", "samhsa.gov", "rehab.com",
    "rehabs.com", "addictioncenter.com", "recovery.org", "goodtherapy.org", "therapyden.com", "headway.co",
    "helloalma.com", "zencare.co", "sondermind.com", "growtherapy.com", "medicare.gov", "opencare.com",
    "solvhealth.com", "sesamecare.com", "doctor.com", "wellness.com", "healthline.com", "linkedin.com",
    "bbb.org", "manta.com", "chamberofcommerce.com", "nextdoor.com", "hipaaspace.com", "carelistings.com",
}

_INDEX_HINT = re.compile(r"locations?|our-offices|offices|find-a-location|find-us|clinics|our-clinics|"
                         r"facilities|branches|where-we-are", re.I)
_CLOSED = re.compile(r"\bpermanently closed\b|\bclosed\b|\b(coming|opening) soon\b", re.I)
_ADMIN = re.compile(r"\b(administrative|administration|corporate|headquarters|billing|mailing)\b", re.I)
_GENERIC_WORDS = {
    "health", "healthcare", "medical", "medicine", "center", "centers", "clinic", "clinics", "care", "services",
    "family", "group", "behavioral", "mental", "counseling", "recovery", "treatment", "wellness", "associates",
    "pllc", "llc", "inc", "practice", "physicians", "hospital", "the", "and", "of", "texas", "north", "south",
    "east", "west", "primary", "specialty", "community", "pain", "psychiatry", "psychiatric", "therapy",
    "institute", "partners", "location", "office",
}


def is_directory_domain(url):
    h = host(url)
    return any(h == d or h.endswith("." + d) for d in DIRECTORY_DOMAINS)


def brand_words(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 3 and w not in _GENERIC_WORDS}


def same_organization(location_name, org_name, site):
    """Does a location's name belong to this organization? Unnamed
    locations count as yes (they get the parent's name)."""
    if not location_name:
        return True
    brand = brand_words(org_name)
    words = brand_words(location_name)
    site_flat = re.sub(r"[^a-z0-9]", "", site.split(".")[0])
    if words & brand or any(w in site_flat for w in words if len(w) >= 4):
        return True
    return name_similarity(location_name, org_name) >= 0.6


# ---------------------------------------------------------------- discovery

def _links(page, pattern=None, same_prefix=None):
    soup = BeautifulSoup(page.html, "html.parser")
    site, out = host(page.url), []
    for a in soup.find_all("a", href=True):
        url = urldefrag(urljoin(page.url, a["href"]))[0].rstrip("/")
        if urlparse(url).scheme not in ("http", "https") or host(url) != site:
            continue
        if re.search(r"\.(pdf|jpe?g|png|gif|docx?|zip)$", url, re.I):
            continue
        path = urlparse(url).path
        text = a.get_text(" ", strip=True)
        if pattern and not (pattern.search(path) or pattern.search(text)):
            continue
        if same_prefix and not (path.startswith(same_prefix + "/") and len(path) > len(same_prefix) + 1):
            continue
        if url not in out:
            out.append(url)
    return out


def discover_location_pages(pages, browser=None):
    """Find a "locations" index page and the location pages it links to.
    Returns [Page]: the index pages first, then the detail pages."""
    index_urls = []
    for page in pages:
        for url in _links(page, _INDEX_HINT):
            if url not in index_urls and len(urlparse(url).path.strip("/").split("/")) <= 2:
                index_urls.append(url)
    fetched, seen = [], {p.url.rstrip("/") for p in pages}
    for url in index_urls[:2]:
        page = next((p for p in pages if p.url.rstrip("/") == url), None)
        if page is None:
            try:
                page = fetch_page(url, browser)
            except FetchError:
                continue
        if page.url.rstrip("/") not in seen:
            fetched.append(page)
            seen.add(page.url.rstrip("/"))
        prefix = urlparse(page.url).path.rstrip("/")
        for detail in _links(page, same_prefix=prefix):
            if len(fetched) >= MAX_LOCATION_PAGES:
                break
            if detail in seen:
                continue
            seen.add(detail)
            try:
                fetched.append(fetch_page(detail, browser))
            except FetchError:
                continue
    return fetched


def locations_from_pages(location_pages):
    """Locations from the index and detail pages. A detail page with one
    address is that location; its heading is the name if none was found."""
    found = []
    for page in location_pages:
        parts = extract_page(page)
        locs = distinct_locations(parts["locations"])
        if len(locs) == 1 and not locs[0].name:
            locs[0].name = page_heading(page.html)
        # The location this page is about: the only one on it, or the one
        # whose name matches the page's address (.../diamond-hill-health-center
        # for "Diamond Hill Health Center"; a footer address doesn't match).
        slug = urlparse(page.url).path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")
        for loc in locs:
            if len(locs) == 1 or (loc.name and name_similarity(slug, loc.name) >= 0.8):
                loc.own_page = True
        found.extend(locs)
    return found


def _location_key(loc):
    if house_number(loc.street) and zip5(loc.zip):
        return (house_number(loc.street), zip5(loc.zip))
    return (house_number(loc.street), zip5(loc.zip), " ".join(street_key(loc.street).split()[:3]))


def distinct_locations(locations):
    """One per address. schema.org and AI versions win over plain text,
    and missing phone/fax/email/name are filled from the duplicates. The
    location's own page wins as its url over a list page that mentions it."""
    order = {"schema.org": 0, "AI": 1, "text": 2}
    merged = {}
    for loc in sorted(locations, key=lambda l: order.get(l.source, 3)):
        key = _location_key(loc)
        if key not in merged:
            merged[key] = Location(**vars(loc))
            continue
        keep = merged[key]
        for attr in ("name", "phone", "fax", "email"):
            if not getattr(keep, attr) and getattr(loc, attr):
                setattr(keep, attr, getattr(loc, attr))
        if loc.own_page and not keep.own_page:
            keep.url, keep.own_page = loc.url, True
            if loc.name:            # the location's own page names it best
                keep.name = loc.name
    return list(merged.values())




# ---------------------------------------------------------------- new-record candidates

def clean_name(name):
    name = html.unescape(name or "")      # "&#8211;" -> "–"
    name = re.sub(r"[‐-―−]", "-", name or "")
    name = name.split("|", 1)[0]
    name = re.sub(r"[®™©]|\(\s*(r|tm)\s*\)", "", name, flags=re.I)
    name = re.sub(r"\s*[,\-]\s*[A-Za-z .'\-]+,\s*[A-Z]{2}\s*$", "", name)
    return re.sub(r"\s+", " ", name).strip(" |-,:")


def _is_city_label(name, city):
    """"Mckinney Location:", "Frisco Office" and the like."""
    rest = re.sub(r"\b(location|office|campus|clinic|branch)s?\b|[:\-]", " ", name, flags=re.I).strip()
    return not rest or rest.lower() == (city or "").lower()


def name_new_locations(locations, parent_name):
    """Give every candidate a usable name: its own, or "<parent> - <City>".
    Repeated names get " - <City>" added so each record is distinct."""
    cleaned = ["" if re.search(r"@|https?:|www\.|\.(com|org|net)\b", l.name or "", re.I) else clean_name(l.name)
               for l in locations]
    counts = {}
    for n in cleaned:
        if n:
            counts[n.lower()] = counts.get(n.lower(), 0) + 1
    cleaned = ["" if _is_city_label(n, l.city) else n for n, l in zip(cleaned, locations)]
    for loc, name in zip(locations, cleaned):
        if not name:
            name = f"{parent_name} - {loc.city.title()}" if loc.city else ""
        elif counts.get(name.lower(), 0) > 1 and loc.city:
            name = f"{name} - {loc.city.title()}"
        loc.name = name
    return locations


# Marks of a separate provider's name: a doctor's title or credentials, or a
# business form. A health system's own sites are named by what they do
# ("Dermatology Clinic", "Diamond Hill Health Center") and carry none.
_PROVIDER_MARK = re.compile(r"\b(dr|md|do|np|pa|dds|dmd|od|lpc|lcsw|phd|llc|pllc|inc|pc|lp|llp)\b\.?", re.I)


def looks_like_directory(locations, org_name, site):
    """A site whose listed locations are mostly other providers (doctors,
    separately named practices) is a directory, even if its domain isn't on
    the known list."""
    named = [l for l in locations if l.name]
    others = [l for l in named if _PROVIDER_MARK.search(l.name.replace(".", " "))
              and not same_organization(l.name, org_name, site)]
    return len(named) >= 3 and len(others) > len(named) / 2


def ineligible_reason(loc):
    if _CLOSED.search(loc.name or ""):
        return "location appears closed"
    if _ADMIN.search(loc.name or ""):
        return "administrative or billing office, not a service location"
    missing = [label for label, value in (("name", loc.name), ("street number", house_number(loc.street)),
                                          ("city", loc.city), ("state", loc.state), ("ZIP", zip5(loc.zip)))
               if not value]
    return f"missing {', '.join(missing)}" if missing else ""


class DuplicateIndex:
    """Existing records by ZIP, from the export, plus everything created in
    this run. With a REDCap client, the live project is searched too."""

    def __init__(self, records, client=None):
        self.client = client
        self.by_zip = {}
        for rid, rec in records.items():
            self.add(rid, rec["name"], rec["street"], rec["zip"])

    def add(self, rid, name, street, zip_code):
        self.by_zip.setdefault(zip5(zip_code), []).append((rid, name, street))

    def find(self, loc):
        z = zip5(loc.zip)
        candidates = list(self.by_zip.get(z, []))
        if self.client:
            for row in self.client.records_in_zip(z):
                candidates.append((row.get("record_id"), row.get("ct1", ""), row.get("ct2", "")))
        for rid, name, street in candidates:
            if same_location({"street": street, "zip": z}, {"street": loc.street, "zip": z}):
                return rid, name
        return None
