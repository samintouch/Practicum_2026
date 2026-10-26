"""
Stage 1b: for records Stage 1 sent to manual review, try to resolve each one
to an NPI (National Provider Identifier) via the free NPPES Registry API
(https://npiregistry.cms.hhs.gov/api/). If two or more records in the same
duplicate_group_id resolve to the SAME NPI, that's an independent, authoritative
confirmation they're the same entity -- override to a score-based decision.
If they resolve to DIFFERENT NPIs, that's evidence they're distinct entities.
If a record can't be resolved at all, it stays "needs manual review".

This never invents a decision from a partial/ambiguous NPI match -- only an
exact NPI-number agreement (or disagreement) moves a record out of manual
review.
"""

import re
import sys
import time
import pandas as pd
import requests

INPUT_CSV = "dedupe_refined_stage1_review.csv"
ORIGINAL_CSV = "Comptroller_refined_groups.csv"
OUTPUT_CSV = "dedupe_refined_stage2_review.csv"

NPPES_URL = "https://npiregistry.cms.hhs.gov/api/"
REQUEST_DELAY_SEC = 0.3  # be polite to the public API

_STRIP_WORDS = {"LLC", "INC", "PLLC", "PA", "PC", "MD", "DO", "LTD", "CO"}
_TITLE_SUFFIXES = {"MD", "DO", "DDS", "DPM", "PA", "NP", "RN", "PHD"}
_ORG_KEYWORDS = {
    "CENTER", "CLINIC", "HOSPITAL", "SERVICES", "GROUP", "ASSOCIATES",
    "OFFICE", "HEALTH", "MEDICAL", "CARE", "PROGRAM", "SYSTEM", "INSTITUTE",
    "FOUNDATION", "PARTNERS", "RECOVERY", "COUNSELING", "PSYCHIATRY",
    "PSYCHIATRIC", "WELLNESS", "REHAB", "THERAPEUTIC", "THERAPY",
    "HEALTHCARE", "OUTPATIENT", "COMMUNITY", "TREATMENT", "BEHAVIORAL",
}


def simplify_name_for_search(name):
    s = re.sub(r"[^A-Za-z0-9 ]", " ", str(name)).upper()
    words = [w for w in s.split() if w not in _STRIP_WORDS]
    return " ".join(words).strip()


def looks_like_person_name(simplified_name):
    words = simplified_name.split()
    if not (2 <= len(words) <= 4):
        return False
    return not any(w in _ORG_KEYWORDS for w in words)


def guess_first_last_name(simplified_name):
    words = [w for w in simplified_name.split() if w not in _TITLE_SUFFIXES]
    if len(words) < 2:
        return None, None
    return words[0], words[-1]


_cache = {}


def street_number(street):
    m = re.match(r"\s*(\d+)", str(street))
    return m.group(1) if m else None


def npi_search(organization_name, state, postal_code, expected_street):
    return _npi_query({"organization_name": organization_name, "state": state}, postal_code, expected_street)


def npi_search_individual(first_name, last_name, state, postal_code, expected_street):
    return _npi_query({"first_name": first_name, "last_name": last_name, "state": state}, postal_code, expected_street)


