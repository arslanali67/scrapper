# Graph Report - linkedin_scraper  (2026-11-05)

## Corpus Check
- Corpus is ~36,445 words - fits in a single context window. You may not need a graph.

## Summary
- 420 nodes · 894 edges · 14 communities (10 shown, 4 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 76 edges (avg confidence: 0.89)
- Token cost: 110,756 input · 0 output

## Community Hubs (Navigation)
- LinkedIn Scraper Pipeline
- Errors & Stop Handling
- Google Scraper Pipeline
- Website Scrape & CSV Helpers
- LinkedIn Test Fakes
- Docs, Plan & Endpoints
- Suppression List
- Google Scraper Tests
- Build Scripts (PDF, Area Data)
- Treg Client & Budget
- Treg Errors & Test Doubles
- HTML Page Parser
- Resume & Page Tokens
- Compliance Notes

## God Nodes (most connected - your core abstractions)
1. `main()` - 26 edges
2. `TregError` - 25 edges
3. `main()` - 25 edges
4. `crawl_site()` - 20 edges
5. `domain_of()` - 18 edges
6. `Treg` - 17 edges
7. `BudgetReached` - 16 edges
8. `LeadWriter` - 16 edges
9. `StopRun` - 15 edges
10. `treg()` - 15 edges

## Surprising Connections (you probably didn't know these)
- `M4 website deep scrape (crawl_site, extract_contacts, extract_details)` --references--> `treg.web.extract`  [INFERRED]
  Lead-Scraper-Build-Modules.pdf → docs/endpoints.md
- `X-Treg-Route-Max-Cost header rule` --rationale_for--> `M2 treg client & cost control (call, BudgetReached, estimate)`  [EXTRACTED]
  docs/endpoints.md → Lead-Scraper-Build-Modules.pdf
- `180 s client timeout` --rationale_for--> `M2 treg client & cost control (call, BudgetReached, estimate)`  [EXTRACTED]
  docs/endpoints.md → Lead-Scraper-Build-Modules.pdf
- `M9 people discovery (search + X-ray fallback)` --references--> `icypeas.people.search`  [EXTRACTED]
  Lead-Scraper-Build-Modules.pdf → docs/endpoints.md
- `M9 people discovery (search + X-ray fallback)` --references--> `treg.google.serp.organic (X-ray search)`  [EXTRACTED]
  Lead-Scraper-Build-Modules.pdf → docs/endpoints.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **LinkedIn people pipeline (discover -> check -> email -> write)** — lead_scraper_build_modules_m9_people_discovery, lead_scraper_build_modules_m10_profile_check, lead_scraper_build_modules_m11_email_phone_finder, lead_scraper_build_modules_m3_leadwriter [EXTRACTED 1.00]
- **Google pipeline (area cells -> Maps search -> website scrape -> paid email -> write)** — lead_scraper_build_modules_m8_area_coverage, lead_scraper_build_modules_m6_maps_search, lead_scraper_build_modules_m4_website_scrape, lead_scraper_build_modules_m5_paid_enrichment, lead_scraper_build_modules_m3_leadwriter [EXTRACTED 1.00]
- **Cost control mechanisms** — readme_budget_max_cost, docs_endpoints_route_max_cost, docs_endpoints_parallel_budget, lead_scraper_prd_cost_model, lead_scraper_build_modules_m2_treg_client [INFERRED 0.85]

## Communities (14 total, 4 thin omitted)

### Community 0 - "LinkedIn Scraper Pipeline"
Cohesion: 0.05
Nodes (44): cities_of(), state_code(), states(), treg.people.enrich, M10 profile check & fallback lookup (check_person, company_domain, map_person), build_query(), check_person(), _city_re() (+36 more)

### Community 1 - "Errors & Stop Handling"
Cohesion: 0.07
Nodes (37): BudgetReached, crawl_site(), fetch(), OutOfBalance, _parse(), require(), StopRun, TregUnavailable (+29 more)

### Community 2 - "Google Scraper Pipeline"
Cohesion: 0.08
Nodes (23): enrich_calls(), LeadSpend, zip_state(), _zips_by_city(), zips_of(), dry_run_cost(), enrich(), main() (+15 more)

### Community 3 - "Website Scrape & CSV Helpers"
Cohesion: 0.08
Nodes (16): cell(), _cities_by_state(), _contact_first(), _decode_cfemail(), domain_of(), email_status(), extract_contacts(), extract_details() (+8 more)

### Community 4 - "LinkedIn Test Fakes"
Cohesion: 0.08
Nodes (25): enrich_profile(), company_row(), CompanyTreg, Fake, PagedTreg, person(), run(), test_build_query() (+17 more)

### Community 5 - "Docs, Plan & Endpoints"
Cohesion: 0.06
Nodes (32): US area data (us_states/us_cities/us_zips.csv), GeoNames (CC BY 4.0), blitzapi.people.email.find (tier 1), crawl4ai.google.serp.maps, treg.people.email.verify, treg.google.serp.organic (X-ray search), hunter.companies.emails (opt-in), icypeas.companies.search (+24 more)

### Community 6 - "Suppression List"
Cohesion: 0.10
Nodes (12): LeadWriter, load_suppression(), Suppression, M3 LeadWriter, CSV output & resume, Common behaviour C1-C9 (dry-run, max-cost, resume, retry, suppress), Cost-per-lead formula (dry-run), Hard budget (--max-cost) and --dry-run, Shared first 12 CSV columns (+4 more)

### Community 7 - "Google Scraper Tests"
Cohesion: 0.12
Nodes (17): Acceptance tests G-A1..G-A4, L-A1..L-A4, X-A1, Offline tests (test_common.py, test_google.py, test_linkedin.py), AreaTreg, biz(), fake_crawl(), FakeTreg, places(), read() (+9 more)

### Community 9 - "Treg Client & Budget"
Cohesion: 0.13
Nodes (6): estimate(), load_token(), Treg, M2 treg client & cost control (call, BudgetReached, estimate), httpx>=0.28, test_estimate()

### Community 10 - "Treg Errors & Test Doubles"
Cohesion: 0.16
Nodes (10): enrich_paid(), TregError, _ScriptedTreg, test_enrich_paid_failure_not_cached(), boom(), test_enrich_paid_flow(), test_company_domain_paths_and_cache(), boom() (+2 more)

## Ambiguous Edges - Review These
- `LinkedIn people mode` → `LinkedIn requirements L1-L8 (company-focused)`  [AMBIGUOUS]
  Lead_Scraper_PRD.pdf · relation: conceptually_related_to

## Knowledge Gaps
- **21 isolated node(s):** `Shared first 12 CSV columns`, `--resume (never pay twice)`, `Clean stop on budget/balance/network/token errors`, `Compliance (GDPR, CAN-SPAM, data stays local)`, `GeoNames (CC BY 4.0)` (+16 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 160 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `LeadWriter` connect `Suppression List` to `LinkedIn Scraper Pipeline`, `Errors & Stop Handling`, `Google Scraper Pipeline`, `Website Scrape & CSV Helpers`?**
  _High betweenness centrality (0.077) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `main()` (e.g. with `StopRun` and `.failures()`) actually correct?**
  _`main()` has 4 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Shared first 12 CSV columns`, `--resume (never pay twice)`, `Clean stop on budget/balance/network/token errors` to the rest of the system?**
  _21 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `LinkedIn Scraper Pipeline` be split into smaller, more focused modules?**
  _Cohesion score 0.0518326545723806 - nodes in this community are weakly interconnected._
- **Why does `TregError` connect `Treg Errors & Test Doubles` to `LinkedIn Scraper Pipeline`, `Errors & Stop Handling`, `Google Scraper Pipeline`, `Website Scrape & CSV Helpers`, `LinkedIn Test Fakes`, `Treg Client & Budget`?**
  _High betweenness centrality (0.076) - this node is a cross-community bridge._
- **Are the 10 inferred relationships involving `TregError` (e.g. with `main()` and `company_domain()`) actually correct?**
  _`TregError` has 10 INFERRED edges - model-reasoned connections that need verification._

### Low-confidence Hints
_AMBIGUOUS edges — the extractor was unsure. Verify before acting on these._

- **What is the exact relationship between `LinkedIn people mode` and `LinkedIn requirements L1-L8 (company-focused)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._