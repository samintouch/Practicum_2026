"""Record duplicate decisions in REDCap (only with --apply).

For every set of records the checker found to be the same facility:
  - each duplicate record (the "which is true duplicate?" IDs, e.g. 5015):
        duplicate = Yes, comment "duplicate with 145"
  - the record kept (the most complete record, e.g. 145):
        duplicate = No, comment "duplicate with 5015"
  - both: Validation = Yes and Validation Date = today
The comment goes at the top of "Additional Comments". An existing comment is kept below it, after a
"Previous Comment: <old validation date>" line.
Records the checker called "Not a duplicate" or "Needs review" are not touched.

Safety checks against the live REDCap data before anything is written:
  - the record must exist
  - a record already marked the same way in REDCap (duplicate Yes/No) is left alone entirely
  - a duplicate record a reviewer already set to "No", or a kept record a reviewer already marked
    as a duplicate, is not overridden - either conflict skips the whole set, so REDCap never says
    "duplicate of 145" while 145 itself is marked a duplicate
  - skipped if the organization name in REDCap no longer resembles the name the checker used
Every change is logged with the old values so it can be undone.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import requests

from .checker import RecordResult
from .matching import compare_names

DUPLICATE_FIELD = "duplicate"          # "Is this a duplicate record?" (yes/no)
COMMENT_FIELD = "general_comments"     # "Additional Comments"
NAME_FIELD = "ct1"                     # "Organization/Facility name"
VALIDATED_FIELD = "validated"          # "Validation" (yes/no)
VALIDATION_DATE_FIELD = "validation_date"   # "Validation Date" (date, M-D-Y on screen; API takes Y-M-D)
YES = "1"
NO = "0"
YES_NO = {"1": "Yes", "0": "No", "": "(blank)"}


class RedcapError(Exception):
    pass


def _redcap_error_text(resp: requests.Response) -> str:
    """REDCap puts its error in a JSON {"error": "..."} body; fall back to the raw text."""
    try:
        body = resp.json()
        if isinstance(body, dict) and body.get("error"):
            return str(body["error"])[:300]
    except ValueError:
        pass
    return " ".join(resp.text.split())[:200] or resp.reason or "no details"


class RedcapClient:
    def __init__(self, url: str, token: str, timeout: int = 120):
        self.url = url
        self._token = token
        self.timeout = timeout

    @classmethod
    def from_config(cls, config_path: Path) -> "RedcapClient":
        """Reads api_url / api_token from REDCAP_API_URL / REDCAP_API_TOKEN, or from redcap_config.py."""
        url, token = os.environ.get("REDCAP_API_URL"), os.environ.get("REDCAP_API_TOKEN")
        if not (url and token):
            if not config_path.exists():
                raise RedcapError(f"the settings file {config_path} was not found. Create it with the REDCap "
                                  "API address (api_url) and your API token (api_token)")
            spec = importlib.util.spec_from_file_location("redcap_config", config_path)
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as exc:
                raise RedcapError(f"{config_path} could not be read ({exc.__class__.__name__}: {exc}) - check "
                                  "its quotes and commas") from exc
            cfg = getattr(module, "config", {})
            url, token = url or cfg.get("api_url"), token or cfg.get("api_token")
        url, token = (url or "").strip(), (token or "").strip()
        if not url.lower().startswith(("https://", "http://")):
            raise RedcapError(f"api_url in {config_path} is missing or is not a web address "
                              "(it should look like https://<your REDCap server>/api/)")
        if not re.fullmatch(r"[0-9A-Fa-f]{32}", token):
            raise RedcapError(f"api_token in {config_path} is missing or is not a REDCap API token (a token is "
                              "32 letters and numbers, shown on the project's API page in REDCap)")
        return cls(url, token)

    def _post(self, **data) -> object:
        try:
            resp = requests.post(self.url, data={"token": self._token, "format": "json", **data}, timeout=self.timeout)
        except requests.exceptions.Timeout as exc:
            raise RedcapError(f"REDCap did not answer within {self.timeout} seconds (timed out). The server may "
                              "be busy or the network slow") from exc
        except requests.exceptions.SSLError as exc:
            raise RedcapError(f"a secure connection to {self.url} could not be made (certificate problem)") from exc
        except requests.exceptions.ConnectionError as exc:
            raise RedcapError(f"could not connect to {self.url}. Check the internet/VPN connection and that "
                              "api_url is correct") from exc
        except requests.RequestException as exc:
            raise RedcapError(f"the request to REDCap failed ({exc.__class__.__name__})") from exc
        message = _redcap_error_text(resp)
        if resp.status_code in (401, 403):
            raise RedcapError(f"REDCap rejected the API token (HTTP {resp.status_code}: {message}). The token is "
                              "wrong, expired, or does not have API rights for this project")
        if resp.status_code == 404:
            raise RedcapError(f"no REDCap API was found at {self.url} (HTTP 404). Check api_url; it usually "
                              "ends with /api/")
        if resp.status_code != 200:
            raise RedcapError(f"REDCap returned an error (HTTP {resp.status_code}: {message})")
        try:
            return resp.json()
        except ValueError as exc:
            raise RedcapError(f"the answer from {self.url} was not REDCap API data. Check api_url points to "
                              "the REDCap API (it usually ends with /api/)") from exc

    def project_title(self) -> str:
        info = self._post(content="project")
        return f"{info.get('project_title')} (project {info.get('project_id')}" + \
               (", PRODUCTION)" if str(info.get("in_production")) == "1" else ", development)")

    def check_fields(self) -> None:
        meta = {f["field_name"]: f for f in self._post(content="metadata")}
        needed = (DUPLICATE_FIELD, COMMENT_FIELD, NAME_FIELD, VALIDATED_FIELD, VALIDATION_DATE_FIELD)
        missing = [f for f in needed if f not in meta]
        if missing:
            raise RedcapError(f"Fields not in this REDCap project: {', '.join(missing)}")
        for yes_no in (DUPLICATE_FIELD, VALIDATED_FIELD):
            if meta[yes_no]["field_type"] != "yesno":
                raise RedcapError(f"'{yes_no}' is not a yes/no field in this project")

    def export(self, record_ids: list[str], fields: list[str]) -> dict[str, dict]:
        data = {"content": "record", "type": "flat"}
        data.update({f"records[{i}]": rid for i, rid in enumerate(record_ids)})
        data.update({f"fields[{i}]": f for i, f in enumerate(["record_id", *fields])})
        return {row["record_id"]: row for row in self._post(**data)}

    def import_rows(self, rows: list[dict]) -> int:
        result = self._post(content="record", type="flat", overwriteBehavior="normal", dateFormat="YMD",
                            returnContent="count", data=json.dumps(rows))
        return int(result.get("count", 0)) if isinstance(result, dict) else 0


@dataclass
class PlannedUpdate:
    record_id: str
    primary_id: str                      # the record kept for this set of duplicates
    role: str                            # "duplicate" or "kept"
    organization_name: str
    decided_by: str
    comment_to_add: str
    set_duplicate: str                   # YES for duplicates, NO for the record kept
    validation_date: str                 # YYYY-MM-DD
    action: str = "UPDATE"               # UPDATE or SKIP
    status: str = "planned"              # planned, updated, skipped, failed
    note: str = ""
    redcap_name: str = ""
    old_duplicate: str = ""
    old_validated: str = ""
    old_validation_date: str = ""
    old_comments: str = ""
    changes: dict = field(default_factory=dict)    # field -> new value actually sent


def plan_updates(results: list[RecordResult], run_date: date | None = None) -> list[PlannedUpdate]:
    """What --apply would write, worked out from the checker's results only (no REDCap calls)."""
    run_date = run_date or date.today()
    plans = []
    for res in results:
        rid = res.record.record_id
        if res.recommendation.startswith("Duplicate of"):
            plans.append(PlannedUpdate(
                record_id=rid, primary_id=res.primary_id or "", role="duplicate",
                organization_name=res.record.name or "", decided_by=res.decided_by,
                comment_to_add=f"By automation: duplicate with {res.primary_id}",   # 5015: "...duplicate with 145"
                set_duplicate=YES, validation_date=f"{run_date:%Y-%m-%d}"))
        elif res.recommendation.startswith("Duplicate - keep"):
            plans.append(PlannedUpdate(
                record_id=rid, primary_id=rid, role="kept",
                organization_name=res.record.name or "", decided_by=res.decided_by,
                comment_to_add=f"By automation: duplicate with {', '.join(res.duplicate_with)}",   # 145: "...with 5015"
                set_duplicate=NO, validation_date=f"{run_date:%Y-%m-%d}"))
    return plans