def _npi_raw_results(search_params):
    key = tuple(sorted(search_params.items()))
    if key in _cache:
        return _cache[key]
    params = {"version": "2.1", "limit": 10, **search_params}
    try:
        resp = requests.get(NPPES_URL, params=params, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except requests.RequestException:
        results = []
    time.sleep(REQUEST_DELAY_SEC)
    _cache[key] = results
    return results


def _npi_query(search_params, postal_code, expected_street):
    postal_code = "" if pd.isna(postal_code) else str(postal_code).split(".")[0]
    expected_num = street_number(expected_street)
    if not postal_code or not expected_num:
        return None

    # Accept a result only when BOTH the zip code AND the street number of its
    # registered LOCATION address match. Zip alone is not enough -- large
    # campuses (hospitals, universities) often share one zip across many
    # distinct buildings, which produced a false match between two different
    # UT Southwestern buildings on the same Harry Hines Blvd campus.
    for r in _npi_raw_results(search_params):
        addrs = [a for a in r.get("addresses", []) if a.get("address_purpose") == "LOCATION"]
        for a in addrs:
            if a.get("postal_code", "")[:5] != postal_code[:5]:
                continue
            if street_number(a.get("address_1", "")) != expected_num:
                continue
            basic = r["basic"]
            display_name = basic.get("organization_name") or \
                f"{basic.get('first_name', '')} {basic.get('last_name', '')}".strip()
            return {"npi": r["number"], "org_name": display_name,
                    "address_1": a.get("address_1", ""), "postal_code": a.get("postal_code", "")}
    return None


def resolve_record(row):
    name = simplify_name_for_search(row["organization_facility_name"])
    if not name:
        return None
    state = row.get("organization_state", "TX")
    zip_code = row.get("organization_zip_code", "")
    street = row.get("organization_street_name", "")

    # Progressively trim trailing words (handles descriptive suffixes NPPES
    # doesn't recognize, e.g. "... OF AMERICA", "... PLANO", "... OF DALLAS")
    # down to a 2-word floor so we don't end up searching on one generic word.
    words = name.split()
    for end in range(len(words), 1, -1):
        match = npi_search(" ".join(words[:end]), state, zip_code, street)
        if match:
            return match

    if looks_like_person_name(name):
        first, last = guess_first_last_name(name)
        if first and last:
            match = npi_search_individual(first, last, state, zip_code, street)
            if match:
                return match

    return None


def main():
    stage1 = pd.read_csv(INPUT_CSV)
    original = pd.read_csv(ORIGINAL_CSV)
    df = stage1.merge(
        original[["record_id", "organization_state", "organization_zip_code"]],
        on="record_id", how="left",
    )

    review_mask = df["stage1_decision"] == "needs manual review"
    print(f"Looking up {review_mask.sum()} records across "
          f"{df.loc[review_mask, 'duplicate_group_id'].nunique()} groups via NPPES...")

    npi_numbers = {}
    npi_org_names = {}
    for idx in df.index[review_mask]:
        row = df.loc[idx]
        match = resolve_record(row)
        npi_numbers[idx] = match["npi"] if match else None
        npi_org_names[idx] = match["org_name"] if match else None

    df["npi"] = pd.Series(npi_numbers)
    df["npi_org_name"] = pd.Series(npi_org_names)

    final_decision = df["stage1_decision"].copy()
    final_reason = df["stage1_reason"].copy()

    for group_id, group in df[review_mask].groupby("duplicate_group_id", sort=False):
        found = group.dropna(subset=["npi"])
        if len(found) < 2:
            continue
        npi_counts = found["npi"].value_counts()
        matched_npi = npi_counts[npi_counts >= 2].index
        for npi in matched_npi:
            same_entity_idx = found.index[found["npi"] == npi]
            primary_idx = df.loc[same_entity_idx, "completeness_score"].idxmax()
            for idx in same_entity_idx:
                if idx == primary_idx:
                    final_decision.loc[idx] = "not duplicate"
                else:
                    final_decision.loc[idx] = "duplicate"
                final_reason.loc[idx] = f"NPI match confirms same entity (NPI {npi})"

    df["final_decision"] = final_decision
    df["final_reason"] = final_reason

    out_cols = [
        "duplicate_group_id", "duplicate_group_size", "record_id",
        "organization_facility_name", "organization_street_name",
        "completeness_score", "npi", "npi_org_name",
        "final_decision", "final_reason",
    ]
    df[out_cols].to_csv(OUTPUT_CSV, index=False)

    resolved_now = (final_decision != df["stage1_decision"]).sum()
    print(f"NPI pass moved {resolved_now} additional records out of manual review")
    print(df["final_decision"].value_counts().to_string())
    print(f"\nWrote {OUTPUT_CSV}")


if __name__ == "__main__":
    sys.exit(main())
