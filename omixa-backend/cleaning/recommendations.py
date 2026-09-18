"""
Recommendations

Turns the read-only quality report (cleaning/quality_report.py) into
something actionable: for each finding, decide whether an existing
cleaning rule can resolve it safely and unambiguously, or whether it
needs a human to look at it before anything happens to the data.

This module makes no decisions the rest of the codebase doesn't
already make, it only *labels* findings using the same rule
behavior that's already implemented in cleaning/rules.py. Nothing
here mutates a dataframe, and nothing here changes which rules exist
or what they do; it just groups them for the frontend so the user can
see, per-issue, "this will be auto-fixed" vs "this needs your call"
before they hit Clean my data.

    safe       -> the finding's issue is fully covered by a specific
                  cleaning rule (see ISSUE_TO_RULE), so running that
                  rule addresses exactly what was found.
    ambiguous  -> either the issue has no matching rule at all, or
                  the finding is "critical" severity (the rule
                  intentionally leaves critical cases untouched
                  rather than guess, e.g. a column that's >70%
                  missing). Surfaced for manual review, never
                  pre-selected for auto-processing.
"""

from __future__ import annotations

from cleaning.resolutions import RESOLUTION_OPTIONS

# Maps a quality_report "issue" key to the single cleaning rule (see
# cleaning/rules.RULE_DISPATCH) that resolves it. Only issues where
# the rule's actual behavior fully covers what the finding describes
# are listed here, everything else is left out on purpose, which is
# what puts it in the "ambiguous" bucket below rather than the "safe"
# one. High-missingness, outliers, constant columns, invalid emails,
# suspicious phone numbers, mixed types, ambiguous dates, and
# possible-duplicate-records are all deliberately excluded: the
# corresponding rule (or no rule at all) already declines to touch
# these, per each rule's own docstring in cleaning/rules.py.
ISSUE_TO_RULE = {
    "missing_values": "missing_values",
    "duplicate_rows": "duplicates",
    "whitespace": "formatting",
    "inconsistent_categories": "categorical_standardization",
}

# Short human-readable reason shown next to a recommended rule.
# Keyed by RULE name (the ISSUE_TO_RULE value), since one rule can
# resolve findings from more than one issue key in principle.
_SAFE_REASON = {
    "missing_values": "Fills gaps automatically (median for numbers, \"Unknown\" for text).",
    "duplicates": "Removes exact duplicate rows, keeping the first occurrence.",
    "formatting": "Trims stray/extra whitespace.",
    "categorical_standardization": "Merges same-value spelling/casing variants automatically.",
}


def generate_recommendations(report: dict) -> dict:
    """
    Takes the dict returned by cleaning.quality_report.generate_report()
    and classifies its findings into "safe" (rule-backed, recommended)
    and "ambiguous" (no safe automated fix, needs manual review).

    Returns:
        {
          "safe": [
            {
              "rule": "duplicates",
              "reason": "Removes exact duplicate rows, keeping the first occurrence.",
              "issue_count": 1,
              "findings": [ <finding dict>, ... ]
            }, ...
          ],
          "ambiguous": [ <finding dict with severity/column/detail/suggestion>, ... ],
          "recommended_rules": ["duplicates", "formatting", ...]  # safe-issue rules found, in dataset order
        }
    """
    findings = report.get("findings", [])

    safe_by_rule: dict[str, list[dict]] = {}
    ambiguous: list[dict] = []

    for finding in findings:
        rule = ISSUE_TO_RULE.get(finding.get("issue"))
        # A rule mapping exists, but "critical" findings are always
        # left to a human even if a same-named rule exists elsewhere
        # in the pipeline (matches missing_values' own >70% carve-out).
        if rule and finding.get("severity") != "critical":
            safe_by_rule.setdefault(rule, []).append(finding)
        else:
            # Attach the available resolution choices (if any) right
            # onto the finding, so the frontend can render "day-first
            # / month-first"-style buttons without a second lookup.
            # Findings for issues with no entry in RESOLUTION_OPTIONS
            # get an empty list, informational only, nothing to pick.
            finding = {**finding, "resolution_options": RESOLUTION_OPTIONS.get(finding.get("issue"), [])}
            ambiguous.append(finding)

    safe = [
        {
            "rule": rule,
            "reason": _SAFE_REASON.get(rule, ""),
            "issue_count": len(matched_findings),
            "findings": matched_findings,
        }
        for rule, matched_findings in safe_by_rule.items()
    ]

    return {
        "safe": safe,
        "ambiguous": ambiguous,
        "recommended_rules": list(safe_by_rule.keys()),
    }
