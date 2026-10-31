"""Read records from a REDCap export: .xlsx or .csv, with either label
headers ("Organization/Facility name", the "labels" export) or variable-name
headers ("ct1", the raw export)."""

from pathlib import Path

import pandas as pd

from .fields import FIELDS


def _find_column(columns, label_start):
    want = label_start.lower()
    for col in columns:
        if col.strip().lower().startswith(want):
            return col
    return None


def _read_csv(path):
    """REDCap writes UTF-8 with a byte-order mark; a CSV re-saved in Excel
    is often Windows-1252 instead."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return pd.read_csv(path, dtype=str, keep_default_na=False, encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Can't read {path}: it isn't UTF-8 or Windows-1252 text")


def _columns(df, path):
    """(record-id column, {field key: column}) for either kind of export."""
    headers = {c.strip().lower(): c for c in df.columns}
    if "record_id" in headers:
        # raw export: headers are REDCap variable names
        columns = {}
        for key, (variable, _) in FIELDS.items():
            if variable not in headers:
                raise ValueError(f"No {variable!r} column ({key}) in {path}")
            columns[key] = headers[variable]
        return headers["record_id"], columns

    id_col = _find_column(df.columns, "Record ID")
    if id_col is None:
        raise ValueError(f"No 'Record ID' or 'record_id' column in {path}; is it a REDCap export?")
    columns = {}
    for key, (_, label) in FIELDS.items():
        col = _find_column(df.columns, label)
        if col is None:
            raise ValueError(f"No column starting with {label!r} in {path}")
        columns[key] = col
    return id_col, columns


def load_records(path):
    """Return {record_id: {field key: value}}, with every value a string
    ("" when blank). Only the columns this tool uses are kept."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        df = _read_csv(path)
    else:
        df = pd.read_excel(path, dtype=str, keep_default_na=False)
    id_col, columns = _columns(df, path)

    records = {}
    for _, row in df.iterrows():
        rid = str(row[id_col]).strip()
        if not rid:
            continue
        records[rid] = {key: str(row[col]).strip() for key, col in columns.items()}
    return records
