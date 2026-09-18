"""
Orchestrates: Read -> Clean -> Export

This file doesn't know the details of any single cleaning rule, that lives in cleaning/rules.py, which is Misumi's spec turned into
code. This file just wires the steps together in order.
"""

import os
from typing import Optional, List
import pandas as pd

from utils.file_handler import find_source_file, cleaned_file_path
from cleaning.rules import apply_rules
from cleaning.resolutions import apply_resolutions
from cleaning.quality_report import generate_report
from cleaning.summary import build_cleaning_summary
from export.exporter import export_dataframe


def read_source(path: str, has_header: bool = True) -> pd.DataFrame:
    """
    Reads the uploaded file, and specifically protects
    identifier-looking columns (phone/account/IBAN/BVN/zip/etc., see detectors.is_identifier_name) from pandas' own dtype
    inference, which runs before any cleaning rule does and would
    otherwise silently drop a leading zero (e.g. a CSV column of
    phone numbers gets read as int64 by default, "0803317157"
    becomes 803317157, and there's no getting that zero back later).
    Reading those specific columns as raw strings up front is what
    lets the later identifier-aware cleaning rules do their job.

    has_header: whether the first row is a header row. Omixa never
    decides this on its own, it's whatever the caller (ultimately the
    user, see routes/process.py's "has_header") says it is. When
    False, the first row is treated as ordinary data and the columns
    are given generic placeholder names (column_1, column_2, ...).
    """
    from cleaning import detectors  # local import: avoids a cycle with rules.py at module load time

    ext = path.rsplit(".", 1)[1].lower()
    header_arg = 0 if has_header else None

    if ext == "csv":
        header = pd.read_csv(path, nrows=0, header=header_arg)
    else:
        header = pd.read_excel(path, nrows=0, header=header_arg)

    dtype_overrides = {
        col: str for col in header.columns if detectors.is_identifier_name(str(col))
    } or None

    if ext == "csv":
        df = pd.read_csv(path, dtype=dtype_overrides, header=header_arg)
    else:
        df = pd.read_excel(path, dtype=dtype_overrides, header=header_arg)

    if not has_header:
        df.columns = [f"column_{i + 1}" for i in range(len(df.columns))]

    return df


def _first_sheet_name(path: str) -> Optional[str]:
    """Name of the sheet pd.read_excel(..., sheet_name=0) actually
    read, used so export.exporter can put the cleaned data back into
    that same sheet while leaving every other sheet in the workbook
    alone. Returns None for anything that isn't a readable .xlsx
    (e.g. a .csv, or a .xls openpyxl can't open), the exporter falls
    back to a plain export in that case."""
    if path.rsplit(".", 1)[-1].lower() != "xlsx":
        return None
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True)
        return wb.sheetnames[0]
    except Exception:
        return None


def run_pipeline(
    job_id: str,
    rules: Optional[List[str]] = None,
    resolutions: Optional[List[dict]] = None,
    has_header: bool = True,
) -> dict:
    """
    Returns a summary dict the frontend can display, e.g.:
        {
          "rows_in": 1000,
          "rows_out": 940,
          "rules_applied": ["missing_values", "duplicates"],
          "changes": {"duplicates_removed": 40, "missing_values_fixed": 20}
        }

    `resolutions` (optional) is a list of user-picked fixes for
    findings that no default rule can safely auto-apply on its own, e.g. "treat this date column as day-first". These are applied
    AFTER the standard rule set, and only ever touch the specific
    column/issue the user explicitly chose; see
    cleaning/resolutions.py. Omitting this argument (or passing an
    empty list) behaves exactly as before this existed.

    `has_header` (optional, default True) is whether the file's first
    row is a header row, see read_source()'s docstring, it's a caller
    choice, never an Omixa assumption.
    """
    source_path = find_source_file(job_id)
    if not source_path:
        raise FileNotFoundError("No source file found for this job")

    df = read_source(source_path, has_header=has_header)
    rows_in = len(df)
    columns_in = len(df.columns)

    # Read-only, and run BEFORE any rule touches df, so this is
    # guaranteed to reflect the file exactly as uploaded, the same
    # report /api/report/<job_id> would return for this file.
    quality_before = generate_report(df)

    # Resolutions are applied FIRST, against the exact column names
    # generate_report() (and therefore the frontend, and therefore
    # the caller's "resolutions" payload) used, e.g. "Signup Date",
    # not the rule-cleaned "signup_date". apply_rules' own
    # column_names step renames columns as one of its first actions,
    # so running resolutions after it, as this used to, meant every
    # resolution's "column" value was already stale and silently
    # matched nothing: apply_resolutions skips (rather than errors on)
    # a column it can't find, so this failed with no visible error at
    # all. See cleaning/resolutions.py's apply_resolutions docstring.
    df, resolution_log = apply_resolutions(df, resolutions)
    cleaned_df, change_log = apply_rules(df, rules=rules)

    rows_out = len(cleaned_df)

    ext = source_path.rsplit(".", 1)[1].lower()
    # .xls (legacy binary Excel) can be READ via xlrd, but pandas has
    # no maintained engine to WRITE it back out, the only library
    # that ever did (xlwt) has been unmaintained for years and isn't
    # a dependency here. So a .xls upload is still fully supported,
    # it just always comes back out as .xlsx, which every modern
    # spreadsheet app opens fine. It also means a .xls source can
    # never keep its other sheets (openpyxl can't open .xls at all,
    # see _first_sheet_name()), only a .xlsx source can.
    out_ext = "xlsx" if ext == "xls" else ext
    out_path = cleaned_file_path(job_id, out_ext)
    export_dataframe(
        cleaned_df,
        out_path,
        ext=out_ext,
        source_path=source_path,
        source_sheet_name=_first_sheet_name(source_path),
    )

    # Re-run the same read-only quality checks against the cleaned data
    # so the frontend can show a before/after score, not just a list of
    # "N cells changed" counts.
    post_report = generate_report(cleaned_df)

    cleaning_summary = build_cleaning_summary(
        rows_in=rows_in,
        rows_out=rows_out,
        column_count=len(cleaned_df.columns) or columns_in,
        change_log=change_log,
        quality_before=quality_before,
        quality_after=post_report,
        resolution_log=resolution_log,
    )

    return {
        "rows_in": rows_in,
        "rows_out": rows_out,
        "rules_applied": change_log["rules_applied"],
        "changes": change_log["changes"],
        "details": change_log.get("details", {}),
        "resolutions_applied": resolution_log["applied"],
        "resolutions_skipped": resolution_log["skipped"],
        "quality_report": post_report,
        "quality_report_before": quality_before,
        "cleaning_summary": cleaning_summary,
    }
