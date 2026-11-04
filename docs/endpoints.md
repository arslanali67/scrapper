# M1 endpoint discovery: findings

Run 2026-10-29 against treg.to, team `arslan-s`. Total spend for M1: **$0.069** (balance $1.00 -> $0.93).
Chosen IDs, caps and example params are in [`endpoints.json`](../endpoints.json). Raw sample responses are in `samples/` (git-ignored: they contain real people's data).

## Rules every call must follow

- **Always send `X-Treg-Route-Max-Cost`.** Routed `treg.*` endpoints bill misses and keep trying providers up to this cap, and the default is **$1 per call**. Per-capability caps are in `endpoints.json`.
- **Client timeout 180 s.** A routed email lookup took 77 s; one with `X-Treg-Route-Verify` ran more than 120 s.
- **Agent token limits:** `GET /orgs` returns 403. Use `GET /auth/me` for the org id. The balance ledger is empty for agents, so track spend from `X-Treg-Cost-Micro`.

## Measured results

| Capability | Endpoint | Cost seen | Result |
|---|---|---|---|
| Maps search | `crawl4ai.google.serp.maps` | $0.00015 per call | **100 rows per call** (limit 200 still gave 100). Dentists in Austin: website, phone, address, hours, rating and place_id on **100/100** |
| Maps search (routed) | `treg.google.serp.maps` | $0.00015 | Only 20 rows; not used |
| Google results | `treg.google.serp.organic` | $0.0015 at limit 100 | X-ray query gave **76 LinkedIn profile URLs** |
| Page extract | `treg.web.extract` | $0.00015 | Markdown of one page (served by crawl4ai) |
| People search (routed) | `treg.people.search` | $0.0038 / 10 rows | 10/10 HR titles, **6/10 in Texas**; `cursor` did not page |
| People search (direct) | `icypeas.people.search` | $0.00038 per row | `profileLocation` filter: **20/20 in Texas**, 7,532 matches, paging works. Plain `location`: 13/20 |
| Person profile | `treg.people.enrich` | $0.000495 | name, title, company, location from a LinkedIn URL |
| Company profile | `treg.linkedin.company.profile` | $0.001 | name, description, followers, employees, location; no website |
| Company search | `treg.companies.search` | $0.000178 | **50 companies** with website, phone, LinkedIn URL, city, state |
| Company enrich | `treg.companies.enrich` | $0.0049 | Only name + domain. **Dropped** |
| Work email, tier 1 | `blitzapi.people.email.find` | $0.000495 per hit, misses free | **15/30 (50%)** |
| Work email, tier 2 | `treg.people.email.find` (exclude blitzapi, enrow; cap $0.011) | ~$0.006 per attempt | 1/4 of tier-1 misses |
| Email verify | `treg.people.email.verify` | $0.00124 | `valid / status / score` |
| Person phone | `treg.people.phone.find` | $0 (miss) | 0/1 |
| Company emails by domain | `hunter.companies.emails` | reserves $0.0245 | Refused under a $0.02 cap; opt-in only |
| Free website crawl | (no treg) homepage + /contact | free | Email on **8/20** dentist sites (40%) with a naive crawl |

## Cost per lead (estimates)

- **Google lead:** about $0.0002 (search share $0.0000015 + occasional JS extract). With `--paid-emails`, add ~$0.0245 for each business whose website shows no email.
- **LinkedIn person with work email:** about $0.0009 with tier 1 only; about $0.0035 with the tier-2 fallback. 1,000 people ≈ $1-4.
  Measured in M11 (fallback misses now free): **$0.0028 per person, $0.0033 per email found**; `--verify` adds ~$0.0009
  per email, `--phone` ~$0.0005 per person.

## Plan changes from these findings

1. **M6:** no place-details call; Maps rows are complete. A cell is saturated at **100** rows.
2. **M8:** split cities that return 100 rows into ZIP codes.
3. **M9/M10:** primary people discovery is `icypeas.people.search` with `currentJobTitle` + `profileLocation` + `currentCompany.industry`. Rows already include company name, website, size and industry, so M10's profile and company lookups are only needed for the X-ray fallback. The local location filter is still kept.
4. **M11:** tier 1 blitzapi, tier 2 routed finder only for misses, and no `X-Treg-Route-Verify`.
5. **M5:** the paid domain search (hunter) is opt-in, not automatic.
6. **M12 company mode:** discovery via `treg.companies.search` (changed in M12 to `icypeas.companies.search`,
   which can filter by state and city).

## Measured later

- **M4 website scrape** (20 Austin dentists, 2026-10-29): 18 sites readable (2 behind a bot check, skipped), email on **10/18**, phone and address on 18/18, $0.
- **M5 Hunter domain search** on the 8 readable sites with no website email (2026-10-30): emails for **3/8**, all verified valid. $0.0245 per domain with results, $0 for the 5 unknown domains; verification added $0-$0.00124. With `--paid-emails` email coverage rises to **13/18 (72%)**, at about $0.025 per extra email found.

- **M8 area coverage** (2026-11-04, Maps data only): "dentists" in Texas, limit 1,000 -> 1,000 unique places from 28
  searches (4 cities, all full, re-searched by 24 ZIP codes) in 36 s for $0.0042. Vermont: one statewide search
  stops at 100; the city split found **377** (3.8x) from 22 searches for $0.0033, dropping 52 from other states.

- **M9 people discovery** (2026-11-04): HR titles x tech industries x `profileLocation` Texas -> 7,760 matches;
  100 rows for $0.038, all 100 unique, in Texas, HR-type title, with company name, website and size.
  X-ray fallback: one query -> 30 unique profiles for $0.0015 with name 30/30, title 29/30, but company only 3/30
  and no website, so X-ray rows need M10's lookup.

- **M10 checks and domains** (2026-11-04): on M1's loose-filter sample the state check dropped exactly the 6 people
  outside Texas (WA, NJ, FL, Pakistan, UK, bare "United States") and kept Dallas-Fort Worth. People search, 20 HR in
  Texas: 0 dropped, domain 20/20 from the company website, $0.0076. X-ray, 20 kept: 48 profiles looked up, 28
  dropped (19 current title no longer HR, 7 outside Texas, 2 no location), domain 18/20 (15 via the company's
  LinkedIn page, 2 name search, 1 website), $0.041 (~$0.002 per kept person, 5x the people search).

- **M11 work email and phone** (2026-11-04, 20 HR people in Texas tech, `--verify --phone`): tier 1 (blitzapi) found
  **9/20** for $0.0045; tier 2 ran only for the 11 misses and found **8/11** (quickenrich 5, trykitt, aiark, moltsets),
  with its 3 misses free. Total **17/20 work emails (85%)**, every person looked up once. All on company domains, no
  free-mail: 10 on the website's domain, 5 on the same brand's other domain (e.g. a short-link website), 1 unrelated
  domain that verified "risky". Verify: 12 valid, 4 risky, 0 invalid (contactout free 4 times, else orbisearch
  $0.00124). Phone: **13/20** for $0.0093 (blitzapi 9, leadocean 3 free, quickenrich 1). Run: $0.0707 logged, 9 min.
  Two exclusions came out of this run (endpoints.json `email_find_person_fallback`):
  - **orbisearch**: the only per-call provider under the cap. It bills $0.0062 on every attempt for a treg-observed hit
    rate of 1.6%, and it was the whole cost of M1's billed misses. Excluding it makes tier-2 misses free.
  - **tomba**: 0 hits in 7 tries, up to 125 s each, 72% of the run's time. It pushed one call past the 180 s client
    timeout; the router went on, moltsets hit, and the $0.01 was billed but never received (`GET /calls` shows it).
    Without tomba, the 4 email-less people re-ran in 7-16 s each: 3 misses ($0) and that moltsets hit.
  `GET /calls` (call history with `cost_charged_micro` per provider attempt) and `GET /orgs/56333/balance` work with
  the agent token.

- **M12 command and company mode** (2026-11-04):
  - **Dry run (L-A3):** `icypeas.people.search.count` and `icypeas.companies.search.count` are free
    (`X-Treg-Cost-Micro: 0`); the balance stayed at 616,932 micro-USD across four dry runs. HR x tech x Texas counts
    7,760, the same as the search's own total.
  - **Page tokens expire:** a 6-day-old icypeas token returned 0 rows and total 0, with no error. So `--resume` pages
    the search again from the start ($0.00038 per row already in the file) and skips those rows before any lookup.
  - **L-A1 + L-A4:** the M11 file (20 people) resumed to `--limit 50`: exactly 30 email lookups, all for new people;
    50/50 in Texas with an HR title; work email **38/50 (76%)**; the 30 new people took **42 s** with 8 in parallel
    (M11: 20 people in 9 min, one at a time, with tomba); $0.0659 ($0.0022 per new person).
  - **Company mode** uses `icypeas.companies.search` ($0.00038 per row). `treg.companies.search` has no state or city
    filter. Filters: `keyword`, `location` ("Texas, United States": 2,081 SaaS companies), `industry`, `headcount`
    (`>=`/`<=` work). `--input` URLs: one `{companyId: {include: [url]}}` lookup each.
  - **L-A2:** 20 company URLs in, exactly those 20 out. Name, industry, HQ, website, description, founded year and
    type 20/20, size band 19/20; website emails 9/20 (3 bot-checked homepages skipped, 2 robots.txt refusals
    respected); $0.0077.
  - **Company search** "SaaS" + Texas, 20 companies: all with a Texas HQ, all mention SaaS; website emails 9/20
    (5 sites unreachable, 2 without a website); $0.0089.
  - **Budget with parallel calls:** each call in flight reserves its cap (8 workers x up to $0.011), which could end
    a small `--max-cost` run early. `Treg` now waits for calls in flight to settle and stops only when the next call
    cannot fit even then.

## Not measured yet

- Per-query depth limit of the X-ray search beyond one query (76 rows at limit 100).
- Recovering the result of a call that outlived the client timeout (it may still bill): M13.
