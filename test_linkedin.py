"""Offline checks for linkedin_scraper.py (no network, no spend). Run: python test_linkedin.py"""
import csv
import tempfile
from pathlib import Path

import linkedin_scraper as ls
from common import TregError

HR = ls.title_variants("HR")


def person(i, **kw):
    return {"firstname": f"First{i}", "lastname": f"Last{i}", "profileUrl": f"https://uk.linkedin.com/in/Person-{i}/?trk=x",
            "lastJobTitle": "HR Manager", "address": "Austin, Texas, United States", "lastCompanyName": f"Co {i}",
            "lastCompanyWebsite": f"www.co{i}.com", "lastCompanyIndustry": "Software Development", **kw}


def test_titles_industries_urls():
    assert len(HR) == 8 and ls.title_variants("hr", "A, B ,") == ["A", "B"]
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


def test_title_matches():
    for ok in ("Head of HR", "Senior Technical Recruiter", "Chief People Officer", "Recruiting Coordinator",
               "Manager, Human Resources", "HR Business Partner II", "Talent Acquisition Lead"):
        assert ls.title_matches(ok, HR), ok
    for bad in ("Product Manager", "Software Engineer", "Director of Sales"):
        assert not ls.title_matches(bad, HR), bad
    assert ls.title_matches("Office Coordinator", ["Office Manager"]) and not ls.title_matches("Sales Manager", ["Office Manager"])
    assert ls.check_person({"lastJobTitle": "Former HR Manager"}, HR, None).startswith("former role")
    assert ls.check_person({"lastJobTitle": ""}, HR, None).startswith("no title")
    assert ls.check_person({"lastJobTitle": "Product Manager"}, HR, None) == "title mismatch: 'Product Manager'"


def test_location_reason():
    for ok in ("Austin, Texas, United States", "Dallas-Fort Worth, United States", "Greater Houston",
               "Texas, United States", "Plano, TX"):
        assert ls.location_reason(ok, "TX") == "", ok
    assert ls.location_reason("Seattle, Washington, United States", "TX") == "outside TX: located in WA"
    assert ls.location_reason("Austin, Minnesota, United States", "TX") == "outside TX: located in MN"
    assert ls.location_reason("Charleston, West Virginia", "VA") == "outside VA: located in WV"
    assert ls.location_reason("Arlington, VA", "TX") == "outside TX: located in VA"
    assert ls.location_reason("London, England, United Kingdom", "TX").startswith("outside TX: location")
    assert ls.location_reason("United States", "TX").startswith("outside TX") and ls.location_reason("", "TX").startswith("no location")


class Fake:
    """Answers per capability from a dict of functions; records calls."""

    def __init__(self, **answers):
        self.answers, self.calls, self.spent_micro = answers, [], 0

    @property
    def spent_usd(self):
        return self.spent_micro / 1e6

    def call(self, capability, params=None, tally=None, max_cost=None):
        self.calls.append((capability, params))
        return self.answers[capability](params)


def test_company_domain_paths_and_cache():
    cache = {}
    assert ls.company_domain({"lastCompanyWebsite": "https://www.Acme.com/about"}, Fake(), cache) == ("acme.com", "website")
    li = Fake(company_domain_from_linkedin=lambda p: {"found": True, "email_domain": "beta.io"})
    p = {"lastCompanyWebsite": "https://www.linkedin.com/company/beta", "lastCompanyUrl": "https://www.linkedin.com/company/beta/"}
    assert ls.company_domain(p, li, cache) == ("beta.io", "linkedin page")
    assert ls.company_domain(p, li, cache) == ("beta.io", "linkedin page") and len(li.calls) == 1  # cached
    rows = {"output": {"companies": [{"name": "Gamma Holdings", "domain": "wrong.com"},
                                     {"name": "Gamma, Inc.", "website_url": "https://gamma.com"}]}}
    by_name = Fake(company_by_name=lambda p: rows)
    assert ls.company_domain({"lastCompanyName": "Gamma Inc"}, by_name, cache) == ("gamma.com", "name search")
    assert by_name.calls == [("company_by_name", {"name": "Gamma Inc", "country": "US", "limit": 3})]
    assert ls.company_domain({"lastCompanyName": "Delta LLC"}, Fake(company_by_name=lambda p: rows), cache) == ("", "")

    def boom(p):
        raise TregError("provider out", 503)
    cache2 = {}
    assert ls.company_domain({"lastCompanyName": "Zeta"}, Fake(company_by_name=boom), cache2) == ("", "") and cache2 == {}


def test_enrich_profile_mapping():
    out = {"output": {"full_name": "Ann Lee", "first_name": "Ann", "last_name": "Lee", "title": "HR Director",
                      "company": "Acme", "company_domain": None, "location": "Austin, Texas, United States"},
           "raw": {"person": {"headline": "People leader", "experiences": [
               {"job_is_current": False, "company_linkedin_url": "https://www.linkedin.com/company/old"},
               {"job_is_current": True, "company_linkedin_url": "https://www.linkedin.com/company/acme"}]}}}
    p = ls.enrich_profile({"profileUrl": "https://www.linkedin.com/in/ann-lee", "lastJobTitle": "stale"},
                          Fake(people_enrich=lambda p: out))
    assert (p["lastJobTitle"], p["lastCompanyName"], p["address"]) == ("HR Director", "Acme", "Austin, Texas, United States")
    assert p["lastCompanyUrl"] == "https://www.linkedin.com/company/acme" and p["headline"] == "People leader"


