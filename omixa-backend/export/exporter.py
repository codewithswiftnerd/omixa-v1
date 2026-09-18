import pandas as pd

# Characters that Excel/Sheets/LibreOffice treat as "this cell is a
# formula" if they're the FIRST character of a cell's text. A CSV or
# XLSX round-tripped through this tool carries whatever the original
# uploader typed, including, potentially, a malicious formula like
# `=cmd|'/c calc'!A1` or `=HYPERLINK("http://evil","click")`, and
# since export is the last thing this app does with the data before
# handing it back to (possibly a different) user to open in a
# spreadsheet app, this is the point where that has to be neutralized.
# See OWASP's CSV Injection guidance.
_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _defuse_cell(value):
    """Prefixes a leading formula-trigger character with a single
    quote, which every major spreadsheet app renders as "force this
    cell to be treated as plain text", the standard mitigation.
    Leaves non-strings (numbers, booleans, NaN) untouched; only text
    cells can carry a formula payload."""
    if isinstance(value, str) and value[:1] in _FORMULA_TRIGGER_CHARS:
        return "'" + value
    return value


def sanitize_formula_injection(df: pd.DataFrame) -> pd.DataFrame:
    """Applies _defuse_cell to every text column. Numeric/boolean/
    datetime columns are skipped entirely, a formula payload can
    only live in a text cell, and this avoids ever touching a
    genuinely numeric value (e.g. a real -5)."""
    text_columns = df.select_dtypes(include=["object", "string"]).columns
    if len(text_columns) == 0:
        return df
    df = df.copy()
    for col in text_columns:
        df[col] = df[col].map(_defuse_cell)
    return df


def export_dataframe(
    df: pd.DataFrame,
    out_path: str,
    ext: str,
    source_path: str | None = None,
    source_sheet_name: str | None = None,
) -> None:
    """Writes the cleaned data to out_path.

    Scope note (see docs/functionality-notes.md): Omixa only ever
    changes cell VALUES. It never sets or forces a font, font size,
    bold/italic state, or column width, on the sheet it writes,
    those simply come out as the target library's defaults because
    this function builds a fresh sheet from the DataFrame rather than
    editing the original file's cells in place. Genuinely preserving
    the original look of the cleaned sheet itself (its exact fonts,
    styles, and column widths, cell by cell) is a bigger, separate
    change, not attempted here.

    What IS handled: if the upload was itself an .xlsx with more than
    one sheet, every sheet OTHER than the one Omixa actually read and
    cleaned is now carried through to the output completely
    untouched, byte-for-byte as it was in the source file, instead of
    silently disappearing (see source_path/source_sheet_name below).
    """
    df = sanitize_formula_injection(df)

    if ext == "csv":
        df.to_csv(out_path, index=False)
        return

    if source_path and source_path.rsplit(".", 1)[-1].lower() == "xlsx":
        if _export_excel_preserving_other_sheets(df, out_path, source_path, source_sheet_name):
            return

    df.to_excel(out_path, index=False)


def _export_excel_preserving_other_sheets(
    df: pd.DataFrame, out_path: str, source_path: str, source_sheet_name: str | None
) -> bool:
    """Rebuilds only the sheet Omixa cleaned; every other sheet in the
    source workbook is copied through as-is. Returns False (falls
    back to a plain single-sheet export) if the source can't be
    reopened with openpyxl for any reason, an unreadable source
    shouldn't turn into a hard failure on download."""
    try:
        import openpyxl
    except ImportError:
        return False

    try:
        wb = openpyxl.load_workbook(source_path)
    except Exception:
        return False

    target_name = source_sheet_name if source_sheet_name in wb.sheetnames else wb.sheetnames[0]
    target_index = wb.sheetnames.index(target_name)

    # Drop the old (uncleaned) version of the target sheet and
    # recreate it in the same tab position, so the other sheets keep
    # their original order relative to it.
    del wb[target_name]
    ws = wb.create_sheet(title=target_name, index=target_index)

    ws.append([str(c) for c in df.columns])
    for row in df.itertuples(index=False, name=None):
        ws.append([None if pd.isna(v) else v for v in row])

    wb.save(out_path)
    return True
