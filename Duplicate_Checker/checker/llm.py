"""Client for the local Ollama model, used only for cases the rules cannot settle."""
from __future__ import annotations

import json
import threading

import requests

PAIR_SYSTEM_PROMPT = """You review possible duplicate records in a directory of behavioral health and \
substance use treatment facilities in Texas.

Two records are TRUE DUPLICATES only if they describe the same facility at the same physical location: \
the same organization entered twice, possibly under an older/newer or slightly different name, or with the \
address written differently. They are NOT duplicates if they are different organizations or practices that \
share a building or street, or different branch locations of one organization at different addresses.

Guidance:
- Formatting differences in addresses (W vs West, Rd vs Road, Ste vs Suite) do not matter. The address \
comparison you are given has already standardized them.
- Same address AND same phone number strongly suggests the same facility, even if the names differ \
(renamed, or taken over by a new operator).
- Different suites in the same building with different names are usually different practices.
- The same name at different street addresses is usually a different branch, unless a website shows the \
facility moved from one address to the other.
- An unreachable website, or one about an unrelated business, is not evidence either way.

Answer only with JSON: {"verdict": "DUPLICATE" or "NOT_DUPLICATE" or "UNSURE", \
"confidence": number from 0 to 1, "reason": "one or two sentences citing the evidence"}"""

ADDRESS_SYSTEM_PROMPT = """You extract the physical street address of an organization from website text.
Answer only with JSON: {"street": "house number and street, with suite if shown", "city": "", "zip": ""}.
Use empty strings if the page does not show a street address for this organization. Never guess."""


class OllamaClient:
    """Asks the model fresh every time - answers are not saved between runs."""

    def __init__(self, base_url: str, model: str, timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._lock = threading.Lock()          # one request at a time - the local model is the bottleneck

    def check(self) -> str | None:
        """Returns an error message, or None when the server is up and the model is installed."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=10)
            resp.raise_for_status()
        except requests.RequestException as exc:
            return f"Ollama is not reachable at {self.base_url} ({exc.__class__.__name__})"
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        if self.model not in models and f"{self.model}:latest" not in models:
            return f"Model '{self.model}' is not installed in Ollama (found: {', '.join(models) or 'none'})"
        return None

    def chat_json(self, system: str, user: str) -> dict | None:
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        with self._lock:
            try:
                resp = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
                resp.raise_for_status()
                result = json.loads(resp.json()["message"]["content"])
            except (requests.RequestException, KeyError, ValueError):
                return None
        return result if isinstance(result, dict) else None

    def judge_pair(self, evidence: str) -> tuple[str, float, str] | None:
        result = self.chat_json(PAIR_SYSTEM_PROMPT, evidence)
        if not result:
            return None
        verdict = str(result.get("verdict", "")).upper().replace(" ", "_")
        if verdict not in ("DUPLICATE", "NOT_DUPLICATE", "UNSURE"):
            verdict = "UNSURE"
        try:
            confidence = max(0.0, min(1.0, float(result.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        return verdict, confidence, str(result.get("reason", "")).strip()

    def extract_address(self, org_name: str, page_text: str) -> dict | None:
        user = f"Organization: {org_name}\n\nWebsite text:\n{page_text}"
        return self.chat_json(ADDRESS_SYSTEM_PROMPT, user)
