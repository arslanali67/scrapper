"""Offline checks for google_scraper.py (no network, no spend). Run: python test_google.py"""
import csv
import tempfile
from pathlib import Path

import google_scraper as gs

RAW = {"name": "ATX Family Dental", "place_id": "ChIJ1", "url": "https://maps.google.com/?cid=1",
       "address": "1700 S 1st St, Austin, TX 78704", "phone": "(512) 717-3147", "website": "https://www.atxfamilydental.com/",
       "category": "Dentist", "categories": ["Dentist", "Cosmetic dentist"], "rating": 4.8, "review_count": 312,
       "latitude": 30.24, "longitude": -97.75, "hours": {"Monday": "8 AM–5 PM", "Sunday": "Closed"}}


def test_map_place():
    lead = gs.map_place(RAW, "dentists", "Austin, TX")
    assert (lead["city"], lead["state"], lead["postal_code"], lead["country"]) == ("Austin", "TX", "78704", "US")
    assert lead["phone"] == "+1 512-717-3147" and lead["domain"] == "atxfamilydental.com"
    assert lead["opening_hours"] == ["Monday: 8 AM–5 PM", "Sunday: Closed"] and lead["place_id"] == "ChIJ1"
    assert lead["search_query"] == "dentists" and lead["search_location"] == "Austin, TX"
    m = gs.US_ADDRESS_RE.search("12 Main St Ste 4, Round Rock, TX 78664-1234, United States")
    assert m and m.groups() == ("Round Rock", "TX", "78664")
    assert gs.map_place({**RAW, "address": "Somewhere without a zip"}, "q", "c")["state"] == ""


def test_place_key_fallback_and_filters():
    assert gs.place_key({"phone": "512-717-3147", "website": "https://x.com"}) == "nopid:+1 512-717-3147|x.com"
    lead = gs.map_place(RAW, "q", "c")
    assert gs.passes(lead) and gs.passes(lead, min_rating=4.5, min_reviews=300, require_website=True)
    assert not gs.passes(lead, min_rating=4.9) and not gs.passes(lead, min_reviews=400)
    assert not gs.passes({**lead, "website": ""}, require_website=True) and gs.passes({**lead, "website": ""})


class FakeTreg:
    def __init__(self, rows):
        self.rows, self.spent_usd, self.calls = rows, 0.00015, []

    def call(self, capability, params):
        self.calls.append((capability, params))
        return {"rows": self.rows}


def test_search_cell_saturation():
    t = FakeTreg([RAW] * gs.ROWS_CAP)
    rows, saturated = gs.search_cell(t, "dentists", "Austin, TX")
    assert saturated and t.calls == [("maps_search", {"query": "dentists", "location": "Austin, TX", "limit": 100})]
    assert gs.search_cell(FakeTreg([RAW] * 20), "q", "c") == ([RAW] * 20, False)


def test_main_end_to_end():
    rows = [RAW, {**RAW, "name": "dup"}, {**RAW, "place_id": "ChIJ2", "website": ""},
            {**RAW, "place_id": "ChIJ3", "name": "Third"}, {**RAW, "place_id": "ChIJ4", "name": "Fourth"}]
    gs.Treg = lambda max_cost: FakeTreg(rows)
    out = Path(tempfile.mkdtemp()) / "g.csv"
    gs.main(["--query", "dentists", "--location", "Austin, TX", "--limit", "2", "--require-website", "--out", str(out)])
    got = list(csv.DictReader(open(out, newline="", encoding="utf-8-sig")))
    assert [r["place_id"] for r in got] == ["ChIJ1", "ChIJ3"], got  # dup skipped, no-website filtered, limit 2
    assert got[0]["all_categories"] == "Dentist | Cosmetic dentist" and got[0]["phone"] == "+1 512-717-3147"


if __name__ == "__main__":
    tests = [f for name, f in sorted(globals().items()) if name.startswith("test_")]
    for f in tests:
        f()
        print("ok", f.__name__)
    print(f"{len(tests)} checks passed")
