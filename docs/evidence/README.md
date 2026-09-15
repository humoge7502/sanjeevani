# SANJEEVANI - Real-Data Package (source PDFs + live web enrichment)

**Version:** 2.1.0 (FINAL)  |  **Generated:** 2026-09-15  |  **Records:** 477 across 44 sections | **Evidence files:** raw search JSONs for every web record

## What this is
One merged, machine-readable dataset with THREE collections:
1. **Source PDFs collection** - every real data point in your two source documents (the 12-page
   *3-Member Workflow & AI Handoff* spec and the 89-page *SAP Hackfest 2026 Unified Dossier*),
   extracted verbatim-faithful with full provenance (org, date, document, page, chapter).
2. **Web enrichment collection (v2.0.0+, refreshed in v2.1.0)** - real-world data gathered from the live web on 2026-09-16
   to complete everything the project needs beyond the PDFs: live freight indices, tariff status,
   port congestion, refreshed market sizes, competitor funding updates, SAP API surfaces, India data,
   regulatory primary sources, AI model licenses and free demo data feeds. Every web record carries
   publisher + URL + access date. Where the PDFs and the web disagree (e.g. newer funding rounds,
   tariff changes), both are kept so you can see what moved.
3. **Raw evidence archive (v2.1.0)** - the 58 raw JSON result files of the web searches themselves
   (`web_evidence/`), so every SJEV-WEB-* record is independently verifiable: EVIDENCE_INDEX.md maps
   each file to the master-JSON section it supports.

Nothing was invented or synthesized: each record quotes its source.

> Why SPEC/CONSTRUCTED exist: the dossier itself labels claims FACT, INFERENCE or HYPOTHESIS and labels
> financial numbers ASSUMPTION. Design decisions (architecture, agent specs, scores, contracts) and the
> Meera-Iyer persona are not empirical claims - they are tagged SPEC and CONSTRUCTED so you never mistake
> a design choice for measured reality.

## Folder layout
```
sanjeevani_real_data/
├── README.md                                  <- this file
├── INDEX.md                                   <- every record ID, searchable table
├── data/
│   ├── sanjeevani_real_data_master.json       <- THE merged master dataset (one file, all sections)
│   └── sanjeevani_real_data_quantitative.csv  <- flat CSV of every quantitative record (157 rows)
├── contracts/                                 <- the 6 frozen shared JSON contracts (verbatim)
│   ├── event.schema.json          ├── impact.schema.json
│   ├── recovery_plan.schema.json  ├── approval.schema.json
│   ├── execution_receipt.schema.json          └── outcome.schema.json
├── web_evidence/                              <- raw JSON results of all 58 web searches
│   └── EVIDENCE_INDEX.md                      <- file-by-file map to the records they prove
└── source_pdfs/                               <- your two original PDFs, archived verbatim
```

## The master JSON
```json
{
  "dataset": "SANJEEVANI_REAL_DATA_MASTER",
  "record_count": 477,
  "sections": [ { "key": "headline_metrics", "title": "...", "record_count": N }, ... ],
  "data": {
    "headline_metrics": [ { "id": "SJEV-STAT-001", "title": "...", "value": 184, "unit": "USD billion/year",
                             "fact_class": "FACT", "source_org": "J.S. Held (via netsuite.com)",
                             "source_date": "2026-04-16", "document": "...Dossier.pdf",
                             "page": 2, "pdf_page": 8, "chapter": "...", "details": "...", "tags": [...] }, ... ],
    "...": [ ... ]
  }
}
```
Record ID scheme: `SJEV-<SECTION>-<NNN>`.

