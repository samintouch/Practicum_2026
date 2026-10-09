"""Load the duplicate-group spreadsheet and the REDCap export, and join them by record ID."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .address import ParsedAddress, parse_address

MISSING = {"", "N/A", "NA", "NAN", "NONE", "NULL", "-"}

# REDCap export column -> field. Matches both the "labels" export (column headers
# are the question text) and the raw export (variable names).
SOURCE_COLUMNS = {
    "record_id": ["record id", "record_id"],
    "name": ["organization/facility name", "organization facility name", "organization_facility_name"],
    "street": ["organization street name", "organization_street_name"],
    "city": ["organization city name", "organization_city_name"],
    "state": ["organization state", "organization_state"],
    "zip": ["organization zip code", "organization_zip_code"],
    "phone": ["organization phone number", "organization_phone_number"],
    "website": ["organization website", "organization_website"],
    "email": ["organization email address", "organization_email_address"],
}

GROUP_COLUMNS = {
    "group_id": ["duplicate_group_id", "duplicate group id"],
    "record_id": ["record_id", "record id", "redcap id"],
    "name": ["organization_facility_name"],
    "street": ["organization_street_name"],
    "city": ["organization_city_name"],
    "state": ["organization_state"],
    "zip": ["organization_zip_code"],
    "completeness_score": ["completeness_score"],
    "duplicate_reason": ["duplicate_reason"],
}


def clean(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = " ".join(str(value).split())
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return None if text.upper() in MISSING else text


def clean_zip(value) -> str | None:
    text = clean(value)
    match = re.search(r"\d{5}", text or "")
    return match.group(0) if match else None


def clean_id(value) -> str | None:
    return clean(value)


def _norm_header(col: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9/_]+", " ", str(col).lower()).split())


def _find_columns(columns, wanted: dict[str, list[str]]) -> dict[str, str]:
    found: dict[str, str] = {}
    normalized = {col: _norm_header(col) for col in columns}
    for field, prefixes in wanted.items():
        for prefix in prefixes:
            hit = next((col for col, norm in normalized.items() if norm.startswith(_norm_header(prefix))), None)
            if hit is not None:
                found[field] = hit
                break
    return found


@dataclass
class Record:
    record_id: str
    group_id: str
    name: str | None
    street: str | None
    city: str | None
    state: str | None
    zip: str | None
    phone: str | None
    website: str | None
    email: str | None
    completeness_score: float | None
    duplicate_reason: str | None

    @property
    def address(self) -> ParsedAddress | None:
        return parse_address(self.street)

    @property
    def full_address(self) -> str:
        city_line = " ".join(p for p in [self.city, self.state, self.zip] if p)
        return ", ".join(p for p in [self.street, city_line] if p)


def load_groups(path: Path, sheet: str | None) -> pd.DataFrame:
    xls = pd.ExcelFile(path)
    sheet = sheet or ("Assigned Duplicate Groups" if "Assigned Duplicate Groups" in xls.sheet_names
                      else xls.sheet_names[0])
    df = pd.read_excel(xls, sheet_name=sheet, dtype=object)
    cols = _find_columns(df.columns, GROUP_COLUMNS)
    missing = [c for c in ("group_id", "record_id") if c not in cols]
    if missing:
        raise SystemExit(f"Sheet '{sheet}' in {path.name} has no column for: {', '.join(missing)}")
    out = pd.DataFrame({field: df[col] for field, col in cols.items()})
    out = out[out["group_id"].notna() & out["record_id"].notna()]
    out["group_id"] = out["group_id"].map(clean_id)
    out["record_id"] = out["record_id"].map(clean_id)
    return out


def load_source(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, dtype=object)
    cols = _find_columns(df.columns, SOURCE_COLUMNS)
    if "record_id" not in cols:
        raise SystemExit(f"Could not find a Record ID column in {path.name}")
    out = pd.DataFrame({field: df[col] for field, col in cols.items()})
    out["record_id"] = out["record_id"].map(clean_id)
    return out.drop_duplicates("record_id").set_index("record_id")


def build_records(groups: pd.DataFrame, source: pd.DataFrame | None) -> dict[str, list[Record]]:
    """Group ID -> records. Values come from the REDCap export when present, else the groups sheet."""
    by_group: dict[str, list[Record]] = {}
    for _, row in groups.iterrows():
        rid = row["record_id"]
        src = source.loc[rid] if source is not None and rid in source.index else None

        def pick(field: str):
            if src is not None and field in src.index and clean(src[field]) is not None:
                return src[field]
            return row.get(field)

        score = row.get("completeness_score")
        try:
            score = float(score) if score is not None and not pd.isna(score) else None
        except (TypeError, ValueError):
            score = None
        rec = Record(
            record_id=rid,
            group_id=row["group_id"],
            name=clean(pick("name")),
            street=clean(pick("street")),
            city=clean(pick("city")),
            state=clean(pick("state")),
            zip=clean_zip(pick("zip")),
            phone=clean(pick("phone")),
            website=clean(pick("website")),
            email=clean(pick("email")),
            completeness_score=score,
            duplicate_reason=clean(row.get("duplicate_reason")),
        )
        by_group.setdefault(rec.group_id, []).append(rec)
    return by_group
