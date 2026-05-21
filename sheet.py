"""
Google Sheets integration.
Creates the spreadsheet (if no SHEET_ID in .env) and ensures the 3 tabs
exist with the correct headers.
"""

import os
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
SPREADSHEET_NAME = "Civitta Defense Outreach"

COMPANY_HEADERS = [
    "Org Nr", "Company Name", "Source", "Address", "SNI Code",
    "Website", "Contact Name", "Contact Email", "Contact Found Date", "Notes",
]
NEWS_HEADERS = [
    "Date", "Company Name", "Org Nr", "News Source", "Headline",
    "Article URL", "Trigger Keywords Matched", "Pitch Points",
    "Draft Email", "Status", "Reviewed By", "Review Date",
]
KEYWORD_HEADERS = ["Company Names", "Geo & Commercial"]

TAB_CONFIGS = [
    ("Companies",     COMPANY_HEADERS),
    ("News & Drafts", NEWS_HEADERS),
    ("Keywords",      KEYWORD_HEADERS),
]


def get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


def open_or_create_spreadsheet(gc: gspread.Client) -> gspread.Spreadsheet:
    if not SHEET_ID:
        raise ValueError(
            "GOOGLE_SHEET_ID is not set in .env.\n"
            "Create a blank Google Sheet, share it with "
            "vinnova-scraper@vernal-day-460412-q2.iam.gserviceaccount.com (Editor), "
            "then paste the Sheet ID into .env as GOOGLE_SHEET_ID=..."
        )
    print(f"Opening spreadsheet: {SHEET_ID}")
    return gc.open_by_key(SHEET_ID)


def ensure_tabs(sh: gspread.Spreadsheet) -> dict[str, gspread.Worksheet]:
    existing = {ws.title: ws for ws in sh.worksheets()}
    tabs = {}

    for i, (title, headers) in enumerate(TAB_CONFIGS):
        if title in existing:
            ws = existing[title]
        else:
            if i == 0 and len(existing) == 1 and "Sheet1" in existing:
                ws = existing["Sheet1"]
                ws.update_title(title)
            else:
                ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers))
            print(f"  Created tab: {title}")

        # Write headers if row 1 is empty
        first_row = ws.row_values(1)
        if not first_row:
            ws.append_row(headers, value_input_option="RAW")
            _format_header_row(ws)
            print(f"  Headers written: {title}")

        tabs[title] = ws

    return tabs


def _format_header_row(ws: gspread.Worksheet):
    ws.format("1:1", {
        "textFormat": {"bold": True},
        "backgroundColor": {"red": 0.18, "green": 0.33, "blue": 0.59},
        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
    })


def get_tabs() -> dict[str, gspread.Worksheet]:
    """Main entry point: return all 3 tabs, creating the sheet if needed."""
    gc = get_client()
    sh = open_or_create_spreadsheet(gc)
    return ensure_tabs(sh)


def get_existing_company_names(companies_ws: gspread.Worksheet) -> set[str]:
    """Return the set of company names already in the Companies tab (column B)."""
    values = companies_ws.col_values(2)  # column B = Company Name
    return {v.strip().lower() for v in values[1:] if v.strip()}  # skip header


def append_companies(companies_ws: gspread.Worksheet, rows: list[list]) -> int:
    """Append company rows to the Companies tab. Returns count added."""
    if not rows:
        return 0
    companies_ws.append_rows(rows, value_input_option="USER_ENTERED")
    return len(rows)


if __name__ == "__main__":
    print("Setting up Google Sheet...")
    tabs = get_tabs()
    print(f"\nAll tabs ready: {list(tabs.keys())}")
    sh_url = tabs["Companies"].spreadsheet.url
    print(f"Spreadsheet URL: {sh_url}")
