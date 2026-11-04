"""LinkedIn scraper: people by role, industry and US location (e.g. HR at tech companies in Texas), written to a lead CSV.

  python linkedin_scraper.py --role HR --industry tech --location Texas --limit 100
  python linkedin_scraper.py --titles "Office Manager,Practice Manager" --location "Austin, TX" --source xray

People come from a people-database search through treg (icypeas), never from logging in to LinkedIn.
--source xray uses Google X-ray search instead (site:linkedin.com/in ...). M9 discovery; M10 checks every person
(current title, state) and fills the company domain; M11 finds work emails; M12 adds the full command.
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from common import (ENDPOINTS, PEOPLE_COLUMNS, BudgetReached, LeadSpend, LeadWriter, OutOfBalance, Treg, TregError,
                    cities_of, domain_of, state_code, states)

# Job-title variants per role; --titles overrides. Unknown roles are searched as written.
ROLE_TITLES = {
    "hr": ["HR Manager", "HR Director", "Human Resources", "HR Business Partner", "HR Generalist",
           "Talent Acquisition", "Recruiter", "People Operations"],
    "recruiter": ["Recruiter", "Technical Recruiter", "Talent Acquisition", "Recruiting Manager"],
    "sales": ["Head of Sales", "Sales Director", "VP Sales", "Sales Manager", "Account Executive"],
    "marketing": ["Head of Marketing", "Marketing Director", "Marketing Manager", "CMO"],
    "founder": ["Founder", "Co-Founder", "CEO", "Owner"],
    "engineering": ["CTO", "VP Engineering", "Head of Engineering", "Engineering Manager"],
    "finance": ["CFO", "Finance Director", "Controller"],
    "it": ["IT Manager", "IT Director", "CIO"],
}
# LinkedIn industry names per short word; --industry also takes exact LinkedIn names, comma-separated.
INDUSTRIES = {
    "tech": ["Software Development", "IT Services and IT Consulting", "Technology, Information and Internet"],
    "healthcare": ["Hospitals and Health Care", "Medical Practices", "Wellness and Fitness Services"],
    "finance": ["Financial Services", "Banking", "Investment Management", "Insurance"],
    "marketing": ["Advertising Services", "Marketing Services"],
    "real estate": ["Real Estate", "Leasing Non-residential Real Estate"],
    "construction": ["Construction"],
    "manufacturing": ["Manufacturing", "Industrial Machinery Manufacturing"],
    "education": ["Higher Education", "E-Learning Providers", "Education Administration Programs"],
    "legal": ["Law Practice", "Legal Services"],
    "retail": ["Retail"],
    "logistics": ["Transportation, Logistics, Supply Chain and Storage", "Truck Transportation"],
}
XRAY_KEYWORD = {"tech": "software", "healthcare": "health", "finance": "finance", "marketing": "marketing"}
PAGE_SIZE = 100  # icypeas rows per call; size is the price dial ($0.00038 per row returned)
PROFILE_RE = re.compile(r"linkedin\.com/in/([^/?#\s]+)", re.I)
# Words that say nothing about the role itself: "HR Manager" and "Head of HR" both match on "HR".
GENERIC_TITLE_WORDS = {"senior", "sr", "junior", "jr", "lead", "head", "manager", "director", "vp", "vice", "chief",
                       "officer", "specialist", "generalist", "partner", "business", "coordinator", "associate",
                       "assistant", "principal", "executive", "of", "the", "and", "global", "regional", "operations"}
FORMER_RE = re.compile(r"^\s*(former|ex[- ]|retired|previously)\b", re.I)
NOT_COMPANY_SITES = {"linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
                     "google.com", "sites.google.com", "bit.ly", "linktr.ee"}
COMPANY_SUFFIX_RE = re.compile(r"\b(inc|llc|l\.l\.c|ltd|co|corp|corporation|company|group|the|plc|lp|llp)\b\.?", re.I)
MIN_CITY_POP_FOR_MATCH = 20000  # "Dallas-Fort Worth" -> Dallas; smaller names are too ambiguous across states


# ---------------------------------------------------------------- discovery (M9)
def title_variants(role=None, titles=None):
    if titles:
        return [t.strip() for t in titles.split(",") if t.strip()]
    return ROLE_TITLES.get((role or "").strip().lower(), [role.strip()] if role else [])


def industry_names(industry):
    if not industry:
        return []
    return INDUSTRIES.get(industry.strip().lower()) or [i.strip() for i in industry.split(",") if i.strip()]


def normalize_profile_url(url):
    """Any LinkedIn profile URL (country subdomain, query, trailing path) -> 'https://www.linkedin.com/in/<slug>'."""
    m = PROFILE_RE.search(url or "")
    return f"https://www.linkedin.com/in/{m.group(1).lower().rstrip('/')}" if m else ""


def location_filter(location):
    """--location -> (profileLocation text for the search, state code or None)."""
    if not location:
        return None, None
    st = state_code(location)
    if st:
        return f"{states()[st]}, United States", st
    m = re.fullmatch(r"(.+?),\s*([A-Za-z .]+)", location.strip())
    if m and state_code(m.group(2)):
        st = state_code(m.group(2))
        return f"{m.group(1).strip()}, {states()[st]}", st
    return location.strip(), None


def build_query(titles, location=None, industries=(), size=None):
    """icypeas filter object (see docs/endpoints.md)."""
    q = {"currentJobTitle": {"include": list(titles)}}
    place, _ = location_filter(location)
    if place:
        q["profileLocation"] = {"include": [place]}  # M1: 20/20 in-state; plain "location" matched 13/20
    if industries:
        q["currentCompany.industry"] = {"include": list(industries)}
    if size:
        lo, _, hi = size.replace("+", "-").partition("-")
        q["currentCompany.headcount"] = {">=": int(lo), **({"<=": int(hi)} if hi else {})}
    return q


def people_search(treg, query, want, seen, token=None):
    """Page through icypeas until `want` new people (by profile URL) or no more results.
    Returns (rows, total, next_token); next_token is None when there are no more pages."""
    rows, total = [], None
    while len(rows) < want:
        page = {"size": min(PAGE_SIZE, want - len(rows)), **({"token": token} if token else {})}
        # Per-row pricing: cap this call at the page's own price (+25% and a cent of slack), not the 200-row worst case.
        cap = page["size"] * ENDPOINTS["people_search"]["price_usd"] * 1.25 + 0.01
        d = treg.call("people_search", {"query": query, "pagination": page}, max_cost=cap)
        leads, total = d.get("leads") or [], d.get("total", total)
        for lead in leads:
            url = normalize_profile_url(lead.get("profileUrl"))
            if url and url not in seen:
                seen.add(url)
                rows.append({**lead, "profileUrl": url})
        token = (d.get("pagination") or {}).get("token") or None
        if not leads or not token:
            token = None
            break
    return rows[:want], total, token


def xray_queries(titles, location=None, industry=None, min_population=50000):
    """Google X-ray queries: 3 titles each, for the state name and then its larger cities (or the given place)."""
    _, st = location_filter(location)
    places = ([states()[st]] + [c for c, pop in cities_of(st) if pop >= min_population]) if st and state_code(location) \
        else [location.split(",")[0].strip()] if location else [""]
    kw = XRAY_KEYWORD.get((industry or "").lower(), "")
    for place in places:
        for i in range(0, len(titles), 3):
            group = " OR ".join(f'"{t}"' for t in titles[i:i + 3])
            yield " ".join(p for p in (f"site:linkedin.com/in ({group})", f'"{place}"' if place else "",
                                       f'"{kw}"' if kw else "") if p)


def parse_xray_title(title):
    """'Jane Doe - HR Manager - Acme Inc | LinkedIn' -> ('Jane Doe', 'HR Manager', 'Acme Inc')."""
    t = re.sub(r"\s*[|\-–]\s*LinkedIn\s*$", "", title or "", flags=re.I)
    parts = [p.strip() for p in re.split(r"\s+[-–—|]\s+", t) if p.strip()]
    return (parts + ["", "", ""])[:3]


def xray_search(treg, query, seen):
    """One Google results call -> people rows with parsed name/title/company (M10 looks the profile up)."""
    rows = []
    for r in ((treg.call("serp", {"q": query, "limit": 100}).get("output") or {}).get("results") or []):
        url = normalize_profile_url(r.get("url") or r.get("link"))
        if url and url not in seen:
            seen.add(url)
            name, title, company = parse_xray_title(r.get("title"))
            rows.append({"profileUrl": url, "fullName": name, "lastJobTitle": title, "lastCompanyName": company,
                         "snippet": r.get("snippet", ""), "source": "xray"})
    return rows


# ---------------------------------------------------------------- checks and company domain (M10)
def _title_words(text):
    return re.findall(r"[a-z0-9]+", (text or "").lower().replace("&", " and "))


def title_matches(job_title, titles):
    """A title matches a variant when it has all of the variant's distinctive words. Words compare on their first
    6 letters so 'Recruiter' and 'Recruiting' match; generic words are picked on the full word ('officer' != 'office')."""
    words = {w[:6] for w in _title_words(job_title)}
    for t in titles:
        core = [w for w in _title_words(t) if w not in GENERIC_TITLE_WORDS] or _title_words(t)
        if {w[:6] for w in core} <= words:
            return True
    return False


@lru_cache(None)
def _state_name_re():
    names = sorted(states().values(), key=len, reverse=True)  # "West Virginia" before "Virginia"
    return re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b", re.I)


@lru_cache(None)
def _city_re(st):
    big = [c for c, pop in cities_of(st) if pop >= MIN_CITY_POP_FOR_MATCH]
    return re.compile(r"\b(" + "|".join(map(re.escape, sorted(big, key=len, reverse=True))) + r")\b", re.I) if big else None


def location_reason(location, st):
    """'' when the person's location is in state `st`, else 'label: detail' saying why not."""
    loc = (location or "").strip()
    if not loc:
        return "no location: profile has none"
    named = {state_code(m) for m in _state_name_re().findall(loc)}
    named |= {c for c in re.findall(r",\s*([A-Z]{2})\b", loc) if c in states()}
    if st in named:
        return ""
    if named:
        return f"outside {st}: located in {', '.join(sorted(named))}"
    if _city_re(st) and _city_re(st).search(loc):  # "Dallas-Fort Worth", "Greater Houston"
        return ""
    return f"outside {st}: location '{loc}'"


