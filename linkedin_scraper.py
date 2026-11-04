"""LinkedIn scraper: people by role, industry and US location (e.g. HR at tech companies in Texas), written to a lead CSV.

  python linkedin_scraper.py --role HR --industry tech --location Texas --limit 100
  python linkedin_scraper.py --titles "Office Manager,Practice Manager" --location "Austin, TX" --source xray

People come from a people-database search through treg (icypeas), never from logging in to LinkedIn.
--source xray uses Google X-ray search instead (site:linkedin.com/in ...). M9 discovery; M10 checks and fills,
M11 finds work emails, M12 adds the full command (dry-run, resume, company mode).
"""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from common import (PEOPLE_COLUMNS, BudgetReached, LeadWriter, OutOfBalance, Treg, TregError, cities_of, domain_of,
                    state_code, states)

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


def people_search(treg, query, want, seen):
    """Page through icypeas until `want` new people (by profile URL) or no more results. Returns (rows, total)."""
    rows, token, total = [], None, None
    while len(rows) < want:
        page = {"size": min(PAGE_SIZE, want - len(rows)), **({"token": token} if token else {})}
        d = treg.call("people_search", {"query": query, "pagination": page})
        leads, total = d.get("leads") or [], d.get("total", total)
        for lead in leads:
            url = normalize_profile_url(lead.get("profileUrl"))
            if url and url not in seen:
                seen.add(url)
                rows.append({**lead, "profileUrl": url})
        token = (d.get("pagination") or {}).get("token")
        if not leads or not token:
            break
    return rows[:want], total


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
    """One Google results call -> people rows with parsed name/title/company (M10 fills the rest)."""
    rows = []
    for r in ((treg.call("serp", {"q": query, "limit": 100}).get("output") or {}).get("results") or []):
        url = normalize_profile_url(r.get("url") or r.get("link"))
        if url and url not in seen:
            seen.add(url)
            name, title, company = parse_xray_title(r.get("title"))
            rows.append({"profileUrl": url, "fullName": name, "lastJobTitle": title, "lastCompanyName": company,
                         "snippet": r.get("snippet", ""), "source": "xray"})
    return rows


def map_person(p, search_query):
    """Discovery row (icypeas, or parsed X-ray) -> people CSV columns."""
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="LinkedIn people by role, industry and location -> lead CSV")
    ap.add_argument("--role", help="HR, recruiter, sales, marketing, founder, engineering, finance, IT, or any title")
    ap.add_argument("--titles", help="exact job titles, comma-separated (overrides --role)")
    ap.add_argument("--industry", help=f"{', '.join(INDUSTRIES)} or exact LinkedIn industry names, comma-separated")
    ap.add_argument("--location", help='a US state ("Texas"), a city ("Austin, TX") or any place text')
    ap.add_argument("--size", help='company headcount, e.g. "11-50" or "1000+"')
    ap.add_argument("--source", choices=("people", "xray"), default="people")
    ap.add_argument("--limit", type=int, default=100, help="people to find")
    ap.add_argument("--out", default="leads_linkedin.csv")
    ap.add_argument("--max-cost", type=float, default=1.0, help="hard spend limit in USD for this run")
    a = ap.parse_args(argv)
    titles = title_variants(a.role, a.titles)
    if not titles:
        ap.error("give --role or --titles")
    if Path(a.out).exists() and Path(a.out).stat().st_size:
        sys.exit(f"{a.out} already exists: choose another --out")

    treg, seen, stop = Treg(a.max_cost), set(), ""
    with LeadWriter(a.out, PEOPLE_COLUMNS, "person_linkedin_url") as out:
        try:
            if a.source == "people":
                query = build_query(titles, a.location, industry_names(a.industry), a.size)
                rows, total = people_search(treg, query, a.limit, seen)
                label = f"{a.role or a.titles} | {a.industry or 'any industry'} | {a.location or 'anywhere'}"
                print(f"people search: {total} matches in total")
                for p in rows:
                    out.write(map_person(p, label))
            else:
                for q in xray_queries(titles, a.location, a.industry):
                    if len(out.seen) >= a.limit:
                        break
                    for p in xray_search(treg, q, seen)[:a.limit - len(out.seen)]:
                        out.write(map_person(p, q))
        except (BudgetReached, OutOfBalance, TregError) as e:
            stop = str(e)
        n = len(out.seen)
    print(f"{n} people -> {a.out} | spent ${treg.spent_usd:.4f}" + (f" | stopped early: {stop}" if stop else ""))


if __name__ == "__main__":
    main()
