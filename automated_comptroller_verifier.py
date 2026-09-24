"""
Field-verification helper (NOT part of the dedup pipeline): for one REDCap
record, fetch the organization's own website and check whether the phone
number, mailing address, and social-media links on the site agree with what
is currently recorded in the spreadsheet.

Scope, deliberately narrow (same philosophy as website_verify.py): this does
NOT try to be a fully automatic "corrector". Address parsing off an
arbitrary web page is unreliable, so every finding is shown together with
the raw page context it came from -- a human decides whether to apply an
update. What it DOES do reliably:
  - pulls phone/fax numbers from <a href="tel:..."> links (much more
    reliable than regex-scanning rendered text, which picks up CSS/JS
    noise), and labels each as phone vs. fax using the icon class or the
    word "Phone"/"Fax" found near that link -- falls back to an
    undifferentiated list if no such signal is present
  - pulls emails from <a href="mailto:...">
  - pulls real social-profile links (facebook/instagram/twitter/x/linkedin),
    filtering out share-widget/intent links that aren't the org's own profile
  - pulls the text window around the record's own zip code / city name as
    address context, and best-effort splits it into street/city/state/zip
  - pulls the <title> tag and og:site_name meta tag as signals of the org's
    CURRENT name, and flags a likely name change if neither is a close
    match to the record's name on file (never auto-applied -- a rebrand in
    marketing copy isn't necessarily a legal/DBA name change)
  - fetches both the home page and any on-site "contact" page, since many
    small-practice sites only list phone/address on a dedicated contact page
  - if the org's site has a "locations" index (common for larger providers,
    e.g. a hospital system or a multi-clinic nonprofit), crawls each
    individual location page it links to and pulls out a name/address/
    phone/fax for each -- preferring an embedded schema.org LocalBusiness
    JSON-LD block when the page has one (exact structured data, no regex
    guessing) and falling back to the same text-based extraction used
    elsewhere. These, and any other ambiguous multi-address findings for
    this record, go into the "Other Locations" tab of the run's output
    workbook (default output/record_summary_redcap_id_<lo>_to_<hi>_TIME_
    <timestamp>.xlsx, named after the id range this run covers and when
    it ran, each row tagged by record_id) -- for a human to review, never
    auto-added to REDCap.

REDCap integration: every unambiguous, single-candidate phone/fax/email/
social/address finding is also collected as a structured field change.
By default (no --apply) these are only ever PRINTED as a dry-run preview
-- nothing is written anywhere. Pass --apply to actually write them to
REDCap via its API (needs redcap_config.py: `config = dict(api_url=...,
api_token=...)`, which is git-ignored -- never commit real credentials).
An applied change re-checks the CURRENT live REDCap value immediately
before writing (not the possibly-stale xlsx snapshot the comparison was
computed from) and skips that one field if it's already changed since,
and every applied record gets `change` set to Yes with a dated note
appended to `change_explain` summarizing exactly what was changed.

Usage (--xlsx is required -- no default spreadsheet is assumed):
    python automated_comptroller_verifier.py 400 --xlsx ComptrollerProject20_DATA_LABELS_2026-04-16_1051.xlsx
    python automated_comptroller_verifier.py 4 --xlsx <file> --skip-new-locations   # skip the (default-on) locations crawl
    python automated_comptroller_verifier.py 3-10 --xlsx <file>            # a range, inclusive both ends
    python automated_comptroller_verifier.py 3 5 8 12-15 --xlsx <file>      # a mix of single ids and ranges
    python automated_comptroller_verifier.py 400 --xlsx <file> --apply      # actually write changes to REDCap

Each record ID (in a range or not) gets its own output/<record_id>.txt
(only under --debug); the whole run also gets one combined output/
record_summary_....xlsx workbook with "Record Summary" and "Other
Locations" tabs.

If a record has no website on file, this just reports that and stops --
it does not search for or guess at one.
"""

import argparse
import contextlib
import datetime
import difflib
import html
import io
import json
import os
import re
import sys
import time
from collections import Counter
from urllib.parse import urljoin, urlparse
import openpyxl
from openpyxl.styles import Alignment
import pandas as pd
import requests

# Street-suffix/directional abbreviation table + normalizer, inlined from
# dedupe_stage1.py (previously imported as `normalize_address`) so this
# script has no other local .py file it depends on to run -- the dedup
# pipeline's own copy in dedupe_stage1.py is untouched and still used
# there; this is a separate, intentionally duplicated copy, not a shared
# import, so a future change to one doesn't silently affect the other.
_ADDRESS_STREET_SUFFIXES = {
    "STREET": "ST", "AVENUE": "AVE", "BOULEVARD": "BLVD", "DRIVE": "DR",
    "ROAD": "RD", "LANE": "LN", "COURT": "CT", "PARKWAY": "PKWY",
    "EXPRESSWAY": "EXPY", "FREEWAY": "FWY", "HIGHWAY": "HWY",
    "PLACE": "PL", "CIRCLE": "CIR", "SUITE": "STE", "BUILDING": "BLDG",
    "FLOOR": "FL", "CENTER": "CTR",
}
_ADDRESS_DIRECTIONALS = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
}


def _normalize_address_suffixes(street):
    """Offline rule-based street-suffix/directional normalization (e.g.
    "Circle" -> "CIR", "North" -> "N"). Same logic as dedupe_stage1.py's
    normalize_address()."""
    if pd.isna(street) or not str(street).strip():
        return ""
    s = str(street).upper()
    s = s.replace(".", "").replace(",", "")
    s = re.sub(r"\s+", " ", s).strip()
    tokens = [_ADDRESS_STREET_SUFFIXES.get(t, t) for t in s.split(" ")]
    tokens = [_ADDRESS_DIRECTIONALS.get(t, t) for t in tokens]
    return " ".join(tokens)


def street_number(street):
    """Leading house number off a street string ("3913 Broaddus Ave" ->
    "3913"), or None. Used to tell whether a candidate location is really
    this record's own address (same house number) rather than a distinct
    branch. Was previously imported from npi_verify.py -- inlined here
    once this file's own NPI cross-check was removed, so npi_verify.py is
    no longer a required companion file for this script."""
    m = re.match(r"\s*(\d+)", str(street))
    return m.group(1) if m else None

# Website text can contain characters (curly quotes, en-dashes, etc.)
# that Windows' default console codepage (cp1252) can't encode, which
# crashes plain `print()` mid-report. Force UTF-8 on stdout/stderr instead
# of relying on the caller to set PYTHONIOENCODING=utf-8 first.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

FETCH_TIMEOUT_SEC = 15
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FieldVerifyBot/1.0)"}

SOCIAL_DOMAINS = {
    "facebook": r"(?<![A-Za-z0-9-])facebook\.com/[A-Za-z0-9._-]+/?",
    "instagram": r"(?<![A-Za-z0-9-])instagram\.com/[A-Za-z0-9._-]+/?",
    # (?<![A-Za-z0-9-]) matters a lot for "x.com" specifically -- without it,
    # this also matches inside unrelated domains that happen to END in
    # "x.com" (wix.com, netflix.com, box.com, fedex.com, ...). A real case:
    # "wix.com/velo" and "wix.com/bolt-performance" (Wix's own JS/CSS
    # references) were being read as the org's own Twitter/X links
    # "x.com/velo" / "x.com/bolt" because the regex had no anchor and just
    # matched the "x.com" substring inside "wix.com".
    "twitter": r"(?<![A-Za-z0-9-])(?:twitter|x)\.com/[A-Za-z0-9_]+/?",
    "linkedin": r"(?<![A-Za-z0-9-])linkedin\.com/(?:company|in)/[A-Za-z0-9._-]+/?",
}
# share/intent links point at THIS page being shared elsewhere, not the
# org's own profile -- must not be mistaken for "the org has a Facebook page"
SOCIAL_JUNK_PATTERNS = re.compile(
    r"sharer|share\.php|share\?|/share/|intent/|dialog/|widget|plugins/", re.I
)


def _fetch(url):
    """Returns (text, final_url) -- final_url is where the request actually
    ended up after following redirects, which is NOT always `url` (a real
    case: the website on file, https://pathway.org/, redirects to
    https://pathwaystx.org/ -- a different domain). Callers that use the
    fetched HTML to find more same-domain links (find_contact_links,
    find_locations_index_links, ...) must anchor that domain check on
    final_url, not the original url, or every such link silently fails the
    domain match and looks like the site has no contact/locations page at
    all. Both are None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=FETCH_TIMEOUT_SEC)
        resp.raise_for_status()
        return resp.text, resp.url
    except requests.RequestException:
        return None, None
    finally:
        time.sleep(0.3)


_browser = None


def _get_browser():
    global _browser
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=True)
    return _browser


def _fetch_browser(url, attempts=2):
    """Full rendered HTML via headless Chromium -- a plain `requests.get`
    only sees what the server sent, and some sites load their contact info
    (a real one only showed its email after full JS/lazy-load rendering,
    even on the dedicated contact page) via client-side JS or a lazy-load
    plugin. Used only as a fallback (see verify()) since launching a
    browser and waiting for a render is much slower than a plain GET.
    Retries once -- reusing one browser across many page loads occasionally
    hits a transient navigation failure that doesn't reproduce on retry."""
    for attempt in range(attempts):
        try:
            page = _get_browser().new_page()
            try:
                page.goto(url, timeout=20000, wait_until="networkidle")
                page.wait_for_timeout(1000)
                return page.content()
            finally:
                page.close()
        except Exception:
            if attempt + 1 == attempts:
                return None
            time.sleep(1)


def _normalize_url(url):
    if not url or not str(url).strip() or str(url).lower() == "nan":
        return None
    u = str(url).strip()
    return u if u.lower().startswith("http") else "http://" + u


def strip_tags(raw_html):
    """Visible text only (script/style removed), case preserved -- used for
    context windows around phone/address matches. Deliberately simpler than
    website_verify._normalize_page_text, which upper-cases and strips
    punctuation for token matching; here we want human-readable output.

    <br>/block-level closing tags become a real newline rather than just a
    space -- needed so an address written as "123 Main St<br>Dallas, TX
    75001" (very common) keeps a hard boundary between the street and the
    city/state/zip line for the address parser, instead of collapsing into
    one ambiguous "123 Main St Dallas, TX 75001" run where a naive regex
    can't tell where the street name ends and the city begins.

    HTML entities (a real page had "Allen ,&nbsp; TX &nbsp; 75002") are
    decoded AFTER tags are stripped, not before -- unescaping first risks
    turning an escaped "&lt;" into a literal "<" that the tag-stripping
    regex would then misread as real markup. A decoded &nbsp; becomes
    U+00A0, which -- unlike a normal space -- Python's \\s does NOT match
    (NBSP lacks the Unicode "White_Space" property), so it's replaced with
    a plain space explicitly rather than left to trip up every regex
    downstream that assumes \\s covers all whitespace."""
    content = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_html, flags=re.I | re.S)
    content = re.sub(r"<br\s*/?>", "\n", content, flags=re.I)
    content = re.sub(r"</(p|div|li|tr|td|h[1-6]|section|article)\s*>", "\n", content, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", content)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _same_domain(url, base_host):
    return base_host in urlparse(url).netloc.lower()


def find_contact_links(html, base_url):
    hrefs = set(re.findall(r'href=["\']([^"\']+)["\']', html))
    base_host = urlparse(base_url).netloc.lower().lstrip("www.")
    out = []
    for h in hrefs:
        if "contact" not in h.lower():
            continue
        full = urljoin(base_url, h)
        if _same_domain(full, base_host):
            out.append(full)
    return sorted(set(out))


def find_locations_index_links(html, base_url):
    """On-site links that plausibly lead to a "find a location"/"our
    locations" index page (as opposed to a single location detail page --
    that distinction is made later by find_location_detail_links)."""
    hrefs = set(re.findall(r'href=["\']([^"\']+)["\']', html))
    base_host = urlparse(base_url).netloc.lower().lstrip("www.")
    junk = ("wp-json", "wp-admin", "/embed", "/feed", ".xml", ".json", "oembed", "${", "{{")
    out = []
    for h in hrefs:
        lh = h.lower()
        if "location" not in lh or any(j in lh for j in junk):
            continue
        full = urljoin(base_url, h).split("?")[0].split("#")[0]
        if _same_domain(full, base_host):
            out.append(full)
    return sorted(set(out))


# A single FLAT path segment like "/locations-frisco" or "/location-
# sunnyvale" -- some sites (WordPress with plain page slugs, no nested
# rewrite rules) name each location page this way instead of nesting it
# under a shared "/locations/" path. The suffix must be a single word: real
# examples of pages that must NOT match this (sub-pages of a location, not
# a location of their own) are "/locations-frisco-feedback",
# "/locations-frisco-reviews", "/locations-frisco-appointments",
# "/locations-frisco-call" -- all rejected automatically since their
# suffix has an extra hyphenated word the plain [a-z0-9]+ class can't
# consume. "/locations-map" structurally fits the one-word-suffix shape
# but isn't a location either, so it's excluded by name explicitly.
_FLAT_LOCATION_SLUG_RE = re.compile(r"^locations?-([a-z0-9]+)$", re.I)
_FLAT_LOCATION_SLUG_EXCLUDE = {"map", "all", "index", "list", "near", "me", "form", "contact"}


def _flat_location_slug_city(url):
    """The city hint from a flat "/locations-sherman"-style URL, or None
    for any other URL shape (nested paths don't carry this signal)."""
    path = urlparse(url).path.strip("/")
    m = _FLAT_LOCATION_SLUG_RE.match(path.split("/")[-1]) if path else None
    return m.group(1).upper() if m else None


def find_location_detail_links(html, base_url):
    """From a locations INDEX page, the individual location detail pages it
    links to. Two accepted shapes: a NESTED path with a "location"/
    "locations" segment followed by a further slug segment (e.g.
    /location/some-clinic/ -- a bare index link like /locations/ itself has
    nothing after that segment, so it's naturally excluded), or a FLAT
    single segment like /locations-frisco (see _FLAT_LOCATION_SLUG_RE)."""
    hrefs = set(re.findall(r'href=["\']([^"\']+)["\']', html))
    base_host = urlparse(base_url).netloc.lower().lstrip("www.")
    junk = ("wp-json", "wp-admin", "/embed", "/feed", ".xml", ".json", "oembed", "#", "${", "{{")
    out = set()
    for h in hrefs:
        lh = h.lower()
        if any(j in lh for j in junk):
            continue
        full = urljoin(base_url, h).split("?")[0].split("#")[0]
        if not _same_domain(full, base_host):
            continue
        segments = [s for s in urlparse(full).path.split("/") if s]
        if not segments:
            continue
        if len(segments) == 1:
            m = _FLAT_LOCATION_SLUG_RE.match(segments[0])
            if m and m.group(1).lower() not in _FLAT_LOCATION_SLUG_EXCLUDE:
                out.add(full)
            continue
        for seg in segments[:-1]:
            if seg.lower() in ("location", "locations", "our-locations", "clinics", "clinic-locations"):
                if len(segments[-1]) >= 3:
                    out.add(full)
                break
    return sorted(out)


def _format_tel_digits(raw):
    from urllib.parse import unquote
    digits = re.sub(r"\D", "", unquote(raw))
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"


def extract_labeled_numbers_from_text(text):
    """Explicit "Phone: ..."/"Tel: ..."/"Fax: ..." pairs, scanned over
    VISIBLE text only. This is the most reliable phone/fax signal available
    -- it's just what the page actually says next to the number -- and it's
    the only way to catch a fax number that's plain text with no tel: link
    at all (common: a phone/address/fax "info card" is often one single
    clickable block wrapping all three lines under ONE tel: link for the
    phone, with the fax line as plain text alongside it)."""
    labels = {}
    for word, tag in (("PHONE", "phone"), ("TEL", "phone"), ("FAX", "fax")):
        for m in re.finditer(rf"\b{word}\s*:?\s*([(+]?\d[\d() .-]{{6,16}}\d)", text, re.I):
            number = _format_tel_digits(m.group(1))
            if number:
                labels.setdefault(number, tag)
    return labels


def extract_tel_links_labeled(html):
    """Returns {number: 'phone'|'fax'|'unknown'} from <a href="tel:..."> tags
    alone, using an ICON CLASS immediately after the href (e.g. e-fas-fax /
    e-fas-phone-alt) as the only same-tag signal. Deliberately narrow and
    close-range: a wider window (or a plain "fax"/"phone" keyword search)
    picks up a SIBLING line's label when a phone/address/fax block is all
    one clickable <a> (a real case: a "Fax: ..." line 150+ chars after the
    phone's own tel: link, inside the same anchor, mislabeled the phone
    number as fax). Text-label extraction above is the authoritative source
    for that case; this is only a fallback for icon-only buttons with no
    visible "Phone"/"Fax" text at all."""
    labels = {}
    for m in re.finditer(r'href=["\']tel:([^"\']+)["\']', html, flags=re.I):
        number = _format_tel_digits(m.group(1))
        if not number:
            continue
        window = html[m.start():m.start() + 250]
        if re.search(r"fa-fax|e-fas-fax|icon-fax|fax-icon", window, re.I):
            label = "fax"
        elif re.search(r"fa-phone|e-fas-phone|icon-phone|phone-icon", window, re.I):
            label = "phone"
        else:
            label = "unknown"
        if number not in labels or labels[number] == "unknown":
            labels[number] = label
    return labels


def extract_site_names(html):
    """Best-effort signals for what the org currently calls itself: the
    <title> tag and the og:site_name meta tag. Neither is authoritative (a
    <title> often has a tagline appended, og:site_name is sometimes just a
    person's name for a solo practice that rebranded around the provider),
    so both are surfaced for a human to judge, not auto-applied."""
    names = {}
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        names["title"] = html_unescape_ws(m.group(1))
    m = re.search(
        r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)["\']',
        html, re.I,
    )
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:site_name["\']',
            html, re.I,
        )
    if m:
        names["og_site_name"] = html_unescape_ws(m.group(1))
    return names


def html_unescape_ws(s):
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


_ERROR_PAGE_PATTERNS = re.compile(
    r"page not found|"
    r"404[ -]+(error|not found|page)|(error|not found)[ -]+404|"
    r"doesn.t exist|does not exist|"
    r"could not be found|couldn.t be found|oops.{0,20}(not found|page)|"
    r"attention required|access denied|domain (has expired|is not configured)|"
    r"this site can.t be reached|account (has been )?suspended|"
    # server-side failures (5xx) -- a real case: a 504 Gateway Timeout's
    # own error page, titled just "Service unavailable", rendered fine
    # under a headless browser (which doesn't check HTTP status the way
    # requests.raise_for_status does) and was read as if it were the org's
    # real page content
    r"service unavailable|bad gateway|gateway time-?out|internal server error|"
    r"50[0-9][ -]+(error|service unavailable|bad gateway|internal server error)|"
    r"(error|service unavailable|bad gateway|internal server error)[ -]+50[0-9]",
    re.I,
)


def is_error_page(html_text):
    """A fetch can succeed (HTTP 200, or a browser render that doesn't
    surface status at all) while the page itself says the URL is broken --
    a WordPress "Page not found" page, or a Cloudflare bot-challenge
    interstitial ("Attention Required!") returned instead of the real site.
    Treating that content as real data produces nonsense (a real case: it
    was read as the org having renamed itself to "Attention Required! |
    Cloudflare"). Checks both the <title> and the first chunk of visible
    text, since some soft-404 pages only say so in an <h1>, not the title."""
    if not html_text:
        return False
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
    title = html_unescape_ws(title_match.group(1)) if title_match else ""
    body_snippet = strip_tags(html_text)[:500]
    return bool(_ERROR_PAGE_PATTERNS.search(f"{title}\n{body_snippet}"))


def _name_tokens(s):
    return set(re.sub(r"[^A-Z0-9 ]", " ", str(s).upper()).split())


def name_similarity(a, b):
    """How much of the record's name on file is still recognizable in the
    site's name. A raw string-similarity ratio unfairly penalizes a site
    title like "Allen Psychiatry & Mental Health | Psychiatrist in Allen,
    TX" for a record named "Allen Psychiatry" even though the org name is
    fully present -- so score the BETTER of token-containment (are all of
    the record name's words present on the site?) and a straight ratio."""
    tokens_a = _name_tokens(a)
    if not tokens_a:
        return 0.0
    containment = len(tokens_a & _name_tokens(b)) / len(tokens_a)
    norm = lambda s: re.sub(r"[^A-Z0-9 ]", "", str(s).upper())
    ratio = difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()
    return max(containment, ratio)


# RFC 2606 reserved domains plus common template-builder placeholders --
# "info@example.com" turning up on a real business's contact page is
# template demo content that was never replaced, not a real email
_PLACEHOLDER_EMAIL_DOMAINS = {
    "example.com", "example.org", "example.net", "example.edu",
    "yourdomain.com", "yoursite.com", "domain.com", "test.com", "site.com",
}


def extract_mailto_links(html):
    raw = re.findall(r'href=["\']mailto:([^"\'?]+)["\']', html, flags=re.I)
    return sorted(
        set(r.strip() for r in raw
            if "@" in r and r.strip().lower().rsplit("@", 1)[-1] not in _PLACEHOLDER_EMAIL_DOMAINS)
    )


def extract_social_links(html):
    found = {}
    for platform, pattern in SOCIAL_DOMAINS.items():
        matches = re.findall(pattern, html, flags=re.I)
        clean = []
        for m in matches:
            start = html.lower().find(m.lower())
            window = html[max(0, start - 60):start]
            if SOCIAL_JUNK_PATTERNS.search(window) or SOCIAL_JUNK_PATTERNS.search(m):
                continue
            url = m if m.lower().startswith("http") else "https://" + m
            clean.append(url.rstrip("/"))
        if clean:
            found[platform] = sorted(set(clean))
    return found


def extract_phone_candidates(text):
    """Fallback for sites that print a phone number as plain text without a
    tel: link. Runs on visible text only (not raw HTML) to avoid CSS/JS
    number noise; still much less reliable than tel: links."""
    pattern = re.compile(r"\(\d{3}\)\s?\d{3}[\s.-]\d{4}|\b\d{3}[\s.-]\d{3}[\s.-]\d{4}\b")
    return sorted(set(m.strip() for m in pattern.findall(text)))


def normalize_phone_digits(phone):
    if phone is None or (isinstance(phone, float) and pd.isna(phone)):
        return None
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def format_phone_for_redcap(phone):
    """REDCap's phone/fax fields (ct6/ct13) expect exactly "(123)-456-7891"
    -- parens around the area code with NO space before the hyphen, unlike
    the "(123) 456-7891" (space, no hyphen after the parens) this script's
    own extraction produces for display, and unlike whatever inconsistent
    formatting a plain-text (no tel: link) phone-candidate match on the
    site happens to use. Only ever applied to the NEW value being written
    -- never to "old", which must stay byte-for-byte whatever REDCap
    currently has, since apply_field_changes_to_redcap() compares it
    against the live value to detect a stale/already-changed field.
    Returns the original string unchanged if it doesn't resolve to a plain
    10-digit US number (so an unusual value is still written rather than
    silently dropped, just not reformatted)."""
    digits = normalize_phone_digits(phone)
    if not digits:
        return phone
    return f"({digits[0:3]})-{digits[3:6]}-{digits[6:10]}"


US_STATE_ABBREVIATIONS = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI",
    "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY", "PUERTO RICO": "PR",
}
# longest name first, so "NEW YORK" matches before a hypothetical shorter
# prefix would; the 2-letter code alternative is tried after full names
_STATE_NAME_PATTERN = "|".join(re.escape(n) for n in sorted(US_STATE_ABBREVIATIONS, key=len, reverse=True))
_STATE_PATTERN = rf"(?:{_STATE_NAME_PATTERN}|[A-Z]{{2}})"


