"""
Shared, read-only heuristics.

Both cleaning/rules.py (auto-fix) and cleaning/quality_report.py
(detect-only diagnostics) need to answer the same questions, "is
this an email column?", "is this a boolean-looking column?", "is
this string actually a placeholder for missing data?", and they
need to answer them the *same way*, or the report would end up
promising a fix that the rules don't actually perform (or vice
versa). This module is the single source of truth for those
questions. Nothing in here mutates a dataframe.
"""

from __future__ import annotations
import re
from typing import Optional
import pandas as pd

from cleaning.phone_formats import COUNTRIES as _COUNTRIES, COUNTRY_ALIASES as _COUNTRY_ALIASES

# Country reference is built from cleaning/phone_formats.COUNTRIES.
# The same curated code/name list already used for phone-number
# resolution, rather than a second hardcoded list, so adding a
# country in one place (phone_formats.py) covers both features.
_COUNTRY_CODE_TO_NAME: dict[str, str] = {code: c["name"] for code, c in _COUNTRIES.items()}
_COUNTRY_NAME_TO_CANONICAL: dict[str, str] = {c["name"].lower(): c["name"] for c in _COUNTRIES.values()}

# Common placeholder strings people use for "no value" that pandas'
# own NA detection doesn't catch (those are usually only caught when
# the *whole* column is empty, not a single stray cell). Matched
# case-insensitively against the trimmed cell value. Deliberately
# does NOT include ambiguous words like "missing", that can be a
# legitimate category label (e.g. a survey answer), so it's left
# alone rather than guessed at.
#
# "unknown" IS included, unlike the original V1 rationale: the
# improvement spec explicitly lists "Unknown"/"unknown" as a
# placeholder Omixa must recognize as missing (spec section 2), so
# this is a deliberate, spec-driven change from V1's more cautious
# default. It's still a *safe* one, normalizing "Unknown" to a real
# missing value, then (if the column's missing % is low enough)
# refilling text columns with the literal string "Unknown" via
# handle_missing_values, round-trips a pure placeholder back to
# exactly what it was. The only actual behavior change is for
# columns where "unknown" was previously left as its own distinct
# category value; it's now folded into the single missing
# representation like every other placeholder.
MISSING_TOKENS = {
    "", "na", "n/a", "n.a.", "n\\a", "null", "none", "nan",
    "-", "--", "?", "#n/a", "nil", "unknown",
}

# Restricted to unambiguous words on purpose. "1"/"0" are excluded
# from the base set: plenty of real columns use 1/0 as numeric codes
# rather than booleans, and we'd rather leave those as numbers than
# guess. EXTENDED_* adds "1"/"0" back in, but only for columns whose
# NAME signals a boolean flag (is_active, status, ...), see
# is_boolean_flag_column below and its use in
# cleaning/rules.handle_boolean_standardization. A generically-named
# numeric-looking column never gets this treatment.
TRUE_WORDS = {"yes", "y", "true", "t"}
FALSE_WORDS = {"no", "n", "false", "f"}
BOOLEAN_WORDS = TRUE_WORDS | FALSE_WORDS

EXTENDED_TRUE_WORDS = TRUE_WORDS | {"1"}
EXTENDED_FALSE_WORDS = FALSE_WORDS | {"0"}
EXTENDED_BOOLEAN_WORDS = EXTENDED_TRUE_WORDS | EXTENDED_FALSE_WORDS

BOOLEAN_FLAG_NAME_KEYWORDS = (
    "is_active", "active", "status", "enabled", "is_verified",
    "verified", "subscribed", "confirmed", "is_",
)

# Fixed, unambiguous gender vocabulary. Deliberately does NOT include
# anything else ("other", "non-binary", "prefer not to say", ...).
    # Those are left completely untouched, never remapped or guessed at.
MALE_WORDS = {"male", "m"}
FEMALE_WORDS = {"female", "f"}
GENDER_WORDS = MALE_WORDS | FEMALE_WORDS

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Characters that are purely visual/formatting noise on an otherwise
# numeric value: currency symbols, thousands separators, percent
# signs, stray whitespace.
_NUMERIC_NOISE_RE = re.compile(r"[,\s$€£¥%]")
_PAREN_NEGATIVE_RE = re.compile(r"^\((.*)\)$")
_CURRENCY_SYMBOL_RE = re.compile(r"[$€£¥]")

