"""
Resolutions

cleaning/rules.py auto-fixes what's unambiguous. cleaning/quality_report.py
flags what isn't. This module is the bridge between the two: for
issue types where a human CAN safely resolve the ambiguity (they just
have to say which interpretation is right, or whether to drop vs.
keep something), it defines the available choices and applies
whichever one the user actually picks.

Nothing here runs automatically. A resolution only touches the data
when the frontend sends an explicit { column, issue, choice } for it
(see routes/process.py + processing/pipeline.py). Every issue type
below also has an implicit "skip" choice, do nothing, leave the
column exactly as flagged, which is what happens if the user never
responds, so silence is always the safe default.

Each issue's options are pure metadata (id/label/description) so the
frontend can render them without hardcoding the choices; each
resolver function has the same shape:

    (df: pd.DataFrame, column: str) -> (pd.DataFrame, int)   # changed count
"""

from __future__ import annotations
from typing import Callable
import pandas as pd

from cleaning.phone_formats import COUNTRIES, normalize_phone

# issue -> list of {id, label, description} choices offered to the
# user, in display order. "skip" is implied for every issue and
# omitted here on purpose, it's the no-op default, not a button.
RESOLUTION_OPTIONS: dict[str, list[dict]] = {
    "ambiguous_date_format": [
        {"id": "day_first", "label": "Day first (31/12/2024)",
         "description": "Standardize to YYYY-MM-DD reading day before month."},
        {"id": "month_first", "label": "Month first (12/31/2024)",
         "description": "Standardize to YYYY-MM-DD reading month before day."},
    ],
    "high_missingness": [
        {"id": "fill_anyway", "label": "Fill anyway",
         "description": "Fill gaps with the column median (numbers) or \"Unknown\" (text), same as the 'Fill gaps' rule uses elsewhere."},
        {"id": "drop_column", "label": "Drop this column",
         "description": "Remove the column entirely rather than keep a mostly-empty field."},
    ],
    "potential_outliers": [
        {"id": "cap", "label": "Cap to typical range",
         "description": "Clip outlier values to the nearest edge of the typical (IQR) range instead of removing them."},
        {"id": "remove_rows", "label": "Remove those rows",
         "description": "Drop rows where this column falls outside the typical range."},
    ],
    "constant_column": [
        {"id": "drop_column", "label": "Drop this column",
         "description": "Remove the column since every value is the same."},
    ],
    "mixed_data_types": [
        {"id": "coerce_numeric", "label": "Convert to numbers",
         "description": "Convert the column to numeric, blanking out any value that isn't a number."},
    ],
    "invalid_email_format": [
        {"id": "blank_invalid", "label": "Blank invalid values",
         "description": "Clear only the values that don't look like valid emails; valid ones are untouched."},
    ],
    "impossible_age": [
        {"id": "blank_invalid", "label": "Blank invalid values",
         "description": "Clear only the out-of-range age values (below 0 or above 120); the rest of the row is kept."},
        {"id": "remove_rows", "label": "Remove those rows",
         "description": "Drop rows where this column has an impossible age."},
    ],
    "impossible_date": [
        {"id": "blank_invalid", "label": "Blank invalid values",
         "description": "Clear only the impossible date values; the rest of the row is kept."},
        {"id": "remove_rows", "label": "Remove those rows",
         "description": "Drop rows where this column has an impossible date."},
    ],
    "possible_duplicate_records": [
        {"id": "remove_near_duplicates", "label": "Remove near-duplicate rows",
         "description": "Drop rows that match another row on every column except this one, keeping the first."},
    ],
    # Built from cleaning/phone_formats.py rather than hardcoded here,
    # so the country list only needs to be maintained in one place.
    # Each option's "group" is the region, used by the frontend to
    # render a grouped <select> instead of one long flat list.
    "suspicious_phone_format": [
        {"id": code, "label": f'{c["name"]} (+{c["dial_code"]})', "group": c["region"],
         "description": f'Normalize to +{c["dial_code"]}-prefixed numbers wherever the digit count matches {c["name"]}\'s format; anything that doesn\'t match is left as-is.'}
        for code, c in COUNTRIES.items()
    ],
}


