# Duplicate Checker Manual

## What this is about

The Comptroller project keeps a directory of behavioral health and substance use treatment
facilities in Texas, stored in REDCap. Over time, some facilities were entered more than
once, sometimes with a slightly different name, a differently written address, or an old address.

These duplicates have to be found and marked. Until now, a person did this by hand: comparing each
pair of suspicious records, visiting websites, and deciding. The Duplicate Checker is a small program
that does the same job. It follows the steps a careful reviewer would follow, and it hands back the
cases it cannot settle.

**In short:** the program first rules out records that are clearly *not* duplicates, then confirms
the ones that clearly *are*, and only then looks more closely at the hard cases. Anything it is not
sure about is marked **"Manual review required"** for a person to check.

## How it works: finding the possible duplicates

A separate script (written in R) scans the whole directory and puts records into **groups of
possible duplicates**. It flags two records when they are in the same city and either their names
or their street addresses look alike.

This first scan is intentionally generous, so it does not miss real duplicates. As a result, many
flagged records are not duplicates at all. For example, five different hospitals on the same long
street (Harry Hines Blvd in Dallas) were flagged together just because their addresses looked
similar. The Duplicate Checker's job is to sort these groups out.

Within each group, the program compares **every pair** of records, one pair at a time.

## Step 1: Put names and addresses into the same format

The same place can be written in many ways. Before comparing anything, the program rewrites names
and addresses into one standard form, so that formatting differences do not get in the way.

| Written in REDCap | Treated as |
|---|---|
| 6110 **W** Parker **Rd** | 6110 **West** Parker **Road** |
| 17070 Red Oak Dr **#409** | 17070 Red Oak Dr **Suite 409** |
| Integrated Psychotherapeutic Services **LLC** | Integrated Psychotherapeutic Services |

Each address is split into its parts: **building number**, **street name** and **suite number**.
These parts are compared separately. This matters because "12870 Hillcrest Rd" and
"12800 Hillcrest Rd" look almost identical as text, but they are different buildings.

Names are compared by their distinctive words. Common words like "Health", "Center", "Clinic" or
"Medical" are not counted as a match on their own, because hundreds of unrelated facilities share
them.

## Step 2: Filter out the records that are clearly NOT duplicates

These rules remove the most common false alarms. No website or AI is needed.

1. **Different name and different building.** If the names share no distinctive words *and* the
   building numbers or streets are different, the two records are **not duplicates**.
   *Example: "Parkland Memorial Hospital" at 5200 Harry Hines Blvd and "UT Southwestern Medical
   Center" at 6000 Harry Hines Blvd.*

2. **Same building, different suite, different name.** Medical office buildings hold many separate
   practices. If two records are in the same building but in different suites, have different
   names and different phone numbers, they are **not duplicates**.
   *Example: "Origins Counseling" in Suite H226 and "Best Service Pain and Rehab" in Suite 200 of
   the same building.*

If the two records have the same phone number, the program does not apply these rules, because a
shared phone number is a strong hint that they may be the same place.

## Step 3: Confirm the records that clearly ARE duplicates

3. **Same or similar name at the same address.** If the names match (or share distinctive words)
   and the addresses are the same once standardized, the records are **duplicates**.
   *Example: "Texas Health Seay Behavioral Health Hospital" at "6110 W Parker Rd" and "Texas Health
   Seay Behavioral Health Center Plano" at "6110 West Parker Road".*

## Step 4: Set aside cases where people have decided both ways before

4. **An individual provider's name at an organization's address.** Sometimes one record is a
   person (for example "Dr Frank Murphy" or "Jane Smith MD") and the other is a practice at the
   exact same address. Past manual reviews went both ways on these: sometimes it was the same
   practice listed under a provider's name, sometimes a separate provider. The program does not
   guess. It marks them **"Manual review required"**.

In testing, Steps 2 to 4 settled about half of all record pairs without needing anything else.

## Step 5: Check the facility's website (only for the remaining cases)

For pairs that are still undecided, the program opens each record's website (the link stored in
REDCap) and reads it, the way a reviewer would:

