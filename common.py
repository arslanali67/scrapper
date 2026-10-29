"""Shared code for both scrapers. The Treg class is the only way any scraper talks to treg."""
import csv
import json
import os
import re
import threading
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import phonenumbers

ROOT = Path(__file__).parent
ENDPOINTS = json.loads((ROOT / "endpoints.json").read_text(encoding="utf-8"))


class TregError(Exception):
    """A call that failed without breaking the run: skip this lead and continue."""

    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


class BudgetReached(Exception):
    """The next call could push spend past --max-cost. Stop the run cleanly."""


class OutOfBalance(Exception):
    """treg balance is empty (402). Stop the run; the message holds the top-up URL."""


def load_token():
    tok = os.environ.get("TREG_TOKEN")
    env = ROOT / ".env"
    if not tok and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("TREG_TOKEN="):
                tok = line.split("=", 1)[1].strip()
    if not tok:
        raise SystemExit("TREG_TOKEN missing: put it in .env (see .env.example)")
    return tok


class Treg:
    RETRY_DELAYS = (1, 4, 10)  # seconds between attempts on 429 / 5xx / connect errors

    def __init__(self, max_cost_usd, log_dir=ROOT / "runs", transport=None, token=None):
        self.budget_micro = round(max_cost_usd * 1e6)
        self.spent_micro = 0
        self.client = httpx.Client(base_url="https://treg.to", timeout=180, transport=transport,
                                   headers={"X-Treg-Token": token or load_token()})
        Path(log_dir).mkdir(exist_ok=True)
        self.log_path = Path(log_dir) / f"{datetime.now():%Y%m%d-%H%M%S}.jsonl"
        self._lock = threading.Lock()

    @property
    def spent_usd(self):
        return self.spent_micro / 1e6

    def call(self, capability, params=None):
        """Call the endpoint chosen for `capability` in endpoints.json. Returns the parsed JSON. Thread-safe."""
        # ponytail: one lock around the whole call keeps the budget check exact across threads but runs treg calls
        # one at a time; switch to reserve-then-settle accounting if parallel treg calls are ever needed for speed.
        with self._lock:
            return self._call(capability, params)

    def _call(self, capability, params):
        ep = ENDPOINTS[capability]
        cap_micro = round(ep["max_cost_usd"] * 1e6)
        # Worst case is the per-call cap, so checking it here means --max-cost can never be overshot.
        if self.spent_micro + cap_micro > self.budget_micro:
            raise BudgetReached(f"budget ${self.budget_micro / 1e6:.2f} reached (spent ${self.spent_usd:.4f})")
        headers = {"X-Treg-Route-Max-Cost": str(ep["max_cost_usd"]), **ep.get("headers", {})}
        get = ep["method"] == "GET"
        req = dict(params=params if get else None, json=None if get else params, headers=headers,
                   timeout=ep.get("timeout_s", 180))
        t0 = time.time()
        for delay in (*self.RETRY_DELAYS, None):
            try:
                r = self.client.request(ep["method"], f"/call/{ep['id']}", **req)
            except httpx.ConnectError as e:  # nothing reached treg, safe to retry
                if delay is None:
                    raise TregError(f"{capability}: cannot reach treg ({e})")
                time.sleep(delay)
                continue
            except httpx.TimeoutException:
                # The call may still finish (and bill) server-side, so never retry it.
                self._log(capability, ep["id"], "timeout", 0, None, t0)
                raise TregError(f"{capability}: timed out")
            if (r.status_code == 429 or r.status_code >= 500) and r.status_code not in (502, 503) and delay is not None:
                time.sleep(delay)
                continue
            break
        cost = int(r.headers.get("X-Treg-Cost-Micro") or 0)
        self.spent_micro += cost
        try:
            body = r.json()
        except ValueError:
            body = {"_text": r.text[:500]}
        served = r.headers.get("X-Treg-Served-By") or (body.get("_treg") or {}).get("served_by") \
            if isinstance(body, dict) else None
        self._log(capability, ep["id"], r.status_code, cost, served, t0)
        if r.status_code < 400:
            return body
        detail = body.get("detail", body) if isinstance(body, dict) else body
        err = detail.get("error") if isinstance(detail, dict) else None
        if r.status_code == 402 and err != "route_max_cost":
            topup = detail.get("topup_url", "") if isinstance(detail, dict) else ""
            raise OutOfBalance(f"treg balance is empty. Top up: {topup or 'treg topup'}")
        # 502 too large, 503 provider out, 402 route_max_cost, 4xx bad input: not charged / skip lead.
        raise TregError(f"{capability}: HTTP {r.status_code} {str(detail)[:200]}", r.status_code)

    def _log(self, capability, endpoint, status, cost, served_by, t0):
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "capability": capability, "endpoint": endpoint,
               "status": status, "cost_micro": cost, "served_by": served_by, "secs": round(time.time() - t0, 1)}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


