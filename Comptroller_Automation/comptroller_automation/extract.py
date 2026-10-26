"""Pull contact details out of fetched pages.

Two kinds of output:
  - site-wide lists (every phone, email, social link and name seen), and
  - Locations: an address together with the phone/fax/email/name that go
    with it, from schema.org data or from the text right after an address.
Each value keeps the page it came from, so the report can cite it.
"""

import json
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from .normalize import (STATES, STATE_CODES, host, house_number, is_placeholder_social, phone_digits,
                        social_key, social_platform, state_code, zip5)


@dataclass
class Found:
    value: object
    source: str
    kind: str = ""        # phone / fax / unknown for numbers, platform for socials
    extra: dict = field(default_factory=dict)


@dataclass
class Location:
    street: str
    city: str
    state: str
    zip: str
    name: str = ""
    phone: str = ""       # 10 digits
    fax: str = ""
    email: str = ""
    url: str = ""         # the page this location was found on
    source: str = ""      # schema.org / text / AI
    distance_miles: float = 0.0   # from the address on file, when chosen as the nearest office
    own_page: bool = False        # url is this location's own page, not a list of locations

    def address(self):
        return {"street": self.street, "city": self.city, "state": self.state, "zip": self.zip}

    def one_line(self):
        return f"{self.street}, {self.city}, {self.state} {self.zip}"


@dataclass
class SiteData:
    phones: list = field(default_factory=list)
    emails: list = field(default_factory=list)
    socials: list = field(default_factory=list)
    names: list = field(default_factory=list)
    locations: list = field(default_factory=list)
    text: dict = field(default_factory=dict)      # page url -> visible lines


def parse(html):
    """(soup with scripts removed, JSON-LD nodes, visible text lines)"""
    soup = BeautifulSoup(html, "html.parser")
    blocks = [t.string or "" for t in soup.find_all("script", type="application/ld+json")]
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    lines = [re.sub(r"\s+", " ", line).strip() for line in soup.get_text("\n").splitlines()]
    return soup, list(_jsonld_nodes(blocks)), [line for line in lines if line]


# ---------------------------------------------------------------- JSON-LD

def _jsonld_nodes(blocks):
    def walk(node):
        if isinstance(node, list):
            for item in node:
                yield from walk(item)
        elif isinstance(node, dict):
            yield node
            for key in ("@graph", "location", "department", "subOrganization", "itemListElement", "item"):
                if key in node:
                    yield from walk(node[key])
    for block in blocks:
        try:
            yield from walk(json.loads(block))
        except (ValueError, TypeError):
            continue


def _first(value):
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def _jsonld_locations(nodes, url):
    found = []
    for node in nodes:
        addr = node.get("address")
        if isinstance(addr, list):
            addr = addr[0] if addr else None
        if not isinstance(addr, dict):
            continue
        street = _first(addr.get("streetAddress"))
        loc = Location(street=street, city=_first(addr.get("addressLocality")),
                       state=state_code(_first(addr.get("addressRegion"))),
                       zip=zip5(_first(addr.get("postalCode"))),
                       name=_first(node.get("name")), phone=phone_digits(_first(node.get("telephone"))),
                       fax=phone_digits(_first(node.get("faxNumber"))), email=_first(node.get("email")).lower(),
                       url=urljoin(url, _first(node.get("url"))) if node.get("url") else url, source="schema.org")
        if re.match(r"\d", street) and loc.zip:
            found.append(loc)
    return found


# ---------------------------------------------------------------- phones

# separators include en/em dashes, e.g. "(817) 313 – 6313"
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.\-–—]{0,3}\d{3}\s?[\s.\-–—]\s?\d{4}(?!\d)")


def label_for(context):
    context = context.lower()
    if re.search(r"\bfax\b|\bf:", context):
        return "fax"
    if re.search(r"phone|\btel\b|call|\bp:|\bt:|office|main|contact", context):
        return "phone"
    return "unknown"


def _phones(soup, lines, nodes, url):
    found = []
    for node in nodes:
        for key, kind in (("telephone", "phone"), ("faxNumber", "fax")):
            values = node.get(key)
            for v in values if isinstance(values, list) else [values]:
                digits = phone_digits(str(v or ""))
                if digits:
                    found.append(Found(digits, url, kind))

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.lower().startswith(("tel:", "fax:")):
            continue
        digits = phone_digits(unquote(href[4:]))
        if not digits:
            continue
        parent_text = a.parent.get_text(" ", strip=True) if a.parent else ""
        kind = "fax" if href.lower().startswith("fax:") else label_for(parent_text[:60])
        found.append(Found(digits, url, "phone" if kind == "unknown" else kind))

    for i, line in enumerate(lines):
        for m in PHONE_RE.finditer(line):
            digits = phone_digits(m.group())
            if not digits:
                continue
            before = line[max(0, m.start() - 30):m.start()]
            if not before.strip() and i > 0:
                before = lines[i - 1][-30:]    # label on the line above, e.g. "Fax:\n(555) ..."
            found.append(Found(digits, url, label_for(before)))
    return found


