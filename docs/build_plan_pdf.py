# Regenerates Lead-Scraper-Build-Modules.pdf: python docs/build_plan_pdf.py Lead-Scraper-Build-Modules.pdf  (needs reportlab)
import sys
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

OUT = sys.argv[1]
NAVY = colors.HexColor("#283d6e")
GREEN = colors.HexColor("#2e7d32")
BAND = colors.HexColor("#eef3ea")
BOXBG = colors.HexColor("#eef5ea")
GRID = colors.HexColor("#c9d3c4")
GREY = colors.HexColor("#666666")
SUB = colors.HexColor("#3c4a6e")
FOOT = "Lead Scraper Suite \u00b7 Build Modules (treg)"

TITLE = ParagraphStyle("T", fontName="Helvetica-Bold", fontSize=22, leading=27, alignment=TA_CENTER)
INTRO = ParagraphStyle("I", fontName="Helvetica", fontSize=10, leading=14, alignment=TA_CENTER, textColor=GREY)
P = ParagraphStyle("P", fontName="Helvetica", fontSize=9.4, leading=13, spaceAfter=4)
SMALL = ParagraphStyle("S", parent=P, fontSize=8.3, leading=11, textColor=GREY)
GOAL = ParagraphStyle("G", parent=P, fontName="Helvetica-Oblique", textColor=colors.HexColor("#444444"))
H3 = ParagraphStyle("H3", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=SUB, spaceBefore=6, spaceAfter=3)
BANNER = ParagraphStyle("B", fontName="Helvetica", fontSize=16, leading=20, textColor=colors.white)
TAG = ParagraphStyle("BT", fontName="Helvetica", fontSize=7.5, leading=9, textColor=colors.HexColor("#c8d0e6"),
                     alignment=2)
CELL = ParagraphStyle("C", fontName="Helvetica", fontSize=8.4, leading=10.8)
CELLB = ParagraphStyle("CB", parent=CELL, fontName="Helvetica-Bold", textColor=GREEN)
CELLH = ParagraphStyle("CH", parent=CELL, fontName="Helvetica-Bold", textColor=colors.white)
PHASE = ParagraphStyle("PH", parent=CELL, fontName="Helvetica-Bold", fontSize=7.5, textColor=NAVY)
NUM = ParagraphStyle("N", fontName="Helvetica", fontSize=12, leading=13)
BOX = ParagraphStyle("BX", fontName="Courier", fontSize=7.8, leading=10.5, textColor=colors.HexColor("#2e5e1e"))
BOXB = ParagraphStyle("BXB", parent=BOX, fontName="Courier-Bold")
BULLET = ParagraphStyle("BU", parent=P, leftIndent=12, bulletIndent=2, spaceAfter=1.5)

W = 182 * mm
PROJ = r"<font face='Courier' size='8.3'>C:\Users\QS\Desktop\linkedin_scraper</font>"
ACT = r"<font face='Courier' size='8.3'>.venv\Scripts\activate</font>"
story = []


def c(t):  # inline code
    return f"<font face='Courier' size='8.3'>{t}</font>"