def estimate(calls):
    """Dry-run cost in USD: {capability: expected number of calls} x price_usd from endpoints.json."""
    return sum(ENDPOINTS[cap]["price_usd"] * n for cap, n in calls.items())


# ---------------------------------------------------------------- lead CSV (M3)
# The first 12 columns are identical in every CSV so files from both scrapers can be stacked.
SHARED_COLUMNS = ["company_name", "website", "domain", "email", "all_emails", "email_source", "phone", "all_phones",
                  "address", "city", "country", "source_url"]
GOOGLE_COLUMNS = SHARED_COLUMNS + [
    "place_id", "category", "all_categories", "state", "postal_code", "latitude", "longitude", "rating",
    "review_count", "opening_hours", "google_maps_url", "facebook", "instagram", "linkedin", "x", "youtube", "tiktok",
    "site_description", "services", "team_members", "contact_form_url", "site_address", "search_query",
    "search_location", "email_status", "scraped_at", "cost_usd"]
PEOPLE_COLUMNS = SHARED_COLUMNS + [
    "full_name", "first_name", "last_name", "job_title", "headline", "person_location", "person_linkedin_url",
    "company_linkedin_url", "industry", "company_size", "email_status", "email_confidence", "search_query",
    "scraped_at", "cost_usd"]
COMPANY_COLUMNS = SHARED_COLUMNS + [
    "linkedin_id", "tagline", "description", "industry", "company_size", "employees_on_linkedin", "headquarters",
    "all_locations", "founded_year", "company_type", "specialties", "followers", "logo_url", "funding_info",
    "socials", "email_status", "scraped_at", "cost_usd"]

_NUMERIC = re.compile(r"^[+-]?[\d .()/-]+$")


