"""Source file readers.

Accepts the shapes a legacy ERP export actually arrives in: CSV, TSV, Excel,
JSON and raw SQL dumps. Everything comes back as a DataFrame with every column
read as text, because type inference is the profiler's job and must not be done
implicitly by the reader. Reading as text is also what stops pandas from
silently turning ``007`` into ``7`` or a date into a timestamp.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import pandas as pd

logger = logging.getLogger(__name__)


class UnsupportedFormatError(ValueError):
    """Raised when a file extension has no reader."""


@dataclass
class ReadResult:
    frame: pd.DataFrame
    source_format: str
    encoding: str = "utf-8"
    delimiter: str | None = None
    warnings: list[str] = field(default_factory=list)


_ENCODINGS = ("utf-8-sig", "utf-8", "latin-1", "cp1252")


def _read_text_with_fallback(data: bytes) -> tuple[str, str]:
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def _sniff_delimiter(sample: str) -> str:
    for candidate in (",", ";", "\t", "|"):
        if sample.count(candidate) > 0:
            return candidate
    return ","


def read_csv_bytes(data: bytes, *, delimiter: str | None = None) -> ReadResult:
    text, encoding = _read_text_with_fallback(data)
    sample = "\n".join(text.splitlines()[:20])
    sep = delimiter or _sniff_delimiter(sample)
    frame = pd.read_csv(
        io.StringIO(text),
        sep=sep,
        dtype=str,
        keep_default_na=False,
        na_filter=False,
        skipinitialspace=True,
        engine="python",
    )
    frame.columns = [str(c).strip() for c in frame.columns]
    return ReadResult(frame=frame, source_format="csv", encoding=encoding, delimiter=sep)


def read_excel_bytes(data: bytes) -> ReadResult:
    frame = pd.read_excel(io.BytesIO(data), dtype=str, keep_default_na=False, engine="openpyxl")
    frame.columns = [str(c).strip() for c in frame.columns]
    return ReadResult(frame=frame, source_format="xlsx", encoding="binary")


def read_json_bytes(data: bytes) -> ReadResult:
    text, encoding = _read_text_with_fallback(data)
    buffer = io.StringIO(text)
    try:
        frame = pd.read_json(buffer, dtype=str)
    except ValueError:
        # A JSON array of objects where keys differ per row: let pandas widen it.
        frame = pd.json_normalize(pd.read_json(io.StringIO(text)))
    frame = frame.fillna("").astype(str)
    frame.columns = [str(c).strip() for c in frame.columns]
    return ReadResult(frame=frame, source_format="json", encoding=encoding)


_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+(?P<table>[\w.\"`\[\]]+)\s*\((?P<cols>[^)]+)\)\s*VALUES\s*(?P<values>.+?);",
    re.IGNORECASE | re.DOTALL,
)
_VALUES_ROW_RE = re.compile(r"\((?P<row>(?:[^()']|'(?:[^']|'')*')*)\)", re.DOTALL)


def _split_sql_values(values_blob: str) -> list[list[str]]:
    """Parse the tuple list of a multi-row INSERT into rows of strings."""
    rows: list[list[str]] = []
    for match in _VALUES_ROW_RE.finditer(values_blob):
        raw = match.group("row")
        cells: list[str] = []
        current: list[str] = []
        in_quote = False
        i = 0
        while i < len(raw):
            char = raw[i]
            if char == "'":
                if in_quote and i + 1 < len(raw) and raw[i + 1] == "'":
                    current.append("'")
                    i += 2
                    continue
                in_quote = not in_quote
            elif char == "," and not in_quote:
                cells.append("".join(current).strip())
                current = []
                i += 1
                continue
            else:
                current.append(char)
            i += 1
        cells.append("".join(current).strip())
        rows.append([c.strip("'") if c.startswith("'") else c for c in cells])
    return rows


def read_sql_bytes(data: bytes) -> ReadResult:
    """Extract rows from INSERT statements in a SQL dump.

    Reads the first INSERT target table found and builds a frame from it. A dump
    containing several tables is expected to be uploaded once per entity, which
    matches how the wizard asks for files.
    """
    text, encoding = _read_text_with_fallback(data)
    warnings: list[str] = []

    match = _INSERT_RE.search(text)
    if not match:
        # Some dumps are pipe-delimited COPY blocks.
        copy_match = re.search(
            r"COPY\s+(?P<table>[\w.\"]+)\s*\((?P<cols>[^)]+)\)\s+FROM\s+stdin;(?P<body>.*?)\\\.\s*$",
            text,
            re.IGNORECASE | re.DOTALL | re.MULTILINE,
        )
        if not copy_match:
            raise UnsupportedFormatError(
                "No INSERT or COPY statement found in the SQL dump."
            )
        columns = [c.strip().strip('"') for c in copy_match.group("cols").split(",")]
        body = copy_match.group("body").strip()
        rows = [line.split("\t") for line in body.splitlines() if line.strip()]
        frame = pd.DataFrame(rows, columns=columns, dtype=str)
        frame = frame.replace({"\\N": ""})
        warnings.append(f"Parsed a COPY block with {len(frame)} rows.")
        return ReadResult(frame=frame, source_format="sql", encoding=encoding, warnings=warnings)

    columns = [c.strip().strip('"').strip("`").strip("[]") for c in match.group("cols").split(",")]
    rows = _split_sql_values(match.group("values"))
    table = match.group("table").strip('"').strip("`").strip("[]")

    normalised: list[list[str]] = []
    for row in rows:
        if len(row) < len(columns):
            row = row + [""] * (len(columns) - len(row))
        elif len(row) > len(columns):
            warnings.append(
                f"Row with {len(row)} values truncated to {len(columns)} columns."
            )
            row = row[: len(columns)]
        normalised.append(["" if v.upper() == "NULL" else v for v in row])

    frame = pd.DataFrame(normalised, columns=columns, dtype=str)
    warnings.append(f"Parsed table {table!r} from INSERT statements.")
    return ReadResult(frame=frame, source_format="sql", encoding=encoding, warnings=warnings)


_READERS = {
    ".csv": read_csv_bytes,
    ".tsv": lambda data: read_csv_bytes(data, delimiter="\t"),
    ".txt": read_csv_bytes,
    ".xlsx": read_excel_bytes,
    ".xlsm": read_excel_bytes,
    ".xls": read_excel_bytes,
    ".json": read_json_bytes,
    ".sql": read_sql_bytes,
}


def read_file(path: Path) -> ReadResult:
    suffix = path.suffix.lower()
    if suffix not in _READERS:
        raise UnsupportedFormatError(
            f"Unsupported file type {suffix!r}. Supported: {', '.join(sorted(_READERS))}"
        )
    return _READERS[suffix](path.read_bytes())


def read_stream(filename: str, data: bytes) -> ReadResult:
    suffix = Path(filename).suffix.lower()
    if suffix not in _READERS:
        raise UnsupportedFormatError(
            f"Unsupported file type {suffix!r}. Supported: {', '.join(sorted(_READERS))}"
        )
    return _READERS[suffix](data)


def supported_extensions() -> list[str]:
    return sorted(_READERS)


def frame_from_records(records: list[dict], columns: list[str]) -> pd.DataFrame:
    """Rebuild a frame from Mongo documents, preserving column order."""
    if not records:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(records, dtype=str)
    for column in columns:
        if column not in frame.columns:
            frame[column] = ""
    return frame[columns].fillna("").astype(str)