- Does the website show the **same address** that we have in REDCap?
- Does one record's website show the **other record's address or phone number**? That is a strong
  sign the two records are connected.
- Is the website **broken**, or does it belong to a completely **different business**? (One record
  linked to a website for an unrelated business, which is useful to know but is not evidence
  either way.)

One special case: sometimes a record's website no longer shows that record's address, but shows
the other record's address instead. This can mean the facility **moved** (so the old record is a
duplicate), but it can also mean the organization has several locations and its website lists
only the main office (so the records are separate branches). The program cannot tell these apart
reliably, so these pairs are also marked **"Manual review required"**, with the website evidence
written in the note.

Websites are only visited when they are needed. Records already settled in Steps 2 to 4 are not
looked up. Some websites block automated visits, or only show their address after the page has
fully loaded. For those, the program opens the page in a hidden web browser, the same way a person
would. In testing, this recovered about 1 in 5 websites that could not be read otherwise.

## Step 6: Ask an AI assistant about what is left (optional)

This step only runs when the program is started with the `--with-ai` option. Without it, the
hard cases that remain are simply marked **"Manual review required"**, and the program runs on
rules and website checks alone.

With `--with-ai`, the hard cases that remain are given to an **AI assistant** (a language model called
*qwen2.5*). The AI runs entirely on the local computer, so no facility data is sent to any outside
AI service.

The AI does not look anything up by itself. It receives a short summary of the evidence from the
earlier steps (both records' names, addresses and phone numbers, whether the addresses match,
and what the websites showed), plus guidance written from past manual reviews, for example:

- Same address *and* same phone number usually means the same facility, even if the name changed.
- Different suites with different names are usually different practices.
- The same name at different addresses is usually a separate branch.

The AI answers **duplicate**, **not a duplicate**, or **unsure**, gives a confidence level, and
explains its reason in a sentence or two. If it is unsure, or less than 60% confident, the pair is
marked **"Manual review required"**.

*Example the AI handled: "Carrollton Springs Treatment Center" and "Regency Hospital of North
Dallas" have completely different names but the same address, and Carrollton Springs' own website
shows Regency's phone number. The AI judged them to be the same facility. The NPI provider registry
supports this: Regency was an older hospital at that building, and Carrollton Springs has been
there since 2011.*

## Step 7: Put the pairs together and pick the record to keep

Once every pair in a group is decided, records that were matched as duplicates are joined
together. If A matches B and B matches C, then A, B and C are all the same facility.

Within each set of duplicates, the program recommends **keeping the most complete record**, the
one with the most information filled in (address, phone, email, website, social media and so on).
The others are marked as duplicates of it.

Every record then ends up in one of three places:

| Result | Meaning |
|---|---|
| **Duplicate** | Same facility as another record. The record to keep is named. |
| **Not a duplicate** | A different facility, or a different location of the same organization. |
| **Manual review required** | The evidence is unclear. A person should decide. |

## What the program produces

- **The "Tracking Duplicates" tab**, in a copy of the duplicates workbook, filled in the same
  layout used for manual reviews. Each true duplicate is listed on the row of the record being kept.
  - **"notes (add to redcap)"** holds the note that goes into REDCap.
  - **"notes from automation"** explains the decision, and whether it came from a rule check or
    the AI review.
  - **"status"** is a colored column: **Not Duplicate** (grey) for records that are not
    duplicates, and **need review** (orange) for records a person still has to decide. After a
    REDCap update, it also shows **updated** (green), **skipped** (yellow) or **failed** (red) for
    each set of duplicates.
- **A detailed results workbook** (only when asked for with `--debug`), showing every record's
  result, every pair that was compared, and what was found on each website.

**REDCap is never changed automatically.** Only when the person running it explicitly asks, and
answers **y** when the program asks at the start, does it record its decisions in REDCap:

- each **duplicate record** gets "Is this a duplicate record?" = **Yes**, and the note
  "By automation: duplicate with 145" (the record kept) in "Additional Comments";
- the **record kept** gets "Is this a duplicate record?" = **No**, and the note
  "By automation: duplicate with 5015" (its duplicates);