def cell(value):
    """One CSV cell: lists joined with ' | ' (deduped, order kept), None -> '', formula-looking text defused."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        value = " | ".join(dict.fromkeys(str(v).strip() for v in value if v not in (None, "")))
    value = str(value).strip()
    # Scraped text that starts with = + - @ would run as a formula in Excel; phone numbers and plain numbers are fine.
    if value[:1] in ("=", "+", "-", "@") and not _NUMERIC.match(value):
        value = "'" + value
    return value


def domain_of(url_or_email):
    """'https://www.Acme.com/about' or 'jo@acme.com' -> 'acme.com'; '' when there is nothing usable."""
    s = (url_or_email or "").strip().lower()
    if "@" in s and "/" not in s:
        s = s.rsplit("@", 1)[1]
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s).split("/")[0].split("?")[0].split(":")[0]
    s = s.removeprefix("www.")
    return s if "." in s else ""


def normalize_phone(raw, region="US"):
    """Any phone text -> international format ('+1 512-474-1234'), or '' when it is not a valid number.
    Not bare E.164: Excel turns '+15124741234' into the number 1.51E+10, but keeps this format as text."""
    try:
        n = phonenumbers.parse(str(raw or ""), region)
    except phonenumbers.NumberParseException:
        return ""
    if not phonenumbers.is_valid_number(n):
        return ""
    return phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.INTERNATIONAL)


class LeadWriter:
    """Appends one lead per row and flushes each row, so a crash loses at most the lead in progress.
    Resume: keys already in an existing file are loaded so callers can skip them before any paid call."""

    def __init__(self, path, columns, key):
        self.path, self.columns, self.key = Path(path), columns, key
        self.seen = set()
        if self.path.exists() and self.path.stat().st_size:
            with open(self.path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames != columns:
                    raise SystemExit(f"{self.path} has different columns; choose another --out file")
                self.seen = {row[key] for row in reader if row.get(key)}
            self.file = open(self.path, "a", newline="", encoding="utf-8")  # no second BOM mid-file
            self.writer = csv.DictWriter(self.file, columns, extrasaction="ignore")
        else:
            self.file = open(self.path, "w", newline="", encoding="utf-8-sig")  # BOM so Excel reads UTF-8
            self.writer = csv.DictWriter(self.file, columns, extrasaction="ignore")
            self.writer.writeheader()

    def has(self, key_value):
        return cell(key_value) in self.seen

    def write(self, lead):
        """Write one lead dict. Returns False (and writes nothing) when its key is already in the file."""
        row = {c: cell(lead.get(c)) for c in self.columns}
        if not row[self.key]:
            raise ValueError(f"lead has no {self.key}")
        if row[self.key] in self.seen:
            return False
        self.writer.writerow(row)
        self.file.flush()
        self.seen.add(row[self.key])
        return True

    def close(self):
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ---------------------------------------------------------------- website deep scrape (M4)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
MAX_PAGES = 8  # internal pages per site, besides the homepage
# Page kinds in priority order: the first link matching each kind is crawled before any second match.
PAGE_KINDS = [("contact", r"contact"), ("about", r"about|who-we-are|our-story"),
              ("team", r"team|staff|doctors|providers|our-people|leadership|meet-"),
              ("services", r"service|treatment|what-we-do|solutions|practice-area"),
              ("locations", r"location|offices|find-us|directions"), ("impressum", r"impressum|imprint")]
SOCIAL = {
    "facebook": r"https?://(?:www\.|m\.)?facebook\.com/(?!sharer|share|plugins|tr\b|dialog)[\w.\-/]+",
    "instagram": r"https?://(?:www\.)?instagram\.com/(?!p/|share)[\w.\-]+",
    "linkedin": r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in|school)/[\w\-%.]+",
    "x": r"https?://(?:www\.)?(?:twitter|x)\.com/(?!intent|share|home)\w+",
    "youtube": r"https?://(?:www\.)?youtube\.com/(?:channel/|c/|user/|@)[\w\-.]+",
    "tiktok": r"https?://(?:www\.)?tiktok\.com/@[\w.\-]+",
}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}")
OBFUSCATED_RE = re.compile(r"([\w.+-]+)\s*[\[({]\s*at\s*[\])}]\s*([\w-]+(?:\s*[\[({]\s*dot\s*[\])}]\s*[\w-]+)+)", re.I)
DOT_RE = re.compile(r"\s*[\[({]\s*dot\s*[\])}]\s*", re.I)
JUNK_EMAIL = re.compile(r"\.(png|jpe?g|gif|webp|svg|css|js|ico)$|@(example|domain|email|yourdomain|yoursite|sentry"
                        r"|[\w.-]*wixpress|[\w.-]*sentry[\w.-]*)\.|^(your|name|email|user(name)?|test|no-?reply|"
                        r"[0-9a-f]{20,})@", re.I)
GENERIC_LOCAL = {"info", "contact", "hello", "office", "admin", "sales", "support", "team", "enquiries", "inquiries",
                 "mail", "frontdesk", "reception", "appointments", "billing", "careers", "jobs", "hr", "marketing",
                 "help", "service", "customerservice", "general"}
ADDRESS_RE = re.compile(r"\d{1,6} [\w .'#-]{2,60}?,? (?:Suite|Ste\.?|#)?[\w .'#-]*?,?\s*[A-Za-z .'-]{2,40},\s*"
                        r"(?:[A-Z]{2}|[A-Z][a-z]+(?: [A-Z][a-z]+)?)\s+\d{5}(?:-\d{4})?")
NAME_RE = re.compile(r"^(?:Dr\.?\s+)?[A-Z][a-z'\-]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z'\-]+){1,2}(?:,\s*[A-Z.]{2,8})?$")
ROLE_RE = re.compile(r"\b(dentist|hygienist|orthodontist|doctor|physician|nurse|therapist|surgeon|assistant|manager|"
                     r"director|owner|founder|co-founder|ceo|cfo|coo|cto|president|partner|principal|attorney|lawyer|"
                     r"associate|specialist|technician|coordinator|receptionist|administrator|officer|head|lead|chief|"
                     r"consultant|engineer|agent|broker|advisor|accountant|recruiter|hr|chair|vp|vice|executive|"
                     r"dds|dmd|md|cpa|esq)\b", re.I)
BOT_WALL_RE = re.compile(r"<title>\s*(Just a moment|Attention Required|Access denied)|cf-chl-|captcha-delivery|"
                         r"_Incapsula_Resource|px-captcha", re.I)
SERVICE_STOP = re.compile(r"contact|call|appointment|schedule|review|testimonial|blog|news|faq|about|our services|"
                          r"location|hours|insurance|welcome|menu|^(what|how|why|when|who|get|learn|read|view|see|"
                          r"more|book|request|meet|need|our)\b|\?|^\d", re.I)
# Words that make a capitalized two/three-word line a menu item or business name rather than a person.
NAME_STOP = {"our", "meet", "about", "view", "book", "same", "dental", "dentistry", "board", "get", "learn", "read",
             "call", "schedule", "contact", "why", "what", "how", "new", "free", "emergency", "family", "general",
             "cosmetic", "your", "the", "welcome", "request", "find", "see", "join", "patient", "patients", "office",
             "home", "services", "insurance", "financing", "special", "smile", "smiles", "teeth", "tooth", "care",
             "clinic", "center", "centre", "group", "health", "team", "practice", "company", "inc", "llc", "austin",
             "texas", "more", "all", "and", "for", "with", "us", "today", "now", "online", "story", "doctor", "doctors"}


class _Page(HTMLParser):
    """Collects what M4 needs from one HTML page in a single pass."""
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "tr", "br", "section", "article",
             "header", "footer", "address", "strong", "b", "dt", "dd"}
    SKIP = {"script", "style", "noscript", "svg", "template"}

    def __init__(self, url):
        super().__init__(convert_charrefs=True)
        self.url = url
        self.links, self.mailtos, self.tels, self.cfemails, self.jsonld = [], [], [], [], []
        self.meta_desc, self.headings, self.blocks, self.email_form = "", [], [], False
        self._skip = self._form = 0
        self._ld = self._link = self._heading = None
        self._block = []

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if a.get("data-cfemail"):
            self.cfemails.append(a["data-cfemail"])
        if tag == "script" and "ld+json" in a.get("type", ""):
            self._ld = []
            return
        if tag in self.SKIP:
            self._skip += 1
            return
        if tag in self.BLOCK:
            self._flush()
        if tag == "meta" and not self.meta_desc and "description" in (a.get("name", ""), a.get("property", "")[3:]):
            self.meta_desc = a.get("content", "").strip()
        elif tag == "a" and a.get("href"):
            href = a["href"].strip()
            low = href.lower()
            if low.startswith("mailto:"):
                self.mailtos.append(unquote(href[7:].split("?")[0]))
            elif low.startswith("tel:"):
                self.tels.append(unquote(href[4:]))
            elif "/cdn-cgi/l/email-protection#" in low:
                self.cfemails.append(href.split("#", 1)[1])
            else:
                self._link = [urljoin(self.url, href).split("#")[0], []]
        elif tag in ("h1", "h2", "h3", "h4"):
            self._heading = [int(tag[1]), []]
        elif tag == "form":
            self._form += 1
        elif tag == "input" and self._form and ("email" in a.get("type", "") or "email" in a.get("name", "").lower()):
            self.email_form = True

    def handle_endtag(self, tag):
        if tag == "script" and self._ld is not None:
            self.jsonld.append("".join(self._ld))
            self._ld = None
            return
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "a" and self._link:
            self.links.append((self._link[0], " ".join("".join(self._link[1]).split())))
            self._link = None
        elif tag in ("h1", "h2", "h3", "h4") and self._heading:
            text = " ".join("".join(self._heading[1]).split())
            if text:
                self.headings.append((self._heading[0], text))
            self._heading = None
        elif tag == "form":
            self._form = max(0, self._form - 1)
        if tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._ld is not None:
            self._ld.append(data)
        elif not self._skip:
            self._block.append(data)
            for open_part in (self._link, self._heading):
                if open_part:
                    open_part[1].append(data)

    def _flush(self):
        text = " ".join("".join(self._block).split())
        if text:
            self.blocks.append(text)
        self._block = []

    def close(self):
        super().close()
        self._flush()

    @property
    def text(self):
        return "\n".join(self.blocks)


def _parse(url, html):
    page = _Page(url)
    try:
        page.feed(html)
        page.close()
    except Exception:  # malformed HTML: keep whatever was parsed so far
        pass
    return page


def _decode_cfemail(hexstr):
    """Cloudflare email protection: first byte is the XOR key for the rest."""
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except ValueError:
        return ""


def _jsonld_objects(page):
    """Every dict inside the page's JSON-LD blocks (handles lists and @graph)."""
    out, stack = [], []
    for raw in page.jsonld:
        try:
            stack.append(json.loads(raw, strict=False))  # strict=False: sites put raw newlines/tabs inside strings
        except ValueError:
            continue
    while stack:
        o = stack.pop()
        if isinstance(o, list):
            stack.extend(o)
        elif isinstance(o, dict):
            out.append(o)
            stack.extend(v for v in o.values() if isinstance(v, (dict, list)))
    return out


