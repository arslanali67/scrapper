"""Offline checks for linkedin_scraper.py (no network, no spend). Run: python test_linkedin.py"""
import csv
import io
import tempfile
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path

import linkedin_scraper as ls
from common import TregError, cell, domain_of

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
        self.answers, self.calls, self.spent_micro, self.notes = answers, [], 0, []

    @property
    def spent_usd(self):
        return self.spent_micro / 1e6

    def note(self, key, error):
        self.notes.append((key, type(error).__name__))

    def failures(self):
        return ""

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


def test_find_contact_tiers_verify_phone():
    lead = lambda: {"person_linkedin_url": "https://www.linkedin.com/in/ann-lee", "first_name": "Ann", "last_name": "Lee",
                    "domain": "acme.com"}
    hit1 = lambda p: {"found": True, "email": "Ann@Acme.com", "all_emails": [{"email": "ann@acme.com"}, {"email": "a.lee@acme.com"}]}
    miss1 = lambda p: {"found": False, "email": None, "all_emails": []}
    hit2 = lambda p: {"output": {"email": "ann.lee@acme.com", "confidence": 0.9},
                      "_treg": {"served_by": "quickenrich.people.email.find"}}
    t = Fake(email_find_person=hit1)  # tier-1 hit: tier 2 never asked
    l = ls.find_contact(lead(), t)
    assert (l["email"], l["email_source"], cell(l["all_emails"])) == ("ann@acme.com", "blitzapi", "ann@acme.com | a.lee@acme.com")
    assert t.calls == [("email_find_person", {"person_linkedin_url": "https://www.linkedin.com/in/ann-lee"})]
    t = Fake(email_find_person=miss1, email_find_person_fallback=hit2)  # miss -> tier 2 with every identity field
    l = ls.find_contact(lead(), t)
    assert (l["email"], l["email_source"], l["email_confidence"]) == ("ann.lee@acme.com", "quickenrich", 0.9)
    assert t.calls[1] == ("email_find_person_fallback", {"linkedin_url": "https://www.linkedin.com/in/ann-lee",
                                                         "first_name": "Ann", "last_name": "Lee", "domain": "acme.com"})
    t = Fake(email_find_person=miss1)  # --no-fallback: a miss stays empty, never a guessed address
    assert not ls.find_contact(lead(), t, fallback=False).get("email") and len(t.calls) == 1

    def down(p):
        raise TregError("provider out", 503)
    t = Fake(email_find_person=down, email_find_person_fallback=lambda p: {"output": {"email": None}})
    assert not ls.find_contact({**lead(), "domain": ""}, t).get("email")  # outage on tier 1 still tries tier 2
    assert "domain" not in t.calls[1][1]  # empty fields are not sent
    verdicts = iter([{"valid": False, "status": "invalid"}, {"valid": True, "status": "safe"}])
    t = Fake(email_find_person=hit1, email_find_person_fallback=hit2, email_verify=lambda p: {"output": next(verdicts)})
    l = ls.find_contact(lead(), t, verify=True)  # a bounce counts as a miss
    assert (l["email"], l["email_status"], l["email_source"]) == ("ann.lee@acme.com", "valid", "quickenrich")
    t = Fake(email_find_person=hit1, email_verify=lambda p: {"output": {"valid": False, "status": "invalid"}})
    l = ls.find_contact(lead(), t, fallback=False, verify=True)
    assert not l.get("email") and (l["all_emails"], l["email_status"]) == (["ann@acme.com"], "invalid")
    t = Fake(email_find_person=miss1, phone_find_person=lambda p: {"output": {"phone": "+15124741234"}})
    assert ls.find_contact(lead(), t, fallback=False, phone=True)["phone"] == "+1 512-474-1234"
    t = Fake(email_find_person=miss1, phone_find_person=lambda p: {"output": {"phone": None}})
    assert "phone" not in ls.find_contact(lead(), t, fallback=False, phone=True)


