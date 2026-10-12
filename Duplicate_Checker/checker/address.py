"""Address normalization and comparison.

The same address gets typed many ways ("6110 W PARKER RD" vs "6110 West Parker
Road", "STE 302" vs "#302"), so addresses are reduced to USPS-style
abbreviations and split into house number, street and unit before comparing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

DIRECTIONS = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
    "N": "N", "S": "S", "E": "E", "W": "W", "NE": "NE", "NW": "NW", "SE": "SE", "SW": "SW",
}

# long/variant spelling -> USPS abbreviation
STREET_WORDS = {
    "ALLEY": "ALY", "AVENUE": "AVE", "AV": "AVE", "AVEN": "AVE", "AVE": "AVE",
    "BOULEVARD": "BLVD", "BOUL": "BLVD", "BLVD": "BLVD", "BYPASS": "BYP",
    "CIRCLE": "CIR", "CIR": "CIR", "COURT": "CT", "CT": "CT", "COVE": "CV",
    "CREEK": "CRK", "CRK": "CRK", "CROSSING": "XING", "DRIVE": "DR", "DRV": "DR", "DR": "DR",
    "EXPRESSWAY": "EXPY", "EXPWY": "EXPY", "EXPRESS": "EXPY", "EXP": "EXPY", "EXPY": "EXPY",
    "FREEWAY": "FWY", "FRWY": "FWY", "FWY": "FWY", "HIGHWAY": "HWY", "HIWAY": "HWY", "HWY": "HWY",
    "INTERSTATE": "I", "IH": "I",
    "LAKES": "LKS", "LANE": "LN", "LN": "LN", "LOOP": "LOOP",
    "PARKWAY": "PKWY", "PKY": "PKWY", "PKWY": "PKWY", "PLACE": "PL", "PL": "PL",
    "PLAZA": "PLZ", "PLZ": "PLZ", "POINT": "PT", "RIDGE": "RDG", "ROAD": "RD", "RD": "RD",
    "ROUTE": "RTE", "RTE": "RTE", "SPRINGS": "SPGS", "SPRING": "SPG", "SQUARE": "SQ",
    "STREET": "ST", "STR": "ST", "ST": "ST", "TERRACE": "TER", "TRACE": "TRCE",
    "TRAIL": "TRL", "TRL": "TRL", "TURNPIKE": "TPKE", "WAY": "WAY",
    "CENTER": "CTR", "CENTRE": "CTR", "MOUNT": "MT", "SAINT": "ST", "FOREST": "FRST",
}

# abbreviations that mark the end of a street name ("PARKER RD")
STREET_TYPES = {
    "ALY", "AVE", "BLVD", "BYP", "CIR", "CT", "CV", "DR", "EXPY", "FWY", "HWY",
    "LN", "LOOP", "PKWY", "PL", "PLZ", "RD", "RTE", "SQ", "ST", "TER", "TPKE",
    "TRCE", "TRL", "WAY", "XING",
}

UNIT_WORDS = {
    "SUITE": "STE", "STE": "STE", "STES": "STE", "UNIT": "STE", "APT": "STE",
    "APARTMENT": "STE", "ROOM": "RM", "ROOMS": "RM", "RM": "RM",
    "BUILDING": "BLDG", "BLDG": "BLDG", "BLD": "BLDG", "FLOOR": "FL", "FL": "FL", "FLR": "FL",
}

ORDINALS = {
    "FIRST": "1ST", "SECOND": "2ND", "THIRD": "3RD", "FOURTH": "4TH", "FIFTH": "5TH",
    "SIXTH": "6TH", "SEVENTH": "7TH", "EIGHTH": "8TH", "NINTH": "9TH", "TENTH": "10TH",
    "ELEVENTH": "11TH", "TWELFTH": "12TH",
}

_UNIT_GLUED = re.compile(r"^(SUITE|STE|UNIT|APT|RM|BLDG|FL)(\d+[A-Z]?)$")
_HOUSE_NUMBER = re.compile(r"^\d+[A-Z]?$")


def tokenize(text: str) -> list[str]:
    """Upper-case, strip punctuation and map every word to its standard abbreviation."""
    text = str(text).upper().replace("#", " STE ").replace("&", " AND ")
    text = re.sub(r"[.,']", "", text)
    text = text.replace("-", "")            # "3C-400" -> "3C400", "I-35" -> "I35"
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    tokens: list[str] = []
    for tok in text.split():
        glued = _UNIT_GLUED.match(tok)
        parts = [glued.group(1), glued.group(2)] if glued else [tok]
        for part in parts:
            part = ORDINALS.get(part, part)
            part = UNIT_WORDS.get(part, DIRECTIONS.get(part, STREET_WORDS.get(part, part)))
            if tokens and part == tokens[-1] and part in ("STE", "RM", "BLDG", "FL"):
                continue                     # "STE #200" -> "STE STE 200"
            tokens.append(part)
    return tokens


@dataclass
class ParsedAddress:
    raw: str
    number: str | None
    street_tokens: list[str]
    units: dict[str, str] = field(default_factory=dict)

    @property
    def street(self) -> str:
        return " ".join(self.street_tokens)

    @property
    def street_core(self) -> str:
        """Street name without leading/trailing direction and the final street type."""
        toks = list(self.street_tokens)
        if len(toks) > 1 and toks[0] in DIRECTIONS.values():
            toks = toks[1:]
        last_type = max((i for i, t in enumerate(toks) if t in STREET_TYPES and i > 0), default=None)
        if last_type is not None:
            toks = toks[:last_type]
        if len(toks) > 1 and toks[-1] in DIRECTIONS.values():
            toks = toks[:-1]
        return " ".join(toks)

    @property
    def has_street_type(self) -> bool:
        return any(t in STREET_TYPES for t in self.street_tokens)

    @property
    def normalized(self) -> str:
        parts = [self.number or "", self.street]
        parts += [f"{k} {v}" for k, v in self.units.items()]
        return " ".join(p for p in parts if p).strip()


def parse_address(text: str | None) -> ParsedAddress | None:
    if not text or not str(text).strip():
        return None
    toks = tokenize(text)
    if not toks:
        return None
    number = toks[0] if _HOUSE_NUMBER.match(toks[0]) else None
    rest = toks[1:] if number else toks

    street: list[str] = []
    units: dict[str, str] = {}
    i = 0
    while i < len(rest):
        tok = rest[i]
        # "1ST FL" - value written before the unit word
        if i + 1 < len(rest) and rest[i + 1] == "FL" and re.match(r"^\d+(ST|ND|RD|TH)?$", tok):
            units.setdefault("FL", re.sub(r"\D", "", tok))
            i += 2
            continue
        if tok in ("STE", "RM", "BLDG", "FL"):
            value = rest[i + 1] if i + 1 < len(rest) else ""
            if tok == "FL":
                value = re.sub(r"(ST|ND|RD|TH)$", "", value)
            units.setdefault(tok, value)
            i += 2
            continue
        if not units:                        # anything after the unit is usually the city
            street.append(tok)
        i += 1
    return ParsedAddress(raw=str(text), number=number, street_tokens=street, units=units)


@dataclass
class AddressComparison:
    level: str          # SAME, SAME_BUILDING, SAME_STREET, DIFFERENT, UNKNOWN
    street_similarity: float
    detail: str


def street_similarity(a: ParsedAddress, b: ParsedAddress) -> float:
    ca, cb = a.street_core, b.street_core
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    score = SequenceMatcher(None, ca, cb).ratio()
    # one street name contains the other ("WHEATLAND" vs "WHEATLAND RD")
    ta, tb = set(ca.split()), set(cb.split())
    shorter = ta if len(ta) <= len(tb) else tb
    if (ta <= tb or tb <= ta) and any(len(t) >= 3 for t in shorter):
        score = max(score, 0.9)
    return score


def compare_addresses(a: ParsedAddress | None, b: ParsedAddress | None) -> AddressComparison:
    if a is None or b is None:
        return AddressComparison("UNKNOWN", 0.0, "address missing on one record")
    sim = street_similarity(a, b)
    if not a.number or not b.number:
        return AddressComparison("UNKNOWN", sim, "house number missing on one record")
    if a.number != b.number:
        if sim >= 0.85:
            return AddressComparison("SAME_STREET", sim,
                                     f"same street, different building number ({a.number} vs {b.number})")
        return AddressComparison("DIFFERENT", sim, "different street address")
    if sim < 0.85:
        return AddressComparison("DIFFERENT", sim,
                                 f"same building number but different street ({a.street} vs {b.street})")
    ste_a, ste_b = a.units.get("STE"), b.units.get("STE")
    if ste_a and ste_b and ste_a != ste_b:
        return AddressComparison("SAME_BUILDING", sim, f"same building, different suite ({ste_a} vs {ste_b})")
    return AddressComparison("SAME", sim, "same address after standardizing abbreviations")


# ---------------------------------------------------------------- page text

_STATE_ZIP = re.compile(r"\b(?:TX|TEXAS)\b\.?,?\s*(\d{5})?", re.I)
_TX_ZIP = re.compile(r"\b(7[5-9]\d{3})(?:-\d{4})?\b")
_NUMBER = re.compile(r"\b\d{1,6}[A-Za-z]?\b")
_UNIT_START = re.compile(r"\s*(?:(?:suite|ste|unit|bldg|building|floor|fl|room|rm)\b|#)", re.I)


@dataclass
class FoundAddress:
    snippet: str
    parsed: ParsedAddress
    zip: str | None
    context: str = ""        # surrounding text, used to see which city the address is in


def _looks_like_phone_or_amount(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else " "
    after = text[end:end + 2]
    return before in "($-./:" or bool(re.match(r"^[-./:)%]\d|^\d", after))


def _street_part(text: str, start: int, city: str | None, window: int) -> str:
    chunk = text[start:start + window]
    segments = chunk.split(" | ")
    part = segments[0]
    if len(segments) > 1 and _UNIT_START.match(segments[1]):
        part += " " + segments[1]
    cuts = [len(part)]
    # the first comma usually ends the street - unless a suite follows it ("Coit Road, Suite 2102")
    for comma in re.finditer(",", part):
        if not _UNIT_START.match(part[comma.end():]):
            cuts.append(comma.start())
            break
    state = _STATE_ZIP.search(part)
    if state:
        cuts.append(state.start())
    if city:
        pos = part.upper().find(city.upper(), 1)
        if pos > 0:
            cuts.append(pos)
    return part[:min(cuts)].strip()


def _snippet(text: str, start: int, window: int) -> str:
    """The address as written on the page, ending at the zip code (or state) when there is one."""
    chunk = text[start:start + window]
    end = _TX_ZIP.search(chunk) or _STATE_ZIP.search(chunk)
    chunk = chunk[:end.end()] if end else chunk.split(" | ")[0]
    return " ".join(chunk.replace(" | ", ", ").split()).strip(" ,")


def find_addresses_in_text(text: str, city: str | None = None, window: int = 120) -> list[FoundAddress]:
    """Pull Texas street addresses (number + street + street type, near TX or a TX zip) out of page text."""
    found: dict[str, FoundAddress] = {}
    for m in _NUMBER.finditer(text):
        if _looks_like_phone_or_amount(text, m.start(), m.end()):
            continue
        chunk = text[m.start():m.start() + window]
        state = _STATE_ZIP.search(chunk)
        zip_match = _TX_ZIP.search(chunk)
        if not state and not zip_match:
            continue
        parsed = parse_address(_street_part(text, m.start(), city, window))
        if not parsed or not parsed.number or not parsed.has_street_type:
            continue
        if not any(t.isalpha() and t not in STREET_TYPES and t not in DIRECTIONS for t in parsed.street_tokens):
            continue
        zip_code = (state.group(1) if state and state.group(1) else None) or (zip_match.group(1) if zip_match else None)
        key = parsed.normalized
        if key not in found:
            found[key] = FoundAddress(snippet=_snippet(text, m.start(), window), parsed=parsed,
                                      zip=zip_code, context=chunk.replace(" | ", ", "))
    return list(found.values())


def address_on_page(addr: ParsedAddress | None, text: str, city: str | None = None,
                    window: int = 120) -> tuple[AddressComparison, str] | None:
    """Look for this address on a page; returns the best comparison and the snippet where it was seen."""
    if addr is None or not addr.number:
        return None
    best: tuple[AddressComparison, str] | None = None
    rank = {"SAME": 0, "SAME_BUILDING": 1}
    for m in re.finditer(rf"\b{re.escape(addr.number)}\b", text, re.I):
        parsed = parse_address(_street_part(text, m.start(), city, window))
        cmp = compare_addresses(addr, parsed)
        if cmp.level in rank and (best is None or rank[cmp.level] < rank[best[0].level]):
            best = (cmp, _snippet(text, m.start(), window))
            if cmp.level == "SAME":
                break
    return best
