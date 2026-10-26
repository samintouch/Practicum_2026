"""Local AI (Ollama) for websites the code can't read cleanly.

The model is only asked when the code-only extraction couldn't decide.
It reads the contact-related parts of the pages and returns every
location the organization lists. Nothing it returns is trusted as is:
each phone, fax, email and street must appear in the page text, or it is
dropped ("grounding"). That stops a made-up value reaching REDCap.
"""

import json
import re
import threading

import requests

from .extract import Location
from .normalize import phone_digits, state_code, zip5

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"
MAX_PROMPT_CHARS = 5000
TIMEOUT = 300

_FIELDS = ["name", "street", "city", "state", "zip", "phone", "fax", "email"]
SCHEMA = {
    "type": "object",
    "properties": {
        "site_type": {"type": "string",
                      "enum": ["single_organization", "directory_of_unrelated_providers"]},
        "locations": {"type": "array", "items": {
            "type": "object",
            "properties": {k: {"type": "string"} for k in _FIELDS},
            "required": _FIELDS,
        }},
    },
    "required": ["site_type", "locations"],
}

PROMPT = """You read text from a website and extract contact details. Copy values exactly as written; \
never invent or guess. Use an empty string for anything the text does not state.

We are checking this organization: {identity}

1. site_type: "single_organization" if this is one organization's own website (one or several of its \
own offices), or "directory_of_unrelated_providers" if it lists many unrelated doctors, clinics or \
hospitals (a doctor finder, a hospital network directory, a listings site).
2. locations: every physical office of this organization that the text lists, each with its own \
name, street (with suite), city, state, zip, phone, fax and email. Keep each phone with the office \
it is written next to. Do not list mailing-only P.O. boxes.

Website text:
---
{text}
---"""

_RELEVANT = re.compile(r"\d{3}\D{0,3}\d{3}\D?\d{4}|\b\d{5}\b|@|\b(suite|ste|street|st\b|ave|road|rd\b|"
                       r"blvd|drive|dr\b|hwy|fwy|pkwy|lane|ln\b|phone|fax|tel|email|location|office|"
                       r"address|clinic|center)\b", re.I)


def _snippet(page_lines, max_chars=MAX_PROMPT_CHARS):
    """Only the lines that look like contact details, with one line either
    side, each repeated footer line sent once. Keeps the prompt small
    enough for a CPU-only model."""
    out, seen = [], set()
    for url, lines in page_lines:
        keep = set()
        for i, line in enumerate(lines):
            if len(line) <= 200 and _RELEVANT.search(line):
                keep.update((i - 1, i, i + 1))
        block = []
        for i in sorted(k for k in keep if 0 <= k < len(lines)):
            if lines[i] not in seen and len(lines[i]) <= 200:
                seen.add(lines[i])
                block.append(lines[i])
        if block:
            out.append(f"[page: {url}]\n" + "\n".join(block))
    return "\n\n".join(out)[:max_chars]


class LocalAI:
    def __init__(self, model=DEFAULT_MODEL, url=DEFAULT_URL):
        self.model, self.url = model, url.rstrip("/")
        self._lock = threading.Lock()   # the model answers one question at a time anyway

    def available(self):
        try:
            tags = requests.get(f"{self.url}/api/tags", timeout=5).json()
        except (requests.RequestException, ValueError):
            return False, f"Ollama is not running at {self.url}"
        names = {m.get("name") for m in tags.get("models", [])}
        if self.model not in names and f"{self.model}:latest" not in names:
            return False, f"model {self.model!r} is not installed (ollama pull {self.model})"
        return True, ""

    def _ask(self, prompt):
        with self._lock:
            resp = requests.post(f"{self.url}/api/generate", timeout=TIMEOUT, json={
                "model": self.model, "prompt": prompt, "stream": False, "format": SCHEMA,
                "think": False,      # thinking models (Qwen 3.x) would take minutes on a CPU
                "options": {"temperature": 0, "num_ctx": 8192},
            })
        resp.raise_for_status()
        return json.loads(resp.json().get("response") or "{}")

    def extract_locations(self, record, page_lines):
        """page_lines: [(url, [lines])]. Returns (site_type, [Location], note)."""
        text = _snippet(page_lines)
        if not text:
            return "", [], "no contact text for the AI to read"
        identity = ", ".join(v for v in (record["name"], record["street"], record["city"], record["state"]) if v)
        try:
            answer = self._ask(PROMPT.format(identity=identity, text=text))
        except (requests.RequestException, ValueError) as exc:
            return "", [], f"AI call failed: {type(exc).__name__}"

        full_text = "\n".join("\n".join(lines) for _, lines in page_lines)
        source_url = page_lines[0][0]
        locations, dropped = [], 0
        for raw in answer.get("locations") or []:
            loc, n_dropped = _grounded(raw, full_text, source_url)
            dropped += n_dropped
            if loc:
                locations.append(loc)
        note = f"AI ({self.model}) found {len(locations)} location(s)"
        if dropped:
            note += f"; {dropped} value(s) dropped because they are not on the page"
        return answer.get("site_type", ""), locations, note


def _grounded(raw, full_text, url):
    """Keep only values that are really in the page text."""
    text_lower = full_text.lower()
    digits_text = re.sub(r"\D", "", full_text)
    dropped = 0

    def number(value):
        nonlocal dropped
        digits = phone_digits(value)
        if digits and digits not in digits_text:
            dropped += 1
            return ""
        return digits

    phone, fax = number(raw.get("phone", "")), number(raw.get("fax", ""))
    email = (raw.get("email") or "").strip().lower()
    if email and email not in text_lower:
        email, dropped = "", dropped + 1

    street = (raw.get("street") or "").strip()
    zip_code = zip5(raw.get("zip", ""))
    number_match = re.match(r"(\d+)\s+(?:[NSEW]\.?\s+)?([A-Za-z0-9]+)", street)
    street_ok = bool(number_match and zip_code and zip_code in full_text
                     and re.search(rf"\b{number_match.group(1)}\b", full_text)
                     and number_match.group(2).lower() in text_lower)
    if street and not street_ok:
        dropped += 1
    if not street_ok:
        return None, dropped
    return Location(street=street, city=(raw.get("city") or "").strip(), state=state_code(raw.get("state", "")),
                    zip=zip_code, name=(raw.get("name") or "").strip(), phone=phone, fax=fax, email=email,
                    url=url, source="AI"), dropped