def _same_site(host, domain):
    return bool(host and domain) and (host == domain or host.endswith("." + domain) or domain.endswith("." + host))


def _contact_first(pages):
    return sorted(pages, key=lambda kp: kp[0] != "contact")


def _pick_pages(home, domain):
    """Up to MAX_PAGES internal links: one per kind first (PAGE_KINDS order), then further matches."""
    cand = []
    for url, text in home.links:
        u = urlparse(url)
        if u.scheme in ("http", "https") and _same_site(domain_of(url), domain) and u.path.strip("/") \
                and not re.search(r"\.(pdf|jpe?g|png|gif|zip|docx?)$", u.path, re.I) \
                and not re.search(r"terms|privacy|policy|cookie|accessibility|login|cart|checkout|/blog/|/tag/|"
                                  r"/category/", u.path, re.I):
            cand.append((url.split("?")[0], f"{u.path} {text}".lower()))
    picked, kinds = {}, set()
    for second_pass in (False, True):
        for kind, pat in PAGE_KINDS:
            for url, hay in cand:
                if len(picked) >= MAX_PAGES:
                    return picked
                if url in picked or not re.search(pat, hay) or (kind in kinds and not second_pass):
                    continue
                picked[url] = kind
                kinds.add(kind)
                if not second_pass:
                    break
    return picked