def _resolve_ambiguous_date(df: pd.DataFrame, column: str, choice: str) -> tuple[pd.DataFrame, int]:
    from cleaning import detectors  # local import: avoids a cycle with rules.py at module load time

    series = df[column]
    day_first = choice == "day_first"
    parsed = pd.to_datetime(series.astype("string"), errors="coerce", dayfirst=day_first, format="mixed")

    formatted = parsed.dt.strftime("%Y-%m-%d")
    original = series.astype("string")
    new_values = original.copy()
    has_value = parsed.notna()
    new_values.loc[has_value] = formatted[has_value]

    diff = (original != new_values) & original.notna()
    count = int(diff.sum())
    if count:
        df[column] = new_values
    return df, count


def _resolve_high_missingness(df: pd.DataFrame, column: str, choice: str) -> tuple[pd.DataFrame, int]:
    if choice == "drop_column":
        if column in df.columns:
            df = df.drop(columns=[column])
            return df, 1
        return df, 0

    # fill_anyway
    missing = int(df[column].isna().sum())
    if not missing:
        return df, 0
    if pd.api.types.is_numeric_dtype(df[column]):
        fill_value = df[column].median()
    else:
        fill_value = "Unknown"
    df[column] = df[column].fillna(fill_value)
    return df, missing


def _resolve_outliers(df: pd.DataFrame, column: str, choice: str) -> tuple[pd.DataFrame, int]:
    series = df[column].dropna()
    if len(series) < 10:
        return df, 0
    q1, q3 = series.quantile(0.25), series.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return df, 0
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr

    if choice == "remove_rows":
        mask = (df[column] < lo) | (df[column] > hi)
        count = int(mask.sum())
        if count:
            df = df.loc[~mask].reset_index(drop=True)
        return df, count

    # cap
    mask = (df[column] < lo) | (df[column] > hi)
    count = int(mask.sum())
    if count:
        df[column] = df[column].clip(lower=lo, upper=hi)
    return df, count


def _resolve_constant_column(df: pd.DataFrame, column: str, choice: str) -> tuple[pd.DataFrame, int]:
    if column in df.columns:
        df = df.drop(columns=[column])
        return df, 1
    return df, 0


def _resolve_mixed_types(df: pd.DataFrame, column: str, choice: str) -> tuple[pd.DataFrame, int]:
    from cleaning import detectors

    original = df[column].astype("string")
    cleaned = original.map(lambda v: detectors.strip_numeric_noise(v) if isinstance(v, str) else v)
    parsed = cleaned.map(lambda v: detectors.try_parse_float(v) if isinstance(v, str) else None)

    # Every non-null value gets reassigned: the parsed number where it
    # converts cleanly, NaN (real missing) where it doesn't, this is
    # what "convert to numeric, blank the rest" means.
    changed_mask = original.notna()
    count = int(changed_mask.sum())
    if count:
        df[column] = parsed
    return df, count


def _resolve_invalid_email(df: pd.DataFrame, column: str, choice: str) -> tuple[pd.DataFrame, int]:
    from cleaning import detectors

    series = df[column].astype("string")
    non_null = series.notna()
    invalid = non_null & ~series.str.match(detectors.EMAIL_RE)
    count = int(invalid.sum())
    if count:
        df.loc[invalid, column] = pd.NA
    return df, count


def _resolve_impossible_age(df: pd.DataFrame, column: str, choice: str) -> tuple[pd.DataFrame, int]:
    series = df[column]
    if not pd.api.types.is_numeric_dtype(series):
        return df, 0
    mask = series.notna() & ((series < 0) | (series > 120))
    count = int(mask.sum())
    if not count:
        return df, 0

    if choice == "remove_rows":
        df = df.loc[~mask].reset_index(drop=True)
    else:  # blank_invalid
        df.loc[mask, column] = pd.NA
    return df, count


