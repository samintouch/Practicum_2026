"""The fields this tool verifies, and how each maps to the REDCap project.

REDCap variable names come from the project's data dictionary
(content=metadata). Spreadsheet columns are matched by the start of their
label, because the label export has long labels with notes attached.
"""

# key -> (REDCap variable, start of the spreadsheet column label)
FIELDS = {
    "name":      ("ct1",  "Organization/Facility name"),
    "street":    ("ct2",  "organization street name"),
    "city":      ("ct3",  "organization city name"),
    "state":     ("ct9",  "organization state"),
    "zip":       ("ct4",  "organization zip code"),
    "county":    ("ct10", "organization county"),
    "phone":     ("ct6",  "organization phone number"),
    "fax":       ("ct13", "organization fax number"),
    "email":     ("ct7",  "organization email address"),
    "website":   ("ct8",  "organization website"),
    "facebook":  ("ct16", "Facebook"),
    "instagram": ("ct17", "Instagram"),
    "twitter":   ("ct18", "Twitter"),
    "tiktok":    ("ct19", "TikTok"),
    "youtube":   ("ct20", "Youtube"),
    "whatsapp":  ("ct26", "Whatsapp"),
}


ADDRESS_KEYS = ("street", "city", "state", "zip", "county")
SOCIAL_KEYS = ("facebook", "instagram", "twitter", "tiktok", "youtube", "whatsapp")

# Only locations in this state are added as new records.
TARGET_STATE = "TX"
# A site listing more other locations than this (a large chain or a listing
# site) gets none added automatically; they go to Other Locations for review.
MAX_NEW_LOCATIONS = 50

# A REVIEW on any field except these blocks every write on that record.
NON_BLOCKING_REVIEW = {"name"}

# Fields the tool will never write on its own, even when the website differs.
# A rebrand on a homepage is not necessarily the legal or DBA name. (The
# website is written only when it redirects to a new domain that still shows
# the facility's details; see verify.py.)
REPORT_ONLY = {"name"}

# Bookkeeping written alongside any real change, matching how staff record
# manual updates. Edit here if your process differs.
CHANGE_NOTE_FIELD = "change_explain"
COMMENTS_FIELD = "general_comments"
CHANGE_FLAG_FIELD = "change"          # yesno
VALIDATED_FIELD = "validated"         # yesno
VALIDATION_DATE_FIELD = "validation_date"  # API expects YYYY-MM-DD
PHASE1_COMPLETE_FIELD = "ogcomplete"  # yesno: Yes on an updated record, No on a new one
ORGSTATUS_FIELD = "orgstatus"         # radio: 0 Open, 1 Closed, 2 Unverified
ORGSTATUS_OPEN = "0"

LABELS = {
    "name": "Facility name", "street": "Street", "city": "City", "state": "State",
    "zip": "ZIP code", "county": "County", "phone": "Phone", "fax": "Fax",
    "email": "Email", "website": "Website", "facebook": "Facebook",
    "instagram": "Instagram", "twitter": "Twitter", "tiktok": "TikTok",
    "youtube": "YouTube", "whatsapp": "WhatsApp",
}


def redcap_var(key):
    return FIELDS[key][0]
