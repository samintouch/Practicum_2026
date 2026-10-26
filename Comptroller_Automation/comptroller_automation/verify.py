"""Compare one record against its website and decide, per field, what to do.

Per-field outcomes:
  MATCH      website agrees with REDCap
  NEW        REDCap is blank and the website shows exactly one value  -> write
  CHANGED    REDCap differs and the website shows exactly one value   -> write
  REVIEW     website shows several candidates, or the change is risky -> person decides
  NOT_FOUND  website shows nothing for this field                     -> leave as is

Steps for one record:
  1. read the website (home + contact/about pages) with code only
  2. find the record's own location on the site, by address or phone
  3. decide each field
  4. if address/phone/fax/email are still unclear, ask the local AI to read
     the pages, then decide again with what it found (only values that are
     really on the page are kept)
  5. collect the site's other locations as possible new records
"""

import re
from dataclasses import dataclass, field

from . import county as county_lookup
from .extract import Found, extract, site_host
from .fields import ADDRESS_KEYS, MAX_NEW_LOCATIONS, NON_BLOCKING_REVIEW, REPORT_ONLY, SOCIAL_KEYS, TARGET_STATE
from .locations import (brand_words, clean_name, discover_location_pages, distinct_locations, ineligible_reason,
                        is_directory_domain, locations_from_pages, looks_like_directory, name_new_locations)
from .normalize import (STATE_NAMES, email_key, format_phone, host, house_number, is_placeholder_social, is_toll_free,
                        name_similarity, phone_digits, same_location, social_key, state_code, street_key, zip5)
from .scraper import FetchError, fetch_site, is_deep_link
from .websearch import suggest_website

MATCH, NEW, CHANGED, REVIEW, NOT_FOUND = "MATCH", "NEW", "CHANGED", "REVIEW", "NOT_FOUND"
NAME_MATCH_THRESHOLD = 0.8


@dataclass
class Options:
    max_pages: int = 6
    browser: object = None
    use_county: bool = True
    ai: object = None              # ai.LocalAI, or None
    ai_suggest_only: bool = False
    find_locations: bool = True
    suggest_websites: bool = False # search the web for records without a website (--suggest-websites)


@dataclass
class FieldResult:
    key: str
    on_file: str
    status: str
    new_value: str = ""
    found: list = field(default_factory=list)   # every distinct value seen on the site
    source: str = ""
    note: str = ""
    ai: bool = False                            # value came from the local AI

    @property
    def writes(self):
        return self.status in (NEW, CHANGED) and self.key not in REPORT_ONLY


@dataclass
class OtherLocation:
    location: object
    reason: str = ""            # why it can't be created; "" means it can
    new_record_id: str = ""
    county: str = ""
    note: str = ""              # e.g. phone taken from the parent record


@dataclass
class RecordResult:
    record_id: str
    record: dict
    status: str                 # checked / no website / fetch failed
    note: str = ""
    pages: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    other_locations: list = field(default_factory=list)
    ai_note: str = ""
    suggestion: object = None   # websearch.Suggestion for a record without a website
    directory: bool = False

    @property
    def changes(self):
        return [f for f in self.fields if f.writes]

    @property
    def reviews(self):
        return [f for f in self.fields if f.status == REVIEW]

    @property
    def blocked(self):
        """Any unclear field (other than the never-written name) means none
        of this record's fields are written; everything goes to a person."""
        return any(f.key not in NON_BLOCKING_REVIEW for f in self.reviews)


def _distinct(items, key):
    seen = {}
    for item in items:
        seen.setdefault(key(item), item)
    return list(seen.values())


def _single_value(key, on_file, candidates, render, source):
    """The common rule: one candidate -> write it, none -> NOT_FOUND, many -> REVIEW."""
    found = [render(c) for c in candidates]
    if not candidates:
        return FieldResult(key, on_file, NOT_FOUND)
    if len(candidates) > 1:
        return FieldResult(key, on_file, REVIEW, found=found,
                           note=f"{len(candidates)} different values on the website")
    return FieldResult(key, on_file, CHANGED if on_file else NEW,
                       new_value=found[0], found=found, source=source(candidates[0]))