def _resolve_impossible_date(df: pd.DataFrame, column: str, choice: str) -> tuple[pd.DataFrame, int]:
    from cleaning import detectors  # local import: avoids a cycle with rules.py at module load time

    series = df[column]
    parsed = detectors.unambiguous_date_parse(series)
    if parsed is None:
        return df, 0  # ambiguous column, resolve that first, same as the report's own check

    too_old = parsed.notna() & (parsed.dt.year < detectors.MIN_PLAUSIBLE_YEAR)
    future = pd.Series(False, index=series.index)
    if detectors.is_birth_date_column(str(column)):
        now = pd.Timestamp.now()
        future = parsed.notna() & (parsed > now)
    mask = too_old | future
    count = int(mask.sum())
    if not count:
        return df, 0

    if choice == "remove_rows":
        df = df.loc[~mask].reset_index(drop=True)
    else:  # blank_invalid
        df.loc[mask, column] = pd.NA
    return df, count


def _resolve_possible_duplicates(df: pd.DataFrame, column: str, choice: str) -> tuple[pd.DataFrame, int]:
    other_cols = [c for c in df.columns if c != column]
    if not other_cols:
        return df, 0
    # Rows that match another row on every OTHER column but are not
    # already exact duplicates: keep the first of each such group,
    # drop the rest, mirrors handle_duplicates' own "keep first"
    # convention in cleaning/rules.py.
    exact_dupes = df.duplicated(keep=False)
    near_dupe_group = df.duplicated(subset=other_cols, keep=False) & ~exact_dupes
    near_dupe_after_first = df.duplicated(subset=other_cols, keep="first")
    to_drop = near_dupe_group & near_dupe_after_first
    count = int(to_drop.sum())
    if count:
        df = df.loc[~to_drop].reset_index(drop=True)
    return df, count


def _resolve_suspicious_phone(df: pd.DataFrame, column: str, choice: str) -> tuple[pd.DataFrame, int]:
    """
    choice is an ISO country code (e.g. "NG", "IN", "US") from
    cleaning/phone_formats.COUNTRIES. Normalizes every value whose
    digit count matches that country's expected format to
    "+<dial_code><national number>"; anything that doesn't match
    (wrong length, obviously not a phone number, etc.) is left
    exactly as it was, never truncated, padded, or guessed at.
    """
    original = df[column].astype("string")
    normalized = original.map(lambda v: normalize_phone(v, choice) if isinstance(v, str) else None)

    changed_mask = normalized.notna() & (normalized != original)
    count = int(changed_mask.sum())
    if count:
        df.loc[changed_mask, column] = normalized[changed_mask]
    return df, count


# issue -> resolver(df, column, choice) -> (df, count)
_RESOLVERS: dict[str, Callable] = {
    "ambiguous_date_format": _resolve_ambiguous_date,
    "high_missingness": _resolve_high_missingness,
    "potential_outliers": _resolve_outliers,
    "constant_column": _resolve_constant_column,
    "mixed_data_types": _resolve_mixed_types,
    "invalid_email_format": _resolve_invalid_email,
    "impossible_age": _resolve_impossible_age,
    "impossible_date": _resolve_impossible_date,
    "possible_duplicate_records": _resolve_possible_duplicates,
    "suspicious_phone_format": _resolve_suspicious_phone,
}


def apply_resolutions(df: pd.DataFrame, resolutions: list[dict] | None) -> tuple[pd.DataFrame, dict]:
    """
    resolutions: [ { "column": "signup_date", "issue": "ambiguous_date_format", "choice": "day_first" }, ... ]

    Applied AFTER the standard rule set (see processing/pipeline.py),
    so this only ever acts on columns the default rules already
    declined to touch. Unknown issue/choice combos, or a column that
    no longer exists (e.g. it was already dropped by an earlier
    resolution in the same batch), are skipped rather than raising, a stale selection shouldn't fail the whole run.
    """
    log = {"applied": [], "skipped": []}
    for item in resolutions or []:
        column = item.get("column")
        issue = item.get("issue")
        choice = item.get("choice")

        resolver = _RESOLVERS.get(issue)
        valid_choice_ids = {opt["id"] for opt in RESOLUTION_OPTIONS.get(issue, [])}

        if not resolver or choice not in valid_choice_ids or column not in df.columns:
            log["skipped"].append({"column": column, "issue": issue, "choice": choice})
            continue

        df, count = resolver(df, column, choice)
        log["applied"].append({"column": column, "issue": issue, "choice": choice, "changed": count})

    return df, log
