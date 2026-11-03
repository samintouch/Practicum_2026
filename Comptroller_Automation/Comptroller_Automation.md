# Comptroller Automation

**Operations manual.** A guide to understanding, running, and reviewing the REDCap automation tool. It covers existing record updates, the new location records that the tool can create and enter, and the local AI model that assists in reading difficult websites.

| | |
|---|---|
| **Tool** | `comptroller_automation` |
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

REDCap is used to store information about organizations and facilities, including organizations that provide Recovery Support Services (RSS). As the database grows, some records become outdated, incomplete, or duplicated. New organizations and new locations of existing organizations also need to be added once they are identified and verified.

This project uses automation to reduce the repetitive work of reviewing and maintaining REDCap data. The automation supports the reviewer; it does not replace human judgment. The program completes routine tasks and flags unclear or important decisions for manual review.

This manual describes the purpose, public health relevance, software, workflow, benefits, limitations, safety measures, and human review steps for the Comptroller Automation tool.

## 2. What Is Automation?

Automation means using a computer program to perform repetitive tasks according to a set of defined rules. Instead of manually copying, comparing, formatting, and entering the same kinds of information many times, a program performs these tasks consistently.

For this project, the automation can:

- Read each record's current information from a REDCap data export, the `.csv` or Excel file downloaded from the REDCap project
- Visit organization websites and compare what they show with REDCap
- Identify possible changes and find additional locations
- Prepare records for review
- Update approved information through the REDCap API
- Produce a results workbook

Automation does not mean the computer makes every decision. When information is unclear or needs judgment, the program sends the record for manual review.

## 3. Why Are We Using Automation?

The REDCap database contains thousands of organization records. Reviewing each one by hand takes a great deal of time and can lead to inconsistent formatting or data entry errors. Automation completes repetitive steps quickly and consistently, so reviewers can spend their time on records that require judgment.

The main reasons for using automation are:

- Reduce repetitive manual work.
- Save significant time when reviewing existing REDCap records.
- Identify information that may be outdated or differ from the organization's current website.
- Fill in missing information (for example, an email address or social media link) when the website clearly provides it.
- Improve consistency when comparing phone numbers, addresses, email addresses, websites, and social media links.
- Find additional locations for an organization and add them to REDCap.
- Reduce copying and typing errors.
- Create a clear record of what was reviewed, what changed, and what still needs manual review.

## 4. Epidemiology and Public Health Relevance

A reliable directory of Recovery Support Services is a useful public health data resource. Public health professionals need accurate information about where services are located and how to contact organizations. Duplicate, outdated, or incomplete records degrade database quality and make it harder to understand service availability and distribution.

From an epidemiology perspective, data quality matters because public health analyses and decisions depend on accurate, consistent information. Well-maintained organization-level data improves later descriptive analyses, such as examining where services are available, identifying possible geographic gaps, and supporting planning or referral activities. Adding additional locations for multi-site organizations provides a more complete picture of where services are actually offered.

This automation does not measure health outcomes or demonstrate that Recovery Support Services improve them. Its purpose is to improve the quality and usability of the service-resource data used for public health work.

The project can support public health by:

- Improving the completeness and consistency of service-resource data.
- Reducing duplicate or outdated information.
- Keeping organization contact and location information current.
- Making the list of service locations more complete.
- Supporting more reliable descriptions or mapping of service availability.
- Letting staff spend less time on repetitive data checking and more time on review, analysis, and other public health activities.

## 5. What Program Performs the Automation?

The automation is performed by the Python program `comptroller_automation` in the `Comptroller_Automation` folder. Python controls the workflow and:

- Reads each record's current information from the REDCap data export file (`.csv` or Excel) given with `--input`
- Visits organization websites and extracts the information they show
- Compare that information with REDCap and apply the program's safety rules
- Asks a local AI model for help when a website is hard to read
- Creates the results workbook
- When authorized, sends approved changes and new records to REDCap through its API

The main components are:

- **Python:** Controls the automation workflow and performs the data processing.
- **Pandas and Openpyxl:** Open the REDCap data export file (`.csv` or Excel) and create the results workbook.
- **Requests and Beautiful Soup:** Fetch organization websites and read their content; also used for the REDCap API.
- **Ollama with a local AI model (qwen2.5:7b by default):** Reads websites whose contact information the code alone can't reliably parse. It runs entirely on the local computer, so no website text or REDCap data is sent to an outside AI service.
- **U.S. Census Bureau geocoder:** A free public service used to find the county for an address.
- **Playwright with headless Chromium:** Opens a page in a real (hidden) browser when a website refuses plain automated requests, and, with `--browser`, for websites built with JavaScript.
- **REDCap and the REDCap API:** REDCap stores the records. The API is the controlled connection used to write changes and create new records when the tool is run with `--apply`.
- **Organization websites:** Provide the current public information used for verification.

The tool uses the website already stored in each REDCap record. It does not search the internet for a replacement when a website is missing. A missing or unusable website is reported for manual review.

### General Coding Process

The automation was developed in small steps so each part could be tested before use with real REDCap records.

