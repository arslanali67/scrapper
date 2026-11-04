"""Offline checks for google_scraper.py (no network, no spend). Run: python test_google.py"""
import csv
import re
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
    """Maps search costs 150 micro-USD; any other capability costs 1000 and returns {}."""

    def __init__(self, rows):
        self.rows, self.spent_micro, self.calls = rows, 0, []
        self.log_path = Path(tempfile.mkdtemp()) / "log.jsonl"

    @property
    def spent_usd(self):
        return self.spent_micro / 1e6

    def call(self, capability, params, tally=None):
        self.calls.append((capability, params))
        cost = 150 if capability == "maps_search" else 1000
        self.spent_micro += cost
        if tally is not None:
            tally.append(cost)
        return {"rows": self.rows} if capability == "maps_search" else {}


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
    gs.main(["--query", "dentists", "--location", "Austin, TX", "--limit", "2", "--require-website", "--no-enrich",
             "--out", str(out)])
    got = read(out)
    assert [r["place_id"] for r in got] == ["ChIJ1", "ChIJ3"], got  # dup skipped, no-website filtered, limit 2
    assert got[0]["all_categories"] == "Dentist | Cosmetic dentist" and got[0]["phone"] == "+1 512-717-3147"
    assert got[0]["cost_usd"] == "7.5e-05"  # the $0.00015 search split over 2 leads


def read(path):
    return list(csv.DictReader(open(path, newline="", encoding="utf-8-sig")))


def places(n):
    return [{**RAW, "place_id": f"P{i}", "name": f"Biz {i}", "website": f"https://biz{i}.com"} for i in range(n)]


def fake_crawl(log, fail_from=None):
    """Stands in for crawl_site: one paid page render per site, so spend flows through the per-lead meter."""
    def crawl(url, treg=None):
        i = int(re.search(r"\d+", url).group())
        log.append(i)
        if fail_from is not None and i >= fail_from:
            raise gs.BudgetReached("budget reached")
        treg.call("web_extract", {"url": url})
        return {"emails": [f"info@biz{i}.com"], "phones": ["+1 512-474-1234"], "facebook": f"https://facebook.com/biz{i}",
                "services": ["Implants", "Crowns"], "error": ""}
    return crawl


def test_dry_run_spends_nothing():
    def no_treg(*a, **k):
        raise AssertionError("dry run must not create a treg client")
    gs.Treg = no_treg
    gs.main(["--query", "q", "--location", "c", "--paid-emails", "--verify", "--dry-run",
             "--out", str(Path(tempfile.mkdtemp()) / "x.csv")])


def test_refuses_to_append_without_resume():
    out = Path(tempfile.mkdtemp()) / "g.csv"
    out.write_text("company_name\n")
    try:
        gs.main(["--query", "q", "--location", "c", "--out", str(out)])
        raise AssertionError("expected SystemExit")
    except SystemExit as e:
        assert "--resume" in str(e)


def test_enrichment_merge_and_cost():
    crawled = []
    gs.Treg = lambda max_cost: FakeTreg(places(2))
    gs.crawl_site = fake_crawl(crawled)
    out = Path(tempfile.mkdtemp()) / "g.csv"
    gs.main(["--query", "dentists", "--location", "Austin, TX", "--limit", "5", "--out", str(out)])
    got = {r["place_id"]: r for r in read(out)}
    r = got["P1"]
    assert r["email"] == "info@biz1.com" and r["email_source"] == "website" and r["facebook"] == "https://facebook.com/biz1"
    assert r["all_phones"] == "+1 512-717-3147 | +1 512-474-1234" and r["services"] == "Implants | Crowns"
    assert r["cost_usd"] == "0.001075"  # 150/2 search share + 1000 page render
    assert sorted(crawled) == [0, 1]


def test_budget_stop_keeps_finished_leads_and_resume_skips_them():
    gs.WORKERS = 1  # deterministic order
    out = Path(tempfile.mkdtemp()) / "g.csv"
    crawled = []
    gs.Treg = lambda max_cost: FakeTreg(places(6))
    gs.crawl_site = fake_crawl(crawled, fail_from=3)  # budget runs out from the 4th lead on
    gs.main(["--query", "q", "--location", "c", "--limit", "6", "--out", str(out)])
    assert sorted(r["place_id"] for r in read(out)) == ["P0", "P1", "P2"]
    # Rerun with --resume (budget fine now): only the 3 missing leads are scraped, nothing twice.
    crawled.clear()
    gs.crawl_site = fake_crawl(crawled)
    gs.main(["--query", "q", "--location", "c", "--limit", "6", "--out", str(out), "--resume"])
    ids = [r["place_id"] for r in read(out)]
    assert sorted(ids) == [f"P{i}" for i in range(6)] and len(ids) == len(set(ids)) and sorted(crawled) == [3, 4, 5]
    gs.WORKERS = 8