def test_process_never_uses_a_linkedin_link_as_domain():
    p = person(1, lastCompanyWebsite="https://www.linkedin.com/company/beta", lastCompanyUrl="https://www.linkedin.com/company/beta")
    t = Fake(company_domain_from_linkedin=lambda q: {"found": True, "email_domain": "beta.io"}, email_find_person=lambda q: {})
    lead, reason, how, _ = ls.process(p, t, HR, "TX", {}, "q", {"fallback": False})
    assert (lead["domain"], lead["website"], how, reason) == ("beta.io", "https://beta.io", "linkedin page", "")
    t = Fake(company_domain_from_linkedin=lambda q: {"found": False}, company_by_name=lambda q: {}, email_find_person=lambda q: {})
    lead, *_ = ls.process(p, t, HR, "TX", {}, "q", {"fallback": False})
    assert lead["domain"] == ""  # not "linkedin.com"
    lead, *_ = ls.process(p, t, HR, "TX", {}, "q", None)  # --no-enrich: no email lookup at all
    assert "email_find_person" not in [c for c, _ in t.calls[3:]]
    odd = Fake(company_domain_from_linkedin=lambda q: {"found": False}, company_by_name=lambda q: {},
               email_find_person=lambda q: ["not", "a", "dict"])  # an odd provider answer: keep the person, log it
    lead, problem, _, _ = ls.process(p, odd, HR, "TX", {}, "q", {"fallback": False})
    assert lead["full_name"] == "First1 Last1" and problem == "email lookup failed (AttributeError)"
    assert odd.notes == [(lead["person_linkedin_url"], "AttributeError")]


class PagedTreg(Fake):
    """icypeas fake: pages of `size` people; the second page repeats one person; 3 pages then no token.
    Email tier 1 hits profile URLs ending in an even digit; tier 2 hits the rest."""

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
        if capability == "email_find_person":
            return {"found": params["person_linkedin_url"][-1] in "02468", "email": "a@co.com"}
        if capability == "email_find_person_fallback":
            return {"output": {"email": "b@co.com"}, "_treg": {"served_by": "quickenrich.people.email.find"}}
        n = len([c for c in self.calls if c[0] == "people_search"])
        size, start = params["pagination"]["size"], (n - 1) * 1000
        people = [self.make(start + i) for i in range(size)] + ([self.make(0)] if n == 2 else [])
        return {"total": 5000, "leads": people, "pagination": {"token": f"t{n}"} if n < self.pages else {}}


def test_people_search_pages_exactly_and_dedupes():
    t, seen = PagedTreg(), set()
    rows, total, token = ls.icypeas_search(t, {"q": 1}, 250, seen)
    sizes = [c[1]["pagination"]["size"] for c in t.calls]
    assert sizes == [100, 100, 50] and total == 5000 and len(rows) == 250 and len(seen) == 250 and token is None
    assert [c[1]["pagination"].get("token") for c in t.calls] == [None, "t1", "t2"]
    rows, _, token = ls.icypeas_search(PagedTreg(), {"q": 1}, 10_000, set())  # stops when the token runs out
    assert len(rows) == 300 and token is None
    rows, _, token = ls.icypeas_search(PagedTreg(), {"q": 1}, 40, set())
    assert len(rows) == 40 and token == "t1"  # more pages left: caller can continue


def test_xray_queries_and_parsing():
    qs = list(ls.xray_queries(HR, "Texas", "tech"))
    assert qs[0] == 'site:linkedin.com/in ("HR Manager" OR "HR Director" OR "Human Resources") "Texas" "software"'
    assert len([q for q in qs if '"Texas"' in q]) == 3 and '"Houston"' in qs[3]
    assert ls.parse_xray_title("Ann Lee – HR Director – Acme | LinkedIn") == ["Ann Lee", "HR Director", "Acme"]
    assert ls.parse_xray_title("Bo Chan - LinkedIn") == ["Bo Chan", "", ""]
    rows = ls.xray_search(PagedTreg(), "q", set())
    assert [r["profileUrl"] for r in rows] == ["https://www.linkedin.com/in/ann-lee"]  # job link dropped, dup dropped


