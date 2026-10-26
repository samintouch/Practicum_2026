# Comptroller Automation

**Operations manual.** A guide to understanding, running and reviewing the REDCap automation tool. It covers existing records, the new location records the tool can create, and the local AI model that helps read difficult websites.

| | |
|---|---|
| **Tool** | `comptroller_automation` (Python package, run with `python -m comptroller_automation`) |
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
8. [How the Local AI Model Is Used](#8-how-the-local-ai-model-is-used)
9. [Adding New Locations](#9-adding-new-locations)
10. [Limitations and Safety Measures](#10-limitations-and-safety-measures)
11. [Setup Requirements](#11-setup-requirements)
12. [Running the Tool](#12-running-the-tool)
13. [Command Options](#13-command-options)
14. [Output and Results](#14-output-and-results)
15. [Record Summary Spreadsheet Column Descriptions](#15-record-summary-spreadsheet-column-descriptions)
16. [Reviewing the Results](#16-reviewing-the-results)
17. [Benefits of the Automation](#17-benefits-of-the-automation)
18. [Drawbacks and Limitations](#18-drawbacks-and-limitations)
19. [Data Quality and Safety Rules](#19-data-quality-and-safety-rules)
20. [Simple Technical Flow](#20-simple-technical-flow)
21. [Questions and Troubleshooting](#21-questions-and-troubleshooting)

---

## 1. Introduction

REDCap is used to store information about organizations and facilities, including organizations that provide Recovery Support Services (RSS). As the database grows, some records become outdated, incomplete or duplicated. New organizations and new locations of existing organizations also need to be added once they are identified and verified.

This project uses automation to reduce the repetitive work of reviewing and maintaining REDCap data. The automation supports the reviewer; it does not replace human judgment. The program completes routine tasks and flags unclear or important decisions for manual review.

This manual describes the purpose, public health relevance, software, workflow, benefits, limitations, safety measures and human-review steps for the Comptroller Automation tool.

## 2. What Is Automation?

Automation means using a computer program to complete repeated tasks by following a set of defined rules. Instead of manually copying, comparing, formatting and entering the same kinds of information many times, a program performs these tasks consistently.

For this project, the automation can:

- read the REDCap export
- visit organization websites and compare what they show with REDCap
- identify possible changes and find additional locations
- prepare records for review
- update approved information through the REDCap API
- produce a results workbook

Automation does not mean the computer makes every decision. When information is unclear or needs judgment, the program sends the record for manual review.

## 3. Why Are We Using Automation?

The REDCap database contains thousands of organization records. Reviewing every one by hand takes a great deal of time and can lead to inconsistent formatting or data-entry errors. Automation completes the repetitive steps quickly and consistently, so reviewers can spend their time on the records that need judgment.

The main reasons for using automation are:

- Reduce repetitive manual work.
- Save time when reviewing existing REDCap records.
- Identify information that may be outdated or differ from the organization's current website.
- Fill in missing information (for example, an email address or social media link) when the website clearly provides it.
- Improve consistency when comparing phone numbers, addresses, email addresses, websites and social media links.
- Find additional locations of an organization and add them to REDCap.
- Reduce copying and typing errors.
- Create a clear record of what was reviewed, what changed and what still needs manual review.

## 4. Epidemiology and Public Health Relevance

A reliable directory of Recovery Support Services is a useful public health data resource. Public health professionals need accurate information about where services are located and how organizations can be contacted. Duplicate, outdated or incomplete records reduce the quality of the database and make it harder to understand the availability and distribution of services.

From an epidemiology perspective, data quality matters because public health analyses and decisions depend on accurate, consistent information. Well-maintained organization-level data improves later descriptive analyses, such as examining where services are available, identifying possible geographic gaps, and supporting planning or referral activities. Adding the additional locations of multi-site organizations gives a more complete picture of where services are actually offered.

This automation does not measure health outcomes or show that Recovery Support Services improve health outcomes. Its purpose is to improve the quality and usability of the service-resource data used for public health work.

The project can support public health by:

- Improving the completeness and consistency of service-resource data.
- Reducing duplicate or outdated information.
- Keeping organization contact and location information current.
- Making the list of service locations more complete.
- Supporting more reliable descriptions or mapping of service availability.
- Letting staff spend less time on repetitive data checking and more time on review, analysis and other public health activities.

## 5. What Program Performs the Automation?

The automation is performed by a Python program, the `comptroller_automation` package in the `Comptroller_Automation` folder. Python controls the workflow and:

- reads the REDCap export
- visits organization websites and extracts the information they show
- compares that information with REDCap and applies the program's safety rules
- asks a local AI model for help when a website is hard to read
- creates the results workbook
- when authorized, sends approved changes and new records to REDCap through its API

The main components are:

- **Python:** controls the automation workflow and performs the data processing.
- **pandas and openpyxl:** read the REDCap Excel export and create the results workbook.
- **requests and Beautiful Soup:** fetch organization websites and read their content; also used for the REDCap API.
- **Ollama with a local AI model (qwen2.5:7b by default):** reads websites whose contact information the code alone can't reliably parse. It runs entirely on the local computer, so no website text or REDCap data is sent to an outside AI service.
- **U.S. Census Bureau geocoder:** a free public service used to find the county for an address.
- **Playwright with headless Chromium:** opens a page in a real (hidden) browser when a website refuses plain automated requests, and, with `--browser`, for websites built with JavaScript.
- **REDCap and the REDCap API:** REDCap stores the records. The API is the controlled connection used to write changes and create new records when the tool is run with `--apply`.
- **Organization websites:** provide the current public information used for verification.

The tool uses the website already stored in each REDCap record. It does not search the internet for a replacement when a website is missing. A missing or unusable website is reported for manual review.

### General Coding Process

The automation was developed in small steps so each part could be tested before use with real REDCap records.

1. Identify the REDCap fields the program needs to review or update, using the project's data dictionary.
2. Read the current REDCap export into Python.
3. Clean and standardize values such as organization names, addresses, phone numbers, state abbreviations, ZIP codes, website URLs and social media links.
4. Read the organization's website: the home page, contact and about pages, and its locations pages.
5. Find this record's own location on the website, by address or phone number.
6. Compare REDCap with the website and classify each field as matching, new, changed, needing review, or not found.
7. When the code alone can't decide, ask the local AI model to read the pages. Keep only the values it returns that really appear on the page.
8. Do not guess when information is unclear; send uncertain cases for human review.
9. Use dry-run mode to preview proposed changes without writing to REDCap.
10. When `--apply` is intentionally used:
    - confirm the REDCap project
    - re-check the live REDCap values
    - write only the changes that meet the safety rules
    - create only the new locations that pass the duplicate and directory checks
11. Save results in the workbook so the reviewer can see what was checked, what changed and what still needs review.
12. Test a small number of records before processing a larger batch.

### General Tasks That Can Be Automated

- Read existing organization information from the REDCap export.
- Standardize and compare selected values.
- Compare phone and fax numbers while ignoring formatting differences.
- Find email addresses and social media profiles on the website.
- Compare physical addresses and look up the county.
- Detect website redirects that may indicate a website change or a merger.
- Identify possible facility-name changes and send them for manual review.
- Find the organization's other locations, check them against REDCap for duplicates, and create new records for the ones that qualify.
- Recognize doctor and hospital directory websites so their listings aren't added as locations.
- Create a comparison and review workbook.
- Write clear changes to REDCap through the API when `--apply` is used and all safety rules are met.

### Tasks That Should Not Be Fully Automated

Some decisions require context and human judgment. The tool provides supporting information, but a person should make the final decision:

- Confirming a facility or organization name change.
- Resolving cases with several possible phone numbers, fax numbers, email addresses or addresses.
- Replacing an existing email address that no longer appears on the website.
- Determining whether organizations with similar names are the same facility.
- Determining whether an organization moved when its website lists several offices.
- Resolving conflicting or unclear information from different parts of a website.
- Deciding whether existing REDCap information should be removed when it isn't found online.
- Completing the remaining fields of a newly created location record (services, accreditation, and so on).

A matching name, address or other value is useful evidence, but it isn't always enough for a final decision. Human review protects the accuracy of the REDCap database.

## 6. What This Tool Does

The tool reviews existing REDCap records, using the website listed in each record as the main source of current information.

For each record, the tool reads the organization's website: the home page, contact and about pages, and any locations pages. It extracts the contact information shown there and compares it with what REDCap has.

- When the website shows **one clear value** that differs from REDCap, the field is prepared for an update.
- When REDCap is **blank** and the website clearly shows a value (for example, a new email address or social media link that wasn't there before), the field is prepared to be filled in.
- When the website shows **several possible values** and the tool can't tell which belongs to this record, nothing is changed and the record is flagged for manual review.

Many organizations list several offices on one website. The tool first finds the record's own office on the site by matching its street address, or failing that its phone number. It then uses that office's phone, fax and email. This avoids mixing up one clinic's details with another's.

When the code alone can't read the website clearly, the tool asks a local AI model to read it (see [section 8](#8-how-the-local-ai-model-is-used)).

The tool also finds the organization's other locations and can add them to REDCap as new records (see [section 9](#9-adding-new-locations)).

REDCap has additional fields this tool does not check. Those fields depend on a consent form completed by the organization, so they can't be confirmed from a public website.

## 7. What It Checks

For each record, the tool compares the following with the organization's website. Clear differences and clearly new information are written when the safety rules are met.

| Field | What happens |
|---|---|
| **Phone number** | Updated when the website clearly shows a different number for this facility; added when REDCap has none. |
| **Fax number** | Same as phone. |
| **Email address** | Added when REDCap has none and the website clearly shows one. A different email on the website is flagged for review rather than replacing the one on file, since the existing address may still work. |
| **Physical address** | Street, city, state and ZIP. Updated when the facility has clearly moved (see [section 10](#10-limitations-and-safety-measures)). |
| **County** | Looked up from the address with the U.S. Census geocoder and updated if it differs. |
| **Social media** | Facebook, Instagram, Twitter/X, TikTok, YouTube and WhatsApp. Added or updated when the website links to one clear profile for that platform. Share buttons, posts and website-builder placeholder links (such as `facebook.com/wix`) are ignored. |
| **Website** | Updated when the website on file redirects to a new domain that still shows the facility's name, street address, phone, email or an office in its ZIP code (the site moved). Flagged for review if the new domain shows none of them. |
| **Facility name** | Flagged if the website shows a clearly different name; never changed automatically. |

Each field gets one of these results:

| Result | Meaning | Written to REDCap? |
|---|---|---|
| `MATCH` | The website agrees with REDCap. | No (nothing to change) |
| `NEW` | REDCap is blank and the website shows exactly one value. | Yes |
| `CHANGED` | REDCap differs and the website shows exactly one value. | Yes |
| `REVIEW` | Several candidates, or a change that needs a person's decision. | No |
| `NOT_FOUND` | The website doesn't show this information. | No (REDCap is left as is) |

## 8. How the Local AI Model Is Used

Websites present contact information in many ways. Some use clear, machine-readable data, which the code reads directly. Others put the address and phone number in a picture-like layout, in separate boxes, in another language, or in a form the code can't separate reliably. For those websites, the tool asks a **local AI model** to read the page.

**When it is used.** Only when the code alone leaves one of these unclear or not found:

- address
- phone
- fax
- email

Records the code can handle on its own never go to the AI.

**What it is asked.** The tool sends the AI only the contact-related text from the website's pages, plus the record's name and address on file. It asks the AI two things:

- whether the website is one organization's own site or a directory of unrelated providers
- every office the organization lists, each with its name, address, phone, fax and email

**How its answers are checked (grounding).** AI models can make mistakes or invent values, so the tool never trusts an answer on its own:

- every phone and fax number the AI returns must appear on the website
- every email must appear on the website
- every street address must appear on the website, with its house number, street name and ZIP code

Anything that doesn't is dropped, and the report says how many values were dropped. The AI's answers then go through exactly the same rules as the code's own findings: one clear value, a matching office, no conflicts.

**Where it runs.** The model runs on the local computer through Ollama. No website text or REDCap data is sent to an outside AI service.

**Speed.** On a computer without a graphics card, each AI call takes about 1 to 4 minutes. For a quick first pass over many records, use `--no-ai` and then re-run only the records that need it.

**Suggestion-only mode.** With `--ai-suggest-only`, values found by the AI are never written. They're listed for manual review instead, marked as AI suggestions.

The `ai_assisted` column in the Record Summary and the `found_by_ai` column in Field Details show where the AI was used.

## 9. Adding New Locations

Hospital systems, multi-clinic providers and organizations with several offices often list all their locations on their website. The tool finds these locations and, when it's confident, creates each as a new REDCap record.

### Where locations are found

- the website's "Locations", "Offices", "Find a location" or similar page, and the individual location pages it links to
- addresses on the home, contact and about pages
- the AI model's reading of the site, when the AI is used

### Excluding directory websites

Some websites are not one organization's own site but a **database** of many doctors, clinics or hospitals. Examples are doctor-finder sites such as `doctors.wellmedhealthcare.com` (thousands of WellMed-affiliated doctors), hospital network directories and listing sites. **The application does not suggest new locations from these websites.** Their listings are:

- never created as new records
- not listed on the Other Locations sheet either, since the providers they list are mostly not the organization's own offices
- not used to change the record's own fields. Every proposed change on that record is sent for manual review.

The record's notes say *"website is a directory of many providers; its listings are not suggested as new locations"*.

A website is treated as a database when any of these is true:

- it is on the tool's list of known directory sites. Examples are WellMed's doctor finder, Zocdoc, Healthgrades, Psychology Today, WebMD, Vitals, Yelp, NPI lookup sites and SAMHSA's treatment locator. To add another, put its domain in `DIRECTORY_DOMAINS` in `comptroller_automation/locations.py`.
- most of the locations it lists are other providers: names that carry a doctor's title or credentials, or a business form (Dr., MD, DO, DDS, NP, PLLC, LLC, Inc and similar), and don't share the organization's name. A health system's own sites, named by what they do or where they are ("Dermatology Clinic", "Emergency Department", "Diamond Hill Health Center"), don't count. That is how JPS Health Network's 25-plus clinics are suggested as its locations, while a doctor finder's list of individual physicians is not.
- the AI model, when used, reports that the site is a directory of unrelated providers

### Very long location lists

A website that lists more than 50 other Texas locations, for example a laboratory chain with over a hundred patient service centers, gets **none** created automatically. They all go to the Other Locations sheet with the reason "website lists N other locations, more than 50", so a person can decide whether they belong in REDCap. The limit is `MAX_NEW_LOCATIONS` in `comptroller_automation/fields.py`.

### When a location becomes a new record

A location is created as a new REDCap record (only with `--apply`) when **all** of these are true:

1. The location is in **Texas**. The organization's offices in other states are never added as new records, and they aren't listed on the Other Locations sheet. The record's notes say how many were left out.
2. The website is the organization's own, not a doctor/hospital database (see above), and lists no more than 50 other Texas locations.
3. This record's own location was identified on the website. If it wasn't, the tool can't be sure the other listings are the same organization's.
4. The location has a name, a street number, city, state and ZIP.
5. It isn't marked closed or "coming soon", isn't announced as opening on a future date ("Opening December 7 at …"), and isn't an administrative, corporate or billing office.
6. It isn't already in REDCap. The tool checks the export and, under `--apply`, the live REDCap project for a record at the same street number and ZIP. It also checks the new records already created earlier in the same run.
7. It isn't this record's own office listed under a slightly different address. The tool treats the same street and ZIP with the same phone number, or a house number within a few doors, as the same office.

Anything that doesn't meet these conditions goes to the **Other Locations** sheet with the reason, for a person to decide.

### What a new record contains

- **Name:** the location's own name. If it has none, or the site only labels it by city (for example "Frisco Location"), it is named "&lt;organization&gt; - &lt;City&gt;". When several locations share the same name, " - &lt;City&gt;" is added so each record is distinct.
- **Street, city, state, ZIP,** from the website.
- **County,** looked up from the address with the U.S. Census geocoder. Left blank if it can't be found; never guessed.
- **Phone, fax and email** of that location. If no phone can be found for the location, the parent record's phone is used, for staff to correct later if needed. When the same run also updates the parent record's phone from the website, the updated number is used. The `note` column on the New Records Added tab says when this happened, for example *"phone not listed for this location; taken from parent record 19"*. The fax is **never** copied from the parent; it is left blank unless the location lists its own.
- **Website:** the location's own page whenever the site has one, for example `https://jpshealthnet.org/locations/diamond-hill-health-center` rather than the `/locations` list it was found on. A location's own page is recognized when the location's name on a list page links to it, or when the page's web address matches the location's name. The name given on that page is also preferred, since it is usually the location's full name. If the location has no page of its own, the page it was found on is used.
- **"Is this a completed record from Phase 1 data collection?"** = No.
- **"Is the organization/facility open or closed?"** = Open, since it was just found listed on the organization's own website.
- **Additional Comments:** a note that the record was added by the Comptroller_Automation tool on a given date, as an additional location of the parent REDCap record, with the page it was found on.

Everything else on the form (services, accreditation, patient limits, and so on) is left blank for staff to complete. REDCap assigns the new record ID itself.

## 10. Limitations and Safety Measures

These rules are built into the tool to make it safe to use with real REDCap data:

- **It never guesses.** If the website shows more than one possible phone number, fax number, email or address, and the tool can't tell which belongs to this record, nothing is changed. The record is flagged for manual review.
- **One unclear field holds the whole record.** When any field on a record needs manual review, none of that record's fields are written automatically. All the proposed changes are listed in the notes for a reviewer to confirm. The only exception is a possible facility-name change, which is reported but doesn't hold the other fields.
- **It never changes the facility name automatically.** Name changes require human review. (The website is only changed in the case described below, when the old site redirects to a new domain that still shows the facility's details.)
- **A different city name for the same street address is taken from the website.** When the street address matches but the website gives another city name for it (for example Horizon City instead of El Paso, or Dallas instead of Addison), the city is updated to the website's.
- **It doesn't suggest a name change for the same name written another way.** The name counts as a match when any of these is true:
  - the exact name on file appears anywhere in the website's text, ignoring capitals and punctuation (so "M.D" and "MD" are the same) and treating "&" as "and". This holds even when the site's title or logo uses a different brand name.
  - the website's name is an abbreviation of the name on file, for example "JPS Health Network" for "John Peter Smith Health Network Family Health Center".
  - the website's name contains every distinctive word of the name on file. Generic words such as Clinic, Medical, Health, Psychiatry, Associates, MD, PLLC or a Texas city name don't count. For example "Wentian Huang MD, PhD, PC" matches "Huang Medical Clinic", and "Tri-MED Integrative Psychiatry & Sleep Medicine" matches "TriMed Behavioral and Sleep Medicine".

  A real rebrand, where the distinctive words change ("Interventional Pain and Wellness Center" to "The Pain and Spine Specialists"), is still flagged. Page titles holding an address, a phone number or more than 8 words are not treated as names.
- **A website that moved is updated; an unknown redirect is not trusted.** When the website on file now redirects to another domain, the tool checks whether the new site shows any of the facility's details: its name, street address, phone number, email, or an office in its ZIP code.
  - **If any of them is there** (for example `https://mirmd.com/` now opens `https://prestigeclinicdfw.com/`, which shows "Prestige Clinic", or a site that says "Diamond Physicians is now Diamond Health"), the website is updated to the new address and the rest of the record is checked against the new site as usual.
  - **If none of them is there,** nothing on that record is written, because the redirect may mean a sale, a merger or an expired domain. The record is sent for manual review.
- **The website must belong to this facility.** At least one of the name, phone, street address or email on the website must match REDCap, or the website must list an office in the record's ZIP code. Otherwise nothing is written.
- **An address is only moved when it's clear.** The tool changes an address only when:
  - the website lists a single address that differs from REDCap, and doesn't show other phone numbers that might belong to an unlisted office, **or**
  - the website lists this facility's phone number at a single, different address, **or**
  - the address on file no longer appears, and exactly one of the website's offices is in the record's ZIP code (or, if none is, its city). That office is taken as the facility's new address, and the website's other offices are handled as new locations, **or**
  - the address on file no longer appears, none of the offices is in its ZIP code or city, and one office is clearly the nearest to the address on file. Clearly nearest means within 25 miles, and at most half as far away as the next-nearest office. Distances come from the U.S. Census geocoder. For example, a Pantego clinic whose website now lists offices in Arlington (4 miles away) and Wylie (about 45 miles away) is moved to the Arlington office, and Wylie becomes a new location.

  The county for the new address must also be confirmed by the Census geocoder; if it can't be, the address isn't changed. Websites that list several addresses, with no clear nearest one, are sent for review.
- **An organization that has left Texas is flagged.** If the address on file no longer appears on the website, and the website lists offices only in other states, the street is marked *"Address has changed, but no location found in Texas"*. Nothing on the record is written, so out-of-state contact details never end up on a Texas record. Only offices in the record's own state are considered when deciding the record's address.
- **Nothing is written from a directory website.** When the website on file is a doctor or hospital directory listing many providers, every proposed change is sent for review, since the listing may describe a different provider.
- **A location page is read on its own.** When the website on file is one page of a larger organization's site (for example `.../locations/allen-tx`), only that page is used for this record's details. The organization's other pages describe other locations or its head office.
- **Hidden addresses are ignored.** Websites built from templates sometimes keep an old or sample address in a section that is hidden from visitors. It's still in the page's code, but never shown on screen. When a website seems to list more than one address, the tool opens those pages in headless Chromium, as a visitor would see them. An address that is hidden while the page's other addresses are visible is dropped. On a page where most addresses are hidden, they are almost always collapsed sections a visitor opens with a click (for example a Locations page listed by state), so they are kept.
- **AI answers must be on the page.** Every value from the AI model is checked against the website text; values that don't appear there are dropped.
- **It never overwrites a recent change made by another user.** Before writing, the tool re-reads the live REDCap values of every field it's about to change. If any of them no longer matches the export the comparison was based on, nothing on that record is written.
- **It never partly updates a record.** A record's changes are written together or not at all.
- **New locations are only created after the directory and duplicate checks.** Any doubt sends the location to the Other Locations sheet.

## 11. Setup Requirements

### 11.1 Python and Required Packages

You need **Python 3.11 or later**. If Python isn't installed, download it from [https://www.python.org/downloads/](https://www.python.org/downloads/).

From the `Comptroller_Automation` folder, install the required packages:

```
pip install -r requirements.txt
```

Then install the headless Chromium browser once:

```
playwright install chromium
```

Some websites refuse plain automated requests, for example by dropping the connection; the tool then opens the page in headless Chromium automatically. Without it, those websites are reported as unreachable.

### 11.2 The Local AI Model (Optional, Recommended)

1. Install Ollama from [https://ollama.com](https://ollama.com).
2. Download the model once:

   ```
   ollama pull qwen2.5:7b
   ```

3. Make sure Ollama is running (it normally starts automatically).

When a run starts, the tool reports whether the AI model is available. If Ollama isn't running or the model isn't installed, the tool says so and continues without AI.

### 11.3 The REDCap Export Spreadsheet

You need the current REDCap data export: the "labels" export as an `.xlsx` or `.csv` file, for example `ComptrollerProject20_DATA_LABELS_2026-04-16_1051.xlsx`. The tool reads each record's current information from this file. It never writes to it.

### 11.4 The REDCap Credentials File

Copy `redcap_config.py.sample` to `redcap_config.py` in the `Comptroller_Automation` folder and fill it in:

```python
config = dict(
    api_url="https://your-redcap-server/api/",
    api_token="YOUR-REDCAP-API-TOKEN-HERE",
)
```

- Get the URL and token from REDCap's own **API** page for the project you want the tool to update. Each token works for exactly one project.
- This file is only needed with `--apply`. Dry runs work without it.

`redcap_config.py` contains a live credential for the REDCap project. Keep it secure. It is excluded from version control through `.gitignore`. Don't email it, paste it into chat, or share it with anyone who doesn't need access.

## 12. Running the Tool

Open a terminal in the `Comptroller_Automation` folder. Each run needs one or more Record IDs (or `--all`) and the REDCap export.

**How to run:**

```
python -m comptroller_automation <record id(s)> --input <spreadsheet file> [--apply]
```

**Common examples:**

| You want to… | Command |
|---|---|
| Preview one record | `python -m comptroller_automation 400 --input export.xlsx` |
| Preview a range of records | `python -m comptroller_automation 3-50 --input export.xlsx` |
| Preview a specific mix of records | `python -m comptroller_automation 3 5 8 12-15 --input export.xlsx` |
| Preview every record | `python -m comptroller_automation --all --input export.xlsx` |
| A quick pass without the AI | `python -m comptroller_automation 1-500 --input export.xlsx --no-ai` |
| Write the confirmed changes to REDCap | `python -m comptroller_automation 400 --input export.xlsx --apply` |

### Dry Run vs. Apply

By default, the tool runs in **dry-run mode**. It works out every change and every new location it would make, but writes nothing to REDCap. The mode is shown before processing begins:

```
DRY RUN: no changes will be written to REDCap (pass --apply to actually write them).
```

With `--apply`, the tool first shows which REDCap project the credentials point to, and asks you to confirm:

```
========================================================================
--apply is set: this run WILL write changes to REDCap.
  REDCap project : Comptroller Automation Test Project (Project ID: 3282)
  API URL        : https://redcap.sph.uth.edu/api/
  Status         : development
========================================================================
Proceed with writing changes to this REDCap project? [y/N]:
```

Enter `y` to continue. Any other answer, including just pressing Enter, cancels the run without making any changes.

For safe use, always run without `--apply` first and review the results workbook. Use `--apply` only once the proposed changes and new locations have been reviewed and are appropriate.

## 13. Command Options

| Option | Required? | What it does |
|---|---|---|
| `<record id(s)>` | Yes, unless `--all` | One or more Record IDs. Single IDs, ranges (`3-10`), or a mix (`3 5 8-10`) all work. |
| `--all` | No | Check every record in the export. |
| `--input <file>` | Yes | The REDCap export (`.xlsx` or `.csv`) to read records from. |
| `--apply` | No | Writes confirmed changes and new location records to REDCap. Without it, every run is a preview only. |
| `--yes` | No | Skips the confirmation prompt under `--apply` (for scheduled runs). Use with care. |
| `--outdir <folder>` | No | Where the results workbook is saved. Defaults to `output`. |
| `--output-xlsx <file>` | No | Saves the results workbook to this exact file instead of the automatic name. |
| `--skip-new-locations` | No | Doesn't look for the organization's other locations. Faster for very large multi-location providers. |
| `--no-ai` | No | Doesn't use the local AI model. |
| `--ai-model <name>` | No | Which Ollama model to use. Default `qwen2.5:7b`. |
| `--ai-suggest-only` | No | Values found by the AI are listed for review instead of being written. |
| `--max-pages <n>` | No | How many pages to read per website. Default 6. |
| `--workers <n>` | No | How many websites to check at once. Default 4. |
| `--browser` | No | Also uses headless Chromium for pages that look built with JavaScript (very little text in the plain page). Sites that refuse plain requests always get the browser, with or without this option. |
| `--no-browser` | No | Never uses headless Chromium, not even for sites that block plain requests. |
| `--no-county` | No | Skips the Census county lookup. |
| `--suggest-websites` | No | For records with no website, searches the web and suggests one. Off unless given. The suggestion is only reported, never written to REDCap. See [Website Suggestions](#website-suggestions). |

### Website Suggestions

About a third of the records in the export have no website on file. These can't be verified, so by default they are only reported for manual review. With the `--suggest-websites` option, the tool also looks for a likely website for each of them. It never writes the suggestion anywhere:

1. It searches the web (Bing, or DuckDuckGo if Bing returns nothing) for the facility's name, city and state, and for its phone number.
2. It skips results that are directories, social media pages or listing sites, such as Yelp, Healthgrades, NPI lookup sites, Psychology Today or Facebook.
3. It opens each remaining result, up to four, and compares it with the record.

A website is only suggested when it shows the record's **phone number or street address**, together with a matching name:

- **High confidence:** both the phone number and the address match.
- **Medium confidence:** one of them matches, plus the name.

If no result passes this check, nothing is suggested.

The suggestion appears in the `website_suggested` column of the Record Summary and in the notes, for example *"Suggested website (please confirm; not written to REDCap): https://a-1behavioralhealth.com/ (phone and street address and name match; high confidence)"*. It is **never written to REDCap**, and the record's other fields are not checked or changed against it. A person confirms the website and enters it in REDCap. The next run then verifies the record against it as usual.

Searches are spaced a few seconds apart so the search engines don't block the tool, so this option adds time to runs that include records without a website.

## 14. Output and Results

The program prints one line per record and creates one results workbook per run.

### Console Output

```
Processing records from 3 to 9999 (4 record(s) total)...

Processing Record    3: ADDISON PAIN AND REGENERATIVE MEDICINE (1.1s)
Processing Record    5: KND BEHAVIORAL CLINIC PLCC (0.0s)
Processing Record  400: INTERVENTIONAL PAIN AND WELLNESS CENTER PLLC (62.4s)
Processing Record 9999: SKIPPED => ID NOT FOUND (0.0s)

3 record(s) checked: 1 updated in REDCap, 2 need manual review, 2 new location record(s) added.
Results workbook: output\summary_3_to_9999_redcap_id_2026_10_21_143000.xlsx
```

`SKIPPED => ID NOT FOUND` means the requested Record ID is not in the export provided with `--input`. Records that needed the AI model take noticeably longer.

### Results Workbook

`output/summary_<first>_to_<last>_redcap_id_<timestamp>.xlsx` has four tabs:

- **Record Summary:** opens by default and is the main tab to review. One row per record found in the export. See the next section.
- **Other Locations:** every additional location the website lists that was **not** added automatically. That includes an existing REDCap record at the same address, missing details, a closed or administrative office, a list too long to add automatically, or a failed REDCap write. Listings from doctor/hospital databases are not shown here. The `reason` column explains each one. These are never added automatically; a person decides whether and how to add them.
- **New Records Added:** one row per new REDCap record the tool created for an additional location. It shows the new Record ID, name, address, county, phone, fax, email, website, whether it was found by the code or the AI, and a `note` (for example, when the phone or fax came from the parent record). In a dry run, this tab lists the locations that **would be** created, with `(dry run: would be created)` in place of a Record ID.
- **Field Details:** one row per record and field. Shows:
  - the REDCap value, the website value, the result (MATCH / NEW / CHANGED / REVIEW / NOT_FOUND) and the action taken
  - whether the AI found the value, a note, every value seen on the site, and the source page

  Use this tab to see exactly why a field was or wasn't changed.

## 15. Record Summary Spreadsheet Column Descriptions

| Column | What it means |
|---|---|
| `redcap_record_id` | The REDCap Record ID this row is about. |
| `facility_name` | The name on file. |
| `updated_redcap_record` | **Yes** only if this run actually wrote changes into REDCap for this record. Always **No** in a dry run, whenever the record needs manual review, or if the REDCap write failed or was skipped. |
| `manual_review_required` | **Yes** if a person needs to read the notes and decide. Reasons include:<br>- several possible values for a field<br>- a possible name or website change<br>- no usable website<br>- under `--apply`, a write that was skipped or failed<br>This is only about this record's own fields; new locations are reported on their own tabs. |
| `found_multiple_locations` | **Yes** if the website lists other locations besides this one, whether they were added or not. |
| `new_records_added` | The Record IDs of new location records created from this record's website. |
| `ai_assisted` | **Yes** if the local AI model was asked to read this record's website. |
| `notes` | The most important column. See below. |
| `website_suggested` | Only with `--suggest-websites`: a website found by web search for a record that has none, confirmed by its phone number or address. Never written to REDCap; check it and add it by hand. |
| `website_changed` / `website_old` / `website_new` | `website_changed` is **Yes** when the website on file now redirects to a different domain. When the new site shows the facility's name, address, phone, email or an office in its ZIP code, `website_new` is written to REDCap; otherwise the record is held for review. `website_old` is always the URL on file. The website is never changed automatically. |
| `phone_changed` / `phone_old` / `phone_new` | `phone_changed` is **Yes** when the website clearly showed a new or different phone number that can be written. `phone_old` is always the number on file; `phone_new` appears only when there's a confirmed value. |
| `fax_changed` / `fax_old` / `fax_new` | Same as phone, for the fax number. |
| `email_changed` / `email_old` / `email_new` | Same as phone, for the email address. |
| `social_changed` / `social_old` / `social_new` | Same as phone, for Facebook, Instagram, Twitter/X, TikTok, YouTube and WhatsApp. When several platforms change, each value is labeled (for example `Facebook: …; Instagram: …`). |
| `address_changed` / `address_old` / `address_new` | Same as phone, for street, city, state, ZIP and county. `address_old` is always the full address on file in one line. |
| `name_changed` / `name_old` / `name_new` | `name_changed` is **Yes** if the website appears to show a different facility name. Never written automatically. |

Yes/No cells are colored to make the sheet easy to scan: **green** for Yes, **amber** for Yes in `manual_review_required` (a person needs to look), and **grey** for No. The same colors are used for `found_by_ai` on the Field Details tab.

When a record needs manual review, its `*_changed` columns read **No** and `*_new` columns are blank, since nothing was written. The proposed values are listed in `notes` instead.

### Reading the `notes` Column

It always takes one of these forms:

- **`Record up to date: No Change Required`:** the website agrees with REDCap, or nothing useful was found. Nothing to do.
- **`Updated: …`:** lists each field that was changed, for example *"The Phone was updated from (972) 323-7474 to (972)-242-4114."* In a dry run this reads **`Potential Update:`**, since nothing has been written yet.
- **`Manual Review Required: nothing was written for this record. Please decide: …`:** something needs a person's decision, so none of the record's fields were written. Each line names the field, explains why it's unclear, and shows both values. For example:

  > - Email: the email on file isn't shown on the website and the website shows a different one; the old one may still work, so check before replacing (REDCap has: admin@…; website has: medicalrecords@…)

  It may be followed by **`Also found on the website, waiting for the review above (not written yet): …`**. These are clear changes the tool found, such as a new Instagram link or a corrected city name, that were held back only because the record needs review. Apply them by hand together with your decision.
- **`Manual Review Required: Facility name …`:** the website shows a possibly different name.
- **`Manual Review Required: No website address found or invalid website address for this record`:** the record has no website on file, or it couldn't be reached (broken link, timeout or error page). The technical reason is shown in brackets.
- **`Manual Review Required: REDCap was not updated: …`:** only under `--apply`. The record changed in REDCap since the export, or REDCap rejected the write. Nothing on the record was written.
- **`Street: Address has changed, but no location found in Texas`:** the address on file no longer appears on the website, and the organization's website only lists offices in other states. The facility has most likely closed or left Texas. Nothing on the record was written; check the website and update the record's open/closed status by hand.
- **`Note: the website also lists N location(s) outside Texas; those are not added as new locations`:** the website lists offices in other states; these are never added as new records.
- **`Note: website is a directory of many providers; its listings are not suggested as new locations`:** the website on file is a doctor/hospital database. Nothing on the record was written and no new locations were suggested.

Records marked as duplicates in REDCap are checked like any other record.

## 16. Reviewing the Results

1. Open the `summary_….xlsx` workbook for the run. It opens on the Record Summary tab.
2. Filter `notes` to hide `Record up to date: No Change Required`, so only records with something to act on remain.
3. For each row where `manual_review_required` = **Yes**, read the notes and open the Field Details tab for that record. Check the website and REDCap side by side, and update REDCap by hand where appropriate.
4. For each row where `updated_redcap_record` = **Yes**, REDCap has already been updated. The same notes were added to that record's Additional Comments and "change explanation" fields, so REDCap itself shows what changed and when.
5. Check rows where `ai_assisted` = **Yes** a little more carefully. The AI's values were confirmed to be on the website, but they are worth a quick look.
6. Open the **New Records Added** tab. These records are already in REDCap (after `--apply`). Spot-check a few, and complete their remaining fields (services, accreditation, and so on) as for any new record.
7. Open the **Other Locations** tab. Read each `reason` and decide whether the location should be added by hand.

## 17. Benefits of the Automation

- Saves time by reducing repeated manual work.
- Improves consistency when checking and formatting data.
- Reduces copying and typing errors.
- Fills in missing contact details and social media links when websites clearly provide them.
- Makes the list of service locations more complete by adding organizations' additional locations.
- Reads difficult websites with a local AI model, without sending data to outside services.
- Identifies possible changes and records that need manual review.
- Documents every change through the results workbook and notes in REDCap.
- Supports reproducibility, because the same rules are applied to every record.
- Lets reviewers focus on difficult or uncertain cases instead of routine checks.

## 18. Drawbacks and Limitations

- Organization websites can be outdated, incomplete, unavailable or inconsistent. Information missing from a website doesn't prove REDCap is wrong, so the tool never deletes data.
- A website may list several phone numbers, emails or locations, making it hard to tell which belongs to a specific record.
- Organizations may have similar names or several locations, and different organizations may share an address.
- Website layouts change, which can affect automated extraction.
- Some websites rely on JavaScript; use `--browser` for these.
- The local AI model can misread a page. Grounding removes invented values, but it can't catch every misunderstanding, such as a real phone number assigned to the wrong office. This is why AI findings still pass through all the safety rules, and why `--ai-suggest-only` is available.
- Without a graphics card, the AI model is slow: about 1 to 4 minutes per website that needs it.
- REDCap API access requires appropriate project permission and a valid token.
- An incorrect rule could affect many records if the program isn't tested carefully. Always do a dry run first.
- Human review is still required for uncertain or important decisions, including name changes.
- The tool doesn't search the internet for a missing website.
- The Census geocoder doesn't match every address, so some counties may need to be filled in by hand.
- Some websites show call-tracking phone numbers that change depending on how the page was reached.

**Limitations of the new-location feature:**

The tool may not find every location of an organization. Some sites list locations on pages the tool doesn't reach, load them dynamically, or present them in a way that can't be parsed reliably. The tool reads at most 30 location pages per website. Directory detection is based on a list of known sites, name patterns and the AI's judgment, so an unfamiliar directory could occasionally slip through. The duplicate check relies on the street number and ZIP code, so a location already in REDCap under a mistyped address might not be recognized.

For these reasons, don't treat the New Records Added and Other Locations tabs as a complete or perfect list. When a full location review matters, also check the organization's website by hand, including its Locations, Contact or Find a Location pages.

## 19. Data Quality and Safety Rules

- Test the program on a small number of records before processing a large batch.
- Run in dry-run mode first and review the results before using `--apply`.
- Try `--apply` first on the test REDCap project before using it on the production project.
- Use only approved REDCap access methods and authorized project credentials.
- Keep the REDCap API token secure. Don't email it, put it in chat, or share it with unauthorized users.
- Don't bypass login, multifactor authentication, CAPTCHA or other security controls.
- Don't change a value automatically when the website information is unclear.
- Don't treat information missing from a website as proof that the REDCap value is wrong.
- Don't change the facility or organization name automatically.
- If any field in a record needs manual review, the program doesn't update that record's other fields in the same run.
- Before writing, the program re-checks the live REDCap values. If any changed since the export, nothing on that record is written. Changes are always all or nothing per record.
- Values found by the AI model are only used when they appear on the website.
- New locations are created only after the directory and duplicate checks. Anything uncertain goes to the Other Locations tab with its reason; nothing is silently dropped.
- Review the results workbook after each run and keep it as part of the project documentation.
- Review the New Records Added tab after each `--apply` run and complete the new records' remaining fields by hand.

## 20. Simple Technical Flow

```
REDCap export
  → read the record (name, address, phone, fax, email, website, social media)
  → read the website (home, contact, about and locations pages)
  → extract contact details and locations with code
  → find this record's own location (by address, then phone)
  → decide each field (MATCH / NEW / CHANGED / REVIEW / NOT_FOUND)
  → still unclear? ask the local AI model, keep only values found on the page, decide again
  → apply the safety rules (redirect, site belongs to facility, one unclear field holds the record)
  → other locations: directory check, eligibility check, duplicate check
  → dry-run results workbook → human review
  → optional --apply: confirm project, re-check live values, update records, create new location records
  → results workbook (Record Summary, Other Locations, New Records Added, Field Details)
```

### Program Structure

The tool is a small Python package in `Comptroller_Automation/comptroller_automation/`:

| File | Purpose |
|---|---|
| `__main__.py` | The command line: reads options, runs the records, writes to REDCap under `--apply`, saves the workbook. |
| `fields.py` | The fields checked, their REDCap variable names, and the bookkeeping fields set on each update. |
| `records.py` | Reads the REDCap export. |
| `scraper.py` | Fetches the website: home, contact and about pages. |
| `extract.py` | Pulls phone numbers, emails, social media links, names and locations from the pages. |
| `locations.py` | Finds locations pages, detects directory websites, and names and checks new locations. |
| `ai.py` | Asks the local AI model to read a website, and checks its answers against the page. |
| `normalize.py` | Standardizes phone numbers, addresses, states and social media links so they compare fairly. |
| `county.py` | Looks up the county for an address with the U.S. Census geocoder. |
| `verify.py` | The decision rules for each field. |
| `redcap.py` | The REDCap API: reading live values, updating records and creating new ones. |
| `report.py` | Builds the results workbook. |

Supporting files:

- `redcap_config.py`: the REDCap URL and API token. A credential; keep it secure.
- The REDCap export: the read-only source of each record's current data.
- `output/`: the results workbooks.
- `tests/`: automated checks of the comparison and grounding rules.

## 21. Questions and Troubleshooting

**The tool says a Record ID wasn't found.**
That Record ID isn't in the export given with `--input`. Check the ID and that you're using the right export file.

**A record has no website on file.**
The tool reports it for manual review and moves on. By default it doesn't search the internet for a replacement. To get a suggested website for these records, run with `--suggest-websites` (see [Website Suggestions](#website-suggestions)).

**It says "Local AI not available".**
Ollama isn't running, or the model isn't installed. Start Ollama, run `ollama pull qwen2.5:7b`, and run the tool again. Until then the tool works without AI.

**A website opens fine in my browser, but the tool says it's invalid or unreachable.**
Some websites block automated requests. The tool retries those pages in headless Chromium automatically. Check that Playwright and its browser are installed (`pip install playwright`, then `playwright install chromium`) and that `--no-browser` wasn't used. A site that blocks even the headless browser has to be checked by hand.

**The run is very slow.**
The AI model is probably running without a graphics card. Run with `--no-ai` for a quick pass, then re-run only the records that need manual review without `--no-ai`. Lowering `--max-pages` or using `--skip-new-locations` also helps.

**Can I re-run the tool safely?**
Yes. Every run re-checks REDCap's live values right before writing, so it won't overwrite a change someone else made. New locations are checked against the live project for duplicates, so re-running doesn't add the same location twice.

**A new location was created that shouldn't have been.**
Delete or correct the record in REDCap. If the website is a directory that isn't recognized, add its domain to `DIRECTORY_DOMAINS` in `comptroller_automation/locations.py` so it's excluded in future runs.

**Where do I get a REDCap API token?**
From REDCap's **API** page for the project you want the tool to update. Each token only works for one project.

**I don't have `redcap_config.py` set up. Can I still use the tool?**
Yes. Dry runs work without it. It's only needed with `--apply`.

**What happens if the REDCap token expires or is invalid?**
The tool checks the token at the project-confirmation step and stops with a clear message if it's rejected. If it expires partway through a run, the tool stops at that point. Records already processed are still included in the workbook. Update `redcap_config.py` and re-run the remaining records.

---

*Comptroller Automation · Operations Manual*
