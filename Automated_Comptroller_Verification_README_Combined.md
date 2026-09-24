# Automated Comptroller Verification

**Operations Manual:** A guide for understanding, running, and reviewing the REDCap automation tool for existing records, and for the new location records it can create along the way.

| | |
|---|---|
| **Tool file** | `automated_comptroller_verifier.py` |
| **Applies to** | Comptroller REDCap records |
| **Audience** | Project staff, supervisors, advisors, and authorized users who run or review the tool |

## Contents

1. [Introduction](#1-introduction)
2. [What Is Automation?](#2-what-is-automation)
3. [Why Are We Using Automation?](#3-why-are-we-using-automation)
4. [Epidemiology and Public Health Relevance](#4-epidemiology-and-public-health-relevance)
5. [What Program Performs the Automation?](#5-what-program-performs-the-automation)
6. [What This Tool Does](#6-what-this-tool-does)
7. [What It Checks](#7-what-it-checks)
8. [Limitations and Safety Measures](#8-limitations-and-safety-measures)
9. [Setup Requirements](#9-setup-requirements)
10. [Running the Tool](#10-running-the-tool)
11. [Command Options](#11-command-options)
12. [Output and Results](#12-output-and-results)
13. [Record Summary Spreadsheet Column Descriptions](#13-record-summary-spreadsheet-column-descriptions)
14. [Reviewing the Results](#14-reviewing-the-results)
15. [Benefits of the Automation](#15-benefits-of-the-automation)
16. [Drawbacks and Limitations](#16-drawbacks-and-limitations)
17. [Data Quality and Safety Rules](#17-data-quality-and-safety-rules)
18. [Simple Technical Flow](#18-simple-technical-flow)
19. [Questions and Troubleshooting](#19-questions-and-troubleshooting)

---

## 1. Introduction

REDCap is used to store information about organizations and facilities, including organizations that provide Recovery Support Services (RSS). As the database grows, some records may become outdated, incomplete, or duplicated. New organizations may also need to be added after they are identified and verified.

This project uses automation to reduce repetitive work involved in reviewing and maintaining REDCap data. The automation is designed to support the reviewer, not replace human judgment. Routine tasks can be completed by the program, while unclear or important decisions are flagged for manual review.

This manual describes the purpose, public health relevance, software, workflow, benefits, limitations, safety measures, and human-review steps for the REDCap automation project.

## 2. What Is Automation?

Automation means using a computer program to complete repeated tasks by following a set of defined rules. Instead of manually copying, comparing, formatting, and entering the same types of information many times, a program can perform these tasks in a consistent way.

For this project, automation can read REDCap export data, review organization websites, compare website information with existing REDCap information, identify possible changes, prepare records for review, update approved information through the REDCap API, and create a processing log.

Automation does not mean that every decision is made by the computer. When information is unclear or requires judgment, the program sends the record for manual review.

## 3. Why Are We Using Automation?

The REDCap database may contain many organization records. Reviewing every record manually can take a large amount of time and may lead to inconsistent formatting or data-entry errors. Automation can complete repetitive steps more quickly and consistently, allowing the reviewer to spend more time on records that require judgment.

The main reasons for using automation are:

- Reduce repetitive manual work.
- Save time when reviewing existing REDCap records.
- Help identify information that may be outdated or different from the organization's current website.
- Improve consistency when comparing phone numbers, addresses, email addresses, websites, and social-media links.
- Help identify additional organization locations for separate review.
- Reduce copying and typing errors.
- Create a clear record of what was reviewed, what changed, and what still requires manual review.

## 4. Epidemiology and Public Health Relevance

A reliable directory of Recovery Support Services is a useful public health data resource. Public health professionals need accurate information about where services are located and how organizations can be contacted. Duplicate, outdated, or incomplete records can reduce the quality of the database and make it harder to understand the availability and distribution of services.

From an epidemiology perspective, data quality is important because public health analyses and decisions depend on accurate and consistent information. Maintaining organization-level data can improve later descriptive analyses, such as examining where services are available, identifying possible geographic gaps, and supporting planning or referral activities.

This automation does not measure health outcomes or prove that Recovery Support Services improve health outcomes. Its purpose is to improve the quality and usability of the service-resource data used for public health work.

The project can support public health by:

- Improving the completeness and consistency of service-resource data.
- Reducing duplicate or outdated information.
- Helping keep organization contact and location information current.
- Supporting more reliable descriptions or mapping of service availability.
- Allowing staff to spend less time on repetitive data checking and more time on review, analysis, and other public health activities.

## 5. What Program Performs the Automation?

The automation is performed by a Python program. The main application file is `automated_comptroller_verifier.py`. Python controls the workflow, reads the REDCap export spreadsheet, reviews organization websites, compares information, applies the program's safety rules, creates result files, and, when authorized, sends approved changes to REDCap through the REDCap API.

The main components are:

- **Python:** controls the automation workflow and performs the data processing.
- **pandas and openpyxl:** support reading the REDCap Excel export and creating output workbooks.
- **requests:** supports communication with organization websites and the REDCap API.
- **Playwright:** optional support for a small number of websites that depend heavily on JavaScript.
- **REDCap:** stores the organization and facility records.
- **REDCap API:** provides the controlled connection used to write clear changes to REDCap when the user runs the tool with `--apply`.
- **Organization websites:** provide the current public information used for verification.

The current verification tool uses the website already stored in each REDCap record. It does not automatically search the internet for a replacement website when a website is missing. A missing or unusable website is reported for manual review.

## General Coding Process

The automation was developed in small steps so each part could be tested before use with real REDCap records.

1. Identify the REDCap fields the program needs to review or update.
2. Read the current REDCap export spreadsheet into Python.
3. Clean and standardize values such as organization names, addresses, phone numbers, state abbreviations, ZIP codes, and website URLs.
4. Compare the REDCap information with information available on the organization website listed in the record.
5. Classify each result as no change, potential update, manual review, or another condition requiring attention.
6. Do not guess when information is unclear; send uncertain cases for human review.
7. Use dry-run mode to preview proposed changes without writing to REDCap.
8. When `--apply` is intentionally used, confirm the REDCap project, re-check the current live value, and update only fields that meet the safety rules.
9. Save results in the output workbook so the reviewer can see what was checked, what changed, and what still requires review.
10. Test a small number of records before processing a larger batch.

## General Tasks That Can Be Automated

The application can automate many repetitive data-quality tasks:

- Read existing organization information from the REDCap export spreadsheet.
- Standardize and compare selected values.
- Compare phone and fax numbers while ignoring formatting differences.
- Identify email addresses available through website links.
- Compare mailing addresses and selected social-media links.
- Detect clear website redirects that may indicate a website change.
- Identify possible facility-name changes and send them for manual review.
- Identify some additional branch or office locations listed on an organization's website.
- Create a comparison and review workbook.
- Write clear changes to REDCap through the API when `--apply` is used and all safety rules are met.
- Record processing results and notes for review.

## Tasks That Should Not Be Fully Automated

Some decisions require context and human judgment. The application can provide supporting information, but a person should make the final decision when needed.

- Confirming a facility or organization name change.
- Resolving cases with several possible phone numbers, fax numbers, email addresses, or addresses.
- Determining whether organizations with similar names represent the same facility.
- Determining whether an organization moved or operates several locations.
- Deciding whether an additional location found on a website should be added as a new REDCap record.
- Resolving conflicting or unclear information from different parts of a website.
- Interpreting information that is not clearly stated on the website.
- Deciding whether existing REDCap information should be removed when it is not found online.

A matching name, address, or other value can provide useful evidence, but it is not always enough for a final decision. Human review helps protect the accuracy of the REDCap database.


## 6. What This Tool Does

This Python application reviews existing REDCap records. It uses the website listed in each record as the main source for checking selected information.

For each REDCap record, the application reviews the organization or facility website and looks for current public information. It then compares the information found on the website with the information already stored in REDCap.

The application checks selected fields such as:

- Phone number
- Fax number
- Email address
- Website address
- Organization or facility name
- Physical address
- Social media links

When the application finds one clear value on the website that differs from REDCap, it can prepare that field for an automatic update.

Website information is not always clear or consistent. For example, a website may list several phone numbers, addresses, or email addresses. If the application cannot determine which value belongs to the REDCap record, it does not make an automatic change. Instead, it flags the record for manual review.

REDCap contains additional fields that this tool does not verify. These fields require information from a consent form completed by the organization and therefore cannot be confirmed from a public website.

## 7. What It Checks

For each record, the application compares the following information with the organization's website. Clear differences can be updated when the program's safety rules are met:

| | |
|---|---|
| **Phone number** | Updated when the website shows a different number than what's on file. |
| **Fax number** | Updated when the website shows a different number than what's on file. |
| **Email address** | Updated when the website shows a different email than what's on file. |
| **Mailing address** | Street, city, state, ZIP, and county. Updated when the website shows a different address than what's on file. |
| **Social media** | Facebook, Instagram, and Twitter/X links. Updated when the website shows something different. |
| **Facility name** | Flagged if the website displays a different name, but never changed automatically (see below). |

The application can also identify when an organization's website lists additional branch locations beyond the location associated with the current REDCap record. This is common for hospital systems, multi-clinic providers, and organizations with multiple offices.

When an additional location has a clear name, a street number, and its own website, and doesn't appear to already exist elsewhere in REDCap, the application creates it as a brand-new REDCap record automatically (only when run with `--apply`; a dry run only previews what would be created). Anything less certain -- missing a name, a street number, or a website, or a likely duplicate of an existing record -- is placed in a separate worksheet for manual review instead, exactly as before. See [New Locations Created Automatically](#new-locations-created-automatically) for details.

## 8. Limitations and Safety Measures

The following rules are built into the application to help make it safe to use with real REDCap data:

- **It never guesses.** If a website shows more than one possible phone number, fax number, email, or address and it can't tell which belongs to this record, nothing is changed. The record is flagged for manual review instead, and none of that record's other fields are auto-updated either (see [Record Summary Spreadsheet Column Descriptions](#13-record-summary-spreadsheet-column-descriptions) below).
- **It never automatically changes the Facility/Organization Name.** Even if the application finds a different organization or facility name on the website with high confidence, the name is not updated automatically. A name change requires human review and confirmation.
- **It never overwrites a recent change made by another user.** Before updating any field, the application checks the current live value in REDCap. If that value has changed since the original spreadsheet was exported, the application skips that field instead of overwriting the newer information.
- **It never partly updates a record.** Before writing anything, the application checks the live REDCap value of every field it's about to change on that record. If even one no longer matches what the change was based on, nothing for that record is written -- not just the mismatched field. This avoids a record ending up with some fields updated and others silently left stale from the same run.
- **A new location is only created automatically when the application is confident, and only after checking for duplicates.** A brand-new REDCap record is only created for an additional location found on a website when it has an identifiable name, a street number, and its own website, and the application doesn't find what looks like the same location already tracked elsewhere in REDCap. Anything missing, or anything that looks like a possible duplicate, is left on the Other Locations worksheet for a person to decide instead.



## 9. Setup Requirements

The following setup is required before the tool can run.

### 9.1 Python and Required Packages

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

### 9.2 The REDCap Export Spreadsheet

You need the current REDCap data export (an `.xlsx` file, e.g. `ComptrollerProject20_DATA_LABELS_2026-04-16_1051.xlsx`) saved somewhere accessible. This is what the tool reads each record's current on-file information from; it is never written back to.

### 9.3 The REDCap Credentials File

Create a file named `redcap_config.py` in the same folder as the tool, containing:

```python
config = dict(
    api_url="https://your-redcap-server/api/",
    api_token="YOUR-REDCAP-API-TOKEN-HERE",
)
```

- Get the URL and token from REDCap's own **API** page, for the specific project you want the tool to update. Each token is scoped to exactly one project.
- This file is required for the tool to write confirmed changes back into REDCap (see `--apply` below).

The `redcap_config.py` file contains a live credential for the REDCap project. Keep this file secure. It is excluded from version control through `.gitignore` and should not be emailed, copied into chat, or shared with anyone who does not need access.

## 10. Running the Tool

Each run requires one or more Record IDs and the REDCap export spreadsheet.

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

By default, the tool runs in **dry-run mode**. It identifies possible changes but does not write anything to REDCap. Use `--apply` only when you want the tool to write clear changes to REDCap. The tool displays the selected mode before processing begins:

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

Enter `y` to continue. Any other response, including pressing Enter, cancels the run. A cancelled run does not make any changes to REDCap. This confirmation appears once before processing begins so the user can verify that the correct REDCap project is selected.

For safer use, first run the tool without `--apply` and review the summary spreadsheet. Use `--apply` only after the proposed changes have been reviewed and are appropriate.

## 11. Command Options

The following command options are available:

| Option | Required? | What it does |
|---|---|---|
| `<record id(s)>` | Yes | One or more Record IDs. Single ids, ranges (`3-10`), or a mix (`3 5 8-10`) all work. |
| `--xlsx <file>` | Yes | Path to the REDCap export spreadsheet to read records from. |
| `--apply` | No | Actually writes confirmed changes to REDCap. Without it, every run is a preview only. |
| `--outdir <folder>` | No | Where the results workbook is saved. Defaults to a folder named `output`. |
| `--skip-new-locations` | No | Skips checking whether the organization's website lists other branch locations. This check runs by default; add this flag to turn it off (useful for a very large multi-location provider, since it takes longer). |
| `--output-xlsx <file>` | No | Overrides where the combined results workbook is saved, instead of the automatic default name. |

## 12. Output and Results

The program displays one status line for each record and creates one combined results workbook.

### Console Output

The console displays a header showing the range of record IDs included in the run, followed by one line for each record. This simple format is intentional so that large batches remain easy to scan.

Each record line includes the record ID, organization name, and the amount of time required to process that record.

```
Processing records from 3 to 9999 (4 record(s) total)...

Processing Record    3: ADDISON PAIN AND REGENERATIVE MEDICINE (33.2s)
Processing Record    5: KND BEHAVIORAL CLINIC PLCC (2.7s)
Processing Record  400: INTERVENTIONAL PAIN AND WELLNESS CENTER PLLC (8.0s)
Processing Record 9999: SKIPPED => ID NOT FOUND (0.0s)
```

`SKIPPED => ID NOT FOUND` means the user requested verification for a record ID that is not present in the `.xlsx` file exported from REDCap for this application to process.

### Combined Results Workbook

`output/record_summary_redcap_id_<first>_to_<last>_TIME_<timestamp>.xlsx` is a single Excel file with three tabs:

- **Record Summary** (the tab that opens by default, and the main one to review): one row per record that was actually found in the spreadsheet, summarizing everything the tool found. Covered field-by-field in the next section.
- **Other Locations**: every extra location an organization's website mentions that wasn't confident enough to create automatically -- missing a name, street number, or website, or a possible duplicate of a record that already exists elsewhere in REDCap. Each row includes a `reason` column explaining why it landed here instead (always starting with "Failed to add new record:"). These are **never** added to REDCap automatically; a person reviews this tab and decides whether/how to add them.
- **New Records Added**: one row per brand-new REDCap record the tool actually created (only under `--apply`) for an additional location it was confident about. Includes the new REDCap Record ID, along with the name, address, county, and phone that were written. See [New Locations Created Automatically](#new-locations-created-automatically).

### New Locations Created Automatically

When an additional location is found on an organization's website, the tool decides where it belongs using three checks, in order:

1. **Does it have a name, a street number, and its own website?** If any of those is missing, the location goes to the Other Locations tab instead.
2. **Does it look like it might already be tracked as its own record elsewhere in REDCap?** The tool checks the rest of the REDCap dataset for an existing record at the same street number with a similar name. If one is found, the location goes to the Other Locations tab instead, noting which existing record it may already be.
3. **Did REDCap actually accept the new record?** If the write itself fails (a REDCap error, a network problem), the location falls back to the Other Locations tab with the failure reason included -- it is never silently dropped.

Only when a location passes all three checks does the tool create it as a brand-new REDCap record.

A new record is created with:

- Name, street, city, state, ZIP, phone, fax, and website, taken from the location found on the site.
- **County**, looked up automatically from the address (see below). Left blank if it can't be determined.
- **"Is this a completed record from Phase 1 data collection?"** set to **No**, since it's a brand-new record, not one from the original data collection.
- **"Is the organization/facility open or closed?"** set to **Open**, since it was just found live on the organization's own website.

Everything else on the REDCap form (services offered, accreditations, patient limits, and so on) is left blank, the same as any newly-added record, for staff to complete by hand.

Separately, whenever the tool successfully updates one or more fields on an **existing** record, that record's own "Is this a completed record from Phase 1 data collection?" flag is set to **Yes**. Creating (or failing to create) a new record for another location found on that same website has no effect on this flag, or on anything else about the original record's own row in the Record Summary tab.

**County lookup:** the tool looks up each new location's county automatically from its address, using two free public address-lookup services (the U.S. Census Bureau's geocoder first, then OpenStreetMap's Nominatim geocoder if the first doesn't find a match). Neither service covers every address perfectly -- if neither can match it, the county is left blank for a person to fill in by hand. It is never guessed.

## 13. Record Summary Spreadsheet Column Descriptions

The Record Summary tab is the main file to review. The columns are described below.

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
| `manual_review_required` | **Yes** if a person needs to look at the notes and decide by hand: the website showed more than one possible value for some field, a possible name change, no usable website at all, or (under `--apply`) REDCap itself refused the write or one of this record's own fields no longer matched what the change was based on. **No** means everything found was clear enough to act on automatically (or nothing needed changing at all). This is entirely about this record's own fields -- whether an additional location elsewhere on the same website was successfully created as its own new record (or failed to be) has no effect on this column; check the New Records Added / Other Locations tabs separately for that. |
| `updated_redcap_record` | **Yes** only if this run actually wrote a change into REDCap for this record. Always **No** during a dry run, always **No** whenever `manual_review_required` is Yes, and still **No** if the REDCap write itself failed (see below). |
| `found_multiple_locations` | **Yes** if the organization's website lists other branch/office locations besides this one, whether those ended up as brand-new REDCap records or on the Other Locations tab. It doesn't say which happened -- check the New Records Added and Other Locations tabs for the details. |

### Reading the `notes` column

It always takes one of these forms:

- **`Record up to date: No Change Required`**: The website agrees with what's on file (or nothing useful was found on it). Nothing to do for this record.
- **`Updated: …`**: Lists exactly which fields were changed and their before/after values. During a dry run this reads **"Potential Update:"** instead, since nothing was actually written yet.
- **`Manual Review Required: …`**: The tool found something a person needs to decide: multiple possible phone numbers/fax numbers/emails/addresses on the site, and/or a possible name change. It lists the field(s) involved and, where relevant, the actual candidate values found on the website.
- **`Manual Review Required: No website address found…`**: The record has no website on file, or the one on file couldn't be reached (broken link, timeout, or an error page even after a retry). Nothing else can be checked for this record until the website field itself is fixed.
- **`Manual Review Required: Failed to update…` / `Manual Review Required: Could not update…`**: Only possible under `--apply`. Either REDCap rejected the write entirely (the reason from REDCap is included), or one or more of this record's own fields no longer matched what the change was based on. Either way, `updated_redcap_record` reads **No** and **none** of this record's fields were written -- the tool never writes only part of a record's changes, since that would leave it silently half-updated. This note lists every field that still needs to be applied by hand, and why.

When a record requires manual review for any field, none of that record’s fields are automatically updated. All potential changes for that record are listed in the notes so that a reviewer can confirm and apply the correct changes manually.

## 14. Reviewing the Results

Use the following steps to review the Record Summary spreadsheet:

1. Open the `record_summary_….xlsx` workbook for the run. It opens on the "Record Summary" tab.
2. Filter or sort by `change_required` = **Yes** to see only the records with anything to act on.
3. For any row where `manual_review_required` = **Yes**, read the `notes` column, check the organization's website and REDCap side by side, and update REDCap by hand with the correct value.
4. For any row where `updated_redcap_record` = **Yes**, REDCap has already been updated automatically for that record. A matching note was also added to that record's Additional Comments in REDCap, so there's a record of exactly what changed and why.
5. Separately, switch to the "New Records Added" tab to see which additional locations were already created as brand-new REDCap records during this run. These are already live in REDCap; the row is just an audit trail of what was created, and where its county came from. Spot-check a few, and fill in the remaining REDCap fields (services, accreditation, and so on) by hand as usual for a new record.
6. Switch to the "Other Locations" tab for any additional branch/office locations that weren't confident enough to create automatically. Read the `reason` column and decide whether/how to add each one to REDCap by hand.

## 15. Benefits of the Automation

The automation provides several practical benefits:

- Saves time by reducing repeated manual work.
- Improves consistency when checking and formatting data.
- Reduces copying and typing errors.
- Makes it easier to compare existing REDCap information with current website information.
- Helps identify possible changes and records that require manual review.
- Provides documentation of changes through the Record Summary workbook and notes.
- Supports reproducibility because the same verification rules can be applied to many records.
- Allows reviewers to focus on difficult or uncertain cases instead of repeating routine checks.

## 16. Drawbacks and Limitations

Automation also has important limitations:

- Organization websites can be outdated, incomplete, unavailable, or inconsistent.
- A website may list several phone numbers, email addresses, or locations, making it difficult to determine which value belongs to a specific REDCap record.
- Organizations may have similar names or several locations.
- Different organizations may share the same address.
- A website may change its layout or technical structure, which can affect automated extraction.
- Some websites use JavaScript or other features that make automated review more difficult.
- REDCap API access requires appropriate project permission and a valid project token.
- An incorrect automation rule could affect many records if the program is not tested carefully.
- Human review is still required for uncertain or important decisions, including possible facility-name changes.
- The current tool does not search the internet for a missing organization website.
- Additional branch locations that meet the tool's criteria (a clear name, street number, website, and no likely duplicate elsewhere in REDCap) are created automatically as new REDCap records; anything less certain is still only reported for manual review.
- The free address-lookup services used to determine a new location's county don't cover every address; some new records may need their county filled in by hand.
- A website's contact/location pages sometimes display a different phone number than what's embedded in the page's own listing (for example, call-tracking numbers that can change depending on how the page was reached). The tool prefers the number in the site's own "locations" listing where one exists, but this isn't foolproof for every site.

**Limitations of Additional new locations:**

The additional-locations feature is a helpful screening tool, but it may not identify every branch or office location associated with an organization. Some websites list locations on a separate webpage or URL that the application may not reach during its normal review. In other cases, location information may be loaded dynamically, displayed in a format that the application cannot reliably parse, or organized in a way that prevents the program from recognizing each location.

For this reason, the **Other Locations** results should not be treated as a complete list of all locations for an organization. When a complete location review is important, the reviewer should also check the organization's website manually, including its Locations, Contact, Find a Location, or similar pages. Any location identified by the application should still be reviewed before it is added to REDCap.

## 17. Data Quality and Safety Rules

The following rules are used to protect REDCap data and support accurate review:

- Test the program on a small number of records before processing a large batch.
- Run the tool in dry-run mode first and review the results before using `--apply`.
- Use only approved REDCap access methods and authorized project credentials.
- Keep the REDCap API token secure and do not email it, place it in chat, or share it with unauthorized users.
- Do not bypass login, multifactor authentication, CAPTCHA, or other security controls.
- Do not automatically change a value when the website information is unclear.
- Do not treat information that is missing from a website as proof that the existing REDCap value is incorrect.
- Do not automatically change the facility or organization name; possible name changes require human review.
- If any field in a record requires manual review, the program does not automatically update the other fields in that record during the same run.
- Before writing a change, the program checks the current live REDCap value. If another user changed the value after the spreadsheet was exported, the program skips writing that field. If this happens for ANY field on a record, none of that record's fields are written -- it's all or nothing, never a partial update.
- A new location is only created automatically when it has a clear name, street number, and website, and doesn't look like a duplicate of an existing REDCap record. A failed or skipped creation always falls back to the Other Locations worksheet with the reason noted, never silently dropped.
- Review the Record Summary workbook after each run and keep the results as part of the project documentation.
- Review the New Records Added tab after each run to confirm the automatically-created records look correct, and complete their remaining REDCap fields by hand.
- Review additional branch locations on the Other Locations tab separately before deciding whether they should be added as new REDCap records.

## 18. Simple Technical Flow

The current verification process can be summarized as:

REDCap Export Spreadsheet → Python (`automated_comptroller_verifier.py`) → Read Existing Record and Website → Review Organization Website → Extract Available Information → Compare Website Information with REDCap → Apply Safety and Manual-Review Rules → Dry-Run Results → Human Review → Optional `--apply` → REDCap API Update → Record Summary and Other Locations Workbook

The normal process should begin with a dry run. The user reviews the results and any manual-review notes. If the proposed changes are correct and the user is authorized to update the project, selected records can then be run with `--apply`. Before a field is written, the program checks the live REDCap value again to protect newer changes.

## Program Structure

The project is organized around the main verification script and supporting files:

- `automated_comptroller_verifier.py`: the main application. It reads requested records, reviews the website on file, compares selected information, applies safety rules, creates the results workbook, and performs authorized REDCap updates.
- `redcap_config.py`: the local configuration file used for REDCap API access when real updates are requested. This file contains a credential and must be kept secure.
- REDCap export `.xlsx`: the read-only source containing exported record information used for comparison.
- Output workbook: contains the Record Summary and Other Locations tabs for review.

This structure separates the source data, verification process, REDCap connection, and review output so the workflow is easier to test, review, and maintain.


## 19. Questions and Troubleshooting

**The tool says a Record ID wasn't found in the spreadsheet.**
That Record ID is not present in the `.xlsx` file provided with `--xlsx`. Check the Record ID and confirm that the correct REDCap export file is being used. The program does not create a report row for a missing Record ID.

**A record has no website on file.**
The tool reports the missing website and stops processing that record. It does not search the internet for a replacement website.

**Can I re-run the tool safely?**
Yes. Every run re-checks REDCap's current live values immediately before writing anything, so it won't overwrite a change someone else already made in the meantime.

**Where do I get a REDCap API token?**
From REDCap's own **API** page for the specific project you want this tool to update. Each token only works for one project.

**I don't have `redcap_config.py` set up. Can I still use the tool?**
Yes. Every dry-run preview works without it. That file is only required the moment you add `--apply`.

**What happens if the REDCap token expires or is invalid?**
The tool checks this before touching anything, at the project-confirmation step described in [Running the Tool](#10-running-the-tool): if the token is already bad at that point, it stops immediately with a message telling you to update `redcap_config.py`, before any record is processed. If a token happens to expire partway through a long `--apply` run, the tool recognizes REDCap's rejection, prints the same message, and stops there rather than continuing to fail on every remaining record one by one. Records already processed before that point still get their row written to the summary spreadsheet, so nothing already done is lost. Just re-run the rest once `redcap_config.py` has a valid token.

---

*Automated Comptroller Verification · Operations Manual*