# ---------------------------------------------------------------- data
MODULES = [
    # id, title, phase tag, priority, current state, goal, used_by, what[], how[], key_files, deps, done_when
    dict(id="M0", title="Project Setup & treg Account", tag="PHASE 1 \u00b7 P0", pri="P0",
         state="Done (Oct 29): venv, treg CLI, token in .env, git",
         goal="Create the Python project, the treg account and the token every later module uses. Nothing here "
              "scrapes yet; it makes sure the first real call in M1 works on this Windows machine.",
         used_by="Both scrapers",
         what=["Create the Python project and a virtual environment.",
               "Install the treg CLI and sign in.",
               "Create a team token for the scrapers (not your personal login).",
               "Store the token outside the code, in a git-ignored .env file.",
               "Put the folder under git from day one."],
         how=[f"In {PROJ} run {c('python -m venv .venv')} then "
              f"{ACT}. Python 3.11+ (already installed).",
              f"Install the treg CLI with {c('pip install tools-registry')} (on macOS/Linux the README's "
              f"{c('curl -fsSL https://treg.to/install.sh | sh')} also works). Run {c('treg login')} and sign in with "
              "GitHub or the emailed code.",
              f"Run {c('treg')} with no arguments: it prints the account, active team and balance. New teams start "
              "with $1.00 free credit, enough for M1 testing.",
              f"Create an agent token for scripts with {c('treg org agent-new')}. This is the {c('TREG_TOKEN')} the "
              "scrapers send; it can be revoked without touching your login.",
              f"Create {c('.env')} with {c('TREG_TOKEN=...')} and {c('.env.example')} with the key name only. "
              f"Add {c('.env')}, {c('.venv/')}, {c('*.csv')} and {c('runs/')} to {c('.gitignore')} so tokens and "
              "lead data never reach git.",
              f"Run {c('git init')} and make the first commit (PRD + setup files only)."],
         files="requirements.txt (new) \u00b7 .env.example (new) \u00b7 .gitignore (new)",
         deps="treg CLI (tools-registry) \u00b7 httpx \u00b7 phonenumbers",
         done=f"{c('treg balance')} prints a balance and {c('.env')} holds a working token."),

    dict(id="M1", title="Endpoint Discovery (treg Catalog)", tag="PHASE 1 \u00b7 P0", pri="P0",
         state="Done (Oct 29): endpoints.json + docs/endpoints.md",
         goal="Find the current best treg endpoint for every data capability, record its ID, price and real hit rate, "
              "and save one sample response each. This is the most important module: if a capability is missing, "
              "we find out here, before writing scraper code.",
         used_by="Both scrapers",
         what=["Search the catalog for each capability in the table below.",
               "Check price and parameters before spending.",
               "Make one small test call per endpoint and save the response.",
               "Write the chosen IDs into endpoints.json.",
               "Measure email hit rate on 20 sample companies."],
         how=[f"For each capability run {c('treg catalog search &quot;&lt;job in plain words&gt;&quot;')}, e.g. "
              f"{c('&quot;linkedin company search&quot;')}, {c('&quot;linkedin company profile&quot;')}, {c('&quot;linkedin profile&quot;')}, {c('&quot;google maps places search&quot;')}, "
              f"{c('&quot;google place details&quot;')}, {c('&quot;google search results&quot;')}, {c('&quot;extract web page&quot;')}, "
              f"{c('&quot;email by domain&quot;')}. Search by the job, not the vendor name.",
              f"Prefer routed {c('treg.*')} endpoints (treg picks the provider). Known ones: "
              f"{c('treg.people.email.find')}, {c('treg.people.email.verify')}, {c('treg.people.phone.find')}, "
              f"{c('treg.people.search')}, plus {c('hunter.people.email.find')}.",
              f"Run {c('treg catalog get &lt;id&gt;')} on each candidate to read price, parameters and notes.",
              f"Test with one call: {c('treg --json call &lt;id&gt; --query K=V &gt; samples/&lt;capability&gt;.json')}. "
              f"Check the charged cost and {c('_treg.served_by')} in the response.",
              f"Write {c('endpoints.json')}: one entry per capability with {c('id')}, {c('price_usd')}, "
              f"{c('params')} and a note. Scrapers read IDs only from this file.",
              "Run the email finder on 20 real company domains and note how many return an email. This number "
              "replaces the guessed 40-60% target in M15. Do the same for work emails on 20 LinkedIn profile URLs.",
              "Measure two caps M8 and M9 depend on: the most results one Maps search returns (paging to the end), "
              "and how many results one Google-results query returns.",
              f"If a capability is missing: run {c('treg catalog request')} to ask for it, or add your own provider "
              f"key to treg with {c('treg tool add')} (e.g. a Google Places API key) so it is called at no treg charge."],
         files="endpoints.json (new) \u00b7 samples/*.json (new, git-ignored) \u00b7 docs/endpoints.md (new, notes)",
         deps="none (treg CLI from M0)",
         done="Every capability has an ID, price and saved sample, or a written decision on how to replace it.",
         table=[["Capability", "Used by", "Search phrase / known ID"],
                ["Google Maps / Places search", "M6, M8", "&quot;google maps places search&quot;"],
                ["Google place details", "-", "not needed: Maps rows are complete (M1)"],
                ["Google web results (SERP)", "M9, M10", "&quot;google search results&quot;"],
                ["Web page extract (JS sites)", "M4", "&quot;extract web page&quot;"],
                ["People search", "M9", "icypeas.people.search"],
                ["LinkedIn person profile", "M10", "treg.people.enrich"],
                ["LinkedIn company profile", "M10, M12", "&quot;linkedin company profile&quot;"],
                ["LinkedIn company search", "M12", "&quot;linkedin company search&quot;"],
                ["Email by domain (companies)", "M5", "hunter.companies.emails (opt-in)"],
                ["Work email by person", "M11", "treg.people.email.find"],
                ["Email verify / phone find", "M5, M11", "treg.people.email.verify, treg.people.phone.find"]]),

    dict(id="M2", title="treg Client & Cost Control", tag="PHASE 1 \u00b7 P0", pri="P0",
         state="Done (Oct 29): common.py Treg + offline checks",
         goal="One function that is the only way any scraper talks to treg. It adds the token, retries, logs every call, "
              "and stops the run before it spends more than the user allowed.",
         used_by="Every module that calls treg (M4-M12)",
         what=[f"{c('call(capability, params)')} that reads the endpoint ID from endpoints.json.",
               "Token, retries and timeouts in one place.",
               "Running cost total from the X-Treg-Cost-Micro header.",
               "A hard budget (--max-cost) and a per-call cap.",
               "Dry-run cost estimate using catalog prices."],
         how=[f"In {c('common.py')} create one {c('httpx.Client')} with base URL {c('https://treg.to')}, header "
              f"{c('X-Treg-Token')} from the environment, and a 180 s timeout (a routed email lookup took 77 s in M1). Agent tokens get 403 on "
              f"{c('GET /orgs')}; use {c('GET /auth/me')} for the org id.",
              f"{c('call()')} sends {c('GET /call/&lt;id&gt;?params')} (or POST with JSON when the endpoint needs a body) "
              "and returns the parsed JSON. The provider's response comes back unchanged.",
              f"Read {c('X-Treg-Cost-Micro')} from every response and add it to a module-level {c('spent_micro')} counter. "
              f"Send {c('X-Treg-Route-Max-Cost')} on every call, from {c('max_cost_usd')} in endpoints.json. treg's default is $1 per call and routed "
              "endpoints bill misses, so this header is never optional.",
              f"Before each call, if {c('spent + price &gt; max_cost')} raise {c('BudgetReached')}; the scraper catches it, "
              "flushes the CSV and prints the summary.",
              "Status handling: 429 and 5xx retry 3 times with backoff (1 s, 4 s, 10 s); 402 = out of balance, stop "
              "the run and print the top-up URL from the response; 503 = provider empty, not charged, skip the lead; "
              "502 = response too large, not charged, skip.",
              f"Append one JSON line per call to {c('runs/&lt;timestamp&gt;.jsonl')}: capability, endpoint, status, cost, "
              "served_by, duration. Never log the token.",
              f"{c('estimate(plan)')} multiplies the prices in endpoints.json by expected call counts for "
              f"{c('--dry-run')}, using the formula in M7."],
         files="common.py (new: call, BudgetReached, estimate) \u00b7 endpoints.json",
         deps="httpx",
         done="A test call through call() returns JSON, cost is logged, and --max-cost 0 stops before any spend."),

    dict(id="M3", title="Lead Model, CSV Output & Resume", tag="PHASE 1 \u00b7 P0", pri="P0",
         state="Done (Oct 29): LeadWriter, columns, Excel-checked",
         goal="Fix the CSV columns once and write leads safely: row by row, de-duplicated, resumable and Excel-friendly. "
              "Both scrapers share the first 12 columns so their files can be stacked later.",
         used_by="Both scrapers",
         what=["Define the shared 12 columns and each scraper's extra columns.",
               "Write each lead as soon as it is finished.",
               "Skip leads already in the file (resume).",
               "Normalize values: phones, domains, multi-value fields."],
         how=[f"Shared columns, in order: {c('company_name, website, domain, email, all_emails, email_source, phone, all_phones, address, city, country, source_url')}.",
              f"LinkedIn people extras (people mode): {c('full_name, first_name, last_name, job_title, headline, person_location, person_linkedin_url, company_linkedin_url, industry, company_size, email_status, email_confidence, search_query, scraped_at, cost_usd')}.",
              f"LinkedIn company extras (company mode): {c('linkedin_id, tagline, description, industry, company_size, employees_on_linkedin, headquarters, all_locations, founded_year, company_type, specialties, followers, logo_url, funding_info, socials, email_status, scraped_at, cost_usd')}.",
              f"Google extras: {c('place_id, category, all_categories, state, postal_code, latitude, longitude, rating, review_count, opening_hours, google_maps_url, facebook, instagram, linkedin, x, youtube, tiktok, site_description, services, team_members, contact_form_url, site_address, search_query, search_location, email_status, scraped_at, cost_usd')}.",
              f"Use stdlib {c('csv.DictWriter')} with encoding {c('utf-8-sig')} (Excel opens it correctly), "
              f"{c('extrasaction=&quot;ignore&quot;')}, and {c('flush()')} after every row so a crash loses at most one lead.",
              f"Resume: on start, if the output file exists, read its dedupe key column ({c('place_id')} or "
              f"{c('person_linkedin_url')} or {c('source_url')}) into a set and skip those leads before any paid call.",
              f"Multi-value fields are joined with {c('&quot; | &quot;')}. Empty stays empty (never &quot;N/A&quot;). Domains are "
              f"lower-case without {c('www.')}. Phones go to international format ({c('+1 512-474-1234')}) with {c('phonenumbers')}: Excel shows bare E.164 "
              "as 1.51E+10 (checked in M3)."],
         files="common.py (LeadWriter, normalize_phone, domain_of) \u00b7 columns defined at top of each scraper file",
         deps="phonenumbers",
         done="Writing, killing and re-running produces no duplicate rows; the file opens cleanly in Excel."),

    dict(id="M4", title="Website Deep Scrape (Full Details)", tag="PHASE 1 · P0", pri="P0",
         state="Done (Oct 29): 18/18 reachable sites read, email 10/18",
         goal="Visit every business website and collect everything useful on it: contact details (emails, phones, "
              "socials) plus what the company is and does (description, services, team, address, hours, contact form). "
              "This is free and runs before any paid lookup; LinkedIn and Google listings almost never show an email.",
         used_by="Google scraper (every place with a website) · LinkedIn company mode",
         what=["Crawl the homepage and the key internal pages (contact, about, services, team).",
               "Read structured data (schema.org JSON-LD) first, then the page text.",
               "Extract emails, phones and social links, and filter out junk.",
               "Extract company details: description, services, team members, address, hours, contact form.",
               "Fall back to treg's page-extract only for JavaScript-only sites."],
         how=[f"Fetch the homepage with httpx (10 s timeout, follow redirects, a normal browser User-Agent). From its "
              f"links pick up to 8 internal pages whose URL or link text matches contact, about, team, staff, services, "
              f"what-we-do, locations or impressum; if none are linked, try {c('/contact')} and {c('/about')}.",
              f"Respect robots.txt with stdlib {c('urllib.robotparser')}; skip disallowed paths.",
              f"Structured data first: parse every {c('&lt;script type=&quot;application/ld+json&quot;&gt;')} block "
              f"(stdlib json) for Organization / LocalBusiness: name, telephone, email, address, openingHours and "
              f"{c('sameAs')} (social links). When present this is the most reliable source.",
              f"Emails: {c('mailto:')} links and regex matches in the text; decode Cloudflare-protected addresses "
              f"({c('data-cfemail')}, a simple XOR) and &quot;name [at] domain [dot] com&quot; forms. Drop junk: image names "
              "(logo@2x.png), placeholders (example.com, sentry, wixpress) and other-domain emails unless they are the only "
              "ones. Rank: same-domain named &gt; same-domain generic (info@, contact@) &gt; other.",
              f"Phones: {c('tel:')} links first, then {c('phonenumbers.PhoneNumberMatcher')} for the lead's country. "
              "Socials: facebook.com, instagram.com, linkedin.com/company, x.com / twitter.com, youtube.com, tiktok.com.",
              f"Company details: {c('site_description')} from the meta / og:description, else the first paragraph of the "
              f"About page (max 500 chars); {c('services')} from h2/h3 headings on the services page (max 15); "
              f"{c('team_members')} as &quot;Name - Title&quot; pairs from the team page where they can be detected; "
              f"{c('contact_form_url')} = first page with a form that has an email field; {c('site_address')} from "
              "JSON-LD or the contact page.",
              "If the homepage loads but has almost no text (a JavaScript app) and nothing was found, render it once "
              "through the page-extract capability via call() (paid, small). Sites that answer with a bot check "
              "(403 / challenge page) are skipped and reported, never worked around.",
              f"Run sites in a {c('ThreadPoolExecutor(max_workers=8)')}; set {c('email_source=&quot;website&quot;')}."],
         files="common.py (crawl_site, extract_contacts, extract_details)",
         deps="none new (httpx, phonenumbers, stdlib re / json / html.parser / urllib.robotparser)",
         done="On 20 known websites, contacts, description and services that are visible on the site are found, "
              "with no junk emails."),

    dict(id="M5", title="Paid Enrichment (Email Finder, Verify, Phone)", tag="PHASE 1 \u00b7 P0/P1", pri="P0/P1",
         state="Done (Oct 30): Hunter 3/8 found, coverage 13/18",
         goal="Fill the email gap that M4 leaves, using paid finders only when the free crawl found nothing, and "
              "optionally verify emails before they go into the CSV.",
         used_by="Both scrapers",
         what=[f"Opt-in paid domain search ({c('--paid-emails')}) when M4 found no email (P1).",
               "Optional email verification with --verify (P1).",
               "Record the source of every email."],
         how=[f"With {c('--paid-emails')} (off by default), if {c('enrich_website')} returned no email and the lead has a "
              f"domain, call {c('hunter.companies.emails')} with {c('domain=acme.com&amp;limit=10')} (one credit covers up to 10 emails). Set {c('email_source')} "
              "to hunter.",
              "It reserves about $0.0245 per domain (free when nothing is found), which is why it is opt-in; its cap in "
              f"endpoints.json is {c('0.03')}.",
              f"With {c('--verify')}, call {c('treg.people.email.verify')} on the best email only and write "
              f"{c('email_status')} = valid / risky / invalid. Invalid emails move out of {c('email')} but stay in "
              f"{c('all_emails')}.",
              "Never look up the same domain twice in one run: keep a dict cache domain -&gt; result.",
              f"{c('--no-enrich')} skips M4 and M5 entirely (cheapest run, source data only)."],
         files="common.py (enrich_paid)",
         deps="none new",
         done="Leads without a website email get one from the finder where available; cost per lead is logged."),

    # ---------------- Google
    dict(id="M6", title="Google Maps Search", tag="PHASE 2 · P0", pri="P0",
         state="Done (Nov 4): 50/50 rows with phone, website, ZIP",
         goal="Turn one search cell, a business type plus one city or ZIP code (&quot;dentists&quot; in &quot;Austin, TX&quot;), "
              "into unique businesses with every field Google shows: website, phone, address, rating, reviews, hours, "
              "status. M8 runs many cells to cover a whole state.",
         used_by="Google scraper",
         what=["Search Google Maps / Places with paging.",
               "Detect when a cell hits the result cap, so M8 can split it.",
               "No place-details call: search rows are already complete (M1: 100/100).",
               "Map provider JSON to the Google CSV columns.",
               "Filters: minimum rating, minimum reviews, website required."],
         how=[f"Call {c('crawl4ai.google.serp.maps')} with {c('query')}, {c('location')} = the cell and {c('limit')} 100: one "
              "call returns up to 100 places for $0.00015.",
              "Count the results. If the count reaches the provider's cap (100, measured in M1), mark the cell "
              "saturated: there are probably more businesses than one search can return.",
              f"De-duplicate by {c('place_id')} across the whole run; fallback key = normalized phone + domain.",
              "No details call: in M1 every row already had website, phone, address, hours, rating and place_id.",
              f"Write one function {c('map_place(raw) -&gt; dict')}; a vendor change only touches it and endpoints.json.",
              f"Apply {c('--min-rating')}, {c('--min-reviews')}, and with "
              f"{c('--require-website')} keep only businesses that have a website. Filters run before the website scrape, "
              "so filtered leads cost nothing more.",
              f"Set {c('source_url')} = Google Maps URL, {c('search_query')} and {c('search_location')} on every row."],
         files="google_scraper.py (search_cell, map_place)",
         deps="none new",
         done="&quot;dentists&quot; in &quot;Austin, TX&quot; with --limit 50 --no-enrich gives 50 unique rows with phone for 90%+."),

    dict(id="M7", title="Google Scraper CLI & Run Summary", tag="PHASE 2 · P0", pri="P0",
         state="Done (Nov 4): G-A1..G-A3 pass (email 56%, budget stop, resume)",
         goal="One command: area plan -&gt; Maps search -&gt; details -&gt; website deep scrape -&gt; CSV, with dry-run, "
              "budget and resume.",
         used_by="Google scraper",
         what=["argparse command line.",
               "Pipeline wiring M2-M6 and M8.",
               "Dry-run estimate and end-of-run summary with coverage report."],
         how=[f"Flags: {c('--query')} (required), {c('--location')} (city, &quot;City, ST&quot;, state name or code), "
              f"{c('--split auto|none|cities|zips')} (auto = cities when the location is a state), {c('--min-population')}, "
              f"{c('--locations-file')}, {c('--limit')}, {c('--out')} (default leads_google.csv), {c('--max-cost')} "
              f"(default 5), {c('--dry-run')}, {c('--resume')}, {c('--no-enrich')}, {c('--verify')}, {c('--min-rating')}, "
              f"{c('--min-reviews')}, {c('--require-website')}, {c('--suppress')}.",
              f"Examples: {c('python google_scraper.py --query &quot;dentists&quot; --location &quot;Austin, TX&quot; --limit 300')} "
              f"and {c('python google_scraper.py --query &quot;IT companies&quot; --location &quot;Texas&quot; --require-website --max-cost 25')}.",
              "Loop: for each cell from M8 -&gt; M6 search -&gt; for each new place: M4 website deep scrape -&gt; M5 if "
              "no email -&gt; write row. Print progress every 10 leads: count, emails found, spend so far.",
              "Dry-run: number of cells x pages x search price + expected leads x (details share + extract share + "
              f"email-finder share (+ verify with {c('--verify')})). Print the total and exit with zero spend.",
              f"Ctrl+C and {c('BudgetReached')} both end cleanly: file flushed, summary printed.",
              "Summary: leads written, % with website / email / phone, cells searched / saturated / re-split, total "
              "cost, cost per lead, errors, path of the run log."],
         files="google_scraper.py (main)",
         deps="none new",
         done="Acceptance tests G-A1 to G-A4 in M15 pass."),

    dict(id="M8", title="Area Coverage (State -&gt; Cities -&gt; ZIP Codes)", tag="PHASE 2 · P0", pri="P0",
         state="Done (Nov 4): Texas 1,000 unique; Vermont 3.8x vs one search",
         goal="Get as close as possible to every matching business in a large area. One Maps search returns at most "
              "100 places (M1), so a state is searched city by city, and any city that hits the cap is searched "
              "again ZIP code by ZIP code.",
         used_by="Google scraper (and M9 reuses the city list)",
         what=["A built-in list of US cities (with population) and ZIP codes per state.",
               "Split a state into cities; re-split saturated cities into ZIP codes.",
               "One dedupe set and one budget across all searches.",
               "Custom area lists for any country via a file.",
               "A coverage report."],
         how=["Build two small data files once from the free GeoNames exports (CC BY 4.0): "
              f"{c('data/us_cities.csv')} (city, state, population, lat, lon) from cities1000, and "
              f"{c('data/us_zips.csv')} (zip, city, state, lat, lon) from the US postal-code file. Commit both; "
              "no runtime download.",
              f"{c('--location &quot;Texas&quot;')} (or {c('TX')}) -&gt; every Texas city with population at or above "
              f"{c('--min-population')} (default 5,000), largest first. {c('--location &quot;Austin, TX&quot;')} -&gt; one cell.",
              "Run M6 per city. When a city comes back saturated, run it again once per ZIP code of that city. "
              "ZIP cells are never split further.",
              f"All cells share M6's {c('place_id')} set, so a business found from two neighbouring cities is scraped "
              "and paid for once.",
              "Results bleed across borders: drop places whose address state differs from the requested state.",
              f"{c('--locations-file areas.txt')}: one location per line (any country), searched as given, no splitting.",
              "Coverage report in the summary and run log: cells searched, saturated, re-split, unique places. "
              "Dry-run uses the number of cells to estimate search cost."],
         files="google_scraper.py (plan_cells, run_cells) · data/us_cities.csv (new) · data/us_zips.csv (new)",
         deps="none new",
         done="&quot;dentists&quot; in &quot;Texas&quot; returns far more unique places than one statewide search, with no "
              "duplicates and a coverage report."),

    # ---------------- LinkedIn
    dict(id="M9", title="People Discovery (Search + X-Ray Fallback)", tag="PHASE 3 · P0", pri="P0",
         state="Done (Nov 4): 100/100 in Texas, HR title, website",
         goal="Turn &quot;HR people at tech companies in Texas&quot; into people with LinkedIn URL, current title, company and "
              "company website, using a people-database search through treg (no LinkedIn login). M1 measured it at "
              "7,532 matches for this example, 20/20 in Texas. Google X-ray search is the fallback.",
         used_by="LinkedIn scraper (people mode)",
         what=["Expand the role into job-title variants.",
               f"Search {c('icypeas.people.search')} with title, profile location and industry filters, paging to the limit.",
               "De-duplicate by profile.",
               f"Fallback {c('--source xray')}: Google X-ray search for LinkedIn profile URLs."],
         how=[f"Role synonyms live in a small dict in linkedin_scraper.py, e.g. {c('HR')} -&gt; HR Manager, HR Director, "
              "Human Resources, Talent Acquisition, Recruiter, People Operations. "
              f"{c('--titles')} overrides the list.",
              f"Query: {c('currentJobTitle')} include the variants; {c('profileLocation')} include "
              f"{c('&quot;Texas, United States&quot;')} (M1: 20/20 in-state; the plain {c('location')} filter gave 13/20); "
              f"{c('currentCompany.industry')} include LinkedIn industry names (tech -&gt; Software Development, IT Services "
              f"and IT Consulting, Technology, Information and Internet); {c('--size')} maps to "
              f"{c('currentCompany.headcount')}.",
              f"Page with {c('pagination.size')} = min(100, rows still needed) and pass back {c('pagination.token')} until "
              "--limit or no token. Size is the price dial: $0.00038 per row returned.",
              f"De-duplicate by the profile slug of {c('profileUrl')}; store {c('search_query')} on each person.",
              f"X-ray fallback: {c('treg.google.serp.organic')} with {c('site:linkedin.com/in (&quot;HR Manager&quot; OR &quot;Recruiter&quot;) &quot;Austin&quot; &quot;software&quot;')}, "
              "limit 100 ($0.0015, 76 profiles in M1), 3-4 titles per query, state expanded to cities from "
              f"{c('data/us_cities.csv')}. Parse {c('Name - Title - Company | LinkedIn')} from result titles; these rows "
              "go through M10's lookup."],
         files="linkedin_scraper.py (title_variants, people_search, xray_search, normalize_profile_url)",
         deps="none new",
         done="&quot;HR&quot; + tech + Texas returns the requested number of unique people, with company website for 90%+."),

    dict(id="M10", title="Profile Check & Fallback Lookup", tag="PHASE 3 · P0", pri="P0",
         state="Done (Nov 4): domain 20/20 people, 18/20 X-ray; drops logged",
         goal="Make sure every person really matches (current title, requested state) and has a company domain for the "
              "email finder. People-search rows already carry company name, website, size and industry, so paid "
              "lookups are only needed for X-ray rows and missing domains.",
         used_by="LinkedIn scraper",
         what=["Local hard filters with logged reasons.",
               "Company domain from the row's company website.",
               "Paid lookups only for X-ray rows and missing domains.",
               "Map to the people CSV columns."],
         how=["Filters: the current title matches a title variant; the address is in the requested state (state name, "
              f"code, or a city / metro from {c('data/us_cities.csv')}, e.g. &quot;Dallas-Fort Worth&quot;). Each rejection "
              "is logged with its reason.",
              f"Domain = host of {c('lastCompanyWebsite')} without www. Missing: the company's LinkedIn page via "
              f"{c('blitzapi.companies.linkedin-to-domain')} ($0.000495, misses free), then one "
              f"{c('treg.companies.search')} by name (leadocean, ~$0); only a row whose name matches is used.",
              f"X-ray rows: {c('treg.people.enrich')} with the profile URL ($0.000495) for name, title, company and "
              "location, then the same filters.",
              f"Map icypeas fields: {c('firstname, lastname, headline, lastJobTitle, address, profileUrl, lastCompanyName, lastCompanyUrl, lastCompanyWebsite, lastCompanySize, lastCompanyIndustry')} "
              "to the people columns from M3."],
         files="linkedin_scraper.py (check_person, company_domain, map_person)",
         deps="none new",
         done="On 20 sample people the domain is filled for 90%+, and out-of-state or non-matching people are dropped "
              "with a reason."),

    dict(id="M11", title="Work Email & Phone Finder", tag="PHASE 3 · P0", pri="P0",
         state="Done (Nov 4): work email 17/20; fallback misses free",
         goal="Find each qualified person's work email, and optionally phone. LinkedIn does not show emails publicly, "
              "so they come from email-finder providers through treg: a cheap tier first, a capped fallback for misses.",
         used_by="LinkedIn scraper (people mode)",
         what=["Tier 1: cheap LinkedIn-URL finder (misses free).",
               "Tier 2: routed finder for tier-1 misses only, capped.",
               "Optional verify and phone.",
               "Never pay twice for the same person."],
         how=[f"Tier 1: {c('blitzapi.people.email.find')} with {c('person_linkedin_url')}: $0.000495 per hit, misses "
              "free; M1 hit rate 15/30 (50%).",
              f"Tier 2 for misses: {c('treg.people.email.find')} with linkedin_url, first/last name and domain; headers "
              f"{c('X-Treg-Route-Max-Cost: 0.011')} and {c('X-Treg-Route-Exclude: blitzapi,enrow,orbisearch,tomba')}; "
              "180 s timeout. Only per-hit providers are left (quickenrich, trykitt, aiark, moltsets), so a miss is free "
              "and a hit costs $0.0048-$0.01. orbisearch billed $0.0062 on every attempt for a 1.6% hit rate; tomba found "
              f"nothing in 7 tries and took 72% of the run's time. M11: 8 of 11 tier-1 misses found. {c('--no-fallback')} "
              "skips it.",
              f"Never send {c('X-Treg-Route-Verify')} on the finder: in M1 it tried 5 providers, ran past 120 s and cost "
              f"$0.018. With {c('--verify')}, call {c('treg.people.email.verify')} separately (up to $0.00124); a "
              "bounced address counts as a miss, so the next tier is tried.",
              f"Write {c('email')}, {c('email_status')}, {c('email_confidence')}, and {c('email_source')} = the provider "
              "that served it. No result: leave it empty; never write unverified guesses such as first.last@domain.",
              f"With {c('--phone')} call {c('treg.people.phone.find')} (cap $0.005; no provider bills misses).",
              "The resume file (M3) is keyed by profile slug, so re-runs never pay for the same person again."],
         files="linkedin_scraper.py (find_contact)",
         deps="none new",
         done="On 20 qualified people about half or more get a work email (M1 rate), and nobody is looked up twice."),

    dict(id="M12", title="LinkedIn Scraper CLI & Company Mode", tag="PHASE 3 · P0/P1", pri="P0/P1",
         state="Done (Nov 4): L-A1..L-A4 pass; 30 people in 42 s",
         goal="One command with two modes: people (default, P0) for contacts like HR managers, and company (P1) for "
              "company-level leads.",
         used_by="LinkedIn scraper",
         what=["People-mode command line (P0).",
               "Company mode: company search or URL list -&gt; company profile -&gt; website deep scrape (P1).",
               "Same common flags as the Google scraper."],
         how=[f"People: {c('python linkedin_scraper.py --role &quot;HR&quot; --industry tech --location &quot;Texas&quot; --limit 300 --max-cost 20 --out hr_texas.csv')}. "
              f"Flags: {c('--role')}, {c('--titles')}, {c('--industry')}, {c('--location')}, {c('--size')}, {c('--source')}, "
              f"{c('--phone')}, {c('--no-fallback')}, {c('--limit')}. The planned {c('--fast')} (skip the profile lookup) "
              "was dropped: the people search needs no lookup, and on X-ray rows M10 found 28 of 48 no longer matching.",
              f"Company: {c('python linkedin_scraper.py --mode company --keyword &quot;SaaS&quot; --location &quot;Texas&quot; --limit 200')} "
              f"or {c('--input company_urls.txt')}. Discovery via {c('icypeas.companies.search')} ($0.00038 per company; "
              f"keyword, location, industry and headcount filters; 2,081 SaaS companies in Texas), not "
              f"{c('treg.companies.search')}, which has no state or city filter. URL lists: one {c('companyId')} lookup "
              "per URL, every URL gets a row. Then the M10 domain step and M4 / M5 for company emails.",
              f"Common flags: {c('--max-cost --dry-run --resume --no-enrich --verify --out')}, plus {c('--paid-emails')} in "
              f"company mode; {c('--suppress')} comes with M14.",
              "People pipeline: M9 queries -&gt; M10 profile + filters -&gt; M11 email -&gt; write row, 8 people at a "
              "time; progress every 10. Ctrl+C finishes the lookups in progress and writes them.",
              f"Dry-run: the free {c('icypeas.people.search.count')} / {c('icypeas.companies.search.count')} gives the "
              "real number of matches, then the measured M10/M11 rates x prices. The balance does not move.",
              "Resume: rows already in the file are skipped before any lookup. Search page tokens expire, so the "
              "search is paged again from the start ($0.00038 per row already in the file).",
              "Never import google_scraper.py, and vice versa: shared code lives only in common.py."],
         files="linkedin_scraper.py (main)",
         deps="none new",
         done="Acceptance tests L-A1 to L-A4 in M15 pass."),

    # ---------------- cross-cutting
    dict(id="M13", title="Errors, Logging & Run Summary", tag="CROSS-CUTTING \u00b7 P1", pri="P1",
         state="Done (Nov 4): network drop -> saved CSV + one-line summary",
         goal="Make every failure visible and safe: a bad website or a provider outage loses one lead, never the run.",
         used_by="Both scrapers",
         what=["One clear message per failure type.",
               "Per-lead error isolation.",
               "Run log you can read after the fact."],
         how=["Wrap each lead in try/except: log the error with the lead key, write the row with what was collected "
              "(empty email/phone), continue.",
              "Website timeouts, SSL errors and 403s are expected: count them, don't print a stack trace.",
              f"Missing {c('TREG_TOKEN')} or an unknown capability in endpoints.json fails at start-up with one line "
              "telling the user what to fix.",
              f"The JSONL run log (M2) plus an {c('errors')} count in the summary is all the logging needed; no logging "
              "framework.",
              f"Built: every stop reason is a {c('StopRun')} (budget, empty balance, treg unreachable, token rejected). "
              "The run ends with the CSV saved and a summary; rows in progress are dropped, not written half-empty, so "
              f"{c('--resume')} redoes them. Before M13 a network drop was a per-lead error, so people were written "
              "without emails and resume never looked them up again. Failed calls are counted by status in the "
              f"summary; unexpected errors go to the run log with the lead key and file:line ({c('Treg.note')})."],
         files="common.py \u00b7 both scrapers",
         deps="none new",
         done="Pulling the network cable mid-run ends with a saved CSV and a readable summary."),

    dict(id="M14", title="Compliance & Suppression List", tag="CROSS-CUTTING \u00b7 P1", pri="P1",
         state="Done (Nov 4): suppressed domains in 0 output CSVs",
         goal="Keep the lead lists usable legally: no opted-out contacts, sources recorded, data kept local.",
         used_by="Both scrapers",
         what=["Suppression list applied before writing.",
               "Source recorded for every email and phone.",
               "Owner reads provider terms and outreach rules once."],
         how=[f"{c('--suppress suppress.txt')}: lines are emails or domains; matching emails are removed, matching "
              "domains drop the whole lead. Applied before the row is written.",
              f"{c('email_source')} and {c('source_url')} are always filled, so any contact can be traced to where it came from.",
              "Never log in to LinkedIn or automate a LinkedIn account (it gets restricted fast and breaks LinkedIn's terms); "
              "profile data comes from providers through "
              f"treg. Read each provider's terms shown in {c('treg catalog get')}.",
              "Outreach to EU/UK contacts needs a documented lawful basis (GDPR); US email outreach must follow CAN-SPAM "
              "(identification + unsubscribe). Named personal emails are personal data; generic info@ addresses are "
              "lower risk.",
              "CSV files stay on this machine and are git-ignored (M0).",
              f"Built: the list is applied inside the CSV writer, so no code path can write around it. With no flag, "
              f"{c('suppress.txt')} in the current folder is used when it exists (git-ignored: it holds emails). Lines may "
              f"be emails, domains, {c('@domain')} or URLs; subdomains match. A business or company whose domain is listed "
              "is skipped before any paid lookup and does not count toward --limit. Not built: a phone-source column "
              "(source_url traces each row), and rows already in a resumed file are not re-checked."],
         files="common.py (load_suppression) \u00b7 suppress.txt (user-maintained)",
         deps="none new",
         done="A domain in suppress.txt never appears in any output CSV."),

    dict(id="M15", title="Testing & README", tag="CROSS-CUTTING · P1", pri="P1",
         state="Built (Nov 4): 57 checks, X-A1 in Excel; awaiting sign-off",
         goal="One small test file for the logic that can silently go wrong, real acceptance runs for each scraper, "
              "and a README so the tool can be used without this document.",
         used_by="Both scrapers",
         what=["Unit checks for parsing, query building and CSV logic.",
               "Acceptance runs against real treg endpoints.",
               "README with setup and examples."],
         how=[f"{c('test_common.py')} (plain asserts): email extraction incl. Cloudflare and [at] forms, junk filtering, "
              "JSON-LD parsing, phone format, profile and company URL normalization, LinkedIn result-title parsing, X-ray "
              "query length, state -&gt; cities planning, resume skips existing keys, budget stop.",
              "G-A1: &quot;dentists&quot; / &quot;Austin, TX&quot; / --limit 50 gives 50 unique rows; phone 90%+, website 80%+, "
              "email at or above the rate measured in M1, description filled for most sites. "
              "G-A2: --max-cost 0.10 stops at or below $0.10. G-A3: kill mid-run, --resume gives no duplicates and no "
              "repeat paid calls.",
              "G-A4: &quot;dentists&quot; / &quot;Texas&quot; / --limit 1000 splits into cities, re-splits saturated cities by ZIP, "
              "no duplicates, coverage report printed.",
              "L-A1: --role HR --industry tech --location Texas --limit 50 gives 50 unique people with name, title, "
              "company and profile URL, all in Texas with a matching title; work email at or above the M1 rate. "
              "L-A2: --mode company --input with 20 URLs returns exactly those 20. L-A3: --dry-run leaves the treg "
              "balance unchanged. L-A4: --resume never repeats an email lookup.",
              "X-A1: every CSV opens in Excel with correct characters, and the first 12 columns match in name and order.",
              f"{c('README.md')}: setup (M0), commands with examples, all flags, columns, cost notes, compliance notes.",
              f"Built: 57 offline checks in {c('test_common.py')}, {c('test_google.py')} and {c('test_linkedin.py')} "
              "(plain asserts, no pytest needed). G-A1..G-A4 ran in M7-M8, L-A1..L-A4 in M12. X-A1, checked in Excel: all "
              "three CSVs open with the shared first 12 columns, accented text intact and phones as text."],
         files="test_common.py (new) · README.md (new)",
         deps="pytest (dev, optional)",
         done="All checks pass and the owner signs off on one real CSV from each scraper."),

    dict(id="M16", title="Graphify Architecture Update", tag="FINAL", pri="Final",
         state="Do last, after implementation",
         goal="Update the project's knowledge graph so it shows the real, final architecture, only after the scrapers "
              "are built and working.",
         used_by="Project documentation",
         what=["Represent the built scrapers and shared core in Graphify.", "Do this last."],
         how=["Once M0-M15 (or the P0 subset) work, run the /graphify skill on the project folder.",
              "Make sure it shows: the two scraper entry points, common.py (treg client, enrichment, CSV writer), "
              "endpoints.json and the treg capabilities behind it, and the CSV outputs.",
              "Do this after implementation, so the graph documents what was actually built rather than this plan."],
         files="graphify-out/ (generated)",
         deps="none",
         done="The graph matches the code."),
]