- both are marked **Validation = Yes** with **today's date** as the Validation Date.

The new note goes at the top of "Additional Comments". Any comment already there is kept
underneath it, after a line showing the record's previous validation date.

It never overrides a decision a reviewer has already entered. Records that REDCap already shows
the same way are left alone. If a reviewer marked any record in the set differently, the whole set
is left alone.

Afterwards, the Tracking Duplicates tab shows what happened. Records that were not updated, for
example because REDCap already had the same answer, are explained in the "notes (add to redcap)"
column. Records that were updated are listed in the "notes from automation" column.

**The REDCap change log.** Every time the program changes REDCap, it also saves a file named
`REDCap_Update_Log_<date>_<time>.xlsx` in the project folder, for example
`REDCap_Update_Log_2026-10-08_1651.xlsx`. It lists every record the program planned to change and
whether it was **updated**, **skipped** or **failed**, with the reason. It also records each
record's **old values**: the duplicate answer, validation, validation date and comments as they
were before the change. The file is saved before anything is sent to REDCap, so even an update
that is interrupted can be checked and, if needed, undone by hand from these old values. Keep it
together with the review file.

## How well it works

The program was tested on 60 groups (140 records) that had already been reviewed by hand. Of
these, 76 records had a manual decision to compare against:

| | Without the AI | With the AI (`--with-ai`) |
|---|---|---|
| Records the program decided | 50 | 71 |
| Agreed with the manual review | **all 50 (100%)** | **67 of 71 (94%)** |
| Left for manual review | 26 | 5 |

- In both modes it **never called a record a duplicate when the reviewer had said it was not**.
- Without the AI, the program only decides the clear-cut cases, and it got every one of them
  right. More records are left for a person to check.
- With the AI, many more records are decided, at the cost of a few mistakes. Those mistakes went
  the safe way: it missed a few real duplicates, which a reviewer can still catch. The duplicates
  it missed needed outside knowledge, such as a hospital that was renamed after a merger, or two
  duplicate records that the first R scan had placed in different groups.

On the full list of 285 groups (655 records), with the AI, it found 418 records that are not
duplicates and 189 that are duplicates, and it set aside 48 for manual review. That is about 7%
of the records, instead of all of them.

## Limits to keep in mind

- The program only compares records **within the same group** from the R scan. Two duplicates that
  the scan put in different groups will not be compared.
- It cannot make phone calls or do outside research. Cases like renamed organizations, mergers or
  closures may still need a person.
- Some websites block automated visits or are out of date, so a website check is not always
  possible.
- The AI can be wrong, especially when one organization has several locations with the same name.
  Duplicates decided by the AI are labeled as such, so they can be double-checked before REDCap
  is updated.

**The program is a first pass that does most of the routine sorting. The final decision on unclear
cases stays with a person.**

## Setting up a computer to run it

These steps are needed once per computer. They are written for Windows. A computer with at least
16 GB of memory is recommended. A graphics card is not needed, but the AI step is faster with one.

1. **Install Python.** Download Python 3.10 or newer from python.org. During installation, tick
   **"Add python.exe to PATH"**.

2. **Install the program's helper packages.** Open PowerShell in the project folder (the folder
   that contains `dup_checker`) and run:

   `python -m pip install -r dup_checker\requirements.txt`

   This installs the tools the program uses to read Excel files, visit websites and write the results.

3. **Install the hidden web browser.** In the same window, run:

   `python -m playwright install chromium`

   This downloads a copy of the Chrome browser that the program uses for websites that block
   automated visits. It does not affect the browser you normally use.

4. **Install the AI assistant** (only needed for `--with-ai`). Download and install **Ollama** from
   ollama.com. It runs quietly in the background. Then download the AI model by running:

   `ollama pull qwen2.5:7b`

   This is a one-time download of about 5 GB. Running `ollama list` afterwards should show
   `qwen2.5:7b`. Everything the AI does stays on this computer.

