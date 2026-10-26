"""
Stage 1: decide duplicate status within each existing duplicate_group_id.

Rule: within a group, if organization_facility_name matches exactly (after
normalization) across every record, AND organization_street_name also matches
exactly (after normalization) across every record, then the record with the
highest completeness_score is kept as "not duplicate" and the rest are marked
"duplicate". Any group that fails either check is left as "needs manual
review" -- this script never guesses.

Address normalization is pluggable: normalize_address() below is a simple
offline rule-based standardizer (abbreviations, directionals, whitespace).
Once USPS API credentials are available, replace its body with a live call
to the USPS Address API and the rest of the pipeline is unaffected.
"""

import re
import sys
import pandas as pd

INPUT_CSV = "Comptroller_refined_groups.csv"
OUTPUT_CSV = "dedupe_refined_stage1_review.csv"

# --- name normalization -----------------------------------------------------

def normalize_name(name):
    if pd.isna(name) or not str(name).strip():
        return ""
    s = str(name).upper()
    s = s.replace("'", "").replace(".", "").replace(",", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s

# --- address normalization (offline fallback; swap in USPS API later) ------

_STREET_SUFFIXES = {
    "STREET": "ST", "AVENUE": "AVE", "BOULEVARD": "BLVD", "DRIVE": "DR",
    "ROAD": "RD", "LANE": "LN", "COURT": "CT", "PARKWAY": "PKWY",
    "EXPRESSWAY": "EXPY", "FREEWAY": "FWY", "HIGHWAY": "HWY",
    "PLACE": "PL", "CIRCLE": "CIR", "SUITE": "STE", "BUILDING": "BLDG",
    "FLOOR": "FL", "CENTER": "CTR",
}

_DIRECTIONALS = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
}

def normalize_address(street):
    """Offline rule-based normalization. Replace with a USPS Address API
    call once credentials are available -- signature can stay the same."""
    if pd.isna(street) or not str(street).strip():
        return ""
    s = str(street).upper()
    s = s.replace(".", "").replace(",", "")
    s = re.sub(r"\s+", " ", s).strip()
    tokens = [_STREET_SUFFIXES.get(t, t) for t in s.split(" ")]
    tokens = [_DIRECTIONALS.get(t, t) for t in tokens]
    return " ".join(tokens)

# --- core logic --------------------------------------------------------------

def decide_group(group):
    names = group["organization_facility_name"].map(normalize_name)
    if names.eq("").any() or names.nunique() != 1:
        return pd.Series("needs manual review: name mismatch", index=group.index)

    addrs = group["organization_street_name"].map(normalize_address)
    if addrs.eq("").any() or addrs.nunique() != 1:
        return pd.Series("needs manual review: address mismatch", index=group.index)

    primary_idx = group["completeness_score"].idxmax()
    result = pd.Series("duplicate", index=group.index)
    result.loc[primary_idx] = "not duplicate"
    return result


def main():
    df = pd.read_csv(INPUT_CSV)

    decisions = []
    reasons = []
    for group_id, group in df.groupby("duplicate_group_id", sort=False):
        decision = decide_group(group)
        for idx in group.index:
            d = decision.loc[idx]
            if d.startswith("needs manual review"):
                decisions.append("needs manual review")
                reasons.append(d.split(": ", 1)[1])
            else:
                decisions.append(d)
                reasons.append("exact name + address match")

    df["stage1_decision"] = decisions
    df["stage1_reason"] = reasons

    out_cols = [
        "duplicate_group_id", "duplicate_group_size", "record_id",
        "organization_facility_name", "organization_street_name",
        "completeness_score", "stage1_decision", "stage1_reason",
    ]
    df[out_cols].to_csv(OUTPUT_CSV, index=False)

    counts = df["stage1_decision"].value_counts()
    groups_total = df["duplicate_group_id"].nunique()
    groups_auto = df.loc[df["stage1_decision"] == "not duplicate", "duplicate_group_id"].nunique()
    print(f"Groups: {groups_total} total, {groups_auto} auto-decided, "
          f"{groups_total - groups_auto} sent to manual review")
    print(counts.to_string())
    print(f"\nWrote {OUTPUT_CSV}")


if __name__ == "__main__":
    sys.exit(main())