def _normalize_state(raw):
    s = raw.strip().upper()
    return US_STATE_ABBREVIATIONS.get(s, s)


# Standard USPS ZIP-prefix (first 3 digits) -> state ranges. Used to reject
# an address candidate whose state and zip are impossible together -- a
# real case: a site's unfinished "Contact" section still had its website
# template's placeholder demo content ("108 Adam Street, New York, NY
# 53502"), and 535xx is a Wisconsin range, not New York's (100-149) --
# obviously bogus, but nothing about the TEXT shape flags it as such
# without this check. Deliberately conservative: only reject when the
# zip3 falls in a KNOWN range for some OTHER state; an unrecognized/gap
# zip3 is left alone rather than risk a false rejection.
_ZIP3_STATE_RANGES = [
    (0, 5, "NY"), (6, 9, "PR"), (10, 27, "MA"), (28, 29, "RI"), (30, 38, "NH"),
    (39, 49, "ME"), (50, 59, "VT"), (60, 69, "CT"), (70, 89, "NJ"),
    (100, 149, "NY"), (150, 196, "PA"), (197, 199, "DE"), (200, 205, "DC"),
    (206, 219, "MD"), (220, 246, "VA"), (247, 268, "WV"), (270, 289, "NC"),
    (290, 299, "SC"), (300, 319, "GA"), (320, 349, "FL"), (350, 369, "AL"),
    (370, 385, "TN"), (386, 397, "MS"), (398, 399, "GA"), (400, 427, "KY"),
    (430, 459, "OH"), (460, 479, "IN"), (480, 499, "MI"), (500, 528, "IA"),
    (530, 549, "WI"), (550, 567, "MN"), (570, 577, "SD"), (580, 588, "ND"),
    (590, 599, "MT"), (600, 629, "IL"), (630, 658, "MO"), (660, 679, "KS"),
    (680, 693, "NE"), (700, 714, "LA"), (716, 729, "AR"), (730, 749, "OK"),
    (750, 799, "TX"), (800, 816, "CO"), (820, 831, "WY"), (832, 838, "ID"),
    (840, 847, "UT"), (850, 865, "AZ"), (870, 884, "NM"), (889, 898, "NV"),
    (900, 961, "CA"), (967, 968, "HI"), (970, 979, "OR"), (980, 994, "WA"),
    (995, 999, "AK"),
]


def zip_state_plausible(zip_code, state):
    z = re.sub(r"\D", "", str(zip_code))[:5]
    if len(z) < 3:
        return True
    zip3 = int(z[:3])
    for low, high, expected in _ZIP3_STATE_RANGES:
        if low <= zip3 <= high:
            return not state or expected == str(state).strip().upper()
    return True


def lookup_county_via_census(street, city, state, zip_code):
    """A website practically never states its own county -- there's
    nothing to scrape for that. The US Census Bureau's geocoder is a
    free, no-API-key, authoritative source that takes a full street
    address and returns the county it's actually in. Used only to
    confirm whether a RELOCATION changed the county too -- returns the
    county name (upper-cased, no "County"
    suffix) on a confident match, or None on any failure/no-match/timeout,
    which the caller treats as "couldn't confirm" and falls back to
    flagging the on-file county as unverified for the new address rather
    than guessing."""
    address = f"{street}, {city}, {state} {zip_code}"
    try:
        resp = requests.get(
            "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress",
            params={"address": address, "benchmark": "Public_AR_Current",
                    "vintage": "Current_Current", "format": "json"},
            timeout=10,
        )
        resp.raise_for_status()
        matches = resp.json().get("result", {}).get("addressMatches", [])
        if not matches:
            return None
        counties = matches[0].get("geographies", {}).get("Counties", [])
        name = counties[0].get("BASENAME") if counties else None
        return name.strip().upper() if name else None
    except Exception:
        return None


# Extra street-suffix abbreviations beyond dedupe_stage1's own table
# (STREET->ST, CIRCLE->CIR, etc.) -- kept LOCAL to this comparison rather
# than added to that shared table, since that table also drives the dedup
# pipeline's grouping decisions and any change there needs re-validating
# against its ground truth (see redcap_dedup_project memory); this
# comparison has a much lower bar (just "do these two strings mean the same
# address"), so it's safe to be more liberal here without that process.
_EXTRA_STREET_SUFFIXES = {"CREEK": "CRK", "SUITE": "STE", "MOUNTAIN": "MTN", "SUMMIT": "SMT"}


def _normalize_street_for_compare(street):
    """Same physical address, different notation, is a common false
    "mismatch": "STE 113" vs "#113", "22 Prestige Circle" vs "22 PRESTIGE
    CIR", or "Watter's Creek" vs "WATTERS CRK" (apostrophe + an
    abbreviation dedupe_stage1's table doesn't have). Strip apostrophes
    (normalize_address only strips "." and ","), reuse dedupe_stage1's own
    street-suffix/directional normalizer (CIRCLE->CIR, DRIVE->DR, SUITE-
    >STE, NORTH->N, ...) FIRST -- it maps a literal "SUITE" word to "STE" --
    then apply the extra local abbreviations, and only THEN collapse "#"
    notation, absorbing any "STE" that's already sitting right before it
    ("Suite #213" normalizes to "STE #213" at that point, which would
    otherwise become the wrong, duplicated "STE STE 213" if the "#" step
    ran before the word-normalization steps instead)."""
    s = re.sub(r"['’]", "", str(street).upper())
    s = _normalize_address_suffixes(s)
    tokens = [_EXTRA_STREET_SUFFIXES.get(t, t) for t in s.split(" ")]
    s = " ".join(tokens)
    return re.sub(r"(?:STE\s+)?#\s*(\d)", r"STE \1", s)


def streets_match(a, b):
    na, nb = _normalize_street_for_compare(a), _normalize_street_for_compare(b)
    return bool(na) and bool(nb) and (na in nb or nb in na)


def _social_link_key(url):
    u = str(url).strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.rstrip("/")


def social_links_match(on_file_url, found_urls):
    key = _social_link_key(on_file_url)
    return any(key == _social_link_key(f) or key in _social_link_key(f) or _social_link_key(f) in key
               for f in found_urls)


def _dedupe_subsumed_addresses(addrs):
    """Multiple mentions of the SAME address on one page sometimes parse to
    slightly different fragments (a real case: one mention lost its street
    name and came out as just "250, Dallas, TX 75240" -- the suite number
    alone -- while another mention of the same place parsed cleanly as
    "5728 LBJ Fwy, Suite 250, Dallas, TX 75240"). Drop any entry whose
    street is wholly contained in another entry's (at the same city/state/
    zip), keeping the more complete one, so these don't look like two
    separate candidate addresses."""
    by_length = sorted(addrs, key=lambda a: len(a["street"]), reverse=True)
    kept = []
    for a in by_length:
        subsumed = any(
            a["city"].upper() == k["city"].upper() and a["state"] == k["state"] and a["zip"] == k["zip"]
            and streets_match(a["street"], k["street"])
            for k in kept
        )
        if not subsumed:
            kept.append(a)
    return kept


# Appended to the STREET group's own pattern in both regexes below so a
# suite/unit designator (with its own number) counts as part of the
# street even when nothing but a plain space separates it from what
# follows -- without this, "3001 Cross Timbers Rd, Suite 120 Flower
# Mound, TX 75028" (a real other-location page, record 46: no comma
# between the suite number and the city) parsed as street="120" (just the
# suite number) instead of the real street, because the permissive regex
# below found a fully valid match starting AT "120" and never needed to
# try starting further back at "3001".
_SUITE_CLAUSE = r"(?:[,\s]+(?:STE|SUITE|UNIT|APT|BLDG|BUILDING|#)\.?\s*[0-9A-Za-z-]+)?"
# Same designator, as a standalone compiled pattern for STRIPPING a suite/
# unit clause back out of an already-parsed street (see
# lookup_county_for_address, which needs the geocoding query itself
# suite-free even though the full address is still what's written to
# REDCap).
_SUITE_CLAUSE_RE = re.compile(r"[,\s]+(?:STE|SUITE|UNIT|APT|BLDG|BUILDING|#)\.?\s*[0-9A-Za-z-]+", re.IGNORECASE)

# Catches a street name (ending in a suffix like St/Street/Ave/Rd/Drive/
# etc.) that a permissive match swallowed whole into the "city" group,
# because nothing but a plain space separates it from the real city
# either (e.g. "990 S Sherman Street Richardson, TX 75081" -- city has no
# digits to stop the permissive regex's city group at, so it happily
# absorbs "S Sherman Street Richardson" as one long "city"). Splits that
# back into street="S Sherman Street" (folded into the real street below)
# and city="Richardson" whenever the suffix word isn't the very last word
# of the captured city text.
_EMBEDDED_STREET_SUFFIX_RE = re.compile(
    r"^(.*?\b(?:ST|STREET|AVE|AVENUE|BLVD|BOULEVARD|DR|DRIVE|RD|ROAD|LN|LANE|CT|COURT|PKWY|PARKWAY|"
    r"EXPY|EXPRESSWAY|FWY|FREEWAY|HWY|HIGHWAY|PL|PLACE|CIR|CIRCLE|WAY|TRL|TRAIL|LOOP|SQ|SQUARE)\.?)\s+"
    r"([A-Za-z .'-]+)$",
    re.IGNORECASE,
)


def parse_street_city_state_zip(snippet):
    """Sites don't consistently abbreviate the state -- some spell it out
    ("Dallas, Texas 75216") instead of using the 2-letter code the
    spreadsheet uses ("TX") -- so accept either and normalize to the
    2-letter form for comparison.

    Tries a STRICT pass first: the street/city separator must be a comma or
    an actual newline (from strip_tags turning <br>/block tags into "\\n").
    A plain space there is ambiguous between "still part of the street
    name" and "start of the city".

    Only if that strict pass finds nothing do we fall back to a PERMISSIVE
    pass (comma OR space) -- needed for real pages that run the suite
    number straight into the city with no punctuation at all ("120 N.
    Miller Rd. #300 Mansfield, TX 76063"). Either pass can still leave a
    street name or suite designator stuck inside the "city" group when a
    plain space is all that separates it from the real city -- see
    _SUITE_CLAUSE and _EMBEDDED_STREET_SUFFIX_RE above for the two ways
    that's caught and corrected below."""
    # The street group's excluded characters include "|" specifically so a
    # footer/nav block crammed onto the same line as the real address (no
    # <br> between them in the source HTML -- so no comma/newline to stop
    # at either) can't be swallowed whole: a real case had "(c) 2026 by
    # Healthy Horizons Clinic | Sitemap | Privacy | Healthy Horizons
    # Clinic | 3913 Broaddus Ave, El Paso, TX 79904" reported with the
    # ENTIRE footer text as the street, because the street group started
    # at the first digit in the snippet ("2026", the copyright year) and
    # a real street address never contains a literal "|" -- excluding it
    # forces the match to fail at that leading-junk starting position and
    # backtrack to the next digit ("3913"), which is the real address.
    strict = re.search(
        rf"([0-9][^,\n|]*?{_SUITE_CLAUSE})[,\n]+\s*([A-Za-z .]+?),?\s+({_STATE_PATTERN}),?\s+(\d{{5}})",
        snippet, re.IGNORECASE,
    )
    m = strict or re.search(
        rf"([0-9][^,\n|]*?{_SUITE_CLAUSE})[,\s]+([A-Za-z .]+?),?\s+({_STATE_PATTERN}),?\s+(\d{{5}})",
        snippet, re.IGNORECASE,
    )
    if not m:
        return None
    street, city, state, zip_ = m.groups()
    embedded = _EMBEDDED_STREET_SUFFIX_RE.match(city.strip())
    if embedded:
        street = f"{street} {embedded.group(1)}"
        city = embedded.group(2)
    return {
        "street": street.strip(" ,"),
        "city": city.strip(" ,"),
        "state": _normalize_state(state),
        "zip": zip_.strip(),
    }


def extract_addresses(text):
    """Scan a page's full text (strip_tags output, real newlines intact)
    for every address on it. Line-based, not a single regex, because a real
    page had the suite number on its OWN line, separate from both the
    street and the city:

        22 Prestige Circle
        Suite 200 & 300,
        Allen, TX 75002

    parse_street_city_state_zip alone can't see across that -- it works on
    one contiguous snippet. Here: find every line that looks like "City,
    STATE ZIP", then walk backwards through the 1-2 lines directly above it
    collecting anything that looks like a street-number line or a
    Suite/Unit/Apt/# line, stopping at the first line that's neither.

    Falls back to parse_street_city_state_zip on a narrow window when there
    ARE no such preceding lines -- covers a run-on single-line address with
    no newline anywhere ("120 N. Miller Rd. #300 Mansfield, TX 76063")."""
    lines = text.split("\n")
    city_zip_re = re.compile(rf"([A-Za-z .]+?),?\s+({_STATE_PATTERN}),?\s+(\d{{5}})", re.IGNORECASE)
    results = []
    for i, line in enumerate(lines):
        m = city_zip_re.search(line)
        if not m:
            continue
        city, state, zip_ = m.groups()
        street_parts = []
        j = i - 1
        while j >= 0:
            prev = lines[j].strip().rstrip(",")
            if not prev:
                j -= 1
                continue
            if re.match(r"^(STE|SUITE|UNIT|APT|#)\b", prev, re.I):
                street_parts.insert(0, prev)
                j -= 1
                continue
            # A footer/nav block with no <br> before the real address (so
            # it lands on the SAME text line as strip_tags flattens it)
            # commonly uses "|" to separate its own copyright text/links
            # from whatever follows -- a real street address never
            # contains a literal "|", so when one is present, only the
            # text after the LAST "|" is a candidate. Real case: a page's
            # footer line "(c) 2026 by Healthy Horizons Clinic | Sitemap |
            # Privacy | Healthy Horizons Clinic | 3913 Broaddus Ave" was
            # being reported WHOLE as the street -- "2026 by ..." matches
            # the street-number heuristic below (a 4-digit "2026" reads
            # just like a house number) purely because it's the start of
            # the line, even though the real address is only the last
            # "3913 Broaddus Ave" segment.
            if "|" in prev:
                prev = prev.rsplit("|", 1)[-1].strip()
            # a genuine street-number line is digits then a space then a
            # letter ("1772 W McDermott Dr") -- a phone/fax line ("469-340-
            # 2777 Fax: 469-925-2856") also starts with a digit but has a
            # dash/colon right after it instead, so it's rejected here
            # rather than being misread as the street
            if re.match(r"^\d{1,6}\s+[A-Za-z]", prev) and not re.search(r"\bfax\b|\bphone\b|\btel\b", prev, re.I):
                street_parts.insert(0, prev)
            break
        if street_parts:
            results.append({
                "street": " ".join(street_parts),
                "city": city.strip(" ,"),
                "state": _normalize_state(state),
                "zip": zip_.strip(),
            })
            continue
        window = "\n".join(lines[max(0, i - 1):i + 1])
        parsed = parse_street_city_state_zip(window)
        if parsed:
            results.append(parsed)
    # discard anything whose state/zip combination is impossible (see
    # zip_state_plausible's docstring) -- typically leftover template
    # placeholder content, not a real address
    return [r for r in results if zip_state_plausible(r["zip"], r["state"])]


