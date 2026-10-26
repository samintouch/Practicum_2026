"""Normalizers so values from the website and from REDCap compare fairly."""

import re
from difflib import SequenceMatcher
from urllib.parse import parse_qs, urlparse

STATES = {
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
STATE_CODES = set(STATES.values())
STATE_NAMES = {code: name.title() for name, code in STATES.items()}

TOLL_FREE_PREFIXES = {"800", "833", "844", "855", "866", "877", "888"}

_STREET_WORDS = {
    "STREET": "ST", "AVENUE": "AVE", "AV": "AVE", "BOULEVARD": "BLVD", "DRIVE": "DR",
    "ROAD": "RD", "LANE": "LN", "COURT": "CT", "PARKWAY": "PKWY", "HIGHWAY": "HWY",
    "FREEWAY": "FWY", "EXPRESSWAY": "EXPY", "PLACE": "PL", "CIRCLE": "CIR",
    "TRAIL": "TRL", "SQUARE": "SQ", "TERRACE": "TER", "CENTER": "CTR",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
}
# Everything from a unit designator onward is dropped when comparing streets.
_UNIT_RE = re.compile(r"\b(STE|SUITE|UNIT|APT|BLDG|BUILDING|FLOOR|FL|RM|ROOM|#)\b.*$|#.*$")


def phone_digits(value):
    """10-digit US number, or "" if the value isn't one (or is a template
    placeholder like (555) 555-5555)."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits[3:] == "5555555" or len(set(digits)) == 1 or digits == "1234567890":
        return ""
    return digits


def format_phone(digits):
    """The form the REDCap field note asks for: (123)-456-7891."""
    return f"({digits[:3]})-{digits[3:6]}-{digits[6:]}"


def is_toll_free(digits):
    return digits[:3] in TOLL_FREE_PREFIXES


def state_code(value):
    s = (value or "").strip().upper().rstrip(".")
    return STATES.get(s, s)


def zip5(value):
    m = re.match(r"\s*(\d{5})", value or "")
    return m.group(1) if m else ""


def street_key(street):
    """Upper-case street with standard abbreviations and no unit/suite."""
    s = (street or "").upper().replace(".", " ").replace(",", " ")
    s = _UNIT_RE.sub("", s)
    words = [_STREET_WORDS.get(w, w) for w in re.findall(r"[A-Z0-9]+", s)]
    return " ".join(words)


def house_number(street):
    m = re.match(r"\s*(\d+)", street or "")
    return m.group(1) if m else ""


def same_location(a, b):
    """a, b: dicts with street and zip. Same house number and either the same
    ZIP or the same street words."""
    num_a, num_b = house_number(a["street"]), house_number(b["street"])
    if not num_a or num_a != num_b:
        return False
    if zip5(a["zip"]) and zip5(a["zip"]) == zip5(b["zip"]):
        return True
    key_a, key_b = street_key(a["street"]).split(), street_key(b["street"]).split()
    return key_a[:3] == key_b[:3]


def _expand_acronyms(words, other):
    """Spell out an abbreviation using the other name's words, when its
    letters are the initials of consecutive words there: "jps" next to
    "john peter smith ..." becomes "john peter smith"."""
    out = []
    for w in words:
        expanded = None
        if 2 <= len(w) <= 6 and w.isalpha() and w not in other:
            for i in range(len(other) - len(w) + 1):
                if "".join(x[0] for x in other[i:i + len(w)]) == w:
                    expanded = other[i:i + len(w)]
                    break
        out.extend(expanded or [w])
    return out


def name_similarity(a, b):
    """1.0 when one name contains the other, after ignoring case,
    punctuation ("M.D" = "MD"), company suffixes and abbreviations
    ("JPS" = "John Peter Smith"); otherwise a 0-1 closeness score."""
    def clean(s):
        s = re.sub(r"[.']", "", (s or "").lower().replace("&", " and "))
        s = re.sub(r"[^a-z0-9 ]", " ", s)
        drop = {"the", "inc", "llc", "pllc", "pc", "of", "and", "co", "corp", "ltd"}
        return [w for w in s.split() if w not in drop]
    wa, wb = clean(a), clean(b)
    if not wa or not wb:
        return 0.0
    a, b = " ".join(_expand_acronyms(wa, wb)), " ".join(_expand_acronyms(wb, wa))
    if f" {a} " in f" {b} " or f" {b} " in f" {a} ":
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def email_key(value):
    return (value or "").strip().lower()


def host(url):
    h = (urlparse(url if "//" in (url or "") else f"//{url}").netloc or "").lower()
    return h.split("@")[-1].split(":")[0].removeprefix("www.")


# Path segments that are never a profile on that platform.
_NOT_PROFILE = {
    "facebook": {"sharer", "sharer.php", "share", "share.php", "dialog", "plugins", "tr",
                 "events", "groups", "hashtag", "login", "watch", "photo", "photo.php", "story.php"},
    "instagram": {"p", "reel", "reels", "explore", "stories", "accounts"},
    "twitter": {"intent", "share", "home", "hashtag", "search", "i", "login"},
    "tiktok": set(),
    "youtube": {"watch", "embed", "results", "playlist", "shorts", "redirect"},
    "whatsapp": set(),
}


# Website-builder accounts that site templates link to by default.
PLACEHOLDER_HANDLES = {"wix", "squarespace", "wordpress", "wordpressdotcom", "godaddy", "weebly",
                       "shopify", "elementor", "wpengine", "hubspot", "duda", "yourpage", "username"}


def is_placeholder_social(key):
    return bool(key) and key.split(":", 1)[1] in PLACEHOLDER_HANDLES


def social_platform(url):
    h = host(url)
    if h.endswith("facebook.com") or h == "fb.com":
        return "facebook"
    if h.endswith("instagram.com"):
        return "instagram"
    if h.endswith("twitter.com") or h == "x.com":
        return "twitter"
    if h.endswith("tiktok.com"):
        return "tiktok"
    if h.endswith("youtube.com"):
        return "youtube"
    if h in ("wa.me", "api.whatsapp.com", "whatsapp.com", "web.whatsapp.com"):
        return "whatsapp"
    return None


def social_key(url, platform=None):
    """Comparable identity of a social profile, e.g. "instagram:addisonpain",
    or None when the link isn't a profile (share buttons, posts, embeds)."""
    url = (url or "").strip()
    if not url:
        return None
    if "//" not in url and "." not in url.split("/")[0]:
        # a bare handle typed into REDCap, like "@addisonpain"
        return f"{platform}:{url.lstrip('@').lower()}" if platform else None
    platform = social_platform(url) or platform
    if not platform:
        return None
    parsed = urlparse(url if "//" in url else f"https://{url}")
    parts = [p for p in parsed.path.split("/") if p]
    query = parse_qs(parsed.query)

    if platform == "whatsapp":
        number = parts[0] if host(url) == "wa.me" and parts else (query.get("phone") or [""])[0]
        number = re.sub(r"\D", "", number)
        return f"whatsapp:{number}" if number else None
    if platform == "facebook" and parts and parts[0] == "profile.php":
        pid = (query.get("id") or [""])[0]
        return f"facebook:{pid}" if pid else None
    if platform == "facebook" and parts[:1] == ["pages"] and len(parts) >= 2:
        parts = parts[1:]
    if platform == "youtube" and parts and parts[0] in ("channel", "c", "user") and len(parts) >= 2:
        return f"youtube:{parts[1].lower()}"
    if platform == "tiktok" and (not parts or not parts[0].startswith("@")):
        return None
    if not parts or parts[0].lower() in _NOT_PROFILE[platform]:
        return None
    return f"{platform}:{parts[0].lstrip('@').lower()}"
