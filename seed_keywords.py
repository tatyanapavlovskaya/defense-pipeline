"""
One-time: seed the Keywords tab with default values and update its header
to the new 3-column structure.
"""
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sheet import get_tabs, KEYWORD_HEADERS, _format_header_row

# ── Default keyword lists ─────────────────────────────────────────────────────

# Column A: Company names to watch for in articles.
# These come from the Companies tab — a curated subset of the most newsworthy.
# Edit freely in the sheet; this is just a starting point.
COMPANY_NAMES = [
    "Saab", "BAE Systems", "Bofors", "FMV", "Försvarsmakten",
    "FLIR", "Thales", "Airbus", "Boeing", "Rheinmetall",
    "Kongsberg", "Patria", "Elbit", "Hanwha", "Leonardo",
    "Aimpoint", "Advenica", "Combitech", "Knowit", "CAG",
    "Verkan", "Arctest", "Aquilon", "Actea", "SSC Space",
    "Westermo", "Soya Group", "Granit Teknikbyrå", "SNG",
]

# Column B: Swedish geo & commercial triggers
GEO_COMMERCIAL_SV = [
    # Geografi
    "Polen", "Ukraina", "NATO", "Baltikum", "Estland", "Lettland",
    "Litauen", "Finland", "Norden", "Europa", "Östeuropa",
    # Kommersiella triggers
    "förvärv", "upphandling", "kontrakt", "ramavtal", "investering",
    "expansion", "export", "samarbete", "partnerskap", "fusion",
    "joint venture", "anbud", "order", "avtal", "leverans",
    "nytt kontrakt", "ny order", "strategiskt samarbete",
]

# Column C: English geo & commercial triggers
GEO_COMMERCIAL_EN = [
    # Geography
    "Poland", "Ukraine", "NATO", "Baltic", "Estonia", "Latvia",
    "Lithuania", "Finland", "Nordic", "Europe", "Eastern Europe",
    # Commercial triggers
    "acquisition", "procurement", "contract", "framework agreement",
    "investment", "expansion", "export", "partnership", "merger",
    "joint venture", "tender", "order", "agreement", "delivery",
    "new contract", "new order", "strategic partnership",
]

# ── Apply to sheet ─────────────────────────────────────────────────────────────
tabs = get_tabs()
kw_ws = tabs["Keywords"]

print(f"Current Keywords header: {kw_ws.row_values(1)}")

# Pad all columns to the same length
max_len = max(len(COMPANY_NAMES), len(GEO_COMMERCIAL_SV), len(GEO_COMMERCIAL_EN))
cn  = COMPANY_NAMES    + [""] * (max_len - len(COMPANY_NAMES))
sv  = GEO_COMMERCIAL_SV + [""] * (max_len - len(GEO_COMMERCIAL_SV))
en  = GEO_COMMERCIAL_EN + [""] * (max_len - len(GEO_COMMERCIAL_EN))

rows = [[c, s, e] for c, s, e in zip(cn, sv, en)]

# Clear and rewrite
kw_ws.clear()
kw_ws.append_row(KEYWORD_HEADERS, value_input_option="RAW")
_format_header_row(kw_ws)
kw_ws.append_rows(rows, value_input_option="USER_ENTERED")

print(f"Keywords tab seeded: {len(COMPANY_NAMES)} companies, "
      f"{len(GEO_COMMERCIAL_SV)} SV terms, {len(GEO_COMMERCIAL_EN)} EN terms")
print(f"Header: {kw_ws.row_values(1)}")
