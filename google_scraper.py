"""Google scraper: businesses from Google Maps for a query + area, enriched from their websites, written to a lead CSV.

  python google_scraper.py --query "dentists" --location "Austin, TX" --limit 300
  python google_scraper.py --query "IT companies" --location "Texas" --require-website --max-cost 25
  python google_scraper.py --query "plumbers" --locations-file areas.txt --dry-run

A state is searched city by city (largest first); a city whose search hits the 100-result cap is searched again
ZIP code by ZIP code. M6 search + mapping, M7 enrichment / dry-run / resume / summary, M8 area coverage.
"""
import argparse
import csv
import re
import sys
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, CancelledError, ThreadPoolExecutor, wait
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from common import (ENDPOINTS, GOOGLE_COLUMNS, ROOT, BudgetReached, LeadSpend, LeadWriter, OutOfBalance, Treg,
                    TregError, crawl_site, domain_of, enrich_paid, estimate, normalize_phone)

ROWS_CAP = ENDPOINTS["maps_search"]["rows_cap"]  # one search never returns more (M1): a full cell is "saturated"
# "..., Austin, TX 78704" or "..., Austin, TX 78704, United States"
US_ADDRESS_RE = re.compile(r",\s*([^,]+?),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?(?:,\s*(?:USA|United States))?\s*$")
SITE_FIELDS = ("facebook", "instagram", "linkedin", "x", "youtube", "tiktok", "site_description", "services",
               "team_members", "contact_form_url", "site_address")
# Dry-run rates measured on 20 Austin dentists (docs/endpoints.md); refine as real runs come in.
SHARE_JS_SITE = 0.05      # sites that need treg to render JavaScript (0/20 measured; kept above zero on purpose)
SHARE_NO_EMAIL = 0.45     # readable sites without an email (8/18)
SHARE_HUNTER_HIT = 0.40   # of those, domains Hunter knows and bills for (3/8)
SHARE_EMAIL = 0.70        # leads with an email to verify (13/18 with --paid-emails)
WORKERS = 8               # websites scraped in parallel
SEARCH_WORKERS = 4        # Maps searches in parallel (each takes ~30 s)
SECONDS_PER_SEARCH = 35   # for the dry-run time estimate


