"""LinkedIn scraper: people by role, industry and US location (e.g. HR at tech companies in Texas), or companies,
written to a lead CSV.

  python linkedin_scraper.py --role HR --industry tech --location Texas --limit 100
  python linkedin_scraper.py --titles "Office Manager,Practice Manager" --location "Austin, TX" --source xray
  python linkedin_scraper.py --role HR --location Texas --limit 500 --verify --phone --dry-run
  python linkedin_scraper.py --mode company --keyword SaaS --location Texas --limit 200
  python linkedin_scraper.py --mode company --input company_urls.txt --paid-emails

People and companies come from a LinkedIn-data search through treg (icypeas), never from logging in to LinkedIn.
--source xray finds people with Google X-ray search instead (site:linkedin.com/in ...). M9 discovery; M10 checks
every person (current title, state) and fills the company domain; M11 finds each kept person's work email (and
phone); M12 the full command: company mode, --dry-run, --resume, 8 people or companies at a time.
"""
import argparse
import json
import re
import sys
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import nullcontext
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from common import (COMPANY_COLUMNS, ENDPOINTS, PEOPLE_COLUMNS, LeadSpend, LeadWriter, StopRun, Treg, TregError,
                    cities_of, crawl_site, domain_of, email_status, enrich_calls, enrich_paid, estimate,
                    normalize_phone, require, state_code, states)

require("people_search", "people_count", "serp", "people_enrich", "company_domain_from_linkedin", "company_by_name",
        "email_find_person", "email_find_person_fallback", "email_verify", "phone_find_person", "companies_search",
        "companies_count", "web_extract", "company_emails_by_domain")

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
COMPANY_RE = re.compile(r"linkedin\.com/company/([^/?#\s,;\"'|]+)", re.I)
# Words that say nothing about the role itself: "HR Manager" and "Head of HR" both match on "HR".
GENERIC_TITLE_WORDS = {"senior", "sr", "junior", "jr", "lead", "head", "manager", "director", "vp", "vice", "chief",
                       "officer", "specialist", "generalist", "partner", "business", "coordinator", "associate",
                       "assistant", "principal", "executive", "of", "the", "and", "global", "regional", "operations"}
FORMER_RE = re.compile(r"^\s*(former|ex[- ]|retired|previously)\b", re.I)
NOT_COMPANY_SITES = {"linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
                     "google.com", "sites.google.com", "bit.ly", "linktr.ee"}
COMPANY_SUFFIX_RE = re.compile(r"\b(inc|llc|l\.l\.c|ltd|co|corp|corporation|company|group|the|plc|lp|llp)\b\.?", re.I)
MIN_CITY_POP_FOR_MATCH = 20000  # "Dallas-Fort Worth" -> Dallas; smaller names are too ambiguous across states
WORKERS = 8  # people / companies handled in parallel (a routed email lookup or a website crawl takes 5-40 s)
# Dry-run rates measured on 20 HR people in Texas (M10, M11 in docs/endpoints.md); refine as real runs come in.
SHARE_KEPT = {"people": 1.0, "xray": 0.42}  # discovered people that pass the checks (20/20; X-ray 20/48)
SHARE_EMAIL_T1 = 0.45   # tier-1 email hits, billed (9/20)
SHARE_EMAIL_T2 = 0.73   # tier-2 hits among tier-1 misses, billed; its misses are free (8/11)
SHARE_EMAIL = 0.85      # people with an email to verify (17/20)
SHARE_PHONE = 0.65      # phone hits, billed (13/20)
XRAY_PER_QUERY = 30     # unique profiles per X-ray query (M9)


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


def normalize_company_url(url):
    """Any LinkedIn company URL -> 'https://www.linkedin.com/company/<slug or id>' ('' if it is not one)."""
    m = COMPANY_RE.search(url or "")
    return f"https://www.linkedin.com/company/{m.group(1).lower()}" if m else ""


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
        q["currentCompany.headcount"] = headcount(size)
    return q


def headcount(size):
    """--size "11-50" or "1000+" -> icypeas headcount range (people and company searches take the same)."""
    lo, _, hi = size.replace("+", "-").partition("-")
    return {">=": int(lo), **({"<=": int(hi)} if hi else {})}


