# Automated Comptroller Verification

**Operations manual.** A guide to running the verification tool for existing REDCap records.

| | |
|---|---|
| **Tool file** | `automated_comptroller_verifier.py` |
| **Applies to** | Comptroller REDCap records |
| **Audience** | anyone running or reviewing this tool |

## Contents

1. [What this tool is](#1-what-this-tool-is)
2. [What it checks](#2-what-it-checks)
3. [Limitations and Safety Measures](#3-limitations-and-safety-measures)
4. [Setup Requirements](#4-setup-requirements)
5. [Running it](#5-running-it)
6. [Command options](#6-command-options)
7. [Output and Results](#7-output-and-results)
8. [Record Summary Spreadsheet Column Descriptions](#8-record-summary-spreadsheet-column-descriptions)
9. [Reviewing the results](#9-reviewing-the-results)
10. [Questions & troubleshooting](#10-questions--troubleshooting)

---

## 1. What this tool is

This Python application reviews existing REDCap records and uses the website listed in each record as the main source for verifying and updating selected information.

The application crawls the organization or facility website associated with each REDCap record and looks for current publicly available information. It then compares the information found on the website with the information already stored in REDCap.

The application checks selected fields such as:

- Phone number
- Fax number
- Email address
- Website address
- Organization or facility name
- Physical address
- Social media links

When the application finds clear and reliable information on the website, it attempts to automatically update the corresponding REDCap field.

However, website information is not always clear or consistent. For example, a website may list multiple phone numbers, addresses, or email addresses, and the application may not be able to determine with confidence which one should be used. In these situations, the application does not automatically update the field. Instead, it flags the record for manual review or further action.

> **In one sentence**
> It finds what's out of date, fixes what it's sure about, and hands everything else to a human with the details already laid out.

> **Note**
> REDCap has other fields on each record that this tool does not check. Those require a consent form filled out by the organization, so they can't be confirmed from a public website.

## 2. What it checks

Six things, per record, compared against the organization's own website. If what's on the website is different from what's already in REDCap, the tool updates it:

| | |
|---|---|
| **Phone number** | Updated when the website shows a different number than what's on file. |
| **Fax number** | Updated when the website shows a different number than what's on file. |
| **Email address** | Updated when the website shows a different email than what's on file. |
| **Mailing address** | Street, city, state, ZIP, and county. Updated when the website shows a different address than what's on file. |
| **Social media** | Facebook, Instagram, and Twitter/X links. Updated when the website shows something different. |
| **Facility name** | Flagged if the website displays a different name, but never changed automatically (see below). |

The application can also identify when an organization's website lists additional branch locations beyond the location associated with the current REDCap record. This is common for hospital systems, multi-clinic providers, and organizations with multiple offices.

Any additional locations found are placed in a separate worksheet for manual review before any further action is taken.

## 3. Limitations and Safety Measures

The following rules are built into the application to help make it safe to use with real REDCap data:

- **It never guesses.** If a website shows more than one possible phone number, fax number, email, or address and it can't tell which belongs to this record, nothing is changed. The record is flagged for manual review instead, and none of that record's other fields are auto-updated either (see [Notes](#8-record-summary-spreadsheet-column-descriptions) below).
- **It never automatically changes the Facility/Organization Name.** Even if the application finds a different organization or facility name on the website with high confidence, the name is not updated automatically. A name change requires human review and confirmation.
- **It never overwrites a recent change made by another user.** Before updating any field, the application checks the current live value in REDCap. If that value has changed since the original spreadsheet was exported, the application skips that field instead of overwriting the newer information.

## 4. Setup Requirements

Three things need to be in place before the tool can run.

### 4.1 Python and Required Packages

You need **Python 3.9 or later** installed.

Install the required packages with:

```
pip install pandas requests openpyxl
```

`playwright` is optional, used only for a small number of JavaScript-heavy sites. If you want that support, also run:

```
pip install playwright
playwright install chromium
```

Ask your technical contact to set this up once if it isn't already.

### 4.2 The REDCap export spreadsheet

You need the current REDCap data export (an `.xlsx` file, e.g. `ComptrollerProject20_DATA_LABELS_2026-04-16_1051.xlsx`) saved somewhere accessible. This is what the tool reads each record's current on-file information from; it is never written back to.

### 4.3 The REDCap credentials file

Create a file named `redcap_config.py` in the same folder as the tool, containing:

```python
config = dict(
    api_url="https://your-redcap-server/api/",
    api_token="YOUR-REDCAP-API-TOKEN-HERE",
)
```

- Get the URL and token from REDCap's own **API** page, for the specific project you want the tool to update. Each token is scoped to exactly one project.
- This file is required for the tool to write confirmed changes back into REDCap (see `--apply` below).

> **Handle this file like a password**
> `redcap_config.py` contains a live credential for your REDCap project. It is already excluded from version control (via `.gitignore`), and it should never be emailed, copied into chat, or shared outside the people who need it.

## 5. Running it

Every run needs a record ID (or several) and the spreadsheet to read from.

**Basic shape:**

```
python automated_comptroller_verifier.py <record id(s)> --xlsx <spreadsheet file>
```

**Common examples:**

| You want to… | Command |
|---|---|
| Preview one record | `python automated_comptroller_verifier.py 400 --xlsx export.xlsx` |
| Preview a range of records | `python automated_comptroller_verifier.py 3-50 --xlsx export.xlsx` |
| Preview a specific mix of records | `python automated_comptroller_verifier.py 3 5 8 12-15 --xlsx export.xlsx` |
| Actually write the confirmed changes to REDCap | `python automated_comptroller_verifier.py 400 --xlsx export.xlsx --apply` |

### Dry run vs. Apply

By default, the tool runs as a **dry run**: it figures out everything it would change, but never actually writes to REDCap. Add `--apply` to let it write the changes it's confident about. The tool always tells you up front which mode it's in:

```
DRY RUN: no changes will be written to REDCap (pass --apply to actually write them).
```

With `--apply`, before touching anything, the tool looks up and shows you exactly which REDCap project the configured credentials point to, then asks you to confirm:

```
========================================================================
--apply is set: this run WILL write changes to REDCap.
  REDCap project : Comptroller Automation Test Project (Project ID: 3282)
  API URL        : https://redcap.sph.uth.edu/api/
========================================================================
Proceed with writing changes to this REDCap project? [y/N]:
```

Answer `y` to continue, or anything else (including just pressing Enter) to cancel. A cancelled run makes no changes to REDCap at all. This confirmation appears once per run, before any record is processed, specifically so you can double-check you're pointed at the right project before anything is written.

> **Recommended habit**
> Run without `--apply` first, review the summary spreadsheet, and only re-run with `--apply` once you're comfortable with what it's about to do.

## 6. Command options

Everything you can add after `automated_comptroller_verifier.py`:

| Option | Required? | What it does |
|---|---|---|
| `<record id(s)>` | Yes | One or more Record IDs. Single ids, ranges (`3-10`), or a mix (`3 5 8-10`) all work. |
| `--xlsx <file>` | Yes | Path to the REDCap export spreadsheet to read records from. |
| `--apply` | No | Actually writes confirmed changes to REDCap. Without it, every run is a preview only. |
| `--outdir <folder>` | No | Where the results workbook is saved. Defaults to a folder named `output`. |
| `--skip-new-locations` | No | Skips checking whether the organization's website lists other branch locations. This check runs by default; add this flag to turn it off (useful for a very large multi-location provider, since it takes longer). |
| `--output-xlsx <file>` | No | Overrides where the combined results workbook is saved, instead of the automatic default name. |

## 7. Output and Results

One line on screen per record, plus one combined results workbook.

### Console Output

The console displays a header showing the range of record IDs included in the run, followed by one line for each record. This simple format is intentional so that large batches remain easy to scan.

Each record line includes the record ID, organization name, and the amount of time required to process that record. A final line at the end of the run reports the total time to process every record in the batch.

```
Processing records from 3 to 9999 (4 record(s) total)...

Processing Record    3: ADDISON PAIN AND REGENERATIVE MEDICINE (33.2s)
Processing Record    5: KND BEHAVIORAL CLINIC PLCC (2.7s)
Processing Record  400: INTERVENTIONAL PAIN AND WELLNESS CENTER PLLC (8.0s)
Processing Record 9999: SKIPPED => ID NOT FOUND (0.0s)

Total time to process 4 record(s): 44.9s
```

`SKIPPED => ID NOT FOUND` means the user requested verification for a record ID that is not present in the `.xlsx` file exported from REDCap for this application to process.

### Combined Results Workbook

`output/record_summary_redcap_id_<first>_to_<last>_TIME_<timestamp>.xlsx` is a single Excel file with two tabs:

- **Record Summary** (the tab that opens by default, and the main one to review): one row per record that was actually found in the spreadsheet, summarizing everything the tool found. Covered field-by-field in the next section.
- **Other Locations**: every extra location an organization's website mentions (another branch, a second clinic, and so on) that isn't this record's own address. These are **never** added to REDCap automatically; a person reviews this tab and decides whether/how to add them as new records.

## 8. Record Summary Spreadsheet Column Descriptions

This is the file to open first. Every column, explained in plain English.

| Column | What it means |
|---|---|
| `redcap_record_id` | The REDCap Record ID this row is about. |
| `website_changed` / `website_old` / `website_new` | `website_changed` is **Yes** only when the website on file redirects to a genuinely different domain (not just an http/https or "www." difference). `website_old` is always the URL on file; `website_new` only appears when there's a real redirect to report. |
| `phone_changed` / `phone_old` / `phone_new` | `phone_changed` is **Yes** only when the website clearly showed one new phone number that could be safely updated. `phone_old` is always the number on file; `phone_new` only appears when there's a confirmed new number. |
| `fax_changed` / `fax_old` / `fax_new` | Same idea as phone, for the fax number. |
| `email_changed` / `email_old` / `email_new` | Same idea as phone, for the email address. |
| `social_changed` / `social_old` / `social_new` | Same idea as phone, for Facebook/Instagram/Twitter links (LinkedIn is checked and mentioned in the notes, but REDCap has no field to store it in, so it's never counted here), except `social_old` only shows a value when there's something to compare against, since it can span up to three different platforms at once. |
| `address_changed` / `address_old` / `address_new` | Same idea as phone, for the mailing address. `address_old` is always the full street/city/state/ZIP on file in one line; `address_new` only appears when there's a confirmed change. |
| `name_changed` / `name_old` / `name_new` | `name_changed` is **Yes** if the website appears to display a different facility name. `name_old` is always the name on file; `name_new` only appears when a possible change was detected. This is **never** written to REDCap automatically; it's always left for a person to confirm, since a name change is a business decision. |
| `notes` | The most important column. See below. |
| `change_required` | **Yes** if this record needs any action at all, whether automatic or manual. **No** means the tool found nothing worth changing. |
| `manual_review_required` | **Yes** if a person needs to look at the notes and decide by hand: the website showed more than one possible value for some field, a possible name change, no usable website at all, or (under `--apply`) REDCap itself refused or only partially accepted the write. **No** means everything found was clear enough to act on automatically (or nothing needed changing at all). |
| `updated_redcap_record` | **Yes** only if this run actually wrote a change into REDCap for this record. Always **No** during a dry run, always **No** whenever `manual_review_required` is Yes, and still **No** if the REDCap write itself failed (see below). |
| `found_multiple_locations` | **Yes** if the organization's website lists other branch/office locations besides this one. See the Other Locations tab for the details. These are never added to REDCap automatically. |

### Reading the `notes` column

It always takes one of these forms:

- **`Record up to date: No Change Required`**: The website agrees with what's on file (or nothing useful was found on it). Nothing to do for this record.
- **`Updated: …`**: Lists exactly which fields were changed and their before/after values. During a dry run this reads **"Potential Update:"** instead, since nothing was actually written yet.
- **`Manual Review Required: …`**: The tool found something a person needs to decide: multiple possible phone numbers/fax numbers/emails/addresses on the site, and/or a possible name change. It lists the field(s) involved and, where relevant, the actual candidate values found on the website.
- **`Manual Review Required: No website address found…`**: The record has no website on file, or the one on file couldn't be reached (broken link, timeout, or an error page even after a retry). Nothing else can be checked for this record until the website field itself is fixed.
- **`Manual Review Required: Failed to update…`**: Only possible under `--apply`. REDCap either rejected the write entirely (the reason from REDCap is included) or accepted only part of it, with one or more fields skipped, usually because REDCap's current value no longer matched what the change was based on. Either way, `updated_redcap_record` reflects only what was actually written, and this note says exactly what still needs to be applied by hand.

> **Important: manual review blocks the whole record**
> When a record needs manual review for even one field, **none** of that record's fields are auto-updated, not even fields the tool was otherwise confident about. Every potential change for that record, including the ones that were unambiguous on their own, is listed in the notes for a person to act on by hand instead.

## 9. Reviewing the results

A simple pass through the Record Summary spreadsheet:

1. Open the `record_summary_….xlsx` workbook for the run. It opens on the "Record Summary" tab.
2. Filter or sort by `change_required` = **Yes** to see only the records with anything to act on.
3. For any row where `manual_review_required` = **Yes**, read the `notes` column, check the organization's website and REDCap side by side, and update REDCap by hand with the correct value.
4. For any row where `updated_redcap_record` = **Yes**, REDCap has already been updated automatically for that record. A matching note was also added to that record's Additional Comments in REDCap, so there's a record of exactly what changed and why.
5. Separately, switch to the "Other Locations" tab for any additional branch/office locations worth adding to REDCap as new records.

## 10. Questions & troubleshooting

**The tool says a Record ID wasn't found in the spreadsheet.**
That ID doesn't exist in the `.xlsx` file you pointed `--xlsx` at. Double-check the ID and that you're using the intended export file. No report or spreadsheet row is created for a missing ID.

**A record has no website on file.**
The tool reports this and stops there for that record. It never searches the internet to find one on its own.

**Can I re-run the tool safely?**
Yes. Every run re-checks REDCap's current live values immediately before writing anything, so it won't overwrite a change someone else already made in the meantime.

**Where do I get a REDCap API token?**
From REDCap's own **API** page for the specific project you want this tool to update. Each token only works for one project.

**I don't have `redcap_config.py` set up. Can I still use the tool?**
Yes. Every dry-run preview works without it. That file is only required the moment you add `--apply`.

**What happens if the REDCap token expires or is invalid?**
The tool checks this before touching anything, at the project-confirmation step described in [Running it](#5-running-it): if the token is already bad at that point, it stops immediately with a message telling you to update `redcap_config.py`, before any record is processed. If a token happens to expire partway through a long `--apply` run, the tool recognizes REDCap's rejection, prints the same message, and stops there rather than continuing to fail on every remaining record one by one. Records already processed before that point still get their row written to the summary spreadsheet, so nothing already done is lost. Just re-run the rest once `redcap_config.py` has a valid token.

---

*Automated Comptroller Verification · internal operations manual*