## What is inside (sections)
- **Headline & macro metrics** (`headline_metrics`) - 7 records
- **Disruption case studies (Red Sea, tariffs, chips, ports, cyber)** (`disruption_case_studies`) - 18 records
- **Pharma cold-chain losses & excursion science** (`pharma_cold_chain_losses`) - 13 records
- **Market sizes & benchmarks** (`market_sizes`) - 8 records
- **Competitor & player landscape** (`competitors`) - 16 records
- **Capability-gap matrix (Table 3)** (`capability_matrix`) - 13 records
- **Solved vs open research map (Table 5) & five claimable gaps** (`solved_vs_open`) - 13 records
- **Research evidence shelf (papers, benchmarks, citations)** (`research_papers`) - 28 records
- **Hugging Face / open model stack (Table 6)** (`open_source_models`) - 9 records
- **Open-source tools, licenses & datasets (Table 7)** (`open_source_tools`) - 12 records
- **SAP ecosystem facts, APIs & license reality** (`sap_ecosystem`) - 22 records
- **Regulatory stack (EU GDP, WHO, USP, FDA, CDSCO)** (`regulatory`) - 13 records
- **India ground reality (exports, 1% problem, eVIN)** (`india_data`) - 23 records
- **Patent prior art & white-space hypotheses** (`patents_ip`) - 14 records
- **Fifteen-concept portfolio with weighted scores** (`concept_portfolio`) - 17 records
- **Personas & empathy data** (`personas`) - 5 records
- **User needs N1-N7 with evidence anchors** (`user_needs`) - 7 records
- **System architecture & governance design** (`architecture`) - 13 records
- **Agent specifications A1-A6, 16-role roster, FMEA** (`agent_specifications`) - 9 records
- **Data architecture (entities, stores, seed dataset)** (`data_architecture`) - 3 records
- **Demo & benchmark numbers** (`demo_benchmark`) - 8 records
- **Evaluation framework & MVP targets** (`evaluation_framework`) - 4 records
- **Program risk register** (`risk_register`) - 1 records
- **Implementation schedule & team assignments** (`implementation_schedule`) - 7 records
- **Business model, ICP, GTM, moat** (`business_model`) - 6 records
- **Financial model & ROI equations** (`financial_model`) - 5 records
- **Q&A war room (30 questions)** (`qa_war_room`) - 30 records
- **Research roadmap (RecoveryBench-12, pitch, blueprint)** (`research_roadmap`) - 6 records
- **Principal source index (Table A.1)** (`sources`) - 35 records
- **Selected references (APA)** (`references`) - 2 records
- **Glossary (Table B.1)** (`glossary`) - 1 records
- **3-member workflow specifications (workflow PDF)** (`workflow_specifications`) - 10 records
- **Shared data contracts (frozen schemas)** (`contracts`) - 6 records
- **WEB: Live freight indices (Drewry WCI/IACI, FBX, CCFI, SCFI)** (`web_freight_indices`) - 8 records
- **WEB: Tariffs & trade policy (US-India pharma, global, macro impact)** (`web_tariffs_trade`) - 8 records
- **WEB: Live port congestion (Singapore, Rotterdam, reefer restrictions)** (`web_port_congestion`) - 5 records
- **WEB: Cold-chain & SCM market sizes (global, pharma, monitoring, storage, visibility)** (`web_coldchain_markets`) - 17 records
- **WEB: Pharma loss verification ($35bn IQVIA, vaccine wastage, disruption costs)** (`web_pharma_losses`) - 7 records
- **WEB: Competitor funding & Gartner MQ updates (2025-2026)** (`web_competitor_updates`) - 10 records
- **WEB: SAP API surfaces & AI agent timeline (IBP/TM/Ariba, Joule, GenAI Hub, BTP)** (`web_sap_updates`) - 6 records
- **WEB: India ground data refresh (exports, cold storage, API dependency, eVIN, targets)** (`web_india_updates`) - 14 records
- **WEB: Regulatory primary-source verification (EU GDP, WHO TRS 961, USP, FDA, CDSCO)** (`web_regulatory_verification`) - 6 records
- **WEB: AI stack licenses & status (TimesFM, Chronos, Moirai, agent frameworks, MCP)** (`web_ai_stack`) - 6 records
- **WEB: Free demo data feeds & APIs (weather, AIS, freight, news, BLE, routing)** (`web_demo_feeds`) - 6 records