def icypeas_search(treg, query, want, seen, token=None, capability="people_search", field="profileUrl",
                   norm=normalize_profile_url):
    """Page through an icypeas search (people, or companies with capability/field/norm) until `want` new rows, keyed
    by the normalized LinkedIn URL in `field`, or no more results. Returns (rows, total, next_token); next_token is
    None when there are no more pages. Tokens expire: an old one silently returns no rows."""
    rows, total = [], None
    while len(rows) < want:
        page = {"size": min(PAGE_SIZE, want - len(rows)), **({"token": token} if token else {})}
        # Per-row pricing: cap this call at the page's own price (+25% and a cent of slack), not the 200-row worst case.
        cap = page["size"] * ENDPOINTS[capability]["price_usd"] * 1.25 + 0.01
        d = treg.call(capability, {"query": query, "pagination": page}, max_cost=cap)
        leads, total = d.get("leads") or [], d.get("total", total)
        for lead in leads:
            url = norm(lead.get(field))
            if url and url not in seen:
                seen.add(url)
                rows.append({**lead, field: url})
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


# ---------------------------------------------------------------- work email and phone (M11)
def _found(capability, r):
    """Finder reply -> (email, other emails, provider, confidence); email is '' on a miss."""
    if capability == "email_find_person":  # blitzapi direct: found, email, all_emails [{email}]
        if not r.get("found"):
            return "", [], "", ""
        return r.get("email") or "", [e.get("email") for e in r.get("all_emails") or []], "blitzapi", ""
    o = r.get("output") or {}  # routed: output.email, confidence 0-1; the provider rides in _treg.served_by
    return o.get("email") or "", [], ((r.get("_treg") or {}).get("served_by") or "").split(".")[0], o.get("confidence")


def find_contact(lead, treg, fallback=True, verify=False, phone=False):
    """M11: the person's work email (and phone) from email-finder providers, never a guessed address.
    Tier 1: blitzapi by LinkedIn URL ($0.000495 per hit, misses free). Tier 2, tier-1 misses only: treg's routed
    finder by URL, name and domain, capped, per-hit providers only (see endpoints.json). With verify, a bounced
    address counts as a miss. TregError skips a step; StopRun (budget, balance, treg unreachable) propagates and stops the run."""
    url = lead["person_linkedin_url"]
    tiers = [("email_find_person", {"person_linkedin_url": url})]
    if fallback:
        ident = {"linkedin_url": url, "first_name": lead.get("first_name"), "last_name": lead.get("last_name"),
                 "domain": lead.get("domain")}
        tiers.append(("email_find_person_fallback", {k: v for k, v in ident.items() if v}))
    bounced = []
    for capability, params in tiers:
        try:
            email, others, source, confidence = _found(capability, treg.call(capability, params))
        except TregError:
            continue  # provider down or timed out: try the next tier
        if not email:
            continue
        email, status = email.lower(), ""
        if verify:
            try:
                status = email_status(treg.call("email_verify", {"email": email}).get("output"))
            except TregError:
                pass
            if status == "invalid":
                bounced.append(email)
                continue
        lead.update(email=email, all_emails=[email, *(e.lower() for e in others if e)], email_source=source,
                    email_status=status, email_confidence=confidence)
        break
    if bounced and not lead.get("email"):
        lead.update(all_emails=bounced, email_status="invalid")  # kept for reference, never as the email
    if phone:
        try:
            number = normalize_phone((treg.call("phone_find_person", {"linkedin_url": url}).get("output") or {})
                                     .get("phone"))
        except TregError:
            number = ""
        if number:
            lead.update(phone=number, all_phones=[number])
    return lead


def process(p, treg, titles, st, cache, search_query, contact=None):
    """One discovered person -> (lead or None, reject reason, how the domain was found, micro-USD spent on them).
    contact: find_contact options (fallback, verify, phone); None skips the email and phone lookups."""
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
    lead["domain"] = domain  # never the host of a LinkedIn or social link in the website field
    if domain and how != "website":
        lead["website"] = f"https://{domain}"
    if contact is not None:
        try:
            find_contact(lead, spend, **contact)
        except StopRun:
            raise
        except Exception as e:  # an odd provider answer must not lose the person: keep what was found
            treg.note(lead["person_linkedin_url"], e)
            return lead, f"email lookup failed ({type(e).__name__})", how, spend.micro
    return lead, "", how, spend.micro