def test_plan_area():
    cells, st = gs.plan_area("Texas")
    assert st == "TX" and cells[0]["cell"] == "Houston, TX" and len(cells) == 391 and gs.plan_area("tx")[0] == cells
    assert all(c["kind"] == "city" for c in cells) and len(gs.plan_area("Texas", min_population=100000)[0]) < 50
    assert gs.plan_area("Texas", split="none")[0] == [{"cell": "Texas", "kind": "state", "city": "", "state": "TX"}]
    assert gs.plan_area("Austin, TX") == gs.plan_area("Austin, Texas") == \
        ([{"cell": "Austin, TX", "kind": "city", "city": "Austin", "state": "TX"}], "TX")
    zips, st = gs.plan_area("Austin, TX", split="zips")
    assert st == "TX" and len(zips) == 74 and zips[0]["kind"] == "zip" and zips[0]["cell"].endswith(", TX")
    assert gs.plan_area("78704") == ([{"cell": "78704, TX", "kind": "zip", "city": "", "state": "TX"}], "TX")
    assert gs.plan_area("London, UK") == ([{"cell": "London, UK", "kind": "free", "city": "", "state": ""}], None)
    f = Path(tempfile.mkdtemp()) / "areas.txt"
    f.write_text("Leeds, UK\n# comment\n\nBristol, UK\n", encoding="utf-8")
    assert [c["cell"] for c in gs.plan_area(locations_file=f)[0]] == ["Leeds, UK", "Bristol, UK"]


class AreaTreg(FakeTreg):
    """Maps search answer depends on the searched cell."""

    def __init__(self, answer):
        super().__init__([])
        self.answer = answer

    def call(self, capability, params, tally=None):
        super().call(capability, params, tally)
        return {"rows": self.answer(params["location"])}


def biz(tag, n, address="1 Main St, Austin, TX 78704"):
    return [{**RAW, "place_id": f"{tag}-{i}", "name": f"{tag} {i}", "address": address} for i in range(n)]


def test_full_city_is_researched_by_zip_and_other_states_dropped():
    def answer(cell):
        if cell == "Austin, TX":
            return biz("city", 100)  # hits the cap -> 74 ZIP searches follow
        return biz(cell, 2) + biz("dup", 1) + biz("nm", 1, "9 Elm St, Santa Fe, NM 87501")
    t = AreaTreg(answer)
    gs.Treg = lambda max_cost: t
    out = Path(tempfile.mkdtemp()) / "g.csv"
    gs.main(["--query", "dentists", "--location", "Austin, TX", "--limit", "1000", "--no-enrich", "--out", str(out)])
    rows = read(out)
    zip_searches = [p["location"] for c, p in t.calls if p["location"] != "Austin, TX"]
    assert len(zip_searches) == 74 and all(z.endswith(", TX") for z in zip_searches)
    assert len(rows) == 100 + 74 * 2 + 1 and len({r["place_id"] for r in rows}) == len(rows)  # dup counted once
    assert not any(r["state"] == "NM" for r in rows)


def test_search_stops_once_limit_is_reached():
    t = AreaTreg(lambda cell: biz(cell, 100))
    gs.Treg = lambda max_cost: t
    out = Path(tempfile.mkdtemp()) / "g.csv"
    gs.main(["--query", "dentists", "--location", "Texas", "--limit", "150", "--no-enrich", "--out", str(out)])
    assert len(read(out)) == 150
    assert len(t.calls) <= 2 * gs.SEARCH_WORKERS, len(t.calls)  # not 391 searches + their ZIP codes


if __name__ == "__main__":
    tests = [f for name, f in sorted(globals().items()) if name.startswith("test_")]
    for f in tests:
        f()
        print("ok", f.__name__)
    print(f"{len(tests)} checks passed")
