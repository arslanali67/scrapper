"""Offline checks for linkedin_scraper.py (no network, no spend). Run: python test_linkedin.py"""
import csv
import tempfile
from pathlib import Path

import linkedin_scraper as ls


def person(i, **kw):
    return {"firstname": f"First{i}", "lastname": f"Last{i}", "profileUrl": f"https://uk.linkedin.com/in/Person-{i}/?trk=x",
            "lastJobTitle": "HR Manager", "address": "Austin, Texas, United States", "lastCompanyName": f"Co {i}",
            "lastCompanyWebsite": f"www.co{i}.com", "lastCompanyIndustry": "Software Development", **kw}


def test_titles_industries_urls():
    assert len(ls.title_variants("HR")) == 8 and ls.title_variants("hr", "A, B ,") == ["A", "B"]
    assert ls.title_variants("Office Manager") == ["Office Manager"] and ls.title_variants() == []
    assert ls.industry_names("tech")[0] == "Software Development" and ls.industry_names("Banking, Insurance") == ["Banking", "Insurance"]
    assert ls.normalize_profile_url("https://uk.linkedin.com/in/Jane-Doe-12/?trk=x") == "https://www.linkedin.com/in/jane-doe-12"
    assert ls.normalize_profile_url("https://www.linkedin.com/company/acme") == ""


def test_build_query():
    q = ls.build_query(["HR Manager"], "Texas", ["Software Development"], "11-50")
    assert q == {"currentJobTitle": {"include": ["HR Manager"]}, "profileLocation": {"include": ["Texas, United States"]},
                 "currentCompany.industry": {"include": ["Software Development"]},
                 "currentCompany.headcount": {">=": 11, "<=": 50}}
    assert ls.build_query(["X"], "Austin, TX")["profileLocation"] == {"include": ["Austin, Texas"]}
    assert ls.build_query(["X"], size="1000+")["currentCompany.headcount"] == {">=": 1000}
    assert "profileLocation" not in ls.build_query(["X"])


class PagedTreg:
    """icypeas fake: pages of `size` people; the second page repeats one person; 3 pages then no token."""

    def __init__(self):
        self.calls, self.spent_usd, self.spent_micro = [], 0.0, 0

    def call(self, capability, params, tally=None):
        self.calls.append((capability, params))
        if capability == "serp":
            return {"output": {"results": [
                {"url": "https://www.linkedin.com/in/ann-lee", "title": "Ann Lee – HR Director – Acme | LinkedIn"},
                {"url": "https://www.linkedin.com/jobs/view/1", "title": "a job"},
                {"url": "https://ca.linkedin.com/in/ANN-LEE/", "title": "dup"}]}}
        n = len([c for c in self.calls if c[0] == "people_search"])
        size, start = params["pagination"]["size"], (n - 1) * 1000
        people = [person(start + i) for i in range(size)] + ([person(0)] if n == 2 else [])
        return {"total": 5000, "leads": people, "pagination": {"token": f"t{n}"} if n < 3 else {}}


def test_people_search_pages_exactly_and_dedupes():
    t, seen = PagedTreg(), set()
    rows, total = ls.people_search(t, {"q": 1}, 250, seen)
    sizes = [c[1]["pagination"]["size"] for c in t.calls]
    assert sizes == [100, 100, 50] and total == 5000 and len(rows) == 250 and len(seen) == 250
    assert [c[1]["pagination"].get("token") for c in t.calls] == [None, "t1", "t2"]
    rows, _ = ls.people_search(PagedTreg(), {"q": 1}, 10_000, set())  # stops when the token runs out
    assert len(rows) == 100 + 100 + 100


def test_xray_queries_and_parsing():
    qs = list(ls.xray_queries(ls.title_variants("HR"), "Texas", "tech"))
    assert qs[0] == 'site:linkedin.com/in ("HR Manager" OR "HR Director" OR "Human Resources") "Texas" "software"'
    assert len([q for q in qs if '"Texas"' in q]) == 3 and '"Houston"' in qs[3]
    assert ls.parse_xray_title("Ann Lee – HR Director – Acme | LinkedIn") == ["Ann Lee", "HR Director", "Acme"]
    assert ls.parse_xray_title("Bo Chan - LinkedIn") == ["Bo Chan", "", ""]
    rows = ls.xray_search(PagedTreg(), "q", set())
    assert [r["profileUrl"] for r in rows] == ["https://www.linkedin.com/in/ann-lee"]  # job link dropped, dup dropped


def run(*args):
    out = Path(tempfile.mkdtemp()) / "p.csv"
    t = PagedTreg()
    ls.Treg = lambda max_cost: t
    ls.main([*args, "--out", str(out)])
    return list(csv.DictReader(open(out, newline="", encoding="utf-8-sig"))), t


def test_main_both_sources():
    rows, t = run("--role", "HR", "--industry", "tech", "--location", "Texas", "--limit", "120")
    assert len(rows) == 120 and len({r["person_linkedin_url"] for r in rows}) == 120
    r = rows[0]
    assert r["person_linkedin_url"] == "https://www.linkedin.com/in/person-0" and r["website"] == "https://www.co0.com"
    assert r["domain"] == "co0.com" and r["full_name"] == "First0 Last0" and r["city"] == "Austin"
    assert r["country"] == "United States" and r["company_name"] == "Co 0"
    rows, t = run("--role", "HR", "--location", "Texas", "--source", "xray", "--limit", "5")
    assert [r["full_name"] for r in rows] == ["Ann Lee"] and rows[0]["job_title"] == "HR Director"


if __name__ == "__main__":
    tests = [f for name, f in sorted(globals().items()) if name.startswith("test_")]
    for f in tests:
        f()
        print("ok", f.__name__)
    print(f"{len(tests)} checks passed")