# ---------------------------------------------------------------- emails

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_JUNK_EMAIL = re.compile(r"(example\.|sentry|wixpress|godaddy|domain\.com|yourdomain|email\.com|"
                         r"\.(png|jpe?g|gif|webp|svg)$)", re.I)


def _emails(soup, lines, url):
    raw = []
    for a in soup.find_all("a", href=True):
        if a["href"].lower().startswith("mailto:"):
            raw.append(unquote(a["href"][7:].split("?")[0]))
    for line in lines:
        raw.extend(EMAIL_RE.findall(line))
    return [Found(e.strip().lower(), url) for e in raw
            if EMAIL_RE.fullmatch(e.strip()) and not _JUNK_EMAIL.search(e)]


# ---------------------------------------------------------------- social

def _socials(soup, url):
    found = []
    for a in soup.find_all("a", href=True):
        link = urljoin(url, a["href"].strip())
        platform = social_platform(link)
        key = social_key(link, platform) if platform else None
        if key and not is_placeholder_social(key):
            found.append(Found(link.split("#")[0], url, platform, {"key": key}))
    return found


# ---------------------------------------------------------------- addresses in text

_STATE_ALT = "|".join(re.escape(s) for s in sorted(STATES, key=len, reverse=True))
_CODE_ALT = "|".join(sorted(STATE_CODES))
ADDRESS_RE = re.compile(
    r"(?P<street>\b\d{1,6}[A-Za-z]?\s+(?:[NSEW]\.?\s+)?[A-Za-z0-9][^,|]{1,60}?"
    r"(?:,\s*(?i:suite|ste|unit|bldg|building|floor|fl|room|rm|#)\.?\s*[\w-]+)?)"
    r"\s*[,|]\s*(?P<city>[A-Za-z][A-Za-z .'-]{1,30}?)[\s,|]*[\s|]"     # "|" = a line break
    rf"(?P<state>(?i:{_STATE_ALT})|(?:{_CODE_ALT}))\.?[\s,|]*[\s|]"
    r"(?P<zip>\d{5})(?:-\d{4})?\b"
)
# The same, for addresses with no comma between street and city
# ("120 N. Miller Rd. #300 Mansfield, TX 76063"): the street has to end in a
# street type so we know where the city starts.
_STREET_TYPE = (r"(?i:st|street|rd|road|ave|avenue|blvd|boulevard|dr|drive|ln|lane|pkwy|parkway|hwy|highway|"
                r"fwy|freeway|expy|expressway|ct|court|cir|circle|pl|place|way|trl|trail|loop|plaza|sq|ter)\.?")
_UNIT = r"(?:\s*,?\s*(?:#\s*[\w-]+|(?i:suite|ste|unit|bldg|building|floor|fl|room|rm)\.?\s*#?\s*[\w-]+))?"
ADDRESS_NO_COMMA_RE = re.compile(
    rf"(?P<street>\b\d{{1,6}}[A-Za-z]?(?:\s+[A-Za-z0-9.']+){{0,5}}?\s+{_STREET_TYPE}{_UNIT})"
    r"\s+(?P<city>[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,2})\s*,\s*"
    rf"(?P<state>(?i:{_STATE_ALT})|(?:{_CODE_ALT}))\.?\s*,?\s+"
    r"(?P<zip>\d{5})(?:-\d{4})?\b"
)
_NOT_OPEN_YET = re.compile(r"\bopening\b|\bcoming soon\b|\bopens\b", re.I)


def _real_street(street):
    """A street has letters and doesn't read like a sentence: "7 at 175 W.
    Magnolia Ave" (from "Opening December 7 at ...") isn't one."""
    return bool(re.search(r"[A-Za-z]{2}", street)) and \
        not re.match(r"\d+[A-Za-z]?\s+(at|on|of|in|to|and|or|for|from|until|through|am|pm)\b", street, re.I)


_NOT_A_NAME = re.compile(r"^(address|location|locations|visit|visit us|contact|contact us|directions|"
                         r"get directions|office|our office|hours|find us|map|mailing address|phone|fax)\W*$", re.I)