GROUPS = [("PHASE 1: SHARED CORE", ["M0", "M1", "M2", "M3", "M4", "M5"]),
          ("PHASE 2: GOOGLE SCRAPER", ["M6", "M7", "M8"]),
          ("PHASE 3: LINKEDIN SCRAPER", ["M9", "M10", "M11", "M12"]),
          ("CROSS-CUTTING: FINISH BEFORE REAL USE", ["M13", "M14", "M15", "M16"])]
BY_ID = {m["id"]: m for m in MODULES}

# ---------------------------------------------------------------- page 1
story.append(Paragraph("Lead Scraper Suite<br/>Build Modules \u2014 treg Action Plan", TITLE))
story.append(Spacer(1, 6))
story.append(Paragraph(
    "Every module required to go from an empty folder to two working, independent lead scrapers (LinkedIn and Google) "
    "that output lead CSVs with emails, phone numbers and every public company detail. For each module: "
    "<b>what to do</b> and <b>exactly how to do it</b>, based on the Lead Scraper PRD v1.0 (Oct 29, 2026) and the "
    "treg registry (github.com/superdesigndev/treg). <b>Updated:</b> Google covers whole states (state -&gt; cities -&gt; ZIP codes) and scrapes each website in full; LinkedIn finds people through a people-database search, with Google X-ray as fallback (e.g. HR in Texas), and gets their work emails. <b>M1 results (Oct 29) are folded in.</b>", INTRO))
