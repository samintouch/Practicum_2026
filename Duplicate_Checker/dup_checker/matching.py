"""Organization name and phone number comparison."""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

LEGAL_WORDS = {
    "LLC", "PLLC", "PLC", "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "LTD",
    "LP", "LLP", "LLLP", "PA", "PC", "DBA", "THE", "OF", "AND", "AT", "A", "AN", "FOR", "IN",
}

# words shared by many unrelated facilities - matching on these alone means nothing
GENERIC_WORDS = {
    "HEALTH", "HEALTHCARE", "MEDICAL", "MEDICINE", "CENTER", "CENTERS", "CTR", "CLINIC", "CLINICS",
    "HOSPITAL", "SERVICES", "SERVICE", "BEHAVIORAL", "MENTAL", "CARE", "TREATMENT", "RECOVERY",
    "COUNSELING", "GROUP", "ASSOCIATES", "ASSOCIATION", "FAMILY", "PRACTICE", "PSYCHIATRY",
    "PSYCHIATRIC", "PAIN", "MANAGEMENT", "MD", "DO", "NP", "DR", "OFFICE", "TEXAS", "TX",
    "NORTH", "SOUTH", "EAST", "WEST", "PROGRAM", "PROGRAMS", "SYSTEM", "NETWORK", "WELLNESS",
    "ADDICTION", "SUBSTANCE", "ABUSE", "OUTPATIENT", "INPATIENT", "PRIMARY", "COMMUNITY",
    "SOLUTIONS", "INSTITUTE", "PHYSICIANS", "SPECIALISTS", "THERAPY", "REHAB", "REHABILITATION",
}


def name_tokens(name: str | None) -> list[str]:
    if not name:
        return []
    text = re.sub(r"[^A-Z0-9 ]", " ", str(name).upper().replace("&", " AND ").replace("'", ""))
    return [t for t in text.split() if t not in LEGAL_WORDS]


@dataclass
class NameComparison:
    level: str            # SAME, SIMILAR, DIFFERENT, UNKNOWN
    score: float
    shared_words: list[str]


def compare_names(a: str | None, b: str | None) -> NameComparison:
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return NameComparison("UNKNOWN", 0.0, [])
    ratio = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    sa, sb = set(ta), set(tb)
    overlap = len(sa & sb) / min(len(sa), len(sb))
    score = max(ratio, overlap if overlap == 1.0 else ratio)
    shared_distinct = sorted((sa & sb) - GENERIC_WORDS)
    if score >= 0.9:
        level = "SAME"
    elif shared_distinct or score >= 0.6:
        level = "SIMILAR"
    else:
        level = "DIFFERENT"
    return NameComparison(level, round(score, 2), shared_distinct)


CREDENTIALS = {
    "MD", "DO", "NP", "PA", "PHD", "PSYD", "LPC", "LCSW", "LMFT", "LCDC", "APRN", "FNP", "PMHNP",
    "DNP", "RN", "LPCS", "LMSW", "BC", "MSN", "DR",
}
ORGANIZATION_WORDS = GENERIC_WORDS | {
    "PLLC", "LLC", "INC", "PA", "CORP", "LTD", "LP", "THE", "OF", "AND", "AT", "FOR", "ASSOCIATION",
    "FOUNDATION", "MINISTRIES", "HOUSE", "HOME", "HOMES", "ACADEMY", "UNIVERSITY", "COUNTY", "CITY",
    "LIVING", "RESIDENCE", "SOBER", "PARTNERS", "AGENCY", "CONSULTING", "COUNCIL", "SOCIETY",
    # words common in recovery program names ("TURNING POINT", "SIMPLY GRACE")
    "PSYCH", "MATTERS", "POINT", "TURNING", "GRACE", "HOPE", "PATH", "PATHWAYS", "BRIDGE", "BRIDGES",
    "JOURNEY", "HAVEN", "LIFE", "NEW", "CHANGE", "CHANGES", "FREEDOM", "SERENITY", "OASIS", "HEALING",
    "HARBOR", "PLACE", "WAY", "STEPS", "STEP", "SIMPLY", "BEGINNINGS", "RECOVER", "SPRINGS", "RANCH",
}


def looks_like_person(name: str | None) -> bool:
    """'DR FRANK STUART MURPHY', 'KARLA BOLTON MD', 'CARNIKA DONALD' - an individual provider, not an organization."""
    if not name:
        return False
    words = re.sub(r"[^A-Z ]", " ", str(name).upper()).split()
    if not words or re.search(r"\d", str(name)):
        return False
    if words[0] == "DR" or (words[-1] in CREDENTIALS and words[-1] != "DR"):
        rest = [w for w in words if w not in CREDENTIALS]
        return 1 <= len(rest) <= 4 and not any(w in ORGANIZATION_WORDS for w in rest)
    # plain "FIRST LAST" / "FIRST MIDDLE LAST"
    return 2 <= len(words) <= 3 and not any(w in ORGANIZATION_WORDS for w in words)


def distinctive_name_words(name: str | None) -> list[str]:
    return [t for t in name_tokens(name) if t not in GENERIC_WORDS and len(t) >= 3]


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def phones_match(a: str | None, b: str | None) -> bool | None:
    pa, pb = normalize_phone(a), normalize_phone(b)
    if not pa or not pb:
        return None
    return pa == pb