1. Identify the REDCap fields the program needs to review or update, using the project's data dictionary.
2. Download a current data export from the REDCap project (a `.csv` or Excel file) and load each record's information into Python.
3. Clean and standardize values such as organization names, addresses, phone numbers, state abbreviations, ZIP codes, website URLs, and social media links.
4. Read the organization's website, the home page, contact and about pages, and its locations pages.
5. Find this record's own location on the website, by address or phone number.
6. Compare REDCap with the website and classify each field as matching, new, changed, needing review, or not found.
7. When the code alone can't decide, ask the local AI model to read the pages. Keep only the values it returns that really appear on the page.
8. Do not guess when information is unclear; send uncertain cases for human review.
9. Use dry-run mode to preview proposed changes without writing to REDCap.
10. When `--apply` is intentionally used:
    - Confirm the REDCap project
    - Re-check the live REDCap values
    - Write only the changes that meet the safety rules
    - Create only the new locations that pass the duplicate and directory checks.
11. Save results in the workbook so the reviewer can see what was checked, what changed, and what still needs review.
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
- Resolving cases with several possible phone numbers, fax numbers, email addresses, or addresses.
- Choosing between several email addresses shown on the website.
- Determining whether organizations with similar names are the same facility.
- Determining whether an organization has moved when its website lists several offices.
- Resolving conflicting or unclear information from different parts of a website.
- Deciding whether existing REDCap information should be removed when it isn't found online.
- Completing the remaining fields of a newly created location record (services, accreditation, and so on).

A matching name, address, or other value is useful evidence, but it isn't always enough for a final decision. Human review protects the accuracy of the REDCap database.

## 6. What This Tool Does

The tool reviews existing REDCap records, using the website listed in each record as the main source of current information.

For each record, the tool reads the organization's website, the home page, contact and about pages, and any location pages. It extracts the contact information shown there and compares it with what REDCap has on file.