def check_against_redcap(plans: list[PlannedUpdate], client: RedcapClient) -> None:
    """Fill in the live REDCap values, work out exactly which fields change, and SKIP anything unsafe."""
    live = client.export([p.record_id for p in plans],
                         [NAME_FIELD, DUPLICATE_FIELD, COMMENT_FIELD, VALIDATED_FIELD, VALIDATION_DATE_FIELD])
    conflicts: dict[str, str] = {}       # primary_id -> reason the whole set is skipped
    for p in plans:
        row = live.get(p.record_id)
        if row is None:
            p.action, p.note = "SKIP", "record not found in REDCap"
            conflicts.setdefault(p.primary_id, f"record {p.record_id} not found in REDCap")
            continue
        p.redcap_name = row.get(NAME_FIELD, "")
        p.old_duplicate = row.get(DUPLICATE_FIELD, "")
        p.old_validated = row.get(VALIDATED_FIELD, "")
        p.old_validation_date = row.get(VALIDATION_DATE_FIELD, "")
        p.old_comments = row.get(COMMENT_FIELD, "")
        if compare_names(p.organization_name, p.redcap_name).level == "DIFFERENT":
            p.action, p.note = "SKIP", (f"name in REDCap is now '{p.redcap_name}' - record changed since the "
                                        "export; re-check before marking")
            conflicts.setdefault(p.primary_id, f"record {p.record_id} has changed in REDCap")
        elif p.old_duplicate and p.old_duplicate != p.set_duplicate:
            p.action = "SKIP"
            p.note = (f"a reviewer already set 'duplicate' to {YES_NO[p.old_duplicate]} in REDCap - "
                      f"not overriding")
            conflicts.setdefault(p.primary_id, f"record {p.record_id} is already marked "
                                               f"'{YES_NO[p.old_duplicate]}' in REDCap")

    for p in plans:
        if p.primary_id in conflicts and p.action != "SKIP":
            p.action, p.note = "SKIP", f"skipped with the rest of its set: {conflicts[p.primary_id]}"
        if p.action == "SKIP":
            continue
        if p.old_duplicate == p.set_duplicate:
            p.action, p.note = "SKIP", (f"already marked '{YES_NO[p.old_duplicate]}' in REDCap, same as found - "
                                        "no update needed")
            continue
        p.changes = {
            DUPLICATE_FIELD: p.set_duplicate,
            VALIDATED_FIELD: YES,
            VALIDATION_DATE_FIELD: p.validation_date,
            COMMENT_FIELD: new_comment_text(p.comment_to_add, p.old_comments, p.old_validation_date),
        }