def run(*args, make=person, out=None, t=None):
    out = out or Path(tempfile.mkdtemp()) / "p.csv"
    t = t or PagedTreg(make, pages=10)
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
    r = next(r for r in rows if r["person_linkedin_url"] == "https://www.linkedin.com/in/person-0")  # threads: any order
    assert r["website"] == "https://www.co0.com" and r["domain"] == "co0.com" and r["full_name"] == "First0 Last0"
    assert r["city"] == "Austin"
    rejected = [l for l in t.log_path.with_name("run-rejected.jsonl").read_text().splitlines()]
    assert rejected and all("outside TX" in l for l in rejected)
    # nobody is looked up twice; only kept people are; tier 2 only for tier-1 misses
    looked = Counter(p["person_linkedin_url"] for c, p in t.calls if c == "email_find_person")
    assert len(looked) == 120 and set(looked.values()) == {1}
    assert sorted(p["linkedin_url"] for c, p in t.calls if c == "email_find_person_fallback") == \
        sorted(r["person_linkedin_url"] for r in rows if r["email_source"] == "quickenrich")
    assert {r["email_source"] for r in rows} == {"blitzapi", "quickenrich"} and all(r["email"] for r in rows)


def test_main_xray_looks_profiles_up_and_fills_domain():
    rows, t = run("--role", "HR", "--location", "Texas", "--source", "xray", "--limit", "5")
    assert [r["full_name"] for r in rows] == ["Ann Lee"] and rows[0]["job_title"] == "HR Director"
    assert rows[0]["domain"] == "acme.com" and rows[0]["website"] == "https://acme.com"
    assert [c for c, _ in t.calls if c != "serp"][:2] == ["people_enrich", "company_by_name"]


def test_main_stops_when_nobody_matches():
    rows, t = run("--role", "HR", "--location", "Texas", "--limit", "500",
                  make=lambda i: person(i, lastJobTitle="Software Engineer"))
    assert rows == [] and len([c for c in t.calls if c[0] == "people_search"]) == 1  # not 5,000 paid rows


def test_resume_never_repeats_a_lookup():  # L-A4
    out = Path(tempfile.mkdtemp()) / "p.csv"
    first, _ = run("--role", "HR", "--location", "Texas", "--limit", "30", out=out)
    rows, t = run("--role", "HR", "--location", "Texas", "--limit", "60", "--resume", out=out)
    again = {p["person_linkedin_url"] for c, p in t.calls if c == "email_find_person"}
    assert len(rows) == 60 and len({r["person_linkedin_url"] for r in rows}) == 60
    assert len(again) == 30 and not again & {r["person_linkedin_url"] for r in first}
    try:
        run("--role", "HR", "--location", "Texas", out=out)  # an existing file needs --resume
        raise AssertionError("expected an exit")
    except SystemExit as e:
        assert "--resume" in str(e)


def test_ctrl_c_writes_everyone_already_looked_up():
    class Interrupted(PagedTreg):
        def call(self, capability, params=None, tally=None, max_cost=None):
            if capability == "people_search" and any(c == "people_search" for c, _ in self.calls):
                raise KeyboardInterrupt  # Ctrl+C while the second page is being bought
            return super().call(capability, params, tally, max_cost)
    with redirect_stdout(io.StringIO()):
        rows, t = run("--role", "HR", "--location", "Texas", "--limit", "150", t=Interrupted(pages=10))
    looked = {p["person_linkedin_url"] for c, p in t.calls if c == "email_find_person"}
    # queued people are cancelled before any lookup; everyone already looked up is written, none thrown away
    assert 90 <= len(rows) <= 100 and looked == {r["person_linkedin_url"] for r in rows}


