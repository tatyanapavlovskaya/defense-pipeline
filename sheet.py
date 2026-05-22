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
    "Org Nr", "Company Name", "Source", "Address", "SNI Code", "Website",
    "Employees", "Revenue (kSEK)",
]
NEWS_HEADERS = [
    "Date", "Company Name", "Org Nr", "News Source", "Headline",
    "Article URL", "Trigger Keywords Matched", "Pitch Points", "Draft Email",
    "Contact Name", "Contact Title", "Contact Email", "Contact Source",
    "Status",
]
KEYWORD_HEADERS = ["Company Names", "Geo & Commercial (SV)", "Geo & Commercial (EN)"]

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

        # Write headers if row 1 doesn't already match exactly.
        # Clear the full row first so stale extra columns don't linger.
        first_row = ws.row_values(1)
        if first_row != headers:
            ws.delete_rows(1)
            ws.insert_rows([headers], row=1)
            _format_header_row(ws)
            print(f"  Headers updated: {title}")

        tabs[title] = ws

    return tabs


def _format_header_row(ws: gspread.Worksheet):
    ws.format("1:1", {
        "backgroundColor": {"red": 1, "green": 1, "blue": 1},
        "textFormat": {
            "bold": True,
            "foregroundColor": {"red": 0, "green": 0, "blue": 0},
        },
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


def get_last_known_contact(news_ws: gspread.Worksheet, company_name: str) -> dict:
    """
    Search the News & Drafts tab for the most recent row for this company
    that has a contact filled in. Returns a dict with name/title/email/source,
    or empty strings if none found.
    """
    rows = news_ws.get_all_values()
    if len(rows) < 2:
        return {"name": "", "title": "", "email": "", "source": ""}
    # NEWS_HEADERS indices: Contact Name=9, Title=10, Email=11, Source=12
    target = company_name.strip().lower()
    match = {"name": "", "title": "", "email": "", "source": ""}
    for row in rows[1:]:
        if len(row) < 13:
            continue
        if row[1].strip().lower() == target and any(row[9:13]):
            match = {
                "name":   row[9].strip(),
                "title":  row[10].strip(),
                "email":  row[11].strip(),
                "source": row[12].strip(),
            }
    return match


def sync_company_keywords(companies_ws: gspread.Worksheet,
                          keywords_ws: gspread.Worksheet) -> int:
    """
    Sync company names from the Companies tab into column A of the Keywords tab.
    Only appends names not already present — never removes or overwrites.
    Returns the number of new names added.
    """
    # All company names from Companies tab (col B)
    all_company_names = [
        v.strip() for v in companies_ws.col_values(2)[1:] if v.strip()
    ]
    # Already in Keywords tab col A
    existing = {
        v.strip().lower()
        for v in keywords_ws.col_values(1)[1:] if v.strip()
    }
    new_names = [n for n in all_company_names if n.strip().lower() not in existing]
    if not new_names:
        return 0

    # Find the first empty cell in col A after existing data
    col_a = keywords_ws.col_values(1)  # includes header
    next_row = len(col_a) + 1

    keywords_ws.update(
        values=[[n] for n in new_names],
        range_name=f"A{next_row}:A{next_row + len(new_names) - 1}",
    )
    return len(new_names)


def read_keywords(keywords_ws: gspread.Worksheet) -> dict:
    """
    Read all three keyword columns from the Keywords tab.
    Returns:
        {
          "company_names": [...],   # Filter 1
          "geo_commercial_sv": [...],  # Filter 2 — Swedish
          "geo_commercial_en": [...],  # Filter 2 — English
        }
    All values are stripped and lowercased for matching.
    """
    rows = keywords_ws.get_all_values()
    data = rows[1:]  # skip header

    def col(i):
        return [r[i].strip().lower() for r in data
                if len(r) > i and r[i].strip()]

    return {
        "company_names":     col(0),
        "geo_commercial_sv": col(1),
        "geo_commercial_en": col(2),
    }


def append_news_rows(news_ws: gspread.Worksheet, rows: list[list]) -> int:
    """Append rows to the News & Drafts tab. Returns count added."""
    if not rows:
        return 0
    news_ws.append_rows(rows, value_input_option="USER_ENTERED")
    return len(rows)


def get_existing_article_urls(news_ws: gspread.Worksheet) -> set[str]:
    """Return URLs already recorded in the News & Drafts tab (column F)."""
    values = news_ws.col_values(6)  # column F = Article URL
    return {v.strip() for v in values[1:] if v.strip()}


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