def _from_location(key, on_file, value, loc, render=str):
    return FieldResult(key, on_file, CHANGED if on_file else NEW, new_value=render(value), found=[render(value)],
                       source=loc.url, ai=loc.source == "AI",
                       note="from this facility's own location listing" + (" (found by AI)" if loc.source == "AI" else ""))


def find_own_location(record, locations):
    """(location, how) for the record's own location on the site, or (None, "")."""
    on = {"street": record["street"], "zip": zip5(record["zip"])}
    if on["street"]:
        for loc in locations:
            if same_location(on, loc.address()):
                return loc, "address"
    on_phone = phone_digits(record["phone"])
    by_phone = [loc for loc in locations if on_phone and loc.phone == on_phone]
    if len(by_phone) == 1 and len(locations) == 1:
        return by_phone[0], "phone"
    # Moved within the same area: the only listed office in the record's ZIP
    # code (or, failing that, its city) is taken as its new address.
    if on["street"]:
        same_zip = [loc for loc in locations if on["zip"] and loc.zip == on["zip"]]
        if len(same_zip) == 1:
            return same_zip[0], "zip"
        same_city = [loc for loc in locations if record["city"] and loc.city.upper() == record["city"].upper()
                     and loc.state == state_code(record["state"])]
        if not same_zip and len(same_city) == 1:
            return same_city[0], "city"
        nearest = _nearest_office(record, locations)
        if nearest:
            return nearest, "nearest"
    return None, ""


NEAREST_MAX_MILES = 25


def _nearest_office(record, locations):
    """Several offices, none at the address, ZIP or city on file: take the
    one nearest the address on file, if it's within NEAREST_MAX_MILES and at
    most half as far as the next one (so the choice is clear-cut)."""
    if len(locations) < 2:
        return None
    here = county_lookup.geocode(record["street"], record["city"], state_code(record["state"]), zip5(record["zip"]))
    if not here:
        return None
    distances = []
    for loc in locations:
        there = county_lookup.geocode(loc.street, loc.city, loc.state, loc.zip)
        if there:
            distances.append((county_lookup.miles_between(here, there), loc))
    if len(distances) < len(locations):
        return None                     # couldn't place every office, so can't be sure
    distances.sort(key=lambda d: d[0])
    (best, loc), (second, _) = distances[0], distances[1]
    if best <= NEAREST_MAX_MILES and best * 2 <= second:
        loc.distance_miles = round(best, 1)
        return loc
    return None


# ---------------------------------------------------------------- phone & fax

def _check_numbers(record, data, locations, own):
    on_phone, on_fax = phone_digits(record["phone"]), phone_digits(record["fax"])
    seen = {f.value for f in data.phones} | {l.phone for l in locations if l.phone} | \
           {l.fax for l in locations if l.fax}
    numbers = _distinct(data.phones, lambda f: (f.value, f.kind))
    found_text = sorted({format_phone(v) for v in seen})

    labeled_phones = [f for f in numbers if f.kind == "phone"]
    phone_pool = labeled_phones or [f for f in numbers if f.kind == "unknown"]
    fax_values = {f.value for f in numbers if f.kind == "fax"}
    phone_cands = _distinct([f for f in phone_pool if f.value not in fax_values
                             and f.value != on_fax and not is_toll_free(f.value)], lambda f: f.value)

    if on_phone and on_phone in seen:
        phone = FieldResult("phone", record["phone"], MATCH, found=found_text)
    elif own and own.phone:
        phone = _from_location("phone", record["phone"], own.phone, own, format_phone)
    else:
        phone = _single_value("phone", record["phone"], phone_cands,
                              lambda f: format_phone(f.value), lambda f: f.source)
        if phone.status == REVIEW:
            phone.found = found_text

    final_phone = phone_digits(phone.new_value) if phone.writes else on_phone
    fax_cands = _distinct([f for f in numbers if f.kind == "fax" and f.value != final_phone], lambda f: f.value)
    if on_fax and on_fax in seen:
        fax = FieldResult("fax", record["fax"], MATCH)
    elif own and own.fax and own.fax != final_phone:
        fax = _from_location("fax", record["fax"], own.fax, own, format_phone)
    else:
        fax = _single_value("fax", record["fax"], fax_cands, lambda f: format_phone(f.value), lambda f: f.source)
    return [phone, fax]