def test_network_drop_mid_run_keeps_csv_and_summary():  # M13 "pull the network cable"
    import httpx
    import json as _json
    import common
    common.Treg.RETRY_DELAYS = (0, 0, 0)
    sent = []

    def handler(req):
        sent.append(req.url.path)
        if len(sent) > 40:
            raise httpx.ConnectError("network is down", request=req)
        ep, body = req.url.path.rsplit("/", 1)[1], _json.loads(req.content)
        if ep == "icypeas.people.search":
            n = len([p for p in sent if p.endswith("icypeas.people.search")])
            leads = [person(n * 1000 + i) for i in range(body["pagination"]["size"])]
            return httpx.Response(200, json={"total": 5000, "leads": leads, "pagination": {"token": f"t{n}"}})
        return httpx.Response(200, json={"found": True, "email": "a@co.com"})  # tier 1 always hits
    out = Path(tempfile.mkdtemp()) / "p.csv"
    ls.Treg = lambda max_cost: common.Treg(max_cost, log_dir=tempfile.mkdtemp(),
                                            transport=httpx.MockTransport(handler), token="t")
    buf = io.StringIO()
    with redirect_stdout(buf):
        ls.main(["--role", "HR", "--location", "Texas", "--limit", "200", "--out", str(out)])  # no traceback
    rows = list(csv.DictReader(open(out, newline="", encoding="utf-8-sig")))
    assert 0 < len(rows) < 200 and all(r["email"] == "a@co.com" for r in rows)  # nobody written half-looked-up
    assert "stopped early: cannot reach treg" in buf.getvalue() and "treg calls failed: unreachable" in buf.getvalue()


def test_suppression_drops_company_and_email():  # M14
    sup = Path(tempfile.mkdtemp()) / "suppress.txt"
    sup.write_text("co3.com\nb@co.com\n")
    rows, t = run("--role", "HR", "--location", "Texas", "--limit", "10", "--suppress", str(sup))
    gone = "https://www.linkedin.com/in/person-3"
    assert len(rows) == 10 and gone not in {r["person_linkedin_url"] for r in rows}
    assert all(r["domain"] != "co3.com" for r in rows)
    assert gone not in {p["person_linkedin_url"] for c, p in t.calls if c == "email_find_person"}  # no paid lookup
    assert all(r["email"] in ("a@co.com", "") for r in rows) and any(r["email"] == "" for r in rows)  # b@co.com removed


def test_dry_run_spends_nothing():  # L-A3
    t = Fake(people_count=lambda p: {"total": 7760}, companies_count=lambda p: {"total": 2081})
    ls.Treg = lambda max_cost: t
    out = Path(tempfile.mkdtemp()) / "never.csv"
    buf = io.StringIO()
    with redirect_stdout(buf):
        ls.main(["--role", "HR", "--industry", "tech", "--location", "Texas", "--limit", "500", "--verify",
                 "--dry-run", "--out", str(out)])
        ls.main(["--mode", "company", "--keyword", "SaaS", "--location", "Texas", "--dry-run"])
    assert [c for c, _ in t.calls] == ["people_count", "companies_count"] and not out.exists()
    assert t.calls[0][1]["query"]["profileLocation"] == {"include": ["Texas, United States"]}
    assert t.calls[1][1]["query"] == {"keyword": {"include": ["SaaS"]}, "location": {"include": ["Texas, United States"]}}
    text = buf.getvalue()
    assert "7760 people match; up to 500" in text and "2081 companies match; up to 100" in text and "$0.0000" in text


def company_row(lid, name, **kw):
    return {"name": name, "lid": lid, "urn": "urn:li:fs_salesCompany:99", "url": f"https://www.linkedin.com/company/{lid}/",
            "address": "801 Barton Springs Road, 78704, Austin, Texas, United States of America",
            "headcountRange": {"gte": 51, "lte": 200}, "numberOfEmployees": 55, "industry": "Software Development",
            "foundedYear": 2016, "type": "Privately Held", "website": f"https://www.{lid}.com", "slogan": "We sync",
            "specialties": [{"value": "SaaS"}, {"value": "software"}], "pictureUrl": "https://img.example/x.png",
            "phone": "", **kw}


