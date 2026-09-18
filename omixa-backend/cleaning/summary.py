"""
Cleaning Summary

Turns the machine-oriented change_log (cleaning/rules.apply_rules)
and the before/after quality reports (cleaning/quality_report.py)
into the human-readable "DATA CLEANING SUMMARY" the improvement spec
asks for, without inventing any number that isn't already tracked
elsewhere in the pipeline. Nothing here mutates a dataframe or makes
a cleaning decision; it's purely a reporting layer on top of what
processing/pipeline.py already computed.
"""

from __future__ import annotations


def _numeric_type_totals(details: dict) -> tuple[int, int]:
    """Splits numeric_text_cleaning's single combined count into
    currency vs percentage, using the per-column "type" tag each rule
    already attaches (cleaning/rules.handle_numeric_text_cleaning)."""
    per_column = (details.get("numeric_text_cleaning") or {}).get("per_column", {})
    currency = sum(v.get("changed", 0) for v in per_column.values() if v.get("type") == "currency")
    percentage = sum(v.get("changed", 0) for v in per_column.values() if v.get("type") == "percentage")
    return currency, percentage


def _columns_affected(details: dict) -> list[str]:
    """Every column touched by any rule this run, deduplicated, read from the same per-column detail dicts the frontend already
    renders, so this can never disagree with what's shown elsewhere."""
    cols: set[str] = set()
    for rule_detail in details.values():
        if not isinstance(rule_detail, dict):
            continue
        per_column = rule_detail.get("per_column")
        if per_column:
            cols.update(per_column.keys())
        renamed = rule_detail.get("renamed")
        if renamed:
            cols.update(renamed.values())
    return sorted(cols)


# Findings that represent something Omixa found in the (post-clean)
# file but deliberately did NOT touch, the "invalid values" spec
# item 13/14 refers to, as opposed to purely advisory notes like
# outliers or a constant column that aren't "invalid" so much as
# "worth a look".
_INVALID_VALUE_ISSUES = {
    "invalid_email_format",
    "ambiguous_date_format",
    "suspicious_phone_format",
    "unrecoverable_scientific_notation",
    "impossible_age",
    "impossible_date",
    "unrecognized_gender_value",
    "unrecognized_country_value",
    "mixed_data_types",
    "inconsistent_categories",
}


def _invalid_values_detected(quality_after: dict) -> int:
    return sum(1 for f in quality_after.get("findings", []) if f.get("issue") in _INVALID_VALUE_ISSUES)


def _resolution_totals(resolution_log: dict) -> dict:
    """Resolutions (cleaning/resolutions.py) are a human resolving an
    ambiguity cleaning/rules.py deliberately left alone, the fix
    still belongs in the same summary buckets a rule's own count
    would land in, just credited to the issue the user resolved
    rather than to a rule name."""
    totals = {"dates_standardized": 0, "phone_numbers_normalized": 0, "missing_values_filled": 0}
    issue_to_key = {
        "ambiguous_date_format": "dates_standardized",
        "suspicious_phone_format": "phone_numbers_normalized",
    }
    for item in resolution_log.get("applied", []):
        key = issue_to_key.get(item.get("issue"))
        if key:
            totals[key] += item.get("changed", 0)
        elif item.get("issue") == "high_missingness" and item.get("choice") == "fill_anyway":
            totals["missing_values_filled"] += item.get("changed", 0)
    return totals


def build_cleaning_summary(
    rows_in: int,
    rows_out: int,
    column_count: int,
    change_log: dict,
    quality_before: dict,
    quality_after: dict,
    resolution_log: dict | None = None,
) -> dict:
    """
    Returns:
        {
          "counts": { ... every figure the spec's summary lists ... },
          "columns_affected": ["age", "country", "gender", ...],
          "quality_score_before": 62,
          "quality_score_after": 91,
          "quality_grade_before": "D",
          "quality_grade_after": "A",
          "text": "DATA CLEANING SUMMARY\\n\\nRows processed: 22\\n..."
        }
    """
    changes = change_log.get("changes", {})
    details = change_log.get("details", {})
    currency_changed, percentage_changed = _numeric_type_totals(details)
    duplicates_detail = details.get("duplicates", {})
    resolution_totals = _resolution_totals(resolution_log or {})

    counts = {
        "rows_processed": rows_in,
        "rows_output": rows_out,
        "columns_processed": column_count,
        "missing_values_standardized": changes.get("missing_token_normalization_changed", 0),
        "missing_values_filled": changes.get("missing_values_changed", 0) + resolution_totals["missing_values_filled"],
        "column_names_standardized": changes.get("column_names_changed", 0),
        "gender_values_normalized": changes.get("gender_standardization_changed", 0),
        "country_values_normalized": changes.get("country_standardization_changed", 0),
        "boolean_values_normalized": changes.get("boolean_standardization_changed", 0),
        "dates_standardized": changes.get("date_standardization_changed", 0) + resolution_totals["dates_standardized"],
        "phone_numbers_normalized": changes.get("phone_cleaning_changed", 0) + resolution_totals["phone_numbers_normalized"],
        "currency_formatting_cleaned": currency_changed,
        "percentage_values_normalized": percentage_changed,
        "duplicate_rows_detected": duplicates_detail.get("duplicate_rows_found", 0),
        "duplicate_rows_removed": duplicates_detail.get("removed", 0),
        "invalid_values_detected": _invalid_values_detected(quality_after),
    }

    score_before = quality_before.get("score")
    score_after = quality_after.get("score")
    grade_before = quality_before.get("grade")
    grade_after = quality_after.get("grade")

    lines = [
        "DATA CLEANING SUMMARY",
        "",
        f"Rows processed: {rows_in}",
        f"Columns processed: {column_count}",
        f"Missing values standardized: {counts['missing_values_standardized']}",
        f"Column names standardized: {counts['column_names_standardized']}",
        f"Gender values normalized: {counts['gender_values_normalized']}",
        f"Country values normalized: {counts['country_values_normalized']}",
        f"Boolean values normalized: {counts['boolean_values_normalized']}",
        f"Dates standardized: {counts['dates_standardized']}",
        f"Phone numbers normalized: {counts['phone_numbers_normalized']}",
        f"Currency formatting cleaned: {counts['currency_formatting_cleaned']}",
        f"Percentage values normalized: {counts['percentage_values_normalized']}",
        f"Duplicate rows detected: {counts['duplicate_rows_detected']}",
        f"Invalid values detected: {counts['invalid_values_detected']}",
    ]
    if score_before is not None and score_after is not None:
        delta = score_after - score_before
        sign = "+" if delta >= 0 else ""
        before_label = f"{score_before}% ({grade_before})" if grade_before else f"{score_before}%"
        after_label = f"{score_after}% ({grade_after})" if grade_after else f"{score_after}%"
        lines += ["", f"Quality score: {before_label} -> {after_label} ({sign}{delta})"]

    return {
        "counts": counts,
        "columns_affected": _columns_affected(details),
        "quality_score_before": score_before,
        "quality_score_after": score_after,
        "quality_grade_before": grade_before,
        "quality_grade_after": grade_after,
        "text": "\n".join(lines),
    }