_SCI_NOTATION_RE = re.compile(r"^-?\d+(\.\d+)?[eE][+-]?\d+$")
_PHONE_SAFE_CHARS_RE = re.compile(r"^[+()\-.\s\d]+$")


def is_missing_token(value: str) -> bool:
    return value.strip().lower() in MISSING_TOKENS


def is_email_column(name: str, series: pd.Series) -> bool:
    """Name hint first (cheap, explicit); falls back to checking
    whether most non-null values actually look like an email."""
    lname = name.lower()
    if "email" in lname or "e-mail" in lname or "e_mail" in lname:
        return True
    values = series.dropna().astype(str)
    if len(values) < 3:
        return False
    matches = values.str.match(EMAIL_RE)
    return bool(matches.mean() >= 0.8)


def is_phone_column(name: str) -> bool:
    lname = name.lower()
    return any(k in lname for k in (
        "phone", "mobile", "contact_no", "contact no", "contactnumber",
        "contact_number", "telephone", "tel_no", "whatsapp", "fax",
    )) or lname.strip() in ("tel", "cell")


def is_gender_column(name: str) -> bool:
    lname = name.lower().strip()
    return lname in ("gender", "sex") or "gender" in lname


def is_boolean_flag_column(name: str) -> bool:
    """Name-only signal that a column is meant to hold a yes/no-style
    flag (is_active, status, ...), used to decide whether "1"/"0" are
    safe to treat as True/False for THIS column. Never applied on its
    own, handle_boolean_standardization still requires every actual
    value in the column to be a recognized boolean word before
    converting anything."""
    lname = name.lower().strip()
    return any(k in lname for k in BOOLEAN_FLAG_NAME_KEYWORDS)


def is_age_column(name: str) -> bool:
    lname = name.lower().strip()
    return lname == "age" or lname.endswith("_age") or lname.startswith("age_")


# A birth date is the one date type where "in the future" is always
# impossible, regardless of dataset, unlike a generic "date" column
# (due_date, expiry_date, event_date, ...) where a future value is
# often completely legitimate. Kept deliberately narrower than
# is_probable_date_column's "date" substring match for that reason.
BIRTH_DATE_NAME_KEYWORDS = ("dob", "birth", "birthday")

# Earliest year treated as plausible for ANY date column. Chosen to
# be conservative (a genuine pre-1900 record is rare in the kind of
# spreadsheet Omixa targets, but not impossible), so this only
# catches values that are essentially always a data-entry slip
# (typo'd year, a "0002" from a broken date picker, etc.).
MIN_PLAUSIBLE_YEAR = 1900


def is_birth_date_column(name: str) -> bool:
    lname = name.lower().strip()
    return any(k in lname for k in BIRTH_DATE_NAME_KEYWORDS)


def is_country_column(name: str) -> bool:
    lname = name.lower().strip()
    return lname == "nation" or "country" in lname


# Column-name hints for values that are IDENTIFIERS rather than
# quantities, account numbers, IBANs, BVNs/NUBANs (Nigerian bank
# identifiers), postal codes, card numbers, etc. Nothing here should
# ever be converted to a numeric dtype: it's never added, averaged,
# or compared numerically, and doing so risks losing a leading zero
# (a real digit in a phone/account number, not padding) or having
# Excel silently render it in scientific notation on export.
ID_NAME_KEYWORDS = (
    "account", "acct", "iban", "swift", "bvn", "nuban", "sort_code",
    "zip", "zipcode", "postal", "postcode", "ssn", "passport",
    "national_id", "reg_no", "registration_no", "card_number",
    "pin_code", "routing_number", "vin", "imei",
)

_LEADING_ZERO_RE = re.compile(r"^0\d+$")


def is_identifier_name(name: str) -> bool:
    """Name-only half of the identifier check, usable before any
    data has been read (e.g. to decide read-time dtype), unlike
    is_identifier_column which also needs the values."""
    if is_phone_column(name):
        return True
    lname = name.lower()
    if any(k in lname for k in ID_NAME_KEYWORDS):
        return True
    return lname.strip() == "id" or lname.endswith("_id") or lname.endswith(" id")


