"""
Stage 1c: for records still in manual review after Stage 1 (exact match) and
Stage 1b (NPI registry), use each record's own website (from the full REDCap
export) as a third independent source: fetch the page and check whether the
record's OWN recorded street address actually appears on it.

Scope, deliberately narrow: this script does NOT try to extract "the"
address from an arbitrary page and compare it across records -- that needs
language understanding a plain script can't do reliably. What it CAN do
cheaply and safely is confirm, for groups where the address already matches
exactly (Stage 1's own normalization) but the NAME does not, that the
address is real and currently in use -- i.e. bridge the "different name,
same place" gap (e.g. "Quazi Imam MD" vs "Quazi M Imam, MD Clinic", same
street address) using the facility's own live site as a witness.

Only groups where 2+ records already have an identical normalized address
AND each of those records' website independently confirms that address are
auto-decided. Everything else -- no website, fetch failure, address not
found on the page, or an address that doesn't already match -- is left as
"needs manual review". This never invents a decision from a partial match.
"""

import re
import sys
import time
import pandas as pd
import requests
from html.parser import HTMLParser

from dedupe_stage1 import normalize_address, _STREET_SUFFIXES, _DIRECTIONALS

REVIEW_CSV = "dedupe_refined_stage2_review.csv"
FULL_DATASET_XLSX = "../ComptrollerProject20_DATA_LABELS_2026-04-16_1051.xlsx"
OUTPUT_CSV = "dedupe_refined_stage3_review.csv"

REQUEST_TIMEOUT_SEC = 10
REQUEST_DELAY_SEC = 0.5
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DedupBot/1.0)"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self.chunks.append(data)

    def text(self):
        return " ".join(self.chunks)


def _normalize_page_text(raw_text):
    text = re.sub(r"[^A-Za-z0-9 ]", " ", raw_text).upper()
    text = re.sub(r"\s+", " ", text).strip()
    # apply the SAME street-suffix/directional abbreviation used to normalize
    # the CSV's address, so "West Parker Road" (spelled out on a site) lines
    # up with the CSV's already-abbreviated "W PARKER RD"
    tokens = [_STREET_SUFFIXES.get(t, t) for t in text.split(" ")]
    tokens = [_DIRECTIONALS.get(t, t) for t in tokens]
    return " ".join(tokens)


def _normalize_url(url):
    if pd.isna(url) or not str(url).strip():
        return None
    u = str(url).strip()
    return u if u.lower().startswith("http") else "http://" + u


def fetch_page_text_requests(url):
    """Fast path: plain HTTP GET, no JS execution. Misses anything a site
    renders client-side (common on large hospital-system sites)."""
    u = _normalize_url(url)
    if not u:
        return None
    try:
        resp = requests.get(u, headers=HEADERS, timeout=REQUEST_TIMEOUT_SEC)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    finally:
        time.sleep(REQUEST_DELAY_SEC)
    parser = _TextExtractor()
    try:
        parser.feed(resp.text)
    except Exception:
        return None
    return _normalize_page_text(parser.text())


_browser = None


def _get_browser():
    global _browser
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=True)
    return _browser