- When the website shows **one clear value** that differs from REDCap, the field is prepared for an update.
- When REDCap is **blank,** and the website clearly shows a value (for example, a new email address or social media link that wasn't there before), the field is prepared to be filled in.
- When the website shows **several possible values,** and the tool can't tell which belongs to this record, nothing is changed, and the record is flagged for manual review.

Many organizations list multiple offices on a single website. The tool first finds the record office on the site by matching its street address or, if that fails, its phone number. It then uses that office's phone, fax, and email. This avoids mixing up one clinic's details with another's.

When the code alone can't read the website clearly, the tool asks a local AI model to read it (see [section 8](#8-how-the-local-ai-model-is-used)).

The tool also finds the organization's other locations and can add them to REDCap as new records (see [section 9](#9-adding-new-locations)).

NB: REDCap has additional fields that this tool does not check. Those fields depend on a consent form completed by the organization, so they can't be confirmed from a public website.

## 7. What It Checks

For each record, the tool compares the following with the organization's website. Clear differences and clear new information are written when the safety rules are met.

| Field | What happens |
|---|---|
| **Phone number** | Updated when the website clearly shows a different number for this facility; added when REDCap has none. |
| **Fax number** | Same as phone. |
| **Email address** | Added when REDCap has none, and replaced when REDCap's email isn't on the website, as long as the website shows exactly one email. When the website shows several, and none is REDCap's, it is flagged for review. |
| **Physical address** | Street, city, state, and ZIP. Updated when the facility has clearly moved (see [section 10](#10-limitations-and-safety-measures)). |
| **County** | Looked up the address with the U.S. Census geocoder (or OpenStreetMap's Nominatim when the Census service is unavailable) and updated it if it differs. |
| **Social media** | Facebook, Instagram, Twitter/X, TikTok, YouTube and WhatsApp. Added or updated from the profile the website links to. When it links to several for one platform, the one whose page name best matches the facility is picked, then the one linked most often, then a named page over a number-only one (`facebook.com/friscomentalhealth` over `profile.php?id=…`). Share buttons, posts, and website-builder placeholder links (such as `facebook.com/wix`) are ignored. |
| **Website** | Updated when the website on file redirects to a new domain that still shows the facility's name, street address, phone, email, or an office in its ZIP code (the site moved). Flagged for review if the new domain shows none of them. |
| **Facility name** | Flagged if the website shows a clearly different name; never changed automatically. |

Each field gets one of these results:

| Result | Meaning | Written to REDCap? |
|---|---|---|
| `MATCH` | The website agrees with REDCap. | No (nothing to change) |
| `NEW` | REDCap is blank, and the website shows exactly one value. | Yes |
| `CHANGED` | REDCap differs, and the website shows exactly one value. | Yes |
| `REVIEW` | Several candidates, or a change that needs a person's decision. | No |
| `NOT_FOUND` | The website doesn't show this information. | No (REDCap is left as is) |

## 8. How the Local AI Model Is Used

Websites present contact information in many ways. Some use clear, machine-readable data that the code can read directly. Others put the address and phone number in a picture-like layout, in separate boxes, in another language, or in a format the code can't reliably parse. For those websites, the tool asks a **local AI model** to read the page.

**When it is used.** Only when the code alone leaves one of these unclear or not found:

- address
- phone
- fax
- email

**NB:** Records that the code can handle on its own never go to AI.

**What does it ask?**

The tool sends the AI only the contact-related text from the website's pages, plus the record's name and address on file, and asks it to list every office the organization shows, each with its name, address, phone, fax, and email. (Whether a website is a doctor/hospital directory is decided by the code, not the AI: the AI once labeled a single organization with offices across Texas as a directory.)

**How the answers are checked (grounding):** AI models can make mistakes or invent values, so the tool never trusts an answer on its own:

- Every phone and fax number the AI returns must appear on the website
- Every email must appear on the website
- Every street address must appear on the website, with its house number, street name, and ZIP code

Anything that doesn't is dropped, and the report says how many values were dropped. The AI's answers then go through exactly the same rules as the code's own findings: one clear value, a matching office, and no conflicts.

**Where it runs:** The model runs on the local computer through Ollama. No website text or REDCap data is sent to an outside AI service.

**Speed:** On a computer without a graphics card, each AI call takes about 1 to 4 minutes. For a quick first pass over many records, use `--no-ai`, then rerun only the records that need it.

**Suggestion-only mode:** With `--ai-suggest-only`, values found by the AI are never written. They're listed for manual review instead, marked as AI suggestions.

The `ai_assisted` column in the Record Summary and the `found_by_ai` column in Field Details show where the AI was used.

## 9. Adding New Locations

Hospital systems, multi-clinic providers, and organizations with several offices often list all their locations on their website. The tool finds these locations and, when it's confident, creates each as a new REDCap record.

### Where locations are found

- The website's "Locations", "Offices", "Find a location" or similar page, and the individual location pages it links to
- Addresses on the home, contact, and about pages
- The AI model's reading of the site, when the AI is used

### Excluding directory websites

**NB:** Some websites are not one organization's site but **databases** of many doctors, clinics, or hospitals. Examples are doctor-finder sites such as `doctors.wellmedhealthcare.com` (thousands of WellMed-affiliated doctors), hospital network directories, and listing sites. **The application does not suggest new locations from these websites.** Their listings are:

- Never created as new records
- Not listed on the New Records - Failed to add sheet either, since the providers they list are mostly not the organization's own offices
- Not used to change the record's own fields. Every proposed change to that record is sent for manual review.

The record's notes say, *"website is a directory of many providers; its listings are not suggested as new locations"*.

A website is treated as a database when any of these are true:

- It is on the tool's list of known directory sites. Examples are WellMed's doctor finder, Zocdoc, Healthgrades, Psychology Today, WebMD, Vitals, Yelp, NPI lookup sites, and SAMHSA's treatment locator. To add another, put its domain in `DIRECTORY_DOMAINS` in `comptroller_automation/locations.py`.
- Most of the locations it lists are other providers: names that carry a doctor's title or credentials, or a business form (Dr., MD, DO, DDS, NP, PLLC, LLC, Inc., and similar), and don't share the organization's name. A health system's own sites, named by what they do or where they are ("Dermatology Clinic", "Emergency Department", "Diamond Hill Health Center"), don't count. That is how JPS Health Network's 25-plus clinics are suggested as its locations, while a doctor finder's list of individual physicians is not.

### No more than 15 new locations per record

If a record's website lists **more than 15** new locations to be added, **none** are added automatically. A list that long is usually from a large health system or chain, and a person should decide which locations belong in REDCap based on whether they offer Recovery Support Services (RSS). In that case, they are **not listed** on the "New Records - Failed to add" tab either. Instead, the record's note in the Record Summary says *"the website gives N new locations for this record, more than the 15 that can be added automatically, so none were added or listed; add the ones that belong in REDCap by hand from the website"*, and `found_multiple_locations` reads Yes.

However, locations from that website that are already in REDCap, closed, or missing details still appear on the "Failed to add" tab with their own reasons. Locations that are already in REDCap, closed, administrative, or with missing details are excluded from the count. For example, JPS Health Network lists 25 new locations, so none are added, while Medical City Arlington lists 9, so all 9 are added. The limit is `NEW_LOCATIONS_PER_RECORD` in `comptroller_automation/fields.py`. (For lists over 50, the location names also aren't sent to the local AI for checking, since they can't be added anyway.)

### When a location becomes a new record

A location is created as a new REDCap record (only with `--apply`) when **all** of these are true:

1. The location is in **Texas**. The organization's offices in other states are never added as new records, and they aren't listed on the New Records - Failed to add sheet. The record's notes say how many were left out.
2. The website is the organization's own, not a doctor/hospital database (see above), and gives no more than 15 new locations for the record (see below).
3. This record's own location was identified on the website. If it wasn't, the tool can't be sure the other listings are the same organization.
4. The location has a name, a street number, a city, a state, and a ZIP.
5. It isn't marked closed or "coming soon", isn't announced as opening on a future date ("Opening December 7 at …"), and isn't an administrative, corporate, or billing office.
6. It isn't already in REDCap. The tool checks the export and, under `--apply`, the live REDCap project for a record at the same street number and ZIP. It also checks the new records already created earlier in the same run.
7. It isn't this record's own office listed under a slightly different address. The tool treats the same street and ZIP with the same phone number, or a house number within a few doors, as the same office.

Anything that doesn't meet these conditions goes to the **New Records - Failed to add** sheet with the reason, for a person to decide.

### Checking location names

The name of a location found in a website's text is usually the line just above its address. Websites sometimes put other text there, so every name is checked before the location is listed:

- **Buttons and headings are rejected**, for example, "Send us a message", "Contact us", "Get directions", "Book an appointment", "Call us", or "Learn more".
- **Status lines are recognized as** "PERMANENTLY CLOSED", "Temporarily closed", "Coming soon", or "Opening soon" between a name and its address marks the location as closed or not yet open (so it isn't added), and the name is taken from the line above.
- **Clear names are accepted as they are.** A name with at least two words that contains a facility word (clinic, center, hospital, health, pharmacy, program, office …) or the organization's own name and doesn't start like a heading ("Our …", "View …"), needs no further check. Most names are like this, for example, "Diamond Hill Health Center".
- **The local AI checks the doubtful rest**, when the AI is available, such as "Women", "Our Locations", or "Trinity Springs". It is asked about one name at a time with a short question, since several at once proved unreliable; each takes about 5 seconds on a CPU. Its answers are remembered between runs in `comptroller_automation/cache/name_checks.json`, so the same text is never asked about twice. Deleting that file makes the tool ask again. A name the AI doesn't confirm is replaced with "&lt;organization&gt; - &lt;City&gt;", and the `note` column explains, for example, *"the website text ", “Send us a message" above this address isn't a location name (checked by the local AI), so it is named after the organization"*. The AI is cautious: in testing, it rejected one real program name, which then simply received the "&lt;organization&gt; - &lt;City&gt;" name. Lists longer than the limit for automatic creation aren’t checked, since they're never created anyway.
- **Names are kept unique.** When two locations would get the same name, the street is added, for example, "PATHWAYS MEDICAL SERVICES - San Antonio - High Ridge" and "PATHWAYS MEDICAL SERVICES - San Antonio - Loop 410".

### What a new record contains

- **Name:** The location's own name, as checked below. If it has none, or the site only labels it by city (for example, "Frisco Location" or "Plano, TX"), it is named "&lt;organization&gt; - &lt;City&gt;". When several locations share the same name, "- &lt;City&gt;" is added so that each record is distinct.
- **Street, city, state, ZIP,** from the website.
- **County,** looked up from the address with the U.S. Census geocoder, or OpenStreetMap's Nominatim as a backup. Left blank if it can't be found; never guessed.
- **Phone, fax, and email** of that location. If no phone can be found for the location, the parent record's phone is used, and staff can correct it later if needed. When the same run also updates the parent record's phone from the website, the updated number is used. The `note` column on the New Records Added tab says when this happened, for example, *"phone not listed for this location; taken from parent record 19."* The fax is **never** copied from the parent; it is left blank unless the location lists its own fax number.
- **Website:** the location's own page whenever the site has one, for example, `https://jpshealthnet.org/locations/diamond-hill-health-center` rather than the `/locations` list it was found on. A location's own page is recognized when the location's name on a list page links to it, or when the page's web address matches the location's name. The name given on that page is also preferred, since it is usually the location's full name. If the location has no page of its own, the page it was found on is used.
- **"Is this a completed record from Phase 1 data collection?"** = No.
- **"Is the organization/facility open or closed?"** = Open, since it was just found listed on the organization's own website.
- **Additional Comments:** a note that the record was added by the Comptroller_Automation tool on a given date, as an additional location of the parent REDCap record, with the page it was found on.

Everything else on the form (services, accreditation, patient limits, and so on) is left blank for staff to complete. REDCap assigns the new record ID itself.

## 10. Limitations and Safety Measures

These rules are built into the tool to make it safe to use with real REDCap data:

- **It never guesses:** If the website shows more than one possible phone number, fax number, email, or address, and the tool can't determine which belongs to this record, nothing changes. The record is flagged for manual review.
- **One unclear field holds the whole record:** When any field on a record needs manual review, none of that record's fields are written automatically. All the proposed changes are listed in the notes for a reviewer to confirm. The only exception is a possible facility-name change, which is reported but doesn't hold the other fields.
- **It never changes the facility name automatically:** Name changes require human review. (The website is only changed in the case described below, when the old site redirects to a new domain that still shows the facility's details.)
- **A different city name for the same street address is taken from the website:** When the street address matches but the website lists a different city name (for example, Horizon City instead of El Paso, or Dallas instead of Addison), the city is updated to the website's value.
- **It doesn't suggest a name change for the same name written another way:** The name counts as a match when any of these are true:
  - The exact name on file appears anywhere in the website's text, ignoring capitals and punctuation (so "M.D" and "MD" are the same) and treating "&" as "and". This holds even when the site's title or logo uses a different brand name.
  - The website's name is an abbreviation of the name on file, for example, "JPS Health Network" for "John Peter Smith Health Network Family Health Center".
  - The website's name contains every distinctive word of the name on file. Generic words such as Clinic, Medical, Health, Psychiatry, Associates, MD, PLLC, or a Texas city name don't count. For example, "Wentian Huang, MD, PhD, PC" matches "Huang Medical Clinic", and "Tri-MED Integrative Psychiatry & Sleep Medicine" matches "TriMed Behavioral and Sleep Medicine".

Other safeguards work the same way. The tool only trusts a website that clearly belongs to the facility; something on it, such as the name, phone, address, or email, must match REDCap. If the website on file now opens a different web address, the new address is used only when that site still shows the facility's details. An address is changed only when the move is clear, for example, when the old address is gone, and the website's only office in the same ZIP code, city, or nearby area is a different one, and only when the new county can be confirmed. Organizations that now list offices only outside Texas, and websites that are directories of many providers, are sent for review without any changes. Addresses visitors can't see on the page, such as hidden template text or outdated search-engine data, are ignored, and anything the local AI suggests must appear on the page. Before writing, the tool rechecks REDCap to ensure it never overwrites someone else's recent changes, and it writes all of a record's changes together or none at all. New locations are created only after confirming they aren't already in REDCap and aren't from a directory; anything uncertain is listed for review instead.

## 11. Setup Requirements

### 11.1 Python and Required Packages

You need **Python 3.11 or later**. If Python isn't installed, download it from [https://www.python.org/downloads/](https://www.python.org/downloads/).

From the `Comptroller_Automation` folder, install the required packages:

```
pip install -r comptroller_automation/requirements.txt
```

Then install the headless Chromium browser once:

```
playwright install chromium
```

Some websites refuse plain automated requests, for example, by dropping the connection; the tool then automatically opens the page in headless Chromium. Without it, those websites are reported as unreachable.

### 11.2 The Local AI Model (Optional, Recommended)

1. Install Ollama from [https://ollama.com](https://ollama.com).
2. Download the model once:

   ```
   ollama pull qwen2.5:7b
   ```

3. Make sure Ollama is running (it normally starts automatically).

When a run starts, the tool reports whether the AI model is available. If Ollama isn't running or the model isn't installed, the tool indicates as much and continues without AI.

### 11.3 The REDCap Export Spreadsheet

You need a current REDCap data export for the project, like a `.csv` or `.xlsx` file. Either of REDCap's export formats works:

- the **labels** export, with column headers like *Organization/Facility name* (for example, `ComptrollerProject20_DATA_LABELS_2026-10-27_1831.csv`)
- the **raw** export, with REDCap variable names as headers, like `record_id` and `ct1` (for example, `ComptrollerProject20_DATA_2026-09-15_2048.csv`)

CSV is recommended: it loads in under a second, while the same data in `.xlsx` takes about 12 seconds. The tool reads each record's current information from this file. It never writes to it.

Use a fresh export for each `--apply` run. Before writing, the tool compares every field it is about to change with REDCap's live value. Any record that changed in REDCap since the export is skipped, so an old export means more skipped records.

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

`redcap_config.py` contains a live credential for the REDCap project. Keep it secure. It is excluded from version control by “. Gitignore”. Don't email it, paste it into chat, or share it with anyone who doesn't need access.

## 12. Running the Tool

Open a terminal in the `Comptroller_Automation` folder. Each run needs one or more Record IDs (given with `--ids`) and the REDCap export.

**How to run:**

```
python -m comptroller_automation --ids <record id(s)> --input <export file> [--apply]
```

**Common examples:**

| You want to… | Command |
|---|---|
| Preview one record | `python -m comptroller_automation --ids 400 --input export.csv` |
| Preview of a range of records | `python -m comptroller_automation --ids 3-50 --input export.csv` |
| Preview a specific mix of records | `python -m comptroller_automation --ids 3 5 8 12-15 --input export.csv` |
| Check every record (see the note below) | `python -m comptroller_automation --all --input export.csv` |
| A quick pass without the AI | `python -m comptroller_automation --ids 1-500 --input export.csv --no-ai` |
| Write the confirmed changes to REDCap | `python -m comptroller_automation --ids 400 --input export.csv --apply` |

**A note on `--all`.** `--all` checks every record in the export, roughly 2,800 records, in a single run. This is rarely practical. Each record takes anywhere from about 10 seconds to more than a minute, depending on the website, the local AI and the address lookups, so a full run can last many hours. A run that long is exposed to network outages, websites or services becoming unavailable, and other interruptions. It also keeps the computer running it busy for the whole time. Running the records in smaller batches with `--ids` (for example `--ids 1-200`, then `--ids 201-400`) is more reliable and easier to review. If a long run does have to be stopped, press Ctrl+C once: the tool saves a report of every record finished so far.

### Dry Run vs. Apply

By default, the tool runs in **dry-run mode**. It works out every change and every new location it would make, but writes nothing to REDCap. The mode is shown before processing begins:

```
DRY RUN: no changes will be written to REDCap (pass --apply to actually write them).
```

With `--apply`, the tool first shows which REDCap projects the credentials point to, and asks you to confirm:

```
========================================================================
--apply is set: this run WILL write changes to REDCap.
  REDCap project: Comptroller Automation Test Project (Project ID: 3282)
  API URL: https://redcap.sph.uth.edu/api/
  Status: development
========================================================================
Proceed with writing changes to this REDCap project? [y/N]:
```

Enter `“y”` to continue. Any other answer, including just pressing Enter, cancels the run without making any changes.

For safe use, always run without `--apply` first and review the results workbook. Use `--apply` only once the proposed changes and new locations have been reviewed and are appropriate.

## 13. Command Options

| Option | Required? | What it does |
|---|---|---|
| `--ids <record id(s)>` | Yes, unless `--all` | One or more Record IDs. Single IDs, ranges (`3-10`), or a mix (`3 5 8-10`) all work. |
| `--all` | Instead of `--ids` | Checks every record in the export. This can take many hours; see the note in [Running the Tool](#12-running-the-tool). |
| `--input <file>` | Yes | The REDCap export to read records from: `.csv` or `.xlsx`, with labels or raw headers. |
| `--apply` | No | Write confirmed changes and new location records to REDCap. Without it, every run is only a preview. |
| `--yes` | No | Skips the confirmation prompt under `--apply` (for scheduled runs). Use with care. |
| `--outdir <folder>` | No | Where the results workbook is saved. Defaults to `output`. |
| `--output-xlsx <file>` | No | Saves the results workbook to this exact file instead of the automatic name. |
| `--skip-new-locations` | No | Doesn't look for the organization's other locations. Faster for very large multi-location providers. |
| `--no-ai` | No | Doesn't use the local AI model. |
| `--ai-model <name>` | No | Which Ollama model to use? Default `qwen2.5:7b`. |
| `--ai-suggest-only` | No | Values found by the AI are listed for review rather than written. |
| `--max-pages <n>` | No | How many pages to read per website? Default 6. |
| `--workers <n>` | No | How many websites to check at once? Default 4. |
| `--browser` | No | Also uses headless Chromium for pages that appear to be built with JavaScript (with very little text on the plain page). Sites that refuse plain requests always get the browser, with or without this option. |
| `--no-browser` | No | Never uses headless Chromium, not even for sites that block plain requests. |
| `--no-county` | No | Skips the Census County lookup. |

## 14. Output and Results

The program prints one line per record and creates one results workbook per run.

### Console Output

```
Please wait, reading ComptrollerProject20_DATA_LABELS_2026-10-27_1831.csv ...
DRY RUN: no changes will be written to REDCap (pass --apply to actually write them).
Processing records from 3 to 9999 (4 record(s) total) ...
Press Ctrl+C once to stop and save a report of the records processed so far; press it twice to quit.

Processing Record    3: ADDISON PAIN AND REGENERATIVE MEDICINE ...
Processing Record    5: KND BEHAVIORAL CLINIC PLCC ...
Processing Record  400: INTERVENTIONAL PAIN AND WELLNESS CENTER PLLC ...
Finished   Record    3: ADDISON PAIN AND REGENERATIVE MEDICINE (1.6s)
Finished   Record    5: KND BEHAVIORAL CLINIC PLCC (2.7s)
Finished   Record  400: INTERVENTIONAL PAIN AND WELLNESS CENTER PLLC (62.4s)
Skipped    Record 9999: ID NOT FOUND

3 record(s) checked: 1 updated in REDCap, 2 need manual review, 2 new location record(s) added.
Results workbook: output\summary_3_to_9999_redcap_id_2026_10_21_143000.xlsx
Total time: 1m 12s
```

- **`Processing Record …`** is printed the moment work on a record starts. Several records are processed at the same time (4 by default; see `--workers`), so a few "Processing" lines may appear before the first "Finished".
- **`Finished Record …`** is printed when a record is fully done, in record-ID order, with the time it took. A record that needs the local AI or many address lookups can take several minutes, and later records wait behind it in the list.
- **`Skipped Record … ID NOT FOUND`** means the requested Record ID isn't in the export given with `--input`.
- The last lines give totals, the results workbook, and the total time for the run.

### Results Workbook

`output/summary_<first>_to_<last>_redcap_id_<timestamp>.xlsx` has four tabs:

- **Record Summary:** Opens by default and is the main tab to review. One row per record found in the export. See the next section.
- **New Records Added:** One row per new REDCap record, the tool created for an additional location. It shows the new Record ID, name, address, county, phone, fax, email, website, whether it was found by the code or the AI, and a `note` (for example, when the phone or fax came from the parent record). In a dry run, this tab lists the locations that **would be** created, with `(dry run: would be created)` in place of a Record ID.
- **New Records - Failed to add:** Every additional location the website lists that was **not** added automatically. That includes an existing REDCap record at the same address, missing details, a closed or administrative office, a list too long to add automatically, or a failed REDCap write. Listings from doctor/hospital databases are not shown here. The column *"These new locations are not added because of the following reason"* explains each one, for example, *"Manual Review Required: An existing REDCap record is at this address: record 972 (HEALTHY HORIZONS CLINIC)"*. These are never added automatically; a person decides whether and how to add them.
- **Field Details:** One row per record and field. Shows:
  - The REDCap value, the website value, the result (MATCH / NEW / CHANGED / REVIEW / NOT_FOUND), and the action taken
  - Whether the AI found the value, a note, every value seen on the site, and the source page

  Use this tab to see exactly why a field was or wasn't changed.

## 15. Record Summary Spreadsheet Column Descriptions

| Column | What it means |
|---|---|
| `redcap_record_id` | The REDCap Record ID for this row is about. |
| `facility_name` | The name on file. |
| `updated_redcap_record` | **Yes,** only if this run actually wrote changes into REDCap for this record. **No Change Required** when the record is already up to date (the notes say *Record up to date*). Otherwise, during a dry run, it reads **Dry Run**. Under `--apply`, it is **No** whenever the record needs manual review, or the REDCap write failed or was skipped. |
| `manual_review_required` | **Yes,** if a person needs to read the notes and make a decision. Reasons include:<br>- several possible values for a field<br>- a possible name or website change<br>- no usable website<br>- under `--apply`, a write that was skipped or failed<br>This is only about this record's own fields; new locations are reported on their own tabs. |
| `found_multiple_locations` | **Yes,** if the website lists other locations besides this one, whether they were added or not. |
| `new_records_added` | The Record IDs for new location records are generated by this record's website. |
| `ai_assisted` | **Yes,** if the local AI model were asked to read this record's website. |
| `notes` | The most important column. See below. |
| `website_changed` / `website_old` / `website_new` | `“website_changed”` is **Yes** when the website on file now redirects to a different domain. When the new site shows the facility's name, address, phone, email, or an office in its ZIP code, `“website_new”` is written to REDCap; otherwise, the record is held for review. `“website_old”` is always the URL on file (**N/A** when the record has no website). The website is never changed automatically. |
| `phone_changed` / `phone_old` / `phone_new` | `phone_changed` is **Yes** when the website clearly shows a new or different phone number that can be written. `phone_old` is always the number on file; `phone_new` appears only when there's a confirmed value. |
| `fax_changed` / `fax_old` / `fax_new` | Same as the phone number, for the fax number. |
| `email_changed` / `email_old` / `email_new` | Same as phone, for the email address. |
| `social_changed` / `social_old` / `social_new` | Same as phone, for Facebook, Instagram, Twitter/X, TikTok, YouTube, and WhatsApp. When several platforms change, each value is labeled (for example, `Facebook: …; Instagram: …`). |
| `address_changed` / `address_old` / `address_new` | Same as phone, for street, city, state, ZIP, and county. `address_old` is always the full address on file in one line. |
| `name_changed` / `name_old` / `name_new` | `name_changed` is **Yes** if the website appears to show a different facility name. Never written automatically. |

Yes/No cells are colored to make the sheet easy to scan **green** for Yes, **amber** for Yes in `manual_review_required` (a person needs to look), and **grey** for No. The same colors are used for `found_by_ai` on the Field Details tab.

When a record needs manual review, its `*_changed` columns read **No** and `*_new` columns are blank, since nothing was written. The proposed values are listed in `notes` instead.

### Reading the `notes` Column

It always takes one of these forms:

- **`Record up to date: No Change Required`:** the website agrees with REDCap, or nothing useful was found. Nothing to do.
- **`Updated: …`:** lists each field that was changed, for example, *"The Phone was updated from (972) 323-7474 to (972)-242-4114."* In a dry run, this reads as **`Potential Update:`**, since nothing has been written yet.
- **`Manual Review Required: nothing was written for this record. Please decide: …`:** something needs a person's decision, so none of the record's fields were written. Each line names the field, explains why it's unclear, and shows both values. For example:

  > - Street: 2 addresses on the website and none match REDCap (REDCap has: 1033 LONG PR RD STE 4; website has: 12800 Hillcrest Road, Dallas, TX 75230, 5728 LBJ Fwy, Suite 250, Dallas, TX 75240)

Records marked as duplicates in REDCap are checked like any other record.

## 16. Reviewing the Results

1. Open the `summary_….xlsx` workbook for the run. It is on the Record Summary tab.
2. Filter `notes` to hide `Record up to date: No Change Required`, so only records with something to act on remain.
3. For each row where `manual_review_required` = **Yes**, read the notes and open the Field Details tab for that record. Check the website and REDCap side by side, and update REDCap manually where appropriate.
4. For each row where `updated_redcap_record` = **Yes**, REDCap has already been updated. The same notes were added to that record's Additional Comments and "change explanation" fields, so REDCap itself shows what changed and when. The new note goes at the top. Any earlier note follows after a blank line, under "Previous note: <date of the last validation>".
5. Check rows where `ai_assisted` = **Yes,** a little more carefully. The AI's values were confirmed to be on the website, but they are worth a quick look.
6. Open the **New Records Added** tab. These records are already in REDCap (after `--apply`). Spot-check a few, and complete their remaining fields (services, accreditation, and so on) as for any new record.
7. Open the **New Records - Failed to add** tab. Read each reason (the *"These new locations are not added because of the following reason"* column) and decide whether the location should be added by hand.

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
- Let reviewers focus on difficult or uncertain cases instead of routine checks.

## 18. Drawbacks and Limitations

- Organization websites can be outdated, incomplete, unavailable, or inconsistent. Information missing from a website doesn't prove REDCap is wrong, so the tool never deletes data.
- A website may list several phone numbers, emails, or locations, making it hard to tell which belongs to a specific record.
- Organizations may have similar names or several locations, and different organizations may share an address.
- **Bot checks can keep the tool out of a website entirely.** A growing number of websites protect themselves with bot checks, such as Cloudflare's "Just a moment…" page, a "403 Forbidden" page, or an "Access denied" page. These checks are designed to allow people to access in a browser and block automated tools. The tool respects them and doesn't try to get past them, so for those websites, it can't read anything:
  - None of the record's fields can be verified or updated
  - No other locations can be found or added, even when the website clearly lists them (for example, an organization's Locations page that a person can open normally)
  - A website that worked in one run can start blocking in a later one; the same record may be updated one month and blocked the next

  These records are marked *"the website on file couldn't be opened (the site blocks automated access with a bot check …)"* for manual review. A person needs to open the website in a browser, update the record, and add any new locations by hand. Running from a different network or at another time sometimes gets through, but it can't be relied on.
- Website layouts change, which can affect automated extraction.
- Some websites rely on JavaScript; use `--browser` for these.

**Limitations of the new-location feature:**

The tool may not find any location of an organization. Websites that block automated access with a bot check can't be read at all, so none of their locations are found (see above). Some sites list locations on pages the tool doesn’t reach, load them dynamically, or present them in a way that can't be parsed reliably. The tool reads at most 30 location pages per website. Directory detection is based on a list of known sites, name patterns, and the AI's judgment, so an unfamiliar directory could occasionally slip through. The duplicate check relies on the street number and ZIP code, so a location already in REDCap under a mistyped address might not be recognized.

For these reasons, don't treat the New Records Added and New Records - Failed to add tabs as a complete or perfect list. When a full location review matters, also check the organization's website by hand, including its Locations, Contact, or Find a Location pages.

## 19. Data Quality and Safety Rules

- Test the program on a small number of records before processing a large batch.
- Run in dry-run mode first and review the results before using `--apply`.
- Try `--apply` first on the test REDCap project before using it on the production project.
- Use only approved REDCap access methods and authorized project credentials.
- Keep the REDCap API token secure. Don't email it, put it in chat, or share it with unauthorized users.
- Don't bypass login, multifactor authentication, CAPTCHA, or other security controls.
- Don't change the value automatically when the website information is unclear.
- Don't treat information missing from a website as proof that the REDCap value is wrong.
- Don't change the facility or organization name automatically.
- If any field in a record needs manual review, the program doesn't update that record's other fields in the same run.
- Before writing, the program re-checks the live REDCap values. If any changes have occurred since the export, nothing on that record is written. Changes are always all-or-nothing per record.
- Values found by the AI model are only used when they appear on the website.
- New locations are created only after the directory and duplicate checks. Anything uncertain goes to the New Records - Failed to add tab with its reason; nothing is silently dropped.
- Review the results workbook after each run and keep it as part of the project documentation.
- Review the New Records Added tab after each `--apply` run and complete the new records' remaining fields by hand.

## 20. Simple Technical Flow

```
REDCap export
  → read the record (name, address, phone, fax, email, website, social media)
  → read the website (home, contact, about, and locations pages)
  → extract contact details and locations with code
  → find this record's own location (by address, then phone)
  → decide each field (MATCH / NEW / CHANGED / REVIEW / NOT_FOUND)
  → still unclear? Ask the local AI model, keep only values found on the page, and decide again
  → apply the safety rules (redirect, site belongs to facility, one unclear field holds the record)
  → other locations: directory check, eligibility check, duplicate check
  → dry-run results workbook → human review
  → optional --apply: confirm project, re-check live values, update records, create new location records
  → results workbook (Record Summary, New Records Added, New Records - Failed to add, Field Details)
```

### Program Structure

The tool is a small Python package in `Comptroller_Automation/comptroller_automation/`:

| File | Purpose |
|---|---|
| `__main__.py` | The command line reads options, runs the records, writes to REDCap under `--apply`, and saves the workbook. |
| `fields.py` | The fields checked, their REDCap variable names, and the bookkeeping fields set on each update. |
| `records.py` | Loads each record's information from the REDCap data export file (`.csv` or Excel). |
| `scraper.py` | Fetches the website: home, contact, and about pages. |
| `extract.py` | Pulls phone numbers, emails, social media links, names, and locations from the pages. |
| `locations.py` | Finds location pages, detects directory websites, and names and checks new locations. |
| `ai.py` | Asks the local AI model to read a website and check its answers against the page. |
| `normalize.py` | Standardizes phone numbers, addresses, states, and social media links so they compare fairly. |
| `county.py` | Looks up the county and map position for an address with the U.S. Census geocoder, with OpenStreetMap's Nominatim as a backup. |
| `verify.py` | The decision rules for each field. |
| `redcap.py` | The REDCap API: reading live values, updating records, and creating new ones. |
| `report.py` | Build the results workbook. |

Support files:

- `redcap_config.py`: the REDCap URL and API token. A credential; keep it secure.
- The REDCap export: the read-only source of each record's current data.
- `output/`: the results workbooks.
- `comptroller_automation/tests/`: automated checks of the comparison and grounding rules.

## 21. Questions and Troubleshooting

**The tool says a Record ID wasn't found.**
That Record ID isn't in the export given with `--input`. Check the ID to confirm you're using the correct export file.

**A record has no website on file.**
The tool reports it for manual review and moves on. It doesn't search the internet for a replacement.

**It says, "Local AI not available".**
Ollama isn't running, or the model isn't installed. Start Ollama, run `ollama pull qwen2.5:7b`, and run the tool again. Until then, the tool works without AI.

**The notes say, "The site blocks automated access with a bot check."**
The website responded with a bot-check page, such as Cloudflare's "Just a moment…", "403 Forbidden", or "Access denied", instead of its content. The site's owner has chosen to keep automated visitors out, so the tool doesn't try to bypass the check, and nothing on the record is read or changed. Open the website in your browser and update the record manually. Before this check existed, such pages were mistaken for the clinic's site (for example, a possible new name, "Just a moment…," and no locations found).

**A website opens fine in my browser, but the tool says it's invalid or unreachable.**
Some websites block automated requests. The tool automatically retries those pages in headless Chromium. Check that Playwright and its browser are installed (`pip install playwright`, then `playwright install chromium`) and that `--no-browser` wasn't used. A site that blocks even the headless browser has to be checked by hand.

**How do I stop running partway?**
Press **Ctrl+C** once. The tool stops after the record it's working on, saves the results workbook for every record it has finished checking, and exits within a second or two. It doesn't wait for websites or AI calls still in progress. Under `--apply`, the record being written is finished first, so a REDCap write is never cut off halfway. Records that had finished checking but hadn't reached their turn to be written appear in the workbook, but are not written. Press Ctrl+C a second time to quit immediately without saving a workbook. Re-run the remaining records afterward; any record that has already been updated simply shows no changes.

**The run is very slow.**
The AI model is probably running without a graphics card. Run with `--no-ai` for a quick pass, then re-run only the records that need manual review without `--no-ai`. Lowering `--max-pages` or using `--skip-new-locations` also helps.

**Can I re-run the tool safely?**
Yes. Every run rechecks REDCap's live values right before writing, so it won't overwrite changes someone else made. New locations are checked against the live project for duplicates, so re-running doesn't add the same location twice.

**A new location was created that shouldn't have been.**
Delete or correct the record in REDCap. If the website is an unrecognized directory, add its domain to `DIRECTORY_DOMAINS` in `comptroller_automation/locations.py` so it's excluded in future runs.

**Where do I get a REDCap API token?**
From REDCap's **API** page for the project you want the tool to update. Each token only works for one project.

**I don't have `redcap_config.py` set up. Can I still use the tool?**
Yes. Dry runs work without it. It's only needed with `--apply`.

**What happens if the REDCap token expires or is invalid?**
The tool checks the token during the project-confirmation step and displays a clear message if it's rejected. If it expires partway through a run, the tool stops at that point. Records already processed are still included in the workbook. Update `redcap_config.py` and re-run the remaining records.

---

*Comptroller Automation · Operations Manual*
