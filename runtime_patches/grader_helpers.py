"""Small data-validation helpers shared by task graders.

This directory is on the grader subprocess PYTHONPATH, including when tasks are
copied into private episode directories.
"""
import json
import math
import re
import tarfile
from html.parser import HTMLParser
from pathlib import Path


def text(value):
    return str(value or "").strip().casefold()


def number(value):
    try:
        result = float(str(value).replace(",", "").replace("$", "").replace("%", ""))
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def close(actual, expected, tolerance=0.11):
    actual, expected = number(actual), number(expected)
    return actual is not None and expected is not None and abs(actual - expected) <= tolerance


def records(workbook, sheet_name, columns, check):
    sheet = next((s for s in workbook if text(s.title) == text(sheet_name)), None)
    check(f"{sheet_name} sheet exists", sheet is not None)
    if sheet is None:
        return []
    values = list(sheet.iter_rows(values_only=True))
    headers = [re.sub(r"[\s_-]+", "_", text(v)) for v in values[0]] if values else []
    missing = set(columns) - set(headers)
    check(f"{sheet_name} required columns", not missing, f"Missing: {sorted(missing)}")
    if missing:
        return []
    return [dict(zip(headers, row)) for row in values[1:] if any(v is not None for v in row)]


def rich_text(value):
    """Read persisted Notion input rich text as well as API response rich text."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return value
        return rich_text(parsed) if not isinstance(parsed, str) else parsed
    if isinstance(value, list):
        return "".join(rich_text(part) for part in value)
    if isinstance(value, dict):
        if "plain_text" in value:
            return value["plain_text"] or ""
        if "text" in value:
            return rich_text(value["text"])
        if "content" in value:
            return rich_text(value["content"])
        if "title" in value:
            return rich_text(value["title"])
    return ""


def fixture_table(task_dir):
    """Return rows from the benchmark table actually served to the agent."""
    class TableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows, self.row, self.cell = [], None, None

        def handle_starttag(self, tag, attrs):
            if tag == "tr":
                self.row = []
            elif tag in ("td", "th") and self.row is not None:
                self.cell = []

        def handle_data(self, data):
            if self.cell is not None:
                self.cell.append(data)

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self.cell is not None:
                self.row.append("".join(self.cell).strip())
                self.cell = None
            elif tag == "tr" and self.row is not None:
                if self.row:
                    self.rows.append(self.row)
                self.row = None

    with tarfile.open(Path(task_dir) / "files/mock_pages.tar.gz") as archive:
        member = next(m for m in archive.getmembers() if m.name.endswith("index.html"))
        parser = TableParser()
        parser.feed(archive.extractfile(member).read().decode())
    if len(parser.rows) < 2:
        raise ValueError("Benchmark fixture has no data table")
    return [dict(zip(parser.rows[0], row)) for row in parser.rows[1:]]


def google_sheet_records(cursor, spreadsheet_title, sheet_title, columns, check):
    """Validate the submitted Google Sheet itself, without requiring an XLSX copy."""
    cursor.execute("""
        SELECT s.spreadsheet_id, s.id FROM gsheet.sheets s
        JOIN gsheet.spreadsheets p ON p.id = s.spreadsheet_id
        WHERE lower(p.title) = lower(%s) AND lower(s.title) = lower(%s)
    """, (spreadsheet_title, sheet_title))
    matches = cursor.fetchall()
    check(f"{spreadsheet_title}/{sheet_title} exists", bool(matches))
    if not matches:
        return []
    cursor.execute("""
        SELECT row_index, col_index, COALESCE(formatted_value, value)
        FROM gsheet.cells WHERE spreadsheet_id = %s AND sheet_id = %s
        ORDER BY row_index, col_index
    """, matches[0])
    rows = {}
    for row, column, value in cursor.fetchall():
        if value not in (None, ""):
            rows.setdefault(row, {})[column] = value
    if not rows:
        check(f"{sheet_title} has data", False)
        return []
    header_row = min(rows)
    headers = {column: re.sub(r"[\s_-]+", "_", text(value))
               for column, value in rows.pop(header_row).items()}
    missing = set(columns) - set(headers.values())
    check(f"{sheet_title} required columns", not missing, f"Missing: {sorted(missing)}")
    if missing:
        return []
    return [{header: row.get(column) for column, header in headers.items()}
            for _, row in sorted(rows.items())]
