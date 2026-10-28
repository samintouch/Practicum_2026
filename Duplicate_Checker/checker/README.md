# checker

Checks the possible duplicate groups produced by `Comptroller.R` and suggests, for each
record, whether it is a true duplicate. It also checks each record's website to see whether
the website shows the address we have in REDCap. Everything it produces is a suggestion to
review, not a final decision.

## Requirements

- Python 3.10+ with `pandas`, `openpyxl`, `requests`, `lxml`, `playwright`
  (`pip install -r checker/requirements.txt`, then `playwright install chromium`)
- The app uses the AI by default: [Ollama](https://ollama.com) running locally with `qwen2.5:7b`
  (`ollama pull qwen2.5:7b`). If Ollama isn't available, the app warns and continues without it;
  pass `--no-ai` to skip it outright. Either way, the unclear cases are marked "Manual review required".
- Optional: Playwright's Chromium browser. Without it, websites that block plain downloads are
  reported as unreachable.

Step-by-step setup instructions for a new computer are in `../Duplicate_Checker_Readme.md`.

## Running

From the `Duplicate_Checker` folder, the one that contains the `checker` folder. Two inputs are required:

| Option | File | Used for |
|---|---|---|
| `--data` | the Comptroller REDCap export, `.xlsx` or `.csv` (e.g. `ComptrollerProject20_DATA_LABELS_2026-04-16_1051.xlsx` / `.csv`) | each record's name, address, phone and website |
| `--dup` | the duplicates workbook, e.g. `Comptroller_Updates_Syeda_7.6.26_10-08-2026.xlsx` | the groups to check (its "Assigned Duplicate Groups" sheet). Its "Tracking Duplicates" tab is filled in; a "New Records to add to redcap" sheet, if present, is dropped from the saved copy - the app never adds new records |

```
python -m checker --data ComptrollerProject20_DATA_LABELS_2026-04-16_1051.xlsx --dup Comptroller_Updates_Syeda_7.6.26_10-08-2026.xlsx
```

Add any of these:
```
--group-ids 81,9            only some groups
--limit 20                   only the first 20 records across all groups - quick test runs
--no-ai                      skip the local AI (on by default); unsettled pairs are marked for manual review
--no-web                    skip websites
--verify-all-websites       also check addresses of records the rules settled
--model llama3.1:8b         use a different Ollama model
--no-browser                don't use the headless browser for blocked or script-built websites
--debug                     also save the detailed results workbook
--apply                     record the decisions in REDCap (see below)
```

Outputs (all written to an `output` folder inside the folder you run from; it's created if missing):
- **`<duplicates file>_app_review_<YYYY-MM-DD_HHMM>.xlsx`**: a copy of the duplicates workbook with the
  Tracking Duplicates tab filled in (see below). Each run makes a new file named with the run's date and
  time. The original file is not changed. Whenever `--apply` changes REDCap, this same workbook also
  gets a **"REDCap Updates"** tab, right after "Tracking Duplicates": what was changed, with each
  record's old values so it can be undone.
- **`Duplicate_Check_Results_<date>.xlsx`** (only with `--debug`; name it with `--out`): the full
  details: every record, every pair compared and why, every website check, and the REDCap changes
  `--apply` would make.

Run `python -m checker --help` for all options. Nothing is cached between runs: every run visits
the websites and asks the AI fresh. Within one run, a website shared by several records is only
downloaded once.

## Tracking Duplicates tab

Filled in the same layout as manual reviews, one row per record in group order (except an
all-"Not a duplicate" group - see below). The app puts **"Duplicate Group Id"** first and **"Redcap
ID"** second (swapped from the manual-review template's own order, where "Redcap ID" came first),
adds **"Status"** right after those two, **"notes from automation"** after the template's own "notes
(add to redcap)" column, then **"Website Visited"** and, as the very last column, **"AI assisted"** -
the template's own columns in between are never reordered or touched beyond what's documented here.
The old name/new name, old address/new address, mailing address and other-changes columns from that
template are removed from this tab if present (`REMOVED_COLUMNS` in `tracking.py`) - a separate
application, **Comptroller Automation**, owns those now, not the Duplicate Checker:

| Result | "potential duplicate with?" | "which is true duplicate?" | "most complete record" |
|---|---|---|---|
| Record kept | its duplicate IDs | its duplicate IDs | its own ID |
| Duplicate records | *no row of their own; listed on the kept record's row* | | |
| Not a duplicate | *blank - see the "Status" column instead* | | |
| Undecided | `Manual review required - possible duplicate with …` | | |

**"Duplicate Group Id"** (added by the app, first column) is the group number from the
`Comptroller.R` scan.

**"Redcap ID"** (added by the app, second column) lists every record ID in the row's group, not just
one - e.g. a kept record's row shows `145, 5015`, and so does its duplicate's row, if it had one.

**One row per group, not per record, when nothing in the group needs a person's attention.** If
every record in a `Comptroller.R` group comes back "Not a duplicate" - no cluster, nothing undecided -
the tab gets a single row for the whole group instead of one per record (`build_rows()` in
`tracking.py`). "Redcap ID" and "records that are not actual duplicates" both list every record ID in
the group, "Status" is "Not Duplicate", "AI assisted"/"Website Visited" are "Yes" if *any* record in
the group used that method, and "notes from automation" is one sentence covering the whole group, e.g.
`Not a duplicate. All 3 records in this group (3035, 205, 2347) were compared and found to be
different facilities. Decided by rule check.` A group with even
one "Duplicate" or "Needs review" record is left as one row per record, as before.

**"records that are not actual duplicates"** lists the other records in the same group that this
record was found *not* to duplicate (pairs still undecided are left out). For example, row 1691
(not a duplicate) shows `237, 185, 310`.

Two notes columns:
- **"notes (add to redcap)"**: exactly the comment `--apply` adds to REDCap's Additional Comments
  for that record, e.g. `From automation: \nId: 5015 is duplicate of Id: 145` on the row of record 145. Blank for
  "Not a duplicate" and "Manual review required" rows, because `--apply` doesn't change those records.
- **After an `--apply` run**, the kept record's row also shows what happened in REDCap, for that record
  and its duplicates. Records that were not updated get a line in "notes (add to redcap)" with the reason,
  e.g. `Record 5015 - record not found in REDCap`, or, when REDCap already had the right value,
  `Record 182 - already marked Not Duplicate in REDCap on 07-15-2026` (the date is when it was last
  validated in REDCap; "Duplicate"/"Not Duplicate" reflects REDCap's "Is this a duplicate record?"
  field, not the raw Yes/No).
  Records that were updated are listed at the end of "notes from automation", e.g. `REDCap updated on
  10/08/2026: record 13 (duplicate=No, Validation=Yes, ...); record 527 (...)`. A failed update is noted
  there too. The tab is saved before REDCap is contacted and updated again afterwards.
- **"Status"** (added by the app), colored: **Duplicate** (blue) for a kept record's row, **Not
  Duplicate** (purple) for "Not a duplicate" rows and **Manual Review Required** (orange) for "Manual
  review required" rows. After `--apply`, the kept record's row instead shows **Duplicate - Updated
  Redcap** (green, as long as at least one record in the set was actually written), **Duplicate -
  Already marked in Redcap** (yellow, REDCap already matched), **Duplicate - Skipped** (grey,
  couldn't be safely identified) or **failed** (red) for that record and its duplicates. Each status's text is bold and
  a darker shade of its background, so the two previously-identical-looking greens (Not Duplicate vs.
  Duplicate - Updated Redcap) are now easy to tell apart.
- **"notes from automation"** (added by the app): how the app reached the decision. This covers the
  reason for each comparison, and whether it came from a rule check or the AI review.
- **"Website Visited"** (added by the app), colored: **Yes** (yellow) if `res.website.status` is one
  of `MATCH`, `SUITE_DIFFERS`, `DIFFERENT`, `OTHER_LOCATION`, `NOT_FOUND` or `UNREACHABLE` (an actual
  fetch was attempted), otherwise **No** (grey) - covering `NOT_CHECKED` (rules settled it first),
  `NO_WEBSITE`, `INVALID_URL`, and records never looked up at all (e.g. with `--no-web`).
- **"AI assisted"** (added by the app, last column), colored: **Yes** (yellow) if "AI" appears in
  the record's `decided_by` (the local model contributed to this record's result), otherwise **No**
  (grey).

"validated date?" gets the date of the run for records the app decided (record kept, Not a duplicate),
and stays blank for "Manual review required" rows.

## How a group is checked

Every pair of records in a group is compared in three stages. Each stage only handles the pairs
the stages before it couldn't settle:

**1. Pre-screen (no internet, no AI).**
- **Address.** Addresses are standardized first ("6110 W PARKER RD" = "6110 West Parker Road",
  "#409" = "STE 409"), then compared:
  SAME, SAME_BUILDING (different suite), SAME_STREET (different building number), DIFFERENT.
- **Name.** Legal suffixes (LLC, PLLC, Inc...) are ignored. Generic words (health, center, clinic...)
  don't count as a match on their own. Result: SAME, SIMILAR, DIFFERENT.
- **Phone.** Same phone number or not.

Rules:
- different name + different address → **Not a duplicate**
- different name + same building, different suite, different phone → **Not a duplicate**
- same/similar name + same address → **Duplicate**
- an individual provider's name (e.g. "DR JANE DOE", "JOHN SMITH MD") + an organization at the exact
  same address → **Needs review** (past manual reviews went both ways)

**2. Websites.** These are fetched only for records still undecided or marked "Needs review".
Rule: if the names match, and one record's own website doesn't show that record's address but does
show the other record's address (with 3 or fewer addresses on the site), the pair goes to
**Needs review**. This can mean the facility moved (duplicate), but the full run showed it usually
means a multi-site organization whose website only lists its main location (not a duplicate).

**3. AI (on by default; qwen2.5:7b)** judges what's left. With `--no-ai`, these pairs are marked
"Manual review required" instead. It gets the comparison results, the website findings, and whether
one record's website shows the other's address or phone number. Answers with confidence below 0.6
become "Needs review".

Records judged duplicates are merged into one cluster. The record with the highest
completeness score is suggested as the primary (record to keep).

## Website check

For each record whose website is needed (see stage 2 above), the app opens the site. If the address isn't on the home page, it
also opens up to 3 contact/location/about pages.

The site is first fetched with a plain download, which is fast. A headless Chrome browser
(Playwright) is used only when that download is blocked or times out, or when the page shows no
address, which can mean the address only appears after the page's scripts run. On the full
285-group run, the browser recovered 14 of the 63 websites that had failed. It isn't used for dead
domains, missing pages (404) or certificate errors (usually a wrong link). Turn it off with `--no-browser`.

Possible results:

| Status | Meaning |
|---|---|
| MATCH | Website shows the REDCap address |
| SUITE_DIFFERS | Same building, different suite on the website. The change is suggested |
| DIFFERENT | Website shows a different address in the same city/zip. The change is suggested |
| OTHER_LOCATION | Website only shows addresses in other cities (likely a head office or other branch). No change suggested |
| NOT_FOUND | No street address on the website |
| UNREACHABLE | Site down, invalid certificate, or blocks automated access |
| NO_WEBSITE / INVALID_URL | Nothing usable in the website field |
| NOT_CHECKED | The rules settled the record without needing the website (use `--verify-all-websites` to check it anyway) |

The app also flags websites that never mention the organization's name, which can mean the link
belongs to a different business.

## Updating REDCap (`--apply`)

Without `--apply`, nothing is sent to REDCap. The report's **REDCap Updates** sheet shows what
would be changed. With `--apply`:

```
python -m checker --data <export.xlsx> --dup <duplicates.xlsx> --apply          # asks y/n right at the start
python -m checker --data <export.xlsx> --dup <duplicates.xlsx> --apply --yes    # no prompt
```

For every set of records found to be the same facility (e.g. 145 and 5015):

| | Duplicate records (the "which is true duplicate?" IDs, e.g. 5015) | Record kept (the most complete record, e.g. 145) |
|---|---|---|
| Is this a duplicate record? (`duplicate`) | **Yes** | **No** |
| Validation (`validated`) | **Yes** | **Yes** |
| Validation Date (`validation_date`) | **today** | **today** |
| Additional Comments (`general_comments`) | `From automation: duplicate with 145` | `From automation: \nId: 5015 is duplicate of Id: 145` (several duplicates: `From automation: \nId: 256, 312 are duplicate of Id: 145`) |

The new comment goes at the **top**. If the record already had a comment, it's kept underneath,
after a line showing the record's old validation date:

```
From automation: duplicate with 13
Previous note: 07-26-2026
The facility appears to be permanently closed per the Google listing.
```

Records the checker calls "Not a duplicate" or "Needs review" are not changed.

Before writing, the app re-reads each record from REDCap. The checker's decision always wins over a
*conflicting* one: whatever "Is this a duplicate record?" is already set to - blank, or a
different answer a reviewer entered earlier - it's written over with the app's own Yes/No, comment
and validation update (`action = UPDATE`). Nothing is sent, and the record's status is
**"no update required"** (`action = NO_CHANGE`), when REDCap's "duplicate" value already matches
exactly what the app found - there's nothing to change. The only things that skip a record (and
its whole set, to be safe; `action = SKIP`) are:
- **a record that no longer exists** in REDCap, or
- **a record whose name has changed a lot** since the export was taken (a sign it may not be the
  same record any more, so it's left for a person to re-check).

After writing, the app reads the records back to confirm the changes were stored. The REDCap Updates
sheet records each record's old values (duplicate, validation, validation date, comments), so any
change can be undone.

The REDCap URL and token are read from `redcap_config.py` (`config = dict(api_url=..., api_token=...)`)
or from the `REDCAP_API_URL` / `REDCAP_API_TOKEN` environment variables. The token is on the
project's **API** page in REDCap (left menu, under *Applications*). The project name is shown before
you confirm. Check it's the project you mean to change. Keep `redcap_config.py` private; it
contains your API token.

**When REDCap can't be reached.** REDCap is only contacted with `--apply`, and the connection is
checked before anything else runs. If the token or address is missing, malformed, rejected or wrong,
or the network is down or times out, the run stops with exit code 2 and a message saying what's wrong
and to run without `--apply` until it's fixed. If REDCap fails part-way through the update, the run
also stops with exit code 2. The Tracking Duplicates tab is written before any REDCap update, so it's
never lost. The old values of every record being changed are saved to the **"REDCap Updates"** tab of
the app-review workbook.

## REDCap Updates tab

Every `--apply` run that changes REDCap adds a **"REDCap Updates"** sheet to the app-review workbook
(`<duplicates file>_app_review_<date>_<time>.xlsx`), right after "Tracking Duplicates". It has one row
per record the app planned to change, with these columns:
- the record's role (record kept, or duplicate of …) and what was set: duplicate Yes/No, validation,
  validation date and the comment;
- **status**: `updated`, `skipped` or `failed`, with a note explaining it;
- the record's **old values**: duplicate, validation, validation date and the full old comment.

The log is written before anything is sent to REDCap, and again with the final status afterwards.
To undo a change, put the old values back into the record in REDCap.

## Output workbook

| Sheet | Contents |
|---|---|
| Summary | Counts and run settings |
| Record Recommendations | One row per record: recommendation, reason, website result, suggested address change, draft note |
| Pair Comparisons | Every pair compared and why it was decided |
| Website Checks | What was found on each website |
| Tracking Duplicates (Draft) | Pre-filled in the same columns as the manual tracking tab |
| REDCap Updates | Records to be marked duplicate. With `--apply`, also the result and the old values |