# ---------------------------------------------------------------- company mode (M12)
def company_query(keyword=None, location=None, industries=(), size=None):
    """icypeas company filter: keywords in the profile, HQ location (same text as for people), industry, headcount."""
    q = {}
    if keyword:
        q["keyword"] = {"include": [k.strip() for k in keyword.split(",") if k.strip()]}
    place, _ = location_filter(location)
    if place:
        q["location"] = {"include": [place]}
    if industries:
        q["industry"] = {"include": list(industries)}
    if size:
        q["headcount"] = headcount(size)
    return q


def read_company_urls(path):
    """LinkedIn company URLs from a text or CSV file (any line holding one): normalized, in file order, deduped."""
    text = Path(path).read_text(encoding="utf-8-sig")
    return list(dict.fromkeys(normalize_company_url(m.group(0)) for m in COMPANY_RE.finditer(text)))


def map_company(row):
    """icypeas company row -> company CSV columns."""
    addr = row.get("address") or ""
    parts = [p.strip() for p in addr.split(",") if p.strip()]  # "[street,] [ZIP,] City, State, Country"
    size = row.get("headcountRange") or {}
    lo, hi = size.get("gte"), size.get("lte")
    website = row.get("website") or ""
    phone = normalize_phone(row.get("phone"))
    return {
        "company_name": row.get("name"), "website": website, "domain": _row_domain({"website": website}),
        "phone": phone, "all_phones": [phone], "address": addr, "headquarters": addr,
        "city": parts[-3] if len(parts) >= 3 and not parts[-3].isdigit() else "",
        "country": parts[-1] if len(parts) >= 2 else "", "source_url": normalize_company_url(row.get("url")),
        "linkedin_id": row.get("lid") or row.get("urn"), "tagline": row.get("slogan"),
        "description": row.get("description"), "industry": row.get("industry"),
        "company_size": f"{lo}-{hi}" if lo is not None and hi is not None else f"{lo}+" if lo is not None else "",
        "employees_on_linkedin": row.get("numberOfEmployees"), "founded_year": row.get("foundedYear"),
        "company_type": row.get("type"), "specialties": [s.get("value") for s in row.get("specialties") or []],
        "logo_url": row.get("pictureUrl"), "scraped_at": datetime.now().isoformat(timespec="seconds"),
    }


def _same_company(row, url):
    """Is a looked-up row the company of this --input URL (vanity name or numeric id)?"""
    slug = url.rsplit("/", 1)[-1]
    return slug in {(row.get("lid") or "").lower(), str(row.get("urn") or "").rsplit(":", 1)[-1],
                    normalize_company_url(row.get("url")).rsplit("/", 1)[-1]}


def process_company(row, treg, domains, hunter, enrich=True, paid_emails=False, verify=False):
    """One company -> (lead, problem, micro-USD spent). An --input URL is looked up first; one the company database
    does not know keeps just its URL. A missing domain comes from the LinkedIn page (M10). Then, unless enrich is off,
    the M4 website scrape and M5 paid email / verify, as in the Google scraper."""
    spend, problem = LeadSpend(treg), ""
    if row.get("lookup"):
        try:
            found, _, _ = icypeas_search(spend, {"companyId": {"include": [row["url"]]}}, 1, set(),
                                         capability="companies_search", field="url", norm=normalize_company_url)
        except TregError:
            found = []
        match = next((r for r in found if _same_company(r, row["url"])), None)
        row, problem = ({**match, "url": row["url"]}, "") if match else (row, "not in the company database")
    lead = map_company(row)
    if not lead["domain"]:
        lead["domain"], _ = company_domain({"lastCompanyUrl": lead["source_url"], "lastCompanyName": lead["company_name"]},
                                           spend, domains)
        lead["website"] = lead["website"] or (f"https://{lead['domain']}" if lead["domain"] else "")
    if enrich:
        try:
            site = crawl_site(lead["website"], treg=spend) if lead["website"] else \
                {"emails": [], "phones": [], "error": "no website"}
        except StopRun:
            raise
        except Exception as e:  # an odd website must not lose the company: keep its LinkedIn data
            treg.note(lead["source_url"], e)
            site = {"emails": [], "phones": [], "error": f"website scrape failed ({type(e).__name__})"}
        lead.update(enrich_paid(site, lead["domain"], spend, paid_emails, verify, hunter))
        lead["all_phones"] = [p for p in dict.fromkeys([lead["phone"], *site.get("phones", [])]) if p]
        lead["phone"] = lead["phone"] or next(iter(lead["all_phones"]), "")
        lead["socials"] = [site.get(k) for k in ("facebook", "instagram", "x", "youtube", "tiktok") if site.get(k)]
        lead["description"] = lead["description"] or site.get("site_description")
        problem = problem or site.get("error", "")
    return lead, problem, spend.micro