class CompanyTreg(Fake):
    """icypeas company fake: by-URL lookups know 'acme' and the numeric id 12345; searches page forever."""

    def call(self, capability, params=None, tally=None, max_cost=None):
        self.calls.append((capability, params))
        if capability == "company_domain_from_linkedin":
            return {"found": False}
        q = params["query"]
        if "companyId" in q:
            slug = q["companyId"]["include"][0].rsplit("/", 1)[-1]
            known = {"acme": company_row("acme", "Acme"),
                     "12345": company_row("beta-co", "Beta", urn="urn:li:fs_salesCompany:12345")}
            return {"total": int(slug in known), "leads": [known[slug]] if slug in known else []}
        n = len([c for c in self.calls if c[0] == "companies_search"])
        return {"total": 2081, "pagination": {"token": f"t{n}"},
                "leads": [company_row(f"co-{n}-{i}", f"Co {n}-{i}") for i in range(params["pagination"]["size"])]}


def test_company_mapping_and_url_file():
    c = ls.map_company(company_row("cirasync", "CiraSync"))
    assert (c["company_name"], c["city"], c["country"], c["company_size"], c["domain"]) == \
        ("CiraSync", "Austin", "United States of America", "51-200", "cirasync.com")
    assert c["source_url"] == "https://www.linkedin.com/company/cirasync" and cell(c["specialties"]) == "SaaS | software"
    assert ls.map_company({"headcountRange": {"gte": 10001}, "url": "x"})["company_size"] == "10001+"
    assert ls.company_query("SaaS, fintech", "Austin, TX", ["Software Development"], "11-50") == {
        "keyword": {"include": ["SaaS", "fintech"]}, "location": {"include": ["Austin, Texas"]},
        "industry": {"include": ["Software Development"]}, "headcount": {">=": 11, "<=": 50}}
    f = Path(tempfile.mkdtemp()) / "urls.txt"
    f.write_text("https://www.linkedin.com/company/acme/\nhttps://WWW.linkedin.com/company/Acme?trk=1\nname,url\n"
                 "Beta,https://www.linkedin.com/company/12345\nnot a url\nhttps://www.linkedin.com/company/ghost-co\n")
    assert ls.read_company_urls(f) == ["https://www.linkedin.com/company/acme", "https://www.linkedin.com/company/12345",
                                       "https://www.linkedin.com/company/ghost-co"]
    return f


def test_main_company_input_returns_exactly_those():  # L-A2
    ls.crawl_site = lambda url, treg=None: {"emails": [f"info@{domain_of(url)}"], "phones": ["+1 512-474-1234"],
                                            "facebook": "https://facebook.com/x"}
    with redirect_stdout(io.StringIO()):
        rows, t = run("--mode", "company", "--input", str(test_company_mapping_and_url_file()), t=CompanyTreg())
    by = {r["source_url"].rsplit("/", 1)[1]: r for r in rows}
    assert sorted(by) == ["12345", "acme", "ghost-co"]  # the duplicate counted once, the unknown one kept
    assert (by["acme"]["company_name"], by["acme"]["email"], by["acme"]["email_source"]) == ("Acme", "info@acme.com", "website")
    assert by["12345"]["company_name"] == "Beta" and by["acme"]["socials"] == "https://facebook.com/x"
    assert by["ghost-co"]["company_name"] == "" and by["acme"]["phone"] == "+1 512-474-1234"


def test_main_company_search_pages_to_the_limit():
    rows, t = run("--mode", "company", "--keyword", "SaaS", "--location", "Texas", "--limit", "5", "--no-enrich",
                  t=CompanyTreg())
    assert len(rows) == 5 and t.calls[0][1] == {"query": {"keyword": {"include": ["SaaS"]}, "location": {
        "include": ["Texas, United States"]}}, "pagination": {"size": 5}}
    assert rows[0]["company_size"] == "51-200" and rows[0]["email"] == ""


if __name__ == "__main__":
    tests = [f for name, f in sorted(globals().items()) if name.startswith("test_")]
    for f in tests:
        f()
        print("ok", f.__name__)
    print(f"{len(tests)} checks passed")