# ---------------------------------------------------------------- email

def _check_email(record, data, locations, own, site):
    on = email_key(record["email"])
    cands = _distinct(data.emails, lambda f: f.value)
    all_emails = {f.value for f in cands} | {l.email for l in locations if l.email}
    if on and on in all_emails:
        return FieldResult("email", record["email"], MATCH)
    if own and own.email:
        result = _from_location("email", record["email"], own.email, own)
    else:
        on_domain = [f for f in cands if f.value.split("@")[1] == site or f.value.split("@")[1].endswith("." + site)]
        use = on_domain if len(cands) > 1 and len(on_domain) == 1 else cands
        result = _single_value("email", record["email"], use, lambda f: f.value, lambda f: f.source)
        result.found = [f.value for f in cands]
    if result.status == CHANGED:
        # an address missing from the site may still work, so don't replace it
        result.status, result.note = REVIEW, ("the email on file isn't shown on the website and the website shows "
                                              "a different one; the old one may still work, so check before replacing")
    return result


# ---------------------------------------------------------------- social media

def _check_socials(record, data):
    results = []
    for platform in SOCIAL_KEYS:
        on_file = record[platform]
        cands = _distinct([f for f in data.socials if f.kind == platform], lambda f: f.extra["key"])
        on_key = social_key(on_file, platform)
        if on_key and on_key in {f.extra["key"] for f in cands}:
            results.append(FieldResult(platform, on_file, MATCH))
            continue
        result = _single_value(platform, on_file, cands, lambda f: f.value, lambda f: f.source)
        if is_placeholder_social(on_key) and result.status == NOT_FOUND:
            result.status, result.note = REVIEW, "REDCap has a website-builder placeholder link, not the facility's"
        results.append(result)
    return results


# ---------------------------------------------------------------- address & county

def _county_result(record, addr, moving, use_county):
    county_on = record["county"]
    if not use_county:
        return FieldResult("county", county_on, NOT_FOUND, note="county check turned off")
    county = county_lookup.county_for(addr["street"], addr["city"], addr["state"], addr["zip"])
    if not county:
        return FieldResult("county", county_on, REVIEW if moving else NOT_FOUND,
                           note="Census geocoder could not match the address")
    if county == county_on.strip().upper().removesuffix(" COUNTY"):
        return FieldResult("county", county_on, MATCH, found=[county])
    return FieldResult("county", county_on, CHANGED if county_on else NEW, new_value=county,
                       found=[county], source="US Census geocoder")


def _same_address_checks(record, own, use_county):
    on_state, on_zip = state_code(record["state"]), zip5(record["zip"])
    results = [FieldResult("street", record["street"], MATCH, source=own.url)]
    for key, site_val, on_val in (("state", own.state, on_state), ("zip", own.zip, on_zip)):
        if site_val and site_val != on_val:
            results.append(FieldResult(key, record[key], CHANGED if on_val else NEW, new_value=site_val,
                                       found=[site_val], source=own.url, ai=own.source == "AI"))
        else:
            results.append(FieldResult(key, record[key], MATCH))
    if own.city and own.city.upper() != record["city"].upper():
        # same street address, different city name: the website's is used
        results.append(FieldResult("city", record["city"], CHANGED if record["city"] else NEW,
                                   new_value=own.city.upper(), found=[own.city.upper()], source=own.url,
                                   ai=own.source == "AI",
                                   note="same street address; the website gives this city name"))
    else:
        results.append(FieldResult("city", record["city"], MATCH))
    addr = {"street": record["street"], "city": record["city"], "state": on_state, "zip": on_zip}
    addr.update({r.key: r.new_value for r in results if r.writes})
    results.append(_county_result(record, addr, False, use_county))
    return results