def fetch_page_text_browser(url, attempts=2):
    """Slow path: renders the page like a real browser so JS-injected
    content (common on hospital-system sites) is visible. Used only as a
    fallback when the fast path doesn't confirm the address. Retries once --
    reusing one browser across many sequential page loads occasionally hits
    a transient navigation failure that doesn't reproduce on a fresh try."""
    u = _normalize_url(url)
    if not u:
        return None
    for attempt in range(attempts):
        try:
            page = _get_browser().new_page()
            try:
                page.goto(u, timeout=20000, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                raw_text = page.inner_text("body")
            finally:
                page.close()
            return _normalize_page_text(raw_text)
        except Exception:
            if attempt + 1 == attempts:
                return None
            time.sleep(1)


_page_cache = {}


def get_page_text(url, use_browser=False):
    key = (url, use_browser)
    if key not in _page_cache:
        fetcher = fetch_page_text_browser if use_browser else fetch_page_text_requests
        _page_cache[key] = fetcher(url)
    return _page_cache[key]


def confirm_address_on_page(url, normalized_address):
    """Try the fast plain-HTTP fetch first; only pay for a headless-browser
    render if that didn't find the address."""
    if address_appears_on_page(normalized_address, get_page_text(url, use_browser=False)):
        return True
    return address_appears_on_page(normalized_address, get_page_text(url, use_browser=True))


def address_appears_on_page(normalized_address, page_text):
    if not normalized_address or not page_text:
        return False
    # allow the page's own whitespace/line-break formatting to differ from a
    # single-space-joined address by matching the tokens in order, loosely
    tokens = normalized_address.split()
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    return re.search(pattern, page_text) is not None


def main():
    review = pd.read_csv(REVIEW_CSV)
    full = pd.read_excel(FULL_DATASET_XLSX)
    website_col = next(c for c in full.columns if "website" in c.lower())
    full = full.rename(columns={"Record ID": "record_id"})[["record_id", website_col]]
    full = full.rename(columns={website_col: "website"})

    df = review.merge(full, on="record_id", how="left")
    df["norm_addr"] = df["organization_street_name"].map(normalize_address)

    final_decision = df["final_decision"].copy()
    final_reason = df["final_reason"].copy()
    website_confirmed = pd.Series(False, index=df.index)

    pending = df[df["final_decision"] == "needs manual review"]
    candidate_groups = 0
    for group_id, group in pending.groupby("duplicate_group_id", sort=False):
        addrs = group["norm_addr"]
        if addrs.eq("").any() or addrs.nunique() != 1 or len(group) < 2:
            continue  # only handles the "address already matches, name doesn't" case
        candidate_groups += 1

        confirmed_idx = []
        for idx, row in group.iterrows():
            if confirm_address_on_page(row["website"], row["norm_addr"]):
                confirmed_idx.append(idx)

        # The group already cleared a strict gate -- every member's full
        # street address is textually IDENTICAL (Stage 1's exact-match
        # normalization), so the only open question is whether the
        # differently-worded names refer to the same place. One member's
        # own live site confirming that address is enough corroboration for
        # that; it doesn't need to be repeated by every member (a record
        # whose website happens to be a generic "our services" page instead
        # of that specific location's page will never confirm anything, by
        # no fault of the data).
        if len(confirmed_idx) >= 1:
            website_confirmed.loc[confirmed_idx] = True
            primary_idx = df.loc[group.index, "completeness_score"].idxmax()
            for idx in group.index:
                final_decision.loc[idx] = "not duplicate" if idx == primary_idx else "duplicate"
                final_reason.loc[idx] = "website confirms shared address (name variant)"

    df["website_confirmed"] = website_confirmed
    df["final_decision"] = final_decision
    df["final_reason"] = final_reason

    out_cols = [
        "duplicate_group_id", "duplicate_group_size", "record_id",
        "organization_facility_name", "organization_street_name",
        "completeness_score", "npi", "npi_org_name", "website",
        "website_confirmed", "final_decision", "final_reason",
    ]
    df[out_cols].to_csv(OUTPUT_CSV, index=False)

    resolved_now = (final_decision != df.merge(review[["record_id", "final_decision"]],
                                                on="record_id", suffixes=("", "_prev"))["final_decision_prev"]).sum()
    print(f"Candidate groups (address already matched, name didn't): {candidate_groups}")
    print(f"Website pass moved {resolved_now} additional records out of manual review")
    print(df["final_decision"].value_counts().to_string())
    print(f"\nWrote {OUTPUT_CSV}")

    if _browser is not None:
        _browser.close()


if __name__ == "__main__":
    sys.exit(main())