def extract_contacts(pages, domain, region="US"):
    """pages: list of (kind, _Page). Returns ranked emails, phones and social links."""
    found, phones, socials = [], [], {}
    for _, p in pages:
        objs = _jsonld_objects(p)
        found += p.mailtos + [_decode_cfemail(h) for h in p.cfemails]
        found += [o["email"].removeprefix("mailto:") for o in objs if isinstance(o.get("email"), str)]
        found += EMAIL_RE.findall(p.text) + [e for raw in p.jsonld for e in EMAIL_RE.findall(raw)]  # broken JSON-LD too
        found += [f"{m.group(1)}@{DOT_RE.sub('.', m.group(2))}" for m in OBFUSCATED_RE.finditer(p.text)]
        phones += [o["telephone"] for o in objs if isinstance(o.get("telephone"), str)] + p.tels
        same_as = [s for o in objs for s in (o.get("sameAs") if isinstance(o.get("sameAs"), list) else [o.get("sameAs")])
                   if isinstance(s, str)]
        hrefs = [u for u, _ in p.links] + same_as
        for name, pat in SOCIAL.items():
            hit = socials.get(name) or next((h for h in hrefs if re.match(pat, h, re.I)), None)
            if hit:
                socials[name] = hit.split("?")[0].rstrip("/")
    for _, p in _contact_first(pages):  # free-text phones last, contact page first
        phones += [m.raw_string for m in phonenumbers.PhoneNumberMatcher(p.text, region)]
    emails = [e for e in dict.fromkeys(e.strip(" .;,'\"<>").lower() for e in found)
              if EMAIL_RE.fullmatch(e) and not JUNK_EMAIL.search(e)]
    own = [e for e in emails if _same_site(e.split("@")[1], domain)]
    # Same-domain emails win (named before generic); other domains (e.g. gmail) only when there is no own one.
    emails = sorted(own, key=lambda e: e.split("@")[0] in GENERIC_LOCAL) if own else emails
    phones = [p for p in dict.fromkeys(normalize_phone(x, region) for x in phones) if p][:5]
    return {"emails": emails, "phones": phones, **socials}