def _moved_address(record, loc, use_county, note):
    status = CHANGED if record["street"] else NEW
    new = {"street": loc.street.upper(), "city": loc.city.upper(), "state": loc.state, "zip": loc.zip}
    results = []
    for key in ("street", "city", "state", "zip"):
        on_val = state_code(record[key]) if key == "state" else zip5(record[key]) if key == "zip" else record[key].upper()
        same = key != "street" and new[key] == on_val
        results.append(FieldResult(key, record[key], MATCH if same else status,
                                   new_value="" if same else new[key], found=[loc.one_line()] if key == "street" else [],
                                   source=loc.url, note=note if key == "street" else "", ai=loc.source == "AI"))
    county = _county_result(record, new, True, use_county)
    results.append(county)
    if county.status == REVIEW:
        for r in results[:-1]:
            if r.writes:
                r.status, r.note = REVIEW, "new address found, but its county could not be confirmed"
    return results


def _check_address(record, data, locations, own, how, use_county):
    found_text = [l.one_line() for l in locations]
    if own and how == "address":
        results = _same_address_checks(record, own, use_county)
        results[0].found = found_text
        return results
    if own and how == "phone" and len(locations) == 1:
        return _moved_address(record, own, use_county,
                              "the website lists this facility's phone number at this address")
    if own and how in ("zip", "city"):
        area = "ZIP code" if how == "zip" else "city"
        results = _moved_address(record, own, use_county, f"the address on file isn't on the website; this is "
                                                          f"the website's only address in the same {area}")
        results[0].found = found_text
        return results
    if own and how == "nearest":
        results = _moved_address(record, own, use_county,
                                 f"the address on file isn't on the website; this is the website's office nearest "
                                 f"to it ({own.distance_miles} miles away); the other offices are new locations")
        results[0].found = found_text
        return results
    if not locations:
        return [FieldResult(k, record[k], NOT_FOUND) for k in ADDRESS_KEYS]

    rest = [FieldResult(k, record[k], NOT_FOUND) for k in ("city", "state", "zip", "county")]
    if len(locations) > 1:
        note = f"{len(locations)} addresses on the website and none matches REDCap"
        return [FieldResult("street", record["street"], REVIEW, found=found_text, note=note)] + rest

    loc = locations[0]
    on_phone = phone_digits(record["phone"])
    site_phones = {f.value for f in data.phones if f.kind != "fax"}
    if record["street"] and (len(site_phones) > 1 or (on_phone in site_phones and loc.phone != on_phone)):
        note = ("one address on the website, but other phone numbers too, so the facility may have "
                "another office that isn't listed")
        return [FieldResult("street", record["street"], REVIEW, new_value=loc.one_line(), found=found_text,
                            source=loc.url, note=note)] + rest
    return _moved_address(record, loc, use_county, "the only address on the website")


# ---------------------------------------------------------------- name & website

def _plain(text):
    """Lower case, "&" as "and", punctuation and extra spaces removed;
    dots and apostrophes vanish so "M.D." reads as "md"."""
    text = re.sub(r"[.'’]", "", (text or "").lower().replace("&", " and "))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _looks_like_a_name(text):
    """Page titles sometimes hold a whole address block or a slogan; those
    aren't names. A name has at most 8 words and no digits or email."""
    return len(text.split()) <= 8 and not re.search(r"\d|@", text)


# Words that don't tell one facility from another, on top of the generic
# business words in locations.py; Texas city names are here because a city
# shared by two names doesn't make them the same organization.
_NOT_DISTINCTIVE = {
    "md", "do", "phd", "pa", "np", "dr", "medical", "medicine", "network", "north", "south", "east", "west",
    "dallas", "houston", "austin", "san", "antonio", "fort", "worth", "el", "paso", "arlington", "plano",
    "irving", "frisco", "mckinney", "denton", "garland", "tyler", "waco", "lubbock", "amarillo", "texas", "dfw",
}


def _same_distinctive_words(on_file, on_website):
    """The website's name is the name on file in another form when every
    distinctive word of the name on file is in it: "Huang Medical Clinic" ->
    "Wentian Huang MD", "TriMed Behavioral..." -> "Tri-MED Integrative...".
    A real rebrand loses them: "Interventional Pain and Wellness Center" ->
    "The Pain and Spine Specialists"."""
    def flat(text):
        return re.sub(r"[-.'’]", "", (text or "").lower())
    words = {w for w in brand_words(flat(on_file)) if w not in _NOT_DISTINCTIVE}
    site = re.sub(r"[^a-z0-9]", "", flat(on_website))
    return bool(words) and all(w in site for w in words)