def is_identifier_column(name: str, series: pd.Series) -> bool:
    """
    True for columns that should never be coerced to a numeric dtype,
    even if every value happens to parse as a number.

    Two ways a column earns this: its name matches a known
    identifier pattern (phone, account, IBAN, postal code, ...), or
    its values do. The value-based check catches identifier columns
    with generic names ("Number", "Ref", "ID") by looking for a
    leading zero on an otherwise all-digit value (e.g. "0803317157",
    "0042"), real quantities essentially never carry a meaningful
    leading zero, but phone numbers, account numbers, and reference
    codes very often do, and converting them to a numeric dtype
    would silently drop that leading digit.
    """
    if is_identifier_name(name):
        return True

    values = series.dropna().astype(str).str.strip()
    if values.empty:
        return False
    return bool(values.str.match(_LEADING_ZERO_RE).any())


def is_probable_date_column(name: str, series: pd.Series) -> bool:
    """Name hint, or a sample of values that mostly parse as dates
    and mostly contain date-shaped separators (so we don't flag a
    plain numeric ID column as a date just because pandas can
    technically parse '20230101' as one)."""
    lname = name.lower()
    if any(k in lname for k in ("date", "dob", "birthday")):
        return True

    values = series.dropna().astype(str)
    if len(values) < 3:
        return False
    shaped = values.str.match(r"^\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}$")
    if shaped.mean() < 0.8:
        return False
    parsed = pd.to_datetime(values, errors="coerce", format="mixed")
    return bool(parsed.notna().mean() >= 0.8)


def unambiguous_date_parse(values: pd.Series) -> Optional[pd.Series]:
    """
    Tries to parse a column of date-like strings safely, judging
    each value on its own rather than as an all-or-nothing block.

    For every non-null value, both day-first and month-first parsing
    are tried:
      - if only one convention produces a valid date (e.g. any day-
        of-month >12 makes month-first impossible), that value is
        unambiguous by elimination;
      - if both produce the SAME date (e.g. already-ISO values, or a
        day/month that happen to coincide), it's unambiguous;
      - if both produce valid but DIFFERENT dates (e.g. "03/04/2024"
        could be 3 April or March 4th), that single value is
        genuinely ambiguous;
      - if neither convention can parse it (a typo, a placeholder
        like "Unknown"), it's simply invalid, not ambiguous.

    A handful of invalid or missing values no longer block
    standardizing the rest of an otherwise-clean column, each row
    is judged independently, so 14 good dates and 1 typo now
    standardize the 14 and leave the typo exactly as it was (see
    handle_date_standardization), instead of the whole column being
    skipped.

    Returns None only when the column contains at least one
    genuinely ambiguous value (day-first vs month-first disagree, that calls for a human to confirm the intended convention, since
    it should apply consistently to every row in the column) or when
    nothing in the column could be parsed at all. Otherwise returns a
    Series aligned to `values`' index, with NaT for any value that
    couldn't be parsed under either convention.
    """
    non_null = values.dropna().astype(str)
    if non_null.empty:
        return None

    day_first = pd.to_datetime(non_null, errors="coerce", dayfirst=True, format="mixed")
    month_first = pd.to_datetime(non_null, errors="coerce", dayfirst=False, format="mixed")

    day_ok = day_first.notna()
    month_ok = month_first.notna()
    both_ok = day_ok & month_ok
    disagree = both_ok & (day_first != month_first)

    if disagree.any():
        return None  # genuine day-first vs month-first ambiguity somewhere in the column

    # Where only one convention parsed, use it; where both parsed,
    # they already agree (the disagreement case returned above), so
    # either works, day-first is the arbitrary tie-breaker.
    resolved = day_first.where(day_ok, month_first)

    if resolved.isna().all():
        return None  # nothing in the column could be parsed at all

    full = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    full.loc[non_null.index] = resolved.values
    return full


def strip_numeric_noise(value: str) -> str:
    s = _NUMERIC_NOISE_RE.sub("", value.strip())
    m = _PAREN_NEGATIVE_RE.match(s)
    if m:
        s = "-" + m.group(1)
    return s


