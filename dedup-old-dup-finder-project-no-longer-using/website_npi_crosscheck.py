"""
Stage 1d: safeguard on top of website_verify.py's "one member's site confirms
the shared address" rule.

That rule is valid evidence that an address is real, but it is NOT evidence
that two differently-named records are the SAME legal entity -- two
genuinely distinct organizations can share one building (a specialty
hospital operating inside another facility is a common real pattern, not an
edge case). Real example this caught: record 13 "Carrollton Springs
Treatment Center" and record 527 "Regency Hospital of North Dallas II LLLP"
were auto-decided as duplicate/not-duplicate purely because one member's
website confirmed the shared address -- but NPI shows them as two separate
registered entities (NPI 1134401466 vs 1013075167) at that same address.

This rescans every group website_verify.py decided via
"website confirms shared address (name variant)" and looks each member up in
NPI independently. If two members resolve to DIFFERENT NPIs, that's direct
evidence they're distinct legal entities -- the decision is reverted back to
"needs manual review", never left as a wrong duplicate/not-duplicate call.
If NPI doesn't resolve for enough members to tell either way, the existing
website-based decision is left alone (no evidence to override it with).
"""

import sys
import pandas as pd

from npi_verify import resolve_record

INPUT_CSV = "dedupe_refined_stage3_review.csv"
ORIGINAL_CSV = "Comptroller_refined_groups.csv"
OUTPUT_CSV = "dedupe_refined_stage4_review.csv"

TARGET_REASON = "website confirms shared address (name variant)"


def main():
    df = pd.read_csv(INPUT_CSV)
    original = pd.read_csv(ORIGINAL_CSV)
    df = df.merge(
        original[["record_id", "organization_state", "organization_zip_code"]],
        on="record_id", how="left",
    )
    final_decision = df["final_decision"].copy()
    final_reason = df["final_reason"].copy()

    target = df[df["final_reason"] == TARGET_REASON]
    print(f"Cross-checking {len(target)} records across "
          f"{target['duplicate_group_id'].nunique()} website-confirmed groups via NPPES...")

    reverted_groups = 0
    for group_id, group in target.groupby("duplicate_group_id", sort=False):
        npis = {}
        for idx, row in group.iterrows():
            match = resolve_record(row)
            if match:
                npis[idx] = match["npi"]

        distinct_npis = set(npis.values())
        if len(distinct_npis) >= 2:
            reverted_groups += 1
            for idx in group.index:
                final_decision.loc[idx] = "needs manual review"
                final_reason.loc[idx] = "NPI shows different registered entities at shared address"

    df["final_decision"] = final_decision
    df["final_reason"] = final_reason
    df.to_csv(OUTPUT_CSV, index=False)

    print(f"Reverted {reverted_groups} group(s) back to manual review")
    print(df["final_decision"].value_counts().to_string())
    print(f"\nWrote {OUTPUT_CSV}")


if __name__ == "__main__":
    sys.exit(main())