def extract_details(pages):
    """Company details: description, services, team members, contact form, address."""
    by_kind = {}
    for kind, p in pages:
        by_kind.setdefault(kind, []).append(p)
    home = by_kind["home"][0]
    desc = home.meta_desc or next((b for p in by_kind.get("about", []) for b in p.blocks if len(b) >= 80), "")
    good = lambda t: 3 <= len(t) <= 40 and len(t.split()) <= 6 and not SERVICE_STOP.search(t)
    # Best source: the site's own menu links into a services section (/services/dental-implants/).
    services = [t for u, t in home.links if good(t) and re.search(
        r"^/(services?|treatments?|what-we-do|solutions|practice-areas?)/[^/]+", urlparse(u).path, re.I)]
    if len(set(services)) < 3:  # no such menu: headings on the services pages
        services = [t for p in by_kind.get("services", []) for lvl, t in p.headings if lvl in (2, 3) and good(t)]
    team = []
    for p in by_kind.get("team", []):
        # ponytail: naive "name block followed by short title block" heuristic; misses card layouts that
        # put the title before the name. Upgrade to per-site selectors only if team data turns out to matter.
        for name, title in zip(p.blocks, p.blocks[1:]):
            core = re.sub(r"^Dr\.?\s+|,.*$", "", name)  # "Dr. Jane Smith, DDS" -> "Jane Smith"
            if NAME_RE.match(name) and not ROLE_RE.search(core) and not NAME_STOP & set(core.lower().split()) \
                    and len(title) <= 60 and ROLE_RE.search(title) and not title.isupper():
                team.append(f"{name} - {title}")
    form = next((p.url for _, p in _contact_first(pages) if p.email_form), "")
    addr = ""
    for o in (o for _, p in pages for o in _jsonld_objects(p)):
        a = o.get("address")
        if isinstance(a, dict):
            addr = ", ".join(str(a[k]) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode")
                             if a.get(k))
        elif isinstance(a, str):
            addr = a
        if addr:
            break
    if not addr:
        m = next((m for _, p in _contact_first(pages) for m in [ADDRESS_RE.search(p.text)] if m), None)
        addr = m.group(0) if m else ""
    return {"site_description": desc[:500], "services": list(dict.fromkeys(services))[:15],
            "team_members": list(dict.fromkeys(team))[:20], "contact_form_url": form, "site_address": addr}


