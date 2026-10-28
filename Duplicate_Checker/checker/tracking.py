"""Fill the "Tracking Duplicates" tab of the duplicates workbook, in the same layout as manual reviews.

One row per record, in group order:
  - record kept (most complete):  its duplicates under "potential duplicate with?" and "which is true
                                  duplicate?", and its own ID under "most complete record"
  - duplicate records:            no row of their own - they're listed on the kept record's row
  - not a duplicate:              "Not a duplicate"
  - undecided:                    "Manual review required - possible duplicate with ..."
A group where every record comes back "Not a duplicate" (nothing else mixed in) gets a single combined
row instead - there's no cluster or undecided pair to call out separately, so one row per record would
just repeat the same "different facility" finding N times.
"validated date?" gets the run date for records the app decided, and stays blank for "Manual review
required" rows.
"notes (add to redcap)" holds exactly the comment --apply adds to REDCap; the app's explanation of each
decision goes in an extra "notes from automation" column.
The name/address/mailing-address/other-changes columns are dropped from the tab entirely - a separate
application (Comptroller Automation) is the one that updates those.
"""
from __future__ import annotations

import io
import posixpath
from copy import copy
import re
import shutil
import zipfile
from datetime import date
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .checker import PairResult, RecordResult
from .redcap import PlannedUpdate, describe_changes, plan_updates

SHEET = "Tracking Duplicates"
HEADERS = [
    "Duplicate Group Id",
    "Redcap ID",
    "Status",
    "potential duplicate with? \n(tip: search for potential duplicates by name and address)",
    "which is true duplicate?",
    "which record is most complete record?\n (mark as not duplicate, verify info, ud",
    "records that are not actual duplicates",
    "validated date?",
    "notes (add to redcap)",
    "notes from automation",
    "Website Visited",
    "AI assisted",
]
# column numbers of the app-added columns; everything else is the manual-review template, untouched
(GROUP_COL, STATUS_COL, DUP_WITH_COL, TRUE_DUP_COL, MOST_COMPLETE_COL, NOT_ACTUAL_DUP_COL,
 VALIDATED_DATE_COL, NOTES_COL, AUTOMATION_COL, WEBSITE_VISITED_COL, AI_COL) = 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12
STATUS_COLORS = {                                    # background, font (bold, a darker shade for contrast)
    "Duplicate - Updated Redcap": ("C6EFCE", "006100"), "Duplicate - Skipped": ("E7E6E6", "404040"),
    "failed": ("F8CBAD", "9C0006"), "Manual Review Required": ("F4B183", "833C00"),
    "Not Duplicate": ("D9D2E9", "4C2A85"), "Duplicate": ("BDD7EE", "1F4E78"),
    "Duplicate - Already marked in Redcap": ("FFEB9C", "7F6000"),
}
YES_NO_COLORS = {"Yes": "FFEB9C", "No": "F2F2F2"}      # used for "Website Visited" and "AI assisted"
# website-check results that mean a visit was actually attempted (as opposed to skipped or never possible)
_VISITED_WEBSITE_STATUSES = {"MATCH", "SUITE_DIFFERS", "DIFFERENT", "OTHER_LOCATION", "NOT_FOUND", "UNREACHABLE"}
NEW_RECORDS_SHEET = "New Records to add to redcap"
# name/address columns the Tracking Duplicates tab used to carry; a separate app (Comptroller
# Automation) owns these now, so they're dropped from the tab if the source workbook still has them
REMOVED_COLUMNS = (
    "Old name (previously in redcap)", "New name",
    "Old address (previously in redcap)", "New address",
    "Mailing Address", "other changes",
)
SOURCE = {"Rule": "rule check", "AI": "AI review", "Website rule": "website check"}


def _decided_by(decided: str) -> str:
    return ", ".join(SOURCE.get(d, d) for d in decided.split("/") if d and d != "-") or "the app"