def _mdy(ymd: str) -> str:
    """REDCap exports dates as YYYY-MM-DD; show them the way REDCap displays this field (MM-DD-YYYY)."""
    parts = ymd.split("-")
    return f"{parts[1]}-{parts[2]}-{parts[0]}" if len(parts) == 3 and len(parts[0]) == 4 else ymd


def new_comment_text(comment: str, old_comments: str, old_validation_date: str) -> str:
    """Our comment first; any existing comment kept below it, introduced by the old validation date:

        duplicate with 145
        Previous Comment: 07-11-2026
        <existing comment, unchanged>
    """
    if not old_comments.strip():
        return comment
    previous = f"Previous Comment: {_mdy(old_validation_date)}".rstrip()
    return f"{comment}\n{previous}\n{old_comments}"


def describe_changes(p: PlannedUpdate) -> str:
    labels = {DUPLICATE_FIELD: lambda v: f"duplicate={YES_NO[v]}", VALIDATED_FIELD: lambda v: "Validation=Yes",
              VALIDATION_DATE_FIELD: lambda v: f"Validation Date={v}", COMMENT_FIELD: lambda v: "add comment"}
    return ", ".join(labels[f](v) for f, v in p.changes.items())


def apply_updates(plans: list[PlannedUpdate], client: RedcapClient) -> None:
    """Send the changes, then read them back. Raises RedcapError if anything did not go through."""
    todo = [p for p in plans if p.action == "UPDATE"]
    for p in plans:
        if p.action == "SKIP":
            p.status = "skipped"
    if not todo:
        return
    rows = [{"record_id": p.record_id, **p.changes} for p in todo]
    try:
        client.import_rows(rows)
    except RedcapError as exc:
        for p in todo:
            p.status, p.note = "failed", f"not confirmed - {exc}"
        raise RedcapError(f"sending the changes failed: {exc}") from exc
    # read back to confirm what REDCap actually stored
    try:
        stored = client.export([p.record_id for p in todo], [DUPLICATE_FIELD, COMMENT_FIELD, VALIDATED_FIELD,
                                                             VALIDATION_DATE_FIELD])
    except RedcapError as exc:
        for p in todo:
            p.status, p.note = "failed", f"sent, but could not be read back to confirm - {exc}"
        raise RedcapError(f"the changes were sent but could not be checked afterwards: {exc}") from exc
    for p in todo:
        row = stored.get(p.record_id, {})
        ok = (row.get(DUPLICATE_FIELD) == p.set_duplicate and row.get(VALIDATED_FIELD) == YES
              and bool(row.get(VALIDATION_DATE_FIELD))
              and (COMMENT_FIELD not in p.changes or p.comment_to_add in row.get(COMMENT_FIELD, "")))
        p.status = "updated" if ok else "failed"
        if not ok:
            p.note = "REDCap did not store the change - check the record"
    failed = [p.record_id for p in todo if p.status == "failed"]
    if failed:
        raise RedcapError(f"{len(failed)} record(s) were not stored as expected: {', '.join(failed)}")