def check_person(p, titles, st):
    """'' when the person matches the request (current title, state), else 'label: detail' saying why not.
    The label (text before ':') is what the run summary counts."""
    title = (p.get("lastJobTitle") or "").strip()
    if not title:
        return "no title: no current job title"
    if FORMER_RE.match(title):
        return f"former role: '{title}'"
    if not title_matches(title, titles):
        return f"title mismatch: '{title}'"
    return location_reason(p.get("address"), st) if st else ""


def _company_key(name):
    return " ".join(COMPANY_SUFFIX_RE.sub(" ", re.sub(r"[^\w\s]", " ", (name or "").lower())).split())


def _row_domain(row):
    for k in ("domain", "website", "website_url", "domain_name", "url", "primary_domain"):
        d = domain_of(row.get(k) if isinstance(row.get(k), str) else "")
        if d and d not in NOT_COMPANY_SITES:
            return d
    return ""


def company_domain(p, treg, cache):
    """-> (domain, how): the row's company website, else the company's LinkedIn page via blitzapi (misses free),
    else a company search by name where the name matches. ('', '') when nothing fits; cached per company."""
    d = domain_of(p.get("lastCompanyWebsite"))
    if d and d not in NOT_COMPANY_SITES:
        return d, "website"
    li = p.get("lastCompanyUrl") or ""
    if "linkedin.com/company/" in li:
        key = ("li", li.split("?")[0].rstrip("/").lower())
        if key not in cache:
            try:
                r = treg.call("company_domain_from_linkedin", {"company_linkedin_url": li})
                cache[key] = domain_of(r.get("email_domain")) if r.get("found") else ""
            except TregError:
                pass  # failures are not cached: the next person at this company retries
        if cache.get(key):
            return cache[key], "linkedin page"
    name = p.get("lastCompanyName") or ""
    want = _company_key(name)
    if want:
        key = ("name", want)
        if key not in cache:
            try:
                rows = (treg.call("company_by_name", {"name": name, "country": "US", "limit": 3}).get("output") or {}) \
                    .get("companies") or []
                cache[key] = next((_row_domain(r) for r in rows if _company_key(r.get("name")) == want and _row_domain(r)), "")
            except TregError:
                pass
        if cache.get(key):
            return cache[key], "name search"
    return "", ""