def _check_name(record, data):
    # the exact name on file anywhere on the site (ignoring case and
    # punctuation) means it's still in use, so no name change is suggested
    on_file = _plain(record["name"])
    if on_file:
        for url, lines in data.text.items():
            if f" {on_file} " in f" {_plain(' '.join(lines))} ":
                return FieldResult("name", record["name"], MATCH, found=[record["name"]], source=url,
                                   note="the name on file appears on the website")
    cands = _distinct([f for f in data.names if _looks_like_a_name(f.value)], lambda f: f.value.lower())
    if not cands:
        return FieldResult("name", record["name"], NOT_FOUND)
    best = max(cands, key=lambda f: name_similarity(record["name"], f.value))
    found = [f.value for f in cands]
    if name_similarity(record["name"], best.value) >= NAME_MATCH_THRESHOLD:
        return FieldResult("name", record["name"], MATCH, found=found, source=best.source)
    for cand in cands:
        if _same_distinctive_words(record["name"], cand.value):
            return FieldResult("name", record["name"], MATCH, found=found, source=cand.source,
                               note=f"{cand.value!r} is the same name in another form")
    return FieldResult("name", record["name"], REVIEW, new_value=best.value, found=found, source=best.source,
                       note="website name looks different; names are never changed automatically")


def _check_website(record, pages):
    start, final = host(record["website"]), site_host(pages)
    if start and final and start != final:
        return FieldResult("website", record["website"], REVIEW, new_value=pages[0].url, found=[pages[0].url],
                           note=f"website now redirects to {final}")
    return FieldResult("website", record["website"], MATCH)


# ---------------------------------------------------------------- entry point

def _decide(record, data, locations, pages, use_county):
    own, how = find_own_location(record, locations)
    site = site_host(pages)
    fields = [_check_name(record, data), _check_website(record, pages)]
    fields += _check_address(record, data, locations, own, how, use_county)
    fields += _check_numbers(record, data, locations, own)
    fields.append(_check_email(record, data, locations, own, site))
    fields += _check_socials(record, data)
    return fields, own


def _drop_hidden_addresses(locations, browser):
    """Websites built from templates sometimes keep an old or sample address
    in a block the stylesheet hides. Plain HTML can't tell, so when a site
    seems to list several addresses, check each page's text as a browser
    shows it and keep only the addresses a visitor can see. schema.org
    addresses are kept: they are the site's own structured data."""
    visible = {}
    for url in {l.url for l in locations if l.source == "text"}:
        try:
            visible[url] = _plain(browser.visible_text(url))
        except Exception:
            visible[url] = None          # couldn't check, keep what was found

    def shown(loc):
        text = visible.get(loc.url)
        if text is None:
            return True
        words = _plain(loc.street).split()[:2]       # house number and first street word
        return f" {' '.join(words)} " in f" {text} "

    # A page where most addresses are hidden keeps them in collapsed sections
    # a visitor opens with a click (a "Locations" list by state); those are
    # real. Only a stray hidden address next to visible ones is dropped.
    hidden = set()
    for url in visible:
        on_page = [l for l in locations if l.url == url and l.source == "text"]
        page_hidden = [l for l in on_page if not shown(l)]
        if len(page_hidden) <= len(on_page) - len(page_hidden):
            hidden.update(id(l) for l in page_hidden)
    return [l for l in locations if id(l) not in hidden]


def _needs_ai(record, fields):
    by_key = {f.key: f for f in fields}
    if any(by_key[k].status == REVIEW for k in ("street", "phone", "fax", "email")):
        return True
    return (by_key["street"].status == NOT_FOUND and bool(record["street"])) or by_key["phone"].status == NOT_FOUND