5. **REDCap access** (only needed for `--apply`). See *Connecting to REDCap* below. Without it,
   the program still checks the duplicates and fills in the Tracking Duplicates tab, but it cannot
   update REDCap.

6. **R (only needed for the first scan).** The groups of possible duplicates are created by the
   `Comptroller.R` script. Running it needs R, plus the R packages tidyverse, janitor, readxl,
   stringdist and openxlsx. The location of the Comptroller data file is written near the top of the
   script and may need to be changed on a new computer.

## Connecting to REDCap

The program can only update REDCap if it knows the project's **API address** and an **API
token** (a personal key that works like a password). Both are kept in a small settings file
called `redcap_config.py` in the project folder:

`config = dict(api_url="https://your-redcap-server/api/", api_token="YOUR_32_CHARACTER_TOKEN")`

Replace the two values with your own:

- **api_token:** open the project in REDCap and choose **API** from the left-hand menu (under
  *Applications*). If you do not have a token yet, request one there; the REDCap administrator
  may need to approve it. The token is a string of 32 letters and numbers.
- **api_url:** the REDCap API address, shown on the same API page and in the *API Playground*.
  It is usually the REDCap website address followed by `/api/`.

Treat the token like a password: do not email it or share the file. Each person should use their
own token.

The program only contacts REDCap when it is run with `--apply`. Before doing anything else, it
checks the connection. If the token is wrong or expired, the address is wrong, or the network is
down, it stops straight away with a message explaining the problem. If REDCap stops responding
part-way through an update, the program also stops and says which records may have been affected.
In either case, run the program **without** `--apply` until the problem is fixed. It will still
check the duplicates and fill in the Tracking Duplicates tab.

## Running it

Open PowerShell in the project folder and give the program two files: the **Comptroller data**
(the REDCap export with every record's details) and the **duplicates workbook** (the groups to
check):

```
python -m dup_checker --data ComptrollerDATA.xlsx --duplicates Duplicates.xlsx --with-ai --apply
```

Replace the two file names with your own:

- **ComptrollerDATA.xlsx** is the data exported from the REDCap Comptroller dataset, with every
  record's details: name, address, phone and website. For example,
  `ComptrollerProject20_DATA_LABELS_2026-04-16_1051.xlsx`.
- **Duplicates.xlsx** is the spreadsheet of possible duplicates generated by the `Comptroller.R`
  script. The program reads the groups from its "Assigned Duplicate Groups" tab and fills in its
  "Tracking Duplicates" tab. For example, `Comptroller_Updates_Syeda_7.6.26_10-08-2026.xlsx`.

With `--with-ai` the AI assistant helps with the hard cases, and with `--apply` the decisions are
recorded in REDCap. Both are optional. Leave out `--apply` to only fill in the Tracking Duplicates
tab without touching REDCap, and leave out `--with-ai` to run on rules and website checks alone.

The program shows its progress as it goes. It saves a copy of the duplicates workbook with the
**Tracking Duplicates** tab filled in. The copy's name ends in `_app_review_` followed by the date
and time of the run, for example `..._app_review_2026-10-08_1415.xlsx`, so each run keeps its own
file. The original workbook is not changed. When the run also updates REDCap (`--apply`), it saves
the **REDCap change log** (`REDCap_Update_Log_<date>_<time>.xlsx`, see above) as well.

Useful additions to the command:

| Add | What it does |
|---|---|
| `--with-ai` | Uses the AI assistant for the hard cases. Without it, those are marked "Manual review required". |
| `--group-ids 81,9` | Checks only the listed groups. Good for a quick test. |
| `--debug` | Also saves a detailed results workbook showing every comparison and website check. |
| `--apply` | Also records the decisions in REDCap. Right after starting, the program shows which REDCap project it will update and asks **Continue? (y/n)**. After a **y**, it runs to the end without asking again and lists every change it makes. Needs `redcap_config.py` (see above). |

Without `--with-ai`, a run takes a few minutes. With `--with-ai`, it takes roughly 30 to 60 minutes
for a few hundred groups. Every run starts fresh: it visits the websites again and asks the AI again,
so the results always reflect what the websites show on the day of the run.
