# Lead scrapers (Google + LinkedIn) on treg

Two separate command-line scrapers that write lead CSVs. Each runs only its own data source.

- **`google_scraper.py`**: businesses of one type in a city, ZIP code or whole US state, from Google Maps. Each
  business's website is then scraped for emails, phones, social links, description, services and team.
- **`linkedin_scraper.py`**: people by role, industry and US location (e.g. HR at tech companies in Texas), with
  work emails; or companies by keyword or from a list of LinkedIn company URLs.

All paid data comes through [treg](https://treg.to) with one token. The scrapers never log in to LinkedIn or
automate a LinkedIn account. Websites behind a bot check are skipped, never worked around.

## Setup (once)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

1. Create a treg agent token: `treg org agent-new lead-scraper --all-tools` (needs the treg CLI; see
   `docs/endpoints.md`).
2. Copy `.env.example` to `.env` and put the token in it: `TREG_TOKEN=...`. `.env` is git-ignored; never share it.
3. Check the setup with a free dry run: `python linkedin_scraper.py --role HR --location Texas --dry-run`.

On Windows, use `.venv/Scripts/python -X utf8` instead of `python` so names with accents print correctly.

## Google scraper

```bash
python google_scraper.py --query "dentists" --location "Austin, TX" --limit 300
python google_scraper.py --query "IT companies" --location "Texas" --require-website --max-cost 25
python google_scraper.py --query "plumbers" --locations-file areas.txt --dry-run
```

A state is searched city by city, largest first. A city whose search hits Google's 100-result cap is searched
again ZIP code by ZIP code. Places outside the requested state are dropped.

| Flag | Meaning |
|---|---|
| `--query` | business type, e.g. `"dentists"` (required) |
| `--location` | a state (`Texas`), a city (`"Austin, TX"`) or a ZIP (`78704`) |
| `--locations-file` | one location per line (any country), searched as given |
| `--split auto\|none\|cities\|zips` | `auto` (default): state to cities, full cities to ZIP codes; `none`: one search |
| `--min-population` | smallest city searched when splitting a state (default 5,000) |
| `--limit` | total leads wanted in the output file (default 100) |
| `--out` | output CSV (default `leads_google.csv`) |
| `--max-cost` | hard spend limit in USD for this run (default 5) |
| `--dry-run` | print the estimated cost and exit; spends nothing |
| `--resume` | continue an existing `--out` file, skipping its leads |
| `--no-enrich` | Maps data only: no website scrape, no paid lookups |
| `--paid-emails` | Hunter lookup (~$0.0245) when the website shows no email |
| `--verify` | verify the best email of each lead (up to $0.00124) |
| `--min-rating`, `--min-reviews`, `--require-website` | filters, applied before any enrichment |
| `--suppress` | opt-out list (see Compliance) |

## LinkedIn scraper

```bash
python linkedin_scraper.py --role HR --industry tech --location Texas --limit 300 --max-cost 2
python linkedin_scraper.py --titles "Office Manager,Practice Manager" --location "Austin, TX" --verify --phone
python linkedin_scraper.py --role HR --location Texas --limit 500 --dry-run
python linkedin_scraper.py --mode company --keyword SaaS --location Texas --limit 200
python linkedin_scraper.py --mode company --input company_urls.txt --paid-emails
```

**People mode** (default): people come from a LinkedIn-data search (icypeas through treg). Each person is
checked: the current title must match the role, and the location must be in the requested state. Dropped people
and the reason for each are saved to `runs/<time>-rejected.jsonl`. The company domain is filled in, then the work
email is found in two steps:

1. **blitzapi**, by LinkedIn URL: $0.000495 per hit, misses free.
2. For misses only, **treg's routed finder** (by URL, name and domain): about $0.005 per hit, misses free, capped at
   $0.011 per person. `--no-fallback` skips this step.

No address is ever guessed. `--source xray` finds people with Google X-ray search instead; it costs about 5x more,
because every profile needs a lookup.

**Company mode** (`--mode company`): companies by `--keyword` / `--industry` / `--location` / `--size`, or one row
for every URL in `--input` (a text file or any CSV holding LinkedIn company URLs). Then each company's website is
scraped for emails and phones, as in the Google scraper.

| Flag | Meaning |
|---|---|
| `--mode people\|company` | default `people` |
| `--role` | people: HR, recruiter, sales, marketing, founder, engineering, finance, IT, or any title |
| `--titles` | people: exact job titles, comma-separated (overrides `--role`) |
| `--keyword`, `--input` | company: profile keywords, or a file of LinkedIn company URLs |
| `--industry` | tech, healthcare, finance, marketing, real estate, construction, manufacturing, education, legal, retail, logistics, or exact LinkedIn industry names |
| `--location` | a US state, `"City, ST"` or any place text |
| `--size` | company headcount, e.g. `11-50` or `1000+` |
| `--source people\|xray` | people: the people database (default) or Google X-ray search |
| `--limit` | total people (or companies) wanted in the output file (default 100) |
| `--out` | default `leads_linkedin.csv`, or `leads_linkedin_companies.csv` in company mode |
| `--max-cost` | hard spend limit in USD for this run (default 1) |
| `--dry-run` | the real number of matches (free count) and the estimated cost; spends nothing |
| `--resume` | continue an existing `--out` file; its rows are never looked up again |
| `--no-enrich` | source data only: no email, phone or website lookups |
| `--verify` | verify each found email (up to $0.00124 each) |
| `--phone` | people: also look up each person's phone (misses free) |
| `--paid-emails` | company: Hunter lookup when the website shows no email |
| `--suppress` | opt-out list (see Compliance) |

## Output columns

Every CSV is UTF-8 and opens in Excel with correct characters. Phones are in international format
(`+1 512-474-1234`), so Excel keeps them as text. Lists are joined with ` | `. The **first 12 columns are the same in
all three files**:

`company_name, website, domain, email, all_emails, email_source, phone, all_phones, address, city, country, source_url`

Then:

- **Google:** `place_id, category, all_categories, state, postal_code, latitude, longitude, rating, review_count,
  opening_hours, google_maps_url, facebook, instagram, linkedin, x, youtube, tiktok, site_description, services,
  team_members, contact_form_url, site_address, search_query, search_location, email_status, scraped_at, cost_usd`
- **LinkedIn people:** `full_name, first_name, last_name, job_title, headline, person_location, person_linkedin_url,
  company_linkedin_url, industry, company_size, email_status, email_confidence, search_query, scraped_at, cost_usd`
- **LinkedIn companies:** `linkedin_id, tagline, description, industry, company_size, employees_on_linkedin,
  headquarters, all_locations, founded_year, company_type, specialties, followers, logo_url, funding_info, socials,
  email_status, scraped_at, cost_usd`

`email_source` says where each email came from (`website`, `hunter`, or the finder that served it, e.g. `blitzapi`).
`source_url` is the Google Maps page or LinkedIn profile of the row. `cost_usd` is what that row cost.
`email_status` (with `--verify`) is `valid`, `risky` or `invalid`; invalid addresses never go in `email`.

## Cost

Every call is capped, and a run never spends more than `--max-cost`. Use `--dry-run` first: it is free. Measured on
real runs (details in `docs/endpoints.md`):

| Lead | Cost | Result |
|---|---|---|
| Google business, website scrape only | ~$0.00001 | email 50-56% of businesses, phone ~100% |
| ... with `--paid-emails` | +$0.0245 per business found by Hunter | email ~72% |
| LinkedIn person with work-email search | ~$0.0028 per person ($0.0033 per email found) | work email 76-85% |
| ... with `--verify` / `--phone` | +~$0.0009 per email / +~$0.0005 per person | phone 13/20 |
| LinkedIn company (website scrape only) | ~$0.0004 | email 45% |

For example, 1,000 Texas HR people with work emails cost about $3. Check your balance at treg.to.

## Stopping, resuming and errors

- **Ctrl+C** finishes the lookups in progress, writes them, and prints the summary. Every row is saved the moment
  it is written.
- **`--resume`** continues the same `--out` file. Rows already in it are never looked up or paid for again. In the
  LinkedIn scraper the search pages are bought again ($0.00038 per row already in the file), because search page
  tokens expire.
- The run **stops cleanly**, with the CSV saved and a one-line reason, when the budget is reached, the treg balance
  is empty, treg cannot be reached (network down), or the token is wrong. Then fix the cause and rerun with
  `--resume`. Rows in progress at that moment are redone, never written half-empty.
- One bad website or provider answer loses at most that lead's missing data, never the run. Every call, with its
  cost, and any unexpected error, with the lead and where it happened, are in `runs/<time>.jsonl`.

## Compliance

- **Suppression list:** put opted-out contacts in `suppress.txt` in the folder you run from (it is picked up
  automatically), or pass `--suppress file`. One entry per line: an email, a domain, `@domain` or a URL; `#` starts
  a comment. A listed domain (and its subdomains) drops the whole lead, and such leads cost nothing. A listed email
  is removed from the lead. The list is applied inside the CSV writer, so nothing can bypass it. Rows already in a
  file you `--resume` are not re-checked. `suppress.txt` is git-ignored.
- **Data stays local:** all CSVs, `runs/` and `samples/` are git-ignored. Named people's emails are personal data.
- **Outreach rules:** contacting people in the EU or UK needs a documented lawful basis (GDPR). US email outreach
  must follow CAN-SPAM: say who you are, give a working unsubscribe, and honor it (add the address to
  `suppress.txt`).
- **Provider terms:** read the terms of each data provider once; `treg catalog get <endpoint>` shows them.

## Files

| File | What it is |
|---|---|
| `google_scraper.py`, `linkedin_scraper.py` | the two scrapers; neither imports the other |
| `common.py` | shared code: treg client and budget, CSV writer, website scrape, paid email, suppression |
| `endpoints.json` | the treg endpoint, price and cap per capability; change providers here only |
| `data/` | US states, cities and ZIP codes (GeoNames, CC BY 4.0; see `data/README.md`) |
| `docs/endpoints.md` | every measurement behind the choices above |
| `Lead-Scraper-Build-Modules.pdf` | the build plan (source: `docs/build_plan_pdf.py`) |

## Tests

Offline checks with no network and no spend:

```bash
python test_common.py
```

```bash
python test_google.py
```

```bash
python test_linkedin.py
```

The acceptance runs against real treg endpoints (G-A1 to G-A4, L-A1 to L-A4, X-A1) and their results are recorded
in `docs/endpoints.md` and the build plan.
