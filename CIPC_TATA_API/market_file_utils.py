import csv
import io
import re
from typing import Dict, List, Optional, Tuple

import pandas as pd


def column_letters_to_index(column_letters: str) -> int:
    """Convert Excel-style column letters to a zero-based column index."""
    index = 0
    for char in column_letters.upper():
        if not ("A" <= char <= "Z"):
            raise ValueError(f"Invalid column letter: {column_letters}")
        index = (index * 26) + (ord(char) - ord("A") + 1)
    return index - 1


def parse_single_column_range(cell_range: str) -> Tuple[int, int, int]:
    """Parse a range like F11:F106 into zero-based start/end rows and column."""
    match = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", cell_range.strip().upper())
    if not match:
        raise ValueError(f"Unsupported cell range: {cell_range}")

    start_col, start_row, end_col, end_row = match.groups()
    if start_col != end_col:
        raise ValueError(f"Only single-column ranges are supported: {cell_range}")

    start_row_idx = int(start_row) - 1
    end_row_exclusive = int(end_row)
    column_index = column_letters_to_index(start_col)
    return start_row_idx, end_row_exclusive, column_index


def coerce_float(value, *, absolute: bool = False) -> float:
    """Convert spreadsheet values to floats, falling back to 0.0 for non-numeric cells."""
    if pd.isna(value):
        return 0.0

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        return abs(result) if absolute else result

    text = str(value).strip()
    if not text:
        return 0.0

    text = text.replace(",", ".")
    try:
        result = float(text)
    except (TypeError, ValueError):
        return 0.0

    return abs(result) if absolute else result


def extract_values_from_dataframe(
    df: pd.DataFrame,
    cell_range: str,
    *,
    absolute: bool = False,
    expected_len: int = 96,
) -> List[float]:
    """Extract a fixed number of values from a single-column spreadsheet range."""
    start_row_idx, end_row_exclusive, column_index = parse_single_column_range(cell_range)

    values = []
    for row_idx in range(start_row_idx, min(end_row_exclusive, len(df))):
        value = df.iloc[row_idx, column_index] if column_index < len(df.columns) else None
        values.append(coerce_float(value, absolute=absolute))

    while len(values) < expected_len:
        values.append(0.0)

    return values[:expected_len]


def read_table_from_buffer(buffer, filename: str) -> pd.DataFrame:
    """Read CSV/Excel content into a headerless dataframe."""
    buffer.seek(0)
    lower_name = filename.lower()

    if lower_name.endswith(".csv"):
        raw_content = buffer.read()
        if isinstance(raw_content, bytes):
            text = raw_content.decode("utf-8-sig", errors="replace")
        else:
            text = raw_content

        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            return pd.DataFrame()

        max_columns = max(len(row) for row in rows)
        normalized_rows = [row + [None] * (max_columns - len(row)) for row in rows]
        return pd.DataFrame(normalized_rows)

    engine = "openpyxl" if lower_name.endswith(".xlsx") else None
    return pd.read_excel(buffer, header=None, engine=engine)


def extract_injection_percentages(df: pd.DataFrame) -> List[float]:
    """Return all injection percentages found in the workbook, preferring column B."""
    pattern = re.compile(r"Injection:\s*([\d.]+)%", re.IGNORECASE)

    def collect(values) -> List[float]:
        percentages = []
        for value in values:
            if pd.isna(value):
                continue
            text = str(value)
            for match in pattern.findall(text):
                try:
                    percentages.append(float(match))
                except ValueError:
                    continue
        return percentages

    if df.shape[1] > 1:
        column_matches = collect(df.iloc[:, 1].tolist())
        if column_matches:
            return column_matches

    return collect(df.to_numpy().flatten().tolist())


def extract_loss_percentages(df: pd.DataFrame) -> Dict[str, float]:
    """
    Extract named GDAM loss percentages from the workbook.

    The acceptance files can include separate rows for regional, state, and area
    losses. We inspect each row so the percentage can live in a different
    column than the label and prefer area loss over regional loss when both are
    present.
    """
    injection_pattern = re.compile(r"Injection:\s*([\d.]+)%", re.IGNORECASE)
    generic_percent_pattern = re.compile(r"([\d.]+)\s*%")

    def parse_percentage(text: str) -> Optional[float]:
        match = injection_pattern.search(text) or generic_percent_pattern.search(text)
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    losses: Dict[str, float] = {}
    for _, row in df.iterrows():
        row_parts = []
        for value in row.tolist():
            if pd.isna(value):
                continue
            text = str(value).strip()
            if text:
                row_parts.append(text)

        if not row_parts:
            continue

        row_text = " ".join(row_parts)
        lowered_row_text = row_text.lower()
        if "loss" not in lowered_row_text:
            continue

        percentage = parse_percentage(row_text)
        if percentage is None:
            continue

        if "state" in lowered_row_text:
            losses["state_loss"] = percentage
        elif "area" in lowered_row_text:
            losses["area_loss"] = percentage
        elif "regional" in lowered_row_text:
            losses.setdefault("regional_loss", percentage)

    if "area_loss" not in losses and "regional_loss" in losses:
        losses["area_loss"] = losses["regional_loss"]

    return losses


def matches_gdam_filename(filename: str, target_date) -> bool:
    """Check whether a GDAM/IEX acceptance filename matches the target date."""
    upper_name = filename.upper()
    date_str = target_date.strftime("%y%m%d")
    return f"IEX{date_str}SCH" in upper_name and upper_name.endswith((".XLS", ".XLSX"))
