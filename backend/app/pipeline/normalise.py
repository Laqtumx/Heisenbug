"""Text and date normalisation shared by the rules and the cleaner.

The rules detect a problem and the cleaner fixes it. If each side had its own
idea of what "correct" looks like they would drift, and the cleaner would either
fail to fix a finding or "fix" a value the rule never flagged. Both sides import
the helpers here, so there is one definition of each normal form.

Every function is pure and works on whole Series so the cleaner stays vectorised.
"""

from __future__ import annotations

import re

import pandas as pd

_WHITESPACE_RE = re.compile(r"\s{2,}")

# Tried in order. Mixed is last because it accepts anything pandas can infer and
# would otherwise mask a wrong-but-parseable reading such as 03/04/2024, which is
# ambiguous between day-first and month-first.
DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%b-%Y",
    "%d.%m.%Y",
    "%Y%m%d",
    "%m/%d/%Y",
)

ISO_FORMAT = "%Y-%m-%d"


def collapse_whitespace(values: pd.Series) -> pd.Series:
    """Trim the ends and reduce internal runs of whitespace to one space."""
    return (
        values.astype(str)
        .str.replace(_WHITESPACE_RE, " ", regex=True)
        .str.strip()
    )


def title_case_tolerant(values: pd.Series) -> pd.Series:
    """Title case a column without destroying acronyms.

    ``str.title()`` turns "ACME INDUSTRIES LLP" into "Acme Industries Llp". A
    token that is already all upper case and longer than one character is left
    alone, so "LLP", "GST" and "IN" survive while "acme industries" and
    "ACME industries" both become "Acme Industries".
    """
    collapsed = collapse_whitespace(values)
    titled = collapsed.str.title()
    if not len(collapsed):
        return titled

    def keep(token: str) -> str:
        # A single letter is more likely a stray capital than an acronym.
        if len(token) > 1 and token.isupper():
            return token
        return token.title()

    # Only rows that would actually change need the slower token pass.
    differs = (collapsed != titled) & (collapsed.str.len() > 0)
    if not differs.any():
        return titled
    repaired = collapsed[differs].map(
        lambda text: " ".join(keep(token) for token in text.split())
    )
    return titled.mask(differs, repaired)


def is_title_case_ok(values: pd.Series) -> pd.Series:
    """True where the value's casing is already the normal form.

    Casing only: whitespace is a separate concern that the whitespace branch of
    the same rule already covers. Comparing the raw value here would make every
    padded value look like a casing problem too, and the finding message would
    blame the wrong thing.
    """
    return collapse_whitespace(values) == title_case_tolerant(values)


def parse_dates(values: pd.Series) -> pd.Series:
    """Parse a column of assorted date strings to datetime64.

    Each known format is tried in turn and the results merged, so a column that
    mixes "2024-01-31" with "31/01/2024" parses in full. Values no format can
    read become NaT rather than raising.
    """
    text = values.astype(str).str.strip()
    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    for fmt in DATE_FORMATS:
        remaining = result.isna() & text.notna()
        if not remaining.any():
            break
        try:
            parsed = pd.to_datetime(text.where(remaining), format=fmt, errors="coerce")
        except (ValueError, TypeError):
            continue
        result = result.mask(result.isna() & parsed.notna(), parsed)
    return result


def to_iso_dates(values: pd.Series) -> pd.Series:
    """Render a column of assorted date strings as ISO 8601.

    A value that cannot be parsed is returned unchanged. Silently blanking it
    would destroy data the reviewer never saw; an unparseable date is a finding
    in its own right, not something the cleaner should paper over.
    """
    text = values.astype(str).str.strip()
    parsed = parse_dates(text)
    iso = parsed.dt.strftime(ISO_FORMAT)
    return iso.where(parsed.notna(), text)


def normalise_key(value: str) -> str:
    """Case- and whitespace-insensitive form of a business key."""
    return collapse_whitespace(pd.Series([value])).iloc[0].upper()