# ---------------------------------------------------------------- the command (M12)
def _pages(treg, query, seen, stats, need, label, **search):
    """icypeas rows as (row, search-cost share in micro-USD, label), bought one page at a time and only when the run
    asks for more, so the checks run before more rows are bought. A --resume pages past the rows already in the
    file again ($0.00038 each; tokens expire, so they cannot be saved), but never looks them up."""
    token = None
    while True:
        spend = LeadSpend(treg)
        rows, stats["matches"], token = icypeas_search(spend, query, min(PAGE_SIZE, max(1, need())), seen, token,
                                                       **search)
        yield from ((r, spend.micro / len(rows), label) for r in rows)
        if token is None:
            return


def _xray_rows(treg, titles, location, industry, seen):
    for q in xray_queries(titles, location, industry):
        spend = LeadSpend(treg)
        rows = xray_search(spend, q, seen)
        yield from ((r, spend.micro / len(rows), q) for r in rows)


def dry_run(a, titles, treg):
    """-> one line: how many match (free count call), how many would be looked up, the estimated cost."""
    if a.mode == "company":
        matches = len(read_company_urls(a.input)) if a.input else treg.call("companies_count", {"query": company_query(
            a.keyword, a.location, industry_names(a.industry), a.size)}).get("total") or 0
        n = min(a.limit, matches)
        calls = {"companies_search": n, **({} if a.no_enrich else enrich_calls(n, a.paid_emails, a.verify))}
    else:
        matches = None if a.source == "xray" else treg.call("people_count", {"query": build_query(
            titles, a.location, industry_names(a.industry), a.size)}).get("total") or 0
        n = a.limit if matches is None else min(a.limit, matches)
        found = n / SHARE_KEPT[a.source]
        calls = {"people_search": found} if a.source == "people" else \
            {"serp": found / XRAY_PER_QUERY, "people_enrich": found, "company_domain_from_linkedin": n}
        if not a.no_enrich:
            calls["email_find_person"] = n * SHARE_EMAIL_T1
            if not a.no_fallback:
                calls["email_find_person_fallback"] = n * (1 - SHARE_EMAIL_T1) * SHARE_EMAIL_T2
            if a.verify:
                calls["email_verify"] = n * SHARE_EMAIL
            if a.phone:
                calls["phone_find_person"] = n * SHARE_PHONE
    what, cost = "companies" if a.mode == "company" else "people", estimate(calls)
    return (f"dry run: {'X-ray has no free count;' if matches is None else f'{matches} {what} match;'} up to {n} "
            f"would be looked up -> estimated ${cost:.4f} (hard limit --max-cost ${a.max_cost:.2f}"
            + ("; raise it to get them all" if cost > a.max_cost else "") + f"). Spent ${treg.spent_usd:.4f} "
            "(the match count is free).")