def verify_record(record_id, record, opts=None):
    opts = opts or Options()
    if not record["website"]:
        result = RecordResult(record_id, record, "no website", "no website on file; nothing to verify against")
        if opts.suggest_websites:
            # only a suggestion for a person to confirm; never written or used to verify
            result.suggestion = suggest_website(record, opts.browser)
        return result
    try:
        pages = fetch_site(record["website"], max_pages=opts.max_pages, browser=opts.browser)
    except FetchError as exc:
        result = RecordResult(record_id, record, "fetch failed", str(exc))
        if opts.suggest_websites:
            # a dead website is as good as none: look for the current one
            result.suggestion = suggest_website(record, opts.browser)
        return result

    data = extract(pages)
    if opts.browser and len(distinct_locations(data.locations)) > 1:
        data.locations = _drop_hidden_addresses(data.locations, opts.browser)
    directory = is_directory_domain(pages[0].url) or is_directory_domain(record["website"])
    location_pages = []
    if opts.find_locations and not directory:
        location_pages = discover_location_pages(pages, opts.browser)
    # A deep link is one location's page: contact details come only from it,
    # but the chain's other locations still come from its locations pages.
    location_page_locs = locations_from_pages(location_pages)
    # keep every number seen, before duplicate addresses are merged together
    data.phones += [Found(n, l.url, kind) for l in data.locations + location_page_locs
                    for n, kind in ((l.phone, "phone"), (l.fax, "fax")) if n]
    own_site_locs = distinct_locations(data.locations if is_deep_link(record["website"])
                                       else data.locations + location_page_locs)

    # the record's own address can only be one of the offices in its state
    home_state = state_code(record["state"]) or TARGET_STATE
    fields, own = _decide(record, data, _in_state(own_site_locs, home_state), pages, opts.use_county)
    result = RecordResult(record_id, record, "checked", pages=[p.url for p in pages + location_pages])

    if opts.ai and _needs_ai(record, fields):
        # contact pages first, they're most likely to hold the answer
        page_lines = sorted(data.text.items(), key=lambda kv: "contact" not in kv[0].lower())
        site_type, ai_locations, result.ai_note = opts.ai.extract_locations(record, page_lines)
        if site_type == "directory_of_unrelated_providers":
            directory = True
        if ai_locations:
            own_site_locs = distinct_locations(own_site_locs + ai_locations)
            fields, own = _decide(record, data, _in_state(own_site_locs, home_state), pages, opts.use_county)
            if opts.ai_suggest_only:
                for f in fields:
                    if f.ai and f.writes:
                        f.status, f.note = REVIEW, f"AI suggestion, not written (--ai-suggest-only); {f.note}"
    result.fields, result.directory = fields, directory


    street = next(f for f in fields if f.key == "street")
    if (record["street"] and street.status in (NOT_FOUND, REVIEW) and own_site_locs
            and not _in_state(own_site_locs, home_state)):
        # the organization still operates, but no longer in this state; its
        # out-of-state addresses aren't useful to a reviewer, so none are listed
        state_name = STATE_NAMES.get(home_state, home_state)
        street.status, street.found, street.new_value = REVIEW, [], ""
        street.note = f"Address has changed, but no location found in {state_name}"
        result.note = street.note

    if directory:
        # a doctor/hospital directory lists many providers; its details
        # can't be assumed to be this facility's
        for f in result.changes:
            f.status, f.note = REVIEW, "website is a directory of many providers, so nothing is written"
        result.note = "website is a directory of many providers"
    else:
        # Only trust a website we can tie to this facility: its name, phone,
        # address or email must line up with what's on file, or it must list
        # an office in the record's own ZIP code.
        labels = {"name": "name", "street": "street address", "phone": "phone", "email": "email"}
        matched = [labels[f.key] for f in fields if f.key in labels and f.status == MATCH]
        on_zip = zip5(record["zip"])
        in_zip = any(on_zip and l.zip == on_zip for l in own_site_locs)
        tied = bool(matched) or in_zip
        if fields[1].status == REVIEW and tied:
            # the website on file moved to this new domain: it still shows the facility
            evidence = ", ".join(matched) if matched else "an office in the same ZIP code"
            website = fields[1]
            website.status = CHANGED
            website.note = (f"the website on file now redirects to {site_host(pages)}, which shows this "
                            f"facility's {evidence}")
        elif fields[1].status == REVIEW:
            # redirected somewhere that shows nothing of this facility, maybe
            # after a sale, a merger or a lapsed domain
            for f in result.changes:
                f.status, f.note = REVIEW, ("website redirects to another domain that doesn't show this "
                                            "facility's name, address, phone or email, so nothing is written")
        if not tied and result.changes:
            for f in result.changes:
                f.status, f.note = REVIEW, ("website could not be tied to this facility (no name, phone, "
                                            "address or email match), so nothing is written")
            result.note = "website not confirmed as this facility's"

    all_locs = distinct_locations(own_site_locs + location_page_locs)
    elsewhere = [l for l in all_locs if l.state and l.state != TARGET_STATE]
    if elsewhere and not directory:
        note = (f"the website also lists {len(elsewhere)} location(s) outside "
                f"{STATE_NAMES.get(TARGET_STATE, TARGET_STATE)}; those are not added as new locations")
        result.note = f"{result.note}; {note}" if result.note else note
    all_locs = _in_state(all_locs, TARGET_STATE)
    brand = next((f.value for f in data.names if f.kind in ("og:site_name", "schema.org")), "")
    result.other_locations, note = _other_locations(record, all_locs, own, fields, directory, site_host(pages),
                                                    brand)
    if note:
        result.note = f"{result.note}; {note}" if result.note else note
    return result