## How to load it
```python
import json, pandas as pd
master = json.load(open("data/sanjeevani_real_data_master.json"))
stats  = master["data"]["headline_metrics"]              # any section key
df     = pd.read_csv("data/sanjeevani_real_data_quantitative.csv")   # all quantitative rows
df[df.section == "market_sizes"]                          # filter by category
```
```bash
jq '.data.competitors' data/sanjeevani_real_data_master.json
```

## Coverage guarantees (what was captured, nothing skipped)
- All **35 principal sources** (Table A.1) and the full APA reference list and glossary (Ch. 28).
- Every statistic in Chapters 1-2 and 9 with its cited org + date (J.S. Held $184bn, Gartner $53bn/40%,
  Red Sea 90%/$5,481 FEU/+417%/ITF-OECD $300, tariffs 16.8%/30%/50%/77%, WHO 50% vaccines, 20% biologics,
  12% shipments, $35bn losses, Kanwal 2026 dwell window, India $30.47bn/9.4%/74.2%/1% problem/7.97% GDP/
  eVIN 733-36-29,000/52 paise, cold-chain markets $62.5->95.1bn, $35->120bn, India $16.4->31.4bn, etc.).
- All **9 competitor profiles + 4 funding records** (Table 2 + source #15), the **12-row capability matrix**
  (Table 3), the **11-row solved-vs-open map** (Table 5) and the five claimable gaps.
- The full **research evidence shelf** (Table 4 + Chapter 6 anchor works with citation counts), all
  **8 Hugging Face model assets** (Table 6) and **12+ open-source tools/datasets with licenses** (Table 7).
- The complete **SAP ecosystem record**: Sapphire/Connect dates and agent counts, IBP/TM/Ariba API surfaces
  (incl. ARI-19205, 500-supplier batch), HANA vector/KG GA dates, GenAI Hub licensing gotcha, A2A/MCP, batch agent.
- The complete **regulatory stack** (EU GDP 2013/C 343/01 + 2015/C 95/01, WHO TRS 961 Annex 9, USP
  <1079>/<1079.4>/<1118>, 20-25C/15-30C bounds, 21 CFR Part 11, CDSCO/GDP.PP Ver. 00 Apr 2024) with the
  regulation-to-feature mapping.
- All **15 concepts with the full 10-criterion x 15-concept scoring matrix** (Tables 11/12, totals as printed),
  weights, verdict and sensitivity check.
- Personas (Meera/Rajat/Anil + verbatim quotes), user needs N1-N7, architecture (10-stage loop, 7 bands,
  approval tiers, L1-L4 escalation, component-SAP feasibility), agents A1-A6 + 16-role roster + 8-row FMEA,
  data model (12 entities, 3 stores, seed dataset composition).
- Demo/benchmark numbers (Table 26 + live demo numbers), 12-metric evaluation framework, 7-row risk register,
  day-by-day schedules (both documents), demo runbook, red flags, checklists.
- Business model (BMC, ICP, GTM, Y1/Y3/Y5, moat ranking), financial model (assumptions, outputs, ROI
  equations, worked example, value-pool shares), all **30 Q&A war-room answers**.
- The **6 frozen shared contracts verbatim** + patent prior art (7 refs) + hypotheses H1-H5/I1-I5 + filing strategy.

## Honest limitations (so your team stays defensible)
- Scores, financial outputs and the value-pool shares are recorded **exactly as printed**; the printed concept
  totals are the document's own arithmetic and are not re-derived here.
- The dossier's underlying 91 raw search JSON archives are referenced by the documents but were not included
  in your upload - only their consolidated index (Table A.1) exists in the PDFs, so only that is reproduced.
- Page references use the **printed page numbers** shown on each dossier page (`pdf_page` field gives the PDF
  file page, printed N = PDF N+6).

## Source documents
- `SANJEEVANI_3_Member_Workflow_AI_Handoff.pdf` (12 pp.) - team architecture, contracts, member specs, schedule, runbook.
- `SANJEEVANI_SAP_Hackfest_2026_Unified_Dossier.pdf` (89 pp.) - strategy, evidence, architecture, competition run-book, business plan.
