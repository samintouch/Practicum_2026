"""Minimal REDCap API client, the update step and new-record creation."""

import datetime
import json
import re

import requests

from . import county as county_lookup
from .fields import (CHANGE_FLAG_FIELD, CHANGE_NOTE_FIELD, COMMENTS_FIELD, LABELS, ORGSTATUS_FIELD,
                     ORGSTATUS_OPEN, PHASE1_COMPLETE_FIELD, VALIDATED_FIELD, VALIDATION_DATE_FIELD, redcap_var)
from .normalize import format_phone, phone_digits

TOOL_NAME = "Comptroller_Automation"


class RedcapError(Exception):
    pass


class Redcap:
    def __init__(self, api_url, api_token):
        self.api_url, self.api_token = api_url, api_token

    def _post(self, **data):
        data.update(token=self.api_token, format="json", returnFormat="json")
        resp = requests.post(self.api_url, data=data, timeout=60)
        if resp.status_code in (401, 403):
            raise RedcapError(f"REDCap rejected the API token (HTTP {resp.status_code}): {resp.text.strip()}")
        if not resp.ok:
            raise RedcapError(f"REDCap API error (HTTP {resp.status_code}): {resp.text.strip()[:300]}")
        return resp.json()

    def project_info(self):
        return self._post(content="project")

    def export_record(self, record_id, fields):
        data = {"content": "record", "records[0]": str(record_id)}
        for i, name in enumerate(["record_id", *fields]):
            data[f"fields[{i}]"] = name
        rows = self._post(**data)
        if not rows:
            raise RedcapError(f"record {record_id} no longer exists in REDCap")
        return rows[0]

    def records_in_zip(self, zip_code):
        """record_id, name and street of every live record in one ZIP code."""
        if not re.fullmatch(r"\d{5}", zip_code or ""):
            return []
        data = {"content": "record", "filterLogic": f'[{redcap_var("zip")}] = "{zip_code}"'}
        for i, name in enumerate(["record_id", redcap_var("name"), redcap_var("street")]):
            data[f"fields[{i}]"] = name
        return self._post(**data)

    def import_record(self, record_id, values):
        """REDCap only touches the fields sent, so nothing else on the record changes."""
        payload = [{"record_id": str(record_id), **values}]
        return self._post(content="record", action="import", overwriteBehavior="normal",
                          data=json.dumps(payload))

    def create_record(self, values):
        """New record, numbered by REDCap itself. Returns the new record ID."""
        result = self._post(content="record", action="import", overwriteBehavior="normal",
                            forceAutoNumber="true", returnContent="ids",
                            data=json.dumps([{"record_id": "0", **values}]))
        if not isinstance(result, list) or not result:
            raise RedcapError(f"REDCap did not return a new record ID: {result}")
        return str(result[0])


def load_client():
    try:
        from redcap_config import config
    except ImportError as exc:
        raise RedcapError("redcap_config.py not found next to the tool; copy redcap_config.py.sample "
                          "and fill in api_url and api_token") from exc
    return Redcap(config["api_url"], config["api_token"])


def change_lines(result):
    lines = []
    for f in result.changes:
        label = LABELS[f.key]
        if f.on_file:
            lines.append(f"The {label} was updated from {f.on_file} to {f.new_value}.")
        else:
            lines.append(f"The {label} was added: {f.new_value}.")
    return lines


def apply_changes(client, result):
    """Write the record's changes. Before writing, every field is re-read
    live from REDCap; if any no longer matches the export this run compared
    against, the whole record is skipped (someone edited it since).

    Returns (written: bool, message)."""
    changes = result.changes
    if not changes:
        return False, "nothing to write"
    if result.blocked:
        return False, "not written: some fields need manual review"

    variables = [redcap_var(f.key) for f in changes]
    live = client.export_record(result.record_id, variables + [CHANGE_NOTE_FIELD, COMMENTS_FIELD])
    stale = [f"{LABELS[f.key]} is now {live.get(redcap_var(f.key), '')!r}"
             for f in changes if (live.get(redcap_var(f.key)) or "").strip() != f.on_file.strip()]
    if stale:
        return False, "skipped, record changed in REDCap since the export: " + "; ".join(stale)

    today = datetime.date.today().isoformat()
    note = (f"Automated website verification on {today} ({result.record['website']}):\n"
            + "\n".join(change_lines(result)))
    values = {redcap_var(f.key): f.new_value for f in changes}
    for note_field in (CHANGE_NOTE_FIELD, COMMENTS_FIELD):
        existing = (live.get(note_field) or "").strip()
        values[note_field] = f"{existing}\n\n{note}" if existing else note
    values[CHANGE_FLAG_FIELD] = "1"
    values[VALIDATED_FIELD] = "1"
    values[VALIDATION_DATE_FIELD] = today
    values[PHASE1_COMPLETE_FIELD] = "1"

    client.import_record(result.record_id, values)
    return True, f"wrote {len(changes)} field(s)"


def new_record_values(loc, parent_id, parent):
    """Fields for a new location's record. The phone falls back to the
    parent record's when the location doesn't list its own; the fax never
    does (blank unless the location lists one)."""
    county = county_lookup.county_for(loc.street, loc.city, loc.state, loc.zip) or ""
    phone = format_phone(loc.phone) if loc.phone else parent["phone"]
    fax = format_phone(loc.fax) if loc.fax else ""
    values = {
        redcap_var("name"): loc.name,
        redcap_var("street"): loc.street.upper(),
        redcap_var("city"): loc.city.upper(),
        redcap_var("state"): loc.state,
        redcap_var("zip"): loc.zip,
        redcap_var("county"): county,
        redcap_var("phone"): phone,
        redcap_var("fax"): fax if phone_digits(fax) != phone_digits(phone) else "",
        redcap_var("email"): loc.email,
        redcap_var("website"): loc.url or parent["website"],
        PHASE1_COMPLETE_FIELD: "0",
        ORGSTATUS_FIELD: ORGSTATUS_OPEN,
        COMMENTS_FIELD: (f"This record was added by the {TOOL_NAME} tool on {datetime.date.today().isoformat()} "
                         f"as an additional location of REDCap Record ID {parent_id}, found on {loc.url}."),
    }
    return {k: v for k, v in values.items() if v}
