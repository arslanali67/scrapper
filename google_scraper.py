"""Google scraper: businesses from Google Maps for a query + area, enriched from their websites, written to a lead CSV.

  python google_scraper.py --query "dentists" --location "Austin, TX" --limit 300
  python google_scraper.py --query "IT companies" --location "Austin, TX" --require-website --paid-emails --dry-run

M6 search + mapping, M7 enrichment / dry-run / resume / summary. M8 adds state -> cities -> ZIP coverage.
"""
import argparse
import re
import sys
from collections import Counter
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from common import (ENDPOINTS, GOOGLE_COLUMNS, BudgetReached, LeadSpend, LeadWriter, OutOfBalance, Treg, TregError,
                    crawl_site, domain_of, enrich_paid, estimate, normalize_phone)

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


def enrich(lead, treg, paid_emails, verify, cache):
    """M4 website scrape + M5 paid email/verify for one lead. Returns (lead, website_error, micro_usd_spent)."""
    spend = LeadSpend(treg)
    site = crawl_site(lead["website"], treg=spend) if lead["website"] else {"emails": [], "phones": [], "error": "no website"}
    lead.update({k: site.get(k) for k in SITE_FIELDS})
    lead.update(enrich_paid(site, lead["domain"], spend, paid_emails, verify, cache))
    lead["all_phones"] = [p for p in dict.fromkeys([lead["phone"], *site.get("phones", [])]) if p]
    lead["phone"] = lead["phone"] or next(iter(lead["all_phones"]), "")
    return lead, site.get("error", ""), spend.micro


def dry_run_cost(leads, cells, paid_emails, verify):
    calls = {"maps_search": cells, "web_extract": leads * SHARE_JS_SITE}
    if paid_emails:
        calls["company_emails_by_domain"] = leads * SHARE_NO_EMAIL * SHARE_HUNTER_HIT
    if verify:
        calls["email_verify"] = leads * SHARE_EMAIL
    return estimate(calls)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Google Maps businesses -> lead CSV with emails, phones and site details")
    ap.add_argument("--query", required=True, help='business type, e.g. "dentists"')
    ap.add_argument("--location", required=True, help='one city or ZIP, e.g. "Austin, TX"')
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

    leads_expected, cells = min(a.limit, ROWS_CAP), 1
    if a.dry_run:
        cost = estimate({"maps_search": cells}) if a.no_enrich else dry_run_cost(leads_expected, cells, a.paid_emails, a.verify)
        print(f"dry run: {cells} Maps search(es), up to {leads_expected} leads -> estimated ${cost:.4f} "
              f"(hard limit --max-cost ${a.max_cost:.2f}). Nothing was spent.")
        return
    if Path(a.out).exists() and Path(a.out).stat().st_size and not a.resume:
        sys.exit(f"{a.out} already exists: add --resume to continue it, or choose another --out")

    treg = Treg(a.max_cost)
    stats, site_errors = Counter(), Counter()
    stop, saturated, cache = "", False, {}
    with LeadWriter(a.out, GOOGLE_COLUMNS, "place_id") as out:
        resumed = len(out.seen)
        candidates, taken = [], set()
        try:
            before = treg.spent_micro
            rows, saturated = search_cell(treg, a.query, a.location)
            search_micro = treg.spent_micro - before
            stats["found"] = len(rows)
            for raw in rows:
                if len(candidates) >= a.limit - resumed:
                    break
                lead = map_place(raw, a.query, a.location)
                if out.has(lead["place_id"]) or lead["place_id"] in taken:
                    stats["duplicates"] += 1
                elif not passes(lead, a.min_rating, a.min_reviews, a.require_website):
                    stats["filtered"] += 1
                else:
                    candidates.append(lead)
                    taken.add(lead["place_id"])
        except (BudgetReached, OutOfBalance, TregError) as e:
            stop, rows, search_micro = str(e), [], 0
        share = search_micro / max(1, len(candidates))  # each lead carries its part of the search cost

        def write(lead, spent_micro):
            lead["cost_usd"] = round((share + spent_micro) / 1e6, 6)
            if out.write(lead):
                stats["written"] += 1
                stats["email"] += bool(lead.get("email"))
                stats["phone"] += bool(lead.get("phone"))
                stats["website"] += bool(lead.get("website"))
                if stats["written"] % 10 == 0:
                    print(f"  {stats['written']} leads | {stats['email']} with email | spent ${treg.spent_usd:.4f}")

        if a.no_enrich:
            for lead in candidates:
                write(lead, 0)
        elif candidates:
            ex = ThreadPoolExecutor(WORKERS)
            futures = {ex.submit(enrich, lead, treg, a.paid_emails, a.verify, cache): lead for lead in candidates}
            try:
                for f in as_completed(futures):
                    try:
                        lead, err, spent = f.result()
                    except CancelledError:
                        continue
                    except (BudgetReached, OutOfBalance) as e:
                        stop = stop or str(e)
                        for g in futures:
                            g.cancel()  # not-yet-started leads; running ones still finish and are written
                        continue
                    except Exception as e:  # a bug or odd site must not lose the lead: keep its Maps data
                        lead, err, spent = futures[f], f"enrichment failed ({type(e).__name__})", 0
                    if err:
                        site_errors[re.sub(r" \(.*", "", err)] += 1
                    write(lead, spent)
            except KeyboardInterrupt:
                stop = "stopped with Ctrl+C"
                for g in futures:
                    g.cancel()
            finally:
                ex.shutdown(wait=True, cancel_futures=True)

    n = stats["written"]
    pct = lambda k: f"{stats[k]}/{n} ({100 * stats[k] // n}%)" if n else "0"
    print(f"\n{n} new leads -> {a.out}" + (f" (+{resumed} already in the file)" if resumed else ""))
    print(f"  email {pct('email')} | phone {pct('phone')} | website {pct('website')}")
    print(f"  Maps: {stats['found']} places, {stats['duplicates']} duplicates, {stats['filtered']} filtered"
          + (f" | search hit the {ROWS_CAP}-result cap: more places exist (M8 splits the area)" if saturated else ""))
    if site_errors:
        print("  websites not read: " + ", ".join(f"{k} x{v}" for k, v in site_errors.most_common()))
    print(f"  spent ${treg.spent_usd:.4f}" + (f" (${treg.spent_usd / n:.5f} per lead)" if n else "")
          + f" | run log {treg.log_path}")
    if stop:
        print(f"  stopped early: {stop}")


if __name__ == "__main__":
    main()