def main(argv=None):
    ap = argparse.ArgumentParser(description="LinkedIn people (or companies) by role, industry and location -> lead CSV")
    ap.add_argument("--mode", choices=("people", "company"), default="people")
    ap.add_argument("--role", help="people: HR, recruiter, sales, marketing, founder, engineering, finance, IT, or any title")
    ap.add_argument("--titles", help="people: exact job titles, comma-separated (overrides --role)")
    ap.add_argument("--keyword", help='company: words in the company profile, comma-separated, e.g. "SaaS"')
    ap.add_argument("--input", help="company: a file of LinkedIn company URLs (one per line, or any CSV holding them)")
    ap.add_argument("--industry", help=f"{', '.join(INDUSTRIES)} or exact LinkedIn industry names, comma-separated")
    ap.add_argument("--location", help='a US state ("Texas"), a city ("Austin, TX") or any place text')
    ap.add_argument("--size", help='company headcount, e.g. "11-50" or "1000+"')
    ap.add_argument("--source", choices=("people", "xray"), default="people",
                    help="people: the people database (default) or Google X-ray search")
    ap.add_argument("--limit", type=int, default=100, help="total people (or companies) wanted in the output file")
    ap.add_argument("--out", help="default leads_linkedin.csv, or leads_linkedin_companies.csv in company mode")
    ap.add_argument("--max-cost", type=float, default=1.0, help="hard spend limit in USD for this run")
    ap.add_argument("--dry-run", action="store_true", help="print the match count and estimated cost; spends nothing")
    ap.add_argument("--resume", action="store_true", help="continue an existing --out file; its rows are never looked up again")
    ap.add_argument("--no-enrich", action="store_true", help="source data only: no email, phone or website lookups")
    ap.add_argument("--no-fallback", action="store_true", help="people: tier-1 email finder only (skip the routed finder)")
    ap.add_argument("--verify", action="store_true", help="verify each found email (up to $0.00124 each)")
    ap.add_argument("--phone", action="store_true", help="people: also look up each person's phone (misses free)")
    ap.add_argument("--paid-emails", action="store_true", help="company: Hunter lookup when the website shows no email")
    a = ap.parse_args(argv)
    company, titles = a.mode == "company", title_variants(a.role, a.titles)
    if company and not (a.input or a.keyword or a.industry or a.location):
        ap.error("company mode needs --input, or --keyword / --industry / --location")
    if company and (a.phone or a.no_fallback):
        ap.error("--phone and --no-fallback are for people mode")
    if not company and not titles:
        ap.error("give --role or --titles")
    if not company and (a.keyword or a.input or a.paid_emails):
        ap.error("--keyword, --input and --paid-emails need --mode company")
    if a.no_enrich and (a.verify or a.phone or a.paid_emails):
        ap.error("--no-enrich cannot be combined with --verify, --phone or --paid-emails")
    if a.input and not Path(a.input).is_file():
        ap.error(f"--input {a.input} not found")
    if a.dry_run:
        try:
            print(dry_run(a, titles, Treg(a.max_cost)))
        except (StopRun, TregError) as e:  # the free count call failed: one line, nothing spent
            sys.exit(f"dry run failed: {e}")
        return
    out_path = a.out or ("leads_linkedin_companies.csv" if company else "leads_linkedin.csv")
    if Path(out_path).exists() and Path(out_path).stat().st_size and not a.resume:
        sys.exit(f"{out_path} already exists: add --resume to continue it, or choose another --out")

    treg, what, ctrl_c = Treg(a.max_cost), "companies" if company else "people", "stopped with Ctrl+C"
    _, st = location_filter(a.location)
    contact = None if a.no_enrich else dict(fallback=not a.no_fallback, verify=a.verify, phone=a.phone)
    stats, reasons, domains, emails, problems = Counter(), Counter(), Counter(), Counter(), Counter()
    cache, hunter = {}, {}  # ponytail: shared by the threads without a lock; two at once may both pay for one lookup
    pending, stop = {}, ""  # future -> (row, search-cost share, search label)
    rejects_path = treg.log_path.with_name(treg.log_path.stem + "-rejected.jsonl")
    columns, key = (COMPANY_COLUMNS, "source_url") if company else (PEOPLE_COLUMNS, "person_linkedin_url")

    with LeadWriter(out_path, columns, key) as out, \
            (nullcontext() if company else open(rejects_path, "a", encoding="utf-8")) as rejects:
        resumed, seen = len(out.seen), set(out.seen)  # rows already in the file are skipped before any lookup
        quota = max(0, a.limit - resumed)
        need = lambda: quota - stats["kept"] - len(pending)
        if company and a.input:
            rows = (({"url": u, "lookup": True}, 0, "") for u in read_company_urls(a.input) if u not in seen)
        elif company:
            rows = _pages(treg, company_query(a.keyword, a.location, industry_names(a.industry), a.size), seen, stats,
                          need, "", capability="companies_search", field="url", norm=normalize_company_url)
        elif a.source == "xray":
            rows = _xray_rows(treg, titles, a.location, a.industry, seen)
        else:
            label = f"{a.role or a.titles} | {a.industry or 'any industry'} | {a.location or 'anywhere'}"
            rows = _pages(treg, build_query(titles, a.location, industry_names(a.industry), a.size), seen, stats,
                          need, label)
        if company:
            work = lambda item: process_company(item[0], treg, cache, hunter, not a.no_enrich, a.paid_emails, a.verify)
        else:
            work = lambda item: process(item[0], treg, titles, st, cache, item[2], contact)
        pool = ThreadPoolExecutor(WORKERS)

        def cancel():
            for f in pending:
                f.cancel()  # only work not started yet: running lookups finish and are written

        def start_more():
            nonlocal stop
            while not stop and len(pending) < WORKERS and need() > 0:
                try:
                    item = next(rows, None)
                except (StopRun, TregError) as e:  # a search page could not be bought
                    stop = str(e)
                    return
                except Exception as e:  # an odd search answer: stop with one line, details in the run log
                    treg.note("search page", e)
                    stop = f"search failed ({type(e).__name__}; see the run log)"
                    return
                if item is None:
                    return
                pending[pool.submit(work, item)] = item

        def write(lead, share, spent):
            lead["cost_usd"] = round((share + spent) / 1e6, 6)
            if not out.write(lead):
                return False
            stats["kept"] += 1
            stats["email"] += bool(lead.get("email"))
            stats["phone"] += bool(lead.get("phone"))
            stats["website"] += bool(lead.get("website"))
            if stats["kept"] % 10 == 0:
                print(f"  {stats['kept']} {what} | {stats['email']} with email | spent ${treg.spent_usd:.4f}")
            return True

        def finish(item, f):
            nonlocal stop
            try:
                result = f.result()
            except StopRun as e:
                stop = stop or str(e)  # this row is dropped unwritten, so --resume redoes it
                return cancel()
            except Exception as e:  # a bug before the row had any data: lose this one row, never the run
                treg.note(item[0].get("profileUrl") or item[0].get("url"), e)
                problems[f"failed ({type(e).__name__})"] += 1
                return
            if company:
                lead, problem, spent = result
                if problem:
                    problems[re.sub(r" \(.*", "", problem)] += 1
                write(lead, item[1], spent)
                return
            lead, reason, how, spent = result
            stats["found"] += 1
            if lead is not None and reason:  # kept with what was found before a step failed
                problems[re.sub(r" \(.*", "", reason)] += 1
            if lead is None:
                reasons[reason.split(":")[0]] += 1
                p = item[0]
                rejects.write(json.dumps({"profile": p.get("profileUrl"), "title": p.get("lastJobTitle"),
                                          "location": p.get("address"), "reason": reason}) + "\n")
            elif write(lead, item[1], spent):
                domains[how or "none"] += 1
                emails[lead.get("email_source") or "none"] += 1
            if not stop and stats["found"] >= 50 and stats["kept"] < stats["found"] * 0.1:
                # Guard against paging (and paying) through thousands of rows that all fail the checks.
                stop = (f"only {stats['kept']} of {stats['found']} people pass the checks; "
                        f"check --role/--titles/--location (dropped people: {rejects_path})")
                cancel()

        try:
            while True:
                try:
                    start_more()
                    if not pending:
                        break
                    done, _ = wait(list(pending), return_when=FIRST_COMPLETED)
                    for f in done:
                        item = pending.pop(f)
                        if not f.cancelled():
                            finish(item, f)
                except KeyboardInterrupt:
                    if stop == ctrl_c:
                        raise
                    stop = ctrl_c
                    cancel()
                    print(f"  Ctrl+C: finishing the {what} in progress so --resume never pays for them again")
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    n = stats["kept"]
    pct = lambda k: f"{stats[k]}/{n} ({100 * stats[k] // n}%)" if n else "0"
    counts = lambda c: ", ".join(f"{k} {v}" for k, v in c.most_common()) or "-"
    print(f"\n{n} new {what} -> {out_path}" + (f" (+{resumed} already in the file)" if resumed else "")
          + (f" | {stats['matches']} match the search" if stats["matches"] else ""))
    if company:
        print(f"  email {'not looked up (--no-enrich)' if a.no_enrich else pct('email')} | phone {pct('phone')} "
              f"| website {pct('website')}")
    else:
        print(f"  {stats['found']} checked, {stats['found'] - n} dropped"
              + (f" ({', '.join(f'{k} x{v}' for k, v in reasons.most_common())})" if reasons else ""))
        print(f"  company domain: {counts(domains)}")
        print(f"  work email: {'not looked up (--no-enrich)' if a.no_enrich else counts(emails)}"
              + (f" | phone: {stats['phone']} of {n}" if a.phone else ""))
    if problems:
        print("  problems: " + ", ".join(f"{k} x{v}" for k, v in problems.most_common()))
    print(f"  spent ${treg.spent_usd:.4f}" + (f" (${treg.spent_usd / n:.5f} per row)" if n else "")
          + f" | run log {treg.log_path}" + ("" if company else f" | dropped people {rejects_path}"))
    if treg.failures():
        print(treg.failures())
    if stop:
        print(f"  stopped early: {stop}")


if __name__ == "__main__":
    main()
