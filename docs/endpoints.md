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

## Plan changes from these findings

1. **M6:** no place-details call; Maps rows are complete. A cell is saturated at **100** rows.
2. **M8:** split cities that return 100 rows into ZIP codes.
3. **M9/M10:** primary people discovery is `icypeas.people.search` with `currentJobTitle` + `profileLocation` + `currentCompany.industry`. Rows already include company name, website, size and industry, so M10's profile and company lookups are only needed for the X-ray fallback. The local location filter is still kept.
4. **M11:** tier 1 blitzapi, tier 2 routed finder only for misses, and no `X-Treg-Route-Verify`.
5. **M5:** the paid domain search (hunter) is opt-in, not automatic.
6. **M12 company mode:** discovery via `treg.companies.search`.

## Not measured yet

- Per-query depth limit of the X-ray search beyond one query (76 rows at limit 100).
- Phone-find hit rate (1 sample).
- Email hit rate for M4's full crawler (pending M4; 40% is the naive baseline).
