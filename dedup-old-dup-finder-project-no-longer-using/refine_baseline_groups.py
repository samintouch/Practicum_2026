"""
Stage 0: re-derive cleaner duplicate groups from the ORIGINAL (unmodified)
Comptroller.R output.

Context: Comptroller.R itself can no longer be edited, so the fixes that were
validated there (house-number gate on the address-match branch; fuller,
word-boundary-safe normalization) are re-implemented here in Python instead,
applied as a refinement pass on top of whatever the unmodified R script
produces. Everything downstream (dedupe_stage1.py -> npi_verify.py ->
website_verify.py) then runs on this refined file exactly as before.

This deliberately reproduces R's ACTUAL similarity metric: Comptroller.R
calls stringsim(..., method="jw") without setting p, and stringdist's "jw"
method only applies the Winkler prefix bonus when p > 0 -- with the default
p=0 it is plain Jaro similarity, not Jaro-Winkler. jellyfish.jaro_similarity
matches this exactly (verified against R: both give 0.9444... for
"MARTHA"/"MARHTA"), so the re-grouping below reproduces the same scoring R
would use, not a different metric.

What this does NOT do: adopt the "require both name AND address" (Option A)
change. That was tested and rejected -- against 72 manually-verified records,
it caught only 5 of 17 real duplicates (vs 15 of 17 for the either/or rule),
silently dropping the rest instead of just flagging fewer records. The
either/or condition is kept; only the address branch gets the extra
house-number requirement.

Only re-examines pairs the ORIGINAL R script already grouped together --
it can't recover a true duplicate the original algorithm never put in the
same group in the first place (e.g. two records that only a phone call or
cross-referencing outside data would ever connect).
"""

import re
import sys
import pandas as pd
import jellyfish

INPUT_CSV = "Comptroller_R_duplicates_baseline.csv"
OUTPUT_CSV = "Comptroller_refined_groups.csv"

NAME_STRIP_WORDS = {"llc", "pllc", "plc", "inc", "clinic", "center", "healthcare", "health", "medical"}

ADDR_ABBREV = {
    "street": "st", "road": "rd", "avenue": "ave", "boulevard": "blvd", "suite": "ste",
    "drive": "dr", "lane": "ln", "court": "ct", "parkway": "pkwy", "expressway": "expy",
    "freeway": "fwy", "highway": "hwy", "place": "pl", "circle": "cir",
    "building": "bldg", "floor": "fl",
    "north": "n", "south": "s", "east": "e", "west": "w",
}

MATCH_THRESHOLD = 0.90


def normalize_name(x):
    if pd.isna(x):
        return ""
    s = str(x).lower()
    for word in NAME_STRIP_WORDS:
        s = re.sub(rf"\b{word}\b", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_addr(x):
    if pd.isna(x):
        return ""
    s = str(x).lower()
    for word, abbrev in ADDR_ABBREV.items():
        s = re.sub(rf"\b{word}\b", abbrev, s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def house_number(normalized_addr):
    m = re.match(r"(\d+)", normalized_addr)
    return m.group(1) if m else None


def jaro(a, b):
    if not a or not b:
        return 0.0
    return jellyfish.jaro_similarity(a, b)


def refine_group(group):
    """Same seed-based (star) clustering approach as Comptroller.R's loop,
    scoped to just this one pre-existing group -- re-split it wherever a
    member no longer clears the tightened match condition against the
    group's running seed."""
    idx = list(group.index)
    n_name = {i: normalize_name(group.loc[i, "organization_facility_name"]) for i in idx}
    n_addr = {i: normalize_addr(group.loc[i, "organization_street_name"]) for i in idx}
    n_house = {i: house_number(n_addr[i]) for i in idx}

    assigned = {}
    reason = {}
    next_sub = 0
    for i in idx:
        if i in assigned:
            continue
        assigned[i] = next_sub
        for j in idx:
            if j == i or j in assigned:
                continue
            name_sim = jaro(n_name[i], n_name[j])
            phys_sim = jaro(n_addr[i], n_addr[j])
            name_match = name_sim >= MATCH_THRESHOLD
            house_match = n_house[i] is not None and n_house[i] == n_house[j]
            phys_match = phys_sim >= MATCH_THRESHOLD and house_match

            if name_match or phys_match:
                assigned[j] = next_sub
                if name_match and phys_match:
                    reason[j] = "Name + Physical Address"
                elif name_match:
                    reason[j] = "Name"
                else:
                    reason[j] = "Physical Address"
        next_sub += 1

    return assigned, reason


def main():
    df = pd.read_csv(INPUT_CSV)

    new_group_id = {}
    new_reason = {}
    counter = 0
    for orig_gid, group in df.groupby("duplicate_group_id", sort=False):
        sub_assignment, sub_reason = refine_group(group)
        offset = {sub: counter + sub for sub in set(sub_assignment.values())}
        counter += len(offset)
        for i in group.index:
            new_group_id[i] = offset[sub_assignment[i]]
            new_reason[i] = sub_reason.get(i)

    df["duplicate_group_id"] = pd.Series(new_group_id)
    df["duplicate_reason"] = pd.Series(new_reason)
    df["duplicate_group_size"] = df.groupby("duplicate_group_id")["record_id"].transform("size")

    df = df[df["duplicate_group_size"] > 1].copy()
    max_score = df.groupby("duplicate_group_id")["completeness_score"].transform("max")
    df["recommended_primary_record"] = (df["completeness_score"] == max_score).map({True: "YES", False: "NO"})
    df["review_needed"] = "YES"

    df = df.sort_values(["duplicate_group_id", "completeness_score"], ascending=[True, False])
    df.to_csv(OUTPUT_CSV, index=False)

    groups_before = pd.read_csv(INPUT_CSV)["duplicate_group_id"].nunique()
    print(f"Groups: {groups_before} (original R baseline) -> {df['duplicate_group_id'].nunique()} (refined)")
    print(f"Flagged records: {len(pd.read_csv(INPUT_CSV))} -> {len(df)}")
    print(f"\nWrote {OUTPUT_CSV}")


if __name__ == "__main__":
    sys.exit(main())