story.append(Spacer(1, 4))
story.append(Table([[""]], colWidths=[120 * mm], style=[("LINEABOVE", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc"))]))
story.append(Spacer(1, 8))

rows = [[Paragraph(h, CELLH) for h in ["Module", "Scope", "Priority", "Current State"]]]
style = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("GRID", (0, 0), (-1, -1), 0.4, GRID),
         ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3.5),
         ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]
for name, ids in GROUPS:
    style += [("SPAN", (0, len(rows)), (-1, len(rows))), ("BACKGROUND", (0, len(rows)), (-1, len(rows)), colors.HexColor("#e9edf5"))]
    rows.append([Paragraph(name, PHASE), "", "", ""])
    for i, mid in enumerate(ids):
        m = BY_ID[mid]
        if i % 2:
            style.append(("BACKGROUND", (0, len(rows)), (-1, len(rows)), BAND))
        rows.append([Paragraph(mid, CELLB), Paragraph(m["title"], CELL), Paragraph(m["pri"], CELL),
                     Paragraph(m["state"], CELL)])
story.append(Table(rows, colWidths=[17 * mm, 70 * mm, 18 * mm, 77 * mm], repeatRows=1, style=style))
story.append(Spacer(1, 6))
story.append(Paragraph(
    "<b>Priority:</b> P0 = needed for the core loop (search -&gt; details -&gt; enrich -&gt; CSV) \u00b7 P1 = needed "
    "before real client use \u00b7 Final = after implementation. &quot;Current State&quot; assumes an empty project folder. File "
    "names are the proposed structure from the PRD (linkedin_scraper.py, google_scraper.py, common.py, "
    "endpoints.json); rename them if you prefer.", SMALL))
story.append(Spacer(1, 8))
story.append(Paragraph(
    "<b>Recommended working rule:</b> implement one module at a time, in the order above. Test every module with "
    "<b>--dry-run and tiny limits (5-10 leads)</b> first, check the cost in the run log, then run it for real and "
    "review the diff before moving on. Do not attempt both scrapers in one step. Phase 2 (Google) comes before Phase 3 "
    "(LinkedIn) because Maps listings already include phone and website, so the shared enrichment gets tested on the "
    "easier source first. Module M16 (Graphify) is always last.", P))

# ---------------------------------------------------------------- how to use
story.append(Spacer(1, 4))
story.append(Paragraph("How to use this plan", H3))
for t in [
    "<b>One module = one work session.</b> Ask Claude: <i>&quot;Implement M2 from Lead-Scraper-Build-Modules.pdf&quot;</i>. "
    "Each module lists its goal, steps, files and a <b>Done when</b> check, so it can be built and verified on its own.",
    "<b>Check &quot;Done when&quot; before moving on.</b> If it fails, fix it inside the same module. Later modules assume "
    "earlier ones work.",
    "<b>M1 decides everything after it.</b> Do not start M6 or M9 until M1 has real endpoint IDs and sample responses; "
    "if a LinkedIn or Google capability is missing, change the plan there, not halfway through a scraper.",
    "<b>Update the Current State column</b> as modules finish (Not started -&gt; In progress -&gt; Done) so this file "
    "stays the project's progress tracker.",
    "<b>Need only one scraper?</b> Build Phase 1 + that scraper's phase + M13-M15. Google-only skips M9-M12; "
    "LinkedIn-only skips M6-M8, except the US city list from M8 step 1, which M9 needs.",
]:
    story.append(Paragraph(t, BULLET, bulletText="\u2022"))


# ---------------------------------------------------------------- modules
def banner(m):
    t = Table([[Paragraph(f"{m['id']} \u2014 {m['title']}", BANNER), Paragraph(m["tag"], TAG)]],
              colWidths=[W - 40 * mm, 40 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                           ("LEFTPADDING", (0, 0), (0, 0), 10), ("RIGHTPADDING", (-1, 0), (-1, 0), 10)]))
    return t


def steps(items):
    t = Table([[Paragraph(str(i), NUM), Paragraph(s, P)] for i, s in enumerate(items, 1)],
              colWidths=[8 * mm, W - 8 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2),
                           ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    return t


def keybox(m):
    t = Table([[Paragraph(f"<b>Key files:</b> {m['files']}", BOX)],
               [Paragraph(f"<b>New dependencies:</b> {m['deps']}", BOX)],
               [Paragraph(f"<b>Done when:</b> <font face='Helvetica'>{m['done']}</font>", BOX)]],
              colWidths=[W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), BOXBG), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                           ("TOPPADDING", (0, 0), (-1, 0), 6), ("BOTTOMPADDING", (0, -1), (-1, -1), 6)]))
    return t