def _in_state(locations, state):
    """Locations in this state (or with no state given)."""
    return [l for l in locations if not l.state or l.state == state]


def _same_street_name(a, b):
    def name_only(street):
        return " ".join(street_key(street).split()[1:3])
    return (zip5(a["zip"]) == zip5(b["zip"]) and bool(name_only(a["street"]))
            and name_only(a["street"]) == name_only(b["street"]))


def _number_gap(a, b):
    na, nb = house_number(a["street"]), house_number(b["street"])
    return abs(int(na) - int(nb)) if na and nb else 10 ** 9


def _other_locations(record, locations, own, fields, directory, site, brand):
    street = next(f for f in fields if f.key == "street")
    zip_field = next(f for f in fields if f.key == "zip")
    mine = [{"street": record["street"], "zip": zip5(record["zip"])}]
    if own:
        mine.append(own.address())
    if street.status in (NEW, CHANGED):     # the facility moved to this address
        mine.append({"street": street.new_value, "zip": zip_field.new_value or zip5(record["zip"])})
    others = [l for l in locations if not any(m["street"] and same_location(m, l.address()) for m in mine)]
    # Same ZIP and street name with the same phone or a house number a few
    # doors away: the same office with a typo in one of the addresses
    # (1772 vs 1774 W McDermott Dr) or a move within the same complex.
    my_phones = {phone_digits(record["phone"]), own.phone if own else ""} - {""}
    others = [l for l in others if not any(_same_street_name(m, l.address()) and
                                           (l.phone in my_phones or _number_gap(m, l.address()) <= 50)
                                           for m in mine)]
    if not others:
        return [], ""
    if directory or looks_like_directory(others, brand or record["name"], site):
        # a doctor/hospital database: its listings are other providers, so
        # none are suggested, not even for review
        note = "its listings are not suggested as new locations"
        return [], note if directory else f"website looks like a directory of many providers; {note}"
    # An unnamed location, or one only labelled by its city ("Wylie Office"),
    # gets "<organization> - <City>". For a deep link the record's own name is
    # one branch's name, so the site's brand is better.
    others = name_new_locations(others, clean_name(brand) if brand and is_deep_link(record["website"])
                                else record["name"])
    if not own and street.status not in (NEW, CHANGED):
        return [OtherLocation(l, "this record's own location couldn't be identified on the website, so "
                                 "its other locations aren't added automatically") for l in others], ""
    if len(others) > MAX_NEW_LOCATIONS:
        reason = (f"website lists {len(others)} other locations, more than {MAX_NEW_LOCATIONS}; a list this "
                  f"long is checked by a person before anything is added")
        return [OtherLocation(l, reason) for l in others], ""
    return [OtherLocation(l, ineligible_reason(l)) for l in others], ""
