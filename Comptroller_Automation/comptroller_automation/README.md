# Comptroller Automation

Checks each REDCap facility record against the facility's own website. Where the website clearly shows a new or different value, the tool updates REDCap through the API. It also adds the organization's other locations as new records. A local AI model reads websites the code can't parse cleanly.

The full operations manual is [Comptroller_Automation.md](../Comptroller_Automation.md), one folder up.

This README sits with the code (`Comptroller_Automation/comptroller_automation/`). Run every command below from the **`Comptroller_Automation` folder**, one level up, where `requirements.txt`, `redcap_config.py` and your export file are.

**Fields checked:** facility name, street, city, state, ZIP, county, phone, fax, email, website, and Facebook, Instagram, Twitter/X, TikTok, YouTube and WhatsApp.

## Setup

```
pip install -r requirements.txt
playwright install chromium
copy redcap_config.py.sample redcap_config.py     # then fill in api_url and api_token
```

Some websites refuse plain automated requests, for example by dropping the connection. The tool then opens the page in headless Chromium automatically.

`redcap_config.py` holds a live API token. Git ignores it. Don't share it.

**Local AI (optional, on by default):** install [Ollama](https://ollama.com), then run `ollama pull qwen2.5:7b`. If Ollama isn't running, the tool says so and continues without AI.

## Running

Run these from the `Comptroller_Automation` folder:

```
python -m comptroller_automation --ids 1-50 --input ComptrollerProject20_DATA_LABELS_2026-10-27_1831.csv
python -m comptroller_automation --ids 3 5 12-15 --input <export.csv>
python -m comptroller_automation --ids 1-50 --input <export.csv> --apply
python -m comptroller_automation --all --input <export.csv>
```

Each run takes either `--ids`, with one or more record IDs and ranges (`3`, `10-20`, or a mix like `3 5 12-15`), or `--all`.

**A note on `--all`.** `--all` checks every record in the export, roughly 2,800 records, in a single run. This is rarely practical. Each record takes anywhere from about 10 seconds to more than a minute, depending on the website, the local AI and the address lookups, so a full run can last many hours. A run that long is exposed to network outages, websites or services becoming unavailable, and other interruptions. It also keeps the computer running it busy for the whole time. Running the records in smaller batches with `--ids` (for example `--ids 1-200`, then `--ids 201-400`) is more reliable and easier to review. If a long run does have to be stopped, press Ctrl+C once: the tool saves a report of every record finished so far.

`--input` takes a REDCap export as `.csv` or `.xlsx`. Either kind of export works: the "labels" export (column headers like *Organization/Facility name*) or the raw export (headers like `ct1`). A CSV loads in under a second, against about 12 seconds for the same data as `.xlsx`.

Each run ends with a summary line, the path of the results workbook and the total time it took (for example `Total time: 12m 40s`).

**To stop a run,** press **Ctrl+C** once. The tool finishes the record it's on, saves a report of every record checked so far, and exits within a second or two, without waiting for websites or AI calls still in progress. Press Ctrl+C a second time to quit at once without a report. A REDCap write is never cut off halfway.

Every run is a **dry run** unless you add `--apply`. With `--apply`, the tool first shows which REDCap project the token belongs to and asks you to confirm.

| Option | Meaning |
|---|---|
| `--ids` | record IDs and ranges to check, e.g. `--ids 3 5 10-20` (this or `--all` is required) |
| `--all` | check every record in the export; can take many hours (see the note above) |
| `--input` | **required:** the REDCap export (.csv or .xlsx) |
| `--apply` | write confirmed changes and new location records to REDCap |
| `--yes` | skip the confirmation prompt (for scheduled runs) |
| `--outdir DIR` | where the report goes (default `Comptroller_Automation/output/`) |
| `--output-xlsx FILE` | exact report file name instead of the automatic one |
| `--skip-new-locations` | don't look for other locations |
| `--no-ai` | don't use the local AI model |
| `--ai-model NAME` | Ollama model to use (default `qwen2.5:7b`) |
| `--ai-suggest-only` | never write a value the AI found; list it for review instead |
| `--max-pages N` | pages read per website (default 6) |
| `--workers N` | websites checked at once (default 4) |
| `--browser` | also use headless Chromium for pages that look built with JavaScript |
| `--no-browser` | never use headless Chromium, not even for sites that block plain requests |
| `--no-county` | skip the Census county lookup |
| `--suggest-websites` | for records with no website, search the web and suggest one (off unless given; never written to REDCap) |

## How each field is decided

| Result | Meaning | Written? |
|---|---|---|
| `MATCH` | website agrees with REDCap | no |
| `NEW` | REDCap blank, website shows exactly one value | yes |
| `CHANGED` | REDCap differs, website shows exactly one value | yes |
| `REVIEW` | several candidates, or a risky change | no, a person decides |
| `NOT_FOUND` | nothing on the website | no, REDCap is left as is |

When a website lists several offices, the tool first finds this record's own office by matching its address or phone. It then takes that office's phone, fax and email.

## When the local AI is used

The AI is only asked when the code leaves the address, phone, fax or email unclear or not found. It reads the contact-related text of the pages and lists each office with its name, address, phone, fax and email. Every value it returns has to appear in the page text. Anything that doesn't is dropped, so it can't invent data.

On a computer without a graphics card, each AI call takes about 1–4 minutes. Use `--no-ai` for a fast first pass.

## New locations

The tool reads the site's "Locations" pages, and its contact pages, for other offices. A location becomes a new REDCap record (under `--apply`) only if all of these hold:

- the location is in **Texas**. Offices in other states are never added; the notes give a count. The state is `TARGET_STATE` in `fields.py`.
- the site is the organization's own, not a doctor/hospital database (see below)
- this record's own office was identified on the site
- the location has a name, street number, city, state and ZIP, and isn't closed, "coming soon", announced as opening on a future date, or an administrative office
- it isn't already in REDCap at the same street number and ZIP. Both the export and the live project are checked.
- **no more than 15 new locations for the record.** If a record's website gives more than 15 that would be added (`NEW_LOCATIONS_PER_RECORD` in `fields.py`), **none** are added, and they aren't listed on "New Records - Failed to add" either. The record's note in the Record Summary says *"the website gives N new locations for this record, more than the 15 that can be added automatically, so none were added or listed; add the ones that belong in REDCap by hand from the website"*, and `found_multiple_locations` reads Yes. Locations already in REDCap, closed or incomplete don't count toward the 15.

**Doctor and hospital databases.** Some websites are databases of many doctors, clinics or hospitals, for example `doctors.wellmedhealthcare.com`, Zocdoc, Healthgrades or Psychology Today. **The app does not suggest new locations from these sites at all.** Their listings appear on neither New Records Added nor New Records - Failed to add, and nothing on the record is written. The record's note says *"website is a directory of many providers; its listings are not suggested as new locations"*. A site is treated as a database when any of these is true:

- it's on the list of known directory sites (`DIRECTORY_DOMAINS` in `locations.py`; add a domain there to exclude it)
- most of the locations it lists are other providers: names with a doctor's title or credentials, or a business form (Dr., MD, DDS, PLLC, LLC, Inc …) that don't share the organization's name. A health system's own sites named by what they do ("Dermatology Clinic", "Diamond Hill Health Center") don't count, so a system like JPS gets its locations suggested.

**Location names are checked.** Text written next to an address isn't always a name. Buttons and headings ("Send us a message", "Contact us", "Book an appointment") are rejected. A status line ("PERMANENTLY CLOSED") marks the location as closed, and the name is taken from the line above it. Names that are clearly names, with at least two words including a facility word (clinic, center, hospital, health …) or the organization's own name, are accepted as they are. When the local AI is available, it checks only the doubtful rest, one at a time (about 5 seconds each on a CPU). Its answers are remembered in `comptroller_automation/cache/name_checks.json`, so a name is never asked about twice. A name it doesn't confirm is replaced with "&lt;organization&gt; - &lt;City&gt;", and the `note` column says so. Two new locations never share a name: "&lt;organization&gt; - San Antonio - High Ridge" and "… - San Antonio - Loop 410".

A new record's website is the location's own page when the site has one, for example `…/locations/diamond-hill-health-center`, not the `/locations` list. The tool recognizes it when the location's name links to it, or when the page address matches the name. An unnamed location is called "&lt;organization&gt; - &lt;City&gt;". If no phone can be found for the location, the new record uses the parent record's phone (the updated number when this run updates the parent too); the `note` column on New Records Added says so. The fax is never copied from the parent, so it stays blank unless the location lists its own. Each new record is set to Open, with "completed record from Phase 1" = No, and a comment naming the parent record.

Locations that don't qualify go on the "New Records - Failed to add" sheet with the reason.

## Safety rules

- **No guessing.** Several candidates means `REVIEW`. When any field on a record needs review, none of that record's fields are written. Facility name is the only exception.
- **The name is never written.**
- **A website that moved is updated.** If the website on file redirects to another domain and the new site shows any of the facility's details, the website is updated to the new address. Those details are its name, street address, phone, email, or an office in its ZIP code. For example, `mirmd.com` → `prestigeclinicdfw.com` for Prestige Clinic. The rest of the record is then checked as usual. If the new site shows none of them, nothing on that record is written and it goes to manual review.
- **No name change for the same name in another form.** No name change is suggested when:
  - the name on file appears anywhere on the website, ignoring capitals and punctuation (so "M.D" = "MD") and treating "&" as "and"
  - the website's name is an abbreviation of it ("JPS Health Network" for "John Peter Smith Health Network …")
  - the website's name contains every distinctive word of the name on file. Generic words like Clinic, Medical, Psychiatry, Associates, MD and Texas city names don't count, so "Wentian Huang MD, PhD, PC" matches "Huang Medical Clinic".

  Page titles holding an address, phone number or more than 8 words aren't treated as names.
- **The site must be this facility's.** At least one of name, phone, street or email has to match, or the site lists an office in the record's ZIP code. If not, nothing is written.
- **Social links: one is picked.** When the site links to several pages on the same platform, the tool picks the one whose name best matches the facility, then the one linked most, then a named page over a number-only one. It doesn't ask for review.
- **Email follows the website when it's clear.** If the website shows exactly one email, it's added when REDCap has none, and replaces REDCap's when that one isn't on the site. If the site shows several emails and none is REDCap's, it's flagged for review.
- **An address is only moved when it's clear.** That means one of:
  - the site lists a single address
  - this facility's phone is listed at a single address
  - the address on file is gone and the site's only office in the same ZIP code (or city) is a different address. The site's other offices then become new locations.
  - the address on file is gone, no office is in its ZIP or city, and one office is clearly the nearest: within 25 miles, and at most half as far as the next one. Distances come from the Census geocoder. The other offices become new locations.

  The county comes from the US Census geocoder, or from OpenStreetMap's Nominatim when the Census service is down or can't match the address. If the county can't be confirmed, the address isn't moved. For distances only, an office whose exact address can't be found is placed at the middle of its ZIP code.
- **City names follow the website.** When the street address matches but the website gives a different city name (Horizon City instead of El Paso), the city is updated to the website's.
- **The organization may have left Texas.** If the address on file is gone and the site lists offices only in other states, the street is flagged *"Address has changed, but no location found in Texas"* and nothing on the record is written.
- **Nothing is written from a directory website** (a doctor/hospital database listing many providers). Everything is sent for review, and no new locations are suggested from it.
- **A deep-linked page is read on its own.** If the website on file is one page of a bigger site, only that page is used for this record's details.
- **Hidden addresses are ignored.** Template-built sites sometimes keep an old or sample address in a block the stylesheet hides. When a site seems to list several addresses, the tool opens those pages in headless Chromium and drops a hidden address that sits beside visible ones. A page where most addresses are hidden, like a "Locations" list in click-to-open sections, is kept as is. Stale schema.org data, which only search engines read, is handled the same way: if it holds an address and phone that no visitor sees, while the site visibly shows a different address, both are ignored.
- **Placeholder social links are ignored.** Website-builder accounts like `facebook.com/wix` are never written.
- **REDCap is re-checked before every write.** If any field changed since the export, the whole record is skipped.
- **Sites that block automated visitors are reported, not worked around.** Some sites answer with a bot check (Cloudflare's "Just a moment...", "403 Forbidden", "Access denied") instead of their content. The tool doesn't try to get past it. The record is marked *"the website on file couldn't be opened (the site blocks automated access with a bot check…)"* for a person to check in a browser.
- **Records without a website are reported and skipped.** Nothing is searched for or guessed, unless you add `--suggest-websites` (see below). Records marked as duplicates in REDCap are checked like any other record.

### Website suggestions (`--suggest-websites`)

Off by default. With this option, a record that has no website gets a suggested one. The tool:

1. searches Bing (or DuckDuckGo if Bing gives nothing) for the facility's name, city and state, and its phone number
2. skips directories, social media and listing sites (Yelp, Healthgrades, NPI sites, Facebook …)
3. opens each remaining result

A site is only suggested when it shows the record's phone number or street address, along with a matching name:
- **high confidence:** phone and address both match
- **medium confidence:** one of them matches, plus the name

The suggestion appears in the `website_suggested` column and the notes. It is **never written to REDCap**, and the record's other fields aren't checked against it. A person confirms it and adds it by hand, and the next run then verifies the record as usual. Searches are spaced a few seconds apart, so this adds time to runs that include records without a website.

Each updated record also gets these fields set (configured in `fields.py`, in this folder):

- `change` = Yes
- `validated` = Yes
- `validation_date` = today
- `ogcomplete` (completed record from Phase 1) = Yes
- a dated note in `change_explain` and `general_comments`

## Report

Each run writes `Comptroller_Automation/output/summary_<first>_to_<last>_redcap_id_<timestamp>.xlsx`. It's laid out like the earlier verifier's report:

- **Record Summary:** one row per record. The first columns are the status flags (`updated_redcap_record`, `manual_review_required`, `found_multiple_locations`, `new_records_added`, `ai_assisted`), then `notes` (*No Change Required* / *Updated* / *Potential Update* / *Manual Review Required*). After those come the `*_changed`, `*_old` and `*_new` columns for each field.
- **New Records Added:** new records created, or "would be created" in a dry run.
- **New Records - Failed to add:** locations not added, with the reason.
Yes/No cells are colored: green for **Yes**, amber for a **Yes** in `manual_review_required`, and grey for **No**.

- **Field Details:** one row per record and field, with the REDCap value, website value, result, action, whether the AI found it, and the source page.

## Limitations

- **Bot checks can block the tool.** Some websites show a bot check to automated tools (Cloudflare's "Just a moment…", "403 Forbidden", "Access denied") instead of their content. The tool doesn't try to get past these. For such a site it can't verify or update the record's fields, and it can't find or add the organization's other locations, even when a person can see them in a browser. A site that worked once can start blocking later. These records are flagged for manual review, to be checked and updated by hand.
- **Not every location is found.** Locations on pages the tool doesn't reach, loaded by scripts, or in unusual layouts can be missed. The New Records tabs aren't a complete list.
- **Address lookups can fail.** The Census geocoder (or the OpenStreetMap backup) doesn't match every address, so some counties are left blank for staff to fill in. A county is never guessed.
- **The local AI is slow on a CPU** (about 1–4 minutes per call), and offices that only the AI finds can vary between runs.

## Code layout

| File | Purpose |
|---|---|
| `fields.py` | field list, REDCap variable names, bookkeeping fields |
| `records.py` | reads the REDCap export (.csv or .xlsx, labels or raw headers) |
| `scraper.py` | fetches the home page plus contact, about and location pages |
| `extract.py` | pulls phones, emails, social links, names and locations from pages |
| `locations.py` | finds location pages, detects directories, names and checks new locations |
| `ai.py` | local AI (Ollama) extraction and on-page grounding |
| `normalize.py` | phone, address and social-link normalizing for fair comparison |
| `county.py` | county and map-position lookup (US Census geocoder, OpenStreetMap Nominatim as backup) |
| `verify.py` | the per-field decision rules |
| `redcap.py` | REDCap API calls, updates and new-record creation |
| `report.py` | Excel report |

Tests: from the `Comptroller_Automation` folder, `python -m pytest comptroller_automation/tests` (needs `pytest`).