_PHONE_TOKEN_RE = re.compile(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")


def _phones_by_zip_from_listing(text):
    """For a "locations" INDEX/grid page where each office is its own
    short card (address, then that SAME office's own phone number on the
    very next non-blank line, e.g. "Flower Mound, TX 75028" / "972-350-
    0225" / "Learn More"), maps each card's zip to its own phone.

    This exists because a location's own individual DETAIL sub-page often
    can't be trusted for its phone number at all: a real case (record 46,
    painspinetexas.com) has every detail sub-page embed the SAME sitewide
    "quick nav" widget with all 5-6 offices' tel: links pooled together,
    none of them labeled as belonging to any one page, so picking any one
    of them for "this page's phone" is a guess -- one that was
    consistently wrong. The index page's own card layout has no such
    ambiguity: each phone is unambiguously paired with the address
    directly above it. Returns {zip: phone}."""
    lines = text.split("\n")
    city_zip_re = re.compile(rf"([A-Za-z .]+?),?\s+({_STATE_PATTERN}),?\s+(\d{{5}})", re.IGNORECASE)
    result = {}
    for i, line in enumerate(lines):
        m = city_zip_re.search(line)
        if not m:
            continue
        zip_ = m.group(3)
        for j in range(i + 1, min(i + 3, len(lines))):
            nxt = lines[j].strip()
            if not nxt:
                continue
            phone_m = _PHONE_TOKEN_RE.fullmatch(nxt)
            if phone_m:
                result[zip_] = phone_m.group(0)
            break
    return result


def confirm_address_without_zip(text, record):
    """Last-resort confirmation for a page that genuinely never prints a
    zip code next to its address at all -- a real case: a site's header
    said only "3122 Milrany / Melissa, TX" (no zip anywhere on the page),
    so extract_addresses (which requires a 5-digit zip to anchor a match)
    found nothing, and the record's own, genuinely-present address was
    about to be reported as a "relocation" just because nothing with a zip
    matched it. Looks for the record's own street NUMBER within a short
    window before an occurrence of its CITY name -- much looser than the
    zip-based extraction, so only used as a fallback when that finds
    nothing at all, never to override a zip-based finding."""
    city = str(record.get("city") or "").strip()
    street_num = street_number(record.get("street"))
    if not city or not street_num:
        return None
    for m in re.finditer(re.escape(city), text, re.I):
        window = text[max(0, m.start() - 80):m.end()]
        if re.search(rf"(?<!\d){re.escape(street_num)}(?!\d)", window):
            return re.sub(r"\s+", " ", window).strip()
    return None


def extract_jsonld_locations(html):
    """schema.org LocalBusiness/MedicalOrganization/etc. JSON-LD blocks with
    a name/telephone/address -- when present, this is exact structured data
    straight from the page, not a regex guess. Handles both a bare object
    and a "@graph" array (common WordPress SEO-plugin output)."""
    found = []
    for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                             html, flags=re.I | re.S):
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        items = data.get("@graph", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if isinstance(data, dict) and "@graph" not in data:
            items = [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            addr = item.get("address")
            if isinstance(addr, dict) and addr.get("streetAddress"):
                found.append({
                    "name": item.get("name"),
                    "phone": item.get("telephone"),
                    "street": addr.get("streetAddress"),
                    "city": addr.get("addressLocality"),
                    "state": _normalize_state(addr.get("addressRegion") or ""),
                    "zip": str(addr.get("postalCode") or "").split("-")[0],
                })
    return found


# Matches a section-break HEADING (an actual <h1>-<h6> tag, or -- in
# already-stripped text -- a short standalone line), never just any
# mention of the word "locations" in running text (a marketing sentence
# like "we serve locations across Texas" earlier on the same page must NOT
# trigger a split). This is what distinguishes a page's headquarters/main
# contact info from a list of its OTHER offices when both sit on the same
# "Contact" page rather than a separate locations index (see
# find_other_locations for the separate-index-page version of this).
_OTHER_LOCATIONS_HEADING_HTML_RE = re.compile(
    r"<h[1-6][^>]*>\s*(?:our|other|all|additional)?\s*(?:office\s+)?locations\s*</h[1-6]>|"
    r"<h[1-6][^>]*>\s*branch(?:es)?\s*</h[1-6]>",
    re.I,
)
_OTHER_LOCATIONS_HEADING_TEXT_RE = re.compile(
    r"^(?:our|other|all|additional)?\s*(?:office\s+)?locations$|^branch(?:es)?$|"
    r"^satellite\s*(?:offices|locations)$",
    re.I,
)


def split_html_primary_other(html):
    """(primary_html, other_html) -- other_html is None if no such heading
    is found (the common case: most sites have only one location, and
    everything stays "primary"). Splitting the RAW html (not just the
    stripped text) matters because it's what lets href-based extraction
    (extract_tel_links_labeled et al) also respect the split, not just the
    plain-text-label extraction."""
    m = _OTHER_LOCATIONS_HEADING_HTML_RE.search(html)
    if not m:
        return html, None
    return html[:m.start()], html[m.start():]


def split_text_primary_other(text):
    """Text-level counterpart to split_html_primary_other, used for the
    address search (which works off the joined stripped-text of all
    fetched pages, not raw HTML)."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if _OTHER_LOCATIONS_HEADING_TEXT_RE.match(line.strip()):
            return "\n".join(lines[:i]), "\n".join(lines[i:])
    return text, None


def split_into_branch_blocks(other_text):
    """Within an "Office Locations"-style list, each branch is introduced
    by a short ALL-CAPS label line ("ABILENE", "DALLAS", "HABILITATIVE
    HOMES - RESIDENTIAL PROGRAM") followed by its own address/phone/fax.
    Split on that convention. `other_text` starts with the heading line
    itself (see split_html_primary_other/the text equivalent), so it's
    skipped here rather than treated as the first branch's name."""
    lines = [l for l in other_text.split("\n") if l.strip()]
    label_re = re.compile(r"^[A-Z0-9][A-Z0-9 .,&/'–-]{1,60}$")
    phoneish_re = re.compile(r"\d{3}.{0,3}\d{3}.{0,3}\d{4}")
    blocks = []
    name, buf = None, []
    for line in lines[1:]:
        s = line.strip()
        if label_re.match(s) and s == s.upper() and not phoneish_re.search(s):
            if name is not None and not buf:
                # no address/phone lines collected yet under the current
                # label -- this is a second label line right after the
                # first (a real case: "3H YOUTH RANCH - RESIDENTIAL
                # PROGRAM" followed by a "PERMANENTLY CLOSED" status line),
                # so it's a continuation of the same branch's name, not a
                # new branch
                name = f"{name} - {s}"
                continue
            if name is not None:
                blocks.append((name, "\n".join(buf)))
            name, buf = s, []
        else:
            buf.append(line)
    if name is not None:
        blocks.append((name, "\n".join(buf)))
    return blocks


def extract_branch_info(name, block_text, url):
    """Same idea as extract_location_info, but for one branch block of
    text (no JSON-LD to prefer here -- these are plain list entries on a
    contact page, not their own dedicated pages)."""
    found = extract_addresses(block_text)
    street, city, state, zip_ = (
        (found[0]["street"], found[0]["city"], found[0]["state"], found[0]["zip"])
        if found else (None, None, None, None)
    )
    tel_labels = extract_labeled_numbers_from_text(block_text)
    phones = sorted(n for n, lbl in tel_labels.items() if lbl == "phone")
    faxes = sorted(n for n, lbl in tel_labels.items() if lbl == "fax")
    if not street and not phones and not faxes:
        return None
    return {
        "name": name, "street": street, "city": city, "state": state, "zip": zip_,
        "phone": phones[0] if phones else None,
        "fax": faxes[0] if faxes else None,
        "url": url,
    }


def extract_location_info(url, html, phone_by_zip=None):
    """Best-effort name/address/phone/fax for ONE location detail page.
    Prefers embedded JSON-LD (exact) and falls back to the same
    text/tel-link extraction used for the main record when there's none.

    Also always computes the text-based address as `text_addr`, even when
    JSON-LD is present and used for `street`/`city`/`state`/`zip` -- some
    sites embed the SAME organization-level JSON-LD on every page,
    including "location" sub-pages that are clearly about a different
    office (confirmed by their phone numbers, which are page-specific and
    correctly differ). find_other_locations cross-checks this across all
    crawled pages and substitutes `text_addr` in when it catches that.

    `phone_by_zip` (see _phones_by_zip_from_listing) is an unambiguous
    per-office phone map built from the site's own "locations" index/grid
    page, if it has one -- preferred over anything found on THIS detail
    page whenever nothing here was clearly labeled "phone"/"fax" (see
    below), since a detail sub-page commonly pools every office's tel:
    links together with no way to tell which one is its own."""
    jsonld = extract_jsonld_locations(html)
    page_text = strip_tags(html)
    text_found = extract_addresses(page_text)
    text_addr = text_found[0] if text_found else None

    if jsonld:
        best = jsonld[0]
        name, street, city, state, zip_ = (
            best["name"], best["street"], best["city"], best["state"], best["zip"])
        address_source = "jsonld"
    else:
        names = extract_site_names(html)
        name = names.get("og_site_name") or names.get("title")
        street, city, state, zip_ = (
            (text_addr["street"], text_addr["city"], text_addr["state"], text_addr["zip"])
            if text_addr else (None, None, None, None)
        )
        address_source = "text"

    text_labels = extract_labeled_numbers_from_text(page_text)
    href_labels = extract_tel_links_labeled(html)
    tel_labels = {**href_labels, **text_labels}
    phones = sorted(n for n, lbl in tel_labels.items() if lbl == "phone")
    faxes = sorted(n for n, lbl in tel_labels.items() if lbl == "fax")
    if not phones and not faxes:
        # Nothing on this page was clearly labeled as either -- rather
        # than arbitrarily picking one number out of a pooled, unlabeled
        # "unknown" set (which was consistently the WRONG office's
        # number in practice), prefer the index page's own unambiguous
        # per-office phone when one's available for this exact zip.
        hinted = (phone_by_zip or {}).get(zip_ or "")
        phones = [hinted] if hinted else sorted(n for n, lbl in tel_labels.items() if lbl == "unknown")

    if not street and not phones:
        return None  # nothing usable found on this page at all

    return {
        "name": name, "street": street, "city": city, "state": state, "zip": zip_,
        "phone": phones[0] if phones else None,
        "fax": faxes[0] if faxes else None,
        "url": url,
        "address_source": address_source,
        "text_addr": text_addr,
    }


def find_other_locations(website, home_html, record):
    """Crawl a "locations" index page (if the site has one) and pull out
    name/address/phone/fax for each individual location it links to,
    excluding whichever one is this record's own (matched by street
    number). These are candidate NEW REDCap records -- surfaced for a human
    to review and add manually, never written anywhere automatically."""
    index_links = find_locations_index_links(home_html, website)
    detail_links = set()
    phone_by_zip = {}
    for index_url in index_links[:3]:
        index_html, resolved_index_url = _fetch(index_url)
        if not index_html:
            continue
        detail_links.update(find_location_detail_links(index_html, resolved_index_url or index_url))
        phone_by_zip.update(_phones_by_zip_from_listing(strip_tags(index_html)))
    if not detail_links:
        return []

    # Some sites only fill in each card's own phone number via JS after
    # load (a real case: painspinetexas.com's "/location/" grid page has
    # every card's own phone missing from the plain-fetched HTML
    # entirely, only appearing once rendered) -- retry with a headless-
    # browser render specifically to recover this, but only when the
    # cheap plain fetch came up with nothing at all, and only for the
    # same index page(s) already fetched above (not every detail page).
    if not phone_by_zip:
        for index_url in index_links[:3]:
            rendered = _fetch_browser(index_url)
            if rendered:
                phone_by_zip.update(_phones_by_zip_from_listing(strip_tags(rendered)))

    max_crawl = 40
    truncated = len(detail_links) > max_crawl
    infos = []
    for url in sorted(detail_links)[:max_crawl]:
        detail_html, _ = _fetch(url)
        if not detail_html:
            continue
        info = extract_location_info(url, detail_html, phone_by_zip)
        if info:
            infos.append(info)

    # A JSON-LD address that doesn't match the CITY named in its own page's
    # URL slug (e.g. .../locations-sherman claiming to be in "Frisco") is
    # very likely a generic, organization-wide schema copy-pasted onto
    # every location page, not one written for that specific page (a real
    # case: omnipainrelief.com's Sherman/Anna/Mesquite/Sunnyvale pages all
    # embedded the SAME JSON-LD pointing at the main Frisco office, even
    # though each page's own phone number -- extracted separately, from
    # text/tel: links, not JSON-LD -- correctly differed per page). Checked
    # per-page against that page's OWN slug rather than by comparing
    # addresses ACROSS pages, because the one page that's genuinely
    # supposed to have that address (Frisco itself, here) must be left
    # alone -- a same-address-appears-more-than-once rule would wrongly
    # "fix" that one too, discarding its only correct address.
    for info in infos:
        if info["address_source"] != "jsonld" or not info["text_addr"]:
            continue
        slug_city = _flat_location_slug_city(info["url"])
        jsonld_city = (info["city"] or "").upper()
        if slug_city and jsonld_city and slug_city not in jsonld_city and jsonld_city not in slug_city:
            ta = info["text_addr"]
            info["street"], info["city"], info["state"], info["zip"] = ta["street"], ta["city"], ta["state"], ta["zip"]

    own_street_num = street_number(record["street"])
    locations = [
        info for info in infos
        if not (info["street"] and own_street_num and street_number(info["street"]) == own_street_num)
    ]
    if truncated:
        print(f"  (site lists {len(detail_links)} locations; only the first {max_crawl} were crawled)")
    return locations


_xlsx_cache = {}


def _load_xlsx(xlsx_path):
    """Reading this spreadsheet (2710 rows x 917 columns) takes ~11
    seconds on its own -- load_record() used to call pd.read_excel fresh
    on every single call, so a --range batch of N record ids paid that
    cost N times over, even for ids that immediately fail with "not
    found" (no network fetch involved at all, just this). Cached per
    xlsx_path so it's read from disk once per process, not once per id."""
    if xlsx_path not in _xlsx_cache:
        df = pd.read_excel(xlsx_path)
        df.columns = [c.strip() for c in df.columns]
        _xlsx_cache[xlsx_path] = df
    return _xlsx_cache[xlsx_path]


def load_record(xlsx_path, record_id):
    df = _load_xlsx(xlsx_path)
    match = df[df["Record ID"] == record_id]
    if match.empty:
        # a plain exception, not SystemExit -- SystemExit would abort the
        # whole process (and the rest of a --range batch with it) the
        # moment ANY one id in the batch turns out to be missing, instead
        # of just skipping that one id and moving on
        raise ValueError(f"Record ID {record_id} not found in {xlsx_path}")
    row = match.iloc[0]

    def col(substr, exclude=()):
        for c in df.columns:
            lc = c.lower()
            if substr in lc and not any(e in lc for e in exclude):
                return c
        return None

    fields = {
        "name": row.get(col("organization/facility name")),
        "street": row.get(col("organization street name")),
        "city": row.get(col("organization city name")),
        "state": row.get(col("organization state", exclude=("mailing",))),
        "zip": row.get(col("organization zip code", exclude=("mailing",))),
        "county": row.get(col("organization county")),
        "phone": row.get(col("organization phone number")),
        "fax": row.get(col("organization fax number")),
        "email": row.get(col("organization email address")),
        "website": row.get(col("organization website")),
        "facebook": row.get("Facebook") if "Facebook" in df.columns else None,
        "instagram": row.get("Instagram") if "Instagram" in df.columns else None,
        "twitter": row.get("Twitter") if "Twitter" in df.columns else None,
    }
    return fields


def _extract_page_signals(pages):
    """Run every per-page extractor (tel/fax labels, email, social links,
    site name) over a {url: html} dict and merge the results. Factored out
    so the SAME logic can run twice: once over plain-`requests` HTML, and
    again over headless-browser-rendered HTML as a fallback (see verify())
    when the plain fetch came up empty on a signal a JS-heavy page hid."""
    href_labels, text_labels, all_mailto, all_social, site_names = {}, {}, [], {}, {}
    text_blobs = []
    for url, html in pages.items():
        if not html:
            continue
        page_text = strip_tags(html)
        for number, label in extract_tel_links_labeled(html).items():
            if number not in href_labels or href_labels[number] == "unknown":
                href_labels[number] = label
        for number, label in extract_labeled_numbers_from_text(page_text).items():
            text_labels.setdefault(number, label)
        all_mailto += extract_mailto_links(html)
        for platform, links in extract_social_links(html).items():
            all_social.setdefault(platform, []).extend(links)
        for key, value in extract_site_names(html).items():
            site_names.setdefault(key, value)  # keep the home page's version if seen first
        text_blobs.append(page_text)
    return href_labels, text_labels, all_mailto, all_social, site_names, text_blobs


# REDCap field variable names for this project (from the project's own
# data dictionary, confirmed via a read-only `content=metadata` API call --
# NOT guessed from the xlsx export's column labels). Only fields we're
# willing to auto-write go here: the facility NAME is deliberately excluded
# even though the script can flag a likely change, because a rebrand in
# marketing copy isn't necessarily the legal/DBA name REDCap should show --
# that always needs a human decision, never an auto-write. There is no
# REDCap field for LinkedIn at all in this project, so a LinkedIn finding
# can only ever be reported, never sent to REDCap.
REDCAP_FIELD_MAP = {
    "phone": "ct6", "fax": "ct13", "email": "ct7", "website": "ct8",
    "street": "ct2", "city": "ct3", "state": "ct9", "zip": "ct4", "county": "ct10",
    "facebook": "ct16", "instagram": "ct17", "twitter": "ct18",
}
# Deliberately kept OUT of REDCAP_FIELD_MAP above -- that map governs
# auto-writes to an EXISTING record's own fields, and a facility rename is
# never one of those (see the map's own docstring). A BRAND NEW record
# obviously needs a name to exist at all, though, so this is a separate
# constant used only when creating a new record for an "other location"
# found on a site (confirmed via the same read-only content=metadata call).
REDCAP_NAME_FIELD = "ct1"
REDCAP_CHANGE_FLAG_FIELD = "change"
REDCAP_CHANGE_NOTE_FIELD = "change_explain"          # same narrative note as the field below
REDCAP_ADDITIONAL_COMMENTS_FIELD = "general_comments"  # labeled "Additional Comments" in REDCap
REDCAP_VALIDATED_FIELD = "validated"                   # yesno, labeled "Validation"
REDCAP_VALIDATION_DATE_FIELD = "validation_date"       # date_mdy display, API wants YYYY-MM-DD
# yesno field, labeled "Is this a completed record from Phase 1 data
# collection?" -- per the user's request, a brand new record created here
# was obviously never part of that original data collection (set to "0",
# No), while successfully updating an EXISTING record's fields marks it
# as complete (set to "1", Yes). Confirmed via the same read-only
# content=metadata call as every other field id above.
REDCAP_PHASE1_COMPLETE_FIELD = "ogcomplete"
# radio field, labeled "Is the organization/facility open or closed?" --
# choices are "0, Open | 1, Closed | 2, Unverified ...". A location this
# tool just found live on the organization's own website is obviously
# open, so a brand new record is always created as Open.
REDCAP_ORGSTATUS_FIELD = "orgstatus"
REDCAP_ORGSTATUS_OPEN = "0"

# human-readable labels for the narrative note (matching the house style
# from real past examples: "The <label> was updated from X to Y"), NOT the
# same as field_changes' own "label" (which is closer to the REDCap field's
# own name, e.g. "organization phone number" vs. this dict's "phone number")
NARRATIVE_LABELS = {
    "phone": "Phone", "fax": "Fax", "email": "Email",
    "website": "Website", "street": "Address", "city": "City", "state": "State",
    "zip": "ZIP code", "county": "County", "facebook": "Facebook", "instagram": "Instagram",
    "twitter": "Twitter",
}


def _narrative_lines(applied_changes):
    """Shared by build_change_narrative (the REDCap comment) and
    build_summary_row (the CSV notes column) so both build their "Updated"
    section from identical logic. One "<Label> from: OLD to: NEW;" line per
    field that actually changed; a field whose old/new are the same after
    all (a relocation writes all four of street/city/state/zip together
    even when, say, the state didn't actually change) is left out UNLESS
    it's one of city/state/zip/county -- those are always shown alongside
    any address change, even when unchanged, as a plain "Label: value;"
    fact rather than a "from/to" pair. Returns (lines, saw_real_change) --
    saw_real_change is False when the only lines present are always-shown
    context facts, not real changes (context alone doesn't count)."""
    always_shown_keys = {"city", "state", "zip", "county"}
    lines = []
    saw_real_change = False
    for c in applied_changes:
        old_disp = c["old"] if c["old"] not in (None, "") else "(NONE)"
        new_disp = c["new"]
        unchanged = str(c["old"] or "").strip().lower() == str(new_disp).strip().lower()
        label = NARRATIVE_LABELS.get(c.get("key"), c["label"])
        if unchanged and c.get("key") in always_shown_keys:
            lines.append(f"{label}: {new_disp};")
            continue
        if unchanged:
            continue
        saw_real_change = True
        lines.append(f"{label} from: {old_disp} to: {new_disp};")
    return lines, saw_real_change


def build_change_narrative(applied_changes, needs_review=None):
    """Compact, label-based format per the user's request (replacing an
    earlier full-sentence house style): "Updated:\\nPhone from: OLD to:
    NEW;\\nEmail from: (NONE) to: NEW;" -- one line per field, no leading
    topic sentence, "(NONE)" instead of "(blank)" for a missing old value.
    This is what's actually written to REDCap's change_explain/
    general_comments, so its wording is intentionally NOT the same as the
    CSV summary's notes column (build_summary_row) -- that one uses
    "Manual Review Required:"/"No Change Required" headers per a later,
    CSV-specific request; this stays "Needs manual review: ..." since it's
    a live production write, not touched by that later request.

    `needs_review` (verify()'s own list of field labels -- "Phone"/"Fax"/
    "Email"/"Address" -- where multiple candidates were found on the site
    and none matched what's on file, so no auto-write was possible) is
    appended as its own line naming exactly which fields need a human to
    pick between the candidates, so a reviewer opening this same comment
    in REDCap knows what to go check without having to re-run the tool.

    Returns None if nothing actually changed AND nothing needs review
    (context-only entries alone don't count as a change)."""
    lines, saw_real_change = _narrative_lines(applied_changes)

    review_line = None
    if needs_review:
        review_line = ("Needs manual review: " + ", ".join(needs_review)
                        + " -- multiple candidates found on the website, could not determine which is correct.")

    if not lines or not saw_real_change:
        return review_line

    body = "Updated:\n" + "\n".join(lines)
    if review_line:
        body += "\n" + review_line
    return body


def _load_redcap_config():
    """Lazy import -- a dry-run-only user should never need
    redcap_config.py to exist at all; only --apply (and its pre-flight
    checks) actually touches the network or needs credentials."""
    try:
        from redcap_config import config
    except ImportError as e:
        raise RuntimeError(
            "redcap_config.py not found (or missing api_url/api_token) -- create it "
            "with `config = dict(api_url=..., api_token=...)` before using --apply"
        ) from e
    if "api_url" not in config or "api_token" not in config:
        raise RuntimeError("redcap_config.py's config dict must have both api_url and api_token")
    return config


def _redcap_auth_error_message(exc):
    """If `exc` (any exception raised by one of the redcap_* calls above)
    looks like REDCap rejected the API token itself -- as opposed to some
    other per-record problem like a stale live value or a network hiccup
    -- return a clear, actionable message; otherwise None. REDCap returns
    HTTP 401/403 for an expired/invalid/revoked token, which `raise_for_
    status()` turns into a `requests.HTTPError` carrying that response on
    `.response` -- any OTHER exception type (ConnectionError, Timeout,
    a plain RuntimeError, ...) has no such attribute, so `getattr(...,
    None)` correctly returns None for those, telling the caller this
    is NOT a token problem. Distinguishing this matters because every
    remaining record in the batch would fail the exact same way -- there
    is no point grinding through the rest of a --apply run once this is
    detected, unlike an ordinary single-record failure."""
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) in (401, 403):
        detail = (resp.text or "").strip()
        msg = (f"REDCap rejected the API token (HTTP {resp.status_code}) -- it is likely expired, "
               "invalid, or has been revoked. Update redcap_config.py with a new, valid token "
               "before running --apply again.")
        if detail:
            msg += f"\n  REDCap says: {detail}"
        return msg
    return None


def redcap_project_info(config):
    """Read-only: identifies which REDCap project the configured token is
    scoped to (a token is always scoped to exactly one project, never
    account-wide). Used right before --apply proceeds, so the person
    running the tool sees exactly which project is about to be written to
    and can confirm or cancel -- never inferred/assumed from the url alone,
    since the same api_url can host many projects."""
    data = {"token": config["api_token"], "content": "project", "format": "json", "returnFormat": "json"}
    resp = requests.post(config["api_url"], data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()


def redcap_export_record(record_id, fields):
    """Read-only: current live values for the given fields on one record.
    Used right before an --apply write, to catch the record having changed
    in REDCap since the xlsx snapshot this run's comparison was based on --
    never used to decide anything on its own."""
    config = _load_redcap_config()
    data = {
        "token": config["api_token"], "content": "record", "format": "json",
        "returnFormat": "json", "records[0]": str(record_id),
    }
    for i, f in enumerate(["record_id"] + list(fields)):
        data[f"fields[{i}]"] = f
    resp = requests.post(config["api_url"], data=data, timeout=30)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise RuntimeError(f"REDCap has no record {record_id} (it may have been deleted since the xlsx export)")
    return rows[0]


def redcap_search_records_by_name(search_term, fields):
    """Read-only: just the given fields, for the (small) set of REDCap
    records whose own name field contains `search_term` -- filtered
    SERVER SIDE via REDCap's filterLogic contains() function (confirmed
    live against this project's own API), so a duplicate check never has
    to pull the whole project's records, only the handful that could
    plausibly be a match for one specific location.

    `search_term` is only ever used to narrow candidates -- the real
    decision is the name_similarity + street-number check done afterward
    by whoever calls this (see find_live_redcap_duplicate) -- so a quote
    or backslash that would otherwise break the filterLogic string
    literal is simply dropped rather than escaped (REDCap's own escaping
    support for this turned out to be inconsistent in practice)."""
    literal = re.sub(r"""["'\\]""", "", search_term or "").strip()
    if not literal:
        return []
    config = _load_redcap_config()
    data = {
        "token": config["api_token"], "content": "record", "format": "json",
        "returnFormat": "json", "filterLogic": f'contains([{REDCAP_NAME_FIELD}], "{literal}")',
    }
    for i, f in enumerate(fields):
        data[f"fields[{i}]"] = f
    resp = requests.post(config["api_url"], data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()


def redcap_import_record(record_id, field_values):
    """Writes ONLY the fields in field_values (plus the record's own id) --
    REDCap's import API replaces just the fields present in the payload,
    not the whole record, so this can never blank out an untouched field."""
    config = _load_redcap_config()
    payload = {"record_id": str(record_id), **field_values}
    data = {
        "token": config["api_token"], "content": "record", "format": "json",
        "returnFormat": "json", "action": "import", "overwriteBehavior": "normal",
        "data": json.dumps([payload]),
    }
    resp = requests.post(config["api_url"], data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()


def apply_field_changes_to_redcap(record_id, field_changes, needs_review=None):
    """Before writing anything, EVERY field this call would actually write
    (i.e. every non-'context_only' change) is checked against REDCap's
    CURRENT live value (not the possibly-stale xlsx snapshot the change
    was originally computed from). If even one of them no longer matches
    -- someone already edited that field since, for instance -- NOTHING is
    written: this is all-or-nothing, never a partial write. Writing just
    the fields that still matched would leave the record looking
    "Updated" while simultaneously needing manual review for the very
    same write attempt, which is exactly the confusing state this avoids.
    Every real field change is reported back as skipped in that case, not
    just the one(s) that actually mismatched, so a reviewer can see the
    whole batch that didn't go through.

    When the write does go through: the SAME narrative note (see
    build_change_narrative) is appended -- never replacing whatever was
    already there -- to BOTH change_explain and general_comments
    ("Additional Comments"), `change` and `validated` ("Validation") are
    both set to Yes, and `validation_date` is set to today. This mirrors
    how staff have documented these updates by hand in the past, so an
    automated pass looks the same in REDCap as a manual one would.

    Returns (applied, skipped) -- lists of field_changes entries."""
    if not field_changes:
        return [], []
    real_changes = [c for c in field_changes if not c.get("context_only")]
    current = redcap_export_record(
        record_id, [c["field"] for c in real_changes] + [REDCAP_CHANGE_NOTE_FIELD, REDCAP_ADDITIONAL_COMMENTS_FIELD])

    mismatches = []
    for change in real_changes:
        live_value = (current.get(change["field"]) or "").strip()
        expected_old = (change["old"] or "").strip()
        if live_value != expected_old:
            mismatches.append({**change, "skip_reason": f"REDCap currently has {live_value!r}, "
                                                          f"not the {expected_old!r} this change was computed from"})

    if mismatches:
        # Abort the whole batch -- see docstring. Every real change is
        # reported as skipped: the ones that actually mismatched keep
        # their own reason, and every other real change in this batch
        # (which still matched) is reported as blocked by them, since
        # none of it was written.
        mismatched_fields = {m["field"] for m in mismatches}
        blocked_by = "; ".join(f"{m['label']} ({m['field']})" for m in mismatches)
        skipped = mismatches + [
            {**c, "skip_reason": f"not written -- this record's REDCap update was aborted because "
                                  f"{blocked_by} no longer matched the expected value"}
            for c in real_changes if c["field"] not in mismatched_fields
        ]
        return [], skipped

    # context_only entries (e.g. county: there's no way to determine a new
    # one from a website) are never written -- just carried into the
    # narrative as-is, alongside whichever real changes passed the check
    # above.
    applied = list(field_changes)
    payload = {c["field"]: c["new"] for c in real_changes}

    if not payload:
        return applied, []

    narrative = build_change_narrative(applied, needs_review)
    if narrative:
        for note_field in (REDCAP_CHANGE_NOTE_FIELD, REDCAP_ADDITIONAL_COMMENTS_FIELD):
            existing = (current.get(note_field) or "").strip()
            payload[note_field] = f"{existing}\n\n{narrative}" if existing else narrative
        payload[REDCAP_VALIDATION_DATE_FIELD] = datetime.date.today().isoformat()
        payload[REDCAP_VALIDATED_FIELD] = "1"
    payload[REDCAP_CHANGE_FLAG_FIELD] = "1"
    # Reaching here means at least one real field is about to be
    # successfully written to this EXISTING record (payload was checked
    # non-empty above) -- per the user's request, that marks it complete
    # for Phase 1 data collection purposes.
    payload[REDCAP_PHASE1_COMPLETE_FIELD] = "1"

    redcap_import_record(record_id, payload)
    return applied, []


_NOMINATIM_HEADERS = {"User-Agent": "FacilityVerifyBot/1.0 (REDCap facility-record verification tool)"}


def _clean_county_name(name):
    return re.sub(r"\s+County$", "", name, flags=re.I).strip().upper()


def _lookup_county_census(street, city, state, zip_code):
    """Tier 1: the U.S. Census Bureau's public Geocoding Services API --
    authoritative government address-range (TIGER/Line) data, but with
    real gaps: it couldn't match "3001 Cross Timbers Rd" (Flower Mound)
    or "2118 E State Highway 114" (Southlake) at all, both perfectly
    valid, real addresses -- apparently because those particular street
    segments simply aren't in its reference ranges."""
    params = {
        "street": street, "city": city or "", "state": state or "",
        "zip": zip_code or "", "benchmark": "Public_AR_Current",
        "vintage": "Current_Current", "format": "json",
    }
    try:
        resp = requests.get("https://geocoding.geo.census.gov/geocoder/geographies/address",
                             params=params, timeout=20)
        resp.raise_for_status()
        matches = resp.json()["result"]["addressMatches"]
        if not matches:
            return None
        counties = matches[0]["geographies"].get("Counties") or []
        return _clean_county_name(counties[0]["NAME"]) if counties else None
    except Exception:
        return None


def _lookup_county_nominatim(street, city, state, zip_code):
    """Tier 2 fallback, tried only when Census comes up empty:
    OpenStreetMap's Nominatim geocoder, which matched both of the real
    addresses above that Census couldn't. Free, no key, but its usage
    policy caps casual/free use at roughly 1 request/second -- this is
    only ever called for the handful of addresses Census couldn't match
    in one run, so a flat 1s pause here stays well under that."""
    address = ", ".join(p for p in (street, city, state, zip_code) if p)
    try:
        time.sleep(1)
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address, "format": "jsonv2", "addressdetails": 1, "limit": 1},
            headers=_NOMINATIM_HEADERS, timeout=20,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None
        county = results[0].get("address", {}).get("county")
        return _clean_county_name(county) if county else None
    except Exception:
        return None


def lookup_county_for_address(street, city, state, zip_code):
    """Free, no-key reverse lookup of the county a street address falls
    in -- used only when creating a brand new REDCap record for an
    "other location" (see create_redcap_location_record), since that's
    the one case with no existing on-file county value to just carry
    forward unchanged the way an existing record's own update already
    does. Tries the Census geocoder first, then OpenStreetMap's Nominatim
    if that finds nothing (see the two tier functions above for why
    neither alone is enough). Returns the plain county name matching this
    project's existing "organization county" values (e.g. "DENTON" -- no
    "County" suffix, uppercased), or None if NEITHER service could match
    the address -- never guessed, and any network/parsing problem in
    either tier is treated the same as no match rather than raised, since
    a blank county is far safer than a wrong one for a field a human will
    double check anyway."""
    if not street:
        return None
    # Both geocoders below return ZERO matches for an otherwise-valid
    # address with a suite/unit number tacked on (e.g. "3001 Cross
    # Timbers Rd, Suite 120" matches nothing from either service, while
    # "3001 Cross Timbers Rd" alone matches cleanly) -- a suite is inside
    # a building, not a separate geographic point, so it doesn't affect
    # which COUNTY the building is in anyway. Only the geocoding query
    # uses the stripped street; the full address (suite included) is
    # still what's written to REDCap.
    clean_street = _SUITE_CLAUSE_RE.sub("", street).strip(" ,")
    return (_lookup_county_census(clean_street, city, state, zip_code)
            or _lookup_county_nominatim(clean_street, city, state, zip_code))


# Every location THIS run has already created, so a second new location
# later in the same run -- found via a DIFFERENT parent record -- that
# turns out to be the exact same place is still caught even though a
# fresh REDCap search (see find_live_redcap_duplicate) would normally be
# enough on its own. Checked first, with no network call at all.
_locations_created_this_run = []


def _remember_created_location_for_dedup(loc, new_record_id):
    num = street_number(loc.get("street"))
    if num:
        _locations_created_this_run.append((new_record_id, loc.get("effective_name"), num))


def find_live_redcap_duplicate(loc, exclude_record_id=None):
    """Checks REDCap directly for a record that looks like it's already
    this exact location -- searched by NAME first (server-side, via
    redcap_search_records_by_name, so this never has to pull the whole
    project), using the location's own base/cleaned name (before any "-
    City" disambiguation was added, since an EXISTING record for the same
    org is likely to have that base name verbatim) -- then confirmed by
    matching street NUMBER on whatever that search turns up, plus a loose
    name similarity against the name this location would actually be
    created with. This is checked right before every create, live, so it
    reflects anything already in REDCap RIGHT NOW -- including a record
    this very run already created a moment ago for a different parent
    (see _locations_created_this_run above), which a one-time snapshot
    (the xlsx export, or a single bulk fetch at the start of the run)
    would have no way to know about. Returns (record_id, name) of the
    match, or None."""
    candidate_name = loc.get("effective_name") or loc.get("name") or ""
    num = street_number(loc.get("street"))
    if not candidate_name or not num:
        return None
    exclude = None if exclude_record_id is None else str(exclude_record_id)

    for rid, name, existing_num in _locations_created_this_run:
        if exclude is not None and str(rid) == exclude:
            continue
        if existing_num == num and name_similarity(candidate_name, name or "") >= 0.5:
            return (rid, name)

    search_term = loc.get("cleaned_name") or candidate_name
    rows = redcap_search_records_by_name(
        search_term, ["record_id", REDCAP_NAME_FIELD, REDCAP_FIELD_MAP["street"]])
    for row in rows:
        rid = row.get("record_id")
        if exclude is not None and str(rid) == exclude:
            continue
        if street_number(row.get(REDCAP_FIELD_MAP["street"])) != num:
            continue
        name = row.get(REDCAP_NAME_FIELD)
        if name_similarity(candidate_name, name or "") >= 0.5:
            return (rid, name)
    return None


def create_redcap_location_record(loc, parent_record_id):
    """Creates a brand new REDCap record for one "other location" found on
    a site -- name/street/city/state/zip/phone/fax/website plus a
    best-effort county lookup (see lookup_county_for_address) are set;
    everything else on the form (services offered, accreditation, patient
    limits, etc.) is left blank for staff to fill in by hand. Per the
    user's request, a new record is always marked NOT complete for Phase
    1 data collection (REDCAP_PHASE1_COMPLETE_FIELD = "0" -- it obviously
    wasn't part of that original collection) and Open (a location this
    tool just found live on the organization's own website is, by
    definition, currently operating). Record numbering is left entirely
    to REDCap itself (forceAutoNumber) -- this project has record
    auto-numbering enabled, so the id returned here is whatever REDCap
    actually assigned, never anything guessed locally.

    `parent_record_id` is the record whose own website this location was
    found on -- written into Additional Comments so anyone looking at this
    new record later can trace it back to where it came from and how it
    got there, the same way a manual entry would note its own source.

    Returns (new_record_id, county) -- county is whatever
    lookup_county_for_address found, or None."""
    config = _load_redcap_config()
    county = lookup_county_for_address(loc.get("street"), loc.get("city"), loc.get("state"), loc.get("zip"))
    payload = {
        "record_id": "0",  # placeholder -- forceAutoNumber replaces this
        REDCAP_NAME_FIELD: loc["effective_name"],
        REDCAP_FIELD_MAP["street"]: loc.get("street") or "",
        REDCAP_FIELD_MAP["city"]: loc.get("city") or "",
        REDCAP_FIELD_MAP["state"]: loc.get("state") or "",
        REDCAP_FIELD_MAP["zip"]: loc.get("zip") or "",
        REDCAP_FIELD_MAP["phone"]: loc.get("phone") or "",
        REDCAP_FIELD_MAP["fax"]: loc.get("fax") or "",
        REDCAP_FIELD_MAP["website"]: loc.get("url") or "",
        REDCAP_PHASE1_COMPLETE_FIELD: "0",
        REDCAP_ORGSTATUS_FIELD: REDCAP_ORGSTATUS_OPEN,
        REDCAP_ADDITIONAL_COMMENTS_FIELD: (
            "This record was added using the Automated_Comptroller_Verification script after being "
            f"identified as an additional location associated with REDCap Record ID: {parent_record_id}"
        ),
    }
    if county:
        payload[REDCAP_FIELD_MAP["county"]] = county
    data = {
        "token": config["api_token"], "content": "record", "format": "json",
        "returnFormat": "json", "action": "import", "overwriteBehavior": "normal",
        "forceAutoNumber": "true", "returnContent": "ids",
        "data": json.dumps([payload]),
    }
    resp = requests.post(config["api_url"], data=data, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(result["error"])
    new_id = result[0] if isinstance(result, list) and result else None
    if not new_id:
        raise RuntimeError(f"REDCap did not return a new record id -- response: {result}")
    return new_id, county


def _clean_location_name(name):
    """Strips trademark marks and any city/state tag a site baked directly
    into a location's name -- e.g. "Carrollton Springs Changes® | Frisco,
    TX" becomes "Carrollton Springs Changes". Any city needed to
    disambiguate two same-named locations is added back deliberately, in
    ONE controlled "<name> - <City>" format, by
    _assign_effective_location_names below -- never whatever the site
    itself happened to tack on (a pipe-separated nav crumb, a trailing
    ", City, ST", etc.)."""
    if not name:
        return name
    # Sites are inconsistent about which dash character they use in a
    # name (an en dash "–", em dash "—", minus sign "−",
    # etc.), and comparing/searching on the raw text downstream (REDCap's
    # own filterLogic contains(), used by find_live_redcap_duplicate, is
    # an exact-character substring match -- not the fuzzy name_similarity
    # used elsewhere) would silently miss a name that's identical except
    # for the dash character. A real case: "HABILITATIVE HOMES –
    # RESIDENTIAL PROGRAM" (en dash, exactly as the site renders it) got
    # created as a new REDCap record three separate times, because each
    # run's duplicate search used whatever dash the site happened to
    # render that day, which never matched the plain-hyphen version
    # already sitting in REDCap from an earlier run. Normalized to a
    # plain "-" here, once, up front -- so every use of this name from
    # here on (the search term AND the value actually written to REDCap)
    # is consistent regardless of what the site itself uses.
    cleaned = re.sub(r"[‐-―−]", "-", name)
    # A "|" in a scraped name is reliably a nav/branding separator, never
    # part of the real facility name -- the same assumption already used
    # for street-address junk (see extract_addresses). A page's <title>
    # or og:site_name meta tag, used here as a fallback when there's no
    # JSON-LD, commonly reads like "Geode Health | Mental Health Care
    # Focused On You" (brand + marketing tagline) or "Some Clinic |
    # Sitemap | Privacy" -- only the FIRST segment is ever the real name.
    cleaned = cleaned.split("|", 1)[0]
    cleaned = re.sub(r"[®™©]", "", cleaned)
    cleaned = re.sub(r"\(\s*[Rr]\s*\)", "", cleaned)
    cleaned = re.sub(r"\(\s*[Tt][Mm]\s*\)", "", cleaned)
    # a trailing "<sep> City, ST" tail -- city is letters/spaces/periods/
    # hyphens/apostrophes, state is a two-letter abbreviation
    cleaned = re.sub(r"\s*[,\-]\s*[A-Za-z .'\-]+,\s*[A-Z]{2}\s*$", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" |-,")


def _first_street_word(street):
    """The first real word of a street address, skipping the leading
    house NUMBER (e.g. "Alameda" from "8720 Alameda Ave Ste B") -- used
    only as a last-resort disambiguator, in _assign_effective_location_names
    below, between two fallback-named locations that share the same
    city too."""
    if not street:
        return None
    words = re.sub(r"^\d+\s*", "", street).split()
    return words[0] if words else None


def _assign_effective_location_names(locations, parent_name=None):
    """A site's own "locations" listing often just repeats ONE org-wide
    name for every branch it lists, with no distinct per-branch name at
    all -- when that's the case here (the same CLEANED name shows up on
    2+ of this record's OTHER locations), that name alone can't identify
    any ONE of them as its own new REDCap record, so each gets
    disambiguated as "<name> - <City>" (e.g. a record whose site lists
    "Texoma Care Primary and Specialty Physicians" for several different
    addresses becomes "Texoma Care Primary and Specialty Physicians -
    Sherman", "... - Denison", etc.). A name that's unique among this
    batch is left alone, since it's presumably already that branch's own
    distinct name (e.g. "Texoma Urgent Care" vs. "Texoma Family
    Practice").

    When a location has NO usable name at all (nothing left after
    cleanup), a fallback is built instead of leaving it blank: "<parent
    record's own name> - <City>" (e.g. a location found on Healthy
    Horizons Clinic's own site, with no name of its own, in El Paso
    becomes "HEALTHY HORIZON CLINICS - El Paso"). If that's still not
    enough to tell apart two DIFFERENT unnamed locations in the SAME
    city, the first word of the street name is appended too (e.g. "...
    - El Paso - Alameda" for "8720 Alameda Ave Ste B"). This fallback
    only ever applies to a location with no name to begin with -- one
    with an ambiguous but genuine name is still handled the way above."""
    cleaned_names = {id(loc): _clean_location_name(loc.get("name")) for loc in locations}
    name_counts = Counter((n or "").strip().lower() for n in cleaned_names.values() if n)

    fallback_city_counts = Counter()
    for loc in locations:
        if not (cleaned_names[id(loc)] or "").strip() and parent_name and loc.get("city"):
            fallback_city_counts[loc["city"].strip().lower()] += 1

    result = []
    for loc in locations:
        name = (cleaned_names[id(loc)] or "").strip()
        if not name and parent_name and loc.get("city"):
            effective_name = f"{parent_name} - {loc['city']}"
            if fallback_city_counts[loc["city"].strip().lower()] > 1:
                first_word = _first_street_word(loc.get("street"))
                if first_word:
                    effective_name = f"{effective_name} - {first_word}"
        elif name and name_counts[name.lower()] > 1 and loc.get("city"):
            effective_name = f"{name} - {loc['city']}"
        else:
            effective_name = name
        # kept alongside effective_name (which may have "- City" tacked on)
        # so a later live-REDCap duplicate search (see
        # find_live_redcap_duplicate) can search on the base org name --
        # what an EXISTING record for the same org is actually likely to
        # have, verbatim -- rather than our own added city qualifier.
        result.append({**loc, "effective_name": effective_name, "cleaned_name": name})
    return result


def _street_name_without_number(street):
    """The rest of a street string after stripping its leading house
    number -- e.g. "Broaddus Ave" from "3913 Broaddus Ave". Used only by
    _drop_same_name_street_collisions below to compare two addresses'
    street NAMES loosely, independent of their house numbers."""
    if not street:
        return ""
    return re.sub(r"^\s*\d+\s*", "", street).strip()


def _drop_same_name_street_collisions(locations):
    """Per the user's request: when two locations end up with the exact
    same effective_name AND the same street NAME (only their house
    NUMBER differs -- e.g. "3905 Broaddus Avenue" and "3913 Broaddus
    Ave" both landing on the same fallback name "... - El Paso -
    Broaddus", since the fallback in _assign_effective_location_names
    only disambiguates by city + the street's first word), only the
    FIRST one found is kept; the rest are dropped entirely -- never
    created, and never even reported on the Other Locations tab -- so
    two genuinely different addresses don't end up sharing one
    confusing, identical name."""
    kept = []
    for loc in locations:
        name_key = (loc.get("effective_name") or "").strip().lower()
        street_key = _normalize_street_for_compare(_street_name_without_number(loc.get("street")))
        collides = bool(name_key) and bool(street_key) and any(
            (k.get("effective_name") or "").strip().lower() == name_key
            and _normalize_street_for_compare(_street_name_without_number(k.get("street"))) == street_key
            for k in kept
        )
        if collides:
            continue
        kept.append(loc)
    return kept


# Matched as whole words against a location's name -- a real case: a
# branch-list entry like "3H YOUTH RANCH - RESIDENTIAL PROGRAM" was
# immediately followed by its own "PERMANENTLY CLOSED" status line, which
# the name-block parsing (split_into_branch_blocks) folds into that same
# branch's name rather than treating it as a separate block. There's no
# reason to auto-create a REDCap record for a location the site itself
# says no longer operates.
_CLOSED_NAME_RE = re.compile(r"\bPERMANENTLY\b|\bCLOSED\b", re.IGNORECASE)


def _location_creation_eligible(loc):
    """A location only becomes its own new REDCap record when we're sure
    enough about it: an identifiable name, a street NUMBER (not just a
    city/zip), its own website, and a name that doesn't itself say the
    location is closed -- per the user's explicit criteria. Anything less
    falls back to a row on the Other Locations tab instead, same as
    before this feature existed."""
    name_text = f"{loc.get('name') or ''} {loc.get('effective_name') or ''}"
    return (bool(loc.get("effective_name")) and bool(street_number(loc.get("street")))
            and bool(loc.get("url")) and not _CLOSED_NAME_RE.search(name_text))


def _location_ineligible_reason(loc):
    name_text = f"{loc.get('name') or ''} {loc.get('effective_name') or ''}"
    if _CLOSED_NAME_RE.search(name_text):
        return "Failed to add new record: name indicates this location is closed"
    missing = []
    if not loc.get("effective_name"):
        missing.append("name")
    if not street_number(loc.get("street")):
        missing.append("street address/number")
    if not loc.get("url"):
        missing.append("website")
    return "Failed to add new record: missing " + " and ".join(missing)


_existing_locations_index_cache = {}


def _existing_redcap_locations_index(xlsx_path):
    """Every (street number) -> [(record id, name), ...] already on file
    in the whole xlsx export, built once per file and cached. Used only to
    check that a new-location candidate isn't actually an already-tracked
    facility under a different record id before creating a brand new
    REDCap record for it -- this project's whole purpose is de-duplicating
    facility records, so a location otherwise confident enough to create
    still gets one more check against the rest of the dataset first."""
    if xlsx_path in _existing_locations_index_cache:
        return _existing_locations_index_cache[xlsx_path]
    df = _load_xlsx(xlsx_path)

    def col(substr, exclude=()):
        for c in df.columns:
            lc = c.lower()
            if substr in lc and not any(e in lc for e in exclude):
                return c
        return None

    name_col, street_col = col("organization/facility name"), col("organization street name")
    index = {}
    if name_col and street_col:
        for _, row in df.iterrows():
            num = street_number(row.get(street_col))
            if num:
                index.setdefault(num, []).append((row.get("Record ID"), row.get(name_col)))
    _existing_locations_index_cache[xlsx_path] = index
    return index


def _find_existing_duplicate_record(loc, xlsx_path, exclude_record_id):
    """Returns (record_id, name) of an existing REDCap record that looks
    like it's already this same location -- matched on an exact street
    NUMBER plus a loose name similarity (an exact street-number match with
    a wildly different name is presumably a different business that
    happens to share a building, not a duplicate) -- or None."""
    num = street_number(loc.get("street"))
    if not num:
        return None
    candidate_name = loc.get("effective_name") or loc.get("name") or ""
    for rid, name in _existing_redcap_locations_index(xlsx_path).get(num, []):
        if rid == exclude_record_id:
            continue
        if name_similarity(candidate_name, name or "") >= 0.5:
            return (rid, name)
    return None


OTHER_LOCATIONS_COLUMNS = ["record_id", "name", "street", "city", "state", "zip", "phone", "fax", "url", "reason"]

# One row per brand new REDCap record actually created for an "other
# location" (see create_redcap_location_record) -- kept on its own tab,
# separate from Other Locations (which is only ever locations that did
# NOT get auto-created: disqualified, a likely duplicate, or a failed
# create attempt), so a reviewer can tell at a glance what's new in
# REDCap versus what still needs a human to add by hand.
CREATED_LOCATIONS_COLUMNS = [
    "record_id", "new_record_id", "name", "street", "city", "state", "zip", "county", "phone", "fax", "url",
]


SUMMARY_COLUMNS = [
    "redcap_record_id",
    "website_changed", "website_old", "website_new",
    "phone_changed", "phone_old", "phone_new",
    "fax_changed", "fax_old", "fax_new",
    "email_changed", "email_old", "email_new",
    "social_changed", "social_old", "social_new",
    "address_changed", "address_old", "address_new",
    "name_changed", "name_old", "name_new",
    "notes", "change_required",
    "manual_review_required", "updated_redcap_record", "found_multiple_locations",
]


def _field_changes_for(field_changes, keys):
    return [c for c in field_changes if c["key"] in keys and not c.get("context_only")]


def _old_new_display(changes):
    """Blank/blank when nothing in this category changed. A single changed
    field shows its plain old/new value; 2+ (e.g. both Facebook AND
    Instagram changed, or a relocation touching street+city+state+zip
    together) are joined as "<Label>: <value>; <Label>: <value>" so each
    value stays attributable to its own field."""
    if not changes:
        return "", ""
    label = lambda c: NARRATIVE_LABELS.get(c["key"], c["label"])
    old_disp = lambda c: c["old"] if c["old"] not in (None, "") else "(NONE)"
    if len(changes) == 1:
        return str(old_disp(changes[0])), str(changes[0]["new"])
    old_str = "; ".join(f"{label(c)}: {old_disp(c)}" for c in changes)
    new_str = "; ".join(f"{label(c)}: {c['new']}" for c in changes)
    return old_str, new_str


def _on_file_display(value):
    """NaN-safe plain string for an "existing on-file value" column --
    never raises on a pandas NaN/None the way str(nan) technically
    wouldn't, but a bare "nan" string would look like real data."""
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)


def build_summary_row(record_id, result, record, updated_redcap=False, dry_run=False,
                       redcap_error=None, redcap_skipped=None):
    """One row per valid (found-in-spreadsheet) record for the run-level
    summary spreadsheet -- a quick per-record scan of what changed, what
    needs a human to look at it, and what (if anything) got written to
    REDCap. Mirrors the same field_changes/needs_review/name_changed data
    already computed by verify() and shown in the console/file report and
    the REDCap comment -- this is just that same information reshaped into
    one flat row instead of free text.

    `dry_run` (True whenever --apply wasn't passed) only changes the
    "Updated:" section's own label to "Potential Update:" -- nothing was
    actually written to REDCap in that mode, so the note shouldn't claim
    otherwise, even though the *_changed columns still show what WOULD
    change (same as always, independent of --apply).

    `redcap_error` (a string, only ever set under --apply) means the whole
    REDCap write attempt for this record raised an exception -- nothing
    was written at all. `redcap_skipped` (a list of the field_changes
    entries apply_field_changes_to_redcap itself declined to write, each
    with its own "skip_reason") means the write mostly succeeded but one
    or more fields were left untouched, typically because REDCap's live
    value no longer matched what the change was computed from. Either
    case gets its own line in the notes and forces manual_review_required
    -- a failed or partial write is exactly the kind of thing a human
    needs to notice and retry/investigate, not something that should
    quietly look like nothing happened.

    Creating new REDCap records for "other locations" found on this
    record's own website (see create_redcap_location_record) is entirely
    separate from this record's own manual_review_required/notes: whether
    those creations succeed or fail has no bearing on whether THIS record
    needs review, since they're different REDCap records altogether. A
    failed creation is reported only on the Other Locations tab (with its
    own reason column), never here.

    `record` is the same on-file dict verify() itself worked from (as
    returned by load_record) -- its own website/phone/fax/email/address/
    name values are what the *_old columns always show, REGARDLESS of
    whether this record has any detected change, is blocked for manual
    review, or has no usable website at all. Previously those columns
    were forced blank in every one of those cases (matching the "blank
    when nothing changed" rule the *_new columns still follow) -- per the
    user's request, "existing data" should never disappear just because
    nothing could be auto-applied; a reviewer needs to see what's
    currently on file for every record, not just the ones with a clean
    single-candidate change."""
    field_changes = result["field_changes"]
    needs_review = result["needs_review"]
    needs_review_details = result["needs_review_details"]
    name_changed = result["name_changed"]
    other_locations = result["other_locations"]
    found_multiple_locations = bool(other_locations) or bool(result.get("new_location_candidates"))
    # Set only by verify()'s two early-return paths -- no website on file
    # at all, or the one on file couldn't be reached/rendered (fetch
    # failure, timeout, error/challenge page even after a browser-render
    # retry). Either way there's nothing else to check for this record,
    # so it's handled as its own case below rather than folded into the
    # normal ambiguity-based "blocked" handling.
    website_invalid = bool(result.get("website_invalid"))

    # Ambiguity found ANYWHERE in this record blocks auto-writing EVERY
    # field for it, not just the ambiguous one(s) -- per the user's
    # explicit request: an otherwise-unambiguous email/social change sits
    # unapplied right alongside the ambiguous field(s), all surfaced only
    # as suggestions for a human to act on. So every *_changed column
    # reads "No" (nothing was actually auto-writable) and old/new stay
    # blank (same "blank when no change" rule already used elsewhere) --
    # the suggested values live only in the notes column instead.
    blocked = bool(needs_review)

    # Always shown regardless of blocked/website_invalid/no-change status
    # -- see the docstring's `record` note above. address_old combines
    # street/city/state/zip into the same single-line format the "On
    # file: ..." report lines already use elsewhere.
    website_old = _on_file_display(_normalize_url(record["website"]))
    phone_old = _on_file_display(record["phone"])
    fax_old = _on_file_display(record["fax"])
    email_old = _on_file_display(record["email"])
    name_old = _on_file_display(record["name"])
    address_old = (f"{_on_file_display(record['street'])}, {_on_file_display(record['city'])}, "
                    f"{_on_file_display(record['state'])} {_on_file_display(record['zip'])}")

    if website_invalid or blocked:
        website_changed = phone_changed = fax_changed = email_changed = social_changed = address_changed = name_changed = False
        website_new = phone_new = fax_new = email_new = social_new = address_new = name_new = ""
        social_old = ""
    else:
        website_changes = _field_changes_for(field_changes, ("website",))
        phone_changes = _field_changes_for(field_changes, ("phone",))
        fax_changes = _field_changes_for(field_changes, ("fax",))
        email_changes = _field_changes_for(field_changes, ("email",))
        social_changes = _field_changes_for(field_changes, ("facebook", "instagram", "twitter"))
        address_changes = _field_changes_for(field_changes, ("street", "city", "state", "zip"))

        website_changed = bool(website_changes)
        phone_changed, fax_changed, email_changed = bool(phone_changes), bool(fax_changes), bool(email_changes)
        social_changed, address_changed = bool(social_changes), bool(address_changes)

        # only the "new" side is taken from _old_new_display here -- "old"
        # always comes from the on-file record itself, computed above,
        # not from whatever field_changes happened to capture
        _, website_new = _old_new_display(website_changes)
        _, phone_new = _old_new_display(phone_changes)
        _, fax_new = _old_new_display(fax_changes)
        _, email_new = _old_new_display(email_changes)
        social_old, social_new = _old_new_display(social_changes)
        _, address_new = _old_new_display(address_changes)
        name_new = result.get("name_new") or ""

    # Notes format per the user's request: a record with no usable website
    # at all gets one fixed note and is unconditionally flagged for manual
    # review, ahead of every other case (there's nothing else to check).
    # Otherwise, when this record is BLOCKED (ambiguity found somewhere),
    # everything -- the fields that would otherwise have auto-written, the
    # ambiguous fields' own candidate lists, and a possible name change --
    # is folded into ONE "Manual Review Required:" section, since nothing
    # here gets auto-applied. Otherwise, the same three-way split as
    # before: "Updated:" for fields this record CAN have auto-written;
    # "Manual Review Required:" just for a possible name change (never
    # auto-written even when unambiguous); or, when neither applies, "No
    # Change Required".
    if website_invalid:
        notes = ("Manual Review Required: No website address found or invalid website address "
                  "for this record")
    elif blocked:
        review_items = []
        lines, saw_real_change = _narrative_lines(field_changes)
        if lines and saw_real_change:
            review_items.append("\n".join(lines))
        for label, candidates in needs_review_details.items():
            review_items.append(f"{label}: multiple candidates found, could not determine which is correct "
                                 f"(website has {', '.join(str(c) for c in candidates)})")
        if result["name_changed"]:
            review_items.append(f"Facility name: possible name change on the website, never auto-written "
                                 f"(record has {result['name_old']!r}, site shows {result['name_new']!r})")
        notes = "Manual Review Required:\n" + "\n".join(review_items)
    else:
        note_sections = []
        lines, saw_real_change = _narrative_lines(field_changes)
        if lines and saw_real_change:
            # "Updated:" only when the write actually went through. Under
            # --apply, field_changes still holds every field that WOULD
            # change even when the write was skipped/aborted/failed (see
            # apply_field_changes_to_redcap) -- this must read the same as
            # a dry run in that case, not claim success right next to a
            # manual-review note about the very same fields.
            update_label = "Updated:" if (updated_redcap and not dry_run) else "Potential Update:"
            note_sections.append(f"{update_label}\n" + "\n".join(lines))
        if name_changed:
            note_sections.append(
                "Manual Review Required: Facility name (possible name change on the website, never auto-written)")
        if redcap_error:
            note_sections.append(
                f"Manual Review Required: Failed to update this record in REDCap -- {redcap_error}")
        for c in (redcap_skipped or []):
            note_sections.append(
                f"Manual Review Required: Could not update {c['label']} ({c['field']}) in REDCap -- "
                f"{c['skip_reason']}")
        # Now that the summary spreadsheet is a real .xlsx (not a CSV),
        # the notes cell can hold genuine multi-line text -- wrap_text is
        # turned on for this column when the workbook is written, so every
        # line is actually visible instead of hiding behind a CSV
        # viewer's single-visual-line cell rendering.
        notes = "\n\n".join(note_sections) if note_sections else "Record up to date: No Change Required"

    manual_review_required = website_invalid or blocked or bool(redcap_error) or bool(redcap_skipped)
    change_required = bool(
        website_changed or phone_changed or fax_changed or email_changed or social_changed or address_changed
        or name_changed or manual_review_required or found_multiple_locations
    )

    def yn(b):
        return "Yes" if b else "No"

    return {
        "redcap_record_id": record_id,
        "website_changed": yn(website_changed), "website_old": website_old, "website_new": website_new,
        "phone_changed": yn(phone_changed), "phone_old": phone_old, "phone_new": phone_new,
        "fax_changed": yn(fax_changed), "fax_old": fax_old, "fax_new": fax_new,
        "email_changed": yn(email_changed), "email_old": email_old, "email_new": email_new,
        "social_changed": yn(social_changed), "social_old": social_old, "social_new": social_new,
        "address_changed": yn(address_changed), "address_old": address_old, "address_new": address_new,
        "name_changed": yn(name_changed), "name_old": name_old, "name_new": name_new,
        "notes": notes,
        "change_required": yn(change_required),
        "manual_review_required": yn(manual_review_required),
        "updated_redcap_record": yn(updated_redcap),
        "found_multiple_locations": yn(found_multiple_locations),
    }


def write_run_workbook(path, summary_rows, other_location_rows, created_location_rows=None):
    """One .xlsx per run (replacing the two separate CSVs this used to be)
    -- "Record Summary" as the first/active tab (the main one to review,
    columns per SUMMARY_COLUMNS), "Other Locations" as the second
    (OTHER_LOCATIONS_COLUMNS, locations NOT auto-created -- disqualified, a
    likely duplicate, or a failed create attempt), and "New Records Added"
    as the third (CREATED_LOCATIONS_COLUMNS, one row per brand new REDCap
    record this run actually created). Always overwrites `path` outright rather
    than appending -- each run already gets its own uniquely timestamped
    filename (see main()), so there's nothing to accumulate into across
    runs the way the old CSV-based other-locations file used to.

    The summary sheet's "notes" column gets wrap_text turned on, a wide
    column, and a row height sized to its actual line count -- now that
    this is a real spreadsheet file (not a CSV a generic reader has to
    guess at), genuine multi-line note text (one line per changed field,
    blank-line-separated sections) is fully visible without the viewer
    needing to manually resize anything, unlike a CSV cell's embedded
    newline, which only ever showed its first visual line."""
    wb = openpyxl.Workbook()

    ws_summary = wb.active
    ws_summary.title = "Record Summary"
    ws_summary.append(SUMMARY_COLUMNS)
    for row in summary_rows:
        ws_summary.append([row.get(col, "") for col in SUMMARY_COLUMNS])
    ws_summary.freeze_panes = "A2"

    notes_col_idx = SUMMARY_COLUMNS.index("notes") + 1
    notes_col_letter = openpyxl.utils.get_column_letter(notes_col_idx)
    ws_summary.column_dimensions[notes_col_letter].width = 80
    wrap_alignment = Alignment(wrap_text=True, vertical="top")
    for row_idx in range(2, ws_summary.max_row + 1):
        cell = ws_summary.cell(row=row_idx, column=notes_col_idx)
        cell.alignment = wrap_alignment
        line_count = str(cell.value or "").count("\n") + 1
        ws_summary.row_dimensions[row_idx].height = max(15, min(15 * line_count, 300))

    ws_other = wb.create_sheet("Other Locations")
    ws_other.append(OTHER_LOCATIONS_COLUMNS)
    for row in other_location_rows:
        ws_other.append([row.get(col, "") for col in OTHER_LOCATIONS_COLUMNS])
    ws_other.freeze_panes = "A2"

    ws_created = wb.create_sheet("New Records Added")
    ws_created.append(CREATED_LOCATIONS_COLUMNS)
    for row in (created_location_rows or []):
        ws_created.append([row.get(col, "") for col in CREATED_LOCATIONS_COLUMNS])
    ws_created.freeze_panes = "A2"

    wb.save(path)


def verify(record_id, xlsx_path, check_other_locations=True, debug=False):
    record = load_record(xlsx_path, record_id)
    website = _normalize_url(record["website"])
    print(f"Record {record_id}: {record['name']}")
    print(f"Website on file: {website}\n")
    if not website:
        print("No website on file for this record -- nothing to verify against.")
        return {"record_id": record_id, "field_changes": [], "other_locations": [], "new_location_candidates": [],
                "needs_review": [], "needs_review_details": {}, "name_changed": False, "name_old": None, "name_new": None,
                "website_invalid": True}

    home_text, resolved_website = _fetch(website)
    pages = {website: home_text}
    # anchor same-domain link-finding on where the page actually ended up
    # (resolved_website), not the possibly-stale `website` on file -- see
    # _fetch's docstring
    if home_text:
        for link in find_contact_links(home_text, resolved_website or website):
            if link not in pages:
                pages[link], _ = _fetch(link)

    # a redirect to a different DOMAIN (not just http->https or a www./
    # trailing-slash difference) means the url on file is stale even
    # though it still technically "works" -- worth its own suggestion
    # rather than only fixing the link-finding logic silently (real case:
    # record 39's pathway.org redirects to pathwaystx.org)
    redirected_to_new_domain = None
    if resolved_website:
        old_host = urlparse(website).netloc.lower().removeprefix("www.")
        new_host = urlparse(resolved_website).netloc.lower().removeprefix("www.")
        if new_host and new_host != old_host:
            redirected_to_new_domain = resolved_website
            print(f"NOTE: this URL redirects to a different domain -- {redirected_to_new_domain}\n")

    # A fetch can "succeed" (HTTP 200, or a browser render that never
    # surfaces status at all) while the page itself is a soft-404 or a bot-
    # challenge interstitial instead of the real site (a real case: the URL
    # on file 404'd, and the JS-render fallback below then rendered
    # Cloudflare's "Attention Required!" page and read THAT as the org's
    # content). Double-check anything that looks broken with one browser
    # render (a plain GET can be blocked while the real page still renders
    # fine) before giving up on that URL entirely.
    for url, html_text in list(pages.items()):
        if html_text and not is_error_page(html_text):
            continue
        rendered = _fetch_browser(url)
        pages[url] = rendered if (rendered and not is_error_page(rendered)) else None

    if not pages[website]:
        print(f"  {website} looks broken -- fetch failed, or the page itself says \"not found\" / "
              "shows an error, even after a browser render. The record's website field likely "
              "needs updating; skipping the rest of this check since nothing reliable can be "
              "verified against a page that isn't really there.")
        return {"record_id": record_id, "field_changes": [], "other_locations": [], "new_location_candidates": [],
                "needs_review": [], "needs_review_details": {}, "name_changed": False, "name_old": None, "name_new": None,
                "website_invalid": True}

    if debug:
        # only reached once the site's been confirmed reachable and not an
        # error/challenge page (the broken-site case returns above) --
        # "MATCH" here means "same domain as what's on file", not that the
        # page's content was independently verified in any deeper sense.
        print("\n--- Website ---")
        print(f"  On file: {website}")
        if redirected_to_new_domain:
            print(f"  Resolved to: {redirected_to_new_domain}")
            print("  ==> MISMATCH: the URL on file redirects to a different domain")
        else:
            print(f"  Resolved to: {resolved_website or website}")
            print("  ==> MATCH (same domain)")

    for url, html_text in pages.items():
        if not html_text:
            print(f"  (could not fetch {url})")

    # A contact page for an org with multiple offices commonly puts its
    # headquarters/main info first, then an "Office Locations"/"Our
    # Locations" heading followed by every OTHER office -- without
    # separating these, phone/fax/address extraction pools numbers and
    # addresses from every branch together with the main one, so a
    # multi-location org's main contact info gets buried in noise (a real
    # case: 9 phone numbers reported as candidates for "the" phone number,
    # one per office, instead of just the headquarters' own). Split each
    # page at that heading; only the PRIMARY half feeds the main
    # phone/fax/address comparison below, and the OTHER half becomes
    # same-page "other locations" (merged into that report section later).
    primary_pages = {}
    other_html_by_url = {}
    for url, html_text in pages.items():
        if not html_text:
            continue
        primary_html, other_html = split_html_primary_other(html_text)
        primary_pages[url] = primary_html
        if other_html:
            other_html_by_url[url] = other_html

    href_labels, text_labels, all_mailto, all_social, site_names, text_blobs = _extract_page_signals(primary_pages)

    # some sites lazy-load their contact info via JS (a real one only
    # showed its email after full rendering, even on the dedicated contact
    # page) -- a plain GET never sees that. If email or ALL phone/fax
    # signals came up empty, retry every page with a headless-browser
    # render and merge in whatever that finds, rather than reporting a gap
    # that's really just a plain-fetch blind spot.
    if not all_mailto or not (href_labels or text_labels):
        if debug:
            print("  (no email and/or phone found via a plain fetch -- retrying with a headless "
                  "browser render, since some sites load contact info via JavaScript)")
        browser_pages = {}
        for url in pages:
            rendered = _fetch_browser(url)
            if rendered:
                primary_rendered, other_rendered = split_html_primary_other(rendered)
                browser_pages[url] = primary_rendered
                if other_rendered and url not in other_html_by_url:
                    other_html_by_url[url] = other_rendered
        b_href, b_text, b_mailto, b_social, b_names, b_blobs = _extract_page_signals(browser_pages)
        for number, label in b_href.items():
            if number not in href_labels or href_labels[number] == "unknown":
                href_labels[number] = label
        for number, label in b_text.items():
            text_labels.setdefault(number, label)
        all_mailto += b_mailto
        for platform, links in b_social.items():
            all_social.setdefault(platform, []).extend(links)
        for key, value in b_names.items():
            site_names.setdefault(key, value)
        text_blobs += b_blobs

    # explicit "Phone:"/"Fax:" text labels are authoritative and win over an
    # icon-class guess from a tel: href when the two disagree
    tel_labels = {**href_labels, **text_labels}
    # a tel: href with no icon-class signal and no text label anywhere is
    # still almost always the main phone line (fax numbers as click-to-call
    # links are rare) -- default it to phone rather than leaving it unknown
    for number, label in tel_labels.items():
        if label == "unknown":
            tel_labels[number] = "phone"

    all_mailto = sorted(set(all_mailto))
    for k in all_social:
        all_social[k] = sorted(set(all_social[k]))
    full_text = "\n".join(text_blobs)
    if not tel_labels:
        for number in extract_phone_candidates(full_text):
            tel_labels[number] = "unknown"

    phone_nums = sorted(n for n, lbl in tel_labels.items() if lbl == "phone")
    fax_nums = sorted(n for n, lbl in tel_labels.items() if lbl == "fax")
    unknown_nums = sorted(n for n, lbl in tel_labels.items() if lbl == "unknown")
    # if labeling found nothing usable, fall back to treating everything as
    # an undifferentiated phone candidate (old behavior) rather than losing
    # the signal entirely
    if not phone_nums and not fax_nums:
        phone_nums = unknown_nums

    record_zip = "" if pd.isna(record["zip"]) else str(record["zip"]).split(".")[0][:5]
    record_city = "" if pd.isna(record["city"]) else str(record["city"]).strip().upper()

    seen_addrs = set()
    all_addrs = []
    for p in extract_addresses(full_text):
        key = (p["street"].lower(), p["city"].lower(), p["state"].lower(), p["zip"])
        if key not in seen_addrs:
            seen_addrs.add(key)
            all_addrs.append(p)
    all_addrs = _dedupe_subsumed_addresses(all_addrs)

    parsed_addrs = [
        a for a in all_addrs
        if (record_zip and a["zip"][:5] == record_zip) or (record_city and a["city"].upper() == record_city)
    ]
    if not parsed_addrs:
        # before concluding the record's address isn't on the page at all,
        # try the looser no-zip confirmation -- some pages just never print
        # a zip code next to the address (see confirm_address_without_zip)
        no_zip_hit = confirm_address_without_zip(full_text, record)
        if no_zip_hit:
            parsed_addrs = [{
                "street": record["street"], "city": record["city"],
                "state": record["state"], "zip": str(record["zip"]),
            }]
    # the record's own zip/city isn't anywhere on the site at all, but the
    # site DOES have some other clearly-parsed address on it -- most likely
    # the org relocated. Worth surfacing as a candidate rather than just
    # reporting "not found", even though it doesn't match what's on file by
    # definition (that's the whole point: it's a possible NEW address)
    relocation_candidates = [] if parsed_addrs else all_addrs

    # More than one candidate address is ambiguous -- there's no way to
    # tell which one (if any) is really THIS record's, so guessing at a
    # single "MISMATCH"/"POSSIBLE RELOCATION" would likely be wrong (a
    # real case: a multi-location org's other branches, sharing the same
    # city, all got flagged as address mismatches for one specific
    # record). Route them to the "Other locations" section instead, as
    # candidate additional locations, and treat the address as
    # unconfirmed here rather than wrongly "decided". A single candidate
    # either way is unambiguous and keeps the normal MISMATCH/relocation
    # handling below.
    address_other_locations = []
    if len(parsed_addrs) > 1:
        address_other_locations = parsed_addrs
        parsed_addrs = []
    elif len(relocation_candidates) > 1:
        address_other_locations = relocation_candidates
        relocation_candidates = []
    if address_other_locations:
        address_other_locations = [
            {"name": None, "street": a["street"], "city": a["city"], "state": a["state"], "zip": a["zip"],
             "phone": None, "fax": None, "url": resolved_website or website}
            for a in address_other_locations
        ]

    # Quiet mode: only ever print a field's section when there's something
    # actionable about it -- either a MISMATCH (on file disagrees with what
    # the site says) or something the site has that's missing on file
    # entirely. A match, or a field the site simply doesn't confirm, is
    # deliberately not shown -- the user asked to cut that noise out.
    # a mismatch is only flagged when there's exactly ONE candidate number
    # on the website -- if the site lists several (a real case: a
    # multi-location org's contact/office-locations content lists one
    # phone per branch), there's no way to know which one is supposed to
    # be THIS record's, so guessing any single one as "the" mismatch would
    # likely be wrong. Ambiguous cases are silently skipped rather than
    # flagged, same as the existing address-ambiguity handling.
    on_file_phone_digits = normalize_phone_digits(record["phone"])
    site_phone_digits = [normalize_phone_digits(p) for p in phone_nums]
    if len(phone_nums) == 1 and on_file_phone_digits not in site_phone_digits:
        print("\n--- Phone ---")
        print(f"  On file: {record['phone']}")
        print(f"  On website: {phone_nums}")
        if on_file_phone_digits is None:
            print(f"  ==> NEW: website lists {phone_nums}, nothing on file")
        else:
            print(f"  ==> MISMATCH: website lists {phone_nums}, spreadsheet has {record['phone']}")
        if unknown_nums:
            print(f"  Also found (couldn't tell phone vs. fax from page context): {unknown_nums}")
    elif debug:
        # --debug wants the full picture written to the report file even
        # when there's nothing actionable -- a MATCH, an ambiguous set of
        # candidates, or nothing found at all on the site.
        print("\n--- Phone ---")
        print(f"  On file: {record['phone']}")
        print(f"  On website: {phone_nums}")
        if len(phone_nums) == 1:
            print("  ==> MATCH")
        elif len(phone_nums) > 1:
            print(f"  ==> AMBIGUOUS: {len(phone_nums)} candidate phone numbers found on the website, "
                  "could not determine which is correct")
        else:
            print("  ==> NOT FOUND on website")
        if unknown_nums:
            print(f"  Also found (couldn't tell phone vs. fax from page context): {unknown_nums}")

    on_file_fax_digits = normalize_phone_digits(record["fax"])
    site_fax_digits = [normalize_phone_digits(p) for p in fax_nums]
    if len(fax_nums) == 1 and on_file_fax_digits not in site_fax_digits:
        print("\n--- Fax ---")
        print(f"  On file: {record['fax']}")
        print(f"  On website: {fax_nums}")
        if on_file_fax_digits is None:
            print(f"  ==> NEW: website lists {fax_nums}, nothing on file")
        else:
            print(f"  ==> MISMATCH: website lists {fax_nums}, spreadsheet has {record['fax']}")
    elif debug:
        print("\n--- Fax ---")
        print(f"  On file: {record['fax']}")
        print(f"  On website: {fax_nums}")
        if len(fax_nums) == 1:
            print("  ==> MATCH")
        elif len(fax_nums) > 1:
            print(f"  ==> AMBIGUOUS: {len(fax_nums)} candidate fax numbers found on the website, "
                  "could not determine which is correct")
        else:
            print("  ==> NOT FOUND on website")

    # at most one entry each by construction now (ambiguous multi-address
    # cases were already routed to address_other_locations above)
    mismatched_addrs = [a for a in parsed_addrs if not streets_match(a["street"], record["street"])]
    if mismatched_addrs or relocation_candidates:
        print("\n--- Address ---")
        print(f"  On file: {record['street']}, {record['city']}, {record['state']} {record['zip']}")
        if mismatched_addrs:
            for addr in mismatched_addrs:
                print(f"  ==> MISMATCH: {addr['street']}, {addr['city']}, {addr['state']} {addr['zip']}")
        elif relocation_candidates:
            print("  The record's own zip/city isn't anywhere on the site, but the site does list "
                  "another address -- possible relocation, not just a field-level edit. Confirm "
                  "before updating (city/state/zip would ALL need to change):")
            for addr in relocation_candidates:
                print(f"  ==> {addr['street']}, {addr['city']}, {addr['state']} {addr['zip']}")
    elif debug:
        # --debug wants the full picture written to the report file even
        # when there's nothing actionable -- a MATCH, an ambiguous set of
        # candidates (already routed to address_other_locations above, so
        # neither mismatched_addrs nor relocation_candidates fired), or
        # nothing found at all on the site.
        print("\n--- Address ---")
        print(f"  On file: {record['street']}, {record['city']}, {record['state']} {record['zip']}")
        if len(parsed_addrs) == 1:
            print("  ==> MATCH")
        elif address_other_locations:
            print(f"  ==> AMBIGUOUS: {len(address_other_locations)} candidate addresses found on the "
                  "website, could not determine which is correct (see \"Other locations\" section)")
        else:
            print("  ==> NOT FOUND on website")

    own_street_num = street_number(record["street"])

    def _not_own_location(loc):
        return not (loc["street"] and own_street_num and street_number(loc["street"]) == own_street_num)

    # same-page branches (an "Office Locations" list on the SAME contact
    # page) cost nothing extra to check -- always look, regardless of
    # --skip-new-locations, unlike the separate-index-page crawl below
    same_page_locations = []
    for url, other_html in other_html_by_url.items():
        for name, block_text in split_into_branch_blocks(strip_tags(other_html)):
            info = extract_branch_info(name, block_text, url)
            if info and _not_own_location(info):
                same_page_locations.append(info)

    other_locations = list(same_page_locations) + [loc for loc in address_other_locations if _not_own_location(loc)]
    skip_hint = None
    if check_other_locations:
        for loc in find_other_locations(resolved_website or website, pages.get(website) or "", record):
            if _not_own_location(loc):
                other_locations.append(loc)
    elif not other_locations:
        skip_hint = ("  (--skip-new-locations was passed, so the site's locations index, if it has "
                      "one, was not crawled)")

    # dedup on name+address, NOT address alone -- multiple genuinely
    # distinct services/departments can legitimately share one building
    # (a real case: Metrocare runs several differently-named clinics and
    # pharmacies out of the same "3230 Remond Dr" address), and keying on
    # address alone would wrongly collapse those into a single entry
    seen_locs = set()
    deduped_locations = []
    for loc in other_locations:
        key = ((loc["name"] or "").lower(), (loc["street"] or "").lower(),
               (loc["city"] or "").lower(), (loc["zip"] or ""))
        if key not in seen_locs:
            seen_locs.add(key)
            deduped_locations.append(loc)

    # This project only tracks Texas facilities -- an out-of-state branch
    # (or one whose state couldn't be determined at all) isn't a candidate
    # for a new REDCap record here, so it's dropped before it ever reaches
    # the Other Locations spreadsheet rather than left for a human to
    # notice and discard later.
    deduped_locations = [loc for loc in deduped_locations if str(loc.get("state") or "").strip().upper() == "TX"]

    # Per the user's request: a location we're sure enough about --an
    # identifiable name, a street NUMBER, and its own website-- becomes a
    # brand new REDCap record instead of just a row on the Other Locations
    # tab for a human to type in by hand. Names get disambiguated first
    # (see _assign_effective_location_names) since a site often reuses one
    # org-wide name across every branch it lists. Anything not eligible,
    # OR that looks like it might already be tracked as its own record
    # elsewhere in REDCap (matched by street number + name similarity --
    # this project's whole purpose is de-duplication), still falls back to
    # the Other Locations tab exactly like before, now with a reason
    # column explaining why it landed there instead of being created.
    deduped_locations = _assign_effective_location_names(deduped_locations, _on_file_display(record.get("name")) or None)
    deduped_locations = _drop_same_name_street_collisions(deduped_locations)
    new_location_candidates, other_locations_for_tab = [], []
    for loc in deduped_locations:
        if not _location_creation_eligible(loc):
            other_locations_for_tab.append({**loc, "reason": _location_ineligible_reason(loc)})
            continue
        dup = _find_existing_duplicate_record(loc, xlsx_path, record_id)
        if dup:
            other_locations_for_tab.append(
                {**loc, "reason": f"Failed to add new record: possible duplicate of existing REDCap record "
                                   f"{dup[0]} ({dup[1]})"})
            continue
        new_location_candidates.append(loc)

    # like every other section, only shown unconditionally when there's
    # something real to report -- the "nothing found"/"pass --locations"
    # hints are diagnostic, not actionable, so they're --debug-only
    total_found = len(new_location_candidates) + len(other_locations_for_tab)
    if total_found or debug:
        print("\n--- Other locations on this site ---")
        if new_location_candidates:
            print(f"  {len(new_location_candidates)} other location(s) have enough info (name, street number, "
                  f"website) to become their own new REDCap record:")
            for loc in new_location_candidates:
                addr = f"{loc['street']}, {loc['city']}, {loc['state']} {loc['zip']}"
                print(f"    {loc['effective_name']} -- {addr} -- phone {loc['phone'] or '?'} -- {loc['url']}")
        if other_locations_for_tab:
            print(f"  {len(other_locations_for_tab)} other location(s) going to the Other Locations tab instead:")
            for loc in other_locations_for_tab:
                addr = f"{loc['street']}, {loc['city']}, {loc['state']} {loc['zip']}" if loc["street"] else "(no address found)"
                print(f"    {loc['name'] or '(name unknown)'} -- {addr} -- phone {loc['phone'] or '?'} -- "
                      f"{loc['url']} -- {loc['reason']}")
        if not total_found:
            if skip_hint:
                print(skip_hint)
            elif check_other_locations:
                print("  (no separate \"locations\" index found on this site, or nothing new besides this record)")

    best_name_match = None
    if site_names:
        for key, value in site_names.items():
            score = name_similarity(record["name"], value)
            if best_name_match is None or score > best_name_match[1]:
                best_name_match = (value, score)
        if best_name_match and best_name_match[1] < 0.5:
            print("\n--- Facility name ---")
            print(f"  On file: {record['name']}")
            for key, value in site_names.items():
                score = name_similarity(record["name"], value)
                print(f"  On website ({key}): {value!r}  [similarity {score:.2f}]")
            print(f"  ==> POSSIBLE NAME CHANGE: record says {record['name']!r}, "
                  f"site currently displays {best_name_match[0]!r}")
        elif debug:
            # --debug wants the full picture in the report file even when
            # there's nothing actionable -- a MATCH, same as the other
            # fields' debug-only branches.
            print("\n--- Facility name ---")
            print(f"  On file: {record['name']}")
            for key, value in site_names.items():
                score = name_similarity(record["name"], value)
                print(f"  On website ({key}): {value!r}  [similarity {score:.2f}]")
            print("  ==> MATCH")
    elif debug:
        print("\n--- Facility name ---")
        print(f"  On file: {record['name']}")
        print("  ==> NOT FOUND (no <title>/og:site_name signal found on the site)")

    # a mismatch is only flagged when there's exactly ONE candidate email on
    # the site -- if several are found and NONE match what's on file, there's
    # no way to know which one (if any) is really supposed to replace it, so
    # that's left alone rather than guessed at (same rule already applied to
    # phone/fax). Multiple NEW emails (nothing on file to begin with, so
    # nothing to guess between) are still all shown -- there's no ambiguity
    # to avoid there, just new information.
    on_file_email = None if pd.isna(record["email"]) else str(record["email"]).strip().lower()
    found_emails_lower = [e.lower() for e in all_mailto]
    show_email = bool(all_mailto) and on_file_email not in found_emails_lower \
        and (on_file_email is None or len(all_mailto) == 1)
    if show_email:
        print("\n--- Email ---")
        print(f"  On file: {record['email']}")
        print(f"  On website: {all_mailto}")
        if on_file_email is None:
            print(f"  ==> NEW: website lists {all_mailto}, nothing on file")
        else:
            print(f"  ==> MISMATCH: website lists {all_mailto}, spreadsheet has {record['email']}")
    elif debug:
        print("\n--- Email ---")
        print(f"  On file: {record['email']}")
        print(f"  On website: {all_mailto}")
        if not all_mailto:
            print("  ==> NOT FOUND on website")
        elif on_file_email in found_emails_lower:
            print("  ==> MATCH")
        else:
            print(f"  ==> AMBIGUOUS: {len(all_mailto)} candidate emails found on the website, "
                  "could not determine which is correct")

    social_lines = []
    social_debug_lines = []
    for platform in ("facebook", "instagram", "twitter", "linkedin"):
        on_file = record.get(platform, None) if platform != "linkedin" else None
        found = all_social.get(platform, [])
        has_on_file = isinstance(on_file, str) and on_file.strip()
        if not found:
            if debug:
                on_file_note = f" (on file: {on_file!r})" if has_on_file else ""
                social_debug_lines.append(f"  {platform.capitalize()}: ==> NOT FOUND on website{on_file_note}")
            continue
        if not has_on_file:
            social_lines.append(f"  {platform.capitalize()}: ==> NEW: nothing on file, website has {found}")
        elif not social_links_match(on_file, found):
            social_lines.append(
                f"  {platform.capitalize()}: ==> MISMATCH: website has {found}, spreadsheet has {on_file!r}")
        elif debug:
            social_debug_lines.append(f"  {platform.capitalize()}: ==> MATCH ({found})")
    if social_lines or social_debug_lines:
        print("\n--- Social media ---")
        for line in social_lines:
            print(line)
        for line in social_debug_lines:
            print(line)

    # field_changes mirrors `suggestions` but as structured {field, label,
    # old, new} entries for the REDCap field variable names in
    # REDCAP_FIELD_MAP -- this is what --apply actually writes. Only ever
    # populated for the SAME unambiguous, single-candidate cases suggestions
    # already covers; anything ambiguous (multiple candidates) intentionally
    # has no entry here either, same reasoning as the display logic above.
    # The facility name is deliberately never in here even when flagged --
    # see REDCAP_FIELD_MAP's docstring.
    def _str_or_none(v):
        return None if pd.isna(v) else str(v)

    suggestions = []
    field_changes = []
    if redirected_to_new_domain:
        suggestions.append(
            f"organization website: {website!r} -> {redirected_to_new_domain!r} -- the URL on file "
            "redirects here (different domain), so the old one is stale even though it still resolves"
        )
        field_changes.append({"field": REDCAP_FIELD_MAP["website"], "key": "website", "label": "organization website",
                               "old": website, "new": redirected_to_new_domain})
    # multiple candidate numbers on the site is ambiguous (which office's
    # number is "the" one for this record?) -- only ever suggest a change
    # when exactly one candidate was found, same rule as the display above
    if len(phone_nums) == 1 and on_file_phone_digits not in site_phone_digits:
        new_phone = format_phone_for_redcap(phone_nums[0])
        suggestions.append(f"organization phone number: {record['phone']!r} -> {new_phone!r}")
        field_changes.append({"field": REDCAP_FIELD_MAP["phone"], "key": "phone", "label": "organization phone number",
                               "old": _str_or_none(record["phone"]), "new": new_phone})
    if len(fax_nums) == 1 and on_file_fax_digits not in site_fax_digits:
        new_fax = format_phone_for_redcap(fax_nums[0])
        suggestions.append(f"organization fax number: {record['fax']!r} -> {new_fax!r}")
        field_changes.append({"field": REDCAP_FIELD_MAP["fax"], "key": "fax", "label": "organization fax number",
                               "old": _str_or_none(record["fax"]), "new": new_fax})
    if show_email:
        suggestions.append(f"organization email address: {record['email']!r} -> {all_mailto[0]!r}")
        field_changes.append({"field": REDCAP_FIELD_MAP["email"], "key": "email", "label": "organization email address",
                               "old": _str_or_none(record["email"]), "new": all_mailto[0]})
    if best_name_match and best_name_match[1] < 0.5:
        suggestions.append(
            f"Organization/Facility name: site now displays {best_name_match[0]!r} -- verify whether "
            "this is an actual legal/DBA name change or just marketing branding before updating"
        )
        # deliberately NO field_changes entry -- a name change always needs
        # a human decision, never an auto-write (see REDCAP_FIELD_MAP)
    if len(parsed_addrs) == 1:
        best = parsed_addrs[0]
        if not streets_match(best["street"], record["street"]):
            suggestions.append(
                f"organization street name: {record['street']!r} -> {best['street']!r}"
            )
            field_changes.append({"field": REDCAP_FIELD_MAP["street"], "key": "street", "label": "organization street name",
                                   "old": _str_or_none(record["street"]), "new": best["street"].upper()})
            # city/state/zip/county weren't touched here (only the street
            # differed) -- shown in the narrative anyway, for the full
            # address picture in one place, per the user's request
            for part in ("city", "state", "zip", "county"):
                field_changes.append({"field": REDCAP_FIELD_MAP[part], "key": part, "label": f"organization {part}",
                                       "old": _str_or_none(record[part]), "new": _str_or_none(record[part]),
                                       "context_only": True})
    elif len(relocation_candidates) == 1:
        new_addr = relocation_candidates[0]
        suggestions.append(
            f"POSSIBLE RELOCATION -- record's city/zip not found anywhere on site, but site lists "
            f"{new_addr['street']}, {new_addr['city']}, {new_addr['state']} {new_addr['zip']}; "
            f"if confirmed, update organization street name/city/state/zip all together "
            f"(currently {record['street']!r}, {record['city']!r}, {record['state']!r}, {record['zip']!r})"
        )
        # a relocation changes street/city/state/zip together -- unlike a
        # single mismatched street (same city/zip, just a street typo/
        # update), this is a bigger claim, so it's still auto-write-eligible
        # only because it's an UNAMBIGUOUS single candidate, same bar as
        # everything else here
        for part in ("street", "city", "state", "zip"):
            field_changes.append({"field": REDCAP_FIELD_MAP[part], "key": part, "label": f"organization {part}",
                                   "old": _str_or_none(record[part]), "new": str(new_addr[part]).upper()})
        # A website never states its own county, so a plain scrape can't
        # tell us whether a relocation also changed it -- but the free,
        # no-key Census geocoder can, given the NEW address. Only when
        # that confidently resolves do we treat it as a real, write-
        # eligible change (or a confirmed-unchanged one); any failure
        # (no match, API down, timeout) falls back to flagging the old
        # county as unverified rather than guessing either way.
        old_county = _str_or_none(record["county"])
        new_county = lookup_county_via_census(new_addr["street"], new_addr["city"], new_addr["state"], new_addr["zip"])
        if new_county and (not old_county or new_county != old_county.strip().upper()):
            field_changes.append({"field": REDCAP_FIELD_MAP["county"], "key": "county", "label": "organization county",
                                   "old": old_county, "new": new_county})
        elif new_county:
            field_changes.append({"field": REDCAP_FIELD_MAP["county"], "key": "county", "label": "organization county",
                                   "old": old_county, "new": old_county, "context_only": True})
        else:
            county_display = old_county
            if county_display and str(new_addr["city"]).strip().lower() != str(record["city"] or "").strip().lower():
                county_display = f"{county_display} (on file for the OLD city -- not verified for the new one)"
            field_changes.append({"field": REDCAP_FIELD_MAP["county"], "key": "county", "label": "organization county",
                                   "old": county_display, "new": county_display, "context_only": True})
    elif address_other_locations:
        suggestions.append(
            f"organization street name: site lists {len(address_other_locations)} possible addresses for this "
            "record (ambiguous, same city/zip) -- see \"Other locations\" section instead of guessing which one"
        )
    for platform in ("facebook", "instagram", "twitter", "linkedin"):
        on_file = record.get(platform) if platform != "linkedin" else None
        found = all_social.get(platform, [])
        if not found:
            continue
        has_value = isinstance(on_file, str) and on_file.strip()
        if not has_value:
            suggestions.append(f"{platform.capitalize()}: (blank) -> {found[0]!r}")
        elif not social_links_match(on_file, found):
            suggestions.append(f"{platform.capitalize()}: {on_file!r} -> {found[0]!r}")
        else:
            continue
        if platform in REDCAP_FIELD_MAP:  # no REDCap field for LinkedIn in this project
            field_changes.append({"field": REDCAP_FIELD_MAP[platform], "key": platform, "label": platform.capitalize(),
                                   "old": on_file if has_value else None, "new": found[0]})
    if suggestions or debug:
        print("\n--- Suggested updates (review before applying) ---")
        if suggestions:
            for s in suggestions:
                print(f"  - {s}")
        else:
            print("  (none -- website data agrees with, or adds nothing beyond, what's on file)")

    # Fields where multiple candidates were found on the site and none of
    # them matched what's on file -- there's no way to tell which (if any)
    # is really this record's, so no field_changes entry was created above
    # (same "don't guess when ambiguous" rule as everywhere else in this
    # function). These are surfaced separately for the summary spreadsheet
    # so a human can pick between the candidates, rather than silently
    # vanishing because nothing got auto-suggested.
    needs_review = []
    needs_review_details = {}
    if len(phone_nums) > 1 and on_file_phone_digits not in site_phone_digits:
        needs_review.append("Phone")
        needs_review_details["Phone"] = [format_phone_for_redcap(p) for p in phone_nums]
    if len(fax_nums) > 1 and on_file_fax_digits not in site_fax_digits:
        needs_review.append("Fax")
        needs_review_details["Fax"] = [format_phone_for_redcap(p) for p in fax_nums]
    if len(all_mailto) > 1 and on_file_email is not None and on_file_email not in found_emails_lower:
        needs_review.append("Email")
        needs_review_details["Email"] = list(all_mailto)
    if address_other_locations:
        needs_review.append("Address")
        needs_review_details["Address"] = [
            f"{a['street']}, {a['city']}, {a['state']} {a['zip']}" for a in address_other_locations
        ]
    name_changed = bool(best_name_match and best_name_match[1] < 0.5)

    return {"record_id": record_id, "field_changes": field_changes, "other_locations": other_locations_for_tab,
            "new_location_candidates": new_location_candidates,
            "needs_review": needs_review, "needs_review_details": needs_review_details, "name_changed": name_changed,
            "name_old": record["name"] if name_changed else None,
            "name_new": best_name_match[0] if name_changed else None,
            "website_invalid": False}


def _expand_record_ids(tokens):
    """Each token is a single id ("400"), a range ("3-10", inclusive both
    ends), or a comma-separated run of either ("3,5,8-10") -- so both
    `automated_comptroller_verifier.py 3 4 5` and
    `automated_comptroller_verifier.py 3-5` (or a mix,
    `automated_comptroller_verifier.py 3-5 8 12-15`) work."""
    ids = []
    for token in tokens:
        for part in str(token).split(","):
            part = part.strip()
            if not part:
                continue
            m = re.match(r"^(\d+)-(\d+)$", part)
            if m:
                start, end = int(m.group(1)), int(m.group(2))
                if start > end:
                    start, end = end, start
                ids.extend(range(start, end + 1))
            else:
                ids.append(int(part))
    return ids


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("record_ids", nargs="+",
                         help="one or more Record IDs, and/or ranges like 3-10 (inclusive). "
                              "Space- or comma-separated, e.g.: 400   3 5 8   3-10   3-5,8,12-15")
    parser.add_argument("--xlsx", required=True,
                         help="path to the REDCap xlsx export (e.g. "
                              "ComptrollerProject20_DATA_LABELS_2026-04-16_1051.xlsx) to read records from. "
                              "Required -- no default, so this always runs against a file you explicitly named.")
    parser.add_argument("--outdir", default="output",
                         help="directory to write <record_id>.txt reports (and, by default, the "
                              "record_summary_redcap_id_<lo>_to_<hi>_TIME_<timestamp>.xlsx workbook) "
                              "into (default: output)")
    parser.add_argument("--skip-new-locations", action="store_true",
                         help="do NOT crawl the site's \"locations\" index (if it has one) for other "
                              "locations besides this record's own. That crawl is now ON by default -- "
                              "pass this flag to skip it, e.g. for a large multi-location provider "
                              "(a hospital system) where it can take a couple of minutes, since every "
                              "location page is fetched individually.")
    parser.add_argument("--debug", action="store_true",
                         help="show diagnostic/no-op messages that are otherwise suppressed: the "
                              "plain-fetch-fallback notice, an empty \"Other locations\"/\"Suggested "
                              "updates\" section, and an ERROR line for a record id missing from the "
                              "spreadsheet. Off by default to keep a --range batch's output focused "
                              "on records that actually have something to report.")
    parser.add_argument("--apply", action="store_true",
                         help="actually write the proposed phone/fax/email/social/address changes to "
                              "REDCap via its API (requires redcap_config.py -- see that file's "
                              "docstring). Off by default: without --apply this ONLY prints what "
                              "would change, exactly like every other mode here, and never touches "
                              "REDCap or the network for writing.")
    parser.add_argument("--output-xlsx", default=None,
                         help="path to the single .xlsx workbook this run writes (default: "
                              "<outdir>/record_summary_redcap_id_<lo>_to_<hi>_TIME_<timestamp>.xlsx, "
                              "named after the id range and the exact time this run happened). "
                              "Contains three tabs: \"Record Summary\" (one row per valid record id, "
                              "see the field-by-field docs), \"Other Locations\" (every additional "
                              "branch/office location an organization's website mentions that was NOT "
                              "auto-created -- missing a name/street number/website, a likely duplicate "
                              "of an existing record, or a failed create attempt -- each row tagged with "
                              "the record_id it came from and never written to REDCap automatically), and "
                              "\"New Records Added\" (one row per brand new REDCap record this run "
                              "actually created for a confidently-identified other location, only under "
                              "--apply).")
    args = parser.parse_args()

    # Printed FIRST, before the dry-run/--apply notices below -- so the
    # person running the tool gets immediate confirmation the script has
    # actually started, rather than waiting on the REDCap project lookup
    # (--apply) or anything else before seeing anything at all.
    record_ids = _expand_record_ids(args.record_ids)
    id_lo, id_hi = min(record_ids), max(record_ids)
    if id_lo == id_hi:
        print(f"Processing record {id_lo} ({len(record_ids)} record(s) total)...\n")
    else:
        print(f"Processing records from {id_lo} to {id_hi} ({len(record_ids)} record(s) total)...\n")

    if not args.apply:
        print("DRY RUN: no changes will be written to REDCap (pass --apply to actually write them).\n")
    else:
        # --apply is a hard-to-reverse, shared-system action -- confirm
        # with the person running the tool, showing exactly which REDCap
        # PROJECT the configured token targets (fetched live, never
        # assumed from the url alone, since one REDCap server can host
        # many projects and a token is scoped to only one of them),
        # before doing anything else. Any "no" answer, or any failure to
        # even reach REDCap to find out, cancels the whole run.
        try:
            config = _load_redcap_config()
            project = redcap_project_info(config)
        except Exception as e:
            auth_msg = _redcap_auth_error_message(e)
            if auth_msg:
                raise SystemExit(f"\n{auth_msg}\n")
            raise SystemExit(f"Could not confirm which REDCap project --apply would target -- "
                              f"cancelling before making any changes. ({e})")
        print("=" * 72)
        print("--apply is set: this run WILL write changes to REDCap.")
        print(f"  REDCap project : {project.get('project_title', '(unknown)')} "
              f"(Project ID: {project.get('project_id', '(unknown)')})")
        print(f"  API URL        : {config['api_url']}")
        print("=" * 72)
        try:
            answer = input("Proceed with writing changes to this REDCap project? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Cancelled -- no changes were made to REDCap.")
            return

    os.makedirs(args.outdir, exist_ok=True)
    # named after the id range this run was actually invoked for (not just
    # the ones that turned out to be present in the spreadsheet), so a
    # workbook from a `3-10` run and one from a `400` run never collide. A
    # dry run (no --apply) additionally gets its own "_dry_run" filename
    # suffix, so it can never be mistaken for -- or silently overwrite --
    # the file from a real --apply run over the same id range. A trailing
    # run timestamp makes every run's output its own distinct file --
    # this is what replaced the old other-locations CSV's "accumulate
    # across runs by appending" behavior with "one clearly dated file per
    # run" instead, now carried over to the single combined workbook.
    id_range_suffix = f"{id_lo}" if id_lo == id_hi else f"{id_lo}_to_{id_hi}"
    dry_run_suffix = "" if args.apply else "_dry_run"
    run_timestamp = datetime.datetime.now().strftime("%Y_%m_%d_%H%M%S")
    run_workbook_path = args.output_xlsx or os.path.join(
        args.outdir, f"record_summary_redcap_id_{id_range_suffix}_TIME_{run_timestamp}{dry_run_suffix}.xlsx")
    all_other_location_rows = []
    all_created_location_rows = []
    summary_rows = []
    saved_report_count = 0

    # Right-justify the id to a fixed 4-character width (this dataset's ids
    # run up to 5 digits) so "Processing Record" lines line up in a column
    # regardless of how many digits any one id has -- a 5+ digit id just
    # widens its own line instead of breaking alignment for the rest.
    id_width = 5
    run_start = time.time()

    for record_id in record_ids:
        record_start = time.time()
        # Cheap existence check first (load_record hits the cached xlsx,
        # no network) -- a missing id gets exactly one console line and
        # nothing else: no report file (even under --debug), no summary
        # row, no per-record work attempted at all.
        try:
            record = load_record(args.xlsx, record_id)
        except ValueError:
            elapsed = time.time() - record_start
            print(f"Processing Record {record_id:>{id_width}}: SKIPPED => ID NOT FOUND ({elapsed:.1f}s)")
            continue

        # Console only ever gets the one line above. Everything verify()
        # and the apply/dry-run preview below print is the FULL detailed
        # report -- that only goes to the per-record file, and only when
        # --debug is on; otherwise it's captured and discarded, never
        # shown on the real console and never written anywhere.
        out_path = os.path.join(args.outdir, f"{record_id}.txt")
        sink = open(out_path, "w", encoding="utf-8") if args.debug else io.StringIO()
        applied_to_redcap = False
        redcap_apply_error = None
        redcap_skipped = []
        created_locations = []
        location_creation_errors = []
        token_auth_failure = None
        try:
            with contextlib.redirect_stdout(sink):
                try:
                    result = verify(record_id, args.xlsx, not args.skip_new_locations, args.debug)
                except Exception as e:
                    # one bad record (any failure besides a missing id,
                    # already handled above) must not take down the rest
                    # of a --range batch -- report it and move on
                    print(f"  ERROR: {e} -- skipping record {record_id}, continuing with the rest")
                    result = None

                if result:
                    for loc in result["other_locations"]:
                        all_other_location_rows.append({"record_id": record_id, **loc})

                    new_location_candidates = result.get("new_location_candidates", [])
                    if new_location_candidates and not args.apply:
                        print("\n--- New REDCap record(s) that would be created for other locations "
                              "(dry-run -- pass --apply to create them) ---")
                        for loc in new_location_candidates:
                            addr = f"{loc['street']}, {loc['city']}, {loc['state']} {loc['zip']}"
                            print(f"  {loc['effective_name']} -- {addr} -- phone {loc['phone'] or '?'} -- {loc['url']}")
                    elif new_location_candidates:
                        print("\n--- Creating new REDCap record(s) for other locations ---")
                        for loc in new_location_candidates:
                            # Re-checked against LIVE REDCap right before writing,
                            # on the same name (after cleanup/"- City" disambiguation)
                            # and address the record is actually about to be created
                            # with -- the xlsx-based check in verify() only sees a
                            # possibly-stale snapshot, and has no way to know about a
                            # record THIS run already created a moment ago for a
                            # different parent. Never guessed: if the live check
                            # itself can't be confirmed, this location is NOT created
                            # -- it falls back to Other Locations instead of risking
                            # a duplicate.
                            try:
                                live_dup = find_live_redcap_duplicate(loc, record_id)
                            except Exception as e:
                                print(f"  FAILED to confirm {loc['effective_name']} isn't already in "
                                      f"REDCap -- {e}")
                                all_other_location_rows.append({
                                    "record_id": record_id, **loc,
                                    "reason": f"Failed to add new record: could not confirm this "
                                              f"location isn't already in REDCap -- {e}",
                                })
                                continue
                            if live_dup:
                                dup_id, dup_name = live_dup
                                print(f"  SKIPPED creating record for {loc['effective_name']} -- already "
                                      f"exists as REDCap record {dup_id} ({dup_name})")
                                all_other_location_rows.append({
                                    "record_id": record_id, **loc,
                                    "reason": f"Failed to add new record: record already exists in REDCap "
                                              f"as record {dup_id} ({dup_name})",
                                })
                                continue
                            try:
                                new_id, county = create_redcap_location_record(loc, record_id)
                            except Exception as e:
                                # never silently dropped -- falls back to the
                                # Other Locations tab, same as any other
                                # ineligible location, with the failure as
                                # its reason
                                location_creation_errors.append({**loc, "error": str(e)})
                                print(f"  FAILED to create record for {loc['effective_name']} -- {e}")
                                all_other_location_rows.append({
                                    "record_id": record_id, **loc,
                                    "reason": f"Failed to add new record: {e}",
                                })
                            else:
                                _remember_created_location_for_dedup(loc, new_id)
                                created_locations.append({**loc, "new_record_id": new_id, "county": county})
                                all_created_location_rows.append({
                                    "record_id": record_id, "new_record_id": new_id,
                                    "name": loc["effective_name"], "street": loc.get("street"),
                                    "city": loc.get("city"), "state": loc.get("state"), "zip": loc.get("zip"),
                                    "county": county, "phone": loc.get("phone"), "fax": loc.get("fax"),
                                    "url": loc.get("url"),
                                })
                                print(f"  Created record {new_id}: {loc['effective_name']} -- {loc['url']}")

                    field_changes = result["field_changes"]
                    needs_review = result["needs_review"]
                    needs_review_details = result["needs_review_details"]
                    if needs_review:
                        # Ambiguity found SOMEWHERE in this record (multiple
                        # candidates for at least one field) -- per the
                        # user's explicit request, this blocks EVERY field
                        # from being auto-written to REDCap for this record,
                        # not just the ambiguous one(s): even an otherwise-
                        # unambiguous email/social change sits alongside it
                        # unapplied, listed only as a suggestion. Nothing is
                        # written to REDCap at all here, under --apply or
                        # not -- a human reviews the comment and decides.
                        print("\n--- Manual Review Required (NOT written to REDCap) ---")
                        lines, saw_real_change = _narrative_lines(field_changes)
                        if lines and saw_real_change:
                            print("  Potential changes:")
                            for line in lines:
                                print(f"    {line}")
                        for label, candidates in needs_review_details.items():
                            print(f"  {label}: multiple candidates found, could not determine which is correct -- {candidates}")
                        if result["name_changed"]:
                            print(f"  Facility name: possible name change on the website (never auto-written) -- "
                                  f"{result['name_old']!r} -> {result['name_new']!r}")
                    elif field_changes:
                        if not args.apply:
                            print("\n--- Proposed REDCap update (dry-run -- pass --apply to write these) ---")
                            for c in field_changes:
                                if not c.get("context_only"):
                                    print(f"  {c['field']} ({c['label']}): {c['old']!r} -> {c['new']!r}")
                            preview_note = build_change_narrative(field_changes, needs_review)
                            if preview_note:
                                print(f"  Would also write this note to change_explain + general_comments,")
                                print(f"  set validated=Yes, validation_date={datetime.date.today().isoformat()}, change=Yes:")
                                for line in preview_note.splitlines():
                                    print(f"    {line}")
                        else:
                            try:
                                applied, skipped = apply_field_changes_to_redcap(record_id, field_changes, needs_review)
                            except Exception as e:
                                redcap_apply_error = str(e)
                                print(f"\n--- REDCap update FAILED ---\n  {e}")
                                token_auth_failure = _redcap_auth_error_message(e)
                            else:
                                applied_to_redcap = bool(applied)
                                redcap_skipped = skipped
                                print("\n--- REDCap update applied ---")
                                for c in applied:
                                    if not c.get("context_only"):
                                        print(f"  {c['field']} ({c['label']}): {c['old']!r} -> {c['new']!r}")
                                for c in skipped:
                                    print(f"  SKIPPED {c['field']} ({c['label']}): {c['skip_reason']}")
        finally:
            sink.close()  # for the --debug file this flushes it; for the
            if args.debug:  # StringIO discard case there's nothing to keep
                saved_report_count += 1

        elapsed = time.time() - record_start
        print(f"Processing Record {record_id:>{id_width}}: {record['name']} ({elapsed:.1f}s)")

        if result:
            summary_rows.append(build_summary_row(record_id, result, record, updated_redcap=applied_to_redcap,
                                                    dry_run=not args.apply,
                                                    redcap_error=redcap_apply_error,
                                                    redcap_skipped=redcap_skipped))

        if token_auth_failure:
            # REDCap rejected the token itself, not just this one record's
            # write -- every remaining record would fail the exact same
            # way, so stop here rather than grinding through the rest of
            # the batch. Whatever was already processed (including this
            # record's own summary row above) is still written out below,
            # not discarded.
            print(f"\n{'=' * 72}")
            print(token_auth_failure)
            print("Stopping further processing -- every remaining record would fail the same way.")
            print(f"{'=' * 72}")
            break

    if summary_rows:
        write_run_workbook(run_workbook_path, summary_rows, all_other_location_rows, all_created_location_rows)
        # A short, at-a-glance recap instead of raw row counts per tab --
        # these four numbers are what someone skimming the console after a
        # run actually wants to know. They're independent counts, not a
        # partition of summary_rows (e.g. a dry-run's proposed-but-not-
        # yet-applied change falls into neither "updated" nor "unchanged").
        updated_count = sum(1 for r in summary_rows if r["updated_redcap_record"] == "Yes")
        unchanged_count = sum(1 for r in summary_rows if r["notes"] == "Record up to date: No Change Required")
        manual_review_count = sum(1 for r in summary_rows if r["manual_review_required"] == "Yes")
        new_location_count = len(all_created_location_rows)
        print(f"\n{updated_count} record(s) updated in REDCap, \n{unchanged_count} record(s) unchanged, "
              f"\n{manual_review_count} record(s) need manual review, and \n{new_location_count} new "
              f"location(s) added to REDCap.")
        print(f"Full details written to {run_workbook_path}")

    if args.debug and saved_report_count:
        print(f"Saved {saved_report_count} detailed report(s) to {args.outdir}/<record_id>.txt")

    # One clear, unambiguous listing of exactly where the output workbook
    # landed, printed once at the very end -- easier to spot than the
    # message above, which is interleaved with row counts and caveats.
    if summary_rows and os.path.exists(run_workbook_path):
        print("\n--- Output file ---")
        print(f"  {os.path.abspath(run_workbook_path)}")

    # One-line total for the whole run, printed last so it's the final
    # thing on screen -- separate from each record's own per-record
    # timing, which only covers that one record.
    total_elapsed = time.time() - run_start
    minutes, seconds = divmod(total_elapsed, 60)
    elapsed_str = f"{int(minutes)}m {seconds:.1f}s" if minutes else f"{seconds:.1f}s"
    print(f"\nTotal time to process {len(record_ids)} record(s): {elapsed_str}")

    if _browser is not None:
        _browser.close()


if __name__ == "__main__":
    sys.exit(main())