def crawl_site(url, region="US", treg=None, client=None):
    """M4: scrape one business website for free; treg's page extract only for JavaScript-only sites.
    Returns emails (ranked), email, phones, social links and company details; 'error' says why a site was unreadable."""
    out = {"emails": [], "email": "", "phones": [], "pages_crawled": 0, "js_fallback": False, "error": ""}
    if not url:
        return {**out, "error": "no website"}
    if not re.match(r"https?://", url, re.I):
        url = "https://" + url
    own_client = client is None
    client = client or httpx.Client(headers={"User-Agent": UA}, timeout=10, follow_redirects=True)
    try:
        robots = RobotFileParser()
        try:
            r = client.get(urljoin(url, "/robots.txt"))
            robots.parse(r.text.splitlines() if r.status_code == 200 else [])
        except httpx.HTTPError:
            robots.parse([])

        def fetch(u):
            """(page, None) or (None, why)."""
            if not robots.can_fetch("*", u):
                return None, "disallowed by robots.txt"
            try:
                r = client.get(u)
            except httpx.HTTPError as e:
                return None, f"unreachable ({type(e).__name__})"
            if r.status_code in (403, 429, 503) or BOT_WALL_RE.search(r.text[:3000]):
                return None, "blocked (bot check)"
            if r.status_code >= 400:
                return None, f"unavailable (HTTP {r.status_code})"
            if "html" not in r.headers.get("content-type", "text/html"):
                return None, "not an HTML page"
            return _parse(str(r.url), r.text), None

        home, why = fetch(url)
        if home is None:  # sites that block bots are skipped, never worked around
            return {**out, "error": f"homepage {why}"}
        domain = domain_of(home.url) or domain_of(url)  # after redirects
        picked = _pick_pages(home, domain)
        if not any(k in picked.values() for k in ("contact", "about")):
            picked.update({urljoin(home.url, "/contact"): "contact", urljoin(home.url, "/about"): "about"})
        pages = [("home", home)] + [(kind, p) for u, kind in picked.items() for p in [fetch(u)[0]] if p]
        contacts = extract_contacts(pages, domain, region)
        if treg and not contacts["emails"] and len(home.text) < 200:
            try:  # page loaded fine but renders with JavaScript: let treg render the homepage once
                d = treg.call("web_extract", {"url": home.url})
                md = "\n".join(pg.get("markdown") or "" for pg in (d.get("output") or {}).get("pages") or [])
                if md:
                    html = re.sub(r"\[([^\]]*)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', md)
                    pages.append(("home", _parse(home.url, "<p>" + html.replace("\n", "</p><p>") + "</p>")))
                    contacts = extract_contacts(pages, domain, region)
                    out["js_fallback"] = True
            except TregError:
                pass
        return {**out, **contacts, **extract_details(pages),
                "email": contacts["emails"][0] if contacts["emails"] else "", "pages_crawled": len(pages)}
    finally:
        if own_client:
            client.close()


# ---------------------------------------------------------------- paid enrichment (M5)
def email_status(verify_output):
    """treg.people.email.verify output -> 'valid' | 'risky' | 'invalid' (providers use their own words)."""
    o = verify_output or {}
    word = str(o.get("status") or "").lower()
    if re.search(r"accept|catch|risky|unknown|role|disposable", word):
        return "risky"
    return "valid" if o.get("valid") else "invalid"


def enrich_paid(site, domain, treg, paid_emails=False, verify=False, cache=None):
    """M5: fill the email gap the free website scrape left, and optionally verify the best email.
    site: crawl_site() result. Returns email, all_emails, email_source, email_status for the lead.
    TregError skips a step (lead keeps what it has); BudgetReached / OutOfBalance propagate and stop the run."""
    emails = list(site.get("emails") or [])
    source = "website" if emails else ""
    if paid_emails and not emails and domain:
        cache = {} if cache is None else cache
        if domain not in cache:  # never pay twice for one domain in a run
            try:
                rows = (treg.call("company_emails_by_domain", {"domain": domain, "limit": 10}).get("data") or {}) \
                    .get("emails") or []
            except TregError:
                rows = None  # not cached: a later lead with the same domain may retry
            if rows is not None:
                rows.sort(key=lambda e: ((e.get("verification") or {}).get("status") != "valid", -(e.get("confidence") or 0)))
                cache[domain] = [e["value"].lower() for e in rows if e.get("value")]
        emails = cache.get(domain) or []
        source = "hunter" if emails else ""
    status, best = "", emails[0] if emails else ""
    if verify and emails:
        for candidate in emails[:2]:  # verify the best; if it is invalid, try the runner-up once
            try:
                status = email_status(treg.call("email_verify", {"email": candidate}).get("output"))
            except TregError:
                status = ""
                break
            if status != "invalid":
                best = candidate
                break
        else:
            best = ""  # every verified candidate bounced: keep them in all_emails only
    return {"email": best, "all_emails": emails, "email_source": source, "email_status": status}