# ---------------------------------------------------------------- area planning (M8)
def _rows(name):
    with open(ROOT / "data" / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@lru_cache(None)
def states():
    """{'TX': 'Texas', ...} from data/us_states.csv (GeoNames)."""
    return {r["code"]: r["name"] for r in _rows("us_states.csv")}


def state_code(text):
    """'TX', 'tx' or 'Texas' -> 'TX'; None if it is not a US state."""
    t = (text or "").strip()
    return t.upper() if t.upper() in states() else next((c for c, n in states().items() if n.lower() == t.lower()), None)


@lru_cache(None)
def _cities_by_state():
    out = {}
    for r in _rows("us_cities.csv"):  # already sorted by state, largest first
        out.setdefault(r["state"], []).append((r["city"], int(r["population"])))
    return out


@lru_cache(None)
def _zips_by_city():
    out = {}
    for r in _rows("us_zips.csv"):
        out.setdefault((r["city"].lower(), r["state"]), []).append(r["zip"])
        out.setdefault(("", r["state"]), []).append(r["zip"])  # every ZIP of the state
        out[("#", r["zip"])] = [r["state"]]
    return out


def zips_of(city, state):
    return _zips_by_city().get(((city or "").lower(), state), [])


def plan_area(location=None, split="auto", min_population=5000, locations_file=None):
    """-> (cells, state). A cell is {'cell': text for the Maps search, 'kind': city|zip|state|free, 'city', 'state'}.
    state: the requested US state (places outside it are dropped), or None."""
    if locations_file:
        lines = [l.strip() for l in Path(locations_file).read_text(encoding="utf-8").splitlines()]
        return [{"cell": l, "kind": "free", "city": "", "state": ""} for l in lines if l and not l.startswith("#")], None
    s = location.strip()
    st = state_code(s)
    if st:
        if split == "none":
            return [{"cell": states()[st], "kind": "state", "city": "", "state": st}], st
        if split == "zips":
            return [{"cell": f"{z}, {st}", "kind": "zip", "city": "", "state": st} for z in zips_of("", st)], st
        return [{"cell": f"{c}, {st}", "kind": "city", "city": c, "state": st}
                for c, pop in _cities_by_state().get(st, []) if pop >= min_population], st
    if re.fullmatch(r"\d{5}", s):
        st = (_zips_by_city().get(("#", s)) or [""])[0]
        return [{"cell": f"{s}, {st}" if st else s, "kind": "zip", "city": "", "state": st}], st or None
    m = re.fullmatch(r"(.+?),\s*([A-Za-z .]+)", s)
    if m and state_code(m.group(2)):
        city, st = m.group(1).strip(), state_code(m.group(2))
        if split == "zips" and zips_of(city, st):
            return [{"cell": f"{z}, {st}", "kind": "zip", "city": city, "state": st} for z in zips_of(city, st)], st
        return [{"cell": f"{city}, {st}", "kind": "city", "city": city, "state": st}], st
    return [{"cell": s, "kind": "free", "city": "", "state": ""}], None


# ---------------------------------------------------------------- one search cell + mapping (M6)
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


# ---------------------------------------------------------------- enrichment (M7)
def enrich(lead, treg, paid_emails, verify, cache):
    """M4 website scrape + M5 paid email/verify for one lead. Returns (lead, website_error, micro_usd_spent)."""
    spend = LeadSpend(treg)
    site = crawl_site(lead["website"], treg=spend) if lead["website"] else {"emails": [], "phones": [], "error": "no website"}
    lead.update({k: site.get(k) for k in SITE_FIELDS})
    lead.update(enrich_paid(site, lead["domain"], spend, paid_emails, verify, cache))
    lead["all_phones"] = [p for p in dict.fromkeys([lead["phone"], *site.get("phones", [])]) if p]
    lead["phone"] = lead["phone"] or next(iter(lead["all_phones"]), "")
    return lead, site.get("error", ""), spend.micro


def dry_run_cost(leads, cells, paid_emails, verify, no_enrich=False):
    calls = {"maps_search": cells}
    if not no_enrich:
        calls["web_extract"] = leads * SHARE_JS_SITE
        if paid_emails:
            calls["company_emails_by_domain"] = leads * SHARE_NO_EMAIL * SHARE_HUNTER_HIT
        if verify:
            calls["email_verify"] = leads * SHARE_EMAIL
    return estimate(calls)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Google Maps businesses -> lead CSV with emails, phones and site details")
    ap.add_argument("--query", required=True, help='business type, e.g. "dentists"')
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--location", help='a state ("Texas"), a city ("Austin, TX") or a ZIP ("78704")')
    where.add_argument("--locations-file", help="one location per line (any country), searched as given")
    ap.add_argument("--split", choices=("auto", "none", "cities", "zips"), default="auto",
                    help="auto: a state -> its cities, full cities -> ZIP codes (default); none: one search as given")
    ap.add_argument("--min-population", type=int, default=5000, help="smallest city searched when splitting a state")
    ap.add_argument("--limit", type=int, default=100, help="total leads wanted in the output file")
    ap.add_argument("--out", default="leads_google.csv")
    ap.add_argument("--max-cost", type=float, default=5.0, help="hard spend limit in USD for this run")
    ap.add_argument("--dry-run", action="store_true", help="print the estimated cost and exit; spends nothing")
    ap.add_argument("--resume", action="store_true", help="continue an existing --out file, skipping its leads")
    ap.add_argument("--no-enrich", action="store_true", help="Maps data only: no website scrape, no paid lookups")
    ap.add_argument("--paid-emails", action="store_true", help="Hunter lookup when the website shows no email")
    ap.add_argument("--verify", action="store_true", help="verify the best email of each lead")
    ap.add_argument("--min-rating", type=float, default=0.0)
    ap.add_argument("--min-reviews", type=int, default=0)
    ap.add_argument("--require-website", action="store_true", help="keep only businesses with a website")
    a = ap.parse_args(argv)
    if a.no_enrich and (a.paid_emails or a.verify):
        ap.error("--no-enrich cannot be combined with --paid-emails or --verify")

    cells, area_state = plan_area(a.location, a.split, a.min_population, a.locations_file)
    if not cells:
        sys.exit("no search areas: check --location / --min-population / --locations-file")
    if a.dry_run:
        leads = min(a.limit, len(cells) * ROWS_CAP)
        cost = dry_run_cost(leads, len(cells), a.paid_emails, a.verify, a.no_enrich)
        resplit = " (+ ZIP-code searches for any city that returns 100 results)" if a.split == "auto" and \
            any(c["kind"] == "city" for c in cells) else ""
        mins = len(cells) * SECONDS_PER_SEARCH / SEARCH_WORKERS / 60
        print(f"dry run: {len(cells)} Maps searches{resplit}, up to {leads} leads -> estimated ${cost:.4f}, "
              f"searching takes up to ~{mins:.0f} min (stops early once --limit is reached). "
              f"Hard limit --max-cost ${a.max_cost:.2f}. Nothing was spent.")
        return
    if Path(a.out).exists() and Path(a.out).stat().st_size and not a.resume:
        sys.exit(f"{a.out} already exists: add --resume to continue it, or choose another --out")

    treg = Treg(a.max_cost)
    stats, site_errors, area = Counter(), Counter(), Counter()
    stop, cache, taken = "", {}, set()
    with LeadWriter(a.out, GOOGLE_COLUMNS, "place_id") as out:
        resumed, quota = len(out.seen), max(0, a.limit - len(out.seen))
        searches, enrichers = ThreadPoolExecutor(SEARCH_WORKERS), ThreadPoolExecutor(WORKERS)
        pending = {}  # future -> ("search", cell) | ("enrich", (lead, search_share_micro))
        todo = deque(cells)  # searches not started yet: started a few at a time, only while leads are still needed

        def run_search(cell):
            spend = LeadSpend(treg)
            rows, saturated = search_cell(spend, a.query, cell["cell"])
            return rows, saturated, spend.micro

        def start_searches():
            running = sum(1 for k, _ in pending.values() if k == "search")
            while todo and running < SEARCH_WORKERS and not stop and stats["accepted"] < quota:
                cell = todo.popleft()
                pending[searches.submit(run_search, cell)] = ("search", cell)
                running += 1

        def cancel():
            todo.clear()
            for f in pending:
                f.cancel()  # only not-yet-started work; running calls finish and are kept

        def write(lead, share, spent):
            lead["cost_usd"] = round((share + spent) / 1e6, 6)
            if out.write(lead):
                stats["written"] += 1
                stats["email"] += bool(lead.get("email"))
                stats["phone"] += bool(lead.get("phone"))
                stats["website"] += bool(lead.get("website"))
                if stats["written"] % 10 == 0:
                    print(f"  {stats['written']} leads | {stats['email']} with email | {area['searched']} searches "
                          f"| spent ${treg.spent_usd:.4f}")

        def on_search(cell, f):
            nonlocal stop
            try:
                rows, saturated, micro = f.result()
            except (BudgetReached, OutOfBalance) as e:
                stop = stop or str(e)
                return cancel()
            except TregError:
                area["failed"] += 1
                return
            area["searched"] += 1
            area[cell["kind"] + "_cells"] += 1
            stats["found"] += len(rows)
            if saturated:
                area["saturated"] += 1
                if a.split == "auto" and cell["kind"] == "city":
                    zips = zips_of(cell["city"], cell["state"])
                    area["resplit"] += bool(zips)
                    # the full city's ZIP codes go first, before the next (smaller) cities
                    todo.extendleft({"cell": f"{z}, {cell['state']}", "kind": "zip", "city": cell["city"],
                                     "state": cell["state"]} for z in reversed(zips))
            new = []
            for raw in rows:
                if stats["accepted"] + len(new) >= quota:
                    break
                lead = map_place(raw, a.query, cell["cell"])
                if out.has(lead["place_id"]) or lead["place_id"] in taken:
                    stats["duplicates"] += 1
                elif area_state and lead["state"] and lead["state"] != area_state:
                    stats["outside"] += 1  # Maps results bleed across state lines
                elif not passes(lead, a.min_rating, a.min_reviews, a.require_website):
                    stats["filtered"] += 1
                else:
                    new.append(lead)
                    taken.add(lead["place_id"])
            stats["accepted"] += len(new)
            share = micro / len(new) if new else 0  # each new lead carries its part of this search's cost
            for lead in new:
                if a.no_enrich:
                    write(lead, share, 0)
                else:
                    pending[enrichers.submit(enrich, lead, treg, a.paid_emails, a.verify, cache)] = ("enrich", (lead, share))

        def on_enrich(item, f):
            nonlocal stop
            lead, share = item
            try:
                lead, err, spent = f.result()
            except (BudgetReached, OutOfBalance) as e:
                stop = stop or str(e)
                return cancel()
            except Exception as e:  # a bug or odd site must not lose the lead: keep its Maps data
                err, spent = f"enrichment failed ({type(e).__name__})", 0
            if err:
                site_errors[re.sub(r" \(.*", "", err)] += 1
            write(lead, share, spent)

        try:
            start_searches()
            while pending:
                done, _ = wait(list(pending), return_when=FIRST_COMPLETED)
                for f in done:
                    kind, item = pending.pop(f)
                    if not f.cancelled():
                        (on_search if kind == "search" else on_enrich)(item, f)
                start_searches()
        except KeyboardInterrupt:
            stop = "stopped with Ctrl+C"
            cancel()
        finally:
            searches.shutdown(wait=True, cancel_futures=True)
            enrichers.shutdown(wait=True, cancel_futures=True)

    n = stats["written"]
    pct = lambda k: f"{stats[k]}/{n} ({100 * stats[k] // n}%)" if n else "0"
    print(f"\n{n} new leads -> {a.out}" + (f" (+{resumed} already in the file)" if resumed else ""))
    emails = "not looked up (--no-enrich)" if a.no_enrich else pct("email")
    print(f"  email {emails} | phone {pct('phone')} | website {pct('website')}")
    print(f"  area: {area['searched']} searches ({area['city_cells']} cities, {area['zip_cells']} ZIP codes, "
          f"{area['state_cells'] + area['free_cells']} other), {area['saturated']} hit the {ROWS_CAP}-result cap, "
          f"{area['resplit']} cities re-searched by ZIP" + (f", {area['failed']} failed" if area["failed"] else ""))
    print(f"  places: {stats['found']} found, {stats['duplicates']} duplicates, {stats['outside']} outside "
          f"{area_state or 'area'}, {stats['filtered']} filtered")
    if site_errors:
        print("  websites not read: " + ", ".join(f"{k} x{v}" for k, v in site_errors.most_common()))
    print(f"  spent ${treg.spent_usd:.4f}" + (f" (${treg.spent_usd / n:.5f} per lead)" if n else "")
          + f" | run log {treg.log_path}")
    if stop:
        print(f"  stopped early: {stop}")


if __name__ == "__main__":
    main()