def _short(text: str, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."


def build_rows(results: list[RecordResult], run_date: date | None = None,
               redcap_outcome: list[PlannedUpdate] | None = None,
               pairs: list[PairResult] | None = None) -> list[list]:
    """Rows for the tab, in the order of `results` (group order).
    "records that are not actual duplicates" lists the other records in the group that this record was
    found NOT to duplicate (from `pairs`; undecided pairs are left out).
    "notes (add to redcap)" is exactly the comment --apply adds to REDCap's Additional Comments (blank for
    records --apply doesn't change); "notes from automation" explains how the app decided.
    `redcap_outcome` (the plans after an --apply run) adds what actually happened in REDCap: records that
    were not updated are explained in "notes (add to redcap)", records that were updated are listed in
    "notes from automation"."""
    run_date = run_date or date.today()
    redcap_comment = {p.record_id: p.comment_to_add for p in plan_updates(results, run_date)}
    outcome_by_set: dict[str, list[PlannedUpdate]] = {}
    for p in redcap_outcome or []:
        outcome_by_set.setdefault(p.primary_id, []).append(p)
    order = {res.record.record_id: i for i, res in enumerate(results)}
    not_duplicate_of: dict[str, list[str]] = {}
    for p in pairs or []:
        if p.verdict == "NOT_DUPLICATE":
            not_duplicate_of.setdefault(p.a.record_id, []).append(p.b.record_id)
            not_duplicate_of.setdefault(p.b.record_id, []).append(p.a.record_id)
    group_members: dict[str, list[str]] = {}
    by_group: dict[str, list[RecordResult]] = {}
    for res in results:
        group_members.setdefault(res.record.group_id, []).append(res.record.record_id)
        by_group.setdefault(res.record.group_id, []).append(res)
    # a group where every record is "Not a duplicate" (nothing mixed in) gets one row for the whole
    # group instead of one per record - there's no cluster or undecided pair to call out separately
    all_not_duplicate_groups = {gid for gid, members in by_group.items()
                                if all(m.recommendation == "Not a duplicate" for m in members)}
    merged_groups_done: set[str] = set()
    rows = []
    for res in results:
        r, rec = res.record, res.recommendation
        gid = r.group_id
        if gid in all_not_duplicate_groups:
            if gid in merged_groups_done:
                continue                               # already emitted this group's one combined row
            merged_groups_done.add(gid)
            members = by_group[gid]
            ids = ", ".join(m.record.record_id for m in members)
            sources = "/".join(sorted({d for m in members for d in m.decided_by.split("/") if d and d != "-"}))
            note = (f"Not a duplicate. All {len(members)} records in this group ({ids}) were compared "
                    f"and found to be different facilities. Decided by {_decided_by(sources)}.")
            ai_assisted = "Yes" if any("AI" in m.decided_by.split("/") for m in members) else "No"
            website_visited = "Yes" if any(m.website is not None and m.website.status in _VISITED_WEBSITE_STATUSES
                                           for m in members) else "No"
            rows.append([_number(gid), _number(ids), "Not Duplicate", "", "", "", _number(ids),
                        run_date, "", note, website_visited, ai_assisted])
            continue
        if rec.startswith("Duplicate of"):
            continue                                   # listed on the kept record's row
        dup_with = ", ".join(res.duplicate_with)
        if rec.startswith("Duplicate - keep"):
            cols = [dup_with, dup_with, r.record_id]
            note = (f"True duplicate. Keep this record (most complete); duplicate record(s): "
                    f"{dup_with}. Decided by {_decided_by(res.decided_by)}: {_short(res.reason)}")
        elif rec == "Not a duplicate":
            cols = ["", "", ""]
            note = f"Not a duplicate. Decided by {_decided_by(res.decided_by)}: {_short(res.reason)}"
        else:
            cols = [f"Manual review required - possible duplicate with {dup_with}".strip(" -"), "", ""]
            note = (f"MANUAL REVIEW REQUIRED - the app could not decide whether this record is a "
                    f"duplicate of {dup_with or 'the other records'}. {_short(res.reason)}")
        # "validated date?": the run date for decided records; blank while a person still has to decide
        validated = "" if rec == "Needs review" else run_date
        redcap_note = redcap_comment.get(r.record_id, "")
        status = ("Manual Review Required" if rec == "Needs review" else "Not Duplicate" if rec == "Not a duplicate"
                   else "Duplicate" if rec.startswith("Duplicate - keep") else "")
        ai_assisted = "Yes" if "AI" in res.decided_by.split("/") else "No"
        website_visited = "Yes" if res.website is not None and res.website.status in _VISITED_WEBSITE_STATUSES else "No"
        if r.record_id in outcome_by_set:
            redcap_note, note = _with_redcap_outcome(outcome_by_set[r.record_id], r.record_id, redcap_note,
                                                     note, run_date)
            status = _set_status(outcome_by_set[r.record_id])
        not_dups = ", ".join(sorted(not_duplicate_of.get(r.record_id, []), key=lambda x: order.get(x, 0)))
        ids_in_group = ", ".join(group_members.get(gid, [r.record_id]))
        rows.append([_number(gid), _number(ids_in_group), status, *map(_number, cols),
                     _number(not_dups), validated, redcap_note, note, website_visited, ai_assisted])
    return rows


def _set_status(plans: list[PlannedUpdate]) -> str:
    """One status for a kept record and its duplicates."""
    statuses = {p.status for p in plans}
    if statuses & {"failed", "sending"}:
        return "failed"
    if "updated" in statuses:
        return "Duplicate - Updated Redcap"
    if statuses == {"skipped"}:
        return "Duplicate - Skipped"
    if statuses == {"no update required"}:
        return "Duplicate - Already marked in Redcap"
    return ""


def _with_redcap_outcome(plans: list[PlannedUpdate], kept_id: str, redcap_note: str, note: str,
                         run_date: date) -> tuple[str, str]:
    """Add what --apply did to a kept record's row (the row covers its duplicates too)."""
    plans = sorted(plans, key=lambda p: p.role != "kept")          # the kept record first
    kept = next((p for p in plans if p.record_id == kept_id), None)
    # the comment only went into REDCap if the kept record was actually updated
    lines = [redcap_note] if kept is not None and kept.status == "updated" else []
    lines += [f"Record {p.record_id} - {p.note}"
              for p in plans if p.status in ("skipped", "no update required")]
    updated = [f"record {p.record_id} ({describe_changes(p)})" for p in plans if p.status == "updated"]
    failed = [f"record {p.record_id} ({p.note})" for p in plans if p.status in ("failed", "sending")]
    if updated:
        note += f" REDCap updated on {run_date:%m/%d/%Y}: {'; '.join(updated)}."
    if failed:
        note += f" REDCap update FAILED for {'; '.join(failed)}."
    return "\n".join(line for line in lines if line), note


def _number(value: str):
    """A single record ID goes in as a number (like the manual sheet); lists like '256, 312' stay text."""
    return int(value) if isinstance(value, str) and value.isdigit() else value


def _open_workbook(path: Path):
    """openpyxl, tolerating workbooks (e.g. from R's openxlsx) that list parts which don't exist."""
    try:
        return load_workbook(path)
    except KeyError:
        pass
    src = zipfile.ZipFile(path)
    names = set(src.namelist())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            data = src.read(name)
            if name.endswith(".rels"):
                base = posixpath.dirname(posixpath.dirname(name))
                text = data.decode("utf-8")
                for target in re.findall(r'Target="([^"]+)"', text):
                    resolved = posixpath.normpath(posixpath.join(base, target)).lstrip("/")
                    if "://" not in target and resolved not in names:
                        text = re.sub(rf'<Relationship [^>]*Target="{re.escape(target)}"[^>]*/>', "", text)
                data = text.encode("utf-8")
            dst.writestr(name, data)
    buf.seek(0)
    return load_workbook(buf)


APP_COLUMNS = ("status", "Status", "AI assisted", "Duplicate Group Id", "notes from automation", "Website Visited")


def _drop_columns(ws, names) -> None:
    """Delete any column in `ws` whose header exactly matches one of `names`, wherever it is."""
    header_row = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
    for name in names:
        for c in range(len(header_row), 0, -1):
            if header_row[c - 1] == name:
                ws.delete_cols(c)
                header_row.pop(c - 1)


def _migrate_to_new_layout(ws) -> None:
    """Move an older tab to the current column layout: drop any of the app's own columns, wherever an
    earlier version of the app put them, then reinsert them at their current positions. The manual-review
    template's remaining columns (notes (add to redcap), validated date?, ...) are never reordered."""
    _drop_columns(ws, APP_COLUMNS)
    # back to the template's own columns, starting with its own "Redcap ID" in column 1 - make room
    # for Group Id in front of it, then for Status right after it
    ws.insert_cols(GROUP_COL, amount=1)             # blank column for Group Id, before Redcap ID
    ws.insert_cols(STATUS_COL, amount=1)            # blank column for Status, right after Redcap ID
    # insert_cols()/delete_cols() move cell content but not column widths (an openpyxl limitation), so a
    # width set for the column that used to be here would otherwise stay behind under the new content.
    # Drop every explicit width; write_tracking_tab sets the ones it cares about right after this runs.
    for letter in list(ws.column_dimensions.keys()):
        del ws.column_dimensions[letter]


def write_tracking_tab(source: Path, output: Path, rows: list[list]) -> None:
    """Copy `source` to `output` (unless they're the same file) and refill its Tracking Duplicates tab.
    Every other sheet is left as it is, except "New Records to add to redcap" (if present), which is
    dropped - the Duplicate Checker never adds new records, so that tab would stay empty and misleading.
    The name/address/mailing-address/other-changes columns are also dropped from Tracking Duplicates if
    the source workbook still has them - a separate application (Comptroller Automation) updates those.
    When writing over the source itself, a backup is made first."""
    if source.resolve() == output.resolve():
        backup = source.with_name(f"{source.stem}_backup_{date.today():%Y-%m-%d}{source.suffix}")
        shutil.copyfile(source, backup)
        print(f"Backup of {source.name}: {backup.name}")
    wb = _open_workbook(source)
    if NEW_RECORDS_SHEET in wb.sheetnames:
        del wb[NEW_RECORDS_SHEET]
    if SHEET in wb.sheetnames:
        ws = wb[SHEET]
        _drop_columns(ws, REMOVED_COLUMNS)
        if ws.cell(row=1, column=GROUP_COL).value != HEADERS[GROUP_COL - 1]:
            _migrate_to_new_layout(ws)
        for row in ws.iter_rows(min_row=2, max_row=max(ws.max_row, 2)):
            for cell in row:
                cell.value = None
        for row in range(2, max(ws.max_row, 2) + 1):          # status/AI/website colors from an earlier run
            ws.cell(row=row, column=STATUS_COL).fill = PatternFill()
            ws.cell(row=row, column=AI_COL).fill = PatternFill()
            ws.cell(row=row, column=WEBSITE_VISITED_COL).fill = PatternFill()
        notes_header = ws.cell(row=1, column=NOTES_COL)
        for col in (GROUP_COL, STATUS_COL, WEBSITE_VISITED_COL, AI_COL, AUTOMATION_COL):
            new_header = ws.cell(row=1, column=col)
            if new_header.value != HEADERS[col - 1]:
                # tabs from before these columns existed: add the header, styled like the notes header
                new_header.value = HEADERS[col - 1]
                if notes_header.has_style:
                    new_header.font, new_header.fill = copy(notes_header.font), copy(notes_header.fill)
                    new_header.border = copy(notes_header.border)
                new_header.alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")
    else:
        ws = wb.create_sheet(SHEET)
        for j, header in enumerate(HEADERS, start=1):
            cell = ws.cell(row=1, column=j, value=header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="D9E1F2")
            cell.alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")
    wrap = Alignment(wrap_text=True, vertical="top", horizontal="left")
    for i, values in enumerate(rows, start=2):
        for j, value in enumerate(values, start=1):
            cell = ws.cell(row=i, column=j, value=value if value != "" else None)
            if isinstance(value, date):
                cell.number_format = "mm/dd/yyyy"
            cell.alignment = wrap
            if j == STATUS_COL and value in STATUS_COLORS:
                bg, font_color = STATUS_COLORS[value]
                cell.fill = PatternFill("solid", fgColor=bg)
                cell.font = Font(bold=True, color=font_color)
            elif j in (WEBSITE_VISITED_COL, AI_COL) and value in YES_NO_COLORS:
                cell.fill = PatternFill("solid", fgColor=YES_NO_COLORS[value])
    ws.column_dimensions[get_column_letter(GROUP_COL)].width = max(ws.column_dimensions[get_column_letter(GROUP_COL)].width or 0, 9)
    ws.column_dimensions[get_column_letter(STATUS_COL)].width = max(ws.column_dimensions[get_column_letter(STATUS_COL)].width or 0, 32)
    ws.column_dimensions[get_column_letter(AI_COL)].width = max(ws.column_dimensions[get_column_letter(AI_COL)].width or 0, 12)
    ws.column_dimensions[get_column_letter(WEBSITE_VISITED_COL)].width = max(ws.column_dimensions[get_column_letter(WEBSITE_VISITED_COL)].width or 0, 14)
    ws.column_dimensions[get_column_letter(DUP_WITH_COL)].width = max(ws.column_dimensions[get_column_letter(DUP_WITH_COL)].width or 0, 40)
    ws.column_dimensions[get_column_letter(TRUE_DUP_COL)].width = max(ws.column_dimensions[get_column_letter(TRUE_DUP_COL)].width or 0, 26)
    ws.column_dimensions[get_column_letter(MOST_COMPLETE_COL)].width = max(ws.column_dimensions[get_column_letter(MOST_COMPLETE_COL)].width or 0, 26)
    ws.column_dimensions[get_column_letter(NOT_ACTUAL_DUP_COL)].width = max(ws.column_dimensions[get_column_letter(NOT_ACTUAL_DUP_COL)].width or 0, 26)
    ws.column_dimensions[get_column_letter(VALIDATED_DATE_COL)].width = max(ws.column_dimensions[get_column_letter(VALIDATED_DATE_COL)].width or 0, 16.25)
    ws.column_dimensions[get_column_letter(NOTES_COL)].width = max(ws.column_dimensions[get_column_letter(NOTES_COL)].width or 0, 60)
    ws.column_dimensions[get_column_letter(AUTOMATION_COL)].width = max(ws.column_dimensions[get_column_letter(AUTOMATION_COL)].width or 0, 90)
    ws.freeze_panes = "A2"
    wb.save(output)
