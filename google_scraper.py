"""Google scraper: businesses from Google Maps for a query + area, written to a lead CSV.

M6: one search cell (query + one city/ZIP) -> unique places -> filters -> CSV rows.
M7 adds website/paid enrichment, dry-run, resume and the run summary; M8 adds state -> cities -> ZIP coverage.
"""
import argparse
import re
from datetime import datetime

from common import ENDPOINTS, GOOGLE_COLUMNS, BudgetReached, LeadWriter, OutOfBalance, Treg, domain_of, normalize_phone

ROWS_CAP = ENDPOINTS["maps_search"]["rows_cap"]  # one search never returns more (M1): a full cell is "saturated"
# "..., Austin, TX 78704" or "..., Austin, TX 78704, United States"
US_ADDRESS_RE = re.compile(r",\s*([^,]+?),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?(?:,\s*(?:USA|United States))?\s*$")


def search_cell(treg, query, cell):
    """One Maps search: returns (rows, saturated). Saturated = the provider's cap was hit, more places likely exist."""
    rows = treg.call("maps_search", {"query": query, "location": cell, "limit": ROWS_CAP}).get("rows") or []
    return rows, len(rows) >= ROWS_CAP


def place_key(raw):
    """Dedupe key: Google place_id, else normalized phone + domain."""
    return raw.get("place_id") or f"nopid:{normalize_phone(raw.get('phone'))}|{domain_of(raw.get('website'))}"


def map_place(raw, query, cell):
    """Provider row -> Google CSV columns. A vendor change only touches this function and endpoints.json."""
    addr = raw.get("address") or ""
    m = US_ADDRESS_RE.search(addr)
    phone = normalize_phone(raw.get("phone"))
    return {
        "company_name": raw.get("name"), "website": raw.get("website"), "domain": domain_of(raw.get("website")),
        "phone": phone, "all_phones": [phone], "address": addr,
        "city": m.group(1) if m else "", "state": m.group(2) if m else "", "postal_code": m.group(3) if m else "",
        "country": "US" if m else "", "source_url": raw.get("url"), "google_maps_url": raw.get("url"),
        "place_id": place_key(raw), "category": raw.get("category"), "all_categories": raw.get("categories"),
        "latitude": raw.get("latitude"), "longitude": raw.get("longitude"), "rating": raw.get("rating"),
        "review_count": raw.get("review_count"),
        "opening_hours": [f"{day}: {hours}" for day, hours in (raw.get("hours") or {}).items()],
        "search_query": query, "search_location": cell, "scraped_at": datetime.now().isoformat(timespec="seconds"),
    }


def passes(lead, min_rating=0.0, min_reviews=0, require_website=False):
    """Filters run before any enrichment, so filtered-out places cost nothing more."""
    return (float(lead.get("rating") or 0) >= min_rating and int(lead.get("review_count") or 0) >= min_reviews
            and bool(lead.get("website") or not require_website))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Google Maps businesses -> lead CSV")
    ap.add_argument("--query", required=True, help='business type, e.g. "dentists"')
    ap.add_argument("--location", required=True, help='one city or ZIP, e.g. "Austin, TX"')
    ap.add_argument("--limit", type=int, default=100, help="max leads to write")
    ap.add_argument("--out", default="leads_google.csv")
    ap.add_argument("--max-cost", type=float, default=5.0, help="hard spend limit in USD")
    ap.add_argument("--min-rating", type=float, default=0.0)
    ap.add_argument("--min-reviews", type=int, default=0)
    ap.add_argument("--require-website", action="store_true", help="keep only businesses with a website")
    a = ap.parse_args(argv)

    treg = Treg(a.max_cost)
    written = dupes = filtered = 0
    rows, saturated = [], False
    with LeadWriter(a.out, GOOGLE_COLUMNS, "place_id") as out:
        try:
            rows, saturated = search_cell(treg, a.query, a.location)
            for raw in rows:
                if written >= a.limit:
                    break
                lead = map_place(raw, a.query, a.location)
                if out.has(lead["place_id"]):
                    dupes += 1
                elif not passes(lead, a.min_rating, a.min_reviews, a.require_website):
                    filtered += 1
                elif out.write(lead):
                    written += 1
        except (BudgetReached, OutOfBalance) as e:
            print(f"stopped: {e}")
    print(f"{written} leads -> {a.out} | {len(rows)} places found, {dupes} duplicates, "
          f"{filtered} filtered{' | search hit the ' + str(ROWS_CAP) + '-result cap: more places likely exist' if saturated else ''}"
          f" | spent ${treg.spent_usd:.5f}")


if __name__ == "__main__":
    main()