for m in MODULES:
    story.append(Spacer(1, 12))
    story.append(KeepTogether([
        banner(m), Spacer(1, 6),
        Paragraph(f"<b>Goal:</b> {m['goal']}", GOAL),
        Paragraph(f"<b>Used by:</b> {m['used_by']}", SMALL),
        Paragraph("What to do", H3),
        *[Paragraph(w, BULLET, bulletText="\u2022") for w in m["what"]],
    ]))
    story.append(Paragraph("How to do it", H3))
    story.append(steps(m["how"]))
    if "table" in m:
        story.append(Spacer(1, 4))
        tr = m["table"]
        data = [[Paragraph(x, CELLH) for x in tr[0]]] + [[Paragraph(x, CELL) for x in r] for r in tr[1:]]
        story.append(Table(data, colWidths=[60 * mm, 22 * mm, W - 82 * mm], repeatRows=1, style=[
            ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("GRID", (0, 0), (-1, -1), 0.4, GRID),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAND]), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(Spacer(1, 6))
    story.append(KeepTogether([keybox(m)]))

story.append(Spacer(1, 16))
story.append(Paragraph("Lead Scraper Suite \u00b7 Build Modules \u2014 treg Action Plan \u00b7 Build specification "
                       "derived from the Lead Scraper PRD v1.0 and the treg registry documentation.", SMALL))


class NumberedCanvas(rl_canvas.Canvas):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._pages = []

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        n = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(colors.HexColor("#999999"))
            self.drawString(16 * mm, 10 * mm, FOOT)
            self.drawRightString(letter[0] - 16 * mm, 10 * mm, f"Page {self._pageNumber} of {n}")
            super().showPage()
        super().save()


SimpleDocTemplate(OUT, pagesize=letter, leftMargin=17 * mm, rightMargin=17 * mm, topMargin=16 * mm,
                  bottomMargin=18 * mm, title="Lead Scraper Suite - Build Modules (treg)",
                  author="Lead Scraper Suite").build(story, canvasmaker=NumberedCanvas)
print("wrote", OUT)
