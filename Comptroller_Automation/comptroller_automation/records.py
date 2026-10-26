"""Read records from a REDCap "labels" export (.xlsx or .csv)."""

from pathlib import Path

import pandas as pd

from .fields import FIELDS


def _find_column(columns, label_start):
    want = label_start.lower()
    for col in columns:
        if col.strip().lower().startswith(want):
            return col
    return None


def load_records(path):
    """Return {record_id: {field key: value}}, with every value a string
    ("" when blank). Only the columns this tool uses are kept."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    else:
        df = pd.read_excel(path, dtype=str, keep_default_na=False)

    id_col = _find_column(df.columns, "Record ID")
    if id_col is None:
        raise ValueError(f"No 'Record ID' column in {path}")

    wanted = {key: label for key, (_, label) in FIELDS.items()}
    columns = {}
    for key, label in wanted.items():
        col = _find_column(df.columns, label)
        if col is None:
            raise ValueError(f"No column starting with {label!r} in {path}")
        columns[key] = col

    records = {}
    for _, row in df.iterrows():
        rid = str(row[id_col]).strip()
        if not rid:
            continue
        records[rid] = {key: str(row[col]).strip() for key, col in columns.items()}
    return records