class PagedTreg(Fake):
    """icypeas fake: pages of `size` people; the second page repeats one person; 3 pages then no token."""

    def __init__(self, make=person, pages=3):
        super().__init__()
        self.make, self.pages = make, pages

    def call(self, capability, params=None, tally=None, max_cost=None):
        self.calls.append((capability, params))
        if capability == "serp":
            return {"output": {"results": [
                {"url": "https://www.linkedin.com/in/ann-lee", "title": "Ann Lee – HR Director – Acme | LinkedIn"},
                {"url": "https://www.linkedin.com/jobs/view/1", "title": "a job"},
                {"url": "https://ca.linkedin.com/in/ANN-LEE/", "title": "dup"}]}}
        if capability == "people_enrich":
            return {"output": {"full_name": "Ann Lee", "title": "HR Director", "company": "Acme",
                               "location": "Houston, Texas, United States"}, "raw": {}}
        if capability == "company_by_name":
            return {"output": {"companies": [{"name": "Acme", "domain": "acme.com"}]}}
        n = len([c for c in self.calls if c[0] == "people_search"])
        size, start = params["pagination"]["size"], (n - 1) * 1000
        people = [self.make(start + i) for i in range(size)] + ([self.make(0)] if n == 2 else [])
        return {"total": 5000, "leads": people, "pagination": {"token": f"t{n}"} if n < self.pages else {}}


def test_people_search_pages_exactly_and_dedupes():
    t, seen = PagedTreg(), set()
    rows, total, token = ls.people_search(t, {"q": 1}, 250, seen)
    sizes = [c[1]["pagination"]["size"] for c in t.calls]
    assert sizes == [100, 100, 50] and total == 5000 and len(rows) == 250 and len(seen) == 250 and token is None
    assert [c[1]["pagination"].get("token") for c in t.calls] == [None, "t1", "t2"]
    rows, _, token = ls.people_search(PagedTreg(), {"q": 1}, 10_000, set())  # stops when the token runs out
    assert len(rows) == 300 and token is None
    rows, _, token = ls.people_search(PagedTreg(), {"q": 1}, 40, set())
    assert len(rows) == 40 and token == "t1"  # more pages left: caller can continue


def test_xray_queries_and_parsing():
    qs = list(ls.xray_queries(HR, "Texas", "tech"))
    assert qs[0] == 'site:linkedin.com/in ("HR Manager" OR "HR Director" OR "Human Resources") "Texas" "software"'
    assert len([q for q in qs if '"Texas"' in q]) == 3 and '"Houston"' in qs[3]
    assert ls.parse_xray_title("Ann Lee – HR Director – Acme | LinkedIn") == ["Ann Lee", "HR Director", "Acme"]
    assert ls.parse_xray_title("Bo Chan - LinkedIn") == ["Bo Chan", "", ""]
    rows = ls.xray_search(PagedTreg(), "q", set())
    assert [r["profileUrl"] for r in rows] == ["https://www.linkedin.com/in/ann-lee"]  # job link dropped, dup dropped


def run(*args, make=person):
    out = Path(tempfile.mkdtemp()) / "p.csv"
    t = PagedTreg(make, pages=10)
    t.log_path = Path(tempfile.mkdtemp()) / "run.jsonl"
    ls.Treg = lambda max_cost: t
    ls.main([*args, "--out", str(out)])
    return list(csv.DictReader(open(out, newline="", encoding="utf-8-sig"))), t


def test_main_people_keeps_paging_past_rejects():
    # every 4th person is outside Texas: the run keeps paging until 120 people pass the checks
    make = lambda i: person(i, address="Seattle, Washington, United States") if i % 4 == 3 else person(i)
    rows, t = run("--role", "HR", "--industry", "tech", "--location", "Texas", "--limit", "120", make=make)
    assert len(rows) == 120 and len({r["person_linkedin_url"] for r in rows}) == 120
    assert not any("Seattle" in r["person_location"] for r in rows)
    r = rows[0]
    assert r["person_linkedin_url"] == "https://www.linkedin.com/in/person-0" and r["website"] == "https://www.co0.com"
    assert r["domain"] == "co0.com" and r["full_name"] == "First0 Last0" and r["city"] == "Austin"
    rejected = [l for l in t.log_path.with_name("run-rejected.jsonl").read_text().splitlines()]
    assert rejected and all("outside TX" in l for l in rejected)


def test_main_xray_looks_profiles_up_and_fills_domain():
    rows, t = run("--role", "HR", "--location", "Texas", "--source", "xray", "--limit", "5")
    assert [r["full_name"] for r in rows] == ["Ann Lee"] and rows[0]["job_title"] == "HR Director"
    assert rows[0]["domain"] == "acme.com" and rows[0]["website"] == "https://acme.com"
    assert [c for c, _ in t.calls][:3] == ["serp", "people_enrich", "company_by_name"]


def test_main_stops_when_nobody_matches():
    rows, t = run("--role", "HR", "--location", "Texas", "--limit", "500",
                  make=lambda i: person(i, lastJobTitle="Software Engineer"))
    assert rows == [] and len([c for c in t.calls if c[0] == "people_search"]) == 1  # not 5,000 paid rows


if __name__ == "__main__":
    tests = [f for name, f in sorted(globals().items()) if name.startswith("test_")]
    for f in tests:
        f()
        print("ok", f.__name__)
    print(f"{len(tests)} checks passed")