def try_parse_float(cleaned: str) -> Optional[float]:
    if cleaned in ("", "-", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def standardize_country_value(value: str) -> Optional[str]:
    """
    Maps a country CODE ("NG"), a common alternate name/abbreviation
    ("USA", "UK", see phone_formats.COUNTRY_ALIASES), or a
    differently-cased/spaced country NAME ("nigeria", " Nigeria ") to
    the single canonical name ("Nigeria") from
    cleaning/phone_formats.COUNTRIES. Returns None (meaning: leave
    the original value untouched) for anything not found in either
    reference, no fuzzy matching, so a typo or an unlisted country
    is left alone rather than guessed at. Extending country coverage
    only ever requires adding an entry to phone_formats.COUNTRIES or
    phone_formats.COUNTRY_ALIASES; nothing here needs to change.
    """
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    code = v.upper()
    if code in _COUNTRY_CODE_TO_NAME:
        return _COUNTRY_CODE_TO_NAME[code]
    if code in _COUNTRY_ALIASES:
        return _COUNTRY_CODE_TO_NAME[_COUNTRY_ALIASES[code]]
    return _COUNTRY_NAME_TO_CANONICAL.get(v.lower())


def is_scientific_notation(value: object) -> bool:
    """True for strings shaped like a float in scientific notation
    (e.g. "1.343E+12"), the classic sign that a spreadsheet
    silently reformatted a long digit string (a phone number) into a
    number."""
    if not isinstance(value, str):
        return False
    return bool(_SCI_NOTATION_RE.match(value.strip()))


def try_recover_scientific_phone(value: str) -> Optional[str]:
    """
    Attempts to reconstruct the original all-digit string from a
    scientific-notation value, WITHOUT inventing any digit.

    A value like "1.343E+12" only carries as many significant digits
    as its mantissa shows ("1343"); expanding it to a 13-digit number
    would mean inventing 9 trailing zeros that were never actually
    there. This only returns a reconstructed value when the mantissa
    already carries enough digits after the decimal point to cover
    the exponent, i.e. the expansion is exact, not padded. That's
    rarely true for a real spreadsheet-mangled phone number (Excel
    usually keeps only a handful of significant digits), so this
    intentionally returns None, "flag it, don't guess", for the
    large majority of real cases; see cleaning/quality_report.py's
    unrecoverable_scientific_notation finding for what happens then.
    """
    if not is_scientific_notation(value):
        return None

    s = value.strip()
    sign = "-" if s.startswith("-") else ""
    s = s.lstrip("+-")
    mantissa, exponent_str = re.split(r"[eE]", s)
    exponent = int(exponent_str)
    if exponent < 0:
        return None  # a phone number can't have a fractional digit count

    if "." in mantissa:
        int_part, frac_part = mantissa.split(".", 1)
    else:
        int_part, frac_part = mantissa, ""

    if len(frac_part) < exponent:
        return None  # would require inventing trailing zeros -> unsafe, flag instead

    digits = (int_part + frac_part)[: len(int_part) + exponent]
    if not digits:
        return None
    return sign + digits


def normalize_phone_punctuation(value: str) -> Optional[str]:
    """
    Strips spaces, brackets, hyphens, and dots from a phone-like
    value while preserving every digit and a single leading '+' (if
    present), "+234 806-123-4567" -> "+2348061234567",
    "(234) 806 123 4567" -> "2348061234567". Returns None (leave
    untouched) if the value contains anything other than digits and
    that expected punctuation (e.g. an extension like "ext. 204", or
    free text), mixed content like that isn't safe to strip blindly,
    since punctuation might be meaningful there. Never adds, drops,
    reorders digits, or adds a country code that wasn't already
    present; the number stays a string throughout (never a numeric
    dtype). Country-aware reformatting (choosing which region a bare
    number belongs to) is a separate, user-confirmed step, see
    cleaning/phone_formats.normalize_phone.
    """
    s = value.strip()
    if not s or not _PHONE_SAFE_CHARS_RE.match(s):
        return None
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    cleaned = ("+" if plus else "") + digits
    return cleaned if cleaned != s else None