def enrich_profile(p, treg):
    """X-ray rows only have a Google title: look the profile up for current title, company and location."""
    r = treg.call("people_enrich", {"linkedin_url": p["profileUrl"]})
    o, person = r.get("output") or {}, (r.get("raw") or {}).get("person") or {}
    current = next((e for e in person.get("experiences") or [] if e.get("job_is_current")), {})
    return {**p, "firstname": o.get("first_name") or p.get("firstname"), "lastname": o.get("last_name") or p.get("lastname"),
            "fullName": o.get("full_name") or p.get("fullName"), "lastJobTitle": o.get("title") or p.get("lastJobTitle"),
            "lastCompanyName": o.get("company") or p.get("lastCompanyName"), "address": o.get("location") or p.get("address"),
            "lastCompanyWebsite": o.get("company_domain") or current.get("company_domain") or "",
            "lastCompanyUrl": current.get("company_linkedin_url") or "", "headline": person.get("headline")}


def map_person(p, search_query):
    """Discovery row (icypeas, or looked-up X-ray) -> people CSV columns."""
    first, last = p.get("firstname") or "", p.get("lastname") or ""
    full = p.get("fullName") or " ".join(x for x in (first, last) if x)
    if full and not first:
        first, _, last = full.partition(" ")
    website = p.get("lastCompanyWebsite") or ""
    if website and not re.match(r"https?://", website):
        website = "https://" + website
    loc = p.get("address") or ""
    return {
        "full_name": full, "first_name": first, "last_name": last, "job_title": p.get("lastJobTitle"),
        "headline": p.get("headline"), "person_location": loc, "person_linkedin_url": p.get("profileUrl"),
        "source_url": p.get("profileUrl"), "company_name": p.get("lastCompanyName"),
        "company_linkedin_url": p.get("lastCompanyUrl"), "website": website, "domain": domain_of(website),
        "industry": p.get("lastCompanyIndustry"), "company_size": p.get("lastCompanySize"),
        "address": p.get("lastCompanyAddress"), "city": loc.split(",")[0].strip() if loc else "",
        "country": loc.split(",")[-1].strip() if "," in loc else "", "search_query": search_query,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
    }