def _text_locations(lines, url):
    """Each address in the text, plus the phone/fax/email written right
    after it (before the next address starts) and a name just before it."""
    flat = " | ".join(lines)
    matches = [m for m in ADDRESS_RE.finditer(flat) if _real_street(m.group("street"))]
    for m in ADDRESS_NO_COMMA_RE.finditer(flat):
        if _real_street(m.group("street")) and not any(m.start() < o.end() and o.start() < m.end() for o in matches):
            matches.append(m)
    matches.sort(key=lambda m: m.start())
    found = []
    for i, m in enumerate(matches):
        if _NOT_OPEN_YET.search(flat[max(0, m.start() - 80):m.start()]):
            continue                    # "Opening December 7 at 175 W. Magnolia Ave ..."
        tail_end = matches[i + 1].start() if i + 1 < len(matches) else len(flat)
        tail = flat[m.end():min(tail_end, m.end() + 100)]
        loc = Location(street=re.sub(r"\s*\|\s*", ", ", m.group("street")).strip(" ,"),
                       city=m.group("city").strip(), state=state_code(m.group("state")),
                       zip=m.group("zip"), url=url, source="text")
        for pm in PHONE_RE.finditer(tail):
            kind = label_for(tail[max(0, pm.start() - 30):pm.start()])
            digits = phone_digits(pm.group())
            if kind == "fax" and not loc.fax:
                loc.fax = digits
            elif kind != "fax" and not loc.phone:
                loc.phone = digits
        email = EMAIL_RE.search(tail)
        loc.email = email.group().lower() if email else ""
        before = [c.strip() for c in flat[max(0, m.start() - 160):m.start()].split("|") if c.strip()]
        if before:
            candidate = before[-1]
            # a name wrapped onto two lines: "Northwest Health Center for" / "Women"
            if len(before) > 1 and re.search(r"\b(for|of|and|at|the|in|on)$|[&\-–]$", before[-2], re.I):
                candidate = f"{before[-2]} {candidate}"
            if (len(candidate.split()) <= 8 and not re.search(r"\d", candidate)
                    and not candidate.endswith((".", "!", "?", ":")) and not _NOT_A_NAME.match(candidate)
                    and candidate.upper() not in STATES and candidate.upper() not in STATE_CODES
                    and not re.fullmatch(r"[a-z_]+", candidate)):     # icon text like "location_on"
                loc.name = candidate
        found.append(loc)
    return found


# ---------------------------------------------------------------- names

_ORG_TYPES = re.compile(r"Organization|LocalBusiness|MedicalBusiness|MedicalClinic|Physician|"
                        r"Hospital|MedicalOrganization|NGO|Dentist|HealthAndBeautyBusiness", re.I)


def split_title(title):
    return [p.strip() for p in re.split(r"\s[|\-–—:]\s", title or "")
            if p.strip() and p.strip().lower() not in ("home", "homepage", "welcome")]


def _names(raw_soup, nodes, url):
    found = []
    og = raw_soup.find("meta", property="og:site_name")
    if og and og.get("content"):
        found.append(Found(og["content"].strip(), url, "og:site_name"))
    for node in nodes:
        types = node.get("@type")
        types = " ".join(types) if isinstance(types, list) else str(types or "")
        if _ORG_TYPES.search(types) and isinstance(node.get("name"), str):
            found.append(Found(node["name"].strip(), url, "schema.org"))
    if raw_soup.title and raw_soup.title.string:
        found += [Found(piece, url, "title") for piece in split_title(raw_soup.title.string)]
    return found


def page_heading(html):
    """The page's main heading, used as a location's name on its own page."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return re.sub(r"\s+", " ", h1.get_text(" ", strip=True))
    pieces = split_title(soup.title.string if soup.title else "")
    return pieces[0] if pieces else ""


def _link_own_pages(locations, soup, page_url):
    """A location whose name is a link to another page on the same site
    (a location card linking to that location's page) gets that page as
    its url, rather than the list page it was found on."""
    links = {}
    for a in soup.find_all("a", href=True):
        url = urljoin(page_url, a["href"]).split("#")[0]
        text = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).lower()
        # not the home page: a logo link carries the organization's name
        if (text and host(url) == host(page_url) and url.rstrip("/") != page_url.rstrip("/")
                and urlparse(url).path.strip("/")):
            links.setdefault(text, url)

    def key(loc):
        return re.sub(r"\s+", " ", loc.name or "").strip().lower()
    addresses = {}
    for loc in locations:
        addresses.setdefault(key(loc), set()).add((house_number(loc.street), loc.zip))
    for loc in locations:
        url = links.get(key(loc))
        # a name shared by several addresses on the page isn't one location's link
        if url and len(addresses[key(loc)]) == 1:
            loc.url, loc.own_page = url, True
    return locations


def extract_page(page):
    raw_soup = BeautifulSoup(page.html, "html.parser")
    soup, nodes, lines = parse(page.html)
    locations = _jsonld_locations(nodes, page.url) + _text_locations(lines, page.url)
    return {
        "phones": _phones(soup, lines, nodes, page.url),
        "emails": _emails(soup, lines, page.url),
        "socials": _socials(raw_soup, page.url),
        "names": _names(raw_soup, nodes, page.url),
        "locations": _link_own_pages(locations, raw_soup, page.url),
        "lines": lines,
    }


def extract(pages):
    data = SiteData()
    for page in pages:
        parts = extract_page(page)
        data.phones += parts["phones"]
        data.emails += parts["emails"]
        data.socials += parts["socials"]
        data.locations += parts["locations"]
        data.text[page.url] = parts["lines"]
        if page is pages[0]:
            data.names += parts["names"]
    return data


def site_host(pages):
    return host(pages[0].url) if pages else ""