def process(p, treg, titles, st, cache, search_query):
    """One discovered person -> (lead or None, reject reason, how the domain was found, micro-USD spent on them)."""
    spend = LeadSpend(treg)
    if p.get("source") == "xray":
        try:
            p = enrich_profile(p, spend)
        except TregError:
            return None, "profile lookup failed", "", spend.micro
    reason = check_person(p, titles, st)
    if reason:
        return None, reason, "", spend.micro
    domain, how = company_domain(p, spend, cache)
    lead = map_person(p, search_query)
    if domain and not lead["domain"]:
        lead["domain"], lead["website"] = domain, f"https://{domain}"
    return lead, "", how, spend.micro


def main(argv=None):
    ap = argparse.ArgumentParser(description="LinkedIn people by role, industry and location -> lead CSV")
    ap.add_argument("--role", help="HR, recruiter, sales, marketing, founder, engineering, finance, IT, or any title")
    ap.add_argument("--titles", help="exact job titles, comma-separated (overrides --role)")
    ap.add_argument("--industry", help=f"{', '.join(INDUSTRIES)} or exact LinkedIn industry names, comma-separated")
    ap.add_argument("--location", help='a US state ("Texas"), a city ("Austin, TX") or any place text')
    ap.add_argument("--size", help='company headcount, e.g. "11-50" or "1000+"')
    ap.add_argument("--source", choices=("people", "xray"), default="people")
    ap.add_argument("--limit", type=int, default=100, help="people to keep (after the checks)")
    ap.add_argument("--out", default="leads_linkedin.csv")
    ap.add_argument("--max-cost", type=float, default=1.0, help="hard spend limit in USD for this run")
    a = ap.parse_args(argv)
    titles = title_variants(a.role, a.titles)
    if not titles:
        ap.error("give --role or --titles")
    if Path(a.out).exists() and Path(a.out).stat().st_size:
        sys.exit(f"{a.out} already exists: choose another --out")

    treg, seen, cache = Treg(a.max_cost), set(), {}
    _, st = location_filter(a.location)
    reasons, domains, stats, stop = Counter(), Counter(), Counter(), ""
    rejects_path = treg.log_path.with_name(treg.log_path.stem + "-rejected.jsonl")
    label = f"{a.role or a.titles} | {a.industry or 'any industry'} | {a.location or 'anywhere'}"

    with LeadWriter(a.out, PEOPLE_COLUMNS, "person_linkedin_url") as out, \
            open(rejects_path, "a", encoding="utf-8") as rejects:
        def handle(rows, share, search_query):
            for p in rows:
                if stats["kept"] >= a.limit:
                    return
                stats["found"] += 1
                lead, reason, how, spent = process(p, treg, titles, st, cache, search_query)
                if lead is None:
                    reasons[reason.split(":")[0]] += 1
                    rejects.write(json.dumps({"profile": p.get("profileUrl"), "title": p.get("lastJobTitle"),
                                              "location": p.get("address"), "reason": reason}) + "\n")
                    continue
                domains[how or "none"] += 1
                lead["cost_usd"] = round((share + spent) / 1e6, 6)
                stats["kept"] += out.write(lead)
            if stats["found"] >= 50 and stats["kept"] < stats["found"] * 0.1:
                # Guard against paging (and paying) through thousands of rows that all fail the checks.
                raise TregError(f"only {stats['kept']} of {stats['found']} people pass the checks; "
                                f"check --role/--titles/--location (dropped people: {rejects_path})")

        try:
            if a.source == "people":
                query, token, total = build_query(titles, a.location, industry_names(a.industry), a.size), None, None
                while stats["kept"] < a.limit:
                    spend = LeadSpend(treg)  # one page at a time, so the checks run before more rows are bought
                    rows, total, token = people_search(spend, query, min(PAGE_SIZE, a.limit - stats["kept"]), seen, token)
                    handle(rows, spend.micro / len(rows) if rows else 0, label)
                    if not rows or token is None:
                        break
                print(f"people search: {total} matches in total")
            else:
                for q in xray_queries(titles, a.location, a.industry):
                    if stats["kept"] >= a.limit:
                        break
                    spend = LeadSpend(treg)
                    rows = xray_search(spend, q, seen)
                    handle(rows, spend.micro / len(rows) if rows else 0, q)
        except (BudgetReached, OutOfBalance) as e:
            stop = str(e)
        except TregError as e:
            stop = str(e)

    print(f"{stats['kept']} people -> {a.out} | {stats['found']} checked, "
          f"{stats['found'] - stats['kept']} dropped" + (f" ({', '.join(f'{k} x{v}' for k, v in reasons.most_common())})"
                                                       if reasons else ""))
    print(f"  company domain: " + ", ".join(f"{k} {v}" for k, v in domains.most_common()) if domains else "  company domain: -")
    print(f"  spent ${treg.spent_usd:.4f} | run log {treg.log_path} | dropped people {rejects_path}"
          + (f" | stopped early: {stop}" if stop else ""))


if __name__ == "__main__":
    main()
